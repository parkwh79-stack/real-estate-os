from copy import deepcopy

from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import storage
from db.repositories import deal_state_repository
from routers import projects
from test_request_project_scope import Authority


def test_hosted_land_note_and_source_audit_preserve_retries_and_saved_report_scope(monkeypatch):
    authority = Authority("tenant-a")
    authority.row["landLedger"] = {"summary": {"totalPurchaseAmount": 100}, "parcels": []}
    original_update = authority.update
    writes = []

    def update(key, data, expected_state_version=None):
        writes.append(deepcopy(data))
        return original_update(key, {**deepcopy(authority.row), **data}, expected_state_version)

    authority.update = update
    identity = {"authenticated": True, "user": {"tenantId": "tenant-a", "userId": "verified-reviewer"}}
    for module in (main, projects, storage):
        monkeypatch.setattr(module, "storage_backend", lambda: "postgres")
    for module in (main, projects):
        monkeypatch.setattr(module, "identity_context_from_request", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(projects, "PostgresProjectRepository", lambda **_kwargs: authority)
    monkeypatch.setattr(projects, "PROJECTS_DB", {})
    monkeypatch.setattr(projects, "REPORTS_DB", {"p": [{"report": {"paragraphEvidenceLinks": {"links": []}}}]})
    authorized = True

    def authorize(*_args, **_kwargs):
        if not authorized:
            raise HTTPException(status_code=403, detail="Review permission required")
        return identity

    monkeypatch.setattr(projects, "authorize_request", authorize)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Hosted review audit used compatibility state or logging")

    monkeypatch.setattr(storage, "_load_json_state", forbidden)
    monkeypatch.setattr(projects, "record_user_activity_log", forbidden)
    monkeypatch.setattr(projects, "_hydrated_project_source", lambda _pid, _opts, *, authoritative_source: authoritative_source)
    # The source reconciliation itself has independent workbook coverage.
    monkeypatch.setattr(projects, "reconcile_land_costs_use_case", lambda _project: {
        "comparisonHash": "source-sha256", "ledgerContract": {"value": 100},
    })

    events = []

    class Activity:
        def __init__(self, *, tenant_id, user_id):
            assert (tenant_id, user_id) == ("tenant-a", "verified-reviewer")

        def append_project_event(self, kind, event):
            events.append((kind, event))

        def append_api_request(self, event):
            return event["eventId"]

    monkeypatch.setattr(projects, "PostgresActivityLogRepository", Activity)
    monkeypatch.setattr(main, "normalized_activity_repository", lambda **_kwargs: Activity(**_kwargs))
    client = TestClient(main.app)
    note_url = "/projects/p/land-cost-review"
    payload = {"comment": "원본 금액 확인", "expectedComparisonHash": "source-sha256", "reviewedBy": "spoofed"}
    saved = client.put(note_url, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["review"]["reviewedBy"] == "verified-reviewer"
    retry = client.put(note_url, json=payload)
    assert retry.status_code == 200, retry.text
    assert retry.json() == saved.json()
    assert len(writes) == 1
    assert len(authority.row["landCostReview"]["reviewHistory"]) == 1
    assert client.put(note_url, json={**payload, "expectedComparisonHash": "changed"}).status_code == 409
    assert len(writes) == 1

    records = {}
    report_reads = []

    class DealState:
        def __init__(self, *, tenant_id, **_kwargs):
            assert tenant_id == "tenant-a"

        def list_records(self, project_id, **_kwargs):
            assert project_id == "p"
            return deepcopy(list(records.values()))

        def upsert_records(self, project_id, rows):
            assert project_id == "p"
            for row in rows:
                assert row["tenantId"] == "tenant-a"
                records[row["recordId"]] = deepcopy(row)
            return rows

    class Reports:
        def __init__(self, *, tenant_id):
            assert tenant_id == "tenant-a"

        def list_metadata(self, project_id):
            assert project_id == "p"
            return [{"report_id": "stored-report"}]

        def get(self, project_id, report_id):
            report_reads.append((project_id, report_id))
            return {"report_id": report_id, "version": 1, "report": {"paragraphEvidenceLinks": {"links": [
                {"paragraphId": "weak-paragraph", "sourceLocatorCoverage": {"linked": 0, "missingLocator": 1}},
            ]}}}

    monkeypatch.setattr(deal_state_repository, "PostgresDealStateRepository", DealState)
    monkeypatch.setattr(projects, "PostgresReportRepository", Reports)
    audit_url = "/projects/p/deal-state-records/source-locator-audit"
    audited = client.post(audit_url)
    assert audited.status_code == 200, audited.text
    assert audited.json()["checkedBy"] == "verified-reviewer"
    assert audited.json()["weakParagraphCount"] == 1
    assert audited.json()["weakParagraphExamples"][0]["reportId"] == "stored-report"
    assert audited.json()["evidenceTraceWeakRisk"]["reviewStatus"] == "open"
    assert report_reads == [("p", "stored-report")]
    assert len(records) == 2
    assert client.post(audit_url).status_code == 200
    assert len(records) == 2
    assert events[-1][0] == "source_locator_completeness_audited"
    assert events[-1][1]["metadata"]["checkedReportCount"] == 1

    plan_url = "/projects/p/kakao/intake-plan"
    plan_preview = client.post(plan_url, json={})
    assert plan_preview.status_code == 200, plan_preview.text
    assert set(plan_preview.json()) == {"plan", "kakaoMessage"}
    assert events[-1][0] == "kakao_message_preview"
    assert events[-1][1]["metadata"]["messageMode"] == "preview_only"
    assert len(writes) == 1
    assert client.post("/projects/foreign/kakao/intake-plan", json={}).status_code == 404

    authorized = False
    assert client.post(plan_url, json={}).status_code == 403
    assert client.post(audit_url).status_code == 403
    assert client.put(note_url, json=payload).status_code == 403
    assert len(writes) == 1
    assert client.post("/projects/foreign/deal-state-records/source-locator-audit").status_code == 404
    assert projects.PROJECTS_DB == {}
