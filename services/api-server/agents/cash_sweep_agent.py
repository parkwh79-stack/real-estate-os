from __future__ import annotations

import datetime
from typing import Dict, List, Optional

from schemas.cash_sweep import (
    CashSweepMonthRow,
    CashSweepRequest,
    CashSweepResult,
    CashSweepTranche,
)


class CashSweepAgent:
    """Deterministic monthly Cash Sweep + Reserve simulation.

    Each month: accrue interest, pay interest by seniority, top up the reserve
    (DSRA) toward its target before principal, then sweep the remaining surplus
    to principal prepayment in priority order. This models the PLATFORM9 deck's
    Cash Sweep / Reserve Account mechanic.
    """

    analysis_type = "cash_sweep_v1"

    def simulate(self, request: CashSweepRequest) -> CashSweepResult:
        ppy = max(1, request.periodsPerYear)
        sweep_ratio = max(0.0, min(1.0, request.sweepRatio))
        sorted_tranches = sorted(request.tranches, key=lambda t: t.priority)

        balances: Dict[str, float] = {t.tranche: t.amount for t in sorted_tranches}
        monthly_rate: Dict[str, float] = {
            t.tranche: (t.interestRate + t.feeRate) / ppy for t in sorted_tranches
        }
        full_repaid_month: Dict[str, Optional[int]] = {t.tranche: None for t in sorted_tranches}
        for t in sorted_tranches:
            if balances[t.tranche] <= 0:
                full_repaid_month[t.tranche] = 0

        reserve = 0.0
        rows: List[CashSweepMonthRow] = []
        total_interest_paid = 0.0
        total_interest_shortfall = 0.0
        total_principal_swept = 0.0

        for index, raw_cash in enumerate(request.monthlyOperatingCashflow, start=1):
            opening = sum(balances.values())
            cash = float(raw_cash)

            interest_accrued = sum(balances[t.tranche] * monthly_rate[t.tranche] for t in sorted_tranches)
            interest_paid = min(max(0.0, cash), interest_accrued) if cash > 0 else 0.0
            interest_shortfall = max(0.0, interest_accrued - interest_paid)
            cash_after_interest = max(0.0, cash - interest_paid)

            reserve_gap = max(0.0, request.reserveTarget - reserve)
            reserve_contribution = min(cash_after_interest, reserve_gap)
            reserve += reserve_contribution
            cash_after_reserve = cash_after_interest - reserve_contribution

            sweep_available = cash_after_reserve * sweep_ratio
            remaining_sweep = sweep_available
            principal_paid_total = 0.0
            tranche_principal_paid: Dict[str, float] = {}
            for t in sorted_tranches:
                pay = min(remaining_sweep, balances[t.tranche])
                if pay > 0:
                    balances[t.tranche] -= pay
                    remaining_sweep -= pay
                    principal_paid_total += pay
                tranche_principal_paid[t.tranche] = round(pay, 4)
                if balances[t.tranche] <= 1e-6 and full_repaid_month[t.tranche] is None:
                    full_repaid_month[t.tranche] = index

            total_interest_paid += interest_paid
            total_interest_shortfall += interest_shortfall
            total_principal_swept += principal_paid_total

            rows.append(
                CashSweepMonthRow(
                    month=index,
                    openingBalance=round(opening, 4),
                    interestAccrued=round(interest_accrued, 4),
                    interestPaid=round(interest_paid, 4),
                    interestShortfall=round(interest_shortfall, 4),
                    reserveContribution=round(reserve_contribution, 4),
                    reserveBalance=round(reserve, 4),
                    sweepAvailable=round(sweep_available, 4),
                    principalPaid=round(principal_paid_total, 4),
                    endingBalance=round(sum(balances.values()), 4),
                    tranchePrincipalPaid=tranche_principal_paid,
                    trancheEndingBalance={k: round(v, 4) for k, v in balances.items()},
                )
            )

        return CashSweepResult(
            analysisType=self.analysis_type,
            months=len(rows),
            sweepRatio=sweep_ratio,
            reserveTarget=request.reserveTarget,
            totalInterestPaid=round(total_interest_paid, 4),
            totalInterestShortfall=round(total_interest_shortfall, 4),
            totalPrincipalSwept=round(total_principal_swept, 4),
            endingReserveBalance=round(reserve, 4),
            endingTotalBalance=round(sum(balances.values()), 4),
            trancheFullRepaidMonth=full_repaid_month,
            rows=rows,
            summary=self._build_summary(
                sorted_tranches,
                full_repaid_month,
                sum(balances.values()),
                total_interest_shortfall,
            ),
            createdAt=datetime.datetime.now().isoformat(),
        )

    @staticmethod
    def _build_summary(
        tranches: List[CashSweepTranche],
        full_repaid_month: Dict[str, Optional[int]],
        ending_balance: float,
        interest_shortfall: float,
    ) -> str:
        senior = tranches[0].tranche if tranches else None
        parts: List[str] = []
        if senior is not None:
            month = full_repaid_month.get(senior)
            if month:
                parts.append(f"Cash Sweep으로 최우선순위 '{senior}' 원금은 {month}개월 차에 전액 상환됩니다.")
            else:
                parts.append(f"최우선순위 '{senior}' 원금은 분석 기간 내 전액 상환되지 않습니다.")
        if ending_balance > 1e-6:
            parts.append(f"기말 미상환 잔액은 KRW {ending_balance:,.0f}이며 추가 회수·매각 전략이 필요합니다.")
        else:
            parts.append("분석 기간 내 전체 Tranche 원금이 전액 상환됩니다.")
        if interest_shortfall > 1e-6:
            parts.append(f"이자 지급 부족액 합계는 KRW {interest_shortfall:,.0f}로 Reserve 또는 추가 신용보강 검토가 필요합니다.")
        return " ".join(parts)
