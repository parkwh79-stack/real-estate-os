import hashlib
import json
from pathlib import Path

import pytest

from agents.feasibility_workbook_importer import import_feasibility_study_from_workbook
from agents.feasibility_workbook_validator import validate_feasibility_workbook
from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study


SAMPLE_DIR = Path(__file__).resolve().parents[4] / "sample"
MANIFEST_PATH = Path(__file__).resolve().parent / "fixtures" / "bonri" / "source_manifest.json"


def test_bonri_source_manifest_contract_is_machine_readable_without_sample_file():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["schemaVersion"] == "realytics-bonri-source-manifest-v1"
    assert len(manifest["sha256"]) == 64
    assert int(manifest["sha256"], 16) >= 0
    assert manifest["workbookInventory"]["sheetCount"] == 12
    assert manifest["workbookInventory"]["formulaCount"] == 12_050
    assert manifest["workbookInventory"]["arrayFormulaCount"] == 68
    assert manifest["cashFlowContract"]["transactionRange"] == "F35:BQ119"
    assert manifest["cashFlowContract"]["periodCount"] == 64
    assert manifest["goldenImport"]["periodCount"] == 64
    assert manifest["goldenCostMapping"]["staticCostUsesKrw"]["totalProjectCost"] > 0


@pytest.mark.skipif(not list(SAMPLE_DIR.glob("*.xlsx")), reason="workspace sample workbook is not available in CI")
def test_real_bonri_sample_import_uses_formula_profile_and_cell_provenance(tmp_path, monkeypatch):
    workbook_path = next(SAMPLE_DIR.glob("*.xlsx"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert hashlib.sha256(workbook_path.read_bytes()).hexdigest() == manifest["sha256"]
    validation = validate_feasibility_workbook(workbook_path)
    assert validation["profile"] == "legacy-bonri-mixed-use-pf-feasibility"
    assert validation["status"] == "failed"
    assert validation["formulaIntegrity"]["hasBlockingFormulaErrors"] is True
    inventory = manifest["workbookInventory"]
    assert validation["sheetCount"] == inventory["sheetCount"]
    assert validation["formulaIntegrity"]["formulaCount"] == inventory["formulaCount"]
    assert validation["formulaIntegrity"]["arrayFormulaCount"] == inventory["arrayFormulaCount"]
    assert validation["calculationEvidence"]["recalculated"] is False
    assert validation["formulaIntegrity"]["formulaRefCount"] == inventory["formulaRefCount"]
    assert validation["formulaIntegrity"]["cachedErrorCounts"]["#REF!"] == inventory["cachedErrorCounts"]["#REF!"]
    assert validation["formulaIntegrity"]["cachedErrorCounts"]["#DIV/0!"] == inventory["cachedErrorCounts"]["#DIV/0!"]

    study = import_feasibility_study_from_workbook(workbook_path, project_id="sample-bonri")
    tranches = {row["tranche_id"]: row for row in study["financing"]}
    assert tranches["tr_a"]["interest_rate"] == 0.055
    assert tranches["tr_a"]["fee_rate"] == 0.02
    assert tranches["tr_b"]["amount"] == 0
    assert tranches["tr_a"]["term_months"] is None
    terms = study["raw_import"]["financingTermReview"]
    assert terms["approvalStatus"] == "not_requested"
    assert terms["terms"][0]["fields"]["interest_rate"]["sourceCell"] == "입력!D131"
    from storage import get_project_record, upsert_project_record
    from use_cases.feasibility_workbook_llm import feasibility_review_state

    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "sample-review.sqlite3"))
    project = {"project": {"id": "sample-bonri"}, "feasibilityStudyV2Draft": study,
               "pf_structure": [{"tranche_id": "working", "amount": 1, "interest_rate": .04}]}
    assert upsert_project_record("sample-bonri", project, 1)
    reopened = feasibility_review_state(get_project_record("sample-bonri"))
    assert reopened["workbookFinancingReview"]["terms"][0]["fields"]["interest_rate"]["value"] == .055
    assert reopened["terms"]["financing"][0]["interest_rate"] == .04
    assert study["metrics"]["total_revenue"] > 0
    assert study["metrics"]["total_cost"] > 0
    assert study["raw_import"]["sampleProfile"]["fieldLocators"]["revenue.total"]["cell"] == "D11"
    contract = manifest["cashFlowContract"]
    golden = manifest["goldenImport"]
    assert study["raw_import"]["sampleProfile"]["fieldLocators"]["cashflow.monthly_cash_flow"]["range"] == contract["transactionRange"]
    rows = study["monthly_cash_flow"]
    assert len(rows) == golden["periodCount"]
    assert rows[0]["period"] == golden["firstPeriod"]
    assert rows[-1]["period"] == golden["lastPeriod"]
    assert rows[-1]["source_locator"]["cell"].startswith(contract["lastPeriodColumn"])
    assert rows[0]["source_locator"]["sheetName"] == contract["sheetName"]
    assert rows[0]["source_locator"]["normalizedUnit"] == "KRW"
    current_differences = [row["source_locator"]["currentCashCheck"]["differenceKrw"] for row in rows]
    cumulative_checks = [row["source_locator"]["legacyCumulativeCashFlowCheck"] for row in rows]
    assert max(abs(value) for value in current_differences) <= contract["currentCashToleranceKrw"]
    assert max(abs(check["differenceKrw"]) for check in cumulative_checks if check["differenceKrw"] is not None) <= contract["cumulativeCashToleranceKrw"]
    resets = [
        {"monthIndex": row["month_index"], "period": row["period"]}
        for row in rows
        if row["source_locator"]["legacyCumulativeCashFlowCheck"]["carryResetDetected"]
    ]
    assert resets == [contract["terminalCarryReset"]]
    for field, expected_key in (
        ("cash_in", "operatingCashInKrw"),
        ("cash_out", "projectCashOutKrw"),
        ("pf_draw", "pfDrawKrw"),
        ("loan_repayment", "loanRepaymentKrw"),
        ("equity_contribution", "equityContributionKrw"),
        ("equity_recovery", "equityRecoveryKrw"),
    ):
        assert sum(sum(row[field].values()) for row in rows) == golden[expected_key]
    assert sum(row["net_cash_flow"] for row in rows) == golden["sourceNetCashFlowKrw"]
    assert study["metrics"]["max_minus"] == pytest.approx(golden["maxMinusKrw"])

    request = build_cash_flow_run_request_from_feasibility_study(study)
    cost_mapping = manifest["goldenCostMapping"]
    assert request["sourcesAndUses"]["cost"] == cost_mapping["staticCostUsesKrw"]
    monthly_costs = {
        key: sum(float(row.get(key) or 0) for row in request["costSchedule"]["rows"])
        for key in ("landCost", "constructionCost", "financialCost", "otherCost")
    }
    for key, expected in cost_mapping["monthlyCostScheduleKrw"].items():
        assert monthly_costs[key] == pytest.approx(expected, abs=cost_mapping["categoryToleranceKrw"])

    # The source-derived budget calculation (not verified loan terms) can be
    # explicitly frozen into a PF draft and reopened without the live workbook.
    from agents.cash_flow_engine import run_cash_flow_engine
    from db.repositories.report_repository import ReportRepository
    from storage import upsert_calculation_run_record, upsert_report_record, load_state
    from use_cases.cash_flow_comparison import content_hash
    from use_cases.report_calculation_basis import freeze_cash_flow_basis
    from use_cases.report_generation import generate_and_save_pf_report_use_case, export_report_use_case

    result = run_cash_flow_engine(request)
    run = upsert_calculation_run_record("sample-bonri", {"calculationRunId": "sample-report-cf",
        "engineKey": "cash_flow_engine", "status": "completed",
        "inputSnapshot": request, "resultSnapshot": result})
    selected = freeze_cash_flow_basis(run, project_id="sample-bonri", tenant_id="legacy",
        selection={"calculationRunId": run["calculationRunId"], "inputHash": content_hash(request), "resultHash": content_hash(result)})
    repository = ReportRepository({}, lambda: pytest.fail("Use incremental report persistence"), upsert_report_record)
    from core.calculations import calculate_feasibility_metrics
    from use_cases.report_calculation_basis import freeze_calculation_basis
    feasibility_input = {**study["metrics"], "sourceMetadata": {
        "source": "본리동 원본 사업수지", "sourceHash": manifest["sha256"],
        "fieldLocators": study["raw_import"]["sampleProfile"]["fieldLocators"],
        "limitation": "원본 수식 오류·대출 약정은 미확인"}}
    feasibility_result = calculate_feasibility_metrics(feasibility_input)
    feasibility_run = upsert_calculation_run_record("sample-bonri", {
        "calculationRunId": "sample-report-feasibility", "engineKey": "feasibility", "status": "completed",
        "inputDigest": {"capturePolicy": "effective_request_v1"},
        "inputSnapshot": feasibility_input, "resultSnapshot": feasibility_result})
    selected_feasibility = freeze_calculation_basis(feasibility_run, slot="feasibility", project_id="sample-bonri", tenant_id="legacy",
        selection={"calculationRunId": feasibility_run["calculationRunId"],
                   "inputHash": content_hash(feasibility_input), "resultHash": content_hash(feasibility_result)})
    generated = generate_and_save_pf_report_use_case(repository, "sample-bonri", project, cash_flow_run=selected,
        calculation_runs={"feasibility": selected_feasibility})
    reopened = ReportRepository(load_state(project_workspace_only=True)["reports"], lambda: None).get(
        "sample-bonri", generated["reportRecord"]["report_id"])
    cf_section = reopened["report"]["sections"]["summaryCashFlow"]
    assert cf_section["rows"] == result["monthlyCashFlow"]
    assert cf_section["summary"] == result["summary"]
    exported = json.loads(export_report_use_case(reopened, {"format": "json"})["content"])
    assert exported["report"]["cashFlowCalculationSnapshot"] == run
    assert exported["report"]["calculationSnapshots"]["feasibility"] == feasibility_run
    assert reopened["report"]["sections"]["fundingStructure"]["조달 입력"] == [
        {"항목": label, "금액": request["sourcesAndUses"]["funding"][key]}
        for key, label in (("equity", "자기자본"), ("bridgeLoan", "브리지 대출"), ("pfLoan", "PF 대출"))]
    assert reopened["report"]["sections"]["useOfFunds"]["총사업비 예산"] == request["sourcesAndUses"]["cost"]["totalProjectCost"]
    assert "PF 계산 미선택" in reopened["report"]["sections"]["financialTerms"]["상태"]
    assert exported["report"]["financingBasis"] == reopened["report"]["financingBasis"]
    assert reopened["report"]["sections"]["projectSchedule"]["저장된 일정 입력"] == request["schedule"]
    assert reopened["report"]["sections"]["salesPaymentSchedule"]["저장된 수금 결과"] == result["salesCollectionSchedule"]
    assert exported["report"]["timingBasis"] == reopened["report"]["timingBasis"]
    assert reopened["metadata"]["timing_basis_hash"] == content_hash(reopened["report"]["timingBasis"])
    reconciliation = reopened["report"]["calculationReconciliation"]
    assert exported["report"]["calculationReconciliation"] == reconciliation
    assert reopened["metadata"]["calculation_reconciliation_hash"] == content_hash(reconciliation)
    # The summary metrics capture totals, not the detailed cost fields. A
    # matching total must not turn those missing captured inputs into a pass.
    assert reconciliation["status"] == "incomplete"
    assert [row["status"] for row in reconciliation["rows"]] == ["same", "same", "missing", "missing", "missing", "missing"]
    assert reconciliation["runInputHashes"] == {"cash_flow": content_hash(request), "feasibility": content_hash(feasibility_input)}
    assert reopened["report"]["sections"]["businessFeasibility"]["사업성 요약"]["저장된 결과"] == feasibility_result
    assert "미확인" in reopened["report"]["sections"]["businessFeasibility"]["PF 지표"]["상태"]
    assert hashlib.sha256(workbook_path.read_bytes()).hexdigest() == manifest["sha256"]
