import pytest

from agents.cash_sweep_agent import CashSweepAgent
from schemas.cash_sweep import CashSweepRequest, CashSweepTranche


def _tranches():
    return [
        CashSweepTranche(tranche="Senior", amount=1000.0, priority=1, interestRate=0.06),
        CashSweepTranche(tranche="Mezzanine", amount=400.0, priority=2, interestRate=0.10),
    ]


def test_sweep_repays_senior_before_mezzanine():
    # Large monthly surplus -> senior should clear before any mezzanine principal.
    result = CashSweepAgent().simulate(CashSweepRequest(
        monthlyOperatingCashflow=[600, 600, 600],
        tranches=_tranches(),
        sweepRatio=1.0,
    ))
    senior_repaid = result.trancheFullRepaidMonth["Senior"]
    mezz_repaid = result.trancheFullRepaidMonth["Mezzanine"]
    assert senior_repaid is not None
    # Mezzanine only starts being repaid after senior is cleared.
    first_mezz_payment_month = next(
        (r.month for r in result.rows if r.tranchePrincipalPaid["Mezzanine"] > 0), None
    )
    assert first_mezz_payment_month is not None and first_mezz_payment_month >= senior_repaid


def test_reserve_funds_before_principal_sweep():
    result = CashSweepAgent().simulate(CashSweepRequest(
        monthlyOperatingCashflow=[100],
        tranches=_tranches(),
        reserveTarget=50.0,
        sweepRatio=1.0,
    ))
    row = result.rows[0]
    # interest accrued month 1 = 1000*0.005 + 400*(0.10/12) = 5 + 3.333... = 8.333
    assert row.interestPaid == pytest.approx(8.3333, abs=1e-3)
    # remaining 91.667 -> 50 to reserve, rest swept to principal
    assert row.reserveContribution == pytest.approx(50.0)
    assert row.reserveBalance == pytest.approx(50.0)
    assert row.principalPaid == pytest.approx(41.6667, abs=1e-3)


def test_interest_shortfall_when_cash_insufficient():
    result = CashSweepAgent().simulate(CashSweepRequest(
        monthlyOperatingCashflow=[2.0],  # less than the ~8.33 interest due
        tranches=_tranches(),
    ))
    row = result.rows[0]
    assert row.interestPaid == pytest.approx(2.0)
    assert row.interestShortfall == pytest.approx(6.3333, abs=1e-3)
    assert row.principalPaid == 0.0
    assert result.totalInterestShortfall > 0


def test_sweep_ratio_partial_leaves_balance():
    full = CashSweepAgent().simulate(CashSweepRequest(
        monthlyOperatingCashflow=[800, 800], tranches=_tranches(), sweepRatio=1.0,
    ))
    half = CashSweepAgent().simulate(CashSweepRequest(
        monthlyOperatingCashflow=[800, 800], tranches=_tranches(), sweepRatio=0.5,
    ))
    assert half.totalPrincipalSwept < full.totalPrincipalSwept
    assert half.endingTotalBalance > full.endingTotalBalance


def test_full_repayment_clears_all_tranches():
    result = CashSweepAgent().simulate(CashSweepRequest(
        monthlyOperatingCashflow=[2000], tranches=_tranches(), sweepRatio=1.0,
    ))
    assert result.endingTotalBalance == pytest.approx(0.0, abs=1e-3)
    assert result.trancheFullRepaidMonth["Senior"] == 1
    assert result.trancheFullRepaidMonth["Mezzanine"] == 1
