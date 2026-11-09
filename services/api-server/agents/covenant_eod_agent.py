"""Covenant / EOD definition engine (deterministic).

Derives the lender-facing **covenant thresholds** (목표 분양률, LTV/LTC 상한,
책임준공, Cash Sweep 개시, 상환계좌 자금집행순서, Equity 선투입, 공사비 초과 충당)
and the **EOD (기한이익상실) conditions** from a deal's deterministic feasibility /
PF numbers. Each item carries a source locator so the Term Sheet / Covenant Sheet /
IC Memo cite the calculation basis instead of a placeholder.

Invariants (AGENTS.md §1):
- Deterministic & evidence-first: every threshold VALUE comes from the deterministic
  feasibility/PF engine output (exitSaleRate, ltvByRevenue, ltcByCost, requiredEquity,
  constructionCost, tranche priorities). The LLM never sets a covenant number.
- No fabrication: when the underlying number is missing, the covenant is emitted as
  ``status=needs_evidence`` with a dataQualityNote — never invented. Standard lender
  policy defaults (e.g. LTC ceiling, min DSCR) are explicitly labelled
  ``basis=lender_policy_default`` so they are not mistaken for deal-derived evidence.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

COVENANT_EOD_SCHEMA_VERSION = "realytics-covenant-eod-v1"

# Standard main-PF lender policy defaults (labelled as policy, not deal evidence).
DEFAULT_LTC_CEILING = 0.90
DEFAULT_MIN_DSCR = 1.20
DEFAULT_COST_OVERRUN_BUFFER = 0.05  # 5% construction cost overrun before equity top-up

_INCOME_ASSET_TYPES = {"commercial", "knowledge_industry_center", "logistics", "lodging"}

# Responsible reviewer role labels (aligned with the AI Risk Committee reviewer roles
# in use_cases/chief_deal_orchestration.py: credit_reviewer / market_stress_tester /
# construction_risk / legal_compliance).
_REVIEWER_LABELS = {
    "credit_reviewer": "여신 심사역",
    "market_stress_tester": "시장·스트레스 심사역",
    "construction_risk": "시공·공사 리스크 심사역",
    "legal_compliance": "법률·컴플라이언스 심사역",
}

# Covenant category → responsible reviewer role.
_CATEGORY_REVIEWER = {
    "sales": "market_stress_tester",
    "leverage": "credit_reviewer",
    "coverage": "credit_reviewer",
    "completion": "construction_risk",
    "repayment": "credit_reviewer",
    "equity": "credit_reviewer",
    "waterfall": "credit_reviewer",
    "cost": "construction_risk",
}

# EOD code → responsible reviewer role.
_EOD_REVIEWER = {
    "EOD_TARGET_SALES_MISS": "market_stress_tester",
    "EOD_LTV_BREACH": "credit_reviewer",
    "EOD_DSCR_MISS": "credit_reviewer",
    "EOD_COMPLETION_FAILURE": "construction_risk",
    "EOD_COST_OVERRUN_UNCURED": "construction_risk",
    "EOD_DISCOUNT_LIMIT": "market_stress_tester",
}


def _reviewer(role: str) -> Dict[str, str]:
    return {"role": role, "label": _REVIEWER_LABELS.get(role, role)}


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _ceil_pct(rate: Optional[float]) -> Optional[float]:
    """Round a 0..1 rate up to the next whole percent (covenants are stated in %)."""
    if rate is None or rate <= 0:
        return None
    return math.ceil(rate * 100) / 100


def _locator(field: str, engine_key: str = "feasibility") -> Dict[str, Any]:
    return {"engineKey": engine_key, "field": field, "sourceType": "calculation_run"}


class CovenantEodAgent:
    """Build covenant + EOD definitions from a deterministic feasibility analysis."""

    def build(
        self,
        analysis: Dict[str, Any],
        *,
        asset_type: str = "other",
        pf_structure: Optional[List[Dict[str, Any]]] = None,
        policy: Optional[Dict[str, Any]] = None,
        project_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        policy = policy or {}
        pf_structure = pf_structure or []
        returns = analysis.get("developerReturns", {}) or {}
        pf = analysis.get("pfReadiness", {}) or {}
        costs = analysis.get("costs", {}) or {}
        revenue = analysis.get("revenue", {}) or {}

        exit_sale_rate = _num(pf.get("exitSaleRate"))
        break_even_rate = _num(returns.get("breakEvenSaleRate"))
        ltv_by_revenue = _num(pf.get("ltvByRevenue"))
        ltc_by_cost = _num(pf.get("ltcByCost"))
        required_equity = _num(pf.get("requiredEquity"))
        construction_cost = _num(costs.get("constructionCost"))
        total_cost = _num(costs.get("totalCost"))
        total_revenue = _num(revenue.get("totalRevenue"))
        ltv_threshold = _num(
            (analysis.get("assetCalculationProfiles", {}) or {})
            .get("profiles", {})
            .get(asset_type, {})
            .get("ltvRiskThreshold")
        )

        covenants: List[Dict[str, Any]] = []
        eod: List[Dict[str, Any]] = []
        notes: List[Dict[str, Any]] = []

        # 1) 목표 분양률 (target cumulative sales rate) — from exit sale rate.
        target_rate = _ceil_pct(exit_sale_rate) or _ceil_pct(break_even_rate)
        if target_rate is not None:
            basis_field = "pfReadiness.exitSaleRate" if exit_sale_rate else "developerReturns.breakEvenSaleRate"
            covenants.append({
                "code": "TARGET_SALES_RATE",
                "category": "sales",
                "label": "목표 분양률",
                "monitoringMetric": "누적 분양률",
                "thresholdType": "min",
                "thresholdValue": target_rate,
                "unit": "ratio",
                "basis": "대출금 회수 필요 분양률(Exit 분양률) 기준",
                "breachAction": "분양수입금 상환계좌 적립(Cash Sweep) 강화 및 할인분양 검토",
                "status": "active",
                "sourceLocator": _locator(basis_field),
            })
            eod.append({
                "code": "EOD_TARGET_SALES_MISS",
                "label": "목표 분양률 미달",
                "trigger": "약정 시점 누적 분양률이 목표 분양률 미달",
                "thresholdValue": target_rate,
                "unit": "ratio",
                "severity": "high",
                "cure": "할인분양·추가담보·Equity 선투입 등 보완조치",
                "sourceLocator": _locator(basis_field),
            })
        else:
            notes.append({"code": "TARGET_SALES_RATE", "reason": "Exit 분양률·손익분기 분양률 산출 불가(수입/사업비 근거 부족)"})

        # 2) LTV 상한 — asset-profile risk threshold; current vs threshold.
        if ltv_threshold is not None:
            cov = {
                "code": "LTV_CEILING",
                "category": "leverage",
                "label": "LTV 상한",
                "monitoringMetric": "LTV(총수입 대비)",
                "thresholdType": "max",
                "thresholdValue": ltv_threshold,
                "unit": "ratio",
                "basis": f"자산유형({asset_type}) 리스크 임계 LTV",
                "breachAction": "추가담보·상환재원 보강 또는 인출 제한",
                "status": "active",
                "sourceLocator": {"engineKey": "asset_profile", "field": f"profiles.{asset_type}.ltvRiskThreshold", "sourceType": "policy"},
            }
            if ltv_by_revenue is not None:
                cov["currentValue"] = ltv_by_revenue
                cov["breached"] = ltv_by_revenue > ltv_threshold
            covenants.append(cov)
            eod.append({
                "code": "EOD_LTV_BREACH",
                "label": "LTV 상한 초과",
                "trigger": "LTV(총수입 대비)가 약정 상한 초과",
                "thresholdValue": ltv_threshold,
                "unit": "ratio",
                "severity": "high",
                "cure": "추가담보 제공 또는 선순위 축소",
                "sourceLocator": _locator("pfReadiness.ltvByRevenue"),
            })

        # 3) LTC 상한 — lender policy default (labelled as policy, not deal evidence).
        ltc_ceiling = _num(policy.get("ltcCeiling")) or DEFAULT_LTC_CEILING
        cov_ltc = {
            "code": "LTC_CEILING",
            "category": "leverage",
            "label": "LTC 상한",
            "monitoringMetric": "LTC(총사업비 대비)",
            "thresholdType": "max",
            "thresholdValue": ltc_ceiling,
            "unit": "ratio",
            "basis": "대주단 표준 정책(정책 기본값)",
            "breachAction": "Equity 추가 선투입 또는 사업비 조정",
            "status": "active",
            "sourceLocator": {"engineKey": "lender_policy", "field": "ltcCeiling", "sourceType": "lender_policy_default"},
        }
        if ltc_by_cost is not None:
            cov_ltc["currentValue"] = ltc_by_cost
            cov_ltc["breached"] = ltc_by_cost > ltc_ceiling
        covenants.append(cov_ltc)

        # 4) 최소 DSCR — income assets only; requires NOI evidence to be an active covenant.
        if asset_type in _INCOME_ASSET_TYPES:
            noi = _num((analysis.get("logisticsAnalysis") or {}).get("noi")) or _num(policy.get("noi"))
            min_dscr = _num(policy.get("minDscr")) or DEFAULT_MIN_DSCR
            if noi is not None:
                covenants.append({
                    "code": "MIN_DSCR",
                    "category": "coverage",
                    "label": "최소 DSCR",
                    "monitoringMetric": "DSCR",
                    "thresholdType": "min",
                    "thresholdValue": min_dscr,
                    "unit": "x",
                    "basis": "임대형 자산 운영 상환능력(정책 기본값)",
                    "breachAction": "DSRA 적립 강화 또는 배당 제한",
                    "status": "active",
                    "sourceLocator": {"engineKey": "lender_policy", "field": "minDscr", "sourceType": "lender_policy_default"},
                })
                eod.append({
                    "code": "EOD_DSCR_MISS",
                    "label": "DSCR 미달",
                    "trigger": "약정 DSCR 미달",
                    "thresholdValue": min_dscr,
                    "unit": "x",
                    "severity": "high",
                    "cure": "DSRA 충당 또는 상환",
                    "sourceLocator": {"engineKey": "lender_policy", "field": "minDscr", "sourceType": "lender_policy_default"},
                })
            else:
                notes.append({"code": "MIN_DSCR", "reason": "임대형 자산 NOI 근거 부족 — DSCR 커버넌트는 근거 확보 후 확정"})

        # 5) 책임준공 (responsible completion) — presence covenant for construction PF.
        has_construction = bool(construction_cost)
        covenants.append({
            "code": "RESPONSIBLE_COMPLETION",
            "category": "completion",
            "label": "시공사 책임준공 확약",
            "monitoringMetric": "공정률·준공",
            "thresholdType": "presence",
            "thresholdValue": None,
            "unit": None,
            "basis": "시공사 책임준공(미이행 시 채무인수)",
            "breachAction": "채무인수 또는 시공사 교체",
            "status": "active" if has_construction else "needs_evidence",
            "sourceLocator": _locator("costs.constructionCost") if has_construction else {"engineKey": "feasibility", "field": "costs.constructionCost", "sourceType": "missing"},
        })
        eod.append({
            "code": "EOD_COMPLETION_FAILURE",
            "label": "책임준공 미이행",
            "trigger": "약정 공정률·준공기한 미달 및 책임준공 미이행",
            "thresholdValue": None,
            "unit": None,
            "severity": "critical",
            "cure": "시공사 채무인수 실행",
            "sourceLocator": _locator("costs.constructionCost"),
        })

        # 6) Cash Sweep 개시 분양률 — proceeds swept to repayment reserve from target rate.
        if target_rate is not None:
            covenants.append({
                "code": "CASH_SWEEP_TRIGGER",
                "category": "repayment",
                "label": "분양수입금 상환계좌 적립(Cash Sweep) 개시",
                "monitoringMetric": "누적 분양률",
                "thresholdType": "min",
                "thresholdValue": target_rate,
                "unit": "ratio",
                "basis": "목표 분양률 도달 시 초과 분양수입 상환계좌 적립",
                "breachAction": "상환계좌 적립 미이행 시 인출 중단",
                "status": "active",
                "sourceLocator": _locator("pfReadiness.exitSaleRate"),
            })

        # 7) Equity 선투입 — required equity as a drawdown condition precedent.
        if required_equity is not None and required_equity > 0:
            covenants.append({
                "code": "EQUITY_FIRST_IN",
                "category": "equity",
                "label": "Equity 선투입",
                "monitoringMetric": "Equity 투입액",
                "thresholdType": "min",
                "thresholdValue": required_equity,
                "unit": "KRW",
                "basis": "총사업비 대비 필요 자기자본(PF 인출 선행조건)",
                "breachAction": "선행조건 미충족 시 인출 불가",
                "status": "active",
                "sourceLocator": _locator("pfReadiness.requiredEquity"),
            })

        # 8) 자금집행순서 / 상환계좌 배분 — from tranche priorities.
        execution_order = self._execution_order(pf_structure)
        if execution_order:
            covenants.append({
                "code": "FUND_EXECUTION_ORDER",
                "category": "waterfall",
                "label": "상환계좌 자금집행순서",
                "monitoringMetric": "상환계좌 배분",
                "thresholdType": "order",
                "thresholdValue": execution_order,
                "unit": None,
                "basis": "Tranche 상환순위 기준 자금집행순서",
                "breachAction": "집행순서 위반 시 EOD 검토",
                "status": "active",
                "sourceLocator": {"engineKey": "pf_structure", "field": "tranches.priority", "sourceType": "project_data"},
            })

        # 9) 공사비 초과 충당 — construction cost overrun buffer → equity top-up.
        buffer = _num(policy.get("costOverrunBuffer"))
        if buffer is None:
            buffer = DEFAULT_COST_OVERRUN_BUFFER
        if construction_cost is not None and construction_cost > 0:
            ceiling = round(construction_cost * (1 + buffer))
            covenants.append({
                "code": "COST_OVERRUN_BUFFER",
                "category": "cost",
                "label": "공사비 초과 충당",
                "monitoringMetric": "실투입 공사비",
                "thresholdType": "max",
                "thresholdValue": ceiling,
                "unit": "KRW",
                "basis": f"도급 공사비 대비 {int(buffer * 100)}% 초과분은 Equity 선투입으로 충당",
                "breachAction": "초과분 Equity 추가 선투입",
                "status": "active",
                "sourceLocator": _locator("costs.constructionCost"),
            })
            eod.append({
                "code": "EOD_COST_OVERRUN_UNCURED",
                "label": "공사비 초과 미충당",
                "trigger": "공사비 초과분이 Equity로 충당되지 않음",
                "thresholdValue": ceiling,
                "unit": "KRW",
                "severity": "high",
                "cure": "Equity 추가 선투입 또는 사업비 조정",
                "sourceLocator": _locator("costs.constructionCost"),
            })

        # Discount sale EOD (always a standard main-PF trigger when sales-driven).
        if target_rate is not None:
            eod.append({
                "code": "EOD_DISCOUNT_LIMIT",
                "label": "할인분양 한도 초과",
                "trigger": "대주 동의 없는 할인분양 또는 약정 할인율 한도 초과",
                "thresholdValue": None,
                "unit": None,
                "severity": "medium",
                "cure": "대주 사전동의 및 상환계획 재수립",
                "sourceLocator": _locator("pfReadiness.exitSaleRate"),
            })

        # Attach the responsible reviewer role to each covenant / EOD condition.
        for cov in covenants:
            cov["responsibleReviewer"] = _reviewer(_CATEGORY_REVIEWER.get(cov.get("category", ""), "credit_reviewer"))
        for item in eod:
            item["responsibleReviewer"] = _reviewer(_EOD_REVIEWER.get(item.get("code", ""), "credit_reviewer"))

        summary = {
            "covenantCount": len(covenants),
            "activeCovenantCount": sum(1 for c in covenants if c.get("status") == "active"),
            "needsEvidenceCount": sum(1 for c in covenants if c.get("status") == "needs_evidence"),
            "eodConditionCount": len(eod),
            "breachedCovenantCodes": [c["code"] for c in covenants if c.get("breached")],
            "reviewerRoles": sorted({c["responsibleReviewer"]["role"] for c in covenants}),
        }

        result = {
            "schemaVersion": COVENANT_EOD_SCHEMA_VERSION,
            "assetType": asset_type,
            "summary": summary,
            "covenants": covenants,
            "eodConditions": eod,
            "dataQualityNotes": notes,
            "termSheetCovenants": self._term_sheet_covenants(covenants, eod, execution_order),
            "inputsPresent": {
                "exitSaleRate": exit_sale_rate is not None,
                "ltvByRevenue": ltv_by_revenue is not None,
                "ltcByCost": ltc_by_cost is not None,
                "requiredEquity": required_equity is not None,
                "constructionCost": construction_cost is not None,
                "totalRevenue": total_revenue is not None,
                "totalCost": total_cost is not None,
            },
        }
        result["covenantSheet"] = self._covenant_sheet(result, project_name=project_name)
        return result

    @staticmethod
    def _fmt_threshold(value: Any, unit: Any) -> str:
        if value is None:
            return "—"
        if isinstance(value, list):
            return " → ".join(str(v) for v in value)
        if isinstance(value, str):
            return value
        if unit == "ratio":
            return f"{value * 100:.1f}%"
        if unit == "KRW":
            return f"{value / 100_000_000:,.1f}억원"
        if unit == "x":
            return f"{value:.2f}x"
        return f"{value:,.2f}"

    def _covenant_sheet(self, result: Dict[str, Any], *, project_name: Optional[str] = None) -> Dict[str, Any]:
        """Korean lender-facing Covenant Sheet (markdown) built from the derived result."""
        lines: List[str] = []
        title = f"# Covenant Sheet — {project_name}" if project_name else "# Covenant Sheet"
        lines.append(title)
        lines.append("")
        lines.append("> 결정론적 사업성·PF 계산값에서 도출한 여신 커버넌트 및 기한이익상실(EOD) 조건입니다. "
                     "근거가 없는 항목은 '근거 필요'로 표기하며 임의로 수치를 생성하지 않습니다.")
        lines.append("")
        lines.append("## 1. 재무·사업 커버넌트")
        lines.append("")
        lines.append("| 커버넌트 | 기준 | 임계값 | 위반 시 조치 | 담당 | 상태 |")
        lines.append("|---|---|---|---|---|---|")
        for cov in result.get("covenants", []):
            threshold = self._fmt_threshold(cov.get("thresholdValue"), cov.get("unit"))
            status = "적용" if cov.get("status") == "active" else "근거 필요"
            reviewer = (cov.get("responsibleReviewer") or {}).get("label", "—")
            lines.append(
                f"| {cov.get('label','—')} | {cov.get('basis','—')} | {threshold} | "
                f"{cov.get('breachAction','—')} | {reviewer} | {status} |"
            )
        lines.append("")
        lines.append("## 2. 기한이익상실(EOD) 조건")
        lines.append("")
        lines.append("| EOD 조건 | 트리거 | 심각도 | 보완조치 | 담당 |")
        lines.append("|---|---|---|---|---|")
        severity_ko = {"critical": "치명", "high": "높음", "medium": "보통", "low": "낮음"}
        for item in result.get("eodConditions", []):
            reviewer = (item.get("responsibleReviewer") or {}).get("label", "—")
            sev = severity_ko.get(item.get("severity", ""), item.get("severity", "—"))
            lines.append(
                f"| {item.get('label','—')} | {item.get('trigger','—')} | {sev} | "
                f"{item.get('cure','—')} | {reviewer} |"
            )
        notes = result.get("dataQualityNotes", [])
        if notes:
            lines.append("")
            lines.append("## 3. 근거 필요 항목")
            lines.append("")
            for note in notes:
                lines.append(f"- {note.get('reason','—')}")
        lines.append("")
        return {"schemaVersion": "realytics-covenant-sheet-v1", "projectName": project_name, "markdown": "\n".join(lines)}

    @staticmethod
    def _execution_order(pf_structure: List[Dict[str, Any]]) -> List[str]:
        priority_rank = {"senior": 1, "선순위": 1, "middle": 2, "mezzanine": 2, "중순위": 2, "junior": 3, "후순위": 3}

        def rank(tranche: Dict[str, Any]) -> int:
            raw = str(tranche.get("priority") or tranche.get("name") or tranche.get("tranche") or "").lower()
            for key, value in priority_rank.items():
                if key in raw:
                    return value
            num = _num(tranche.get("priority"))
            return int(num) if num is not None else 9

        ordered = sorted([t for t in pf_structure if isinstance(t, dict)], key=rank)
        labels: List[str] = []
        for tranche in ordered:
            name = tranche.get("name") or tranche.get("tranche") or "Tranche"
            labels.append(str(name))
        if labels:
            labels.append("시공사")
        return labels

    @staticmethod
    def _term_sheet_covenants(
        covenants: List[Dict[str, Any]],
        eod: List[Dict[str, Any]],
        execution_order: List[str],
    ) -> Dict[str, Any]:
        """Shape consumable by TermSheetAgent covenant overrides."""
        out: Dict[str, Any] = {}
        if eod:
            out["eod"] = [f"{item['label']}: {item['trigger']}" for item in eod]
        if execution_order:
            out["execution_order"] = execution_order
        discount = [c for c in covenants if c["code"] == "TARGET_SALES_RATE"]
        if discount:
            rate = discount[0]["thresholdValue"]
            out["discount_triggers"] = [f"목표 분양률 {round(rate * 100)}% 미달 시 할인분양·상환계좌 적립 검토"]
        sweep = [c for c in covenants if c["code"] == "CASH_SWEEP_TRIGGER"]
        if sweep:
            out["revenue_distribution"] = (
                f"목표 분양률 {round(sweep[0]['thresholdValue'] * 100)}% 도달 시 초과 분양수입금은 "
                "상환계좌에 적립하여 선순위부터 순차 상환한다."
            )
        return out
