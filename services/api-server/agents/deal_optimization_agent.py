from __future__ import annotations

"""Deal optimization — turn 'these gates fail' into 'here is how to make it work'.

When a deal does not clear the credit gates, simply listing the breaches is not
enough. This agent computes the borrower-side levers that would close each gap —
공사비 절감, 분양가 상향, 자기자본 확충 / PF 축소, 추가 신용보강·담보 — with target
magnitudes, then judges whether a feasible negotiated plan exists or the deal is
not recommendable (여신 취급 곤란). Deterministic math against policy thresholds;
the LLM only narrates.
"""

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional

DEAL_OPTIMIZATION_SCHEMA_VERSION = "realytics-deal-optimization-v1"

# Plausibility bounds for borrower-side negotiation (beyond these the lever is
# considered impractical, pushing the deal toward "not recommended").
TARGET_MARGIN = 0.10          # developer profit margin floor for viability
MAX_CONSTRUCTION_CUT = 0.10   # ~10% construction-cost reduction is negotiable
MAX_PRICE_UP = 0.07           # ~7% sale-price increase is market-plausible
DEFAULT_MAX_SENIOR_LTV = 0.65


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _eok(v: Any) -> Optional[float]:
    n = _num(v)
    return round(n / 1e8, 1) if n is not None else None


