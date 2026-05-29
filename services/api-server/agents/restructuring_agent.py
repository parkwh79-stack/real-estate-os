from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from agents.scenario_engine_agent import ScenarioEngineAgent
from schemas.scenario import ScenarioSetRequest, ScenarioSetResult, TrancheItem


RESTRUCTURING_AGENT_VERSION = "realytics-restructuring-agent-v1"
DEFAULT_MIN_SENIOR_BUFFER_KRW = 1_000_000_000
DEFAULT_UNSOLD_LTV_TARGET = 0.90


class RestructuringAgent:
    """Recommend deterministic fixes for rejection-driving PF scenario risks."""

    def build_restructuring_plan(
        self,
        request: ScenarioSetRequest,
        scenario_result: ScenarioSetResult | None = None,
        options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        options = options or {}
        scenario_result = scenario_result or ScenarioEngineAgent().run_scenario_set(request)
        senior_buffer = float(options.get("seniorBuffer") or options.get("senior_buffer") or DEFAULT_MIN_SENIOR_BUFFER_KRW)
        ltv_target = float(options.get("unsoldLtvTarget") or options.get("unsold_ltv_target") or DEFAULT_UNSOLD_LTV_TARGET)

        senior_fix = self._senior_shortfall_fix(request, scenario_result, senior_buffer)
        unsold_ltv_fix = self._unsold_ltv_fix(scenario_result, ltv_target)
        adjusted_result = None
        adjusted_request_summary = None
        if senior_fix.get("requiredSeniorReduction") and senior_fix["requiredSeniorReduction"] > 0:
            adjusted_request = self._apply_senior_reduction(request, senior_fix["requiredSeniorReduction"])
            adjusted_result = ScenarioEngineAgent().run_scenario_set(adjusted_request)
            adjusted_request_summary = self._request_summary(adjusted_request)

        before = self._scenario_health(scenario_result)
        after = self._scenario_health(adjusted_result) if adjusted_result else None
        recommendations = self._recommendations(senior_fix, unsold_ltv_fix)

        return {
            "schemaVersion": RESTRUCTURING_AGENT_VERSION,
            "analysisType": "pf_restructuring_plan",
            "status": "completed",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "decisionObjective": "Remove rejection-driving Senior shortfall and reduce unsold LTV breach before Credit Review approval.",
            "baseScenarioHealth": before,
            "adjustedScenarioHealth": after,
            "adjustedRequestSummary": adjusted_request_summary,
            "seniorShortfallFix": senior_fix,
            "unsoldLtvFix": unsold_ltv_fix,
            "recommendations": recommendations,
            "approvalPath": self._approval_path(after or before, recommendations),
            "sourceMetadata": {
                "sourceName": "Calculation Scenario Engine",
                "sourceType": "agent_artifact",
                "sourceUrlOrEvidenceId": scenario_result.scenarioSetId,
                "baseDate": request.baseDate,
                "collectedAt": datetime.now(timezone.utc).isoformat(),
                "confidence": 0.95,
            },
        }

    def _senior_shortfall_fix(
        self,
        request: ScenarioSetRequest,
        scenario_result: ScenarioSetResult,
        senior_buffer: float,
    ) -> Dict[str, Any]:
        base_waterfall = next(
            (item for item in scenario_result.waterfallScenarios if item.scenarioId == "sale_70_disc_0"),
            None,
        )
        if not base_waterfall:
            return {
                "status": "not_applicable",
                "message": "Base waterfall scenario sale_70_disc_0 was not generated.",
            }
        senior_row = next((row for row in base_waterfall.rows if row.priority == 1), None)
        senior_tranche = self._senior_tranche(request)
        if not senior_row or not senior_tranche:
            return {
                "status": "not_applicable",
                "message": "Senior tranche row is missing.",
            }
        shortfall = max(0.0, senior_row.shortfall)
        claim_factor = 1.0 + senior_tranche.interestRate + senior_tranche.feeRate
        required_reduction = 0.0 if shortfall <= 0 else min(senior_tranche.amount, (shortfall + senior_buffer) / claim_factor)
        direct_reserve = 0.0 if shortfall <= 0 else shortfall + senior_buffer
        return {
            "status": "clear" if shortfall <= 0 else "action_required",
            "scenarioId": base_waterfall.scenarioId,
            "seniorTranche": senior_tranche.tranche,
            "seniorAmount": senior_tranche.amount,
            "seniorClaim": senior_row.totalClaim,
            "cashAvailable": base_waterfall.cashAvailable,
            "shortfall": shortfall,
            "buffer": senior_buffer if shortfall > 0 else 0.0,
            "claimReductionFactor": claim_factor,
            "requiredSeniorReduction": round(required_reduction, 0),
            "requiredDsraOrEquityInjection": round(direct_reserve, 0),
            "suggestedActions": [
                "Reduce senior tranche exposure by the required amount and replace it with equity, junior capital, or sponsor cash support.",
                "Alternatively deposit DSRA/repayment reserve equal to the shortfall plus buffer before approval.",
                "Re-run waterfall after any tranche resizing; do not approve solely on waiver if senior shortfall remains.",
            ] if shortfall > 0 else ["No senior shortfall action required."],
            "sourceLocators": [
                {
                    "locatorId": f"scenariosEvaluation.waterfallScenarios[{base_waterfall.scenarioId}].rows[{senior_row.tranche}].shortfall",
                    "sourceType": "scenario_waterfall_row",
                    "source": "scenariosEvaluation.waterfallScenarios",
                    "path": f"waterfallScenarios[scenarioId={base_waterfall.scenarioId}].rows[tranche={senior_row.tranche}]",
                    "label": "Senior shortfall",
                    "value": shortfall,
                }
            ],
        }

    def _unsold_ltv_fix(self, scenario_result: ScenarioSetResult, ltv_target: float) -> Dict[str, Any]:
        rows = scenario_result.unsoldLtvMatrix.rows
        breach_rows = [row for row in rows if row.unsoldLtv > ltv_target and row.discountedUnsoldValue > 0]
        if not breach_rows:
            return {
                "status": "clear",
                "targetLtv": ltv_target,
                "maxLtv": scenario_result.unsoldLtvMatrix.summary.get("maxLtv", 0.0),
                "suggestedActions": ["No unsold LTV breach action required."],
            }
        worst = max(breach_rows, key=lambda row: row.unsoldLtv)
        pf_amount = scenario_result.unsoldLtvMatrix.pfAmount
        supported_pf = worst.discountedUnsoldValue * ltv_target
        required_pf_reduction = max(0.0, pf_amount - supported_pf)
        required_additional_collateral = max(0.0, (pf_amount / ltv_target) - worst.discountedUnsoldValue)
        return {
            "status": "action_required",
            "targetLtv": ltv_target,
            "maxLtv": scenario_result.unsoldLtvMatrix.summary.get("maxLtv", 0.0),
            "worstCell": {
                "saleRate": worst.saleRate,
                "discountRate": worst.discountRate,
                "unsoldUnits": worst.unsoldUnits,
                "discountedUnsoldValue": worst.discountedUnsoldValue,
                "unsoldLtv": worst.unsoldLtv,
            },
            "pfAmount": pf_amount,
            "targetSupportedPfAmount": round(supported_pf, 0),
            "requiredPfReductionOrEquityIncrease": round(required_pf_reduction, 0),
            "requiredAdditionalCollateralValue": round(required_additional_collateral, 0),
            "suggestedActions": [
                "Reduce total PF exposure or increase equity until PF balance is within target LTV against discounted unsold value.",
                "Attach additional collateral, forward-sale commitment, bulk-sale agreement, or refinancing evidence if PF exposure is maintained.",
                "Keep discount-sale trigger and cash sweep covenant until max unsold LTV is below threshold.",
            ],
            "sourceLocators": [
                {
                    "locatorId": f"scenariosEvaluation.unsoldLtvMatrix.rows[sale={worst.saleRate},discount={worst.discountRate}]",
                    "sourceType": "scenario_unsold_ltv_cell",
                    "source": "scenariosEvaluation.unsoldLtvMatrix.rows",
                    "path": f"unsoldLtvMatrix.rows[saleRate={worst.saleRate}][discountRate={worst.discountRate}]",
                    "label": "Worst unsold LTV cell",
                    "value": worst.unsoldLtv,
                }
            ],
        }

    def _apply_senior_reduction(self, request: ScenarioSetRequest, reduction: float) -> ScenarioSetRequest:
        payload = request.model_dump()
        senior_priority = min(item["priority"] for item in payload["pfStructure"])
        for tranche in payload["pfStructure"]:
            if tranche["priority"] == senior_priority:
                tranche["amount"] = max(0.0, tranche["amount"] - reduction)
                tranche["tranche"] = f"{tranche['tranche']} - resized"
                break
        payload["scenarioSetId"] = f"{request.scenarioSetId or 'scenario-set'}-senior-resized"
        payload["sourceMetadata"] = {
            "sourceName": "Restructuring Agent senior reduction simulation",
            "sourceType": "agent_estimate",
            "sourceUrlOrEvidenceId": request.scenarioSetId,
            "baseDate": request.baseDate,
            "confidence": 0.9,
        }
        return ScenarioSetRequest.model_validate(payload)

    def _scenario_health(self, scenario_result: ScenarioSetResult | None) -> Dict[str, Any] | None:
        if scenario_result is None:
            return None
        risk_codes = [flag.code for flag in scenario_result.riskFlags]
        base_waterfall = next(
            (item for item in scenario_result.waterfallScenarios if item.scenarioId == "sale_70_disc_0"),
            None,
        )
        senior_shortfall = 0.0
        if base_waterfall:
            senior_row = next((row for row in base_waterfall.rows if row.priority == 1), None)
            senior_shortfall = senior_row.shortfall if senior_row else 0.0
        return {
            "scenarioSetId": scenario_result.scenarioSetId,
            "riskCodes": risk_codes,
            "seniorShortfall": senior_shortfall,
            "maxUnsoldLtv": scenario_result.unsoldLtvMatrix.summary.get("maxLtv", 0.0),
            "breachCount": scenario_result.unsoldLtvMatrix.summary.get("breachCount", 0),
        }

    def _recommendations(
        self,
        senior_fix: Dict[str, Any],
        unsold_ltv_fix: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        recommendations = []
        if senior_fix.get("status") == "action_required":
            recommendations.extend([
                {
                    "rank": 1,
                    "code": "REDUCE_SENIOR_OR_ADD_EQUITY",
                    "title": "Reduce senior tranche or add equity/DSRA",
                    "amount": senior_fix.get("requiredSeniorReduction"),
                    "alternativeAmount": senior_fix.get("requiredDsraOrEquityInjection"),
                    "effect": "Removes base-case SENIOR_SHORTFALL if rerun confirms full senior repayment.",
                    "conditionDraft": "Prior to approval, reduce senior tranche exposure or deposit repayment reserve by the required amount plus buffer.",
                },
            ])
        if unsold_ltv_fix.get("status") == "action_required":
            recommendations.append({
                "rank": 2,
                "code": "REDUCE_TOTAL_PF_OR_ADD_COLLATERAL",
                "title": "Reduce total PF exposure or add collateral to cure Unsold LTV breach",
                "amount": unsold_ltv_fix.get("requiredPfReductionOrEquityIncrease"),
                "alternativeAmount": unsold_ltv_fix.get("requiredAdditionalCollateralValue"),
                "effect": "Targets unsold LTV at or below the configured threshold.",
                "conditionDraft": "Maintain PF exposure only if additional collateral, forward-sale, bulk-sale, or refinancing evidence supports target unsold LTV.",
            })
        if not recommendations:
            recommendations.append({
                "rank": 1,
                "code": "NO_RESTRUCTURING_REQUIRED",
                "title": "No rejection-driving scenario risk detected",
                "amount": 0,
                "effect": "Scenario Engine did not detect senior shortfall or unsold LTV breach.",
            })
        return recommendations

    def _approval_path(self, health: Dict[str, Any], recommendations: List[Dict[str, Any]]) -> Dict[str, Any]:
        risk_codes = set(health.get("riskCodes", []))
        blockers = []
        if "SENIOR_SHORTFALL" in risk_codes:
            blockers.append("SENIOR_SHORTFALL")
        if "UNSOLD_LTV_BREACH" in risk_codes:
            blockers.append("UNSOLD_LTV_BREACH")
        if "STRESS_SENIOR_SHORTFALL" in risk_codes:
            blockers.append("STRESS_SENIOR_SHORTFALL")
        return {
            "currentStatus": "rework_required" if blockers else "conditional_approval_candidate",
            "remainingBlockers": blockers,
            "nextSteps": [
                "Apply recommended restructuring inputs.",
                "Re-run Scenario Engine and PF Review orchestration.",
                "Review or waive remaining non-critical Risk Register rows.",
                "Attach evidence for any market, land, collateral, or sponsor-support assumption.",
            ],
            "conditionDrafts": [
                item.get("conditionDraft")
                for item in recommendations
                if item.get("conditionDraft")
            ],
        }

    def _senior_tranche(self, request: ScenarioSetRequest) -> TrancheItem | None:
        if not request.pfStructure:
            return None
        return sorted(request.pfStructure, key=lambda item: item.priority)[0]

    def _request_summary(self, request: ScenarioSetRequest) -> Dict[str, Any]:
        return {
            "scenarioSetId": request.scenarioSetId,
            "totalPfAmount": sum(item.amount for item in request.pfStructure),
            "pfStructure": [
                {
                    "tranche": item.tranche,
                    "amount": item.amount,
                    "priority": item.priority,
                    "interestRate": item.interestRate,
                    "feeRate": item.feeRate,
                }
                for item in request.pfStructure
            ],
        }
