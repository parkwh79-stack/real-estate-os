"""Scoped bridge for typed records while domain repositories are extracted."""
from copy import deepcopy
from collections.abc import Mapping
import json

from db.postgres_runtime_pool import api_tenant_transaction
from db.repositories.request_project_repository import current_project_scope


class WorkerLeaseLost(RuntimeError):
    pass


def request_tenant():
    scope = current_project_scope()
    return scope.tenant_id if scope is not None else None


def scoped_transaction():
    scope = current_project_scope()
    if scope is None:
        raise RuntimeError('Verified runtime scope required')
    return (scope.transaction_factory or api_tenant_transaction)(scope.tenant_id)


def bind_scope(values, payload):
    tenant = request_tenant()
    if not tenant:
        raise RuntimeError('Verified runtime scope required')
    values, payload = dict(values), deepcopy(payload)
    for claimed in (values.get('tenant_id'), payload.get('tenantId'), payload.get('tenant_id')):
        if claimed and claimed != tenant:
            raise ValueError('Typed record tenant does not match request')
    values['tenant_id'] = tenant
    payload['tenantId'] = tenant
    return values, payload


def upsert_request_records(records):
    from db.postgres import _typed_upsert_command
    tenant = request_tenant()
    if tenant is None:
        raise RuntimeError('Verified request scope required')
    with scoped_transaction() as conn:
        for table, values, payload in records:
            values, payload = dict(values), deepcopy(payload)
            for claimed in (values.get('tenant_id'), payload.get('tenantId'), payload.get('tenant_id')):
                if claimed and claimed != tenant:
                    raise ValueError('Typed record tenant does not match request')
            values['tenant_id'] = tenant
            payload['tenantId'] = tenant
            sql, params = _typed_upsert_command(table, values, payload)
            scope = current_project_scope()
            if table == 'async_jobs' and scope.worker_id:
                sql += ' WHERE async_jobs.lease_owner = %s AND async_jobs.attempt_count = %s'
                params.extend([scope.worker_id, values.get('attempt_count')])
            row = conn.execute(sql + ' RETURNING tenant_id', params).fetchone()
            if row is None and table == 'async_jobs' and scope.worker_id:
                raise WorkerLeaseLost('Worker job lease is no longer owned')
            if row is None or (row.get('tenant_id') if isinstance(row, Mapping) else row[0]) != tenant:
                raise ValueError('Typed record write returned invalid scope')


def list_request_records(table, spec, where, limit):
    tenant = request_tenant()
    where = dict(where or {})
    if where.get('tenant_id') and where['tenant_id'] != tenant:
        raise ValueError('Typed record filter tenant does not match request')
    where['tenant_id'] = tenant
    if any(key not in spec['columns'] for key in where):
        raise ValueError('Unsupported typed record filter')
    sql = f"SELECT tenant_id, payload_json FROM realytics.{table} WHERE " + ' AND '.join(f'{key} = %s' for key in where)
    sql += f" ORDER BY {spec['order']} LIMIT %s"
    with scoped_transaction() as conn:
        rows = conn.execute(sql, [*where.values(), max(1, min(int(limit), 1000))]).fetchall()
    results = []
    for row in rows:
        row_tenant, payload = (row.get('tenant_id'), row.get('payload_json')) if isinstance(row, Mapping) else row
        if row_tenant != tenant:
            raise ValueError('Typed record query returned invalid scope')
        payload = json.loads(payload) if isinstance(payload, str) else deepcopy(payload)
        if any(payload.get(key) not in (None, '', tenant) for key in ('tenantId', 'tenant_id')):
            raise ValueError('Typed record payload tenant mismatch')
        payload['tenantId'] = tenant
        results.append(payload)
    return results


def insert_request_record(table, values, payload, *, connection=None):
    from db.postgres import _TYPED_JSONB_TABLES
    values, payload = bind_scope(values, payload)
    spec = _TYPED_JSONB_TABLES[table]
    if any(key not in values for key in spec['key']):
        raise ValueError('Missing runtime record key')
    columns = [column for column in spec['columns'] if column in values]
    sql = f"INSERT INTO realytics.{table} ({', '.join(columns)}, payload_json) VALUES ({', '.join(['%s'] * len(columns))}, %s::jsonb) ON CONFLICT DO NOTHING RETURNING tenant_id"
    params = [values[column] for column in columns] + [json.dumps(payload, ensure_ascii=False, default=str)]
    from contextlib import nullcontext
    with nullcontext(connection) if connection is not None else scoped_transaction() as conn:
        row = conn.execute(sql, params).fetchone()
        if row is not None and (row.get('tenant_id') if isinstance(row, Mapping) else row[0]) != request_tenant():
            raise ValueError('Inserted record returned invalid tenant')
        return row is not None


def update_request_agent_run(values, payload, expected_state_version, *, outbox=None):
    from db.postgres import _TYPED_JSONB_TABLES
    values, payload = bind_scope(values, payload)
    columns = [key for key in _TYPED_JSONB_TABLES['agent_runs']['columns'] if key in values and key not in ('run_id', 'tenant_id')]
    sql = f"UPDATE realytics.agent_runs SET {', '.join(f'{key} = %s' for key in columns)}, payload_json = %s::jsonb WHERE tenant_id = %s AND run_id = %s AND state_version = %s RETURNING tenant_id"
    params = [values[key] for key in columns] + [json.dumps(payload, ensure_ascii=False, default=str), request_tenant(), values['run_id'], expected_state_version]
    with scoped_transaction() as conn:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return False
        if (row.get('tenant_id') if isinstance(row, Mapping) else row[0]) != request_tenant():
            raise ValueError('Agent update returned invalid tenant')
        if outbox:
            insert_request_record('runtime_outbox_events', *outbox, connection=conn)
    return True


def claim_scoped_job(*, worker_id, now, lease_expires_at):
    tenant = request_tenant()
    scope = current_project_scope()
    if scope is None or scope.worker_id != worker_id or scope.transaction_factory is None:
        raise RuntimeError('A tenant-scoped worker transaction is required')
    with scoped_transaction() as conn:
        row = conn.execute('''SELECT id, payload_json, attempt_count FROM realytics.async_jobs
            WHERE tenant_id = %s AND attempt_count < max_attempts AND
            (status = 'queued' OR (status = 'running' AND lease_expires_at < %s))
            ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1''', (tenant, now)).fetchone()
        if row is None:
            return None
        job_id, payload, attempts = (row['id'], row['payload_json'], row['attempt_count']) if isinstance(row, Mapping) else row
        payload = json.loads(payload) if isinstance(payload, str) else deepcopy(payload)
        if payload.get('tenantId') != tenant:
            raise ValueError('Claimed job tenant mismatch')
        payload.update(status='running', startedAt=payload.get('startedAt') or now.isoformat(),
            leaseOwner=worker_id, leaseExpiresAt=lease_expires_at.isoformat(),
            attemptCount=int(attempts)+1, updatedAt=now.isoformat())
        conn.execute('''UPDATE realytics.async_jobs SET status = 'running', attempt_count = %s,
            lease_owner = %s, lease_expires_at = %s, updated_at = %s, payload_json = %s::jsonb
            WHERE tenant_id = %s AND id = %s''', (payload['attemptCount'], worker_id, lease_expires_at,
            now, json.dumps(payload, ensure_ascii=False, default=str), tenant, job_id))
        return payload
