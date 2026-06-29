from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Query

from integrations.market_data import (
    MolitRtmsConnector,
    ValuemapConnector,
    aggregate_by_complex,
    attach_coordinates,
    market_data_status,
    summarize_transactions,
)
import math

from integrations.vworld import (
    build_geocoder,
    fetch_jiga_in_radius,
    fetch_parcel_by_point,
    geocode_address,
    vworld_configured,
)
from integrations.market_data import MolitRtmsConnector, lawd_cd_from_pnu
from use_cases.map_intelligence import build_project_map_intelligence
from use_cases.market_comparables import recent_deal_yms
from agents.marketability_agent import MarketabilityAgent
from agents.legal_review_agent import LegalReviewAgent

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

    # 개별공시지가(jiga) is carried on the VWorld 연속지적도 properties.
    jiga = parcel_props.get("jiga")
    try:
        jiga_val = float(str(jiga).replace(",", "")) if jiga not in (None, "") else None
    except (TypeError, ValueError):
        jiga_val = None
    gosi_year = parcel_props.get("gosi_year")

    parcel = {
        "pnu": resolved_pnu or None,
        "addressFragment": parcel_props.get("addr") or parcel_props.get("jibun") or address or None,
        "lotNumber": parcel_props.get("jibun"),
        "landCategory": parcel_props.get("jimok") or parcel_props.get("dgm_nm"),
    }
    if jiga_val:
        parcel["officialLandPricePerSqm"] = jiga_val
        parcel["officialLandPriceBaseYear"] = gosi_year
    if geometry is not None:
        parcel["geometry"] = geometry
    project_data: dict = {"land_acquisition": [parcel]} if (resolved_pnu or geometry) else {}

    # --- Agent-driven layer signals (실거래/공급 = Marketability, 제한물권 = Legal) ---
    signals: dict = {}
    # 공시지가 — from VWorld cadastral jiga
    signals["official_land_price"] = {
        "status": "available" if jiga_val else "needs_data",
        "source": "VWorld 개별공시지가",
        "detail": f"{int(jiga_val):,}원/㎡ ({gosi_year} 기준)" if jiga_val else "공시지가 조회 불가",
    }
    # 용도지역 — VWorld 토지이용계획 reference overlay (map toggle)
    signals["zoning"] = {
        "status": "available",
        "source": "VWorld 토지이용계획",
        "detail": "지도 ‘용도지역’ 레이어 토글로 표시",
    }
    restricted_rights: list = []
    # 실거래 사례 — live count for the 법정동
    tx_records: list = []
    if lawd_cd:
        try:
            tx_records, _ = MolitRtmsConnector().fetch(
                lawd_cd=lawd_cd, deal_ym=recent_deal_yms(1)[0], asset_type="apartment", deal_kind="trade", num_rows=100
            )
        except Exception:
            tx_records = []
    signals["transactions"] = {
        "status": "available" if tx_records else "needs_data",
        "featureCount": len(tx_records) or None,
        "source": "국토부 실거래가",
        "detail": f"최근월 {len(tx_records)}건" if tx_records else "활용신청/지역 데이터 필요",
    }
    # 주변 공급 — Marketability Agent (경쟁도 estimate from nearby comparables)
    try:
        mk = MarketabilityAgent().analyze({
            "project": {"type": "apartment"},
            "product_mix": [{"type": "apartment", "units": 1, "exclusiveAreaPy": 30}],
            "market": {"transactionComparables": tx_records[:30]},
        })
        supply = (mk or {}).get("supplyAnalysis") or {}
        level = supply.get("competitionLevel")
        signals["supply"] = {
            "status": "available" if level else "needs_data",
            "source": "Marketability Agent",
            "detail": f"경쟁도 {level}" if level else "분양·공급 데이터 연동 시 제공",
        }
    except Exception:
        signals["supply"] = {"status": "needs_data", "source": "Marketability Agent", "detail": "분양·공급 데이터 필요"}
    # 리스크(제한물권) — Legal Agent land-rights / encumbrance check.
    # The address tool has no 등기(registry) data, so run the check on registry
    # evidence only (empty here) → honest "등기 증빙 필요" instead of a false
    # ownership-share alarm. When a project supplies registry evidence, the
    # encumbrances (저당권/압류/지상권 등) are listed.
    has_registry = bool(project_data.get("landEvidence") or project_data.get("land_evidence"))
    try:
        legal = LegalReviewAgent().review(project_data if has_registry else {})
        lr = next((c for c in legal.get("checks", []) if c.get("key") == "land_rights"), None)
        severity = lr.get("severity") if lr else None
        if has_registry and lr and severity != "clear":
            restricted_rights = lr.get("findings") or []
        signals["risk"] = {
            "status": "available" if (has_registry and restricted_rights) else "needs_data",
            "featureCount": len(restricted_rights) or None,
            "source": "Legal Agent · 등기 제한물권",
            "detail": "; ".join(restricted_rights[:2]) if restricted_rights else "등기 증빙 첨부 시 저당권·압류·지상권 등 제한물권 자동 점검",
        }
    except Exception:
        signals["risk"] = {"status": "needs_data", "source": "Legal Agent · 등기 제한물권", "detail": "등기 증빙 필요"}

    project_data["_layerSignals"] = signals
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
        "restrictedRights": restricted_rights,
        "map": map_payload,
    }


