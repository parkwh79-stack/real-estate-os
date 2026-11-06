from contextlib import contextmanager
from types import SimpleNamespace
import pytest

from db import postgres
from db.repositories import runtime_record_repository as runtime
from db.repositories.request_project_repository import project_request_scope


def test_generic_typed_request_uses_runtime_transaction_and_never_admin_dsn(monkeypatch):
    statements = []
    class Connection:
        def execute(self, sql, params):
            statements.append((sql, params))
            return self
        def fetchone(self):
            return ('tenant-a',)
    @contextmanager
    def transaction(tenant):
        assert tenant == 'tenant-a'
        yield Connection()
    monkeypatch.setattr(runtime, 'api_tenant_transaction', transaction)
    monkeypatch.setattr(postgres, 'connect', lambda: pytest.fail('admin DSN used'))
    monkeypatch.setattr(postgres, 'ensure_schema', lambda *_: pytest.fail('runtime DDL'))
    with project_request_scope(lambda: SimpleNamespace(tenant_id='tenant-a')):
        postgres.upsert_typed_jsonb_record('chief_pod_artifacts', {'project_id': 'p', 'artifact_key': 'a'}, {'projectId': 'p'})
        with pytest.raises(ValueError, match='tenant'):
            postgres.upsert_typed_jsonb_record('chief_pod_artifacts', {'project_id': 'p', 'artifact_key': 'a'}, {'tenantId': 'tenant-b'})
    assert len(statements) == 1
    assert 'tenant_id' in statements[0][0]
    assert 'tenant-a' in statements[0][1]
