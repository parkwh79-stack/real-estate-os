from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

import main
import storage
from db.repositories import deal_state_repository, runtime_record_repository
from routers import projects
from test_request_project_scope import Authority
from use_cases import approval_readiness_gate


@pytest.fixture
def normalized_chief_http(monkeypatch):
    authority = Authority("tenant-a")
    authority.row["project"]["name"] = "Saved PF project"
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("REALYTICS_AI_PROVIDER", "none")
    monkeypatch.setattr(projects, "PROJECTS_DB", {})
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **_: authority)

    def identity(*_args, **_kwargs):
        return {"authenticated": True, "user": {"tenantId": "tenant-a", "userId": "reviewer"}}

    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", identity)
        monkeypatch.setattr(module, "enforce_project_tenant_scope", lambda *_: None)
    monkeypatch.setattr(projects, "authorize_request", lambda *_: None)

    def forbid_legacy(*_args, **_kwargs):
        raise AssertionError("Chief plan used legacy app_state")

    monkeypatch.setattr(storage, "_load_json_state", forbid_legacy)
    calls = []

    class DealState:
        def __init__(self, *, tenant_id, **_kwargs):
            assert tenant_id == "tenant-a"

        def list_records(self, project_id, *, record_type, **_kwargs):
            assert project_id == "p"
            calls.append(record_type)
            return []

    class Connection:
        def execute(self, sql, params):
            if "realytics.assumption_overrides" in sql:
                assert params == ("tenant-a", "p")
                assert "status = 'active'" in sql
                calls.append("assumption_overrides")
                self.rows = [
                    ("tenant-a", "p", "verified", "cost", "cost.total", "active", "reviewer", None, None,
                     {"approvedValue": 100}),
                    ("tenant-a", "p", "expired", "cost", "cost.total", "active", "reviewer", None, None,
                     {"expiresAt": "2000-01-01T00:00:00Z", "approvedValue": 999}),
                ]
            else:
                assert "realytics.pod_ai_reviews" in sql
                assert params == ["p", "tenant-a", 200]
                calls.append("pod_ai_reviews")
                self.rows = []
            return self

        def fetchall(self):
            return self.rows

    @contextmanager
    def transaction(tenant_id):
        assert tenant_id == "tenant-a"
        yield Connection()

    class Activity:
        def append_api_request(self, event):
            return event["eventId"]

        def append_project_event(self, event_type, event):
            assert event["projectId"] == "p"
            calls.append(event_type)

    monkeypatch.setattr(deal_state_repository, "PostgresDealStateRepository", DealState)
    monkeypatch.setattr(approval_readiness_gate, "api_tenant_transaction", transaction)
    monkeypatch.setattr(runtime_record_repository, "api_tenant_transaction", transaction)
    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_: Activity())
    monkeypatch.setattr(projects, "PostgresActivityLogRepository", lambda **_: Activity())

    return TestClient(main.app), authority, calls


def test_chief_plan_reads_scoped_approved_overrides_without_legacy_state(normalized_chief_http):
    client, authority, calls = normalized_chief_http
    response = client.post("/projects/p/chief-orchestration/plan", json={
        "workflowTrack": "pf",
        "project_data": {"approvedAssumptionOverrides": [{"overrideId": "unverified", "approvedValue": 999}]},
    })

    assert response.status_code == 200, response.text
    plan = response.json()
    assert len(plan["agentPods"]) == 6
    assert plan["taskGraph"]["workflowTrack"] == "pf"
    assumptions = plan["sharedDealStateGraph"]["assumptionBook"]
    assert [row["value"] for row in assumptions] == [100]
    assert assumptions[0]["sourceLocator"]["overrideId"] == "verified"
    assert authority.row["sharedDealStateGraph"]["graph"]["assumptionBook"] == assumptions
    assert authority.row["_meta"]["stateVersion"] == 2
    assert calls == ["land_evidence", "parsed_fact", "assumption_overrides", "pod_ai_reviews", "chief_orchestration_plan_created"]


def test_chief_run_all_reports_missing_material_without_legacy_state(normalized_chief_http):
    client, authority, calls = normalized_chief_http
    response = client.post("/projects/p/chief-orchestration/run-all-pods", json={
        "workflowTrack": "pf", "options": {"podAiReviewMode": "deterministic"},
    })

    assert response.status_code == 200, response.text
    summary = response.json()["runAllSummary"]
    assert summary["requestedPodCount"] == 6
    assert summary["blockedCount"] > 0
    assert authority.row["_meta"]["stateVersion"] == 2
    assert authority.row["chief_orchestration_state"]["podRuns"]
    assert "chief_orchestration_all_pods_executed" in calls
