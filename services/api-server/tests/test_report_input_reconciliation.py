from base64 import b64decode
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import pytest

from agents.report_export_agent import ReportExportAgent
from routers import projects
from use_cases.report_input_reconciliation import build_input_reconciliation, verify_input_reconciliation
from use_cases.cash_flow_comparison import content_hash
from test_pf_report_calculation_bases import selected, project


def run(engine, source, rid="one"):
    return {"calculationRunId": rid, "engineKey": engine, "inputSnapshot": source}


def budget_report(**overrides):
    source = {"total_revenue": 1000, "total_cost": 600, "land_cost": 100,
              "construction_cost": 400, "finance_cost": 50, "other_cost": 50}
    return {"calculationSnapshots": {"feasibility": run("feasibility", source),
        "returns": run("return_metrics", {**source, **overrides}, "two")}}


def test_known_matching_inputs_never_claim_full_approval_and_do_not_mutate():
    report = budget_report()
    before = deepcopy(report)
    result = build_input_reconciliation(report)
    assert result["status"] == "no_difference_in_checked_inputs"
    assert result["comparableCount"] == 6 and result["differenceCount"] == 0
    assert "전체 정합성 승인은 아닙니다" in result["summary"]
    assert report == before


def test_difference_remains_visible_alongside_missing_input_and_explicit_zero():
    report = budget_report(total_revenue=1000.001, finance_cost=0)
    report["calculationSnapshots"]["pf"] = run("pf_metrics", {"total_revenue": None}, "three")
    result = build_input_reconciliation(report)
    rows = {row["key"]: row for row in result["rows"]}
    assert rows["total_revenue"]["status"] == "different"
    assert rows["total_revenue"]["missingCount"] == 1
    assert [c["value"] for c in rows["finance_cost"]["values"]] == [50, None, 0]
    assert rows["finance_cost"]["status"] == "different"
    assert result["unresolvedCount"] == 6


@pytest.mark.parametrize("unit,currency", [("천원", "KRW"), ("KRW", "USD"), (None, "KRW"), ({}, "KRW")])
def test_unknown_or_different_units_are_not_compared_or_converted(unit, currency):
    result = build_input_reconciliation(budget_report(unit=unit, currency=currency))
    assert all(row["status"] == "unit_mismatch" for row in result["rows"])
    assert result["comparableCount"] == 0


def test_missing_is_not_equal_to_zero_and_one_run_is_not_a_comparison():
    result = build_input_reconciliation(budget_report(other_cost=None))
    assert result["rows"][-1]["status"] == "missing"
    report = budget_report()
    del report["calculationSnapshots"]["returns"]
    result = build_input_reconciliation(report)
    assert result["status"] == "incomplete" and result["comparableCount"] == 0


def test_cf_and_chief_inputs_use_captured_values_without_result_fallback():
    report = {"cashFlowCalculationSnapshot": run("cash_flow_engine", {
        "sourcesAndUses": {"revenue": {"totalSalesRevenue": 1000}, "cost": {"totalProjectCost": 600}}}),
        "calculationSnapshots": {"feasibility": run("development_feasibility", {
            "feasibility_input": {"total_revenue": 1000, "total_cost": 700}})}}
    result = build_input_reconciliation(report)
    assert result["rows"][0]["status"] == "same"
    assert result["rows"][1]["status"] == "different"
    assert result["rows"][2]["status"] == "missing"
    assert result["rows"][0]["values"][0]["sourcePath"] == "sourcesAndUses.revenue.totalSalesRevenue"


def test_equal_scenario_totals_do_not_hide_changed_financing_and_metadata_is_ignored():
    source = {"scenarioSetId": "set-a", "productMix": [{"totalRevenue": 1000}],
              "pfStructure": [{"amount": 600, "interestRate": .05}], "waterfallRule": {"includeFees": True}}
    other = {**deepcopy(source), "scenarioSetId": "set-b", "sourceMetadata": {"createdAt": "later"}}
    report = {"calculationSnapshots": {"waterfall": run("waterfall", source),
        "tranche_recovery": run("tranche_recovery", other, "two")}}
    result = build_input_reconciliation(report)
    assert result["sameFamilyChecks"][0]["status"] == "same"
    other["pfStructure"][0]["interestRate"] = .06
    result = build_input_reconciliation(report)
    assert result["status"] == "differences_found"
    assert result["sameFamilyChecks"][0]["status"] == "different"
    assert result["sameFamilyChecks"][0]["leftInputHash"] != result["sameFamilyChecks"][0]["rightInputHash"]


