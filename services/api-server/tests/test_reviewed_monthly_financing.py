from copy import deepcopy

import pytest

from agents.cash_flow_engine import run_cash_flow_engine
from core.financial_input_validation import FinancialInputError
from core.reviewed_monthly_financing import MONTHLY_FINANCING_POLICY


def _request():
    return {
        "project": {"projectId": "monthly-finance", "projectName": "검토 계산"},
        "sourcesAndUses": {
            "revenue": {"totalSalesRevenue": 300},
            "cost": {"landCost": 100, "directConstructionCost": 0, "financialCost": 9,
                     "otherCost": 0, "totalProjectCost": 109},
            "funding": {"pfLoan": 200},
        },
        "schedule": {"construction_start": "M0", "completion": "M2"},
        "productMix": [{"totalSalesRevenue": 300, "collectionSteps": [{
            "stage": "balance", "monthlyCollectionAmounts": [0, 0, 300],
        }]}],
        "costSchedule": {"rows": [{"monthIndex": 0, "landCost": 100}, {"monthIndex": 1, "financialCost": 9}]},
        "pfStructure": [{"trancheId": "senior", "tranche": "선순위", "name": "senior", "amount": 200,
                         "interestRate": 0.12, "feeRate": 0.01, "maturityMonths": 2,
                         "repaymentPriority": 1, "repaymentSource": "repayment_reserve"}],
        "monthlyFinancing": dict(MONTHLY_FINANCING_POLICY),
    }


def _assert_balances(result):
    for row in result["monthlyCashFlow"]:
        accounts = row["accounts"]
        assert row["beginningCash"] + row["netCashFlow"] == pytest.approx(row["endingCash"])
        assert accounts["beginningOperatingCash"] + accounts["operatingAccountInflow"] - accounts["operatingAccountOutflow"] == pytest.approx(accounts["endingOperatingCash"])
        assert accounts["beginningRepaymentReserve"] + accounts["repaymentReserveAccountInflow"] - accounts["repaymentReserveAccountOutflow"] == pytest.approx(accounts["endingRepaymentReserve"])
        assert sum(row["cashOut"][key] for key in ("landCost", "constructionCost", "financialCost", "otherCost")) == pytest.approx(row["cashOut"]["totalCashOut"])
        for debt in row["monthlyFinancing"]["debtBalances"]:
            assert debt["openingBalance"] + debt["draw"] - debt["principalRepayment"] == pytest.approx(debt["closingBalance"])
    assert result["monthlyFinancing"]["debtReconciliationDifference"] == pytest.approx(0, abs=1e-9)
    assert result["monthlyFinancing"]["costReconciliationDifference"] == pytest.approx(0, abs=1e-9)


def test_explicit_policy_replaces_budget_fee_once_interest_opening_balance_and_principal_not_cost():
    request = _request()
    original = deepcopy(request)
    result = run_cash_flow_engine(request)
    rows = result["monthlyCashFlow"]
    assert request == original
    assert rows[0]["cashIn"]["totalPfDraw"] == 102
    assert rows[0]["monthlyFinancing"]["interest"] == 0
    assert rows[0]["monthlyFinancing"]["fees"] == 2
    assert rows[1]["monthlyFinancing"]["interest"] == pytest.approx(1.02)
    assert rows[1]["monthlyFinancing"]["fees"] == 0
    assert rows[1]["cashIn"]["totalPfDraw"] == pytest.approx(1.02)
    assert rows[2]["monthlyFinancing"]["interest"] == pytest.approx(1.0302)
    assert rows[2]["monthlyFinancing"]["principalRepayment"] == pytest.approx(103.02)
    assert rows[2]["cashIn"]["totalPfDraw"] == 0  # no draws on/after M0-based maturity
    summary = result["monthlyFinancing"]
    assert summary["budgetFinancialCost"] == 9
    assert summary["calculatedInterest"] == pytest.approx(2.0502)
    assert summary["calculatedFees"] == 2
    assert summary["calculatedFinancialCost"] == pytest.approx(4.0502)
    assert result["summary"]["totalProjectCost"] == pytest.approx(104.0502)
    assert result["summary"]["outstandingDebt"] == 0
    assert result["summary"]["unfundedOperatingCost"] == pytest.approx(1.0302)
    assert result["analysisOnly"] is True
    assert result["reportEligible"] is False
    assert result["scheduleProvenance"]["reportEligible"] is False
    _assert_balances(result)


