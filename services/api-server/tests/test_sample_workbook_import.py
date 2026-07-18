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
    assert manifest["workbookInventory"]["formulaCount"] == 11_982
    assert manifest["cashFlowContract"]["transactionRange"] == "F35:BQ119"
    assert manifest["cashFlowContract"]["periodCount"] == 64
    assert manifest["goldenImport"]["periodCount"] == 64
    assert manifest["goldenCostMapping"]["staticCostUsesKrw"]["totalProjectCost"] > 0


@pytest.mark.skipif(not list(SAMPLE_DIR.glob("*.xlsx")), reason="workspace sample workbook is not available in CI")
def test_real_bonri_sample_import_uses_formula_profile_and_cell_provenance():
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
    assert validation["formulaIntegrity"]["formulaRefCount"] == inventory["formulaRefCount"]
    assert validation["formulaIntegrity"]["cachedErrorCounts"]["#REF!"] == inventory["cachedErrorCounts"]["#REF!"]
    assert validation["formulaIntegrity"]["cachedErrorCounts"]["#DIV/0!"] == inventory["cachedErrorCounts"]["#DIV/0!"]

    study = import_feasibility_study_from_workbook(workbook_path, project_id="sample-bonri")
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
