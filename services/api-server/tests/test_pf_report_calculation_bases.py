"""Report selections bind stored engine requests/results through save and export."""
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException

from routers import projects
from storage import upsert_calculation_run_record
import storage
from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from core.calculations import calculate_feasibility_metrics
from db.repositories.report_repository import ReportRepository, ReportPublicationConflict, PostgresReportRepository
from use_cases.cash_flow_comparison import content_hash
from use_cases.report_calculation_basis import attach_calculation_bases, calculation_issue
from test_calculation_input_snapshots import project, saved_run, change_project
from test_postgres_report_publication import _postgres_repository


def feasibility_record(project_id="project-1", tenant="tenant-a"):
    source = {"total_revenue": 2800, "total_cost": 1800}
    return {"calculationRunId": "saved-feasibility", "projectId": project_id, "tenantId": tenant,
            "engineKey": "feasibility", "status": "completed", "inputSnapshot": source,
            "inputDigest": {"capturePolicy": "effective_request_v1"},
            "resultSnapshot": calculate_feasibility_metrics(source)}


@pytest.fixture
def selected(project):
    client, pid, _ = project
    ids = {}
    for slot, path in (("feasibility", "feasibility/calculate"), ("pf", "pf/structure"), ("returns", "returns/calculate")):
        response = client.post(f"/projects/{pid}/{path}", json={})
        assert response.status_code == 200, response.text
        ids[slot] = response.json()["calculationRunId"]
    response = client.post(f"/projects/{pid}/scenarios/recovery", json={"assumptionIds": ["synthetic-scenario-assumption"]})
    assert response.status_code == 200, response.text
    ids.update(response.json()["calculationRunIds"])
    runs = {slot: saved_run(client, pid, rid) for slot, rid in ids.items()}
    selections = {slot: {"calculationRunId": run["calculationRunId"],
        "inputHash": content_hash(run["inputSnapshot"]), "resultHash": content_hash(run["resultSnapshot"])} for slot, run in runs.items()}
    return client, pid, runs, selections


def test_all_sections_save_reopen_export_same_calculations_after_project_changes(selected, project, monkeypatch):
    client, pid, runs, selections = selected
    choices = client.get(f"/projects/{pid}/reports/calculation-bases")
    assert choices.status_code == 200, choices.text
    assert len(choices.json()["records"]) == 6
    assert all(row["eligible"] for row in choices.json()["records"])
    assert "resultSnapshot" not in choices.text
    change_project(project)
    response = client.post(f"/projects/{pid}/reports/generate", json={"calculationBases": selections})
    assert response.status_code == 200, response.text
    created = response.json()
    report, rid = created["report"], created["report_id"]
    assert report["calculationSnapshots"] == runs
    business = report["sections"]["businessFeasibility"]
    assert business["사업성 요약"]["저장된 결과"]["total_revenue"] == 2800
    assert business["PF 지표"]["저장된 결과"] == runs["pf"]["resultSnapshot"]
    assert business["투자 수익률"]["저장된 결과"] == runs["returns"]["resultSnapshot"]
    assert business["미분양 LTV 시나리오"]["저장된 결과"] == runs["unsold_ltv"]["resultSnapshot"]
    assert report["sections"]["salesRevenueDistribution"]["상환순서 시나리오"]["저장된 결과"] == runs["waterfall"]["resultSnapshot"]
    assert report["sections"]["exitStrategy"]["트랜치 회수 시나리오"]["저장된 결과"] == runs["tranche_recovery"]["resultSnapshot"]
    assert "synthetic-scenario-assumption" in created["report_markdown"]
    persisted = ReportRepository(storage.load_state(project_workspace_only=True)["reports"], lambda: None)
    assert persisted.get(pid, rid)["report"] == report
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw: pytest.fail("Do not rebind saved reports"))
    assert client.get(f"/projects/{pid}/reports/{rid}").json()["report"] == report
    for fmt in ("json", "markdown", "html", "pdf", "docx"):
        exported = client.post(f"/projects/{pid}/reports/{rid}/exports", json={"format": fmt})
        assert exported.status_code == 200, exported.text
        downloaded = client.get(f"/projects/{pid}/reports/{rid}/exports/{exported.json()['export_id']}/download")
        assert downloaded.status_code == 200
        if fmt == "json": assert downloaded.json()["report"]["calculationSnapshots"] == runs
        if fmt in {"markdown", "html"}: assert all(run["calculationRunId"] in downloaded.text for run in runs.values())
        if fmt == "pdf": assert downloaded.content.startswith(b"%PDF-")
        if fmt == "docx": assert downloaded.content.startswith(b"PK")


