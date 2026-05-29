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
                "interest_rate": tranche.interestRate + tranche.feeRate,
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
            risk_flags.append(ScenarioRiskFlag(
                code="WATERFALL_SHORTFALL",
                severity="high",
                message="Base waterfall scenario has total repayment shortfall.",
                sourceLocators=[_waterfall_locator(base_waterfall.scenarioId, "totalShortfall", base_waterfall.totalShortfall)],
            ))
            for row in base_waterfall.rows:
                if row.shortfall <= 0:
                    continue
                if row.priority == 1:
                    risk_flags.append(ScenarioRiskFlag(
                        code="SENIOR_SHORTFALL",
                        severity="critical",
                        message="Base waterfall scenario shows senior tranche repayment shortfall.",
                        sourceLocators=[_waterfall_row_locator(base_waterfall.scenarioId, row)],
                    ))
                elif row.priority == 2:
                    risk_flags.append(ScenarioRiskFlag(
                        code="MEZZANINE_SHORTFALL",
                        severity="high",
                        message="Base waterfall scenario shows mezzanine tranche repayment shortfall.",
                        sourceLocators=[_waterfall_row_locator(base_waterfall.scenarioId, row)],
                    ))

        if stress_waterfall and any(row.shortfall > 0 and row.priority == 1 for row in stress_waterfall.rows):
            stress_row = next((row for row in stress_waterfall.rows if row.shortfall > 0 and row.priority == 1), None)
            risk_flags.append(ScenarioRiskFlag(
                code="STRESS_SENIOR_SHORTFALL",
                severity="high",
                message="Stress waterfall scenario shows senior tranche repayment shortfall.",
                sourceLocators=[_waterfall_row_locator(stress_waterfall.scenarioId, stress_row)] if stress_row else [_waterfall_locator(stress_waterfall.scenarioId, "totalShortfall", stress_waterfall.totalShortfall)],
            ))

        max_ltv = unsold_ltv_matrix.summary.get("maxLtv", 0.0)
        if max_ltv > 0.90:
            risk_flags.append(ScenarioRiskFlag(
                code="UNSOLD_LTV_BREACH",
                severity="high",
                message=f"Unsold LTV reaches {max_ltv * 100:.1f}% and breaches the 90% threshold.",
                sourceLocators=_unsold_ltv_locators(unsold_ltv_matrix, min_ltv=0.90),
            ))
        elif max_ltv > 0.80:
            risk_flags.append(ScenarioRiskFlag(
                code="UNSOLD_LTV_HIGH",
                severity="caution",
                message=f"Unsold LTV reaches {max_ltv * 100:.1f}% and requires monitoring.",
                sourceLocators=_unsold_ltv_locators(unsold_ltv_matrix, min_ltv=0.80),
            ))

        if unsold_ltv_matrix.summary.get("breachCount", 0) > 5:
            risk_flags.append(ScenarioRiskFlag(
                code="TRIGGER_REQUIRED",
                severity="high",
                message="Multiple scenario cells breach LTV or repayment thresholds, so discount-sale and cash sweep triggers are required.",
                sourceLocators=[{
                    "locatorId": "scenariosEvaluation.unsoldLtvMatrix.summary.breachCount",
                    "sourceType": "scenario_summary_field",
                    "source": "scenariosEvaluation.unsoldLtvMatrix.summary",
                    "path": "unsoldLtvMatrix.summary.breachCount",
                    "label": "Unsold LTV breach count",
                    "value": unsold_ltv_matrix.summary.get("breachCount", 0),
                }],
            ))
        return risk_flags
    @staticmethod
    def _build_report_fragments(avg_unit_price: float, base_ltv: float, max_ltv: float) -> ScenarioReportFragments:
        return ScenarioReportFragments(
            waterfallSummary=(
                "Waterfall simulation checks whether sale proceeds can repay total PF principal, interest, and fees at the base 70% sale-rate scenario. "
                "If sale rate falls to 50% or discount sale occurs, mezzanine and junior repayment can weaken, so repayment-reserve allocation and cash-sweep covenants should be reviewed."
            ),
            unsoldLtvSummary=(
                f"Unsold recovery analysis uses an average unit price of KRW {avg_unit_price:,.0f}. Under the base 10% discount assumption, unsold LTV is estimated at {base_ltv * 100:.1f}%. "
                f"In stress conditions, LTV can rise to {max_ltv * 100:.1f}%, requiring lender-side collateral and recovery monitoring."
            ),
            trancheRecoverySummary=(
                "Tranche recovery analysis indicates senior exposure is relatively more resilient, while mezzanine and junior recovery can decline under slower sales or higher discounts. "
                "Junior exposure, equity cushion, construction-cost retention, and discount-sale triggers should be reviewed together."
            ),
            approvalCondition=(
                "Recommended approval conditions: 1) strengthen repayment-reserve allocation and restrict unsupervised fund execution while cumulative sale rate remains below 60%; "
                "2) if unsold inventory exceeds 20% within three months after completion, require lender consultation for discount sale of at least 10% or bulk sale trigger execution."
            ),
        )


