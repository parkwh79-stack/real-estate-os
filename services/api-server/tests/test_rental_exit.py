from copy import deepcopy

import pytest

from core.financial_input_validation import FinancialInputError
from core.rental_exit import run_rental_exit


def scenario():
    return {
        "currency": "KRW", "asOfDate": "2026-01-01",
        "openingUnrestrictedCash": 20, "openingDepositBalance": 10,
        "periods": [
            {"date": "2026-02-01", "potentialRent": 20, "occupancyRate": 0.9, "otherOperatingIncome": 0,
             "operatingExpense": 8, "capitalExpense": 2, "depositReceived": 5, "depositRefunded": 2,
             "debtService": 1, "sourceLocator": {"sheet": "운영", "cell": "D12"}},
            {"date": "2026-04-01", "potentialRent": 20, "occupancyRate": 0.9, "otherOperatingIncome": 0,
             "operatingExpense": 8, "capitalExpense": 2, "depositReceived": 0, "depositRefunded": 0,
             "debtService": 1, "refinancePrincipalRepayment": 3},
        ],
        "maturity": {"date": "2026-03-01", "principal": 80, "interest": 2, "fees": 1},
        "refinance": {
            "fundingDate": "2026-03-01", "requestedPrincipal": 60, "fees": 2, "reserve": 1,
            "ltv": {"valuation": 120, "limit": 0.6},
            "dscr": {"annualCfads": 12, "minimum": 1.25, "annualInterestRate": 0.12, "amortizationMonths": None},
            "debtYield": {"annualNoi": 9, "minimum": 0.1}, "commitmentLimit": 62,
        },
        "exit": {
            "date": "2026-05-01", "grossSalePrice": 130, "transactionCosts": 5, "taxes": 4,
            "depositTreatment": "transfer", "refinancePayoffInterest": 2, "refinancePayoffFees": 1,
            "refinanceReserveReleased": 1,
        },
        "investors": [
            {"id": "LP", "investedAt": "2025-01-01", "capital": 35, "principalPriority": 1, "remainderBps": 6000},
            {"id": "GP", "investedAt": "2025-01-01", "capital": 30, "principalPriority": 2, "remainderBps": 4000},
        ],
    }


def test_rent_refinance_same_day_settlement_sale_and_recovery_reconcile():
    inputs = scenario()
    before = deepcopy(inputs)
    result = run_rental_exit(inputs)
    assert inputs == before
    assert result["reportEligible"] is False
    assert result["refinance"]["caps"] == {"ltv": 72, "dscr": 80, "debtYield": 90, "commitment": 62}
    assert result["refinance"]["bindingCaps"] == ["commitment"]
    assert result["refinance"]["netProceeds"] == 57
    assert result["maturity"]["availableBeforeSettlement"] == 84
    assert result["maturity"]["debtPaid"] == 83
    assert result["summary"]["maturityShortfall"] == 0
    assert result["sale"]["netSaleCash"] == 108
    assert result["sale"]["depositSettlement"] == 13
    assert result["summary"]["totalNoi"] == 20
    assert result["summary"]["distributableCash"] == 67
    assert result["summary"]["distributionReconciliationDifference"] == 0
    assert result["summary"]["decision"] == "settled"
    assert [(row["id"], row["returnedCapital"], row["profitDistribution"]) for row in result["investorRecovery"]] == [
        ("LP", 35, 1), ("GP", 30, 1),
    ]
    assert result["investorRecovery"][0]["xirr"] > 0
    assert result["ledger"][0]["sourceLocator"] == {"sheet": "운영", "cell": "D12"}
    for row in result["ledger"]:
        assert row["beginningProjectedCash"] + row["netUnrestrictedFlow"] == row["endingProjectedCash"]
        assert row["beginningRestrictedDeposits"] + row["netRestrictedDepositFlow"] == row["endingRestrictedDeposits"]
        assert row["beginningRefinanceReserve"] + row["netRefinanceReserveFlow"] == row["endingRefinanceReserve"]
    settlement = next(row for row in result["ledger"] if row["kind"] == "exit_settlement")
    assert settlement["refinancePrincipalDue"] == 57
    assert settlement["debtPaid"] == 60
    assert result["ledger"][-1]["endingProjectedCash"] == 0


@pytest.mark.parametrize(("funding_date", "amount", "status"), [
    ("2026-03-02", 60, "funding_after_maturity"),
    ("2026-03-01", 63, "limit_exceeded"),
    ("2026-03-01", 50, "insufficient_closing_funds"),
])
def test_unfunded_refinance_does_not_fill_gap_or_produce_phantom_debt(funding_date, amount, status):
    inputs = scenario()
    inputs["refinance"].update(fundingDate=funding_date, requestedPrincipal=amount)
    inputs["exit"]["overdueDebtCharges"] = 2
    result = run_rental_exit(inputs)
    assert result["refinance"]["status"] == status
    assert result["refinance"]["drawnPrincipal"] == 0
    assert result["maturity"]["shortfall"] == 56
    assert result["summary"]["maturityShortfall"] == 56
    assert result["summary"]["exitDebtShortfall"] == 0
    assert result["summary"]["decision"] == "shortfall"
    assert result["summary"]["distributableCash"] == 71
    assert result["sale"]["excludedUnfundedRefinanceReserveRelease"] == 1
    no_draw = next(row for row in result["ledger"] if row["date"] == "2026-04-01")
    assert no_draw["excludedUnfundedRefinanceService"] == 1
    assert no_draw["excludedUnfundedRefinancePrincipal"] == 3


