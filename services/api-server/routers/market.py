from __future__ import annotations

from fastapi import APIRouter, Query

from integrations.market_data import (
    MolitRtmsConnector,
    ValuemapConnector,
    aggregate_by_complex,
    attach_coordinates,
    market_data_status,
    summarize_transactions,
)
from integrations.vworld import build_geocoder, fetch_parcel_by_point, geocode_address, vworld_configured
from integrations.market_data import lawd_cd_from_pnu
from use_cases.map_intelligence import build_project_map_intelligence

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/status")
def get_market_data_status():
    """Secret-safe readiness of the market data providers (실거래가 / 밸류맵)."""
    return {"schemaVersion": "realytics-market-data-status-v1", **market_data_status()}


@router.get("/transactions")
def get_market_transactions(
    lawd_cd: str = Query(..., description="법정동코드 5자리 (LAWD_CD)"),
    deal_ym: str = Query(..., description="거래년월 YYYYMM (DEAL_YMD)"),
    asset_type: str = Query("apartment", description="apartment|officetel|row_house|detached_house|land|commercial"),
    deal_kind: str = Query("trade", description="trade(매매)|rent(전월세)"),
    num_rows: int = Query(200, ge=1, le=1000),
    geocode: bool = Query(False, description="VWorld로 상위 N건 좌표화(지도 마커용)"),
    geocode_limit: int = Query(30, ge=1, le=60),
    aggregate: bool = Query(False, description="단지(complex)별 시세 집계 포함"),
):
    """국토부 실거래가(MOLIT RTMS) — actual closed transactions for the given
    법정동/월. Public data; each record carries its source for provenance.
    With geocode=true and a VWorld key, top-N records get lat/lng for map pins."""
    records, diagnostic = MolitRtmsConnector().fetch(
        lawd_cd=lawd_cd,
        deal_ym=deal_ym,
        asset_type=asset_type,
        deal_kind=deal_kind,
        num_rows=num_rows,
    )
    geocoded = 0
    if geocode and records and vworld_configured():
        geocoded = attach_coordinates(records, build_geocoder(), limit=geocode_limit)
    response = {
        "schemaVersion": "realytics-market-transactions-v1",
        "query": {"lawdCd": lawd_cd, "dealYm": deal_ym, "assetType": asset_type, "dealKind": deal_kind},
        "summary": {**summarize_transactions(records), "geocodedCount": geocoded},
        "records": records,
        "diagnostic": diagnostic,
    }
    if aggregate:
        response["complexes"] = aggregate_by_complex(records)
    return response


@router.get("/parcel-lookup")
def parcel_lookup(
    address: str = Query("", description="대상지 주소 (동 단위 권장)"),
    pnu: str = Query("", description="법정동코드/실거래가용 PNU (선택)"),
):
    """토지·거래 분석: 주소를 좌표화(VWorld) → 해당 지점의 지적 필지(연속지적도)를
    가져와 지도 데이터로 반환. 실거래가용 법정동코드도 함께 돌려준다."""
    geo_status = "skipped"
    point = None
    if address.strip() and vworld_configured():
        # Try the address, then progressively trimmed candidates (drop 일원/번지
        # suffix, then 읍면동-level) so messy inputs still geocode.
        raw = address.strip()
        candidates = [raw]
        cleaned = raw.replace("일원", "").strip()
        if cleaned and cleaned != raw:
            candidates.append(cleaned)
        tokens = cleaned.split()
        dong_idx = next((i for i, t in enumerate(tokens) if t.endswith(("동", "리", "가"))), -1)
        if dong_idx >= 0:
            candidates.append(" ".join(tokens[: dong_idx + 1]))
        for cand in dict.fromkeys(candidates):
            point, gdiag = geocode_address(cand)
            geo_status = gdiag.get("status", "error")
            if point:
                break

    parcel_props: dict = {}
    geometry = None
    if point:
        geometry, pdiag = fetch_parcel_by_point(point["lng"], point["lat"])
        if pdiag.get("status") == "resolved":
            parcel_props = pdiag.get("properties") or {}

    resolved_pnu = (parcel_props.get("pnu") or pnu or "").strip()
    lawd_cd = lawd_cd_from_pnu(resolved_pnu) or lawd_cd_from_pnu(pnu)

    parcel = {
        "pnu": resolved_pnu or None,
        "addressFragment": parcel_props.get("addr") or parcel_props.get("jibun") or address or None,
        "lotNumber": parcel_props.get("jibun"),
        "landCategory": parcel_props.get("jimok") or parcel_props.get("dgm_nm"),
    }
    if geometry is not None:
        parcel["geometry"] = geometry
    project_data = {"land_acquisition": [parcel]} if (resolved_pnu or geometry) else {}
    map_payload = build_project_map_intelligence(project_data)
    if not map_payload.get("center") and point:
        map_payload["center"] = {"lng": point["lng"], "lat": point["lat"]}

    return {
        "schemaVersion": "realytics-parcel-lookup-v1",
        "address": address,
        "geocodeStatus": geo_status,
        "center": map_payload.get("center"),
        "pnu": resolved_pnu or None,
        "lawdCd": lawd_cd,
        "hasParcel": geometry is not None,
        "map": map_payload,
    }


@router.get("/comparables")
def get_market_comparables(
    lat: float | None = Query(None),
    lng: float | None = Query(None),
    radius_m: int = Query(500, ge=50, le=5000),
):
    """밸류맵 제휴 comparable/listing data (partner API; mock fallback until the
    partnership spec/credentials are configured)."""
    records, diagnostic = ValuemapConnector().fetch(lat=lat, lng=lng, radius_m=radius_m)
    return {
        "schemaVersion": "realytics-market-comparables-v1",
        "records": records,
        "diagnostic": diagnostic,
    }