def test_saved_reconciliation_and_all_exports_follow_frozen_inputs(selected, monkeypatch):
    client, pid, _, selections = selected
    response = client.post(f"/projects/{pid}/pf/structure", json={"total_revenue": 3000})
    assert response.status_code == 200
    rid = response.json()["calculationRunId"]
    choices = client.get(f"/projects/{pid}/reports/calculation-bases").json()["records"]
    chosen = next(row for row in choices if row["calculationRunId"] == rid)
    selections["pf"] = {key: chosen[key] for key in ("calculationRunId", "inputHash", "resultHash")}
    response = client.post(f"/projects/{pid}/reports/generate", json={"calculationBases": selections})
    assert response.status_code == 200, response.text
    saved = response.json()
    reconciliation = saved["report"]["calculationReconciliation"]
    assert reconciliation["status"] == "differences_found"
    assert saved["metadata"]["report_eligible"] is False
    assert saved["metadata"]["calculation_reconciliation_hash"] == content_hash(reconciliation)
    projects.project_repository().update(pid, {"feasibility_input": {"total_revenue": 999999}})
    monkeypatch.setattr(projects, "list_calculation_run_records", lambda *a, **kw: pytest.fail("Do not compare live calculations"))
    assert client.get(f"/projects/{pid}/reports/{saved['report_id']}").json()["report"]["calculationReconciliation"] == reconciliation
    record = projects.report_repository().get(pid, saved["report_id"])
    for fmt in ("json", "markdown", "html", "pdf", "docx"):
        exported = client.post(f"/projects/{pid}/reports/{saved['report_id']}/exports", json={"format": fmt})
        assert exported.status_code == 200, exported.text
        if fmt == "json":
            download = client.get(f"/projects/{pid}/reports/{saved['report_id']}/exports/{exported.json()['export_id']}/download")
            assert download.json()["report"]["calculationReconciliation"] == reconciliation
    forged = deepcopy(saved["report"])
    forged["calculationReconciliation"]["differenceCount"] = 0
    with pytest.raises(ValueError): verify_input_reconciliation(forged)
    del record["report"]["calculationReconciliation"]
    assert client.get(f"/projects/{pid}/reports/{saved['report_id']}").status_code == 409


def test_docx_uses_real_headings_and_keeps_heading_with_following_content():
    report = {"title": "입력 대조 검토", "sectionOrder": [{"index": 1, "key": "review", "title": "검토 결과"}],
              "sections": {"review": {"가정": "분석용 가정이며 최종 승인이 아닙니다."}}}
    document = b64decode(ReportExportAgent().export({"report": report}, "docx")["content"])
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(BytesIO(document)) as archive:
        tree = ET.fromstring(archive.read("word/document.xml"))
        headings = [
            p for p in tree.findall('.//w:p', ns)
            if (style := p.find('w:pPr/w:pStyle', ns)) is not None
            and style.get(f"{{{ns['w']}}}val") == "Heading1"]
        assert ["".join(p.itertext()) for p in headings] == ["목차", "1. 검토 결과"]
        style = ET.fromstring(archive.read("word/styles.xml")).find('w:style[@w:styleId="Heading1"]', ns)
        assert style.find("w:pPr/w:keepNext", ns) is not None
        assert style.find("w:pPr/w:outlineLvl", ns).get(f"{{{ns['w']}}}val") == "0"


def test_docx_comparison_rows_keep_value_label_and_status_together():
    report = budget_report(other_cost=None)
    report["calculationReconciliation"] = build_input_reconciliation(report)
    document = b64decode(ReportExportAgent().export({"report": report}, "docx")["content"])
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    with ZipFile(BytesIO(document)) as archive:
        tree = ET.fromstring(archive.read("word/document.xml"))
        rows = tree.findall('.//w:tbl/w:tr', ns)
        assert len(rows) == 13
        assert rows[0].find('w:trPr/w:tblHeader', ns) is not None
        assert all(row.find('w:trPr/w:cantSplit', ns) is not None for row in rows)
        cells = rows[-1].findall('w:tc', ns)
        assert ["".join(cell.itertext()) for cell in cells] == ["기타 비용 입력", "투자 수익률", "미기록 KRW / KRW", "입력 누락"]
