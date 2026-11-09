"""Multi-asset feasibility comparison.

Runs the deterministic DevelopmentFeasibilityAgent for the SAME land under
several asset-type scenarios and returns a side-by-side comparison matrix,
a deterministic ranking, and honest data-quality notes.

Invariants (AGENTS.md §1):
- Deterministic & evidence-first: every scenario is a re-run of the deterministic
  feasibility agent under scenario overrides. No LLM, no invented numbers.
- No fabrication: a scenario without a revenue/cost basis is NOT ranked; it is
  surfaced as ``insufficient_data`` with the exact missing fields. Missing stays
  missing.
"""

from __future__ import annotations

from typing import Any, Dict, List

from agents.development_feasibility_agent import ASSET_CALCULATION_PROFILES
from use_cases.common import deep_merge
from use_cases.development_feasibility import analyze_development_feasibility_use_case

COMPARISON_SCHEMA_VERSION = "realytics-asset-feasibility-comparison-v1"

# Hard cap so an over-long scenario list cannot fan out unbounded feasibility runs.
MAX_SCENARIOS = 12

_ASSET_TYPE_LABELS_KO = {
    "apartment": "아파트",
    "officetel": "오피스텔",
    "commercial": "상업시설",
    "knowledge_industry_center": "지식산업센터",
    "mixed_use": "복합개발",
    "logistics": "물류센터",
    "lodging": "생활숙박/호텔",
    "other": "기타",
}


def _normalize_asset_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in ASSET_CALCULATION_PROFILES:
        return value
    if value in {"mixed_use_residential", "complex", "복합"}:
        return "mixed_use"
    if "officetel" in value or "오피스텔" in value:
        return "officetel"
    if "apartment" in value or "아파트" in value or value == "residential":
        return "apartment"
    if "knowledge" in value or "지산" in value or "지식산업" in value:
        return "knowledge_industry_center"
    if "commercial" in value or "retail" in value or "상업" in value or "상가" in value:
        return "commercial"
    if "logistic" in value or "물류" in value:
        return "logistics"
    if "lodging" in value or "serviced" in value or "생활숙박" in value or "호텔" in value:
        return "lodging"
    return "other" if value else ""


def _default_asset_types(project_data: Dict[str, Any]) -> List[str]:
    """Distinct asset types already present in the project state."""
    types: List[str] = []
    product_mix = project_data.get("product_mix") or project_data.get("productMix") or []
    for item in product_mix:
        if isinstance(item, dict):
            resolved = _normalize_asset_type(
                item.get("productType") or item.get("asset_type") or item.get("type")
            )
            if resolved:
                types.append(resolved)
    project_type = _normalize_asset_type(project_data.get("project", {}).get("type"))
    if project_type:
        types.append(project_type)
    # Preserve first-seen order, drop duplicates and empties.
    return list(dict.fromkeys(t for t in types if t)) or ["other"]


def _scenario_override(scenario: Dict[str, Any], asset_type: str) -> Dict[str, Any]:
    """Build the override applied on top of project_data for one scenario.

    The asset type is forced onto both ``project.type`` and a single-item
    ``product_mix`` so the agent's asset-profile summary resolves to exactly this
    asset. Any caller-supplied ``overrides`` (sales/cost assumptions, architectural
    plan, feasibility_input, pf_structure) win over these defaults.
    """
    override: Dict[str, Any] = {
        "project": {"type": asset_type},
        "product_mix": [{"productType": asset_type}],
    }
    caller_override = scenario.get("overrides")
    if isinstance(caller_override, dict):
        override = deep_merge(override, caller_override)
    return override


def _rankable(row: Dict[str, Any]) -> bool:
    return bool(row.get("totalRevenue", 0) > 0 and row.get("totalCost", 0) > 0)


