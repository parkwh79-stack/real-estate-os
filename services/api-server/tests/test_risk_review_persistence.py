from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from main import app
import storage
from db.repositories.deal_state_repository import PostgresDealStateRepository


@pytest.fixture
def risk_review(tmp_path, monkeypatch, request):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", getattr(request, "param", "sqlite"))
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "risk-review.sqlite3"))
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(tmp_path / "risk-review.json"))
    client = TestClient(app)
    project = client.post("/projects/", json={"project": {"name": "위험 보완 저장 검증"}}).json()
    project_id = project["project"]["id"]
    storage.upsert_user({"userId": "follow-up-reviewer", "tenantId": "risk-review-test", "role": "reviewer", "status": "active"})
    headers = {"X-REALYTICS-USER-ID": "follow-up-reviewer"}
    register = {"risks": [{"riskId": "R-COST-001", "code": "COST_SOURCE_MISSING",
        "title": "공사비 근거 보완", "severity": "high", "status": "open", "reviewStatus": "open",
        "sourceLocator": {"documentId": "cost-evidence", "cell": "B10"}}]}
    storage.upsert_risk_register_record(project_id, "run-risk-follow-up", register)
    row = storage.list_deal_state_records(project_id, record_type="risk_issue")[0]
    yield client, project_id, headers, register, row
    client.delete(f"/projects/{project_id}")


def review(client, project_id, headers, row, *, status="needs_revision", comment="보완 사유: 공사비 근거가 없습니다.\n다음 할 일: 견적서 B10을 확인해 첨부합니다."):
    response = client.patch(
        f"/projects/{project_id}/deal-state-records/{row['recordId']}/review",
        headers=headers, json={"reviewStatus": status, "comment": comment},
    )
    assert response.status_code == 200, response.text
    return response.json()["record"]


def fetch(client, project_id):
    response = client.get(f"/projects/{project_id}/deal-state-records", params={"record_type": "risk_issue", "latest_only": True})
    assert response.status_code == 200
    return response.json()["records"][0]


@pytest.mark.parametrize("risk_review", ["sqlite", "json"], indirect=True)
def test_api_comment_history_survive_identical_risk_materialization(risk_review):
    client, project_id, headers, register, row = risk_review
    saved = review(client, project_id, headers, row)
    original = deepcopy(register)
    storage.upsert_risk_register_record(project_id, "run-risk-follow-up", register)
    reopened = fetch(client, project_id)
    for key in ("recordId", "comment", "reviewStatus", "reviewedBy", "reviewedAt", "reviewHistory"):
        assert reopened[key] == saved[key]
    assert register == original
    storage.upsert_risk_register_record(project_id, "run-risk-follow-up", register)
    assert fetch(client, project_id)["reviewHistory"] == saved["reviewHistory"]


@pytest.mark.parametrize("change", ["source", "body", "run", "new_id"])
def test_review_approval_does_not_propagate_to_changed_risk_or_source(risk_review, change):
    client, project_id, headers, register, row = risk_review
    saved = review(client, project_id, headers, row, status="approved", comment="원본 B10 검토 완료")
    changed = deepcopy(register)
    run_id = "run-risk-follow-up"
    if change == "source":
        changed["risks"][0]["sourceLocator"]["cell"] = "B11"
    elif change == "body":
        changed["risks"][0]["severity"] = "critical"
    elif change == "run":
        run_id = "new-risk-run"
    else:
        changed["risks"][0]["recordId"] = "new-risk-record"
    storage.upsert_risk_register_record(project_id, run_id, changed)
    reopened = fetch(client, project_id)
    assert reopened["reviewStatus"] == "open"
    assert not reopened.get("reviewedBy") and not reopened.get("reviewHistory")
    if change in {"run", "new_id"}:
        assert reopened["recordId"] != saved["recordId"]


@pytest.mark.parametrize("newer", [False, True])
def test_existing_orchestration_review_is_ordered_without_losing_deal_state_comment(risk_review, newer):
    client, project_id, headers, register, row = risk_review
    saved = review(client, project_id, headers, row)
    incoming = deepcopy(register)
    reviewed_at = (datetime.fromisoformat(saved["reviewedAt"]) + timedelta(seconds=1 if newer else -1)).isoformat()
    entry = {"reviewStatus": "resolved", "reviewedBy": "orchestration-reviewer", "reviewedAt": reviewed_at,
             "reviewComment": "해당 원본의 후속 확인 완료"}
    incoming["risks"][0].update({**entry, "status": "resolved", "requiresHumanReview": False, "reviewHistory": [entry]})
    storage.upsert_risk_register_record(project_id, "run-risk-follow-up", incoming)
    reopened = fetch(client, project_id)
    assert reopened["reviewStatus"] == ("resolved" if newer else "needs_revision")
    assert saved["reviewHistory"][0] in reopened["reviewHistory"]
    assert entry in reopened["reviewHistory"]
    assert len(reopened["reviewHistory"]) == 2
    assert reopened["reviewHistory"][-1]["reviewStatus"] == reopened["reviewStatus"]
    storage.upsert_risk_register_record(project_id, "run-risk-follow-up", incoming)
    assert fetch(client, project_id)["reviewHistory"] == reopened["reviewHistory"]


def test_direct_manual_upsert_still_replaces_record_instead_of_reusing_old_review(risk_review):
    client, project_id, headers, register, row = risk_review
    saved = review(client, project_id, headers, row)
    replacement = {**row, "comment": "직접 저장한 현재 메모", "reviewStatus": "needs_revision", "reviewHistory": []}
    storage.upsert_deal_state_record(project_id, replacement)
    reopened = fetch(client, project_id)
    assert reopened["comment"] != saved["comment"]
    assert reopened["comment"] == replacement["comment"] and reopened["reviewHistory"] == []


def test_materialization_lookup_does_not_miss_older_review_beyond_list_limit(risk_review):
    client, project_id, headers, register, row = risk_review
    saved = review(client, project_id, headers, row)
    storage.upsert_deal_state_records(project_id, [
        {"recordId": f"later-{index}", "recordType": "metric", "key": f"metric-{index}", "value": index}
        for index in range(1001)
    ])
    assert row["recordId"] not in {record["recordId"] for record in storage.list_deal_state_records(project_id, limit=1000)}
    storage.upsert_risk_register_record(project_id, "run-risk-follow-up", register)
    assert fetch(client, project_id)["comment"] == saved["comment"]


def test_postgres_exact_record_lookup_keeps_tenant_and_project_predicates():
    calls = []

    class Connection:
        def execute(self, sql, params):
            calls.append((sql, params))
            return self

        def fetchall(self):
            return []

    @contextmanager
    def transaction(tenant_id):
        assert tenant_id == "tenant-risk"
        yield Connection()

    assert PostgresDealStateRepository(tenant_id="tenant-risk", transaction_factory=transaction).list_records(
        "project-risk", record_id="risk-exact", limit=1,
    ) == []
    query, parameters = calls[0]
    assert "tenant_id = %s" in query and "project_id = %s" in query and "record_id = %s" in query
    assert parameters == ("tenant-risk", "project-risk", "risk-exact", 1)
