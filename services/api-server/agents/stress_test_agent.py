from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from agents.cash_flow_engine import run_cash_flow_engine
from core.calculations import (
    calculate_irr_annualized,
    calculate_profit_margin,
    simulate_waterfall,
)
from schemas.stress_test import (
    StressScenarioDriver,
    StressScenarioResult,
    StressTestRequest,
    StressTestResult,
    StressTestSchedule,
)


_SENIOR_STATUS_LABELS = {
    "normal": "정상",
    "credit_enhancement_triggered": "신용보강 발동",
    "senior_principal_loss_possible": "원금 일부 손실 가능",
    "not_evaluated": "평가 불가",
}

DEFAULT_STRESS_SCENARIOS: List[StressScenarioDriver] = [
    StressScenarioDriver(key="base", name="Base Case", saleRate=0.92, constructionCostDelta=0.0, interestRateDelta=0.0, discountRate=0.0),
    StressScenarioDriver(key="mild", name="Mild Stress", saleRate=0.80, constructionCostDelta=0.05, interestRateDelta=0.005, discountRate=0.0),
    StressScenarioDriver(key="severe", name="Severe Stress", saleRate=0.60, constructionCostDelta=0.10, interestRateDelta=0.015, discountRate=0.10),
    StressScenarioDriver(key="tail", name="Tail Risk", saleRate=0.45, constructionCostDelta=0.15, interestRateDelta=0.025, discountRate=0.20),
]