class DealOptimizationAgent:
    analysis_type = "deal_optimization"

    def optimize(
        self,
        project_data: Dict[str, Any],
        artifacts: Dict[str, Any],
        policy: Optional[Any] = None,
        *,
        verified_legal_approval_decision: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        fi = project_data.get("feasibility_input") or {}
        revenue = _num(fi.get("total_revenue")) or _num(fi.get("sales_revenue")) or 0.0
        cost = _num(fi.get("total_cost"))
        construction = _num(fi.get("construction_cost")) or 0.0
        pf_amount = sum(
            float(t.get("amount") or 0) for t in (project_data.get("pf_structure") or []) if isinstance(t, dict)
        )
        pf_structure = artifacts.get("pfStructure") or {}
        ltv = _num(pf_structure.get("ltv"))
        exit_rate = _num(pf_structure.get("exit_sale_rate"))
        credit_enh = artifacts.get("creditEnhancement") or {}
        adjusted_senior_ltv = _num(credit_enh.get("adjustedSeniorLtv"))
        if adjusted_senior_ltv is None:
            adjusted_senior_ltv = ltv
        credit_review = artifacts.get("creditReviewDecision") or {}
        restructuring = artifacts.get("restructuringPlan") or {}
        legal_gate = self._legal_gate(
            project_data,
            artifacts,
            verified_legal_approval_decision=verified_legal_approval_decision,
        )

        hurdle = getattr(policy, "hurdleRate", None) or 0.12
        exit_threshold = getattr(policy, "exitSaleRateReworkThreshold", None) or 0.85
        max_senior_ltv = getattr(policy, "maxAdjustedSeniorLtv", None) or DEFAULT_MAX_SENIOR_LTV

        levers: List[Dict[str, Any]] = []
        actions: List[str] = []
        margin = (revenue - cost) / revenue if revenue > 0 and cost is not None else None

        # 1) Profitability gap -> 공사비 절감 또는 분양가 상향
        if margin is not None and margin < TARGET_MARGIN and revenue > 0:
            profit_gap = TARGET_MARGIN * revenue - (revenue - cost)
            cost_cut_pct = (profit_gap / construction) if construction else None
            price_up_amount = profit_gap / (1 - TARGET_MARGIN)
            price_up_pct = price_up_amount / revenue
            cost_feasible = cost_cut_pct is not None and cost_cut_pct <= MAX_CONSTRUCTION_CUT
            price_feasible = price_up_pct <= MAX_PRICE_UP
            levers.append({
                "type": "construction_cost_down",
                "label": "공사비 절감",
                "targetAmount": round(profit_gap, 0),
                "targetAmountEok": _eok(profit_gap),
                "targetPct": round(cost_cut_pct, 4) if cost_cut_pct is not None else None,
                "feasible": cost_feasible,
            })
            levers.append({
                "type": "sale_price_up",
                "label": "분양가 상향",
                "targetAmount": round(price_up_amount, 0),
                "targetAmountEok": _eok(price_up_amount),
                "targetPct": round(price_up_pct, 4),
                "feasible": price_feasible,
            })
            if cost_feasible:
                actions.append(f"공사비 약 {_eok(profit_gap)}억원(−{cost_cut_pct * 100:.1f}%) 협의 절감 → 개발이익률 {TARGET_MARGIN:.0%} 확보")
            if price_feasible:
                actions.append(f"분양가 약 {price_up_pct * 100:.1f}% 상향 → 사업수지 개선")
            if not cost_feasible and not price_feasible:
                actions.append(f"개발이익률 {margin:.1%} → 목표 {TARGET_MARGIN:.0%} 달성에 공사비/분양가 조정폭이 현실적 범위를 초과")

        # 2) Senior LTV over cap -> 자기자본 확충 / PF 축소
        if adjusted_senior_ltv is not None and adjusted_senior_ltv > max_senior_ltv and ltv:
            collateral = pf_amount / ltv if ltv else 0.0
            reduction = pf_amount * (1 - max_senior_ltv / adjusted_senior_ltv)
            ltv_feasible = reduction <= 0.4 * pf_amount  # up to ~40% equity/PF swing
            levers.append({
                "type": "equity_increase_or_pf_reduction",
                "label": "자기자본 확충 / PF 축소",
                "targetAmount": round(reduction, 0),
                "targetAmountEok": _eok(reduction),
                "targetSeniorLtv": max_senior_ltv,
                "feasible": ltv_feasible,
            })
            actions.append(f"자기자본 {_eok(reduction)}억원 확충 또는 PF {_eok(reduction)}억원 축소 → 선순위 LTV {max_senior_ltv:.0%} 이내")

        # 3) Exit sale rate too high -> 분양가 상향 또는 PF 축소
        if exit_rate is not None and exit_rate >= exit_threshold and revenue > 0:
            pf_reduction = max(0.0, pf_amount - revenue * exit_threshold)
            levers.append({
                "type": "exit_rate_relief",
                "label": "Exit 분양률 완화",
                "pfReductionAmount": round(pf_reduction, 0),
                "pfReductionEok": _eok(pf_reduction),
                "targetExitRate": exit_threshold,
                "feasible": pf_reduction <= 0.3 * pf_amount,
            })
            actions.append(f"PF {_eok(pf_reduction)}억원 축소 또는 분양가 상향 → Exit 분양률 {exit_threshold:.0%} 이하")

        # 4) Stress senior preservation -> reuse restructuring cure
        senior_fix = restructuring.get("seniorShortfallFix") or {}
        if senior_fix.get("status") == "action_required":
            actions.append("스트레스 시 선순위 원금 보존을 위한 추가 담보·신용보강 확보 또는 PF 축소 (구조조정안 참조)")
            levers.append({"type": "credit_enhancement", "label": "추가 신용보강·담보", "feasible": True, "source": "restructuringPlan"})

        if legal_gate["status"] != "clear":
            actions.insert(0, legal_gate["requiredAction"])
        verdict, verdict_label = self._verdict(credit_review, margin, levers, legal_gate)

        return {
            "schemaVersion": DEAL_OPTIMIZATION_SCHEMA_VERSION,
            "analysisType": self.analysis_type,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
            "verdictLabel": verdict_label,
            "currentMargin": round(margin, 4) if margin is not None else None,
            "targetMargin": TARGET_MARGIN,
            "levers": levers,
            "borrowerActions": actions,
            "legalEligibilityGate": legal_gate,
            "summary": self._summary(verdict, actions),
        }

    @staticmethod
    def _verdict(
        credit_review: Dict[str, Any],
        margin: Optional[float],
        levers: List[Dict[str, Any]],
        legal_gate: Dict[str, Any],
    ):
        if legal_gate.get("status") == "blocking":
            return "not_recommended", "법률·인허가 차단으로 취급 불가"
        if legal_gate.get("status") == "pending":
            return "rework_required", "법률·인허가 검토 완료 후 재심"
        if credit_review.get("decision") == "reject_recommended":
            return "not_recommended", "여신 취급 곤란 (부결 권고)"
        if credit_review.get("decision") == "rework_required":
            return "rework_required", "보완 후 재심"
        if margin is None:
            return "rework_required", "사업수지 근거 보완 후 재심"
        if not levers:
            return "approvable", "현 조건으로 승인 가능 (추가 협의 불요)"
        # If every gap has at least one feasible lever -> negotiable; else not recommended.
        gap_groups: Dict[str, List[bool]] = {}
        for lever in levers:
            gap = "profitability" if lever["type"] in {"construction_cost_down", "sale_price_up"} else lever["type"]
            gap_groups.setdefault(gap, []).append(bool(lever.get("feasible")))
        all_gaps_curable = all(any(group) for group in gap_groups.values())
        if all_gaps_curable:
            return "negotiable_conditional", "협의 시 조건부 가능"
        return "not_recommended", "여신 취급 곤란 (협의로도 정합 불가)"

    @staticmethod
    def _legal_gate(
        project_data: Dict[str, Any],
        artifacts: Dict[str, Any],
        *,
        verified_legal_approval_decision: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        legal = (
            artifacts.get("legalPermitReview")
            or artifacts.get("legalReview")
            or project_data.get("legalPermitReview")
            or project_data.get("legalReview")
            or project_data.get("legal")
            or {}
        )
        envelope = (
            artifacts.get("buildableEnvelope")
            or (legal.get("buildableEnvelope") if isinstance(legal, dict) else None)
            or project_data.get("buildableEnvelope")
        )
        legal_status = str((legal.get("status") if isinstance(legal, dict) else None) or "").lower()
        envelope_eligibility = str(
            (envelope.get("eligibility") if isinstance(envelope, dict) else None) or ""
        ).lower()
        blocking_risks = [
            risk.get("code")
            for risk in ((legal.get("riskIssues") or []) if isinstance(legal, dict) else [])
            if isinstance(risk, dict) and risk.get("blocking") is True
        ]
        binding_checks = envelope.get("snapshotBindingChecks") if isinstance(envelope, dict) else None
        repository_verified = bool(
            isinstance(binding_checks, list)
            and any(
                isinstance(check, dict)
                and check.get("name") == "approval_repository_verification"
                and check.get("passed") is True
                for check in binding_checks
            )
        )
        binding_valid = bool(
            isinstance(binding_checks, list)
            and binding_checks
            and all(isinstance(check, dict) and check.get("passed") is True for check in binding_checks)
            and repository_verified
        )
        external_decision_verified = DealOptimizationAgent._verified_legal_approval_matches(
            envelope,
            verified_legal_approval_decision,
        )
        if legal_status == "blocking_issue" or envelope_eligibility == "ineligible" or blocking_risks:
            return {
                "status": "blocking",
                "legalReviewStatus": legal_status or None,
                "envelopeEligibility": envelope_eligibility or None,
                "blockingRiskCodes": blocking_risks,
                "requiredAction": "법률·인허가 차단 사유를 해소하고 적격성 Gate를 재평가해야 합니다.",
            }
        if (
            not isinstance(envelope, dict)
            or envelope_eligibility in {"conditional", "not_assessable", ""}
            or envelope.get("reportGrade") is not True
            or not binding_valid
            or not external_decision_verified
        ):
            return {
                "status": "pending",
                "legalReviewStatus": legal_status or None,
                "envelopeEligibility": envelope_eligibility or None,
                "approvalRepositoryDecisionVerified": external_decision_verified,
                "blockingRiskCodes": [],
                "requiredAction": "승인된 필지·규제 스냅샷과 인허가 검토를 연결한 뒤 최적화를 재실행해야 합니다.",
            }
        return {
            "status": "clear",
            "legalReviewStatus": legal_status or None,
            "envelopeEligibility": envelope_eligibility,
            "snapshotBindingVerified": binding_valid,
            "approvalRepositoryDecisionVerified": external_decision_verified,
            "blockingRiskCodes": [],
            "requiredAction": "",
        }

    @staticmethod
    def _verified_legal_approval_matches(
        envelope: Any,
        decision: Optional[Dict[str, Any]],
    ) -> bool:
        """Match an independently supplied approval decision to the envelope.

        The decision is intentionally not read from ``project_data`` or
        ``artifacts``. Their report-grade and binding-check booleans are
        self-attested payload fields and cannot prove an approval-repository
        lookup by themselves.
        """
        if not isinstance(envelope, dict) or not isinstance(decision, dict):
            return False

        approval = envelope.get("regulatoryApproval") or envelope.get("regulatory_approval") or {}
        if not isinstance(approval, dict):
            return False
        parcel_snapshot = envelope.get("siteParcelSnapshot") or envelope.get("site_parcel_snapshot") or {}
        if not isinstance(parcel_snapshot, dict):
            return False

        expected = {
            "approvalStatus": (
                approval.get("approvalStatus")
                or approval.get("approval_status")
                or envelope.get("approvalStatus")
                or envelope.get("approval_status")
                or envelope.get("aggregationApprovalStatus")
            ),
            "approvalDecisionId": (
                approval.get("approvalDecisionId")
                or approval.get("approval_decision_id")
                or envelope.get("approvalDecisionId")
                or envelope.get("approval_decision_id")
            ),
            "snapshotId": (
                envelope.get("regulatoryConstraintSnapshotId")
                or envelope.get("regulatory_constraint_snapshot_id")
                or envelope.get("snapshotId")
                or envelope.get("snapshot_id")
            ),
            "parcelSetHash": (
                parcel_snapshot.get("parcelSetHash")
                or parcel_snapshot.get("parcel_set_hash")
                or envelope.get("parcelSetHash")
                or envelope.get("parcel_set_hash")
            ),
            "sourceHash": (
                approval.get("sourceHash")
                or approval.get("source_hash")
                or envelope.get("sourceHash")
                or envelope.get("source_hash")
            ),
        }
        actual = {
            "approvalStatus": decision.get("approvalStatus") or decision.get("approval_status"),
            "approvalDecisionId": (
                decision.get("approvalDecisionId")
                or decision.get("approval_decision_id")
                or decision.get("id")
            ),
            "snapshotId": decision.get("snapshotId") or decision.get("snapshot_id"),
            "parcelSetHash": decision.get("parcelSetHash") or decision.get("parcel_set_hash"),
            "sourceHash": decision.get("sourceHash") or decision.get("source_hash"),
        }
        if str(actual["approvalStatus"] or "").strip().lower() != "approved":
            return False
        for field, expected_value in expected.items():
            actual_value = actual[field]
            if not expected_value or not actual_value:
                return False
            if str(actual_value).strip() != str(expected_value).strip():
                return False
        return True

    @staticmethod
    def _summary(verdict: str, actions: List[str]) -> str:
        if verdict == "approvable":
            return "현재 사업조건으로 여신 정합성을 충족합니다."
        if verdict == "negotiable_conditional":
            head = "에이전트 협의 결과, 아래 사업조건 조정 시 여신 취급이 가능합니다: "
            return head + " / ".join(actions[:3]) if actions else head
        if verdict == "rework_required":
            return "법률·인허가 또는 신용 보완사항을 해소한 뒤 최적화안을 재심해야 합니다."
        return "공사비·분양가·자본구조 조정폭이 현실적 범위를 초과하여 현 구조로는 여신 취급이 곤란합니다 (부결 권고)."
