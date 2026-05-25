from __future__ import annotations

import datetime
from typing import Any, Dict, List

from core.calculations import calculate_tranche_recovery, calculate_unsold_ltv, simulate_waterfall
from schemas.scenario import (
    ScenarioDrivers,
    ScenarioReportFragments,
    ScenarioRiskFlag,
    ScenarioSetRequest,
    ScenarioSetResult,
    TrancheRecoveryRow,
    UnsoldLtvMatrix,
    UnsoldLtvRow,
    WaterfallResult,
    WaterfallResultRow,
)


class ScenarioEngineAgent:
    """
    ScenarioEngineAgent performs deterministic PF scenario calculations for
    waterfall allocation, unsold LTV, and tranche-level recovery.
    """

    def run_scenario_set(self, request: ScenarioSetRequest) -> ScenarioSetResult:
        scenario_set_id = request.scenarioSetId or f"scenario-set-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        drivers = request.scenarioDrivers or ScenarioDrivers()

        total_units = sum(item.units for item in request.productMix)
        total_revenue = sum(item.totalRevenue for item in request.productMix)
        avg_unit_price = (total_revenue / total_units) if total_units > 0 else 0.0
        total_pf_amount = sum(tranche.amount for tranche in request.pfStructure)

        sorted_tranches = sorted(request.pfStructure, key=lambda item: item.priority)
        tranche_dicts = [
            {
                "tranche": tranche.tranche,
                "amount": tranche.amount,
                "interest_rate": tranche.interestRate,
                "priority": tranche.priority,
            }
            for tranche in sorted_tranches
        ]

        waterfall_scenarios = self._build_waterfall_scenarios(
            drivers=drivers,
            total_revenue=total_revenue,
            sorted_tranches=sorted_tranches,
            tranche_dicts=tranche_dicts,
        )
        unsold_ltv_matrix = self._build_unsold_ltv_matrix(
            drivers=drivers,
            total_units=total_units,
            avg_unit_price=avg_unit_price,
            total_pf_amount=total_pf_amount,
        )
        tranche_recovery_matrix = self._build_tranche_recovery_matrix(
            drivers=drivers,
            total_units=total_units,
            avg_unit_price=avg_unit_price,
            total_revenue=total_revenue,
            sorted_tranches=sorted_tranches,
        )
        risk_flags = self._build_risk_flags(waterfall_scenarios, unsold_ltv_matrix)
        report_fragments = self._build_report_fragments(
            avg_unit_price=avg_unit_price,
            base_ltv=unsold_ltv_matrix.summary.get("baseCaseLtv", 0.0),
            max_ltv=unsold_ltv_matrix.summary.get("maxLtv", 0.0),
        )

        return ScenarioSetResult(
            scenarioSetId=scenario_set_id,
            analysisType="calculation_scenario_engine_v1",
            status="completed",
            dataStatus="complete",
            waterfallScenarios=waterfall_scenarios,
            unsoldLtvMatrix=unsold_ltv_matrix,
            trancheRecoveryMatrix=tranche_recovery_matrix,
            riskFlags=risk_flags,
            reportFragments=report_fragments,
            sourceMetadata=request.sourceMetadata,
            createdAt=datetime.datetime.now().isoformat(),
        )

    def _build_waterfall_scenarios(
        self,
        drivers: ScenarioDrivers,
        total_revenue: float,
        sorted_tranches: List[Any],
        tranche_dicts: List[Dict[str, Any]],
    ) -> List[WaterfallResult]:
        scenarios: List[WaterfallResult] = []
        for sale_rate in drivers.saleRates:
            for discount_rate in [0.0, 0.1]:
                scenario_id = f"sale_{int(sale_rate * 100)}_disc_{int(discount_rate * 100)}"
                gross_collection = total_revenue * sale_rate
                cash_available = max(0.0, gross_collection * (1.0 - discount_rate) - drivers.additionalPriorityCosts)
                raw_waterfall = simulate_waterfall(cash_available, tranche_dicts)

                rows: List[WaterfallResultRow] = []
                total_claim = 0.0
                total_payment = 0.0
                for idx, item in enumerate(raw_waterfall, start=1):
                    tranche_name = item["tranche"]
                    source_tranche = next((tranche for tranche in sorted_tranches if tranche.tranche == tranche_name), None)
                    principal = source_tranche.amount if source_tranche else 0.0
                    interest = principal * (source_tranche.interestRate if source_tranche else 0.0)
                    fee = principal * (source_tranche.feeRate if source_tranche else 0.0)
                    claim = principal + interest + fee
                    payment = item["payment_received"]
                    shortfall = max(0.0, claim - payment)
                    recovery_ratio = payment / claim if claim > 0 else 0.0
                    rows.append(
                        WaterfallResultRow(
                            rank=idx,
                            tranche=tranche_name,
                            priority=source_tranche.priority if source_tranche else idx,
                            principalClaim=principal,
                            interestClaim=interest,
                            feeClaim=fee,
                            totalClaim=claim,
                            paymentReceived=payment,
                            shortfall=shortfall,
                            recoveryRatio=round(recovery_ratio, 4),
                            status="repaid" if shortfall == 0 else ("partially_repaid" if payment > 0 else "unpaid"),
                        )
                    )
                    total_claim += claim
                    total_payment += payment

                scenarios.append(
                    WaterfallResult(
                        scenarioId=scenario_id,
                        saleRate=sale_rate,
                        discountRate=discount_rate,
                        cashAvailable=cash_available,
                        totalClaimAmount=total_claim,
                        remainingCash=max(0.0, cash_available - total_payment),
                        totalShortfall=max(0.0, total_claim - total_payment),
                        rows=rows,
                    )
                )
        return scenarios

    def _build_unsold_ltv_matrix(
        self,
        drivers: ScenarioDrivers,
        total_units: int,
        avg_unit_price: float,
        total_pf_amount: float,
    ) -> UnsoldLtvMatrix:
        rows: List[UnsoldLtvRow] = []
        ltv_values: List[float] = []
        breach_count = 0

        for sale_rate in drivers.saleRates:
            unsold_rate = max(0.0, 1.0 - sale_rate)
            unsold_units = int(total_units * unsold_rate)
            for discount_rate in drivers.discountRates:
                ltv = calculate_unsold_ltv(
                    pf_amount=total_pf_amount,
                    total_units=total_units,
                    unsold_units=unsold_units,
                    avg_unit_price=avg_unit_price,
                    discount_rate=discount_rate,
                )
                risk_level = self._ltv_risk_level(ltv)
                if risk_level in {"high", "critical"}:
                    breach_count += 1
                gross_value = unsold_units * avg_unit_price
                discounted_value = gross_value * (1.0 - discount_rate)
                rows.append(
                    UnsoldLtvRow(
                        saleRate=sale_rate,
                        unsoldRate=unsold_rate,
                        unsoldUnits=unsold_units,
                        discountRate=discount_rate,
                        grossUnsoldValue=gross_value,
                        discountedUnsoldValue=discounted_value,
                        unsoldLtv=round(ltv, 4),
                        riskLevel=risk_level,
                    )
                )
                if ltv > 0:
                    ltv_values.append(ltv)

        max_ltv = max(ltv_values) if ltv_values else 0.0
        best_ltv = min(ltv_values) if ltv_values else 0.0
        base_row = next((row for row in rows if abs(row.saleRate - 0.7) < 0.05 and abs(row.discountRate - 0.1) < 0.05), None)
        stress_row = next((row for row in rows if abs(row.saleRate - 0.5) < 0.05 and abs(row.discountRate - 0.2) < 0.05), None)

        return UnsoldLtvMatrix(
            pfAmount=total_pf_amount,
            totalUnits=total_units,
            avgUnitPrice=avg_unit_price,
            rows=rows,
            summary={
                "bestCaseLtv": round(best_ltv, 4),
                "baseCaseLtv": round(base_row.unsoldLtv if base_row else (sum(ltv_values) / len(ltv_values) if ltv_values else 0.0), 4),
                "stressCaseLtv": round(stress_row.unsoldLtv if stress_row else max_ltv, 4),
                "maxLtv": round(max_ltv, 4),
                "breachCount": breach_count,
            },
        )

    def _build_tranche_recovery_matrix(
        self,
        drivers: ScenarioDrivers,
        total_units: int,
        avg_unit_price: float,
        total_revenue: float,
        sorted_tranches: List[Any],
    ) -> List[TrancheRecoveryRow]:
        matrix: List[TrancheRecoveryRow] = []
        for sale_rate in drivers.saleRates:
            for discount_rate in [0.0, 0.1, 0.2]:
                scenario_id = f"recovery_sale_{int(sale_rate * 100)}_disc_{int(discount_rate * 100)}"
                unsold_units = int(total_units * max(0.0, 1.0 - sale_rate))
                discounted_unsold_value = unsold_units * avg_unit_price * (1.0 - discount_rate)
                collected_cash = max(0.0, total_revenue * sale_rate - drivers.additionalPriorityCosts)
                recovery_pool = collected_cash + discounted_unsold_value

                tranche_rows = []
                prior_claims = 0.0
                for tranche in sorted_tranches:
                    principal = tranche.amount
                    claim = principal + principal * tranche.interestRate + principal * tranche.feeRate
                    recovery_ratio = calculate_tranche_recovery(claim, recovery_pool, prior_claims)
                    tranche_rows.append(
                        {
                            "tranche": tranche.tranche,
                            "priority": tranche.priority,
                            "claimAmount": claim,
                            "recoveredAmount": claim * recovery_ratio,
                            "recoveryRatio": round(recovery_ratio, 4),
                        }
                    )
                    prior_claims += claim

                matrix.append(
                    TrancheRecoveryRow(
                        scenarioId=scenario_id,
                        saleRate=sale_rate,
                        discountRate=discount_rate,
                        recoveryPool=recovery_pool,
                        tranches=tranche_rows,
                    )
                )
        return matrix

    @staticmethod
    def _ltv_risk_level(ltv: float) -> str:
        if ltv <= 0.70:
            return "safe"
        if ltv <= 0.85:
            return "caution"
        if ltv <= 1.0:
            return "high"
        return "critical"

    @staticmethod
    def _build_risk_flags(
        waterfall_scenarios: List[WaterfallResult],
        unsold_ltv_matrix: UnsoldLtvMatrix,
    ) -> List[ScenarioRiskFlag]:
        risk_flags: List[ScenarioRiskFlag] = []
        base_waterfall = next((item for item in waterfall_scenarios if item.scenarioId == "sale_70_disc_0"), None)
        stress_waterfall = next((item for item in waterfall_scenarios if item.scenarioId == "sale_50_disc_10"), None)

        if base_waterfall and base_waterfall.totalShortfall > 0:
            risk_flags.append(ScenarioRiskFlag(code="WATERFALL_SHORTFALL", severity="high", message="기본 시나리오(분양률 70%)에서 전체 대출 원리금 상환 재원이 부족합니다."))
            for row in base_waterfall.rows:
                if row.shortfall <= 0:
                    continue
                if row.priority == 1:
                    risk_flags.append(ScenarioRiskFlag(code="SENIOR_SHORTFALL", severity="critical", message="기본 분양률 시나리오에서 선순위(Senior) 대출 상환에 손실이 발생합니다."))
                elif row.priority == 2:
                    risk_flags.append(ScenarioRiskFlag(code="MEZZANINE_SHORTFALL", severity="high", message="기본 분양률 시나리오에서 중순위(Mezzanine) 대출 상환 재원이 부족합니다."))

        if stress_waterfall and any(row.shortfall > 0 and row.priority == 1 for row in stress_waterfall.rows):
            risk_flags.append(ScenarioRiskFlag(code="STRESS_SENIOR_SHORTFALL", severity="high", message="스트레스 시나리오(분양률 50%)에서 선순위 대출 일부 상환 지연 또는 미상환 리스크가 있습니다."))

        max_ltv = unsold_ltv_matrix.summary.get("maxLtv", 0.0)
        if max_ltv > 0.90:
            risk_flags.append(ScenarioRiskFlag(code="UNSOLD_LTV_BREACH", severity="high", message=f"스트레스 시나리오의 미분양 담보 LTV가 최고 {max_ltv * 100:.1f}%로 기준치 90%를 초과합니다."))
        elif max_ltv > 0.80:
            risk_flags.append(ScenarioRiskFlag(code="UNSOLD_LTV_HIGH", severity="caution", message=f"미분양 담보 LTV가 최고 {max_ltv * 100:.1f}% 수준으로 상승하여 지속 관리가 필요합니다."))

        if unsold_ltv_matrix.summary.get("breachCount", 0) > 5:
            risk_flags.append(ScenarioRiskFlag(code="TRIGGER_REQUIRED", severity="high", message="다수의 시나리오에서 LTV 한도 및 원리금 회수성이 악화되므로 할인분양 실행조건, Cash Sweep, Trigger 설정이 필요합니다."))
        return risk_flags

    @staticmethod
    def _build_report_fragments(avg_unit_price: float, base_ltv: float, max_ltv: float) -> ScenarioReportFragments:
        return ScenarioReportFragments(
            waterfallSummary=(
                "분양대금 상환순서(Waterfall) 시뮬레이션 결과, 기준 분양률 70% 수준에서는 총 PF 원리금 및 수수료 청구액 대비 상환 가능성을 점검할 수 있습니다. "
                "다만 50% 이하의 스트레스 분양률 또는 할인분양이 발생하는 경우 중·후순위 대출 상환 재원에 공백이 발생할 수 있으므로 분양수입금 상환적립과 Cash Sweep 조건을 약정에 반영할 필요가 있습니다."
            ),
            unsoldLtvSummary=(
                f"미분양 발생 시 담보가치 검토 결과, 평균 분양가 KRW {avg_unit_price:,.0f} 기준으로 10% 할인분양을 가정한 기준 미분양 LTV는 {base_ltv * 100:.1f}% 수준으로 산정됩니다. "
                f"스트레스 조건에서는 LTV가 최고 {max_ltv * 100:.1f}%까지 상승할 수 있어 대주단의 담보보전 리스크 관리가 필요합니다."
            ),
            trancheRecoverySummary=(
                "각 Tranche별 최종 회수율(Recovery Rate)을 추정한 결과, 선순위 Tranche는 상대적으로 회수 안정성이 높으나 중순위 이하 채권은 분양률 하락 및 할인매각 적용 시 최종 회수율이 저하될 수 있습니다. "
                "따라서 후순위 노출액, Equity 완충, 공사비 유보 및 할인분양 Trigger를 함께 검토해야 합니다."
            ),
            approvalCondition=(
                "본건 구조의 리스크를 완화하기 위하여 다음 조건을 대출약정의 특약 또는 승인조건으로 제안합니다. "
                "1) 누적 분양률이 60%에 미달하는 기간에는 분양수입금의 상환적립 비율을 강화하고 대주단 협의 없는 자금집행을 제한할 것. "
                "2) 준공 후 3개월 내 미분양률이 20%를 초과하는 경우 대주단 협의 하에 10% 이상 할인분양 또는 Bulk Sale을 실행하는 Trigger 조항을 반영할 것."
            ),
        )
