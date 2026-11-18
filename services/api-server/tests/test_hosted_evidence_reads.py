from contextlib import contextmanager
from copy import deepcopy

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import storage
from db import postgres, postgres_runtime_pool
from db.repositories import deal_state_repository
from routers import projects
from test_request_project_scope import Authority


def test_hosted_graph_and_read_only_audit_use_scoped_authorities_without_review_writes(monkeypatch):
    authority = Authority("tenant-a")
    identity = {"authenticated": True, "user": {"tenantId": "tenant-a", "userId": "analyst", "role": "analyst"}}
    records = [
        {"recordId": "missing", "recordType": "metric", "sourceLocator": {}},
        {"recordId": "verified", "recordType": "evidence", "sourceLocator": {"apiRowId": "source-row"}},
    ]
    graph = {
        "projectId": "p", "runId": "r1", "graphId": "graph-one",
        "nodes": [{"id": "n1", "type": "source_locator"}], "edges": [],
        "nodeCount": 1, "edgeCount": 0, "sourceLocatorCount": 1,
    }
    state = {"databaseFailed": False, "foreignRow": False, "queries": []}

    for module in (main, projects, storage):
        monkeypatch.setattr(module, "storage_backend", lambda: "postgres")
    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **_kwargs: authority)
    monkeypatch.setattr(projects, "PROJECTS_DB", {})

    def authorize(_request, permission):
        if permission == "project:review":
            raise HTTPException(status_code=403, detail="Reviewer permission required")
        assert permission == "project:read"
        return identity

    monkeypatch.setattr(projects, "authorize_request", authorize)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Evidence read accessed legacy state or wrote a risk/business record")

    monkeypatch.setattr(storage, "_load_json_state", forbidden)
    monkeypatch.setattr(postgres, "ensure_schema", forbidden)
    monkeypatch.setattr(postgres, "_ensure_app_state_table", forbidden)
    monkeypatch.setattr(projects, "list_evidence_graph_records", forbidden)
    monkeypatch.setattr(projects, "get_evidence_graph_record", forbidden)
    monkeypatch.setattr(projects, "persist_project", forbidden)

    class Activity:
        def append_api_request(self, event):
            return event["eventId"]

    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_kwargs: Activity())

    class DealState:
        def __init__(self, *, tenant_id, **_kwargs):
            assert tenant_id == "tenant-a"

        def list_records(self, project_id, **_kwargs):
            assert project_id == "p"
            return deepcopy(records)

        upsert_records = forbidden

    class Reports:
        def __init__(self, *, tenant_id):
            assert tenant_id == "tenant-a"

        def list_metadata(self, project_id):
            assert project_id == "p"
            return []

    class Runs:
        def __init__(self, *, tenant_id, **_kwargs):
            assert tenant_id == "tenant-a"

        def get(self, project_id, run_id):
            assert project_id == "p"
            return None

    monkeypatch.setattr(deal_state_repository, "PostgresDealStateRepository", DealState)
    monkeypatch.setattr(projects, "PostgresReportRepository", Reports)
    monkeypatch.setattr(projects, "PostgresOrchestrationRepository", Runs)

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class GraphConnection:
        def execute(self, sql, params):
            assert "FROM realytics.evidence_graphs" in sql
            assert "tenant_id = %s AND project_id = %s" in sql
            assert params[:2] == ["tenant-a", "p"]
            state["queries"].append(sql)
            if state["databaseFailed"]:
                raise RuntimeError("Graph store unavailable")
            if len(params) > 2 and params[2] != "r1":
                return Rows([])
            return Rows([("tenant-b" if state["foreignRow"] else "tenant-a", "p", "r1", deepcopy(graph))])

    @contextmanager
    def transaction(tenant):
        assert tenant == "tenant-a"
        yield GraphConnection()

    monkeypatch.setattr(postgres_runtime_pool, "api_tenant_transaction", transaction)
    client = TestClient(main.app)
    audit_url = "/projects/p/deal-state-records/source-locator-audit"
    inspected = client.get(audit_url)
    assert inspected.status_code == 200, inspected.text
    assert inspected.headers["Cache-Control"] == "private, no-store"
    assert inspected.json()["missingLocatorCount"] == 1
    assert inspected.json()["checkedDealStateRecordCount"] == 2
    assert inspected.json()["hasReviewableRecords"] is True
    assert inspected.json()["persisted"] is False
    assert inspected.json()["sourceLocatorMissingRisk"] is None
    assert client.post(audit_url).status_code == 403
    records.clear()
    assert client.get(audit_url).json()["hasReviewableRecords"] is False

    listed = client.get("/projects/p/evidence-graphs")
    assert listed.status_code == 200, listed.text
    assert listed.json()["records"][0]["graphId"] == "graph-one"
    assert listed.json()["records"][0]["nodeCount"] == 1
    detail = client.get("/projects/p/orchestrations/r1/evidence-graph")
    assert detail.status_code == 200, detail.text
    assert detail.json() == graph
    assert client.get("/projects/p/orchestrations/missing/evidence-graph").status_code == 404
    state["foreignRow"] = True
    assert client.get("/projects/p/evidence-graphs").status_code == 503
    state["foreignRow"] = False
    state["databaseFailed"] = True
    assert client.get("/projects/p/evidence-graphs").status_code == 503
    assert client.get("/projects/foreign/evidence-graphs").status_code == 404
    assert projects.PROJECTS_DB == {}
    assert state["queries"]