def _extract_row(scenario: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    revenue = analysis.get("revenue", {}) or {}
    costs = analysis.get("costs", {}) or {}
    returns = analysis.get("developerReturns", {}) or {}
    pf = analysis.get("pfReadiness", {}) or {}
    program = analysis.get("architecturalProgram", {}) or {}
    quality = analysis.get("dataQuality", {}) or {}
    profile_summary = analysis.get("assetCalculationProfiles", {}) or {}

    asset_type = scenario["assetType"]
    profile = (profile_summary.get("profiles", {}) or {}).get(
        asset_type, ASSET_CALCULATION_PROFILES.get(asset_type, ASSET_CALCULATION_PROFILES["other"])
    )
    ltv_by_revenue = pf.get("ltvByRevenue", 0) or 0
    ltv_threshold = profile.get("ltvRiskThreshold", 0) or 0

    row: Dict[str, Any] = {
        "scenarioId": scenario["scenarioId"],
        "label": scenario["label"],
        "assetType": asset_type,
        "assetTypeLabel": _ASSET_TYPE_LABELS_KO.get(asset_type, asset_type),
        "revenueMode": profile.get("revenueMode"),
        "recoveryHaircut": profile.get("recoveryHaircut"),
        "ltvRiskThreshold": ltv_threshold,
        "totalRevenue": revenue.get("totalRevenue", 0) or 0,
        "totalCost": costs.get("totalCost", 0) or 0,
        "grossFloorAreaSqm": program.get("grossFloorAreaSqm", 0) or 0,
        "saleableAreaSqm": program.get("saleableAreaSqm", 0) or 0,
        "developmentProfit": returns.get("developmentProfit", 0) or 0,
        "profitMargin": returns.get("profitMargin", 0) or 0,
        "profitOnCost": returns.get("profitOnCost", 0) or 0,
        "breakEvenSaleRate": returns.get("breakEvenSaleRate", 0) or 0,
        "recommendedPfLimit": pf.get("recommendedPfLimit", 0) or 0,
        "ltvByRevenue": ltv_by_revenue,
        "ltcByCost": pf.get("ltcByCost", 0) or 0,
        "exitSaleRate": pf.get("exitSaleRate", 0) or 0,
        "pfReadinessStatus": pf.get("status"),
        "isPfReviewReady": bool(pf.get("isPfReviewReady")),
        "missingFields": list(quality.get("missingFields", []) or []),
        "ltvExceedsThreshold": bool(ltv_threshold and ltv_by_revenue and ltv_by_revenue > ltv_threshold),
    }
    row["status"] = "comparable" if _rankable(row) else "insufficient_data"
    return row


def _normalize_scenarios(
    project_data: Dict[str, Any],
    scenarios: List[Dict[str, Any]] | None,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if scenarios:
        for index, scenario in enumerate(scenarios[:MAX_SCENARIOS], start=1):
            if not isinstance(scenario, dict):
                continue
            asset_type = _normalize_asset_type(scenario.get("assetType") or scenario.get("asset_type"))
            if not asset_type:
                asset_type = "other"
            label = scenario.get("label") or _ASSET_TYPE_LABELS_KO.get(asset_type, asset_type)
            scenario_id = scenario.get("scenarioId") or scenario.get("id") or f"scenario-{index}"
            normalized.append(
                {
                    "scenarioId": str(scenario_id),
                    "label": str(label),
                    "assetType": asset_type,
                    "overrides": scenario.get("overrides") if isinstance(scenario.get("overrides"), dict) else {},
                }
            )
    else:
        for asset_type in _default_asset_types(project_data)[:MAX_SCENARIOS]:
            normalized.append(
                {
                    "scenarioId": f"asset-{asset_type}",
                    "label": _ASSET_TYPE_LABELS_KO.get(asset_type, asset_type),
                    "assetType": asset_type,
                    "overrides": {},
                }
            )
    return normalized


def compare_asset_feasibility_use_case(
    project_data: Dict[str, Any],
    scenarios: List[Dict[str, Any]] | None = None,
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Compare developer feasibility across asset-type scenarios for the same land."""
    normalized = _normalize_scenarios(project_data, scenarios)

    land = project_data.get("land", {}) or {}
    regulation = project_data.get("regulation", {}) or {}
    base_program = {
        "landAreaSqm": land.get("area_sqm", 0) or 0,
        "appliedFar": regulation.get("applied_far", 0) or 0,
        "appliedBcr": regulation.get("applied_bcr", 0) or 0,
        "address": land.get("address"),
    }

    rows: List[Dict[str, Any]] = []
    for scenario in normalized:
        override = _scenario_override(scenario, scenario["assetType"])
        try:
            analysis = analyze_development_feasibility_use_case(project_data, override)
            rows.append(_extract_row(scenario, analysis))
        except Exception as exc:  # noqa: BLE001 - never let one bad scenario break the matrix
            rows.append(
                {
                    "scenarioId": scenario["scenarioId"],
                    "label": scenario["label"],
                    "assetType": scenario["assetType"],
                    "assetTypeLabel": _ASSET_TYPE_LABELS_KO.get(scenario["assetType"], scenario["assetType"]),
                    "status": "error",
                    "error": "사업성 시나리오 계산에 실패했습니다. 입력과 근거를 확인해 주세요.",
                    "missingFields": [],
                }
            )

    comparable = [row for row in rows if row.get("status") == "comparable"]
    ranked = sorted(
        comparable,
        key=lambda row: (row.get("profitMargin", 0), row.get("profitOnCost", 0)),
        reverse=True,
    )
    for position, row in enumerate(ranked, start=1):
        row["rank"] = position

    recommendation: Dict[str, Any] | None = None
    if ranked:
        top = ranked[0]
        recommendation = {
            "scenarioId": top["scenarioId"],
            "label": top["label"],
            "assetType": top["assetType"],
            "assetTypeLabel": top["assetTypeLabel"],
            "profitMargin": top["profitMargin"],
            "developmentProfit": top["developmentProfit"],
            "basis": "ranked_by_profit_margin",
            "note": (
                "동일 토지·규제 조건에서 입력 가정 기준 개발이익률이 가장 높은 시나리오입니다. "
                "가정(분양가·공사비)이 시나리오마다 다르면 결과가 달라지므로 근거 확인 후 판단하세요."
            ),
            "ltvExceedsThreshold": top.get("ltvExceedsThreshold", False),
        }

    data_quality_notes: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("status") == "insufficient_data":
            reasons: List[str] = []
            if not row.get("totalRevenue", 0):
                reasons.append("총수입(분양가/매출) 미확보")
            if not row.get("totalCost", 0):
                reasons.append("총사업비 미확보")
            data_quality_notes.append(
                {
                    "scenarioId": row["scenarioId"],
                    "label": row["label"],
                    "status": "insufficient_data",
                    "reasons": reasons or ["비교 가능한 수치 부족"],
                    "missingFields": row.get("missingFields", []),
                }
            )
        elif row.get("status") == "error":
            data_quality_notes.append(
                {
                    "scenarioId": row["scenarioId"],
                    "label": row["label"],
                    "status": "error",
                    "reasons": [row.get("error", "시나리오 계산 실패")],
                    "missingFields": [],
                }
            )

    return {
        "schemaVersion": COMPARISON_SCHEMA_VERSION,
        "analysisType": "multi_asset_feasibility_comparison",
        "baseProgram": base_program,
        "scenarioCount": len(rows),
        "comparableCount": len(comparable),
        "insufficientDataCount": sum(1 for row in rows if row.get("status") != "comparable"),
        "rows": rows,
        "ranking": ranked,
        "recommendation": recommendation,
        "dataQualityNotes": data_quality_notes,
        "notes": (
            "결정론적 사업성 엔진을 시나리오별로 재실행한 결과입니다. 수치를 새로 만들어내지 않으며, "
            "수입·사업비 근거가 없는 시나리오는 순위에서 제외하고 부족 항목을 그대로 표시합니다."
        ),
    }
