from __future__ import annotations

from fastapi import APIRouter, Query

from integrations.market_data import (
    MolitRtmsConnector,
    ValuemapConnector,
    market_data_status,
    summarize_transactions,
)

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
):
    """국토부 실거래가(MOLIT RTMS) — actual closed transactions for the given
    법정동/월. Public data; each record carries its source for provenance."""
    records, diagnostic = MolitRtmsConnector().fetch(
        lawd_cd=lawd_cd,
        deal_ym=deal_ym,
        asset_type=asset_type,
        deal_kind=deal_kind,
        num_rows=num_rows,
    )
    return {
        "schemaVersion": "realytics-market-transactions-v1",
        "query": {"lawdCd": lawd_cd, "dealYm": deal_ym, "assetType": asset_type, "dealKind": deal_kind},
        "summary": summarize_transactions(records),
        "records": records,
        "diagnostic": diagnostic,
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
