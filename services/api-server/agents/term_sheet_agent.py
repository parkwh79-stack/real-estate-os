from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict, List, Optional

from agents.credit_document_i18n import localize as _localize_condition
from core.calculations import normalize_tranche_priority
from agents.term_sheet_templates import (
    BOILERPLATE,
    DEFAULT_SECTION_SOURCE_AGENT,
    SECTION_SOURCE_AGENT,
    STAGE_META,
    STAGE_SECTIONS,
    TBD,
    TERM_SHEET_STAGE_BRIDGE,
    TERM_SHEET_STAGE_MAIN_PF,
)

TERM_SHEET_SCHEMA_VERSION = "realytics-term-sheet-v1"


def _num(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number if isfinite(number) else None
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


def _man_per_sqm(value: Any) -> str:
    """Official land price is per ㎡; show in 만원/㎡."""
    n = _num(value)
    return f"{n / 1e4:,.0f}만원/㎡" if n is not None else "—"


class TermSheetAgent:
    """Compose a stage-specific term sheet (거래조건서) as a multi-agent collaboration.

    A term sheet is the lender side's indicative conditions to the borrower, so
    each section is contributed by the agent that owns that domain — the Land
    Agent supplies 대지위치·지번(PNU)·공시지가·면적·감정가, the Feasibility/Cash Flow
    agents supply 사업수지·한도·상환, Marketability supplies 분양조건, and
    Credit/Legal supply 담보·책임준공·covenants·EOD. Every section records its
    source agent and evidence so the document is auditable, and unknown values
    fall back to standard clauses or [TBD] like a real indicative term sheet.
    """

    analysis_type = "term_sheet"

    def compose(
        self,
        project_data: Dict[str, Any],
        artifacts: Dict[str, Any],
        stage: str = "auto",
    ) -> Dict[str, Any]:
        project = project_data.get("project") or {}
        tranches_in = [t for t in (project_data.get("pf_structure") or []) if isinstance(t, dict)]
        covenants = project_data.get("covenants") or {}
        pf_structure = artifacts.get("pfStructure") or {}
        credit_enhancement = artifacts.get("creditEnhancement") or {}
        cash_sweep = artifacts.get("cashSweep") or {}
        credit_review = artifacts.get("creditReviewDecision") or {}
        land_review = artifacts.get("landAgentReview") or {}
        feasibility_art = artifacts.get("developerFeasibility") or artifacts.get("feasibility") or {}

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

        resolved_stage = self._resolve_stage(stage, project_data, tranches)
        stage_meta = STAGE_META[resolved_stage]

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

        ctx = {
            "stage": resolved_stage,
            "project": project,
            "land": project_data.get("land") or project_data.get("site") or {},
            "regulation": project_data.get("regulation") or {},
            "feasibility_input": project_data.get("feasibility_input") or {},
            "feasibility_art": feasibility_art,
            "land_review": land_review,
            "covenants": covenants,
            "tranches": tranches,
            "total_facility": total_facility,
            "pf_structure": pf_structure,
            "credit_enhancement": credit_enhancement,
            "security": security,
            "ce_measures": ce_measures,
            "borrower": (project_data.get("developer_info") or {}).get("name") or project.get("developer"),
            "contractor": (project_data.get("contractor_info") or {}).get("name") or project.get("contractor"),
        }

        sections = []
        for key, label in STAGE_SECTIONS[resolved_stage]:
            source_agent = SECTION_SOURCE_AGENT.get(key, DEFAULT_SECTION_SOURCE_AGENT)
            sections.append({
                "key": key,
                "label": label,
                "value": self._section_value(key, resolved_stage, ctx),
                "sourceAgent": source_agent,
                "evidence": self._evidence_for(source_agent, artifacts),
            })

        contributions = self._contributions_summary(sections)

        term_sheet = {
            "schemaVersion": TERM_SHEET_SCHEMA_VERSION,
            "analysisType": self.analysis_type,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "documentType": f"거래조건서(Term Sheet) — {stage_meta['name']}",
            "stage": resolved_stage,
            "stageName": stage_meta["name"],
            "stageDescription": stage_meta["description"],
            "status": "draft",
            "header": {
                "projectName": project.get("name"),
                "projectType": project.get("type"),
                "location": ctx["land"].get("address"),
                "currency": "KRW",
            },
            "parties": {"borrower": ctx["borrower"], "contractor": ctx["contractor"]},
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
            "sections": sections,
            "contributions": contributions,
            "drawdownConditions": [str(x) for x in (covenants.get("drawdown_conditions") or [])],
            "subsequentConditions": [str(x) for x in (covenants.get("subsequent_conditions") or [])],
            "repaymentWaterfall": [str(x) for x in (covenants.get("execution_order") or [])],
            "eodTriggers": [str(x) for x in (covenants.get("eod") or [])]
            + [str(x) for x in (covenants.get("automatic_eod") or [])],
            "approvalConditions": [_localize_condition(str(x)) for x in (credit_review.get("approvalConditions") or [])],
        }
        term_sheet["markdown"] = self._markdown(term_sheet)
        return term_sheet

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _resolve_stage(stage: str, project_data: Dict[str, Any], tranches: List[Dict[str, Any]]) -> str:
        requested = (stage or "auto").strip().lower()
        if requested in (TERM_SHEET_STAGE_BRIDGE, TERM_SHEET_STAGE_MAIN_PF):
            return requested
        hint = str(
            project_data.get("term_sheet_stage")
            or project_data.get("financing_stage")
            or (project_data.get("pf_structure_meta") or {}).get("facilityType")
            or ""
        ).lower()
        if "bridge" in hint or "브릿지" in hint or "토지담보" in hint:
            return TERM_SHEET_STAGE_BRIDGE
        if "main" in hint or "본pf" in hint or "본 pf" in hint:
            return TERM_SHEET_STAGE_MAIN_PF
        return TERM_SHEET_STAGE_MAIN_PF if len(tranches) >= 2 else TERM_SHEET_STAGE_BRIDGE

    @staticmethod
    def _evidence_for(source_agent: str, artifacts: Dict[str, Any]) -> Dict[str, Any]:
        if source_agent == "LandAgent":
            land_review = artifacts.get("landAgentReview") or {}
            return {
                "sourceAgent": source_agent,
                "evidenceIds": land_review.get("evidenceIds") or [],
                "sourceLocators": land_review.get("sourceLocators") or [],
            }
        artifact_key = {
            "DevelopmentFeasibilityAgent": "developerFeasibility",
            "CashFlowEngine": "cashFlow",
            "ScenarioEngine": "scenariosEvaluation",
            "MarketabilityAgent": "marketabilityAnalysis",
            "CreditReviewAgent": "creditReviewDecision",
            "CreditEnhancementAgent": "creditEnhancement",
            "LegalReviewAgent": "legalPermitReview",
        }.get(source_agent)
        return {"sourceAgent": source_agent, "evidenceIds": [], "sourceLocators": ([{"artifact": artifact_key}] if artifact_key else [])}

    @staticmethod
    def _contributions_summary(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        by_agent: Dict[str, List[str]] = {}
        for sec in sections:
            by_agent.setdefault(sec["sourceAgent"], []).append(sec["label"])
        return [{"agent": agent, "sections": labels} for agent, labels in by_agent.items()]

    def _section_value(self, key: str, stage: str, ctx: Dict[str, Any]) -> str:
        bridge = stage == TERM_SHEET_STAGE_BRIDGE
        covenants = ctx["covenants"]
        tranches = ctx["tranches"]
        total = ctx["total_facility"]
        land = ctx["land"]
        land_summary = (ctx["land_review"] or {}).get("summary") or {}
        fi = ctx["feasibility_input"]

        def joined(items: List[Any]) -> str:
            cleaned = [str(i).strip() for i in items if str(i).strip()]
            return " / ".join(cleaned) if cleaned else TBD

        # ---- Land Agent contributions
        if key == "site_location":
            return land.get("address") or TBD
        if key == "zoning_use":
            zoning = ctx["regulation"].get("zoning") or land.get("zoning")
            scale = ctx["project"].get("scale") or ctx["project"].get("규모")
            return " · ".join([str(x) for x in [zoning, scale] if x]) or TBD
        if key == "lot_pnu":
            pnu = land_summary.get("pnu") or land.get("pnu")
            lot = land.get("lotNumber") or land.get("lot_number")
            return " / ".join([str(x) for x in [lot, pnu] if x]) or TBD
        if key == "official_land_price":
            price = land_summary.get("officialLandPrice") or land.get("officialLandPrice") or land.get("official_land_price")
            base_year = land_summary.get("officialLandPriceBaseYear")
            if price:
                base = f" (기준연도 {base_year})" if base_year else ""
                return f"{_man_per_sqm(price)}{base}"
            return "[공시지가 확인 필요]"
        if key == "land_area":
            area = land_summary.get("totalAreaSqm") or land.get("area_sqm") or land.get("land_area_sqm") or land.get("areaSqm")
            gfa = fi.get("gross_floor_area") or fi.get("gfa") or land.get("gross_floor_area")
            area_str = f"{_num(area):,.1f}㎡" if _num(area) is not None else "[대지면적 확인 필요]"
            gfa_str = f" / 연면적 {_num(gfa):,.1f}㎡" if _num(gfa) is not None else ""
            return f"{area_str}{gfa_str}"
        if key == "appraisal_collateral":
            parcels = land_summary.get("parcelCount")
            appraisal = land_summary.get("totalPurchaseAmount") or fi.get("land_cost")
            parts = []
            if parcels:
                parts.append(f"필지 {parcels}건")
            if appraisal:
                parts.append(f"토지가액 {_eok_str(appraisal)}")
            parts.append("감정평가서 별첨 (담보감정가 기준)")
            return " · ".join(parts)

        # ---- Feasibility / Cash Flow contributions
        if key == "business_summary":
            revenue = fi.get("total_revenue") or fi.get("sales_revenue")
            cost = fi.get("total_cost")
            profit = (_num(revenue) - _num(cost)) if (_num(revenue) is not None and _num(cost) is not None) else None
            margin = (profit / _num(revenue)) if (profit is not None and _num(revenue)) else None
            return (
                f"총 매출 {_eok_str(revenue)} · 총 사업비 {_eok_str(cost)} · "
                f"개발이익 {_eok_str(profit)} (이익률 {_pct_str(margin)})"
            )
        if key == "borrower":
            return ctx["borrower"] or TBD
        if key == "contractor":
            return ctx["contractor"] or TBD
        if key == "guarantor":
            base = f"{ctx['contractor']} " if ctx["contractor"] else ""
            return f"{base}연대보증 · 차주 대표이사 연대보증".strip()
        if key == "lenders":
            return "참여대주 [TBD] · 금융주관사 [TBD]"
        if key == "trustee":
            return "신탁사 [TBD] (관리형토지신탁) · 대리금융기관 [TBD]"
        if key == "loan_amount":
            if bridge:
                return f"{_eok_str(total)} (토지 매매금액의 [80]% 이내)"
            if tranches:
                parts = ", ".join(f"{t['name']} {_eok_str(t['amount'])}" for t in tranches)
                return f"총 {_eok_str(total)} ({parts})"
            return _eok_str(total)
        if key == "fund_purpose":
            return (
                "매입 토지 계약금·중도금 지급 및 제반 비용 (담보대출 초과분은 시행사 자체 부담)"
                if bridge
                else "토지비 및 필수사업비"
            )
        if key == "ltv":
            ltv = ctx["pf_structure"].get("ltv")
            return f"{_pct_str(ltv)} (담보감정가액 대비)" if ltv else "[80]% (담보감정가액 대비)"
        if key == "interest_rate":
            if not bridge and tranches and any(t.get("interestRate") for t in tranches):
                return " · ".join(
                    f"{t['name']} {_pct_str(t.get('interestRate'))}(all-in {_pct_str(t.get('allInCost'))})"
                    for t in tranches
                )
            return "대출금리 [TBD]% / 취급수수료 [TBD]%"
        if key == "rate_method":
            return BOILERPLATE["rate_method"]
        if key == "interest_payment":
            return BOILERPLATE["interest_payment_bridge" if bridge else "interest_payment_main_pf"]
        if key == "drawdown_method":
            return BOILERPLATE["drawdown_method"]
        if key == "tenor":
            return "[12]개월" if bridge else "최초 인출일로부터 [42]개월 (책임준공 [40]개월 + 입주·정산 [2]개월)"
        if key == "repayment":
            return BOILERPLATE["repayment_main_pf"]
        if key == "prepayment":
            return BOILERPLATE["prepayment_bridge" if bridge else "prepayment_main_pf"]
        if key == "default_interest":
            return BOILERPLATE["default_interest_bridge" if bridge else "default_interest_main_pf"]
        if key == "conditions_precedent":
            override = covenants.get("drawdown_conditions")
            return joined(override) if override else joined(
                BOILERPLATE["conditions_precedent_bridge" if bridge else "conditions_precedent_main_pf"]
            )
        if key == "conditions_concurrent":
            return joined(BOILERPLATE["conditions_concurrent_bridge"])
        if key == "conditions_subsequent":
            override = covenants.get("subsequent_conditions")
            return joined(override) if override else joined(BOILERPLATE["conditions_subsequent_main_pf"])
        if key == "covenants":
            return joined(BOILERPLATE["covenants_main_pf"])
        if key == "security":
            items: List[str] = []
            if bridge:
                items.append("담보신탁 1순위 우선수익권 (수익한도 [130]%)")
                items.append("차주 대표이사 연대보증")
            else:
                items.append("관리형토지신탁")
                if tranches:
                    prio = ", ".join(
                        f"{t['name']} {t['priority']}순위"
                        for t in sorted(
                            tranches,
                            key=lambda x: normalize_tranche_priority(x.get("priority")),
                        )
                    )
                    items.append(prio + ", 시공사 후순위")
                items.append("대출금관리계좌 근질권 설정 · 시공사 책임준공 확약(미이행 시 채무인수)")
            items.extend(ctx["security"])
            items.extend(ctx["ce_measures"])
            return joined(items)
        if key == "responsible_completion":
            return BOILERPLATE["responsible_completion_main_pf"]
        if key == "discount_sale":
            override = covenants.get("discount_triggers")
            return joined(override) if override else BOILERPLATE["discount_sale_main_pf"]
        if key == "revenue_distribution":
            override = covenants.get("revenue_distribution")
            if isinstance(override, str) and override.strip():
                return override
            return BOILERPLATE["revenue_distribution_main_pf"]
        if key == "fund_management":
            override = covenants.get("execution_order")
            if override:
                return "자금집행순서: " + " → ".join(str(x) for x in override)
            return BOILERPLATE["fund_management_main_pf"]
        if key == "eod_events":
            events = list(BOILERPLATE["eod_events_common"])
            if not bridge:
                events.insert(2, BOILERPLATE["eod_events_main_pf_extra"])
            override = [str(x) for x in (covenants.get("eod") or [])] + [str(x) for x in (covenants.get("automatic_eod") or [])]
            return joined(override + events) if override else joined(events)
        if key == "eod_consequences":
            return BOILERPLATE["eod_consequences"]
        if key == "disposal_waterfall":
            return BOILERPLATE["disposal_waterfall_bridge"]
        if key == "governing_court":
            return BOILERPLATE["governing_court"]
        return TBD

    def _markdown(self, ts: Dict[str, Any]) -> str:
        h = ts["header"]
        f = ts["facility"]
        ce = ts["creditEnhancement"]
        lines: List[str] = []
        lines.append(f"# 거래조건서 (Term Sheet) — {h.get('projectName') or '미상'}")
        lines.append("")
        lines.append(f"> 금융단계: {ts.get('stageName')} — {ts.get('stageDescription')}")
        lines.append("")
        lines.append(f"- 사업유형: {h.get('projectType') or '—'}")
        lines.append(f"- 소재지: {h.get('location') or '—'}")
        lines.append(f"- 차주(시행사): {ts['parties'].get('borrower') or '—'}")
        lines.append(f"- 시공사: {ts['parties'].get('contractor') or '—'}")
        lines.append("")
        lines.append("## 1. 여신 한도 및 구조")
        lines.append(f"- 총 한도: {_eok_str(f.get('totalFacility'))}")
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
        if ce.get("missingCriticalMeasures"):
            lines.append(f"- ⚠ 핵심수단 미확보: {', '.join(ce['missingCriticalMeasures'])}")
        lines.append("")
        lines.append("## 3. 상세 거래조건")
        lines.append("| 구분 | 내용 | 작성 |")
        lines.append("|---|---|---|")
        for sec in ts.get("sections", []):
            value = str(sec.get("value") or "—").replace("\n", " / ").replace("|", "·")
            lines.append(f"| {sec.get('label')} | {value} | {sec.get('sourceAgent')} |")
        if ts.get("approvalConditions"):
            lines.append("")
            lines.append("## 4. 승인조건 (AI 심사)")
            lines.extend([f"- {s}" for s in ts["approvalConditions"]])
        return "\n".join(lines)