def test_partial_selection_never_fills_unselected_sections_with_current_project(selected):
    client, pid, runs, selections = selected
    result = client.post(f"/projects/{pid}/reports/generate", json={"calculationBases": {"pf": selections["pf"]}})
    assert result.status_code == 200, result.text
    report = result.json()["report"]
    assert set(report["calculationSnapshots"]) == {"pf"}
    business = report["sections"]["businessFeasibility"]
    assert business["사업성 요약"] == {"상태": "미확인 — 저장 계산 미선택"}
    assert business["투자 수익률"] == {"상태": "미확인 — 저장 계산 미선택"}
    assert "waterfall" not in report["sections"]["salesRevenueDistribution"]


def test_incomplete_metric_results_remain_unavailable(selected):
    _, _, runs, _ = selected
    for slot in ("pf", "returns"):
        run = deepcopy(runs[slot])
        run["resultSnapshot"]["metrics"] = {}
        assert calculation_issue(run, slot) is not None
    run = deepcopy(runs["waterfall"])
    run["resultSnapshot"]["rows"] = []
    assert calculation_issue(run, "waterfall") is not None


@pytest.mark.parametrize("mutation,status", [("stale", 409), ("wrong_engine", 409), ("missing", 404),
    ("foreign_project", 404), ("foreign_tenant", 404), ("legacy", 409), ("corrupt", 409)])
def test_selection_failure_is_atomic(selected, monkeypatch, mutation, status):
    client, pid, runs, selections = selected
    changed = deepcopy(runs["pf"])
    if mutation == "stale": selections["pf"]["inputHash"] = "f" * 64
    if mutation == "wrong_engine": changed["engineKey"] = "return_metrics"
    if mutation == "foreign_project": changed["projectId"] = "another"
    if mutation == "foreign_tenant": changed["tenantId"] = "another"
    if mutation == "legacy": changed["inputDigest"] = {}
    if mutation == "corrupt": changed["inputSnapshot"] = {}
    original = projects.list_calculation_run_records
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw:
        ([] if mutation == "missing" else [changed]) if kw.get("calculation_run_id") == changed["calculationRunId"] else original(*a, **kw))
    response = client.post(f"/projects/{pid}/reports/generate", json={"calculationBases": selections})
    assert response.status_code == status, response.text
    assert client.get(f"/projects/{pid}/reports").json() == []


@pytest.mark.parametrize("payload", [{"calculationBases": {}}, {"calculationBases": {"unknown": {}}},
    {"calculationBases": None}, {"calculationBases": {"pf": {"inputSnapshot": {}}}}])
def test_selection_contract_rejects_invalid_or_client_supplied_results(project, payload):
    client, pid, _ = project
    assert client.post(f"/projects/{pid}/reports/generate", json=payload).status_code == 422


def test_legacy_choices_disabled_and_permission_checked_before_reads(selected, monkeypatch):
    client, pid, runs, selections = selected
    run = runs["pf"]
    run["inputDigest"] = {}
    upsert_calculation_run_record(pid, run)
    choices = client.get(f"/projects/{pid}/reports/calculation-bases").json()["records"]
    assert next(row for row in choices if row["slot"] == "pf")["eligible"] is False
    original = projects.authorize_request
    def deny(request, permission):
        if permission in {"project:read", "report:export"}: raise HTTPException(403, "denied")
        return original(request, permission)
    monkeypatch.setattr(projects, "authorize_request", deny)
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw: pytest.fail("Unauthorized read"))
    assert client.get(f"/projects/{pid}/reports/calculation-bases").status_code == 403
    assert client.post(f"/projects/{pid}/reports/generate", json={"calculationBases": selections}).status_code == 403


