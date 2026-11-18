"""Compare the same budget through source and working input paths."""

from copy import deepcopy

import pytest

from test_sangbong_financial_review import Repository, source
from use_cases.workbook_import_approval import persist_feasibility_workbook_candidate_use_case
from use_cases.workbook_input_reconciliation import reconcile_workbook_inputs


@pytest.fixture
def project():
    amounts = {
        "total_revenue": 320_000_000,
        "total_cost": 250_000_000,
        "land_cost": 100_000_000,
        "construction_cost": 125_000_000,
        "finance_cost": 0,
        "other_cost": 25_000_000,
        "development_profit": 70_000_000,
        "pf_amount": 140_000_000,
    }
    archived = source()
    fields = {}
    for row, (key, amount) in enumerate(amounts.items(), 10):
        multiplier, unit = (1_000_000, "KRW_MILLION") if key == "pf_amount" else (1000, "KRW_THOUSAND")
        fields[key] = {
            "value": amount, "rawValue": amount / multiplier, "sourceCell": f"'합성 사업수지'!K{row}",
            "sourceUnit": unit, "normalizedUnit": "KRW", "calculationState": "saved_formula_cache",
            "applyEligible": True,
        }
    draft = {
        "project_id": "p1", "project_name": "검산용 일반분양",
        "raw_import": {
            "profile": "sangbong-sale-feasibility", "sourceDocument": archived,
            "financialMapping": {"schemaVersion": "sangbong-financial-mapping-v1", "sourceFields": fields},
            "cashFlowPeriodBasis": {"kind": "calendar_monthly", "periods": [
                {"label": "2027-01"}, {"label": "2027-02"}, {"label": "2027-03"}, {"label": "2027-04"},
            ]},
        },
    }
    repo = Repository({
        "id": "p1", "project": {"id": "p1", "type": "apartment"},
        "feasibility_input": {key: value for key, value in amounts.items()
                              if key not in {"development_profit", "pf_amount"}},
        "expected_results": {"development_profit": amounts["development_profit"], "total_pf_amount": amounts["pf_amount"]},
        "schedule": {"construction_start": "2027-03-01", "completion": "2027-05-01"},
        "_meta": {"feasibilityFieldReviews": {"total_revenue": {
            "value": amounts["total_revenue"], "origin": "user_assumption", "reason": "독립 수기 입력 대조",
        }}},
    })
    persist_feasibility_workbook_candidate_use_case(
        repo, "p1", {"feasibilityStudyV2Draft": draft, "sourceDocument": archived}, recorded_by="reviewer"
    )
    registered = repo.project["feasibilityStudyV2Draft"]
    repo.project["source_documents"] = [{**archived, "documentType": "feasibility_workbook",
                                         "feasibilityStudyV2DraftHash": registered["raw_import"]["candidateImport"]["draftHash"]}]
    return repo.project


def test_same_source_and_manual_budget_match_without_aligning_different_period_axes(project):
    original = deepcopy(project)
    result = reconcile_workbook_inputs(project)
    assert result["status"] == "matched"
    assert result["calculationComparison"]["status"] == "matched"
    assert result["periodComparison"]["status"] == "different_axis"
    assert result["periodComparison"]["workbook"]["periodCount"] == 4
    assert result["periodComparison"]["working"]["periodCount"] == 3
    assert result["reportEligible"] is False
    assert result["workbookCalculationEvidence"] == "saved_values"
    rows = {item["key"]: item for item in result["rows"]}
    assert rows["finance_cost"]["workbookValue"] == rows["finance_cost"]["workingValue"] == 0
    assert rows["total_revenue"]["workingOrigin"] == "user_assumption"
    assert rows["development_profit"]["role"] == "reference_output"
    assert rows["pf_amount"]["sourceUnit"] == "KRW_MILLION"
    assert all(metric["differenceWon"] == 0 for metric in result["calculationComparison"]["metrics"])
    assert project == original


def test_cost_change_recalculates_profit_under_the_same_budget_policy(project):
    project["feasibility_input"]["construction_cost"] += 15_000_000
    project["feasibility_input"]["total_cost"] += 15_000_000
    result = reconcile_workbook_inputs(project)
    assert result["status"] == "differences"
    assert result["calculationComparison"]["status"] == "differences"
    metrics = {metric["key"]: metric for metric in result["calculationComparison"]["metrics"]}
    assert metrics["totalProjectCost"]["differenceWon"] == 15_000_000
    assert metrics["developmentProfit"]["differenceWon"] == -15_000_000


def test_missing_and_zero_have_different_reconciliation_states(project):
    del project["feasibility_input"]["finance_cost"]
    result = reconcile_workbook_inputs(project)
    finance = next(item for item in result["rows"] if item["key"] == "finance_cost")
    assert finance["workbookValue"] == 0
    assert finance["workingValue"] is None
    assert finance["differenceWon"] is None
    assert finance["status"] == "missing_working_input"
    assert result["calculationComparison"]["status"] == "unavailable"


def test_missing_reported_profit_does_not_block_calculated_profit(project):
    del project["expected_results"]["development_profit"]
    result = reconcile_workbook_inputs(project)
    assert result["status"] == "incomplete"
    assert result["calculationComparison"]["status"] == "matched"
    profit = next(row for row in result["calculationComparison"]["metrics"] if row["key"] == "developmentProfit")
    assert profit["workingValue"] == 70_000_000


@pytest.mark.parametrize("changed", ["draft", "archive_version", "archive_removal"])
def test_registration_binding_rejects_a_changed_or_missing_source(project, changed):
    if changed == "draft":
        fields = project["feasibilityStudyV2Draft"]["raw_import"]["financialMapping"]["sourceFields"]
        fields["total_revenue"]["value"] = 1
    elif changed == "archive_version":
        project["source_documents"][0]["objectVersion"] = "different-version"
    else:
        del project["source_documents"]
    result = reconcile_workbook_inputs(project)
    assert result["status"] == "source_unavailable"
    assert result["sourceDocument"] is None and result["rows"] == []


def test_detailed_loans_are_not_computed_as_aggregate_budget_inputs(project):
    project["pf_structure"] = [{"amount": 80_000_000, "interest_rate": 0.06}]
    result = reconcile_workbook_inputs(project)
    assert result["status"] == "matched"
    assert result["calculationComparison"]["status"] == "unavailable"
