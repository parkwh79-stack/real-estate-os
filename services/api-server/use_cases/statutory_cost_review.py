"""Evidence-only review for development charges that need legal confirmation."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agents.legal_permit_route import classify_permit_use
from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows, parcel_pnu

STATUTORY_COST_REVIEW_VERSION = "realytics-statutory-cost-review-v1"
STATUTORY_COST_CATALOG = {
    "school_site_charge": "학교용지부담금",
    "building_artwork": "건축물 미술작품 설치·출연",
}

_LAW_TITLES = {
    "school_site_charge": "학교용지 확보 등에 관한 특례법",
    "building_artwork": "문화예술진흥법",
}
_RELEVANT_ARTICLES = {
    "school_site_charge": {"제5조", "제5조의2"},
    "building_artwork": {"제9조"},
}
_STATUTE_QUERY_PURPOSES = {
    "school_site_charge": "학교용지부담금 부과 대상, 예외 및 산정 근거 조문 확인",
    "building_artwork": "건축물 미술작품 설치 또는 출연 대상과 산정 근거 조문 확인",
}
_ARTICLE = re.compile(r"^(제\s*\d+\s*조(?:\s*의\s*\d+)?)")
_LAW_HOSTS = {"law.go.kr", "www.law.go.kr", "open.law.go.kr"}
_WORKBOOK_SUBTOTAL_CELLS = {
    "sangbong-sale-feasibility": ("cost_122", "1. 사업수지", "K122", "제세공과금"),
    "sangbong-private-rental-feasibility": ("cost_110", "1. 사업수지", "K110", "제세공과금"),
}
_BONRI_SUBTOTAL_CELL = ("levy_and_charges", "사업수지", "D52", "각종부담금")


def statutory_cost_query_plan() -> list[dict[str, str]]:
    return [
        {"tool": "search_law", "query": title, "purpose": _STATUTE_QUERY_PURPOSES[key]}
        for key, title in _LAW_TITLES.items()
    ]


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _source_date(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _whole_won(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    if isinstance(value, Decimal) and not value.is_finite():
        return None
    if not 0 <= value <= 10**16:
        return None
    integer = int(value)
    return integer if integer == value else None


def official_statutory_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.netloc.lower() not in _LAW_HOSTS:
        return None
    public_query = "&".join(
        pair for pair in parsed.query.split("&")
        if pair.partition("=")[0].lower() in {"lsiseq", "lsid", "lsefyd", "efyd", "ancno", "jo", "mst", "id"}
    )
    return urlunsplit(("https", parsed.netloc, parsed.path, public_query, ""))


def _official_law_evidence(key: str, rows: list[dict], expected_date: str | None) -> list[dict]:
    result = []
    for row in rows:
        if row.get("lawName") != _LAW_TITLES[key] or row.get("sourceAuthority") != "law_go_kr":
            continue
        public_url = official_statutory_url(row.get("url"))
        if not public_url:
            continue
        citation = _ARTICLE.match(str(row.get("article") or row.get("citation") or "").strip())
        article = re.sub(r"\s+", "", citation.group(1)) if citation else None
        effective = _source_date(row.get("verifiedEffectiveDate") or row.get("effectiveDate"))
        requested = _source_date(row.get("requestedAsOfDate"))
        version_id = row.get("resourceVersionId") or row.get("officialVersionId")
        exact = (
            row.get("verified") is True and row.get("verificationStatus") in ("source_attested", "verified_snapshot", "human_reviewed")
            and article in _RELEVANT_ARTICLES[key] and bool(version_id) and bool(row.get("contentHash"))
            and effective is not None and expected_date is not None
            and requested == expected_date and effective <= expected_date
        )
        evidence = {
            "sourceType": "official_law",
            "label": _LAW_TITLES[key] + (f" {article}" if article in _RELEVANT_ARTICLES[key] else ""),
            "status": "verified_article" if exact else "reference_only",
            "locator": {"url": public_url, "versionId": version_id, "effectiveDate": effective, "asOfDate": requested},
        }
        if evidence not in result:
            result.append(evidence)
    return sorted(result, key=lambda item: item["status"] != "verified_article")[:4]


def _workbook_subtotal(project: dict[str, Any]) -> dict | None:
    # Unapproved workbook rows are review evidence only. Use an exact profile,
    # sheet and cell so an unrelated subtotal cannot be mistaken for a levy.
    for study_key in ("canonicalFeasibilityStudyV2", "feasibilityStudyV2", "feasibilityStudyV2Draft"):
        study = _object(project.get(study_key))
        raw = _object(study.get("raw_import"))
        document = _object(raw.get("sourceDocument"))
        if study_key == "feasibilityStudyV2Draft":
            from use_cases.workbook_input_reconciliation import _registered_source

            if not _registered_source(project, {"sourceDocument": document}):
                continue
        profile = raw.get("profile")
        key, sheet, cell, label = _WORKBOOK_SUBTOTAL_CELLS.get(profile, _BONRI_SUBTOTAL_CELL) if isinstance(profile, str) else _BONRI_SUBTOTAL_CELL
        for row in _rows(study.get("cost_items")):
            if row.get("key") != key or row.get("unit") != "KRW" or row.get("category") != "tax_and_dues":
                continue
            amount = _whole_won(row.get("amount"))
            origin = _object(row.get("provenance"))
            name = origin.get("source_document")
            if (
                amount is None or origin.get("source_type") not in {"xlsx", "xls"}
                or not isinstance(name, str) or not name.strip()
                or origin.get("sheet_name") != sheet or origin.get("cell") != cell
                or document.get("originalFileName") not in (None, name)
            ):
                continue
            title = f"사업수지 원본의 {label}"
            return {
                "amountKrw": amount,
                "label": title,
                "evidence": [{
                    "sourceType": "workbook",
                    "status": "workbook_subtotal",
                    "label": title,
                    "locator": {"archiveId": document.get("archiveId"), "documentName": name, "sheetName": sheet, "cell": cell},
                }],
            }
    return None


def _market_facts(review: dict[str, Any]) -> list[dict]:
    facts = []
    for record in _rows(review.get("evidenceRecords")):
        # Comparables and rental proxies are not project-specific levy bases.
        if record.get("evidenceType") != "product_mix":
            continue
        fields = _object(record.get("extractedFields"))
        locators = _object(record.get("fieldSourceLocators"))
        for key, label in (("units", "상품별 공급 수량"), ("totalRevenue", "상품별 계획 매출")):
            value = _whole_won(fields.get(key))
            locator = _object(locators.get(key))
            source = {
                name: locator[name] for name in ("evidenceId", "pageNumber", "table", "row", "column")
                if locator.get(name) is not None
            }
            if value is None or not source.get("evidenceId") or not (source.get("pageNumber") or source.get("row")):
                continue
            facts.append({
                "sourceType": "market_product", "label": label, "status": "not_assessment_basis",
                "value": value, "locator": source,
            })
    return facts[:8]


def build_statutory_cost_review(
    project_data: dict[str, Any],
    *,
    legal_review: dict[str, Any] | None = None,
    market_review: dict[str, Any] | None = None,
    cost_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect review evidence without inferring applicability or adding a cost."""
    legal = _object(project_data.get("legalPermitReview") if legal_review is None else legal_review)
    references = _rows(legal.get("statutoryCostLawReferences")) + _rows(legal.get("lawReferences"))
    official_basis_date = _source_date(legal.get("statutoryCostAsOfDate"))
    parcel_rows = canonical_project_parcel_rows(project_data)
    land = _object(project_data.get("land"))
    declared_parcels = len(parcel_rows) or int(bool(parcel_pnu(land)))
    common_evidence = []
    if declared_parcels:
        common_evidence.append({
            "sourceType": "land", "label": "등록된 사업 필지", "status": "project_candidate",
            "value": declared_parcels,
        })
    use_labels = {
        "housing": "주택", "mixed_use": "복합용도", "officetel": "오피스텔", "non_housing": "비주거",
    }
    stated_use = use_labels.get(classify_permit_use(project_data))
    if stated_use:
        common_evidence.append({
            "sourceType": "product_plan", "label": "사업 자료에 기록된 용도",
            "status": "project_candidate", "value": stated_use,
        })

    market = _object(market_review)
    cost = _object(cost_review)
    workbook_subtotal = _workbook_subtotal(project_data)
    if workbook_subtotal:
        common_evidence.extend(workbook_subtotal["evidence"])
    if _whole_won(_object(cost.get("costBreakdown")).get("totalCost")) not in (None, 0):
        common_evidence.append({
            "sourceType": "cost_schedule", "label": "비용 Pod의 총사업비",
            "status": "inclusion_not_verified",
        })
    common_evidence.extend(_market_facts(market))

    items = []
    for key, label in STATUTORY_COST_CATALOG.items():
        law_evidence = _official_law_evidence(key, references, official_basis_date)
        missing = []
        if not any(item["status"] == "verified_article" for item in law_evidence):
            missing.append("사업 적용일 기준 공식 조항·요율 근거")
        else:
            missing.append("사업 적용일과 산정 기준·요율의 공식 근거")
        if not declared_parcels:
            missing.append("사업 대상 필지와 관할")
        if key == "school_site_charge":
            missing.extend(["분양 대상 주택 세대수·사업유형·면제 근거", "부과 대상 분양가격과 관할 부과 산정 근거"])
        else:
            missing.extend(["건축물 용도별 적용 연면적·제외면적", "관할 기준 건축비와 설치·출연 방식"])
        missing.append("기존 사업비 포함 여부와 고지서·납부 확인 자료")
        items.append({
            "key": key, "label": label, "status": "unknown",
            "amountKrw": None, "amountKind": None, "analysisOnly": True,
            "legalLookupStatus": _object(legal.get("statutoryCostLookupStatus")).get(_LAW_TITLES[key], "not_requested"),
            "missingInputs": missing, "evidence": law_evidence + common_evidence,
        })
    return {
        "schemaVersion": STATUTORY_COST_REVIEW_VERSION,
        "analysisOnly": True,
        "items": items,
        "workbookSubtotal": workbook_subtotal,
    }
