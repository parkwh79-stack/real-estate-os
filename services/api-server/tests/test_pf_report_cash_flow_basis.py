"""An explicitly selected run remains the CF report basis after working data changes."""
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from routers import projects
from storage import upsert_calculation_run_record
import storage
from use_cases.cash_flow_comparison import content_hash
from db.repositories.report_repository import ReportPublicationConflict, PostgresReportRepository, ReportRepository
from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from use_cases.report_calculation_basis import attach_cash_flow_basis
from test_cash_flow_comparison import record, rehash
from test_postgres_report_publication import _postgres_repository


@pytest.fixture
def selected_run():
    client = TestClient(app)
    pid = client.post("/projects/", json={"project": {"name": "합성 PF 연결 검증"}}).json()["id"]
    run = record("chosen-cash-flow", project_id=pid)
    run["inputSnapshot"]["sourceMetadata"]["inputReview"] = {"fieldReviews": {
        "construction_cost": {"value": 400, "origin": "user_assumption", "reason": "단가 검토용 가정"}}}
    rehash(run)
    run = upsert_calculation_run_record(pid, run)
    # An unrelated current project result must never fill or replace selected fields.
    projects.project_repository().update(pid, {"cashFlowRunResult": {"summary": {"totalRevenue": 9999, "oldOnly": 7},
        "monthlyCashFlow": [{"monthIndex": 0, "netCashFlow": 9999}]}})
    selection = {"calculationRunId": run["calculationRunId"],
        "inputHash": content_hash(run["inputSnapshot"]), "resultHash": content_hash(run["resultSnapshot"])}
    return client, pid, run, selection


def generate(data):
    client, pid, _, selection = data
    return client.post(f"/projects/{pid}/reports/generate", json={"cashFlowBasis": selection})


def test_selected_run_saved_reopened_and_exported_without_current_project_merge(selected_run, monkeypatch):
    client, pid, run, _ = selected_run
    result = generate(selected_run)
    assert result.status_code == 200, result.text
    created = result.json()
    report, rid = created["report"], created["report_id"]
    assert report["cashFlowCalculationSnapshot"] == run
    assert report["cashFlowCalculationSnapshotHash"] == content_hash(run)
    cf = report["sections"]["summaryCashFlow"]
    assert cf["summary"] == run["resultSnapshot"]["summary"]
    assert cf["rows"] == run["resultSnapshot"]["monthlyCashFlow"]
    assert "다른 절" in cf["assumption"]
    assert "단가 검토용 가정" in created["report_markdown"]
    assert created["metadata"]["report_eligible"] is False
    persisted = ReportRepository(storage.load_state(project_workspace_only=True)["reports"], lambda: None)
    assert persisted.get(pid, rid)["report"] == report
    projects.project_repository().update(pid, {"feasibility_input": {"total_revenue": 99999}})
    upsert_calculation_run_record(pid, record(run["calculationRunId"], project_id=pid, construction=700))
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw: pytest.fail("Reopening must not read live runs"))
    reopened = client.get(f"/projects/{pid}/reports/{rid}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["report"] == report
    for fmt in ("json", "markdown", "html", "pdf", "docx"):
        exported = client.post(f"/projects/{pid}/reports/{rid}/exports", json={"format": fmt})
        assert exported.status_code == 200, exported.text
        download = client.get(f"/projects/{pid}/reports/{rid}/exports/{exported.json()['export_id']}/download")
        assert download.status_code == 200, download.text[:200]
        if fmt == "json":
            assert download.json()["report"] == report
        if fmt in {"markdown", "html"}:
            assert run["calculationRunId"] in download.text
            assert "단가 검토용 가정" in download.text
        if fmt == "pdf":
            assert download.content.startswith(b"%PDF-")
        if fmt == "docx":
            assert download.content.startswith(b"PK")


@pytest.mark.parametrize("field", ["inputHash", "resultHash"])
def test_changed_selection_hash_rejects_creation_without_saved_report(selected_run, field):
    client, pid, _, selection = selected_run
    selection[field] = "f" * 64
    assert generate(selected_run).status_code == 409
    assert client.get(f"/projects/{pid}/reports").json() == []


@pytest.mark.parametrize("mutation", ["missing", "foreign_project", "foreign_tenant", "incomplete", "wrong_engine", "invalid_hash"])
def test_invalid_or_unscoped_calculation_cannot_be_selected(selected_run, monkeypatch, mutation):
    _, _, run, _ = selected_run
    changed = deepcopy(run)
    if mutation == "foreign_project": changed["projectId"] = "another"
    if mutation == "foreign_tenant": changed["tenantId"] = "another"
    if mutation == "incomplete": changed["inputSnapshot"] = {}
    if mutation == "wrong_engine": changed["engineKey"] = "pf_metrics"
    if mutation == "invalid_hash": changed["financialInputHash"] = "f" * 64
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw: [] if mutation == "missing" else [changed])
    assert generate(selected_run).status_code == (404 if mutation in {"missing", "foreign_project", "foreign_tenant"} else 409)


