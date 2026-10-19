from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree

import pytest

from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent
from db.repositories.report_repository import ReportRepository, verify_report_snapshots
from routers import projects
from use_cases.cash_flow_comparison import content_hash
from use_cases.report_calculation_basis import attach_cash_flow_basis, attach_calculation_bases
from use_cases.report_financing_basis import attach_financing_basis, verify_financing_basis
from test_cash_flow_comparison import record, rehash
from test_pf_report_calculation_bases import selected, project, feasibility_record
from test_pf_report_cash_flow_basis import selected_run
from test_postgres_report_publication import _postgres_repository


def combined_report():
    report = ReportAgent().generate_structured_report({})
    cf = record()
    cf["inputSnapshot"]["sourcesAndUses"]["funding"]["equity"] = 0
    cf["inputSnapshot"]["sourcesAndUses"]["revenue"]["leaseDeposit"] = 0
    rehash(cf)
    attach_cash_flow_basis(report, cf)
    return report


def test_cf_sections_keep_zero_missing_budget_and_revenue_separate_without_mutation():
    report = combined_report()
    original = deepcopy(report["cashFlowCalculationSnapshot"])
    attach_financing_basis(report)
    funding = report["sections"]["fundingStructure"]
    assert [r["금액"] for r in funding["조달 입력"]] == [0, None, 900]
    assert [r["금액"] for r in funding["사업 수입 입력"]] == [1000, 0, None, None, None]
    assert report["sections"]["useOfFunds"]["총사업비 예산"] == 600
    assert [r["금액"] for r in report["sections"]["useOfFunds"]["사용 예산 입력"]] == [100, 400, 50, 50]
    assert "PF 계산 미선택" in report["sections"]["financialTerms"]["상태"]
    assert report["cashFlowCalculationSnapshot"] == original
    verify_financing_basis(report)


def test_partial_selection_and_legacy_reports_do_not_inherit_live_project_terms():
    report = ReportAgent().generate_structured_report({"pf_structure": [{"tranche": "unrelated", "amount": 99999}]})
    attach_calculation_bases(report, {"feasibility": feasibility_record()})
    legacy = deepcopy(report)
    attach_financing_basis(report)
    assert all(ref is None for ref in report["financingBasis"]["sectionRunIds"].values())
    assert all("미확인" in report["sections"][key]["상태"] for key in ("financialTerms", "fundingStructure", "useOfFunds"))
    repository = ReportRepository({}, lambda: None)
    saved = repository.create("project-1", legacy, ReportExportAgent().export({"report": legacy}, "markdown")["content"])
    assert repository.get("project-1", saved["report_id"])["report"] == legacy
    assert "financingBasis" not in legacy


def test_pf_terms_use_executed_field_names_and_do_not_invent_missing_rates():
    report = {"sections": {}, "calculationSnapshots": {"pf": {
        "calculationRunId": "captured-pf", "engineKey": "pf_metrics", "inputSnapshot": {
            "tranches": [{"name": "explicit zero", "amount": 0, "rate": 0, "fee_rate": 0, "priority": 2},
                         {"tranche": "missing", "amount": 100, "interestRate": .8}],
            "other_priority_costs": 0}, "resultSnapshot": {}}}}
    attach_financing_basis(report)
    terms = report["sections"]["financialTerms"]["트랜치 조건"]
    assert terms[0] == {"명칭": "explicit zero", "금액": 0, "금리(소수)": 0, "수수료율(소수)": 0, "상환순위 입력": 2}
    assert terms[1]["금리(소수)"] is None and terms[1]["수수료율(소수)"] is None
    assert report["sections"]["financialTerms"]["기타 우선비용 입력"] == 0


@pytest.mark.parametrize("field,value", [("landCost", None), ("landCost", "100"), ("landCost", True), ("landCost", float("nan"))])
def test_invalid_or_missing_budget_is_never_rendered_as_zero(field, value):
    report = combined_report()
    report["cashFlowCalculationSnapshot"]["inputSnapshot"]["sourcesAndUses"]["cost"][field] = value
    # NaN is rejected by the existing snapshot seal before report attachment.
    if isinstance(value, float):
        with pytest.raises(ValueError): attach_financing_basis(report)
    else:
        attach_financing_basis(report)
        assert report["sections"]["useOfFunds"]["사용 예산 입력"][0]["금액"] is None


