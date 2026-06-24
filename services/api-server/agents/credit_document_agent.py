from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents.term_sheet_agent import TermSheetAgent

TERM_SHEET_SCHEMA_VERSION = "realytics-term-sheet-v1"
IC_MEMO_SCHEMA_VERSION = "realytics-ic-memo-v1"


def _num(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _eok(value: Any) -> Optional[float]:
    n = _num(value)
    return round(n / 1e8, 1) if n is not None else None


def _eok_str(value: Any) -> str:
    e = _eok(value)
    return f"{e:,.1f}억원" if e is not None else "—"


def _pct_str(value: Any) -> str:
    n = _num(value)
    return f"{n * 100:.1f}%" if n is not None else "—"


def _x_str(value: Any) -> str:
    n = _num(value)
    return f"{n:.2f}x" if n is not None else "—"


class CreditDocumentAgent:
    """Synthesise lender-facing credit documents from a PF review run.

    Both outputs are deterministic restatements of artifacts already produced
    by the orchestration (Credit Review, PF structure, return metrics, stress
    test, credit enhancement, risk register, marketability, legal, approval
    readiness, developability) into the two formats a credit officer actually
    files: a Term Sheet (거래조건서) and an IC Memo (여신심사보고서). No new
    figures are invented — missing inputs render as "—".
    """

    # ------------------------------------------------------------------ Term Sheet
    def build_term_sheet(
        self,
        project_data: Dict[str, Any],
        artifacts: Dict[str, Any],
        stage: str = "auto",
    ) -> Dict[str, Any]:
        """Delegate to the multi-agent TermSheetAgent (Land/Feasibility/PF/Credit分업)."""
        return TermSheetAgent().compose(project_data, artifacts, stage=stage)

    # ------------------------------------------------------------------ IC Memo
    def build_ic_memo(self, project_data: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
        project = project_data.get("project") or {}
        feasibility = project_data.get("feasibility_input") or {}
        credit_review = artifacts.get("creditReviewDecision") or {}
        pf_structure = artifacts.get("pfStructure") or {}
        return_metrics = artifacts.get("returnMetrics") or {}
        stress_test = artifacts.get("stressTest") or {}
        marketability = artifacts.get("marketabilityAnalysis") or {}
        legal = artifacts.get("legalPermitReview") or {}
        developability = artifacts.get("developabilityScore") or {}
        approval_gate = artifacts.get("approvalReadinessGate") or {}

        rm = return_metrics.get("metrics") or {}

        def _metric(key: str):
            entry = rm.get(key)
            return entry.get("value") if isinstance(entry, dict) else None

        base_stress = next((s for s in (stress_test.get("scenarios") or []) if s.get("key") == "base"), None)

        key_metrics = {
            "projectIrr": _metric("projectIrr") if _metric("projectIrr") is not None else (base_stress or {}).get("projectIrr"),
            "equityIrr": _metric("equityIrr"),
            "equityMultiple": _metric("equityMultiple"),
            "paybackMonths": (rm.get("paybackPeriodMonths") or {}).get("value") if isinstance(rm.get("paybackPeriodMonths"), dict) else None,
            "icr": _metric("icr"),
            "ltv": pf_structure.get("ltv"),
            "ltc": pf_structure.get("ltc"),
            "exitSaleRate": pf_structure.get("exit_sale_rate"),
            "developabilityGrade": developability.get("grade"),
            "developabilityScore": developability.get("overallScore"),
            "marketabilityGrade": marketability.get("grade"),
            "marketabilityScore": marketability.get("score"),
        }

        deal_summary = {
            "projectType": project.get("type"),
            "location": (project_data.get("land") or {}).get("address"),
            "totalRevenue": feasibility.get("total_revenue"),
            "totalCost": feasibility.get("total_cost"),
            "pfAmount": sum(
                float(t.get("amount") or 0)
                for t in (project_data.get("pf_structure") or [])
                if isinstance(t, dict)
            )
            or None,
        }

        stress_summary = {
            "scenarios": [
                {"key": s.get("key"), "name": s.get("name"), "projectIrr": s.get("projectIrr"), "seniorStatus": s.get("seniorStatus")}
                for s in (stress_test.get("scenarios") or [])
            ],
            "worstCaseKey": stress_test.get("worstCaseKey"),
            "seniorBreakScenarioKey": stress_test.get("seniorBreakScenarioKey"),
        }

        ic_memo = {
            "schemaVersion": IC_MEMO_SCHEMA_VERSION,
            "analysisType": "ic_memo",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "documentType": "여신심사보고서(IC Memo)",
            "status": "draft",
            "header": {
                "projectName": project.get("name"),
                "preparedBy": "REALYTICS x PLATFORM9 AI",
            },
            "recommendation": {
                "decision": credit_review.get("decision"),
                "decisionLabel": credit_review.get("decisionLabel"),
                "status": credit_review.get("status"),
                "confidence": credit_review.get("confidence"),
                "approvalReady": approval_gate.get("pass"),
                "policyProfile": credit_review.get("policyProfile") or {},
            },
            "dealSummary": deal_summary,
            "keyMetrics": key_metrics,
            "stressSummary": stress_summary,
            "decisionRationale": [str(r) for r in (credit_review.get("decisionReasons") or [])],
            "keyRisks": [
                {"title": r.get("title"), "severity": r.get("severity"), "mitigant": r.get("mitigant"), "code": r.get("code")}
                for r in (credit_review.get("keyRisks") or [])
            ],
            "mitigants": [str(m) for m in (credit_review.get("mitigants") or [])],
            "approvalConditions": [str(c) for c in (credit_review.get("approvalConditions") or [])],
            "requiredFollowUps": [str(f) for f in (credit_review.get("requiredFollowUps") or [])],
            "requiredWaivers": credit_review.get("requiredWaivers") or [],
            "marketabilityAssessment": {
                "grade": marketability.get("grade"),
                "score": marketability.get("score"),
                "comment": (marketability.get("reportParagraphs") or {}).get("recommendation") if isinstance(marketability.get("reportParagraphs"), dict) else None,
            },
            "legalStatus": legal.get("status"),
            "developability": {
                "grade": developability.get("grade"),
                "overallScore": developability.get("overallScore"),
                "gaps": developability.get("gaps") or [],
            },
            "approvalReadiness": {
                "pass": approval_gate.get("pass"),
                "blockers": [
                    (b.get("message") or b.get("reason") or b.get("code")) if isinstance(b, dict) else str(b)
                    for b in (approval_gate.get("blockers") or [])
                ],
            },
            "dataLimitations": [str(x) for x in (credit_review.get("dataLimitations") or [])],
        }
        ic_memo["markdown"] = self._ic_memo_markdown(ic_memo)
        return ic_memo

    def _ic_memo_markdown(self, memo: Dict[str, Any]) -> str:
        rec = memo["recommendation"]
        km = memo["keyMetrics"]
        ds = memo["dealSummary"]
        lines: List[str] = []
        lines.append(f"# 여신심사보고서 (IC Memo) — {memo['header'].get('projectName') or '미상'}")
        lines.append("")
        lines.append("## 1. 심사 의견")
        lines.append(f"- **여신 의견: {rec.get('decisionLabel') or '—'}** (신뢰도 {_pct_str(rec.get('confidence'))})")
        lines.append(f"- 승인 준비: {'준비완료' if rec.get('approvalReady') else '미충족(조건 잔존)'}")
        policy = rec.get("policyProfile") or {}
        if policy.get("lenderName"):
            lines.append(
                f"- 적용 여신정책: {policy.get('lenderName')} (Hurdle {_pct_str(policy.get('hurdleRate'))})"
            )
        lines.append("")
        lines.append("## 2. 거래 개요")
        lines.append(f"- 유형/소재지: {ds.get('projectType') or '—'} / {ds.get('location') or '—'}")
        lines.append(f"- 총 매출 {_eok_str(ds.get('totalRevenue'))} · 총 사업비 {_eok_str(ds.get('totalCost'))} · PF {_eok_str(ds.get('pfAmount'))}")
        lines.append("")
        lines.append("## 3. 핵심 지표")
        lines.append(f"- Project IRR {_pct_str(km.get('projectIrr'))} · Equity IRR {_pct_str(km.get('equityIrr'))} · Equity Multiple {_x_str(km.get('equityMultiple'))} · ICR {_x_str(km.get('icr'))}")
        lines.append(f"- LTV {_pct_str(km.get('ltv'))} · LTC {_pct_str(km.get('ltc'))} · Exit 분양률 {_pct_str(km.get('exitSaleRate'))}")
        lines.append(f"- 개발가능성 {km.get('developabilityGrade') or '—'}등급({km.get('developabilityScore') if km.get('developabilityScore') is not None else '—'}점) · 시장성 {km.get('marketabilityGrade') or '—'}등급")
        lines.append("")
        lines.append("## 4. 스트레스 테스트")
        if memo["stressSummary"]["scenarios"]:
            lines.append("| 시나리오 | Project IRR | Senior 상태 |")
            lines.append("|---|---|---|")
            for s in memo["stressSummary"]["scenarios"]:
                lines.append(f"| {s.get('name') or s.get('key')} | {_pct_str(s.get('projectIrr'))} | {s.get('seniorStatus') or '—'} |")
        else:
            lines.append("- 스트레스 결과 없음")
        lines.append("")
        lines.append("## 5. 주요 리스크 및 완화방안")
        if memo["keyRisks"]:
            for r in memo["keyRisks"]:
                lines.append(f"- [{r.get('severity') or '—'}] {r.get('title') or r.get('code')} → {r.get('mitigant') or '완화방안 검토 필요'}")
        else:
            lines.append("- 식별된 주요 리스크 없음")
        lines.append("")
        lines.append("## 6. 승인조건")
        lines.extend([f"- {c}" for c in memo["approvalConditions"]] or ["- 표준 약정조건 유지"])
        if memo["requiredFollowUps"]:
            lines.append("")
            lines.append("## 7. 후속 필요사항")
            lines.extend([f"- {f}" for f in memo["requiredFollowUps"]])
        if memo["approvalReadiness"]["blockers"]:
            lines.append("")
            lines.append("## 8. 승인 차단요인")
            lines.extend([f"- {b}" for b in memo["approvalReadiness"]["blockers"]])
        if memo["dataLimitations"]:
            lines.append("")
            lines.append("## 9. 데이터 한계")
            lines.extend([f"- {d}" for d in memo["dataLimitations"]])
        return "\n".join(lines)
