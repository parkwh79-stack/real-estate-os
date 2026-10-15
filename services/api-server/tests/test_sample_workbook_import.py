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