@pytest.mark.parametrize("extra", [{"cashFlowRunResult": {}}, {"feasibility_input": {}}, {"report": {}}])
def test_selected_run_does_not_accept_client_values(selected_run, extra):
    client, pid, _, selection = selected_run
    response = client.post(f"/projects/{pid}/reports/generate", json={"cashFlowBasis": selection, **extra})
    assert response.status_code == 422


def test_selection_schema_and_authorization_checked_before_loading_run(selected_run, monkeypatch):
    client, pid, _, selection = selected_run
    bad = {**selection, "inputSnapshot": {}}
    assert client.post(f"/projects/{pid}/reports/generate", json={"cashFlowBasis": bad}).status_code == 422
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw: pytest.fail("No unauthorized reads"))
    original = projects.authorize_request
    def deny(request, permission):
        if permission == "report:export": raise HTTPException(403, "denied")
        return original(request, permission)
    monkeypatch.setattr(projects, "authorize_request", deny)
    assert generate(selected_run).status_code == 403


def test_project_scope_enforced_before_selected_calculation_read(selected_run, monkeypatch):
    def deny(*args, **kwargs):
        raise HTTPException(403, "tenant scope mismatch")
    monkeypatch.setattr(projects, "enforce_project_tenant_scope", deny)
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw: pytest.fail("No out-of-scope reads"))
    assert generate(selected_run).status_code == 403


def test_unavailable_calculation_store_does_not_create_a_partial_report(selected_run, monkeypatch):
    from db.repositories.calculation_run_repository import CalculationRunRepositoryError
    def fail(*args, **kwargs):
        raise CalculationRunRepositoryError("private storage details")
    monkeypatch.setattr(projects, "list_calculation_run_records", fail)
    response = generate(selected_run)
    assert response.status_code == 503
    assert "private storage details" not in response.text
    client, pid, _, _ = selected_run
    assert client.get(f"/projects/{pid}/reports").json() == []


@pytest.mark.parametrize("mutation", ["snapshot", "rows", "markdown", "missing_snapshot", "export", "metadata"])
def test_tampered_saved_report_is_not_reopened_or_downloaded(selected_run, mutation):
    client, pid, _, _ = selected_run
    created = generate(selected_run).json()
    rid = created["report_id"]
    exported = client.post(f"/projects/{pid}/reports/{rid}/exports", json={"format": "json"}).json()
    stored = projects.report_repository().get(pid, rid)
    if mutation == "snapshot": stored["report"]["cashFlowCalculationSnapshot"]["inputSnapshot"]["unit"] = "USD"
    if mutation == "rows": stored["report"]["sections"]["summaryCashFlow"]["rows"][0]["netCashFlow"] = 999
    if mutation == "markdown": stored["report_markdown"] += "tampered"
    if mutation == "missing_snapshot":
        stored["report"].pop("cashFlowCalculationSnapshot")
        stored["report"].pop("cashFlowCalculationSnapshotHash")
    if mutation == "export": stored["exports"][0]["content"] += "tampered"
    if mutation == "metadata": stored["metadata"]["cash_flow_snapshot_hash"] = "f" * 64
    assert client.get(f"/projects/{pid}/reports/{rid}").status_code == 409
    assert client.get(f"/projects/{pid}/reports/{rid}/exports/{exported['export_id']}/download").status_code == 409


def test_selected_cf_draft_cannot_be_published_through_either_repository(selected_run):
    client, pid, _, _ = selected_run
    rid = generate(selected_run).json()["report_id"]
    repository = projects.report_repository()
    saved = repository.get(pid, rid)
    postgres = PostgresReportRepository(tenant_id="tenant-a", transaction_factory=lambda _: pytest.fail("Must not publish"))
    for repo in (repository, postgres):
        with pytest.raises(ReportPublicationConflict):
            repo.publish(saved, published_by="checker", published_at="2026-10-15", decision_package={})
    assert client.post(f"/projects/{pid}/reports/{rid}/publish").status_code in (400, 409)


def test_postgres_round_trip_keeps_selected_basis_and_tenant_boundary():
    run = record(project_id="project-1", tenant="tenant-a")
    report = ReportAgent().generate_structured_report({})
    attach_cash_flow_basis(report, run)
    markdown = ReportExportAgent().export({"report": report}, "markdown")["content"]
    repository, database, transactions = _postgres_repository()
    saved = repository.create("project-1", report, markdown)
    reopened = repository.get("project-1", saved["report_id"])
    assert reopened["report"]["cashFlowCalculationSnapshot"] == run
    exported = repository.add_export(reopened, ReportExportAgent().export(reopened, "json"))
    reopened = repository.get("project-1", saved["report_id"])
    assert json.loads(repository.get_export(reopened, exported["export_id"])["content"])["report"] == report
    foreign = PostgresReportRepository(tenant_id="tenant-b", transaction_factory=transactions)
    assert foreign.get("project-1", saved["report_id"]) is None
    assert database.publications == {}
