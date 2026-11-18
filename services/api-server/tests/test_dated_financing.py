from copy import deepcopy

import pytest

from core.dated_financing import run_dated_financing
from core.financial_input_validation import FinancialInputError


def _loan(**overrides):
    return {
        "trancheId": "senior",
        "commitment": 36_500,
        "drawAvailableFrom": "2024-02-28",
        "drawAvailableThrough": "2024-03-10",
        "maturityDate": "2024-03-11",
        "dayCount": "ACT/365F",
        "repaymentPriority": 1,
        "rateSchedule": [{"date": "2024-02-28", "annualRate": 0.10}],
        "draws": [{"date": "2024-02-28", "amount": 36_500, "conditionsMet": True}],
        "scheduledPrincipal": [],
        "interestPaymentDates": [],
        "fees": [],
        **overrides,
    }


def _scenario(*loans, **overrides):
    return {
        "currency": "KRW",
        "openingRepaymentCash": 0,
        "repaymentCashMovements": [],
        "asOfDate": "2024-03-11",
        "tranches": list(loans or (_loan(),)),
        **overrides,
    }


def test_leap_day_rate_change_partial_repayment_and_maturity_shortfall():
    case = _scenario(
        _loan(
            rateSchedule=[
                {"date": "2024-02-28", "annualRate": 0.10},
                {"date": "2024-03-01", "annualRate": 0.20, "sourceLocator": {"cell": "금융조건!B13"}},
            ],
            scheduledPrincipal=[{"date": "2024-03-01", "amount": 18_250}],
            fees=[{"date": "2024-03-01", "amount": 500}],
        ),
        asOfDate="2024-03-15",
        repaymentCashMovements=[
            {"date": "2024-03-01", "amount": 18_750},
            {"date": "2024-03-11", "amount": 18_300},
            {"date": "2024-03-12", "amount": 90},
        ],
    )
    original = deepcopy(case)
    result = run_dated_financing(case)
    assert case == original
    assert result["analysisOnly"] and not result["reportEligible"]
    assert result["status"] == "shortfall"
    reset_row = result["datedLedger"][1]
    assert reset_row["interestDays"] == 2  # Includes February 29.
    assert reset_row["interestAccruedForInterval"] == 20
    assert reset_row["effectiveAnnualRate"] == 0.20
    assert reset_row["sourceLocators"][1]["suppliedLocator"]["cell"] == "금융조건!B13"

    # 36,500 × 10% × 2/365 + 18,250 × 20% × 10/365 = 120.
    assert result["summary"]["totalInterestAccrued"] == 120
    assert result["payments"][0]["feePaid"] == 500
    assert result["payments"][0]["principalPaid"] == 18_250
    maturity = result["maturities"][0]
    assert (maturity["date"], maturity["totalDue"], maturity["totalPaid"], maturity["shortfall"]) == (
        "2024-03-11", 18_370, 18_300, 70,
    )
    assert (maturity["interestPaid"], maturity["principalPaid"], maturity["feeDue"]) == (120, 18_180, 0)
    assert result["summary"]["endingPrincipal"] == 70
    assert result["summary"]["unpaidPrincipal"] == 70
    assert result["summary"]["endingRepaymentCash"] == 90
    assert result["summary"]["peakMaturityShortfall"] == 70
    assert result["summary"]["totalDebtServicePaid"] == 37_050


def test_same_day_maturities_observe_priority_and_pool_the_shortfall():
    senior = _loan(commitment=1000, draws=[{"date": "2024-02-28", "amount": 1000, "conditionsMet": True}],
                   rateSchedule=[{"date": "2024-02-28", "annualRate": 0}])
    junior = {**senior, "trancheId": "junior", "repaymentPriority": 2}
    case = _scenario(junior, senior, openingRepaymentCash=700)
    result = run_dated_financing(case)
    assert [(row["trancheId"], row["availableBeforePayment"], row["shortfall"]) for row in result["maturities"]] == [
        ("senior", 700, 300), ("junior", 0, 1000),
    ]
    assert result["summary"]["peakMaturityShortfall"] == 1300
    assert result["summary"]["totalMaturityShortfall"] == 1300
    assert result["maturityShortfallsByDate"] == [{"date": "2024-03-11", "shortfall": 1300}]
    assert result["summary"]["endingRepaymentCash"] == 0


def test_draw_constraints_accrual_only_on_drawn_principal_and_pending_maturity():
    result = run_dated_financing(_scenario(
        _loan(
            commitment=36_000,
            dayCount="ACT/360",
            drawAvailableThrough="2024-03-05",
            draws=[
                {"date": "2024-02-28", "amount": 36_000, "conditionsMet": True},
                {"date": "2024-03-01", "amount": 100, "conditionsMet": False},
                {"date": "2024-03-03", "amount": 12_000, "conditionsMet": True},
                {"date": "2024-03-06", "amount": 10, "conditionsMet": True},
            ],
            interestPaymentDates=["2024-03-01"],
            scheduledPrincipal=[{"date": "2024-03-02", "amount": 18_000}],
        ),
        asOfDate="2024-03-08",
        openingRepaymentCash=18_020,
    ))
    assert [row["reason"] for row in result["draws"]] == [
        None, "conditions_not_met", "commitment_exceeded", "outside_draw_window",
    ]
    assert [row["undrawnRequest"] for row in result["draws"]] == [0, 100, 12_000, 10]
    assert result["payments"][0]["totalDue"] == 20
    assert result["payments"][1]["principalPaid"] == 18_000
    assert result["summary"]["endingPrincipal"] == 18_000
    # Three days on 36,000 plus six days on 18,000 at ACT/360.
    assert result["summary"]["totalInterestAccrued"] == 60
    assert result["summary"]["accruedInterestNotDue"] == 40
    assert result["summary"]["peakMaturityShortfall"] is None
    assert result["summary"]["maturitiesPending"] == 1


def test_missed_scheduled_principal_is_in_maturity_balance_only_once():
    result = run_dated_financing(_scenario(
        _loan(
            rateSchedule=[{"date": "2024-02-28", "annualRate": 0}],
            scheduledPrincipal=[{"date": "2024-03-01", "amount": 1_000}],
        ),
        repaymentCashMovements=[{"date": "2024-03-11", "amount": 36_499}],
    ))
    assert result["payments"][0]["shortfall"] == 1_000
    assert result["maturities"][0]["principalDue"] == 36_500
    assert result["maturities"][0]["shortfall"] == 1
    assert result["summary"]["unpaidPrincipal"] == 1


@pytest.mark.parametrize("patch", [
    {"dayCount": "unknown"},
    {"rateSchedule": [{"date": "2024-02-29", "annualRate": 0.10}]},
    {"rateSchedule": [{"date": "2024-02-28", "annualRate": float("nan")}]},
    {"draws": [{"date": "2024-02-28", "amount": 36_500}]},
    {"scheduledPrincipal": [{"date": "2024-03-11", "amount": 36_500}]},
    {"drawAvailableThrough": "2024-03-11"},
    {"maturityDate": "20240311"},
    {"capitalization": True},
])
def test_unrecognized_or_missing_contract_data_rejected(patch):
    with pytest.raises(FinancialInputError, match="tranches") as caught:
        run_dated_financing(_scenario(_loan(**patch)))
    assert caught.value.code == "invalid_dated_financing"


def test_repayment_account_debits_cannot_create_negative_cash():
    with pytest.raises(FinancialInputError, match="상환계정 인출액"):
        run_dated_financing(_scenario(repaymentCashMovements=[{"date": "2024-03-05", "amount": -1}]))