def _haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _jiga_stats(values: List[float], total: int) -> Dict[str, Any]:
    if not values:
        return {"count": total, "sampleCount": 0, "avgPerSqm": None, "medianPerSqm": None, "minPerSqm": None, "maxPerSqm": None}
    s = sorted(values)
    return {
        "count": total or len(values),
        "sampleCount": len(values),
        "capped": total > len(values),
        "avgPerSqm": round(sum(s) / len(s)),
        "medianPerSqm": round(s[len(s) // 2]),
        "minPerSqm": round(s[0]),
        "maxPerSqm": round(s[-1]),
    }


@router.get("/land-radius")
def land_radius(
    lng: float = Query(..., description="대상지 경도"),
    lat: float = Query(..., description="대상지 위도"),
    lawd_cd: str = Query("", description="실거래가용 법정동코드 5자리"),
    deal_ym: str = Query("", description="거래년월 YYYYMM (기본: 최근월)"),
):
    """대상지로부터 반경 500m/1km/2km의 공시지가 통계와 토지 실거래 사례.

    공시지가는 VWorld 지적 buffer 조회의 `jiga`로 산출, 토지 실거래는 국토부
    토지 실거래가를 지오코딩→거리계산으로 반경 분류한다."""
    radii = [500, 1000, 2000]

    # 공시지가 통계 (반경 누적)
    gongsi = []
    for radius in radii:
        values, total, _ = fetch_jiga_in_radius(lng, lat, radius) if vworld_configured() else ([], 0, {})
        gongsi.append({"radius": radius, **_jiga_stats(values, total)})

    # 토지 실거래 — 시군구 단위로 최근 3개월 조회 후 지오코딩→거리 (토지 거래는 희소)
    months = [deal_ym] if deal_ym else recent_deal_yms(3)
    ym = months[0]
    land_deals: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    if lawd_cd:
        for m in months:
            try:
                recs, _ = MolitRtmsConnector().fetch(
                    lawd_cd=lawd_cd, deal_ym=m, asset_type="land", deal_kind="trade", num_rows=200
                )
                records.extend(recs)
            except Exception:
                pass
        if records and vworld_configured():
            attach_coordinates(records, build_geocoder(), limit=60)
        for r in records:
            if not (_is_num(r.get("lat")) and _is_num(r.get("lng"))):
                continue
            dist = _haversine_m(lng, lat, r["lng"], r["lat"])
            # Keep all geocoded land deals (raw-land deals are sparse in built-up
            # areas and RTMS geocodes to the 읍면동 centroid) — the list shows the
            # nearest with their distance; radius buckets count what falls inside.
            land_deals.append({
                "address": r.get("address"),
                "areaSqm": r.get("areaSqm"),
                "totalPrice": r.get("totalPrice"),
                "pricePerPy": r.get("pricePerPy"),
                "transactionDate": r.get("transactionDate"),
                "lat": r.get("lat"),
                "lng": r.get("lng"),
                "distanceM": round(dist),
            })
    land_deals.sort(key=lambda d: d["distanceM"])
    land_deals = land_deals[:25]
    for radius in gongsi:
        radius["landDealCount"] = sum(1 for d in land_deals if d["distanceM"] <= radius["radius"])

    return {
        "schemaVersion": "realytics-land-radius-v1",
        "center": {"lng": lng, "lat": lat},
        "dealYm": ym,
        "radii": gongsi,
        "landDeals": land_deals,
    }


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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