def test_saved_pf_terms_survive_working_changes_and_all_exports(selected, monkeypatch):
    client, pid, runs, selections = selected
    projects.project_repository().update(pid, {"pf_structure": [{"tranche": "changed live", "amount": 99999}]})
    response = client.post(f"/projects/{pid}/reports/generate", json={"calculationBases": selections})
    assert response.status_code == 200, response.text
    saved = response.json()
    report = saved["report"]
    financial = report["sections"]["financialTerms"]
    assert financial["계산 기록"] == runs["pf"]["calculationRunId"]
    assert financial["트랜치 조건"][0]["금액"] == 1200
    assert financial["트랜치 조건"][0]["금리(소수)"] == .058
    assert "미확인" in report["sections"]["fundingStructure"]["상태"]
    assert saved["metadata"]["financing_basis_hash"] == content_hash(report["financingBasis"])
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw: pytest.fail("Do not rebind saved runs"))
    endpoint = f"/projects/{pid}/reports/{saved['report_id']}"
    reopened = client.get(endpoint).json()
    assert reopened["report"] == report
    assert reopened["report_markdown"] == saved["report_markdown"]
    assert "약 6%" in reopened["report_display_markdown"]
    assert reopened["report_display_markdown"] == ReportExportAgent().export({"report": report}, "markdown")["content"]
    for fmt in ("json", "markdown", "html", "pdf", "docx"):
        export = client.post(endpoint + "/exports", json={"format": fmt})
        assert export.status_code == 200, export.text
        download = client.get(endpoint + f"/exports/{export.json()['export_id']}/download")
        assert download.status_code == 200
        if fmt == "json": assert download.json()["report"]["sections"]["financialTerms"] == financial
        if fmt in {"markdown", "html"}: assert "약 6%" in download.text and "changed live" not in download.text
        if fmt == "docx":
            with ZipFile(BytesIO(download.content)) as archive:
                text = "".join(ElementTree.fromstring(archive.read("word/document.xml")).itertext())
                assert "금리" in text and "약 6%" in text
                ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
                tree = ElementTree.fromstring(archive.read("word/document.xml"))
                table = next(t for t in tree.findall('.//w:tbl', ns) if "수수료율" in "".join(t.itertext()))
                rows = table.findall('w:tr', ns)
                assert ["".join(c.itertext()) for c in rows[1].findall('w:tc', ns)] == ["Senior", "1,200", "약 6%", "1%", "1"]
                assert all(row.find('w:trPr/w:cantSplit', ns) is not None for row in rows)


@pytest.mark.parametrize("mutation", ["amount", "basis", "removed", "seal"])
def test_forged_financing_section_or_binding_is_rejected_on_get_and_export(selected_run, mutation):
    client, pid, run, selection = selected_run
    saved = client.post(f"/projects/{pid}/reports/generate", json={"cashFlowBasis": selection}).json()
    record = projects.report_repository().get(pid, saved["report_id"])
    if mutation == "amount": record["report"]["sections"]["useOfFunds"]["총사업비 예산"] = 123
    if mutation == "basis": record["report"]["financingBasis"]["sectionRunIds"]["fundingStructure"] = "foreign"
    if mutation == "removed": del record["report"]["financingBasis"]
    if mutation == "seal": record["metadata"]["financing_basis_hash"] = "f" * 64
    endpoint = f"/projects/{pid}/reports/{saved['report_id']}"
    assert client.get(endpoint).status_code == 409
    assert client.post(endpoint + "/exports", json={"format": "docx"}).status_code == 409


def test_postgres_repository_roundtrip_retains_financing_sections_and_seals():
    report = combined_report()
    attach_financing_basis(report)
    repository, _, _ = _postgres_repository()
    # The disposable repository double requires this scope.
    report["cashFlowCalculationSnapshot"]["projectId"] = "project-1"
    report["cashFlowCalculationSnapshot"]["tenantId"] = "tenant-a"
    report["cashFlowCalculationSnapshotHash"] = content_hash(report["cashFlowCalculationSnapshot"])
    saved = repository.create("project-1", report, ReportExportAgent().export({"report": report}, "markdown")["content"])
    reopened = repository.get("project-1", saved["report_id"])
    assert reopened["report"]["sections"]["fundingStructure"] == report["sections"]["fundingStructure"]
    verify_report_snapshots(reopened, project_id="project-1", tenant_id="tenant-a")
