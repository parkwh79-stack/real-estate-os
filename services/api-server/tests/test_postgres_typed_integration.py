"""Rollback-only integration against an explicitly supplied disposable PostgreSQL DB."""
import os
from contextlib import contextmanager
from uuid import uuid4

import pytest

from db import postgres
from db.repositories.calculation_run_repository import PostgresCalculationRunRepository
from db.repositories.deal_state_repository import PostgresDealStateRepository
from db.repositories.postgres_project_repository import PostgresProjectRepository
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