def _waterfall_locator(scenario_id: str, field: str, value: Any) -> Dict[str, Any]:
    return {
        "locatorId": f"scenariosEvaluation.waterfallScenarios[{scenario_id}].{field}",
        "sourceType": "scenario_waterfall_field",
        "source": "scenariosEvaluation.waterfallScenarios",
        "path": f"waterfallScenarios[scenarioId={scenario_id}].{field}",
        "label": f"Waterfall {field}",
        "scenarioId": scenario_id,
        "value": value,
    }


def _waterfall_row_locator(scenario_id: str, row: Any) -> Dict[str, Any]:
    tranche = getattr(row, "tranche", None) or "unknown"
    priority = getattr(row, "priority", None)
    return {
        "locatorId": f"scenariosEvaluation.waterfallScenarios[{scenario_id}].rows[{tranche}].shortfall",
        "sourceType": "scenario_waterfall_row",
        "source": "scenariosEvaluation.waterfallScenarios",
        "path": f"waterfallScenarios[scenarioId={scenario_id}].rows[tranche={tranche}]",
        "label": f"{tranche} waterfall row shortfall",
        "scenarioId": scenario_id,
        "tranche": tranche,
        "priority": priority,
        "shortfall": getattr(row, "shortfall", None),
        "recoveryRatio": getattr(row, "recoveryRatio", None),
    }


def _unsold_ltv_locators(matrix: UnsoldLtvMatrix, min_ltv: float) -> List[Dict[str, Any]]:
    locators: List[Dict[str, Any]] = []
    rows = sorted(
        [row for row in matrix.rows if row.unsoldLtv >= min_ltv],
        key=lambda row: row.unsoldLtv,
        reverse=True,
    )
    for index, row in enumerate(rows[:5], 1):
        locators.append({
            "locatorId": f"scenariosEvaluation.unsoldLtvMatrix.rows[{row.saleRate:.2f}:{row.discountRate:.2f}].unsoldLtv",
            "sourceType": "scenario_matrix_cell",
            "source": "scenariosEvaluation.unsoldLtvMatrix",
            "path": f"unsoldLtvMatrix.rows[saleRate={row.saleRate},discountRate={row.discountRate}].unsoldLtv",
            "label": f"Unsold LTV matrix cell {index}",
            "saleRate": row.saleRate,
            "discountRate": row.discountRate,
            "unsoldUnits": row.unsoldUnits,
            "unsoldLtv": row.unsoldLtv,
            "riskLevel": row.riskLevel,
        })
    if not locators:
        locators.append({
            "locatorId": "scenariosEvaluation.unsoldLtvMatrix.summary.maxLtv",
            "sourceType": "scenario_summary_field",
            "source": "scenariosEvaluation.unsoldLtvMatrix.summary",
            "path": "unsoldLtvMatrix.summary.maxLtv",
            "label": "Unsold LTV max value",
            "value": matrix.summary.get("maxLtv"),
        })
    return locators
