"""Product planning comparison use case.

Derives the land/zoning parameters from project state and runs the deterministic
ProductPlanningAgent per asset-type scenario. Read-only / on-demand.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.product_planning_agent import PLANNING_BENCHMARKS, ProductPlanningAgent
from domains.feasibility.regulatory import build_preliminary_buildable_envelope, evaluate_program_eligibility
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
    legal_far = float(regulation.get("legal_far") or regulation.get("legalFar") or 0) or 0.0
    project_name = (project_data.get("project", {}) or {}).get("name")
    envelope = build_preliminary_buildable_envelope(project_data, declared_land_area_sqm=land_area_sqm)
    regulatory_conflicts = []
    if envelope:
        envelope_area = ((envelope.get("siteParcelSnapshot") or {}).get("totalIncludedAreaSqm"))
        if not land_area_sqm and envelope_area:
            land_area_sqm = float(envelope_area)
        envelope_legal_far = envelope.get("effectiveLegalFar")
        if envelope_legal_far is not None:
            if legal_far and abs(legal_far - float(envelope_legal_far)) > 0.000001:
                regulatory_conflicts.append({
                    "code": "TOP_LEVEL_LEGAL_FAR_DIFFERS_FROM_PARCEL_ENVELOPE",
                    "topLevelLegalFar": legal_far,
                    "parcelWeightedLegalFar": float(envelope_legal_far),
                    "resolution": "parcel_weighted_envelope_used",
                })
            legal_far = float(envelope_legal_far)
    snapshot = project_data.get("regulatoryConstraintSnapshot") or project_data.get("regulatory_constraint_snapshot") or {}
    snapshot_id = envelope.get("regulatoryConstraintSnapshotId") if envelope else (
        snapshot.get("snapshotId") or snapshot.get("snapshot_id") if isinstance(snapshot, dict) else None
    )
    # A project payload cannot attest its own approval.  Report-grade status is
    # available only when the parcel envelope has already been bound to a
    # repository-verified approval decision.
    constraint_approved = bool(envelope and envelope.get("reportGrade"))

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
    row_eligibilities = []
    for row in result.get("rows") or []:
        row_far = (float(row.get("grossFloorAreaSqm") or 0) / land_area_sqm * 100) if land_area_sqm else None
        eligibility = evaluate_program_eligibility(
            applied_far=row_far,
            legal_far=legal_far,
            constraint_snapshot_id=snapshot_id,
            constraint_approved=constraint_approved,
        )
        if envelope and envelope.get("blockingCodes"):
            eligibility["eligibility"] = "not_assessable"
            eligibility["reportGrade"] = False
            eligibility["blockingConstraintIds"] = sorted(set(
                eligibility.get("blockingConstraintIds", []) + envelope["blockingCodes"]
            ))
        row["scenarioEligibility"] = eligibility
        row_eligibilities.append(eligibility)

    official_ranked_rows = [
        row for row in result.get("rows") or []
        if (row.get("salesRevenue") or row.get("incomeValue"))
        and (row.get("scenarioEligibility") or {}).get("reportGrade") is True
    ]
    exploratory_ranked_rows = [
        row for row in result.get("rows") or []
        if (row.get("salesRevenue") or row.get("incomeValue"))
        and (row.get("scenarioEligibility") or {}).get("eligibility") in {"eligible", "conditional"}
    ]
    if official_ranked_rows:
        top = max(official_ranked_rows, key=lambda row: row.get("salesRevenue") or row.get("incomeValue") or 0)
        result["topByValue"] = {
            "assetType": top["assetType"],
            "assetTypeLabel": top["assetTypeLabel"],
            "value": top.get("salesRevenue") or top.get("incomeValue"),
            "valueKind": "salesRevenue" if top.get("salesRevenue") else "incomeValue",
            "note": "법적 부적격안을 제외한 입력 가정 기준 산출가치입니다. reportGrade 상태를 함께 확인해야 합니다.",
        }
    else:
        result["topByValue"] = None
    if exploratory_ranked_rows:
        exploratory_top = max(
            exploratory_ranked_rows,
            key=lambda row: row.get("salesRevenue") or row.get("incomeValue") or 0,
        )
        result["exploratoryTopByValue"] = {
            "assetType": exploratory_top["assetType"],
            "assetTypeLabel": exploratory_top["assetTypeLabel"],
            "value": exploratory_top.get("salesRevenue") or exploratory_top.get("incomeValue"),
            "valueKind": "salesRevenue" if exploratory_top.get("salesRevenue") else "incomeValue",
            "reportGrade": False,
        }
    else:
        result["exploratoryTopByValue"] = None
    result["rankedPricedCount"] = len(official_ranked_rows)
    result["exploratoryPricedCount"] = len(exploratory_ranked_rows)
    result["buildableEnvelope"] = envelope
    result["scenarioEligibility"] = evaluate_program_eligibility(
        applied_far=applied_far,
        legal_far=legal_far,
        constraint_snapshot_id=snapshot_id,
        constraint_approved=constraint_approved,
    )
    if envelope and envelope.get("blockingCodes"):
        result["scenarioEligibility"]["eligibility"] = "not_assessable"
        result["scenarioEligibility"]["reportGrade"] = False
        result["scenarioEligibility"]["blockingConstraintIds"] = sorted(set(
            result["scenarioEligibility"].get("blockingConstraintIds", []) + envelope["blockingCodes"]
        ))
    if row_eligibilities and all(
        item["eligibility"] in {"ineligible", "not_assessable"}
        for item in row_eligibilities
    ):
        result["recommendationStatus"] = "blocked"
    elif any(item.get("reportGrade") for item in row_eligibilities):
        result["recommendationStatus"] = "report_grade"
    else:
        result["recommendationStatus"] = "exploratory"
    result["regulatoryConflicts"] = regulatory_conflicts
    result["availableAssetTypes"] = [
        {"assetType": key, "label": val["label"]} for key, val in PLANNING_BENCHMARKS.items() if key != "other"
    ]
    return result
