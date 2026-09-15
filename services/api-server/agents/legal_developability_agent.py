"""Deterministic land-use and permit feasibility checks for the Legal Pod.

The agent only concludes from supplied regulation/evidence values. It never
infers a local ordinance limit from a zoning label alone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Dict

from core.schedule import schedule_event_map


OFFICIAL_LAW_SOURCES = [
    {"name": "국토의 계획 및 이용에 관한 법률", "url": "https://www.law.go.kr/법령/국토의계획및이용에관한법률", "scope": "용도지역·지구단위계획·개발행위허가"},
    {"name": "국토의 계획 및 이용에 관한 법률 시행령", "url": "https://www.law.go.kr/법령/국토의계획및이용에관한법률시행령", "scope": "용도지역별 건폐율·용적률·개발행위 기준"},
    {"name": "도시개발법", "url": "https://www.law.go.kr/법령/도시개발법", "scope": "도시개발구역·개발계획·실시계획"},
    {"name": "건축법", "url": "https://www.law.go.kr/법령/건축법", "scope": "건축허가·대지·도로·건축제한"},
]


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        if isinstance(value, dict):
            value = value.get("value")
        result = float(str(value).replace(",", "").replace("%", ""))
        return result if isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if data.get(key) not in (None, ""):
            return data[key]
    return None


class LegalDevelopabilityAgent:
    def analyze(self, project_data: Dict[str, Any], law_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
        project = project_data.get("project") if isinstance(project_data.get("project"), dict) else {}
        regulation = project_data.get("regulation") if isinstance(project_data.get("regulation"), dict) else {}
        land = project_data.get("land") if isinstance(project_data.get("land"), dict) else {}
        schedule = schedule_event_map(project_data.get("schedule"))
        zoning = _first(regulation, "zoning", "zone", "useDistrict", "use_district") or _first(land, "zoning", "zone")
        land_area = _number(_first(land, "area_sqm", "areaSqm", "landAreaSqm", "siteAreaSqm"))
        legal_far = _number(_first(regulation, "legal_far", "legalFar", "statutoryFar"))
        applied_far = _number(_first(regulation, "applied_far", "appliedFar", "plannedFar"))
        legal_bcr = _number(_first(regulation, "legal_bcr", "legalBcr", "statutoryBcr"))
        applied_bcr = _number(_first(regulation, "applied_bcr", "appliedBcr", "plannedBcr"))
        district_plan = _first(regulation, "districtUnitPlan", "district_unit_plan", "districtUnitPlanArea", "지구단위계획")
        urban_development = bool(_first(project, "urbanDevelopment", "urban_development", "urbanDevelopmentDistrict")) or "도시개발" in str(_first(project, "type", "projectType") or "")
        permit_date = _first(schedule, "permit_approval", "permitApproval", "buildingPermitDate")
        rows = [
            self._row("용도지역", zoning, "국토계획법 제36조 및 지자체 도시계획조례", "zoning", required=True),
            self._row("대지면적", land_area, "건축법상 대지·도로·건축제한 검토", "land_area", required=True, unit="㎡"),
            self._row("법정 용적률", legal_far, "국토계획법 시행령 및 지자체 도시계획조례", "legal_far", required=True, unit="%"),
            self._row("계획 용적률", applied_far, "사업 건축계획 또는 사업수지 입력", "applied_far", required=True, unit="%"),
            self._row("법정 건폐율", legal_bcr, "국토계획법 시행령 및 지자체 도시계획조례", "legal_bcr", required=True, unit="%"),
            self._row("계획 건폐율", applied_bcr, "사업 건축계획 또는 사업수지 입력", "applied_bcr", required=True, unit="%"),
            self._row("지구단위계획", district_plan, "국토계획법 제52조·제53조", "district_unit_plan", required=False),
            self._row("건축허가 일정", permit_date, "건축법", "permit_schedule", required=False),
        ]
        missing = [row["field"] for row in rows if row["required"] and row["value"] in (None, "")]
        breaches = []
        if legal_far is not None and applied_far is not None and applied_far > legal_far:
            breaches.append("계획 용적률이 확인된 법정 용적률을 초과합니다.")
        if legal_bcr is not None and applied_bcr is not None and applied_bcr > legal_bcr:
            breaches.append("계획 건폐율이 확인된 법정 건폐율을 초과합니다.")
        conditional = []
        if district_plan not in (None, "", False, "없음", "미지정"):
            conditional.append("지구단위계획 결정도서와 시행지침의 용도·높이·용적률·기반시설 조건을 대조해야 합니다.")
        if urban_development:
            conditional.append("도시개발법상 구역지정·개발계획·실시계획 및 기반시설 부담을 별도로 확인해야 합니다.")
        if not permit_date:
            conditional.append("건축허가 또는 인허가 일정 증빙이 없어 착공 가능성을 확정할 수 없습니다.")
        mcp_refs = (law_context or {}).get("references", []) if isinstance(law_context, dict) else []
        law_sources = [*OFFICIAL_LAW_SOURCES]
        if isinstance(mcp_refs, list):
            law_sources.extend({"name": item.get("title") or item.get("lawName"), "url": item.get("url"), "article": item.get("article"), "sourceType": "korean_law_mcp"} for item in mcp_refs if isinstance(item, dict) and (item.get("title") or item.get("lawName")))
        if breaches:
            status = "blocked"
        elif missing:
            status = "not_assessable"
        elif conditional:
            status = "conditional"
        else:
            status = "conditionally_feasible"
        return {
            "schemaVersion": "realytics-legal-developability-v1",
            "status": status,
            "developabilityStatus": status,
            "zoning": zoning,
            "urbanDevelopmentLawReviewRequired": urban_development,
            "districtUnitPlanReviewRequired": district_plan not in (None, "", False, "없음", "미지정"),
            "areaLawTable": rows,
            "lawSources": law_sources,
            "missingRequiredInputs": missing,
            "breaches": breaches,
            "conditionalFindings": conditional,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "sourceLocator": {"sourceType": "project_regulation", "canonicalFields": [row["canonicalField"] for row in rows]},
        }

    @staticmethod
    def _row(label: str, value: Any, legal_basis: str, canonical_field: str, *, required: bool, unit: str | None = None) -> Dict[str, Any]:
        field_path = {"land_area": "land.area_sqm", "permit_schedule": "schedule.permit_approval"}.get(canonical_field, f"regulation.{canonical_field}")
        return {"field": label, "canonicalField": field_path, "value": value, "unit": unit, "legalBasis": legal_basis, "required": required, "sourceLocator": {"sourceType": "project_input", "canonicalField": field_path}}
