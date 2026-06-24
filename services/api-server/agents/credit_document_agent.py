from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
    def build_term_sheet(self, project_data: Dict[str, Any], artifacts: Dict[str, Any]) -> Dict[str, Any]:
        project = project_data.get("project") or {}
        feasibility = project_data.get("feasibility_input") or {}
        tranches_in = [t for t in (project_data.get("pf_structure") or []) if isinstance(t, dict)]
        covenants = project_data.get("covenants") or {}
        pf_structure = artifacts.get("pfStructure") or {}
        credit_enhancement = artifacts.get("creditEnhancement") or {}
        cash_sweep = artifacts.get("cashSweep") or {}
        credit_review = artifacts.get("creditReviewDecision") or {}

        tranches = [
            {
                "name": t.get("tranche") or t.get("name") or f"Tranche {idx + 1}",
                "amount": _num(t.get("amount")),
                "amountEok": _eok(t.get("amount")),
                "interestRate": _num(t.get("interest_rate") or t.get("rate")),
                "feeRate": _num(t.get("fee_rate")),
                "allInCost": _num(t.get("all_in_cost")),
                "priority": t.get("priority", idx + 1),
            }
            for idx, t in enumerate(tranches_in)
        ]
        total_facility = sum(t["amount"] for t in tranches if t["amount"]) or None

        facility = {
            "totalFacility": total_facility,
            "totalFacilityEok": _eok(total_facility),
            "tranches": tranches,
            "ltv": pf_structure.get("ltv"),
            "ltc": pf_structure.get("ltc"),
            "exitSaleRate": pf_structure.get("exit_sale_rate"),
        }

        security = [str(item) for item in (covenants.get("claim_preservation") or [])]
        ce_measures = [
            m.get("label") or m.get("measure") or m.get("type")
            for m in (credit_enhancement.get("measures") or [])
            if isinstance(m, dict) and (m.get("active") or m.get("status") == "active")
        ]
        ce_measures = [str(m) for m in ce_measures if m]

        term_sheet = {
            "schemaVersion": TERM_SHEET_SCHEMA_VERSION,
            "analysisType": "term_sheet",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "documentType": "거래조건서(Term Sheet)",
            "status": "draft",
            "header": {
                "projectName": project.get("name"),
                "projectType": project.get("type"),
                "location": (project_data.get("land") or {}).get("address"),
                "currency": "KRW",
            },
            "parties": {
                "borrower": (project_data.get("developer_info") or {}).get("name") or project.get("developer"),
                "contractor": (project_data.get("contractor_info") or {}).get("name") or project.get("contractor"),
            },
            "facility": facility,
            "security": security,
            "creditEnhancement": {
                "grade": credit_enhancement.get("enhancementGrade"),
                "baseSeniorLtv": credit_enhancement.get("baseSeniorLtv"),
                "adjustedSeniorLtv": credit_enhancement.get("adjustedSeniorLtv"),
                "activeMeasures": ce_measures,
                "missingCriticalMeasures": credit_enhancement.get("missingCriticalMeasures") or [],
            },
            "cashSweepReserve": {
                "applied": bool(cash_sweep) and cash_sweep.get("status") != "skipped",
                "summary": cash_sweep.get("summary") if isinstance(cash_sweep, dict) else None,
            },
            "drawdownConditions": [str(x) for x in (covenants.get("drawdown_conditions") or [])],
            "subsequentConditions": [str(x) for x in (covenants.get("subsequent_conditions") or [])],
            "repaymentWaterfall": [str(x) for x in (covenants.get("execution_order") or [])],
            "eodTriggers": [str(x) for x in (covenants.get("eod") or [])]
            + [str(x) for x in (covenants.get("automatic_eod") or [])],
            "approvalConditions": [str(x) for x in (credit_review.get("approvalConditions") or [])],
        }
        term_sheet["markdown"] = self._term_sheet_markdown(term_sheet)
        return term_sheet

    def _term_sheet_markdown(self, ts: Dict[str, Any]) -> str:
        h = ts["header"]
        f = ts["facility"]
        ce = ts["creditEnhancement"]
        lines: List[str] = []
        lines.append(f"# 거래조건서 (Term Sheet) — {h.get('projectName') or '미상'}")
        lines.append("")
        lines.append(f"- 사업유형: {h.get('projectType') or '—'}")
        lines.append(f"- 소재지: {h.get('location') or '—'}")
        lines.append(f"- 차주(시행사): {ts['parties'].get('borrower') or '—'}")
        lines.append(f"- 시공사: {ts['parties'].get('contractor') or '—'}")
        lines.append("")
        lines.append("## 1. 여신 한도 및 구조")
        lines.append(f"- 총 PF 한도: {_eok_str(f.get('totalFacility'))}")
        lines.append(f"- LTV: {_pct_str(f.get('ltv'))} · LTC: {_pct_str(f.get('ltc'))} · Exit 분양률: {_pct_str(f.get('exitSaleRate'))}")
        if f.get("tranches"):
            lines.append("")
            lines.append("| Tranche | 금액 | 금리 | 수수료 | All-in | 순위 |")
            lines.append("|---|---|---|---|---|---|")
            for t in f["tranches"]:
                lines.append(
                    f"| {t.get('name')} | {_eok_str(t.get('amount'))} | {_pct_str(t.get('interestRate'))} "
                    f"| {_pct_str(t.get('feeRate'))} | {_pct_str(t.get('allInCost'))} | {t.get('priority')} |"
                )
        lines.append("")
        lines.append("## 2. 신용보강")
        lines.append(f"- 신용보강 등급: {ce.get('grade') or '—'}")
        lines.append(f"- 선순위 LTV: {_pct_str(ce.get('baseSeniorLtv'))} → {_pct_str(ce.get('adjustedSeniorLtv'))} (보강 후)")
        if ce.get("activeMeasures"):
            lines.append(f"- 적용 수단: {', '.join(ce['activeMeasures'])}")
        if ce.get("missingCriticalMeasures"):
            lines.append(f"- ⚠ 핵심수단 미확보: {', '.join(ce['missingCriticalMeasures'])}")
        lines.append("")
        lines.append("## 3. 담보 및 채권보전")
        lines.extend([f"- {s}" for s in ts["security"]] or ["- —"])
        lines.append("")
        lines.append("## 4. 인출선행조건 (CP)")
        lines.extend([f"- {s}" for s in ts["drawdownConditions"]] or ["- —"])
        if ts["subsequentConditions"]:
            lines.append("")
            lines.append("## 4-1. 인출후행조건")
            lines.extend([f"- {s}" for s in ts["subsequentConditions"]])
        lines.append("")
        lines.append("## 5. 자금집행 순서 (Waterfall)")
        lines.extend([f"- {s}" for s in ts["repaymentWaterfall"]] or ["- 약정 waterfall에 따름"])
        lines.append("")
        lines.append("## 6. 기한이익상실 (EOD)")
        lines.extend([f"- {s}" for s in ts["eodTriggers"]] or ["- —"])
        if ts["approvalConditions"]:
            lines.append("")
            lines.append("## 7. 승인조건")
            lines.extend([f"- {s}" for s in ts["approvalConditions"]])
        return "\n".join(lines)

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
