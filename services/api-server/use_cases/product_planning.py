"""Product planning comparison use case.

Derives the land/zoning parameters from project state and runs the deterministic
ProductPlanningAgent per asset-type scenario. Read-only / on-demand.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.product_planning_agent import PLANNING_BENCHMARKS, ProductPlanningAgent
from use_cases.asset_feasibility_comparison import _normalize_asset_type, _default_asset_types

MAX_SCENARIOS = 12


def _normalize_scenarios(
    project_data: Dict[str, Any],
    scenarios: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if scenarios:
        for index, scenario in enumerate(scenarios[:MAX_SCENARIOS], start=1):
            if not isinstance(scenario, dict):
                continue
            asset_type = _normalize_asset_type(scenario.get("assetType") or scenario.get("asset_type")) or "other"
            normalized.append({
                "assetType": asset_type,
                "overrides": scenario.get("overrides") if isinstance(scenario.get("overrides"), dict) else {},
            })
    else:
        for asset_type in _default_asset_types(project_data)[:MAX_SCENARIOS]:
            normalized.append({"assetType": asset_type, "overrides": {}})
    return normalized


def compare_product_planning_use_case(
    project_data: Dict[str, Any],
    scenarios: Optional[List[Dict[str, Any]]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    land = project_data.get("land", {}) or {}
    regulation = project_data.get("regulation", {}) or {}
    land_area_sqm = float(land.get("area_sqm") or 0) or 0.0
    applied_far = float(regulation.get("applied_far") or 0) or 0.0
    applied_bcr = float(regulation.get("applied_bcr") or 0) or 0.0
    project_name = (project_data.get("project", {}) or {}).get("name")

    result = ProductPlanningAgent().compare(
        land_area_sqm=land_area_sqm,
        applied_far=applied_far,
        applied_bcr=applied_bcr,
        scenarios=_normalize_scenarios(project_data, scenarios),
        project_name=project_name,
    )
    if not land_area_sqm or not applied_far:
        result.setdefault("dataQualityNotes", []).append(
            "토지면적 또는 용적률이 없어 연면적 기반 산정이 불가합니다 (토지·규제 자료 보강 필요)."
        )
    result["availableAssetTypes"] = [
        {"assetType": key, "label": val["label"]} for key, val in PLANNING_BENCHMARKS.items() if key != "other"
    ]
    return result
