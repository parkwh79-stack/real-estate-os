from contextlib import contextmanager
from copy import deepcopy

import pytest

import storage
from db.repositories.calculation_run_repository import CalculationRunRepositoryError


class Connection:
    def __init__(self, fail=None):
        self.fail = fail
        self.writes = []
        self.row = None

    def execute(self, sql, params):
        if 'INSERT INTO realytics.calculation_runs' in sql:
            kind, row = 'run', params[:3]
        elif 'INSERT INTO realytics.runtime_outbox_events' in sql:
            kind, row = 'outbox', params[:6]
        elif 'INSERT INTO realytics.deal_state_records' in sql:
            kind, row = 'projection', params[:3]
        else:
            raise AssertionError(sql)
        self.writes.append(kind)
        self.row = None if self.fail == kind else row
        return self

    def fetchone(self):
        return self.row


@pytest.mark.parametrize('failure', [None, 'run', 'outbox', 'projection'])
def test_storage_wires_tenant_atomic_run_outbox_and_projection(monkeypatch, failure):
    monkeypatch.setattr(storage, 'storage_backend', lambda: 'postgres')
    monkeypatch.setattr(storage, '_load_json_state', lambda: pytest.fail('JSON fallback'))
    conn = Connection(failure)
    outcomes = []
    @contextmanager
    def transaction(tenant):
        assert tenant == 'tenant-a'
        try:
            yield conn
        except Exception:
            outcomes.append('rollback')
            raise
        else:
            outcomes.append('commit')
    record = {'calculationRunId': 'calc-1', 'engineKey': 'cash_flow_engine',
              'outputMetrics': [{'key': 'total', 'value': 12, 'sourceLocator': {'cell': 'A1'}}]}
    original = deepcopy(record)
    def write():
        return storage.upsert_calculation_run_record('p1', record, tenant_id='tenant-a', transaction_factory=transaction)
    if failure:
        with pytest.raises(CalculationRunRepositoryError):
            write()
        assert outcomes == ['rollback']
    else:
        result = write()
        assert result['tenantId'] == 'tenant-a'
        assert conn.writes == ['run', 'outbox', 'projection']
        assert outcomes == ['commit']
    assert record == original


def test_normalized_write_requires_explicit_authority_and_rejects_forgery(monkeypatch):
    monkeypatch.setattr(storage, 'storage_backend', lambda: 'postgres')
    with pytest.raises(RuntimeError, match='verified tenant'):
        storage.upsert_calculation_run_record('p', {'tenantId': 'caller-chosen'})
    with pytest.raises(ValueError, match='tenant'):
        storage.upsert_calculation_run_record('p', {'tenantId': 'tenant-b'}, tenant_id='tenant-a')
