"""Project market depth and its isolated public reference lookup."""
from __future__ import annotations

from math import isfinite
from statistics import median
from typing import Any, Dict, Optional

from agents.market_evidence_agent import MarketEvidenceAgent
from agents.market_product_depth_agent import MarketProductDepthAgent
from integrations.market_data import lawd_cd_from_pnu, molit_display_connector
from use_cases.common import deep_merge
from use_cases.market_comparables import ASSET_TYPE_MAP, fetch_project_market_comparables, recent_deal_yms
from use_cases.marketability_review import analyze_marketability_use_case
from use_cases.parcel_pnu_resolution import canonical_project_parcel_rows, parcel_pnu


def analyze_market_product_depth_use_case(
    project_data: Dict[str, Any], options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Check current stored documents with the same market evidence Agent as the Pod."""
    try:
        review = MarketEvidenceAgent().review(project_data)
        source = deep_merge(project_data, {**review["marketPayloadPatch"], "marketEvidenceReview": review})
        marketability = analyze_marketability_use_case(source)
    except (AttributeError, TypeError, ValueError):
        # Scope local input-shape errors to the analysis rather than hiding the
        # user's archived documents along with an unrelated malformed input.
        source, marketability = project_data, {}
        analysis_status = "needs_review"
    else:
        analysis_status = "available"
    result = MarketProductDepthAgent().analyze(source, marketability, options=options)
    return {**result, "analysisStatus": analysis_status}


def project_market_public_lookup(project: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch a bounded public reference without returning or promoting individual trades."""
    label = "국토교통부 실거래가"

    def result(status: str, **values: Any) -> Dict[str, Any]:
        return {"status": status, "count": 0, "sourceName": label, **values}

    parcels = canonical_project_parcel_rows(project)
    land = project.get("land")
    if not parcels and isinstance(land, dict) and parcel_pnu(land):
        parcels = [land]
    districts = {lawd_cd_from_pnu(parcel_pnu(row)) for row in parcels}
    if len(districts) != 1 or None in districts:
        return result("needs_input")

    details = project.get("project")
    project_type = str(details.get("type") or "").strip().lower() if isinstance(details, dict) else ""
    asset = ASSET_TYPE_MAP.get(project_type)
    if not asset or project_type in {"mixed_use", "주상복합", "serviced_residence", "생활숙박시설", "생숙"}:
        return result("needs_input")

    connector = molit_display_connector()
    if not connector.configured():
        return result("unavailable")
    lawd = next(iter(districts))
    month = recent_deal_yms(1)[0]
    metadata = {"lawdCd": lawd, "assetType": asset, "month": month}
    try:
        records, diagnostic = fetch_project_market_comparables(
            project, connector=connector, deal_yms=[month], asset_type=asset, lawd_cd=lawd,
            per_month_rows=30, max_records=30,
        )
    except (OSError, TypeError, ValueError):
        return result("failed", **metadata)
    if not records:
        return result("empty" if diagnostic.get("status") == "empty" else "failed", **metadata)
    prices = [
        price for record in records if isinstance(record, dict) and record.get("dealKind") in (None, "trade")
        if isinstance(price := record.get("pricePerPy"), (int, float)) and not isinstance(price, bool)
        and isfinite(price) and price > 0
    ]
    return result("available", **metadata, count=len(records), medianPerPy=round(median(prices)) if prices else None)