def test_no_policy_keeps_budget_finance_cost_without_interest_or_repayment():
    request = _request()
    del request["monthlyFinancing"]
    request["schedule"] = {"construction_start": "2026-01", "completion": "2026-03"}
    result = run_cash_flow_engine(request)
    assert "monthlyFinancing" not in result
    assert result["summary"]["totalProjectCost"] == 109
    assert sum(row["cashOut"]["financialCost"] for row in result["monthlyCashFlow"]) == 9
    assert all(row["accounts"]["repaymentReserveAccountOutflow"] == 0 for row in result["monthlyCashFlow"])
    assert "outstandingDebt" not in result["summary"]


def test_explicit_zero_rates_are_calculated_and_missing_rates_are_never_defaulted():
    request = _request()
    request["pfStructure"][0].update(interestRate=0, feeRate=0)
    result = run_cash_flow_engine(request)
    assert result["monthlyFinancing"]["calculatedFinancialCost"] == 0
    assert result["summary"]["totalPfDraw"] == 100
    assert result["summary"]["totalPrincipalRepaid"] == 100
    assert result["summary"]["unfundedOperatingCost"] == 0
    assert result["summary"]["developmentProfit"] == 200
    _assert_balances(result)


@pytest.mark.parametrize("field,value", [
    ("amount", None), ("amount", True), ("amount", -1), ("amount", 1.5), ("amount", "200"),
    ("interestRate", None), ("interestRate", True), ("interestRate", -0.01), ("interestRate", 1.01),
    ("interestRate", float("nan")), ("interestRate", float("inf")),
    ("feeRate", None), ("feeRate", -0.01), ("feeRate", 1.01),
    ("maturityMonths", None), ("maturityMonths", True), ("maturityMonths", 0),
    ("maturityMonths", 1.5), ("maturityMonths", 601),
    ("repaymentSource", None), ("repaymentSource", "refinancing"),
    ("priority", "equity"), ("repaymentSchedule", []),
])
def test_invalid_or_unsupported_terms_fail_without_mutating_input(field, value):
    request = _request()
    request["pfStructure"][0][field] = value
    before = deepcopy(request)
    with pytest.raises(FinancialInputError):
        run_cash_flow_engine(request)
    if isinstance(value, float) and value != value:
        assert request["pfStructure"][0][field] is value
    else:
        assert request == before


@pytest.mark.parametrize("option", [
    "drawSchedule", "draw_schedule", "repaymentSchedule", "repayment_schedule", "amortization",
    "interestCapitalization", "interest_capitalization", "penaltyRate", "penalty_rate",
    "variableRate", "variable_rate", "interestRateSchedule", "interest_rate_schedule",
    "rateResetSchedule", "rate_reset_schedule",
])
def test_known_advanced_loan_options_and_aliases_are_rejected_in_direct_monthly_mode(option):
    request = _request()
    request["pfStructure"][0][option] = {"specified": True}
    original = deepcopy(request)

    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)

    assert caught.value.code == "unsupported_monthly_financing_option"
    assert request == original


def test_benign_source_metadata_and_explanatory_fields_do_not_activate_financial_options():
    request = _request()
    request["pfStructure"][0].update(
        notes="원본 조건표의 설명", sourceMetadata={"quotedText": "drawSchedule", "drawSchedule": "인용된 원본 제목"},
    )
    original = deepcopy(request)

    result = run_cash_flow_engine(request)

    assert result["status"] == "completed"
    assert request == original


def test_advanced_options_do_not_change_existing_budget_mode():
    baseline = _request()
    del baseline["monthlyFinancing"]
    changed = deepcopy(baseline)
    changed["pfStructure"][0].update(draw_schedule=[{"monthIndex": 1}], variable_rate=True)

    assert run_cash_flow_engine(changed) == run_cash_flow_engine(baseline)


@pytest.mark.parametrize("policy", [False, {}, {"method": "reviewed_monthly_v1"},
                                      {**MONTHLY_FINANCING_POLICY, "interestBasis": "closing_balance"},
                                      {**MONTHLY_FINANCING_POLICY, "extra": "ignored?"}])
def test_policy_must_explicitly_select_every_supported_assumption(policy):
    request = _request()
    request["monthlyFinancing"] = policy
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)
    assert caught.value.code == "unsupported_monthly_financing_policy"


@pytest.mark.parametrize("source", ["repayment_reserve", "분양대금", "분양대금 상환계정", "sales_proceeds"])
def test_only_explicit_same_sales_reserve_source_aliases_are_accepted_and_preserved(source):
    request = _request()
    request["pfStructure"][0]["repaymentSource"] = source
    assert run_cash_flow_engine(request)["monthlyFinancing"]["tranches"][0]["repaymentSource"] == source


