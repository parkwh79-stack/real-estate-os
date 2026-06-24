from __future__ import annotations

"""Business-friendly source attribution for provenance display.

Credit officers should see *where the data came from in real life* — 시행사 제공
사업수지, 공공데이터포털 공시지가, 감정평가서, 토지대장·등기 — not internal field
paths like ``feasibility_input.construction_cost``. This maps a canonical field +
source locator to a human source label, a readable evidence reference (Excel
sheet/cell, document page, public-data lookup), and, when the value came from an
uploaded document, a link to view it.
"""

from typing import Any, Dict, Optional

# Canonical-field domain -> real-world source label (longest prefix wins).
_DOMAIN_SOURCE = [
    ("land.pnu", "공공데이터포털 (VWorld·PNU)"),
    ("landevidence.appraisal", "감정평가서"),
    ("land.official", "공시지가 (공공데이터포털)"),
    ("officialland", "공시지가 (공공데이터포털)"),
    ("registry", "등기사항증명서"),
    ("landledger", "토지대장·토지조서"),
    ("land.lotnumber", "토지대장·토지조서"),
    ("land.landcategory", "토지대장·토지조서"),
    ("land.owner", "토지대장·등기"),
    ("land.cadastral", "토지대장·토지조서"),
    ("land.purchaseamount", "토지매매계약서 (시행사 제공)"),
    ("land.ownershipshare", "토지대장·등기"),
    ("land.address", "사업개요 (시행사 제공)"),
    ("land.area", "토지대장·토지조서"),
    ("land", "토지 자료 (시행사·공공)"),
    ("regulation", "토지이용계획·건축 인허가"),
    ("schedule", "사업계획 (시행사 제공)"),
    ("feasibility_input", "사업수지 (시행사 제공 Excel)"),
    ("feasibility", "사업수지 (시행사 제공 Excel)"),
    ("cost", "사업수지 제경비 (시행사 제공)"),
    ("cashflow", "현금흐름표 (시행사 제공 Excel)"),
    ("sales_assumptions", "분양 계획 (시행사 제공)"),
    ("marketability", "분양·시장 자료 (시행사·감정·공공)"),
    ("pricing", "분양가 자료 (시행사·시세)"),
    ("pf_structure", "금융조건 (대주 제시)"),
    ("financing", "금융조건 (대주 제시)"),
    ("credit_enhancement", "신용보강 약정 (대주·시공사)"),
    ("covenant", "금융약정서 (대주·시행사 협의)"),
    ("legal", "법률검토·인허가 서류"),
    ("eod", "금융약정서 (EOD 조항)"),
    ("real_asset", "임대차·운영 자료"),
    ("developer", "사업개요 (시행사 제공)"),
    ("contractor", "도급계약 (시공사)"),
    ("project", "사업개요 (시행사 제공)"),
]

_DEFAULT_SOURCE = "시행사 제공자료"


def _source_label(field: str) -> str:
    f = (field or "").lower().replace("_", "").replace(".", ".")
    raw = (field or "").lower()
    for prefix, label in _DOMAIN_SOURCE:
        if raw.startswith(prefix) or prefix.replace("_", "") in f:
            return label
    return _DEFAULT_SOURCE


def _locator_reference(locator: Dict[str, Any]) -> str:
    """Human-readable 'where to look' string from a source locator."""
    if not isinstance(locator, dict):
        return "—"
    sheet = locator.get("sheet") or locator.get("sheetName")
    cell = locator.get("cell")
    if sheet or cell:
        return f"Excel · {sheet or ''} {cell or ''}".strip()
    page = locator.get("pageNumber") or locator.get("page")
    if page:
        return f"업로드 문서 p.{page}"
    if locator.get("apiRowId") or (locator.get("sourceType") in {"api_row", "connector", "public_data"}):
        return "공공데이터 조회 결과"
    return "원자료 첨부"


def _document_link(locator: Dict[str, Any], project_id: Optional[str]) -> Optional[str]:
    """Download link to the uploaded source document, when identifiable."""
    if not isinstance(locator, dict) or not project_id:
        return None
    archive_id = locator.get("archiveId") or locator.get("documentId") or locator.get("originalFileHash")
    if archive_id:
        return f"/projects/{project_id}/source-documents/{archive_id}/download"
    return None


def attribute_source(
    canonical_field: Optional[str],
    source_field: Optional[str],
    locator: Optional[Dict[str, Any]],
    *,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    field = canonical_field or source_field or ""
    locator = locator or {}
    return {
        "sourceLabel": _source_label(field),
        "evidenceRef": _locator_reference(locator),
        "sourceUrl": _document_link(locator, project_id),
    }
