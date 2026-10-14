"""Frozen comparison drafts use persisted calculations, never a mutable project forecast."""
from copy import deepcopy
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from routers import cash_flow_comparison as api, projects
from db.repositories.report_repository import PostgresReportRepository, ReportPublicationConflict
from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from storage import get_project_record, list_calculation_run_records, upsert_calculation_run_record
from test_cash_flow_comparison import record, rehash
from test_postgres_report_publication import _postgres_repository
from use_cases.cash_flow_comparison import content_hash
from use_cases.report_generation import publish_saved_report_use_case


@pytest.fixture
def saved_comparison():
    client = TestClient(app)
    created = client.post("/projects/", json={"project": {"name": "합성 검증 프로젝트"}})
    assert created.status_code == 200
    pid = created.json()["id"]
    rows = []
    for name, cost in (("baseline", 400), ("changed", 500)):
        row = record(name, project_id=pid, construction=cost)
        row["inputSnapshot"]["reviewedMonthlyFinancingReview"] = {"reason": f"{name} 검토 이유", "reviewHash": "a" * 64}
        rehash(row)
        rows.append(upsert_calculation_run_record(pid, row))
    state = client.get(f"/projects/{pid}/cash-flow/comparison").json()
    saved = client.post(f"/projects/{pid}/cash-flow/comparison", json={
        "baselineRunId": "baseline", "comparisonRunId": "changed", "expectedComparisonHash": state["comparisonHash"]})
    assert saved.status_code == 200, saved.text
    return client, pid, rows


def create_draft(client, pid):
    state = client.get(f"/projects/{pid}/cash-flow/comparison")
    assert state.status_code == 200, state.text
    return client.post(f"/projects/{pid}/reports/cash-flow-comparison", json={"expectedComparisonHash": state.json()["comparisonHash"]})


def test_saved_comparison_creates_separate_four_section_draft_with_exact_frozen_sources(saved_comparison):
    client, pid, rows = saved_comparison
    original = deepcopy(get_project_record(pid))
    response = create_draft(client, pid)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "draft"
    assert body["template"] == "cash-flow-comparison-review-v1"
    assert body["metadata"]["report_eligible"] is False
    report = body["report"]
    snapshot = report["cashFlowComparisonSnapshot"]
    assert snapshot["baselineRun"] == rows[0]
    assert snapshot["comparisonRun"] == rows[1]
    assert snapshot["projectId"] == pid and snapshot["tenantId"] == "legacy"
    assert report["cashFlowComparisonSnapshotHash"] == content_hash(snapshot)
    assert len(report["sectionOrder"]) == 4
    assert report["reportEligible"] is False
    assert get_project_record(pid) == original
    unchanged = list_calculation_run_records(pid, engine_key="cash_flow_engine", limit=20)
    assert {row["calculationRunId"]: row for row in unchanged} == {row["calculationRunId"]: row for row in rows}
    assert "기준안" in body["report_markdown"] and "검토용 초안" in body["report_markdown"]
    assert "baseline 검토 이유" in body["report_markdown"] and "changed 검토 이유" in body["report_markdown"]
    for raw_path in ("inputSnapshot", "totalProjectCost", "pfStructure", "resultSnapshot"):
        assert raw_path not in body["report_markdown"]
    reopened = client.get(f"/projects/{pid}/reports/{body['report_id']}")
    assert reopened.status_code == 200
    assert reopened.json()["report"] == report
    assert reopened.json()["report_markdown"] == body["report_markdown"]
    assert reopened.json()["run_id"] is None


