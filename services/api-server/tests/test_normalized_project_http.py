"""Exercise real HTTP routes with no project in the compatibility dictionary.

DB transport and the parser are injected; calculation, request scope and atomic
storage run unchanged. Restricted PostgreSQL/RLS is checked in the live suite.
"""
from contextlib import contextmanager
from copy import deepcopy
from fastapi import HTTPException
from fastapi.testclient import TestClient
import main
import storage
from routers import projects
from db import postgres
from db.repositories import calculation_run_repository
from db.repositories import deal_state_repository
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


def test_workspace_context_reads_normalized_data_without_compatibility_state(monkeypatch):
    authority = Authority("tenant-a")
    authority.row.update({
        "project": {"id": "p", "name": "Saved project"},
        "source_documents": [{"archiveId": "doc-1", "documentType": "business_plan"}],
        "landLedger": {"parcels": [{"pnu": "1111010100100010000"}]},
    })
    original_project = deepcopy(authority.row)
    for module in (main, projects, storage):
        monkeypatch.setattr(module, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **kw: authority)
    identity = lambda *_args, **_kw: {"user": {"tenantId": "tenant-a", "userId": "reviewer"}}
    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", identity)
        monkeypatch.setattr(module, "enforce_project_tenant_scope", lambda *_: None)

    def forbid_legacy(*_args, **_kw):
        raise AssertionError("A normalized workspace read used legacy state")

    monkeypatch.setattr(storage, "_load_json_state", forbid_legacy)
    monkeypatch.setattr(projects, "_persist_shared_deal_state_graph", forbid_legacy)
    monkeypatch.setattr(projects, "record_user_activity_log", forbid_legacy)
    calls = []

    def authorize(_request, permission):
        assert permission == "project:read"
        if _request.headers.get("x-test-deny") == "true":
            raise HTTPException(status_code=403, detail="denied")

    class DealState:
        def __init__(self, *, tenant_id, **_kw):
            assert tenant_id == "tenant-a"

        def list_records(self, project_id, *, record_type, **_kw):
            calls.append("facts")
            assert (project_id, record_type) == ("p", "parsed_fact")
            return [{"recordId": "fact-1", "recordType": "parsed_fact", "domain": "land"}]

    def list_artifacts(table, *, where, limit):
        calls.append("artifacts")
        assert (table, where, limit) == ("chief_pod_artifacts", {"project_id": "p"}, 100)
        return []

    class Activity:
        def append_api_request(self, event):
            return event["eventId"]

    monkeypatch.setattr(projects, "authorize_request", authorize)
    monkeypatch.setattr(deal_state_repository, "PostgresDealStateRepository", DealState)
    monkeypatch.setattr(postgres, "list_typed_jsonb_records", list_artifacts)
    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_kw: Activity())
    client = TestClient(main.app)
    route = "/projects/p/workspace/context?page=input_package"
    assert client.get(route, headers={"x-test-deny": "true"}).status_code == 403
    assert client.get("/projects/outside/workspace/context").status_code == 404
    assert calls == []

    response = client.get(route)
    assert response.status_code == 200, response.text
    context = response.json()
    assert context["contextCompleteness"] == "partial"
    assert context["projectMemory"]["projectName"] == "Saved project"
    assert context["projectMemory"]["sourceDocumentCount"] == 1
    assert context["pageData"]["sourceDocuments"][0]["archiveId"] == "doc-1"
    assert context["pageData"]["parsedFactCount"] == 1
    assert context["pageData"]["landLedgerSummary"]["pnuCount"] == 1
    assert "landEvidenceSummary" not in context["pageData"]
    assert "missingInputRegister" not in context["pageData"]
    assert "dealStateGraph" not in context
    assert calls == ["facts", "artifacts"]
    assert authority.row == original_project
