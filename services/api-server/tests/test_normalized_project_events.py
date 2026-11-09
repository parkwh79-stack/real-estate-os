"""Project writes must append scoped events without loading compatibility state."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import main
import storage
from routers import projects
from db.repositories.activity_log_repository import PostgresActivityLogRepository
from db.repositories.request_project_repository import project_request_scope
from test_request_project_scope import Authority
from use_cases.common import deep_merge


@pytest.fixture
def normalized_events(monkeypatch):
    authority = Authority("tenant-a")
    original_update = authority.update
    authority.update = lambda key, data, expected_state_version=None: original_update(
        key, deep_merge(deepcopy(authority.row), data), expected_state_version,
    )
    foreign_authority = Authority("tenant-b")
    foreign_authority.get = lambda _key: None
    calls = []

    class Connection:
        def execute(self, sql, params):
            calls.append((sql, params))
            return SimpleNamespace(fetchone=lambda: (params[0],))

    @contextmanager
    def transaction(tenant):
        assert tenant == "tenant-a"
        yield Connection()

    def identity(request):
        return {"authenticated": True, "user": {
            "tenantId": request.headers.get("x-test-tenant", "tenant-a"),
            "userId": "verified-reviewer",
        }}

    def authorize(request, permission):
        assert permission in {"project:read", "project:analyze", "assumption:override", "project:review"}
        if request.headers.get("x-test-deny"):
            raise HTTPException(status_code=403, detail="denied")
        return {**identity(request), "authorization": {"permission": permission}}

    def forbid_legacy(*_args, **_kwargs):
        raise AssertionError("Normalized project audit must not use legacy state")

    for module in (main, storage, projects):
        monkeypatch.setattr(module, "storage_backend", lambda: "postgres")
    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", identity)
        monkeypatch.setattr(module, "enforce_project_tenant_scope", lambda *_: None)
        monkeypatch.setattr(module, "record_user_activity_log", forbid_legacy)
    monkeypatch.setattr(storage, "_load_json_state", forbid_legacy)
    monkeypatch.setattr(projects, "PROJECTS_DB", {})
    monkeypatch.setattr(projects, "authorize_request", authorize)
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **kw:
                        authority if kw["tenant_id"] == "tenant-a" else foreign_authority)
    monkeypatch.setattr(projects, "PostgresActivityLogRepository", lambda **kw:
                        PostgresActivityLogRepository(**kw, transaction_factory=transaction))
    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_kw:
                        SimpleNamespace(append_api_request=lambda event: event["eventId"]))
    return authority, calls, TestClient(main.app)


def test_workbook_candidate_and_review_input_save_reopen_with_normalized_events(normalized_events):
    authority, calls, client = normalized_events
    candidate = {"feasibilityStudyV2Draft": {"project_id": "p", "project_name": "샘플 검토"}}
    denied = client.post("/projects/p/feasibility-workbook/import-candidate", json=candidate,
                         headers={"x-test-deny": "true"})
    assert denied.status_code == 403 and calls == []
    foreign = client.post("/projects/p/feasibility-workbook/import-candidate", json=candidate,
                          headers={"x-test-tenant": "tenant-b"})
    assert foreign.status_code == 404 and calls == []

    saved = client.post("/projects/p/feasibility-workbook/import-candidate", json=candidate)
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "candidate" and saved.json()["authoritative"] is False
    state = client.get("/projects/p/feasibility-workbook/review")
    assert state.status_code == 200, state.text
    applied = client.post("/projects/p/feasibility-workbook/apply-input", json={
        "expectedReviewHash": state.json()["reviewHash"],
        "feasibility_input": {"total_revenue": 200},
        "fieldReviews": {"total_revenue": {"origin": "user_assumption", "reason": "샘플 검토 가정"}},
    })
    assert applied.status_code == 200, applied.text
    reopened = client.get("/projects/p").json()
    assert reopened["feasibility_input"]["total_revenue"] == 200
    assert reopened["feasibilityStudyV2Draft"]["raw_import"]["candidateImport"]["recordedBy"] == "verified-reviewer"
    assert not reopened.get("canonicalFeasibilityStudyV2")
    assert len(calls) == 2
    assert [params[1] for _, params in calls] == ["feasibility_workbook_candidate_persisted", "feasibility_input_applied"]
    for sql, params in calls:
        assert "INSERT INTO realytics.user_activity_logs" in sql
        assert params[2:4] == ("verified-reviewer", "p") and params[-1] == "tenant-a"
    assert json.loads(calls[-1][1][-2])["appliedFields"] == ["total_revenue"]
    assert authority.row["_meta"]["stateVersion"] == 3
    assert projects.PROJECTS_DB == {}


def test_parsed_fact_review_keeps_actor_history_and_normalized_event(normalized_events, monkeypatch):
    _authority, calls, client = normalized_events
    saved_rows = {"fact-1": {"recordId": "fact-1", "recordType": "parsed_fact", "value": 100}}
    monkeypatch.setattr(projects, "list_deal_state_records", lambda *_args, **_kw: list(deepcopy(saved_rows).values()))
    def save(project_id, record):
        assert project_id == "p"
        saved_rows[record["recordId"]] = deepcopy(record)
        return deepcopy(record)
    monkeypatch.setattr(projects, "upsert_deal_state_record", save)
    response = client.patch("/projects/p/deal-state-records/fact-1/review", json={
        "reviewStatus": "needs_revision", "comment": "원본 셀 확인 필요", "reviewedBy": "forged-user",
    })
    assert response.status_code == 200, response.text
    saved = response.json()["record"]
    assert saved == saved_rows["fact-1"]
    assert saved["reviewHistory"][0]["reviewedBy"] == "verified-reviewer"
    assert saved["value"] == 100
    assert calls[0][1][1] == "deal_state_record_reviewed"
    assert json.loads(calls[0][1][-2])["recordId"] == "fact-1"


@pytest.mark.parametrize("case", ["missing_scope", "wrong_tenant", "unknown_project", "missing_user", "unauthenticated"])
def test_sensitive_event_rejects_unbound_authority_before_append(monkeypatch, case):
    monkeypatch.setattr(projects, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(projects, "identity_context_from_request", lambda _request: {
        "authenticated": case != "unauthenticated",
        "user": {"tenantId": "tenant-b" if case == "wrong_tenant" else "tenant-a",
                 "userId": "" if case == "missing_user" else "verified-reviewer"},
    })
    monkeypatch.setattr(projects, "PostgresActivityLogRepository", lambda **_kw: pytest.fail("Unbound audit reached SQL"))
    request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/projects/p/review",
                       "headers": [], "query_string": b"", "server": ("testserver", 80)})
    scope = nullcontext() if case == "missing_scope" else project_request_scope(lambda: Authority("tenant-a"))
    with scope, pytest.raises(ValueError):
        projects.record_sensitive_project_event(request, event_type="review_saved",
                                                project_id="outside" if case == "unknown_project" else "p")


def test_sensitive_event_keeps_local_compatibility_audit(monkeypatch):
    captured = []
    monkeypatch.setattr(projects, "storage_backend", lambda: "sqlite")
    monkeypatch.setattr(projects, "user_id_from_request", lambda _request: "local-reviewer")
    monkeypatch.setattr(projects, "record_user_activity_log", captured.append)
    request = Request({"type": "http", "method": "POST", "scheme": "http", "path": "/projects/p/review",
                       "headers": [], "query_string": b"", "server": ("testserver", 80)})
    projects.record_sensitive_project_event(request, event_type="review_saved", project_id="p", metadata={"key": "value"})
    assert captured[0]["eventType"] == "review_saved" and captured[0]["userId"] == "local-reviewer"
    assert captured[0]["metadata"] == {"key": "value"}