class StressTestAgent:
    """Deterministic 4-stage PF stress test."""

    analysis_type = "stress_test_v1"

    def run_stress_test(self, request: StressTestRequest) -> StressTestResult:
        schedule = request.schedule or StressTestSchedule()
        scenarios = request.scenarios or DEFAULT_STRESS_SCENARIOS
        sorted_tranches = sorted(request.pfStructure, key=lambda t: t.priority)
        pf_amount = request.pfAmount or sum(t.amount for t in sorted_tranches)

        results = [
            self._run_scenario(driver, request, schedule, sorted_tranches, pf_amount)
            for driver in scenarios
        ]

        worst = min(results, key=lambda r: (r.projectIrr if r.projectIrr is not None else float("-inf")))
        first_loss = next((r for r in results if r.projectIrr is not None and r.projectIrr < 0), None)
        senior_break = next((r for r in results if r.seniorStatus == "senior_principal_loss_possible"), None)

        return StressTestResult(
            analysisType=self.analysis_type,
            scenarioCount=len(results),
            hurdleRate=request.hurdleRate,
            scenarios=results,
            worstCaseKey=worst.key if results else None,
            firstLossScenarioKey=first_loss.key if first_loss else None,
            seniorBreakScenarioKey=senior_break.key if senior_break else None,
            riskFlags=self._build_risk_flags(results),
            reportFragment=self._build_report_fragment(results),
            createdAt=datetime.datetime.now().isoformat(),
        )

    def _run_scenario(
        self,
        driver: StressScenarioDriver,
        request: StressTestRequest,
        schedule: StressTestSchedule,
        sorted_tranches: List[Any],
        pf_amount: float,
    ) -> StressScenarioResult:
        adjusted_construction = request.constructionCost * (1.0 + driver.constructionCostDelta)
        adjusted_finance = self._adjusted_finance_cost(driver, request, schedule, sorted_tranches, pf_amount)
        adjusted_total_cost = request.landCost + adjusted_construction + request.otherCost + adjusted_finance
        adjusted_revenue = request.totalRevenue * driver.saleRate * (1.0 - driver.discountRate)
        development_profit = adjusted_revenue - adjusted_total_cost
        profit_margin = calculate_profit_margin(development_profit, adjusted_revenue)

        net_cashflows, cashflow_source, cashflow_diagnostics = self._scenario_cashflow(
            request=request,
            schedule=schedule,
            adjusted_construction=adjusted_construction,
            adjusted_finance=adjusted_finance,
            adjusted_revenue=adjusted_revenue,
            pf_amount=pf_amount,
        )
        project_irr = calculate_irr_annualized(net_cashflows, request.periodsPerYear)
        senior_ratio, senior_status = self._senior_status(
            driver=driver,
            request=request,
            sorted_tranches=sorted_tranches,
            adjusted_revenue=adjusted_revenue,
            project_irr=project_irr,
        )

        return StressScenarioResult(
            key=driver.key,
            name=driver.name,
            saleRate=driver.saleRate,
            constructionCostDelta=driver.constructionCostDelta,
            interestRateDelta=driver.interestRateDelta,
            discountRate=driver.discountRate,
            adjustedRevenue=round(adjusted_revenue, 4),
            adjustedConstructionCost=round(adjusted_construction, 4),
            adjustedFinanceCost=round(adjusted_finance, 4),
            adjustedTotalCost=round(adjusted_total_cost, 4),
            developmentProfit=round(development_profit, 4),
            profitMargin=round(profit_margin, 4),
            projectIrr=round(project_irr, 6) if project_irr is not None else None,
            projectIrrStatus=self._irr_status(project_irr, request.hurdleRate),
            meetsHurdle=project_irr is not None and project_irr >= request.hurdleRate,
            seniorRecoveryRatio=round(senior_ratio, 4) if senior_ratio is not None else None,
            seniorStatus=senior_status,
            statusLabel=_SENIOR_STATUS_LABELS.get(senior_status, senior_status),
            cashflowSource=cashflow_source,
            cashflowDiagnostics=cashflow_diagnostics,
        )

    def _adjusted_finance_cost(
        self,
        driver: StressScenarioDriver,
        request: StressTestRequest,
        schedule: StressTestSchedule,
        sorted_tranches: List[Any],
        pf_amount: float,
    ) -> float:
        finance_years = max(0.0, (schedule.salesEndMonth - schedule.constructionStartMonth) / 12.0)
        # Anchor the base finance cost on the feasibility figure when available;
        # only the rate shock adds incremental interest. When no feasibility
        # finance cost is supplied, estimate the base from the tranche stack.
        base_finance = request.financeCost
        if base_finance <= 0 and sorted_tranches:
            base_finance = sum(t.amount * (t.interestRate + t.feeRate) for t in sorted_tranches) * finance_years
        exposure = pf_amount or sum(t.amount for t in sorted_tranches)
        rate_shock = exposure * driver.interestRateDelta * finance_years
        return base_finance + rate_shock

    def _scenario_cashflow(
        self,
        request: StressTestRequest,
        schedule: StressTestSchedule,
        adjusted_construction: float,
        adjusted_finance: float,
        adjusted_revenue: float,
        pf_amount: float,
    ) -> tuple[List[float], str, Dict[str, Any]]:
        """Unlevered project monthly cash flow for the scenario.

        Prefers the real Cash Flow Engine so the IRR reflects an S-curve sales
        collection and a PF-draw-aware schedule instead of a uniform spread.
        Falls back to the stylised uniform model if the engine cannot run.
        """
        engine_series, engine_diagnostics = self._engine_cashflow(
            request=request,
            schedule=schedule,
            adjusted_construction=adjusted_construction,
            adjusted_finance=adjusted_finance,
            adjusted_revenue=adjusted_revenue,
            pf_amount=pf_amount,
        )
        if engine_series is not None:
            return engine_series, "cash_flow_engine", engine_diagnostics
        fallback_series = self._build_scenario_cashflow(
            schedule=schedule,
            land_cost=request.landCost,
            construction_cost=adjusted_construction,
            other_cost=request.otherCost,
            finance_cost=adjusted_finance,
            revenue=adjusted_revenue,
        )
        diagnostics = {
            **engine_diagnostics,
            "source": "fallback_uniform",
            "seriesLength": len(fallback_series),
            "salesCollection": request.salesCollection,
            "periodMonths": schedule.months,
        }
        diagnostics.setdefault("fallbackReason", "cash_flow_engine_unavailable")
        return fallback_series, "fallback_uniform", diagnostics

    @staticmethod
    def _engine_cashflow(
        request: StressTestRequest,
        schedule: StressTestSchedule,
        adjusted_construction: float,
        adjusted_finance: float,
        adjusted_revenue: float,
        pf_amount: float,
    ) -> tuple[Optional[List[float]], Dict[str, Any]]:
        try:
            months = max(1, schedule.months)
            start_year = 2026
            completion_year = start_year + (months // 12)
            completion_month = (months % 12) + 1
            engine_request = {
                "schedule": {
                    "construction_start": f"{start_year}-01-01",
                    "completion": f"{completion_year}-{completion_month:02d}-01",
                },
                "productMix": [
                    {"totalSalesRevenue": adjusted_revenue, "defaultCollection": request.salesCollection}
                ],
                "sourcesAndUses": {
                    "cost": {
                        "landCost": request.landCost,
                        "directConstructionCost": adjusted_construction,
                        "financialCost": adjusted_finance,
                        "otherCost": request.otherCost,
                    },
                    "funding": {"pfLoan": pf_amount or sum(t.amount for t in request.pfStructure)},
                    "revenue": {"totalSalesRevenue": adjusted_revenue},
                },
                "pfStructure": [
                    {"tranche": t.tranche, "amount": t.amount, "drawPriority": t.priority}
                    for t in request.pfStructure
                ],
            }
            result = run_cash_flow_engine(engine_request)
            rows = result.get("monthlyCashFlow") or []
            series = [float(row.get("netCashFlowBeforePfDraw") or 0.0) for row in rows]
            if not series:
                return None, {
                    "source": "fallback_uniform",
                    "fallbackReason": "cash_flow_engine_empty_series",
                    "rowCount": len(rows),
                    "salesCollection": request.salesCollection,
                    "periodMonths": schedule.months,
                }
            return series, {
                "source": "cash_flow_engine",
                "rowCount": len(rows),
                "seriesLength": len(series),
                "salesCollection": request.salesCollection,
                "periodMonths": schedule.months,
            }
        except Exception as exc:
            return None, {
                "source": "fallback_uniform",
                "fallbackReason": "cash_flow_engine_error",
                "errorType": exc.__class__.__name__,
                "errorMessage": str(exc)[:160],
                "salesCollection": request.salesCollection,
                "periodMonths": schedule.months,
            }

    @staticmethod
    def _build_scenario_cashflow(
        schedule: StressTestSchedule,
        land_cost: float,
        construction_cost: float,
        other_cost: float,
        finance_cost: float,
        revenue: float,
    ) -> List[float]:
        months = max(1, schedule.months)
        outflow = [0.0] * months
        inflow = [0.0] * months

        land_month = min(max(0, schedule.landPaymentMonth), months - 1)
        outflow[land_month] += land_cost

        _spread(outflow, construction_cost, schedule.constructionStartMonth, schedule.constructionEndMonth, months)
        _spread(outflow, other_cost, schedule.constructionStartMonth, schedule.salesEndMonth, months)
        _spread(outflow, finance_cost, schedule.constructionStartMonth, schedule.salesEndMonth, months)
        _spread(inflow, revenue, schedule.salesStartMonth, schedule.salesEndMonth, months)

        return [inflow[m] - outflow[m] for m in range(months)]

    def _senior_status(
        self,
        driver: StressScenarioDriver,
        request: StressTestRequest,
        sorted_tranches: List[Any],
        adjusted_revenue: float,
        project_irr: Optional[float],
    ) -> tuple[Optional[float], str]:
        if not sorted_tranches:
            return None, "not_evaluated"

        unsold_fraction = max(0.0, 1.0 - driver.saleRate)
        unsold_collateral = (
            request.totalRevenue
            * unsold_fraction
            * (1.0 - driver.discountRate)
            * (1.0 - request.unsoldRecoveryHaircut)
        )
        recovery_pool = max(0.0, adjusted_revenue + unsold_collateral)

        tranche_dicts = [
            {
                "tranche": t.tranche,
                "amount": t.amount,
                "interest_rate": t.interestRate + driver.interestRateDelta + t.feeRate,
                "priority": t.priority,
            }
            for t in sorted_tranches
        ]
        waterfall = simulate_waterfall(recovery_pool, tranche_dicts)

        senior_priority = sorted_tranches[0].priority
        senior_names = {t.tranche for t in sorted_tranches if t.priority == senior_priority}
        senior_claim = 0.0
        senior_payment = 0.0
        for row in waterfall:
            if row["tranche"] in senior_names:
                senior_claim += row["amount_owed"]
                senior_payment += row["payment_received"]
        senior_ratio = (senior_payment / senior_claim) if senior_claim > 0 else None

        if senior_ratio is not None and senior_ratio < 0.999:
            return senior_ratio, "senior_principal_loss_possible"
        if project_irr is not None and project_irr < 0:
            return senior_ratio, "credit_enhancement_triggered"
        return senior_ratio, "normal"

    @staticmethod
    def _irr_status(rate: Optional[float], hurdle: float) -> str:
        if rate is None:
            return "not_available"
        if rate < 0:
            return "loss"
        if rate >= hurdle:
            return "meets_hurdle"
        return "below_hurdle"

    @staticmethod
    def _build_risk_flags(results: List[StressScenarioResult]) -> List[Dict[str, Any]]:
        flags: List[Dict[str, Any]] = []
        for result in results:
            if result.seniorStatus == "senior_principal_loss_possible":
                flags.append(
                    {
                        "code": "STRESS_SENIOR_PRINCIPAL_LOSS",
                        "severity": "critical",
                        "scenarioKey": result.key,
                        "message": f"'{result.name}' 시나리오에서 Senior 회수율이 {(result.seniorRecoveryRatio or 0) * 100:.1f}%로 원금 손실 가능성이 있습니다.",
                    }
                )
            elif result.seniorStatus == "credit_enhancement_triggered":
                flags.append(
                    {
                        "code": "STRESS_CREDIT_ENHANCEMENT",
                        "severity": "high",
                        "scenarioKey": result.key,
                        "message": f"'{result.name}' 시나리오에서 사업 IRR이 음수로 전환되어 신용보강 발동 가능성이 있습니다.",
                    }
                )
        return flags

    @staticmethod
    def _build_report_fragment(results: List[StressScenarioResult]) -> str:
        def pct(value: Optional[float]) -> str:
            return f"{value * 100:.1f}%" if value is not None else "N/A"

        lines = [
            "4단계 Stress Test는 분양률, 공사비, 금리 변수를 동시에 조정하여 사업 안정성과 Senior 원금보존 가능성을 검증합니다.",
        ]
        for result in results:
            lines.append(
                f"- {result.name}: 분양률 {result.saleRate * 100:.0f}%, 공사비 {result.constructionCostDelta * 100:+.0f}%, "
                f"금리 {result.interestRateDelta * 100:+.1f}%p, 할인율 {result.discountRate * 100:.0f}% 기준 "
                f"Project IRR {pct(result.projectIrr)}, Senior 상태 '{result.statusLabel}'."
            )
        loss = next((r for r in results if r.seniorStatus == "senior_principal_loss_possible"), None)
        if loss:
            lines.append(f"'{loss.name}' 구간부터 Senior 원금 손실 가능성이 있어 추가 신용보강 또는 구조 조정이 필요합니다.")
        else:
            lines.append("모든 시나리오에서 Senior 원금은 회수 가능 범위로 평가됩니다.")
        return "\n".join(lines)


def _spread(series: List[float], amount: float, start_month: int, end_month: int, months: int) -> None:
    if amount == 0:
        return
    start = max(1, start_month)
    end = min(months, max(start, end_month))
    span = end - start + 1
    if span <= 0:
        return
    per = amount / span
    for month in range(start, end + 1):
        series[month - 1] += per