@pytest.mark.parametrize("mutation", ["snapshot", "section", "removed", "metadata", "export"])
def test_integrity_and_publication_guards(selected, mutation):
    client, pid, _, selections = selected
    created = client.post(f"/projects/{pid}/reports/generate", json={"calculationBases": selections}).json()
    rid = created["report_id"]
    eid = client.post(f"/projects/{pid}/reports/{rid}/exports", json={"format": "json"}).json()["export_id"]
    repo = projects.report_repository()
    saved = repo.get(pid, rid)
    postgres = PostgresReportRepository(tenant_id="tenant-a", transaction_factory=lambda _: pytest.fail("No final publication"))
    for repository in (repo, postgres):
        with pytest.raises(ReportPublicationConflict):
            repository.publish(saved, published_by="checker", published_at="2026-10-15", decision_package={})
    if mutation == "snapshot": saved["report"]["calculationSnapshots"]["pf"]["inputSnapshot"]["total_revenue"] = 123
    if mutation == "section": saved["report"]["sections"]["businessFeasibility"]["PF 지표"]["저장된 결과"] = {}
    if mutation == "removed":
        saved["report"].pop("calculationSnapshots")
        saved["report"].pop("calculationSnapshotsHash")
    if mutation == "metadata": saved["metadata"]["calculation_snapshots_hash"] = "f" * 64
    if mutation == "export": saved["exports"][0]["content"] += "tamper"
    assert client.get(f"/projects/{pid}/reports/{rid}").status_code == 409
    assert client.get(f"/projects/{pid}/reports/{rid}/exports/{eid}/download").status_code == 409


def test_postgres_roundtrip_and_tenant_scope():
    report = ReportAgent().generate_structured_report({})
    runs = {"feasibility": feasibility_record()}
    attach_calculation_bases(report, runs)
    repository, _, transactions = _postgres_repository()
    saved = repository.create("project-1", report, ReportExportAgent().export({"report": report}, "markdown")["content"])
    reopened = repository.get("project-1", saved["report_id"])
    assert reopened["report"]["calculationSnapshots"] == runs
    exported = repository.add_export(reopened, ReportExportAgent().export(reopened, "json"))
    assert json.loads(repository.get_export(repository.get("project-1", saved["report_id"]), exported["export_id"])["content"])["report"] == report
    foreign = PostgresReportRepository(tenant_id="tenant-b", transaction_factory=transactions)
    assert foreign.get("project-1", saved["report_id"]) is None


def test_chief_developer_result_keeps_source_snapshot_and_quality(monkeypatch):
    from test_chief_cash_flow_input_capture import project as chief_project, execute, records
    data = chief_project()
    artifact, _ = execute(monkeypatch, data)
    run = next(row for row in records(data, artifact) if row["engineKey"] == "development_feasibility")
    assert calculation_issue(run, "feasibility") is None
    report = ReportAgent().generate_structured_report({})
    attach_calculation_bases(report, {"feasibility": run})
    result = report["sections"]["businessFeasibility"]["사업성 요약"]["저장된 결과"]
    assert result["dataQuality"] == run["resultSnapshot"]["dataQuality"]
    assert "cashFlowRunResult" not in result
    run["inputSnapshot"]["developmentFeasibilityVersion"] = "tampered"
    assert calculation_issue(run, "feasibility") is not None


def test_chief_pf_and_scenario_runs_keep_their_executed_inputs_and_run_references():
    from test_chief_pf_input_capture import project as chief_project, records
    from use_cases.chief_deal_orchestration import _execute_pod_artifact
    data = chief_project()
    artifact = _execute_pod_artifact("pf-input-capture", "pf_credit", data, {})
    runs = {"pf" if row["engineKey"] == "pf_structure" else row["engineKey"]: row for row in records(data, artifact)}
    assert set(runs) == {"pf", "waterfall", "unsold_ltv", "tranche_recovery"}
    for slot, run in runs.items():
        assert calculation_issue(run, slot) is None
    report = ReportAgent().generate_structured_report({})
    attach_calculation_bases(report, runs)
    assert report["calculationSnapshots"] == runs
    assert all(row["원본 위치"]["runId"] == "chief-pf-run" for row in report["calculationSources"])