def test_overdue_debt_and_unfunded_cost_are_separate_and_contract_interest_continues():
    request = _request()
    request["sourcesAndUses"]["revenue"]["totalSalesRevenue"] = 0
    request["productMix"] = []
    request["schedule"]["completion"] = "M3"
    request["pfStructure"][0].update(amount=60, maturityMonths=1, feeRate=0)
    result = run_cash_flow_engine(request)
    rows = result["monthlyCashFlow"]
    assert rows[0]["monthlyFinancing"]["overdueDebt"] == 0
    assert rows[1]["monthlyFinancing"]["overdueDebt"] == 60
    assert rows[2]["monthlyFinancing"]["interest"] == pytest.approx(0.6)
    assert rows[3]["monthlyFinancing"]["interest"] == pytest.approx(0.6)
    assert result["summary"]["outstandingDebt"] == 60
    assert result["summary"]["unfundedOperatingCost"] == pytest.approx(41.8)
    assert result["summary"]["status"] == "monthly_financing_shortfall"
    assert {flag["code"] for flag in result["riskFlags"]} >= {"MONTHLY_DEBT_UNREPAID", "MONTHLY_OPERATING_COST_UNFUNDED"}
    _assert_balances(result)


def test_maturity_uses_absolute_project_month_and_late_reserve_reduces_overdue_principal():
    request = _request()
    request["schedule"] = {"construction_start": "M4", "completion": "M6"}
    request["pfStructure"][0].update(maturityMonths=2, interestRate=0, feeRate=0)
    request["productMix"][0]["collectionSteps"][0]["monthlyCollectionAmounts"] = [0, 0, 0, 300]
    result = run_cash_flow_engine(request)
    assert len(result["monthlyCashFlow"]) == 7
    assert result["monthlyCashFlow"][2]["monthlyFinancing"]["outstandingDebt"] == 100
    assert result["monthlyCashFlow"][2]["monthlyFinancing"]["overdueDebt"] == 100
    assert result["monthlyCashFlow"][3]["monthlyFinancing"]["principalRepayment"] == 100
    assert result["monthlyCashFlow"][3]["monthlyFinancing"]["outstandingDebt"] == 0
    assert result["monthlyFinancing"]["availableDrawCapacity"] == 0
    assert result["monthlyFinancing"]["expiredUndrawnCommitment"] == 100
    _assert_balances(result)


def test_multiple_tranches_repay_priority_and_commitment_does_not_revolve():
    request = _request()
    request["sourcesAndUses"]["revenue"]["totalSalesRevenue"] = 50
    request["productMix"][0].update(totalSalesRevenue=50, collectionSteps=[{"stage": "balance", "monthlyCollectionAmounts": [0, 0, 50]}])
    request["pfStructure"] = [
        {**request["pfStructure"][0], "amount": 60, "interestRate": 0, "feeRate": 0},
        {**request["pfStructure"][0], "trancheId": "junior", "tranche": "후순위", "name": "junior",
         "amount": 80, "interestRate": 0, "feeRate": 0, "repaymentPriority": 2},
    ]
    result = run_cash_flow_engine(request)
    final = result["monthlyCashFlow"][-1]["monthlyFinancing"]
    debt = {row["trancheId"]: row for row in final["debtBalances"]}
    assert debt["senior"]["principalRepayment"] == 50
    assert debt["senior"]["closingBalance"] == 10
    assert debt["junior"]["closingBalance"] == 40
    assert debt["senior"]["undrawnCommitment"] == 0
    assert result["monthlyFinancing"]["totalPrincipalRepaid"] == 50
    _assert_balances(result)


def test_duplicate_tranche_identity_is_rejected_instead_of_collapsing_debt_map():
    request = _request()
    request["pfStructure"].append(deepcopy(request["pfStructure"][0]))
    with pytest.raises(FinancialInputError, match="중복"):
        run_cash_flow_engine(request)


def test_reserve_is_not_swept_before_maturity_and_first_draw_fee_never_charged_without_draw():
    request = _request()
    request["productMix"][0]["collectionSteps"][0]["monthlyCollectionAmounts"] = [0, 300, 0]
    request["pfStructure"][0].update(interestRate=0, feeRate=0)
    result = run_cash_flow_engine(request)
    assert result["monthlyCashFlow"][1]["monthlyFinancing"]["principalRepayment"] == 0
    assert result["monthlyCashFlow"][1]["accounts"]["endingRepaymentReserve"] == 300
    assert result["monthlyCashFlow"][2]["monthlyFinancing"]["principalRepayment"] == 100
    # A full-limit fee leaves no spendable cash; do not fabricate a draw and fee.
    request["pfStructure"][0].update(feeRate=1)
    result = run_cash_flow_engine(request)
    assert result["monthlyFinancing"]["totalDraw"] == 0
    assert result["monthlyFinancing"]["calculatedFees"] == 0
    assert result["monthlyFinancing"]["peakUnfundedOperatingCost"] == 100
    _assert_balances(result)