def test_draft_reopen_and_exports_survive_working_selection_and_original_run_changes(saved_comparison, monkeypatch):
    client, pid, _ = saved_comparison
    created = create_draft(client, pid).json()
    report_id = created["report_id"]
    projects.project_repository().update(pid, {"feasibility_input": {"total_revenue": 999_999}, "_meta": {"cashFlowComparison": None}})
    upsert_calculation_run_record(pid, record("changed", project_id=pid, construction=700))
    monkeypatch.setattr(api, "_records", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("saved draft must not reread live calculations")))

    reopened = client.get(f"/projects/{pid}/reports/{report_id}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["report"] == created["report"]
    assert reopened.json()["report_markdown"] == created["report_markdown"]
    for fmt in ("markdown", "json", "html", "pdf", "docx"):
        exported = client.post(f"/projects/{pid}/reports/{report_id}/exports", json={"format": fmt})
        assert exported.status_code == 200, exported.text
        data = client.get(f"/projects/{pid}/reports/{report_id}/exports/{exported.json()['export_id']}/download")
        assert data.status_code == 200
        if fmt == "markdown":
            assert data.text == created["report_markdown"]
        if fmt == "json":
            assert data.json()["report"] == created["report"]
        if fmt == "pdf":
            assert data.content.startswith(b"%PDF-")
        if fmt == "docx":
            assert data.content.startswith(b"PK")


def test_draft_requires_current_state_and_saved_selection_without_writing(saved_comparison):
    client, pid, _ = saved_comparison
    state = client.get(f"/projects/{pid}/cash-flow/comparison").json()
    projects.project_repository().update(pid, {"feasibility_input": {"total_revenue": 123}})
    stale = client.post(f"/projects/{pid}/reports/cash-flow-comparison", json={"expectedComparisonHash": state["comparisonHash"]})
    assert stale.status_code == 409
    projects.project_repository().update(pid, {"_meta": {"cashFlowComparison": None}})
    missing = create_draft(client, pid)
    assert missing.status_code == 409
    assert client.get(f"/projects/{pid}/reports").json() == []


def test_changed_saved_source_blocks_new_draft_and_missing_source_is_404(saved_comparison):
    client, pid, _ = saved_comparison
    upsert_calculation_run_record(pid, record("changed", project_id=pid, construction=900))
    assert create_draft(client, pid).status_code == 409
    selection = deepcopy(get_project_record(pid)["_meta"]["cashFlowComparison"])
    selection["comparisonRunId"] = "does-not-exist"
    projects.project_repository().update(pid, {"_meta": {"cashFlowComparison": selection}})
    assert create_draft(client, pid).status_code == 404
    assert client.get(f"/projects/{pid}/reports").json() == []


@pytest.mark.parametrize("extra", [{"baselineRunId": "spoof"}, {"report": {}}, {"snapshot": {}}, {"run_id": "approved-run"}])
def test_client_cannot_supply_calculation_results_or_bypass_draft_policy(saved_comparison, extra):
    client, pid, _ = saved_comparison
    state = client.get(f"/projects/{pid}/cash-flow/comparison").json()
    response = client.post(f"/projects/{pid}/reports/cash-flow-comparison", json={"expectedComparisonHash": state["comparisonHash"], **extra})
    assert response.status_code == 422


def test_report_export_permission_and_project_tenant_are_required(saved_comparison, monkeypatch):
    client, pid, _ = saved_comparison
    state = client.get(f"/projects/{pid}/cash-flow/comparison").json()
    original = api.authorize_request
    def deny(request, permission):
        if permission == "report:export":
            raise HTTPException(403, "report export denied")
        return original(request, permission)
    monkeypatch.setattr(api, "authorize_request", deny)
    endpoint = f"/projects/{pid}/reports/cash-flow-comparison"
    assert client.post(endpoint, json={"expectedComparisonHash": state["comparisonHash"]}).status_code == 403
    monkeypatch.setattr(api, "authorize_request", original)
    projects.project_repository().update(pid, {"_meta": {"tenantId": "tenant-a"}})
    monkeypatch.setattr(api, "enforce_project_tenant_scope", lambda *args: {"user": {"tenantId": "tenant-b"}})
    assert client.post(endpoint, json={"expectedComparisonHash": state["comparisonHash"]}).status_code == 403


def test_foreign_project_report_and_calculation_cannot_be_reused(saved_comparison):
    client, pid, _ = saved_comparison
    created = create_draft(client, pid).json()
    foreign = client.post("/projects/", json={"project": {"name": "다른 프로젝트"}}).json()["id"]
    assert client.get(f"/projects/{foreign}/reports/{created['report_id']}").status_code == 404
    assert client.post(f"/projects/{foreign}/reports/{created['report_id']}/exports", json={"format": "json"}).status_code == 404
    upsert_calculation_run_record(foreign, record("foreign-run", project_id=foreign))
    selection = deepcopy(get_project_record(pid)["_meta"]["cashFlowComparison"])
    selection["comparisonRunId"] = "foreign-run"
    projects.project_repository().update(pid, {"_meta": {"cashFlowComparison": selection}})
    assert create_draft(client, pid).status_code == 404


@pytest.mark.parametrize("part", ["markdown", "snapshot", "source", "section", "tenant", "missing_body"])
def test_tampered_frozen_draft_is_rejected_on_resume_and_export(saved_comparison, part):
    client, pid, _ = saved_comparison
    created = create_draft(client, pid).json()
    rid = created["report_id"]
    stored = next(row for row in projects.REPORTS_DB[pid] if row["report_id"] == rid)
    if part == "markdown":
        stored["report_markdown"] += "\nmodified"
    elif part == "snapshot":
        stored["report"]["cashFlowComparisonSnapshot"]["baselineRun"]["resultSnapshot"]["summary"]["totalRevenue"] = 7
    elif part == "source":
        stored["report"]["cashFlowComparisonSnapshot"]["baselineRun"]["sourceLocator"] = {"source": "modified"}
    elif part == "section":
        stored["report"]["sections"]["comparisonSummary"]["주요 지표"][0]["기준안"] = "7원"
    elif part == "tenant":
        stored["report"]["cashFlowComparisonSnapshot"]["tenantId"] = "tenant-other"
    else:
        stored["report"] = {}
    response = client.get(f"/projects/{pid}/reports/{rid}")
    assert response.status_code == 409, response.text
    assert response.headers["cache-control"] == "no-store"
    assert client.post(f"/projects/{pid}/reports/{rid}/exports", json={"format": "json"}).status_code == 409


def test_tampered_export_bytes_are_not_downloaded(saved_comparison):
    client, pid, _ = saved_comparison
    rid = create_draft(client, pid).json()["report_id"]
    exported = client.post(f"/projects/{pid}/reports/{rid}/exports", json={"format": "markdown"}).json()
    stored = next(row for row in projects.REPORTS_DB[pid] if row["report_id"] == rid)
    stored["exports"][0]["content"] += "changed"
    assert client.get(f"/projects/{pid}/reports/{rid}/exports/{exported['export_id']}/download").status_code == 409


def test_comparison_draft_cannot_be_promoted_through_use_case_or_direct_repository(saved_comparison):
    client, pid, _ = saved_comparison
    rid = create_draft(client, pid).json()["report_id"]
    repo = projects.report_repository()
    saved = repo.get(pid, rid)
    with pytest.raises(ReportPublicationConflict):
        publish_saved_report_use_case(repo, None, None, pid, rid, published_by="checker", published_at=datetime.now(timezone.utc).isoformat())
    with pytest.raises(ReportPublicationConflict):
        repo.publish(saved, published_by="checker", published_at="2026-01-01", decision_package={})
    postgres = PostgresReportRepository(tenant_id="tenant-a", transaction_factory=lambda _: (_ for _ in ()).throw(AssertionError("must not start publication")))
    with pytest.raises(ReportPublicationConflict):
        postgres.publish(saved, published_by="checker", published_at="2026-01-01", decision_package={})


def test_absent_review_reasons_are_visible_in_document_sections(saved_comparison):
    client, pid, _ = saved_comparison
    snapshot = create_draft(client, pid).json()["report"]["cashFlowComparisonSnapshot"]
    for side in ("baselineRun", "comparisonRun"):
        snapshot[side]["inputSnapshot"].pop("reviewedMonthlyFinancingReview")
    generated = ReportAgent().generate_cash_flow_comparison_review(snapshot, content_hash(snapshot))
    assumptions = generated["structuredReport"]["sections"]["assumptions"]
    assert assumptions["기준안 검토 이유"] == "미기록"
    assert assumptions["변경안 검토 이유"] == "미기록"
    assert "기준안 검토 이유: 미기록" in generated["reportMarkdown"]
    assert "변경안 검토 이유: 미기록" in generated["reportMarkdown"]


def test_unstructured_legacy_source_annotations_stay_raw_and_render_as_unknown(saved_comparison):
    client, pid, _ = saved_comparison
    snapshot = create_draft(client, pid).json()["report"]["cashFlowComparisonSnapshot"]
    snapshot["baselineRun"]["inputSnapshot"].update(sourceMetadata=["unstructured source"], project=["legacy note"])
    snapshot["baselineRun"]["sourceLocator"] = ["unstructured locator"]
    snapshot["comparisonRun"]["inputSnapshot"]["sourceMetadata"]["sourceName"] = {"note": "legacy"}
    generated = ReportAgent().generate_cash_flow_comparison_review(snapshot, content_hash(snapshot))
    assert generated["structuredReport"]["cashFlowComparisonSnapshot"] == snapshot
    assert all(row["원본"] == "원본 문서명 미확인" for row in generated["structuredReport"]["sections"]["sources"]["저장된 원본"])
    assert "unstructured" not in generated["reportMarkdown"]


def test_postgres_comparison_draft_round_trip_and_export_are_tenant_bound(saved_comparison):
    client, pid, _ = saved_comparison
    snapshot = create_draft(client, pid).json()["report"]["cashFlowComparisonSnapshot"]
    snapshot.update(projectId="project-1", tenantId="tenant-a")
    for side in ("baselineRun", "comparisonRun"):
        snapshot[side].update(projectId="project-1", tenantId="tenant-a")
    generated = ReportAgent().generate_cash_flow_comparison_review(snapshot, content_hash(snapshot))
    repository, database, transactions = _postgres_repository()
    saved = repository.create("project-1", generated["structuredReport"], generated["reportMarkdown"])
    reopened = repository.get("project-1", saved["report_id"])
    assert reopened == saved
    assert reopened["run_id"] is None
    exported = repository.add_export(reopened, ReportExportAgent().export(reopened, "docx"))
    reopened = repository.get("project-1", saved["report_id"])
    assert repository.get_export(reopened, exported["export_id"]) == exported
    assert set(transactions.tenant_ids) == {"tenant-a"}
    assert database.publications == {}
    foreign = PostgresReportRepository(tenant_id="tenant-b", transaction_factory=transactions)
    assert foreign.get("project-1", saved["report_id"]) is None
    database.reports[("tenant-a", "project-1", saved["report_id"])]["payload_json"]["report_markdown"] += "modified"
    with pytest.raises(ReportPublicationConflict):
        repository.get("project-1", saved["report_id"])