def test_old_debt_and_deposit_close_once_when_sale_lands_at_maturity():
    inputs = scenario()
    inputs["periods"].pop()
    inputs["refinance"] = None
    inputs["exit"].update(date=inputs["maturity"]["date"], depositTreatment="refund",
                          refinancePayoffInterest=0, refinancePayoffFees=0, refinanceReserveReleased=0)
    result = run_rental_exit(inputs)
    assert result["maturity"]["shortfall"] == 0
    assert result["summary"]["distributableCash"] == 65
    assert result["summary"]["unpaidInvestorCapital"] == 0
    assert result["ledger"][-1]["endingRestrictedDeposits"] == 0
    assert sum(row.get("debtPaid", 0) for row in result["ledger"]) == 83


def test_unfunded_operating_cash_and_principal_due_remain_in_settlement():
    inputs = scenario()
    inputs["periods"][1].update(capitalExpense=20, depositReceived=100)
    inputs["exit"]["grossSalePrice"] = 48
    result = run_rental_exit(inputs)
    assert result["summary"]["peakOperatingShortfall"] == 10
    assert result["summary"]["scheduledRefinancePrincipalUnderpayment"] == 3
    settlement = next(row for row in result["ledger"] if row["kind"] == "exit_settlement")
    assert settlement["refinancePrincipalDue"] == 60
    assert result["summary"]["exitDebtShortfall"] == 33
    assert result["summary"]["distributableCash"] == 0
    assert result["summary"]["unpaidInvestorCapital"] == 65


def test_exit_returns_principal_by_priority_and_splits_same_priority_whole_won():
    inputs = scenario()
    inputs["exit"]["grossSalePrice"] = 120
    first = run_rental_exit(inputs)
    assert [row["returnedCapital"] for row in first["investorRecovery"]] == [35, 22]
    assert first["summary"]["unpaidInvestorCapital"] == 8
    inputs["investors"][1]["principalPriority"] = 1
    second = run_rental_exit(inputs)
    assert [row["returnedCapital"] for row in second["investorRecovery"]] == [31, 26]
    assert second["summary"]["distributionReconciliationDifference"] == 0


def test_dscr_level_payment_sizing_has_independent_zero_rate_check():
    inputs = scenario()
    terms = inputs["refinance"]["dscr"]
    terms.update(annualInterestRate=0, amortizationMonths=60)
    result = run_rental_exit(inputs)
    assert result["refinance"]["caps"]["dscr"] == 48  # 12 / 1.25 / (12 / 60)
    assert result["refinance"]["status"] == "limit_exceeded"


@pytest.mark.parametrize("defect", [
    "missing_operating_expense", "repeated_period", "unfunded_deposit_refund", "invalid_bps",
    "same_day_refi_and_sale", "unsupported_waterfall", "invalid_cap", "nonfinite_rate",
])
def test_invalid_or_unsupported_terms_raise_domain_error(defect):
    inputs = scenario()
    if defect == "missing_operating_expense":
        del inputs["periods"][0]["operatingExpense"]
    elif defect == "repeated_period":
        inputs["periods"].append(dict(inputs["periods"][0]))
    elif defect == "unfunded_deposit_refund":
        inputs["periods"][0]["depositRefunded"] = 100
    elif defect == "invalid_bps":
        inputs["investors"][0]["remainderBps"] -= 1
    elif defect == "same_day_refi_and_sale":
        inputs["periods"].pop()
        inputs["exit"]["date"] = inputs["maturity"]["date"]
    elif defect == "unsupported_waterfall":
        inputs["investors"][0]["preferredReturn"] = 0.08
    elif defect == "invalid_cap":
        inputs["refinance"]["debtYield"]["minimum"] = 1e-300
    else:
        inputs["periods"][0]["occupancyRate"] = float("nan")
    with pytest.raises(FinancialInputError):
        run_rental_exit(inputs)


def test_missing_overdue_debt_charges_keeps_maturity_result_and_defers_distribution():
    inputs = scenario()
    inputs["refinance"] = None
    result = run_rental_exit(inputs)
    assert result["status"] == "partial"
    assert result["summary"]["maturityShortfall"] == 56
    assert result["summary"]["distributableCash"] is None
    assert result["exitSettlement"]["status"] == "needs_overdue_debt_charges"
    assert result["investorRecovery"] == []
