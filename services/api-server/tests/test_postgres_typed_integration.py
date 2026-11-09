"""Rollback-only integration against an explicitly supplied disposable PostgreSQL DB."""
import os
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from db import postgres
from db.repositories.calculation_run_repository import PostgresCalculationRunRepository
from db.repositories.deal_state_repository import PostgresDealStateRepository
from db.repositories.postgres_project_repository import PostgresProjectRepository
from db.repositories.request_project_repository import project_request_scope
import storage

TEST_DSN = os.environ.get('REALYTICS_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not TEST_DSN, reason='REALYTICS_TEST_DATABASE_URL is not configured')


def test_typed_tables_round_trip_in_isolated_postgres(monkeypatch):
    tenant = f'e2e-{uuid4()}'
    conn = postgres.connect(TEST_DSN)
    try:
        postgres.ensure_schema(conn)
        conn.execute("INSERT INTO realytics.tenants(tenant_id, name, status) VALUES (%s,%s,'active')", (tenant, tenant))
        @contextmanager
        def transaction(tenant_id):
            conn.execute("SELECT set_config('realytics.tenant_id', %s, true)", (tenant_id,))
            yield conn
        project = PostgresProjectRepository(tenant_id=tenant, transaction_factory=transaction).create({'project': {'name': 'Isolated calculation test'}})
        monkeypatch.setattr(storage, 'storage_backend', lambda: 'postgres')
        run = storage.upsert_calculation_run_record(project['id'], {
            'calculationRunId': 'calc:test:1', 'engineKey': 'cash_flow_engine',
            'outputMetrics': [{'key': 'revenue', 'value': 123, 'unit': 'KRW', 'sourceLocator': {'cell': 'A1'}}],
        }, tenant_id=tenant, transaction_factory=transaction)
        repository = PostgresCalculationRunRepository(tenant_id=tenant, transaction_factory=transaction)
        restored = repository.get_run(project['id'], run['calculationRunId'])
        assert restored['tenantId'] == tenant
        assert restored['outputMetrics'] == run['outputMetrics']
        rows = PostgresDealStateRepository(tenant_id=tenant, transaction_factory=transaction).list_records(project['id'])
        assert rows[0]['value'] == 123
        assert rows[0]['calculationRunId'] == run['calculationRunId']
        assert conn.execute('SELECT count(*) FROM realytics.runtime_outbox_events WHERE tenant_id=%s AND aggregate_id=%s',
                            (tenant, run['calculationRunId'])).fetchone()[0] == 1
        other = PostgresCalculationRunRepository(tenant_id='other-tenant', transaction_factory=transaction)
        assert other.get_run(project['id'], run['calculationRunId']) is None
    finally:
        conn.rollback()
        conn.close()


def test_materialized_risk_preserves_exact_record_review_in_isolated_postgres(monkeypatch):
    tenant = f'risk-review-{uuid4()}'
    conn = postgres.connect(TEST_DSN)
    try:
        postgres.ensure_schema(conn)
        conn.execute("INSERT INTO realytics.tenants(tenant_id, name, status) VALUES (%s,%s,'active')", (tenant, tenant))

        @contextmanager
        def transaction(tenant_id):
            conn.execute("SELECT set_config('realytics.tenant_id', %s, true)", (tenant_id,))
            yield conn

        project = PostgresProjectRepository(tenant_id=tenant, transaction_factory=transaction).create({'project': {'name': 'Isolated risk review'}})
        monkeypatch.setattr(storage, 'storage_backend', lambda: 'postgres')
        register = {'risks': [{'recordId': 'reviewed-risk', 'code': 'COST_SOURCE', 'severity': 'high',
                              'status': 'open', 'reviewStatus': 'open', 'sourceLocator': {'sheetName': 'Cost', 'cell': 'B10'}}]}
        with project_request_scope(lambda: SimpleNamespace(tenant_id=tenant), transaction_factory=transaction):
            storage._materialize_risk_register_records(project['id'], 'risk-run', register)
            row = storage.list_deal_state_records(project['id'], record_id='reviewed-risk', limit=1)[0]
            entry = {'reviewStatus': 'needs_revision', 'reviewedBy': 'reviewer', 'reviewedAt': '2026-10-14T00:00:00+00:00',
                     'comment': '보완 사유: 원가 근거 누락\n다음 할 일: 견적서 첨부'}
            reviewed = {**row, **entry, 'reviewHistory': [entry]}
            storage.upsert_deal_state_record(project['id'], reviewed)
            storage._materialize_risk_register_records(project['id'], 'risk-run', register)
            restored = storage.list_deal_state_records(project['id'], record_id='reviewed-risk', limit=1)[0]
            assert restored['comment'] == entry['comment'] and restored['reviewHistory'] == [entry]
            assert storage.list_deal_state_records(project['id'], record_id='missing-risk', limit=1) == []
            changed = deepcopy(register)
            changed['risks'][0]['sourceLocator']['cell'] = 'B11'
            storage._materialize_risk_register_records(project['id'], 'risk-run', changed)
            changed_row = storage.list_deal_state_records(project['id'], record_id='reviewed-risk', limit=1)[0]
            assert changed_row['reviewStatus'] == 'open' and not changed_row.get('reviewHistory')
        outsider = PostgresDealStateRepository(tenant_id='other-tenant', transaction_factory=transaction)
        assert outsider.list_records(project['id'], record_id='reviewed-risk') == []
    finally:
        conn.rollback()
        conn.close()


@pytest.mark.parametrize("mode", ["read_only", "edit"])
def test_proposed_user_ai_grants_and_rls_in_rollback_only_isolated_schema(mode):
    """Exercise the review SQL as a non-owner; no live runtime grants are used."""
    from db.user_ai_profile_privileges import render_user_ai_profile_access_review_sql
    from psycopg.errors import InsufficientPrivilege

    suffix = uuid4().hex
    schema, role = f"ai_review_{suffix}", f"ai_runtime_{suffix}"
    conn = postgres.connect(TEST_DSN)
    try:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'CREATE ROLE "{role}" NOLOGIN NOINHERIT NOBYPASSRLS')
        conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        conn.execute(f'CREATE TABLE "{schema}".user_ai_profiles (user_id text PRIMARY KEY, tenant_id text NOT NULL, model text)')
        conn.execute(f'ALTER TABLE "{schema}".user_ai_profiles ENABLE ROW LEVEL SECURITY')
        conn.execute(f'ALTER TABLE "{schema}".user_ai_profiles FORCE ROW LEVEL SECURITY')
        conn.execute(f'CREATE POLICY tenant_scope ON "{schema}".user_ai_profiles FOR ALL TO "{role}" '
                     "USING (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')) "
                     "WITH CHECK (tenant_id = NULLIF(current_setting('realytics.tenant_id', true), ''))")
        conn.execute(f'INSERT INTO "{schema}".user_ai_profiles VALUES (%s,%s,%s),(%s,%s,%s),(%s,%s,%s)',
                     ("user-a", "tenant-a", "model-a", "user-b", "tenant-a", "model-b", "user-c", "tenant-b", "model-c"))
        conn.execute(render_user_ai_profile_access_review_sql(mode=mode, role_name=role, schema_name=schema))
        conn.execute(f'SET LOCAL ROLE "{role}"')
        table = f'"{schema}".user_ai_profiles'
        for tenant, user, expected in (("tenant-a", "user-a", "model-a"), ("tenant-a", "user-b", "model-b"),
                                       ("tenant-b", "user-c", "model-c"), ("tenant-b", "user-a", None), ("tenant-a", "", None)):
            conn.execute("SELECT set_config('realytics.tenant_id', %s, true), set_config('realytics.user_id', %s, true)", (tenant, user))
            rows = conn.execute(f'SELECT model FROM {table}').fetchall()
            assert rows == ([(expected,)] if expected else [])
        conn.execute("SELECT set_config('realytics.tenant_id', 'tenant-a', true), set_config('realytics.user_id', 'user-a', true)")
        for sql in (f"INSERT INTO {table} VALUES ('user-d','tenant-a','intruder')", f"INSERT INTO {table} VALUES ('user-x','tenant-b','intruder')",
                    f"UPDATE {table} SET model='intruder' WHERE user_id='user-b'", f"DELETE FROM {table} WHERE user_id='user-b'",
                    f"UPDATE {table} SET model='intruder' WHERE user_id='user-c'", f"DELETE FROM {table} WHERE user_id='user-c'"):
            with conn.transaction():
                if mode == "read_only" or sql.startswith("INSERT"):
                    with pytest.raises(InsufficientPrivilege):
                        with conn.transaction():
                            conn.execute(sql)
                else:
                    assert conn.execute(sql).rowcount == 0
        if mode == "edit":
            assert conn.execute(f"UPDATE {table} SET model='updated' WHERE user_id='user-a'").rowcount == 1
            assert conn.execute(f"DELETE FROM {table} WHERE user_id='user-a'").rowcount == 1
            conn.execute(f"INSERT INTO {table} VALUES ('user-a','tenant-a','recreated')")
            assert conn.execute(f"SELECT model FROM {table}").fetchone() == ("recreated",)
    finally:
        conn.rollback()
        conn.close()
