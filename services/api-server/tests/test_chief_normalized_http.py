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
        return {"authenticated": True, "user": {"tenantId": "tenant-a", "userId": "reviewer", "role": "analyst"}}

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
        "project_data": {
            "approvedAssumptionOverrides": [{"overrideId": "unverified", "approvedValue": 999}],
            "source_documents": [{"archiveId": "spoofed", "documentType": "business_plan"}],
        },
    })

    assert response.status_code == 200, response.text
    plan = response.json()
    assert len(plan["agentPods"]) == 6
    assert plan["taskGraph"]["workflowTrack"] == "pf"
    assumptions = plan["sharedDealStateGraph"]["assumptionBook"]
    assert [row["value"] for row in assumptions] == [100]
    assert assumptions[0]["sourceLocator"]["overrideId"] == "verified"
    assert plan["sharedDealStateGraph"]["evidenceVault"] == []
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


@pytest.mark.parametrize("run_count, status", [(0, "unassessed"), (1, "assessed")])
def test_chief_readiness_uses_project_server_records(normalized_chief_http, monkeypatch, run_count, status):
    client, authority, _calls = normalized_chief_http
    monkeypatch.delenv("REALYTICS_LEGAL_REVIEW_DATABASE_URL", raising=False)

    def evaluate(project_id, *, tenant_id):
        assert (project_id, tenant_id) == ("p", "tenant-a")
        return {
            "schemaVersion": "realytics-deal-state-approval-readiness-v1",
            "projectId": project_id,
            "readable": True,
            "latestCalculationRunCount": run_count,
            "legalEvidenceNormalizedReadable": True,
        }

    monkeypatch.setattr(approval_readiness_gate, "build_deal_state_approval_readiness", evaluate)
    response = client.get("/projects/p/chief-orchestration/approval-readiness")
    assert response.status_code == 200, response.text
    assert response.json()["assessmentStatus"] == status
    assert response.json()["legalEvidenceNormalizedReadable"] is True
    assert response.json()["legalReviewWriteAvailable"] is False
    assert response.json()["cureWriteAvailable"] is False
    assert authority.row["_meta"]["stateVersion"] == 1


def test_hosted_cure_routes_require_server_authority(normalized_chief_http, monkeypatch):
    client, authority, _calls = normalized_chief_http

    def legacy_store(*_args, **_kwargs):
        raise AssertionError("Hosted cure touched the compatibility store")

    monkeypatch.setattr(projects, "list_restructuring_cure_records", legacy_store)
    monkeypatch.setattr(projects, "upsert_restructuring_cure_record", legacy_store)
    assert projects.attach_active_restructuring_cures("p", authority.row) == authority.row
    for response in (
        client.get("/projects/p/restructuring-cures"),
        client.post("/projects/p/restructuring-cures", json={}),
        client.post("/projects/p/chief-orchestration/unsold-ltv-cure", json={}),
    ):
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["error"] == "restructuring_cure_authority_unavailable"
    assert authority.row["_meta"]["stateVersion"] == 1


def test_chief_failed_work_order_writes_scoped_status(normalized_chief_http, monkeypatch):
    client, authority, calls = normalized_chief_http
    authority.row["chief_orchestration_state"] = {
        "workOrders": [{"workOrderId": "work-1", "podKey": "land_legal", "status": "queued"}],
    }

    def fail_pod(project_id, pod_key, project_data, options, **_sinks):
        assert (project_id, pod_key) == ("p", "land_legal")
        assert options["actorRoles"] == ["analyst"]
        assert options["ai_context"]["user_id"] == "reviewer"
        assert all(key not in options for key in ("tenantId", "userId"))
        project_data["chief_orchestration_state"]["workOrders"][0]["status"] = "failed"
        raise RuntimeError("failed to produce an artifact")

    def forbid_legacy(*_args, **_kwargs):
        pytest.fail("Chief work-order failure used a local or legacy store")

    monkeypatch.setattr(projects, "upsert_project_record", forbid_legacy)
    monkeypatch.setattr(projects, "run_chief_deal_pod_use_case", fail_pod)
    response = client.post("/projects/p/chief-orchestration/run-pod", json={
        "podKey": "land_legal",
        "options": {"tenantId": "forged", "userId": "forged", "actorRoles": ["admin"]},
    })
    assert response.status_code == 502, response.text
    assert authority.row["chief_orchestration_state"]["workOrders"][0]["status"] == "failed"
    assert authority.row["_meta"]["stateVersion"] == 2
    assert "chief_pod_work_order_failed" in calls


def test_chief_reanalysis_uses_scoped_project_and_actor(normalized_chief_http, monkeypatch):
    client, authority, calls = normalized_chief_http

    def reanalyze(project_id, project_data, **kwargs):
        assert project_id == "p"
        assert kwargs["options"]["ai_context"]["user_id"] == "reviewer"
        assert kwargs["options"]["actorRoles"] == ["analyst"]
        assert kwargs["options"]["workflowTrack"] == "pf"
        project_data["chief_orchestration_state"] = {"podRuns": {"land_legal": {"status": "blocked"}}}
        return {"triggered": True, "rerunCount": 1}

    def forbid_legacy(*_args, **_kwargs):
        pytest.fail("Chief reanalysis used a local or legacy store")

    monkeypatch.setattr(projects, "upsert_project_record", forbid_legacy)
    monkeypatch.setattr(projects, "_completed_pod_keys", lambda _project_id: set())
    monkeypatch.setattr(projects, "reanalyze_after_evidence_use_case", reanalyze)
    response = client.post("/projects/p/chief-orchestration/reanalyze", json={
        "workflowTrack": "pf",
        "options": {"actorRoles": ["admin"]},
    })
    assert response.status_code == 200, response.text
    assert authority.row["chief_orchestration_state"]["podRuns"]["land_legal"]["status"] == "blocked"
    assert authority.row["_meta"]["stateVersion"] == 2
    assert "chief_orchestration_reanalyzed_on_evidence" in calls
