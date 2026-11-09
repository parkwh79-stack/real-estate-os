"""Build an evidence-aware preliminary permit checklist for the Legal Pod."""

from __future__ import annotations

from math import isfinite
from typing import Any

from core.schedule import schedule_event_map


HOUSING_APPROVAL = "housing_business_plan_approval"
BUILDING_PERMIT = "building_permit"

_USE_LABELS = {
    "housing": {
        "residential", "housing", "apartment", "apt", "single_family", "multi_family",
        "주거", "주택", "단독주택", "공동주택", "아파트", "연립주택", "다세대주택", "도시형생활주택",
    },
    "officetel": {"officetel", "오피스텔", "주거용오피스텔", "업무용오피스텔"},
    "mixed_use": {"mixed", "mixed_use", "mixed_use_residential", "complex", "복합", "복합개발", "주상복합", "주거복합"},
    "non_housing": {
        "office", "commercial", "retail", "logistics", "lodging", "hotel", "knowledge_industry_center",
        "업무시설", "사무소", "상가", "상업시설", "근린생활시설", "물류센터", "숙박시설",
        "호텔", "생활숙박시설", "지식산업센터",
    },
}
_ROUTES = {
    HOUSING_APPROVAL: {
        "code": HOUSING_APPROVAL,
        "label": "주택건설사업계획승인",
        "lawBasis": "주택법 제15조·제19조 및 주택법 시행령 제27조",
    },
    BUILDING_PERMIT: {
        "code": BUILDING_PERMIT,
        "label": "건축허가·신고",
        "lawBasis": "건축법 제11조·제14조",
    },
}
_APPROVAL_DOCUMENTS = {
    "building_permit": BUILDING_PERMIT,
    "building_permit_approval": BUILDING_PERMIT,
    "building_notice_acceptance": BUILDING_PERMIT,
    "건축허가서": BUILDING_PERMIT,
    "건축신고필증": BUILDING_PERMIT,
    "housing_business_plan_approval": HOUSING_APPROVAL,
    "housing_construction_business_plan_approval": HOUSING_APPROVAL,
    "주택건설사업계획승인서": HOUSING_APPROVAL,
}


def _use_class(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    label = "_".join(value.casefold().strip().replace("-", " ").split())
    korean_label = label.replace("_", "")
    return next(
        (classification for classification, labels in _USE_LABELS.items() if label in labels or korean_label in labels),
        "unknown",
    )


def classify_permit_use(project_data: dict[str, Any]) -> str:
    """Classify stated uses without treating zoning or a unit count as a building use."""
    project = project_data.get("project")
    project = project if isinstance(project, dict) else {}
    project_use = _use_class(project.get("type") or project.get("projectType") or project.get("assetType"))
    product_mix = project_data.get("product_mix") or project_data.get("productMix")
    product_uses = {
        _use_class(row.get("productType") or row.get("asset_type") or row.get("type"))
        for row in product_mix
        if isinstance(row, dict)
    } if isinstance(product_mix, list) else set()
    uses = {project_use, *product_uses} - {"unknown"}
    if "mixed_use" in uses or ("housing" in uses and len(uses) > 1):
        return "mixed_use"
    if "officetel" in uses:
        return "officetel"
    return next(iter(uses), "unknown")


def approval_document_route(document: dict[str, Any]) -> str | None:
    """Associate an exact approval document type with its permit route."""
    kind = document.get("documentType") or document.get("document_type")
    return _APPROVAL_DOCUMENTS.get(kind.strip().casefold()) if isinstance(kind, str) else None


def build_permit_route_review(
    project_data: dict[str, Any],
    approval_documents: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    use = classify_permit_use(project_data)
    routes = [BUILDING_PERMIT]
    checks = []
    if use in {"housing", "mixed_use", "unknown"}:
        routes.insert(0, HOUSING_APPROVAL)
        checks.append("주택의 유형과 호수·세대수, 주택법 시행령 제27조의 규모 기준 및 예외를 확인합니다.")
    if use == "mixed_use":
        checks.append("준주거지역 또는 유통상업지역을 제외한 상업지역인지, 동일 건축물의 주택이 300세대 미만이고 주택 연면적 비율이 90% 미만인지 확인합니다.")
    if use == "officetel":
        checks.append("오피스텔은 주택법 시행령 제4조의 준주택에 해당합니다. 건축법상 업무시설 용도와 해당 필지의 허용 용도를 확인합니다.")
    if use == "unknown":
        checks.insert(0, "건축계획의 용도와 주택·비주택 구성을 확인합니다.")
    checks.append("관할 허가권자가 발급한 승인서·허가서 또는 신고 수리 문서와 첨부 조건을 확인합니다.")

    schedule = schedule_event_map(project_data.get("schedule"))
    dates = (schedule.get(key) for key in ("permit_approval", "permitApproval", "buildingPermitDate"))
    planned_date = next(
        (value for value in dates if
         isinstance(value, str) and value.strip() or type(value) is int
         or isinstance(value, float) and isfinite(value)),
        None,
    )
    approval_locators = [item for item in approval_documents or [] if item["route"] in routes]
    return {
        "useClassification": use,
        "status": "requires_review",
        "routes": [_ROUTES[route].copy() for route in routes],
        "requiredChecks": checks,
        "plannedDate": planned_date,
        "approvalEvidenceStatus": "source_review_required" if approval_locators else "missing",
        "approvalDocumentLocators": approval_locators,
    }
