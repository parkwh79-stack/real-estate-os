from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional

from schemas.credit_policy import CreditPolicyProfile, resolve_credit_policy


CREDIT_REVIEW_SCHEMA_VERSION = "realytics-credit-review-v1"


class CreditReviewAgent:
    """Produce lender-side credit decision guidance from the consolidated risk register."""

    def decide(
        self,
        project_data: Dict[str, Any],
        artifacts: Dict[str, Any],
        policy: Optional[CreditPolicyProfile] = None,
    ) -> Dict[str, Any]:
        input_issues: List[str] = []

        def finite_artifact(value: Any, path: str) -> Any:
            if isinstance(value, float) and not math.isfinite(value):
                input_issues.append(f"Invalid non-finite financial evidence: {path}.")
                return None
            if isinstance(value, dict):
                return {key: finite_artifact(item, f"{path}.{key}") for key, item in value.items()}
            if isinstance(value, list):
                return [finite_artifact(item, f"{path}[{index}]") for index, item in enumerate(value)]
            return value

        # Work on a copy: invalid upstream numbers remain unresolved, never zero.
        artifacts = finite_artifact(artifacts, "artifacts")
        for key in ("riskRegister", "legalPermitReview", "dataQualityReview", "reconciliationGate"):
            if not isinstance(artifacts.get(key), dict) or not artifacts[key].get("status"):
                input_issues.append(f"Required credit review gate is missing: {key}.")
        policy = policy or resolve_credit_policy(project_data)
        risk_register = artifacts.get("riskRegister") or {}
        risk_summary = risk_register.get("summary") or {}
        credit_inputs = risk_register.get("creditReviewInputs") or {}
        top_risks = risk_register.get("topRisks") or []
        legal = artifacts.get("legalPermitReview") or {}
        data_quality = artifacts.get("dataQualityReview") or {}
        reconciliation = artifacts.get("reconciliationGate") or {}
        marketability = artifacts.get("marketabilityAnalysis") or {}
        pf_structure = artifacts.get("pfStructure") or {}
        return_metrics = artifacts.get("returnMetrics") or {}
        stress_test = artifacts.get("stressTest") or {}
        credit_enhancement = artifacts.get("creditEnhancement") or {}
        signals = self._return_stress_signals(return_metrics, stress_test)
        signals["hurdle"] = policy.hurdleRate

        decision = self._decision(
            risk_register=risk_register,
            legal=legal,
            data_quality=data_quality,
            reconciliation=reconciliation,
            marketability=marketability,
            pf_structure=pf_structure,
            signals=signals,
            policy=policy,
            credit_enhancement=credit_enhancement,
        )
        if input_issues and decision != "reject_recommended":
            decision = "rework_required"
        approval_conditions = self._approval_conditions(risk_register, legal, data_quality, reconciliation)
        approval_conditions = input_issues + approval_conditions
        if signals["seniorBreakKeys"]:
            approval_conditions.append(
                "스트레스 시 Senior 원금 손실 가능 — 추가 신용보강·담보 확보 또는 PF 축소·Equity 확충 필요."
            )
        adjusted_senior_ltv = credit_enhancement.get("adjustedSeniorLtv")
        if (
            policy.maxAdjustedSeniorLtv is not None
            and isinstance(adjusted_senior_ltv, (int, float))
            and adjusted_senior_ltv > policy.maxAdjustedSeniorLtv
        ):
            approval_conditions.append(
                f"보강 후 선순위 LTV {adjusted_senior_ltv:.1%}가 {policy.lenderName} 한도 {policy.maxAdjustedSeniorLtv:.0%}를 초과 — 추가 신용보강·담보 또는 PF 축소 필요."
            )
        approval_conditions = self._dedupe(approval_conditions)[:13]
        key_risks = self._key_risks(top_risks)
        mitigants = self._mitigants(risk_register, project_data)
        required_follow_ups = input_issues + self._required_follow_ups(decision, risk_register, legal, data_quality, reconciliation)
        return {
            "schemaVersion": CREDIT_REVIEW_SCHEMA_VERSION,
            "analysisType": "credit_review_decision",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "agentName": "Credit Review Agent",
            "decision": decision,
            "decisionLabel": self._decision_label(decision),
            "status": self._status(decision),
            "policyProfile": {
                "profileId": policy.profileId,
                "lenderName": policy.lenderName,
                "hurdleRate": policy.hurdleRate,
                "exitSaleRateReworkThreshold": policy.exitSaleRateReworkThreshold,
                "criticalRiskCountReworkThreshold": policy.criticalRiskCountReworkThreshold,
                "marketabilityReworkGrades": policy.marketabilityReworkGrades,
                "maxAdjustedSeniorLtv": policy.maxAdjustedSeniorLtv,
                "requireCommitteeForConditional": policy.requireCommitteeForConditional,
            },
            "confidence": self._confidence(risk_register, data_quality, marketability),
            "summary": {
                "projectName": (project_data.get("project") or {}).get("name"),
                "riskRegisterStatus": risk_register.get("status"),
                "riskCount": risk_summary.get("riskCount", 0),
                "criticalRiskCount": risk_summary.get("criticalCount", 0),
                "highRiskCount": risk_summary.get("highCount", 0),
                "humanReviewRequiredCount": risk_summary.get("humanReviewRequiredCount", 0),
                "legalStatus": legal.get("status"),
                "dataQualityStatus": data_quality.get("status"),
                "reconciliationStatus": reconciliation.get("status"),
                "marketabilityGrade": marketability.get("grade"),
                "marketabilityScore": marketability.get("score"),
                "ltv": pf_structure.get("ltv"),
                "ltc": pf_structure.get("ltc"),
                "exitSaleRate": pf_structure.get("exit_sale_rate"),
                "projectIrrBase": signals["baseIrr"],
                "equityMultiple": signals["equityMultiple"],
                "paybackMonths": signals["paybackMonths"],
                "icr": signals["icr"],
                "stressWorstCaseKey": signals["worstCaseKey"],
                "stressSeniorBreakScenarios": sorted(signals["seniorBreakKeys"]),
            },
            "decisionReasons": input_issues + self._decision_reasons(
                decision,
                risk_register,
                legal,
                data_quality,
                reconciliation,
                marketability,
                pf_structure,
                signals=signals,
            ),
            "keyRisks": key_risks,
            "mitigants": mitigants,
            "approvalConditions": approval_conditions,
            "requiredFollowUps": required_follow_ups,
            "requiredWaivers": self._required_waivers(risk_register, data_quality, policy),
            "creditReviewInputs": credit_inputs,
            "reportParagraphs": self._report_paragraphs(decision, key_risks, approval_conditions, mitigants),
            "humanReview": {
                "required": decision != "approve",
                "reason": " ".join(input_issues) if input_issues else self._human_review_reason(decision, risk_summary, legal, data_quality),
                "suggestedReviewerRole": "credit_officer",
            },
            "dataLimitations": input_issues + self._data_limitations(risk_register, data_quality, marketability),
        }

    def _return_stress_signals(self, return_metrics: Dict[str, Any], stress_test: Dict[str, Any]) -> Dict[str, Any]:
        scenarios = stress_test.get("scenarios") or []
        base_irr = next((s.get("projectIrr") for s in scenarios if s.get("key") == "base"), None)
        senior_break_keys = {
            s.get("key") for s in scenarios if s.get("seniorStatus") == "senior_principal_loss_possible"
        }
        senior_break_keys.discard(None)
        metrics = return_metrics.get("metrics") or {}

        def _val(key):
            entry = metrics.get(key) or {}
            return entry.get("value") if isinstance(entry, dict) else None

        return {
            "baseIrr": base_irr,
            "hurdle": return_metrics.get("hurdleRate") or 0.12,
            "seniorBreakKeys": senior_break_keys,
            "worstCaseKey": stress_test.get("worstCaseKey"),
            "icr": _val("icr"),
            "equityMultiple": _val("equityMultiple"),
            "paybackMonths": (metrics.get("paybackPeriodMonths") or {}).get("value") if isinstance(metrics.get("paybackPeriodMonths"), dict) else None,
        }

    def _decision(
        self,
        *,
        risk_register: Dict[str, Any],
        legal: Dict[str, Any],
        data_quality: Dict[str, Any],
        reconciliation: Dict[str, Any],
        marketability: Dict[str, Any],
        pf_structure: Dict[str, Any],
        signals: Dict[str, Any] | None = None,
        policy: CreditPolicyProfile | None = None,
        credit_enhancement: Dict[str, Any] | None = None,
    ) -> str:
        signals = signals or {}
        policy = policy or resolve_credit_policy({})
        credit_enhancement = credit_enhancement or {}
        risks = risk_register.get("risks") or []
        critical_count = sum(1 for risk in risks if risk.get("severity") == "critical")
        high_count = sum(1 for risk in risks if risk.get("severity") == "high")
        critical_codes = {risk.get("code") for risk in risks if risk.get("severity") == "critical"}

        base_irr = signals.get("baseIrr")
        hurdle = signals.get("hurdle") or policy.hurdleRate
        senior_break_keys = signals.get("seniorBreakKeys") or set()
        rework_grades = set(policy.marketabilityReworkGrades)

        if legal.get("status") == "blocking_issue" or data_quality.get("status") == "blocking" or reconciliation.get("status") == "blocking":
            return "rework_required"
        # Stress reveals senior principal loss already in the base case, or the
        # base-case project return is negative -> escalate hardest.
        if "base" in senior_break_keys:
            return "reject_recommended"
        if critical_count >= policy.criticalRiskCountReworkThreshold:
            return "rework_required"
        if {"SENIOR_SHORTFALL", "STRESS_SENIOR_SHORTFALL"}.intersection(critical_codes):
            return "reject_recommended"
        if isinstance(base_irr, (int, float)) and base_irr < 0:
            return "rework_required"
        if "mild" in senior_break_keys:
            return "rework_required"
        if pf_structure.get("exit_sale_rate") and pf_structure.get("exit_sale_rate") >= policy.exitSaleRateReworkThreshold:
            return "rework_required"
        if marketability.get("grade") in rework_grades:
            return "rework_required"
        adjusted_senior_ltv = credit_enhancement.get("adjustedSeniorLtv")
        if (
            policy.maxAdjustedSeniorLtv is not None
            and isinstance(adjusted_senior_ltv, (int, float))
            and adjusted_senior_ltv > policy.maxAdjustedSeniorLtv
        ):
            return "conditional_approval"
        if risk_register.get("status") in {"blocking", "high_risk"} or high_count:
            return "conditional_approval"
        if data_quality.get("status") == "needs_review" or legal.get("status") == "caution":
            return "conditional_approval"
        # Tail/severe stress breaks senior, or base IRR is below hurdle -> conditions.
        if senior_break_keys:
            return "conditional_approval"
        if isinstance(base_irr, (int, float)) and base_irr < hurdle:
            return "conditional_approval"
        return "approve"

    def _approval_conditions(
        self,
        risk_register: Dict[str, Any],
        legal: Dict[str, Any],
        data_quality: Dict[str, Any],
        reconciliation: Dict[str, Any],
    ) -> List[str]:
        conditions = list(risk_register.get("approvalConditions") or [])
        if legal.get("status") in {"blocking_issue", "caution"}:
            conditions.extend(str(item) for item in legal.get("requiredFollowUps", [])[:5])
        if data_quality.get("status") in {"blocking", "needs_review"}:
            conditions.append("Resolve or explicitly approve Data Quality Agent conflicts before final credit approval.")
        if reconciliation.get("status") in {"blocking", "needs_review"}:
            conditions.append("Resolve cross-agent reconciliation issues or record an approved conservative assumption.")
        return self._dedupe(conditions)[:12]

    def _key_risks(self, top_risks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "riskId": risk.get("riskId"),
                "category": risk.get("category"),
                "code": risk.get("code"),
                "title": risk.get("title"),
                "severity": risk.get("severity"),
                "score": risk.get("score"),
                "mitigant": risk.get("mitigant"),
                "sourceAgent": risk.get("sourceAgent"),
            }
            for risk in top_risks[:8]
        ]

    def _mitigants(self, risk_register: Dict[str, Any], project_data: Dict[str, Any]) -> List[str]:
        mitigants = [
            risk.get("mitigant")
            for risk in risk_register.get("topRisks", [])
            if isinstance(risk, dict) and risk.get("mitigant")
        ]
        covenants = project_data.get("covenants") or {}
        if covenants.get("discount_triggers"):
            mitigants.append("Discount-sale trigger is reflected in covenant package.")
        if covenants.get("claim_preservation"):
            mitigants.append("Claim preservation package is reflected in supplied covenant data.")
        return self._dedupe([str(item) for item in mitigants])[:10]

    def _required_follow_ups(
        self,
        decision: str,
        risk_register: Dict[str, Any],
        legal: Dict[str, Any],
        data_quality: Dict[str, Any],
        reconciliation: Dict[str, Any],
    ) -> List[str]:
        follow_ups = []
        if decision in {"rework_required", "reject_recommended"}:
            follow_ups.append("Do not proceed to final approval until must-resolve issues are cleared or formally waived.")
        for risk in risk_register.get("risks", []):
            if risk.get("severity") == "critical":
                follow_ups.append(f"{risk.get('riskId')} {risk.get('title')}: {risk.get('mitigant')}")
        if legal.get("status") == "blocking_issue":
            follow_ups.append("Legal blocking issues require counsel or permit evidence before approval.")
        if data_quality.get("status") == "blocking":
            follow_ups.append("Data quality blocking issues require authoritative source selection.")
        if reconciliation.get("status") == "blocking":
            follow_ups.append("Cross-agent reconciliation gate is blocking and must be resolved.")
        return self._dedupe(follow_ups)[:12]

    def _required_waivers(
        self,
        risk_register: Dict[str, Any],
        data_quality: Dict[str, Any],
        policy: CreditPolicyProfile,
    ) -> List[Dict[str, Any]]:
        approver_role = policy.waiverApproverRole
        waivers = []
        for risk in risk_register.get("risks", []):
            if risk.get("severity") in {"critical", "high"} and risk.get("status") != "resolved":
                waivers.append({
                    "riskId": risk.get("riskId"),
                    "code": risk.get("code"),
                    "reason": "Required if approval proceeds before this risk is resolved.",
                    "approverRole": approver_role,
                })
        if data_quality.get("status") == "blocking":
            waivers.append({
                "riskId": "DQ-BLOCKING",
                "code": "DATA_QUALITY_BLOCKING",
                "reason": "Required if final approval relies on unresolved conflicting figures.",
                "approverRole": approver_role,
            })
        return waivers[:12]

    def _decision_reasons(
        self,
        decision: str,
        risk_register: Dict[str, Any],
        legal: Dict[str, Any],
        data_quality: Dict[str, Any],
        reconciliation: Dict[str, Any],
        marketability: Dict[str, Any],
        pf_structure: Dict[str, Any],
        signals: Dict[str, Any] | None = None,
    ) -> List[str]:
        signals = signals or {}
        reasons = [f"Credit decision is {self._decision_label(decision)}."]
        if risk_register.get("status"):
            reasons.append(f"Risk Register status is {risk_register.get('status')}.")
        if legal.get("status") and legal.get("status") != "clear":
            reasons.append(f"Legal Review status is {legal.get('status')}.")
        if data_quality.get("status") and data_quality.get("status") != "clear":
            reasons.append(f"Data Quality status is {data_quality.get('status')}.")
        if reconciliation.get("status") and reconciliation.get("status") != "clear":
            reasons.append(f"Cross-agent reconciliation status is {reconciliation.get('status')}.")
        if marketability.get("grade"):
            reasons.append(f"Marketability grade is {marketability.get('grade')} with score {marketability.get('score')}.")
        if isinstance(pf_structure.get("exit_sale_rate"), (int, float)):
            reasons.append(f"PF Exit sale rate is {pf_structure.get('exit_sale_rate'):.1%}.")
        base_irr = signals.get("baseIrr")
        if isinstance(base_irr, (int, float)):
            reasons.append(f"Stress base-case Project IRR is {base_irr:.1%} (hurdle {float(signals.get('hurdle') or 0.12):.0%}).")
        if signals.get("seniorBreakKeys"):
            reasons.append(
                "Senior principal loss is possible under stress scenario(s): "
                + ", ".join(sorted(signals["seniorBreakKeys"])) + "."
            )
        if isinstance(signals.get("icr"), (int, float)) and signals.get("icr"):
            reasons.append(f"Interest coverage (ICR) is {signals['icr']:.2f}.")
        return reasons

    def _report_paragraphs(
        self,
        decision: str,
        key_risks: List[Dict[str, Any]],
        approval_conditions: List[str],
        mitigants: List[str],
    ) -> Dict[str, str]:
        risk_text = ", ".join(risk.get("title") or risk.get("code") or "" for risk in key_risks[:3]) or "주요 리스크 없음"
        condition_text = " / ".join(approval_conditions[:3]) or "표준 약정조건 유지"
        mitigant_text = " / ".join(mitigants[:3]) or "추가 보완 필요"
        return {
            "creditOpinion": (
                f"본건은 현재 Agent 검토 결과 {self._decision_label(decision)} 의견입니다. "
                f"주요 검토 리스크는 {risk_text}입니다."
            ),
            "approvalCondition": f"승인조건으로는 {condition_text}을 우선 반영할 필요가 있습니다.",
            "mitigantSummary": f"주요 완화요인은 {mitigant_text}입니다.",
        }

    def _confidence(
        self,
        risk_register: Dict[str, Any],
        data_quality: Dict[str, Any],
        marketability: Dict[str, Any],
    ) -> float:
        score = 0.75
        if risk_register.get("dataLimitations"):
            score -= 0.1
        if data_quality.get("status") == "blocking":
            score -= 0.2
        elif data_quality.get("status") == "needs_review":
            score -= 0.1
        if marketability.get("dataStatus") in {"partial", "missing_required_data", "insufficient"}:
            score -= 0.1
        return max(0.1, min(0.95, round(score, 2)))

    def _human_review_reason(self, decision: str, risk_summary: Dict[str, Any], legal: Dict[str, Any], data_quality: Dict[str, Any]) -> str:
        if decision == "approve":
            return "No material blocking issue was detected by Credit Review Agent."
        if legal.get("status") == "blocking_issue":
            return "Legal blocking issue exists."
        if data_quality.get("status") == "blocking":
            return "Data quality blocking issue exists."
        return f"{risk_summary.get('humanReviewRequiredCount', 0)} risk register row(s) require review."

    def _data_limitations(
        self,
        risk_register: Dict[str, Any],
        data_quality: Dict[str, Any],
        marketability: Dict[str, Any],
    ) -> List[str]:
        limitations = []
        limitations.extend(str(item) for item in risk_register.get("dataLimitations", []))
        limitations.extend(str(item) for item in data_quality.get("dataLimitations", []))
        limitations.extend(str(item) for item in marketability.get("missingRequiredData", []))
        return self._dedupe(limitations)[:12]

    def _status(self, decision: str) -> str:
        if decision == "approve":
            return "clear"
        if decision == "conditional_approval":
            return "conditional"
        if decision == "rework_required":
            return "requires_rework"
        return "reject_recommended"

    def _decision_label(self, decision: str) -> str:
        return {
            "approve": "승인 가능",
            "conditional_approval": "조건부 승인 가능",
            "rework_required": "보완 후 재심",
            "reject_recommended": "부결 권고",
        }.get(decision, decision)

    def _dedupe(self, values: List[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result
