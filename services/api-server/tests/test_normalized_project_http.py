"""Exercise real HTTP routes with no project in the compatibility dictionary.

DB transport and the parser are injected; calculation, request scope and atomic
storage run unchanged. Restricted PostgreSQL/RLS is checked in the live suite.
"""
from contextlib import contextmanager
from copy import deepcopy
from fastapi.testclient import TestClient
import main
import storage
from routers import projects
from db.repositories import calculation_run_repository
from test_request_project_scope import Authority
from test_normalized_calculation_writes import Connection
from test_cash_flow_dependencies import request_data


def test_upload_calculate_get_without_compatibility_project(monkeypatch):
    authority = Authority('tenant-a')
    original_update = authority.update
    def update(key, data, expected_state_version=None):
        return original_update(key, {**deepcopy(authority.row), **data}, expected_state_version)
    authority.update = update
    for module in (main, projects, storage):
        monkeypatch.setattr(module, 'storage_backend', lambda: 'postgres')
    monkeypatch.setattr(projects, 'PROJECTS_DB', {})
    monkeypatch.setattr(projects, 'PostgresProjectRepository', lambda **kw: authority)
    identity = lambda *_args, **_kw: {'user': {'tenantId':'tenant-a', 'userId':'reviewer'}}
    for module in (main, projects):
        monkeypatch.setattr(module, 'identity_context_from_request', identity)
        monkeypatch.setattr(module, 'enforce_project_tenant_scope', lambda *_: None)
    monkeypatch.setattr(projects, 'authorize_request', lambda *_: None)
    monkeypatch.setattr(projects, 'ai_context_from_request', lambda *_args, **_kw: {})
    monkeypatch.setattr(main, 'record_user_activity_log', lambda row: row)
    class Activity:
        def append_api_request(self, event):
            return event['eventId']
    monkeypatch.setattr(main, 'normalized_activity_repository', lambda **kw: Activity())
    async def parse(pid, file, context):
        assert await file.read() == b'uploaded evidence'
        return {'source_documents':[{'archiveId':'source-1','documentType':'business_plan'}],
                'feasibility_input':{'total_revenue':200, 'total_cost':80}}
    monkeypatch.setattr(projects, 'parse_uploaded_business_plan', parse)
    monkeypatch.setattr(projects, '_maybe_auto_reanalyze_on_upload', lambda *_: None)
    monkeypatch.setattr(storage, '_load_json_state', lambda: (_ for _ in ()).throw(AssertionError('JSON authority used')))
    connections=[]
    @contextmanager
    def transaction(tenant):
        assert tenant == 'tenant-a'
        conn=Connection()
        connections.append(conn)
        yield conn
    repository = calculation_run_repository.PostgresCalculationRunRepository
    monkeypatch.setattr(calculation_run_repository, 'PostgresCalculationRunRepository',
        lambda **kw: repository(**{**kw, 'transaction_factory':transaction}))
    client=TestClient(main.app)
    uploaded=client.post('/projects/p/upload', files={'file':('plan.txt',b'uploaded evidence','text/plain')})
    assert uploaded.status_code == 200, uploaded.text
    calculated=client.post('/projects/p/cash-flow/run', json=request_data())
    assert calculated.status_code == 200, calculated.text
    assert calculated.json()['dependentExecutionStatus'] == 'completed'
    assert calculated.json()['summary']['totalRevenue'] == 200
    reloaded=client.get('/projects/p')
    assert reloaded.status_code == 200
    assert reloaded.json()['source_documents'][0]['archiveId'] == 'source-1'
    assert reloaded.json()['_meta']['stateVersion'] == 2
    assert len(connections) == 5
    assert all(conn.writes[:2] == ['run','outbox'] for conn in connections)
    assert projects.PROJECTS_DB == {}