@pytest.mark.parametrize("schedule", [None, {}, {"construction_start": "M0"}, {"completion": "M2"}])
def test_reviewed_monthly_mode_requires_both_start_and_completion_without_default_horizon(schedule):
    request = _request()
    request["schedule"] = schedule
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)
    assert caught.value.code == "monthly_financing_schedule_required"
    del request["monthlyFinancing"]
    assert run_cash_flow_engine(request)["status"] == "completed"


def test_default_calendar_completion_without_start_preserves_legacy_36_month_horizon():
    request = _request()
    request["schedule"] = {"completion": "2028-03-31"}
    del request["monthlyFinancing"]
    result = run_cash_flow_engine(request)
    assert len(result["monthlyCashFlow"]) == 37
    request["monthlyFinancing"] = dict(MONTHLY_FINANCING_POLICY)
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)
    assert caught.value.code == "monthly_financing_schedule_required"


@pytest.mark.parametrize("classifier", ["name", "priority"])
def test_direct_default_request_keeps_explicit_equity_out_of_pf_draw_capacity(classifier):
    request = _request()
    del request["monthlyFinancing"]
    equity = {"tranche": "출자금", classifier: "equity", "amount": 900}
    request["pfStructure"].append(equity)
    result = run_cash_flow_engine(request)
    assert result["summary"]["pfLimit"] == 200
    request["pfStructure"] = [equity]
    # Do not resurrect an explicitly equity-only structure through generic PF fallback.
    assert run_cash_flow_engine(request)["summary"]["pfLimit"] == 0


@pytest.mark.parametrize("collection", [0, 299, 301])
def test_reviewed_monthly_mode_rejects_unreconciled_sales_without_rescaling(collection):
    request = _request()
    request["productMix"][0]["collectionSteps"][0]["monthlyCollectionAmounts"] = [0, 0, collection]
    original = deepcopy(request)
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)
    assert caught.value.code == "monthly_financing_sales_total_mismatch"
    assert request == original
    del request["monthlyFinancing"]
    assert run_cash_flow_engine(request)["status"] == "completed"


def test_sales_reconciliation_uses_the_same_tolerance_as_cost_reconciliation():
    request = _request()
    request["productMix"][0]["collectionSteps"][0]["monthlyCollectionAmounts"] = [0, 0, 300 + 0.0000005]
    assert run_cash_flow_engine(request)["status"] == "completed"
    request["productMix"][0]["collectionSteps"][0]["monthlyCollectionAmounts"] = [0, 0, 300 + 0.000002]
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_engine(request)
    assert caught.value.code == "monthly_financing_sales_total_mismatch"


def test_working_reviewed_scenario_rejects_stale_canonical_imported_sales_schedule():
    from use_cases.cash_flow import build_cash_flow_request_use_case, run_cash_flow_use_case
    from use_cases.feasibility_workbook_llm import feasibility_review_state

    project = {
        "id": "working-review", "project": {"id": "working-review", "name": "수금 대사", "type": "apartment"},
        "feasibility_input": {"total_revenue": 300, "total_cost": 100, "land_cost": 100},
        "schedule": {"construction_start": "M0", "completion": "M2"},
        "pf_structure": [{"tranche_id": "senior", "name": "선순위", "priority": "senior", "amount": 200,
                          "interest_rate": 0.12, "fee_rate": 0.01, "term_months": 2,
                          "repayment_source": "repayment_reserve"}],
        "canonicalFeasibilityStudyV2": {"raw_import": {"productMonthlySalesRateScenarios": [{"products": [{
            "productType": "apartment", "productRevenue": 200,
            "collectionSteps": [{"stage": "balance", "monthlyCollectionAmounts": [0, 0, 200]}],
        }]}]}},
    }
    original = deepcopy(project)
    payload = {"reviewedMonthlyFinancing": {"policy": dict(MONTHLY_FINANCING_POLICY),
                "reason": "총매출 수정 후 수금 근거 대사", "expectedReviewHash": feasibility_review_state(project)["reviewHash"]}}
    mapped = build_cash_flow_request_use_case(project, payload)
    assert mapped["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 300
    assert mapped["productMix"][0]["totalSalesRevenue"] == 200
    with pytest.raises(FinancialInputError) as caught:
        run_cash_flow_use_case(project, payload)
    assert caught.value.code == "monthly_financing_sales_total_mismatch"
    assert project == original
