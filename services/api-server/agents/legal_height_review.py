"""Parcel-based inputs for preliminary building height review."""

from __future__ import annotations

import re
from math import isfinite
from typing import Any

from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows, parcel_pnu


_DISTRICT_PLAN_NONE = {"미지정", "없음", "해당없음", "미해당", "미확인", "확인불가", "none", "unknown"}
_SIDO_BY_PNU_PREFIX = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시",
    "28": "인천광역시", "29": "광주광역시", "30": "대전광역시",
    "31": "울산광역시", "36": "세종특별자치시", "41": "경기도",
    "43": "충청북도", "44": "충청남도", "46": "전라남도",
    "47": "경상북도", "48": "경상남도", "50": "제주특별자치도",
    "51": "강원특별자치도", "52": "전북특별자치도",
}
_REGION_RE = re.compile(
    r"^(?P<sido>" + "|".join(_SIDO_BY_PNU_PREFIX.values()) + r")"
    r"(?:\s+(?P<city>[가-힣]{1,7}[시군]))?(?:\s|$)"
)


def _height_m(value: Any) -> float | None:
    if isinstance(value, dict):
        if value.get("unit") not in (None, "", "m", "M", "미터"):
            return None
        value = value.get("value")
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) and number > 0 else None


def _district_plan(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    label = value.strip()
    return label if label and "".join(label.split()).lower() not in _DISTRICT_PLAN_NONE else None


def _height_designation(value: Any) -> bool:
    return isinstance(value, str) and any(
        term in "".join(value.split()) for term in ("지구단위계획", "고도지구")
    )


def _included(parcel: dict[str, Any]) -> bool:
    return not (
        parcel.get("excludedFromProject") is True
        or parcel.get("excluded_from_project") is True
        or parcel.get("includedInProject") is False
        or parcel.get("included_in_project") is False
    )


def ordinance_jurisdiction(project_data: dict[str, Any]) -> str | None:
    """Identify one search jurisdiction without putting site identity in queries."""
    land = project_data.get("land")
    land = land if isinstance(land, dict) else {}
    project = project_data.get("project")
    project = project if isinstance(project, dict) else {}
    pnus = [
        pnu for parcel in canonical_project_parcel_rows(project_data)
        if _included(parcel) and (pnu := parcel_pnu(parcel))
    ]
    if not pnus and (representative_pnu := parcel_pnu(land)):
        pnus = [representative_pnu]
    prefixes = {pnu[:5] for pnu in pnus}
    if len(prefixes) > 1:
        return None
    pnu_sido = _SIDO_BY_PNU_PREFIX.get(pnus[0][:2]) if pnus else None
    address = land.get("address") or project.get("address") or ""
    region = _REGION_RE.match(address.strip()) if isinstance(address, str) else None
    if pnu_sido and region and region["sido"] != pnu_sido:
        return None
    sido = pnu_sido or (region["sido"] if region else None)
    if sido and sido.endswith("시"):
        return sido
    return region["city"] if region else None


def build_height_review(project_data: dict[str, Any]) -> dict[str, Any]:
    regulation = project_data.get("regulation")
    regulation = regulation if isinstance(regulation, dict) else {}
    planned = _height_m(regulation.get("plannedHeightM"))
    candidate = _height_m(regulation.get("maxHeightM"))
    project_plan = _district_plan(
        regulation.get("districtUnitPlan") or regulation.get("district_unit_plan")
        or regulation.get("districtUnitPlanArea") or regulation.get("지구단위계획")
    )
    parcels = []
    for index, parcel in enumerate(canonical_project_parcel_rows(project_data), start=1):
        if not _included(parcel):
            continue
        planning = parcel.get("landUsePlanning") or parcel.get("land_use_planning")
        planning = planning if isinstance(planning, dict) else {}
        zones = planning.get("landUseZones", planning.get("land_use_zones"))
        zoning = planning.get("zoning")
        plan_items = planning.get("landUsePlanItems")
        designation_evidence = [
            {
                "designationName": item["designationName"],
                "relation": item.get("conflictName"),
                "baseDate": item.get("lastUpdatedDate"),
            }
            for item in plan_items
            if isinstance(item, dict) and _height_designation(item.get("designationName"))
        ] if isinstance(plan_items, list) else []
        labels = [item["designationName"] for item in designation_evidence]
        if isinstance(zones, list):
            labels.extend(zone for zone in zones if _height_designation(zone))
        designations = list(dict.fromkeys(label.strip() for label in labels))
        district = _district_plan(planning.get("districtUnitPlan")) or next(
            (zone for zone in designations if "지구단위계획" in "".join(zone.split())), None
        )
        source_locator = planning.get("sourceLocator")
        parcel_label = parcel.get("normalizedLotNumber") or parcel.get("lotNumber") or parcel.get("addressFragment")
        parcels.append({
            "parcelIndex": index,
            "pnu": parcel_pnu(parcel) or None,
            "parcelLabel": parcel_label if isinstance(parcel_label, str) else None,
            "zoning": zoning.strip() if isinstance(zoning, str) and zoning.strip() else None,
            "landUseZones": [zone for zone in zones if isinstance(zone, str)] if isinstance(zones, list) else [],
            "districtUnitPlan": district,
            "designations": designations,
            "designationEvidence": designation_evidence,
            "sourceLocator": source_locator if isinstance(source_locator, dict) else None,
        })
    district_names = list(dict.fromkeys(
        label for label in [project_plan, *(row["districtUnitPlan"] for row in parcels)] if label
    ))
    findings = []
    if not parcels or any(not row["pnu"] for row in parcels):
        findings.append("검토 대상 토지조서와 각 필지의 PNU를 확인해야 합니다.")
    if district_names:
        findings.append("지구단위계획 결정 고시·지형도면·시행지침에서 필지별 적용 범위와 높이 기준을 확인해야 합니다.")
    else:
        findings.append("필지별 토지이용계획확인서에서 지구단위계획구역과 고도지구 해당 여부를 확인해야 합니다.")
    if any("고도지구" in "".join(label.split()) for row in parcels for label in row["designations"]):
        findings.append("고도지구 단서가 있는 필지는 도시·군관리계획 결정 고시와 도면으로 높이 기준과 적용 범위를 확인해야 합니다.")
    findings.append("관할 조례·가로구역 지정 고시와 도로·대지경계 기준을 대조해 사업에 적용되는 높이를 확인해야 합니다.")
    if planned is None:
        findings.append("건축계획의 최고 높이(m)와 높이 산정 기준이 필요합니다.")
    if candidate is not None:
        findings.append(
            "계획 높이가 입력된 검토 후보를 초과합니다. 동일한 산정 기준과 적용 범위를 확인해야 합니다."
            if planned is not None and planned > candidate
            else "입력된 높이 상한 후보의 근거 문서와 적용 범위를 확인해야 합니다."
        )
    return {
        "status": "needs_evidence",
        "jurisdictionCandidate": ordinance_jurisdiction(project_data),
        "districtPlanNames": district_names,
        "plannedHeightM": planned,
        "candidateLimitM": candidate,
        "confirmedLimitM": None,
        "parcelReviews": parcels,
        "findings": findings,
    }
