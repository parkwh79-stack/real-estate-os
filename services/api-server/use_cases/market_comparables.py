from __future__ import annotations

"""Inject live 국토부 실거래가 into a PF review as Marketability comparables.

Gated by `REALYTICS_LIVE_MARKET_COMPARABLES` (off by default → tests/CI stay
offline and deterministic; turn it on in a deployment env to auto-fetch). When
enabled and a data.go.kr key is configured, the PF run pulls recent 실거래가 for
the project's 법정동/asset type and adds them to `transactionComparables` so the
Marketability Agent prices 분양가 적정성 / Exit 분양률 against actual closed
transactions. Non-fabricating: no key / no LAWD_CD / no data → nothing injected.
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from integrations.market_data import MolitRtmsConnector, lawd_cd_from_pnu
from use_cases.map_intelligence import _extract_parcels

# Project/product type → RTMS asset endpoint. Proxies are conservative (e.g. a
# mixed-use 주상복합 priced off 아파트 실거래; 생숙 off 오피스텔).
ASSET_TYPE_MAP = {
    "apartment": "apartment",
    "apt": "apartment",
    "아파트": "apartment",
    "mixed_use": "apartment",
    "주상복합": "apartment",
    "officetel": "officetel",
    "오피스텔": "officetel",
    "serviced_residence": "officetel",
    "생활숙박시설": "officetel",
    "생숙": "officetel",
    "row_house": "row_house",
    "연립": "row_house",
    "다세대": "row_house",
    "villa": "row_house",
    "detached_house": "detached_house",
    "단독": "detached_house",
    "knowledge_industry_center": "commercial",
    "지식산업센터": "commercial",
    "commercial": "commercial",
    "retail": "commercial",
    "상업": "commercial",
    "업무": "commercial",
    "land": "land",
    "토지": "land",
}


def live_market_comparables_enabled(environ: Optional[Dict[str, str]] = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get("REALYTICS_LIVE_MARKET_COMPARABLES", "")).strip().lower() in {"1", "true", "yes", "on"}


def derive_lawd_cd(project_data: Dict[str, Any]) -> Optional[str]:
    """Project's 시군구 LAWD_CD — from a parcel PNU, else an explicit field."""
    for parcel in _extract_parcels(project_data):
        code = lawd_cd_from_pnu(parcel.get("pnu") or parcel.get("resolvedPnu"))
        if code:
            return code
    for source in (project_data.get("project"), project_data.get("land"), project_data):
        if isinstance(source, dict):
            raw = source.get("lawdCd") or source.get("lawd_cd")
            if raw and str(raw).isdigit() and len(str(raw)) >= 5:
                return str(raw)[:5]
    return None


def derive_asset_type(project_data: Dict[str, Any]) -> str:
    raw = (
        (project_data.get("project") or {}).get("type")
        or (project_data.get("marketability") or {}).get("productType")
        or ""
    )
    return ASSET_TYPE_MAP.get(str(raw).strip().lower(), "apartment")


def recent_deal_yms(n: int = 3, base: Optional[Tuple[int, int]] = None) -> List[str]:
    """The most recent n 거래년월 (YYYYMM), newest first. `base`=(year,month) for tests."""
    if base:
        year, month = base
    else:
        now = datetime.now()
        year, month = now.year, now.month
    out: List[str] = []
    for i in range(n):
        mm = month - i
        yy = year
        while mm <= 0:
            mm += 12
            yy -= 1
        out.append(f"{yy:04d}{mm:02d}")
    return out


def fetch_project_market_comparables(
    project_data: Dict[str, Any],
    *,
    connector: MolitRtmsConnector,
    deal_yms: List[str],
    asset_type: Optional[str] = None,
    lawd_cd: Optional[str] = None,
    per_month_rows: int = 80,
    max_records: int = 40,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch recent 실거래가 comparables for the project (connector injected)."""
    lawd = lawd_cd or derive_lawd_cd(project_data)
    asset = asset_type or derive_asset_type(project_data)
    if not lawd:
        return [], {"status": "skipped", "reason": "no_lawd_cd", "assetType": asset}
    records: List[Dict[str, Any]] = []
    per_month: List[Dict[str, Any]] = []
    for ym in deal_yms:
        recs, diag = connector.fetch(
            lawd_cd=lawd, deal_ym=ym, asset_type=asset, deal_kind="trade", num_rows=per_month_rows
        )
        per_month.append({"ym": ym, "status": diag.get("status"), "count": len(recs)})
        records.extend(recs)
        if len(records) >= max_records:
            break
    records = records[:max_records]
    status = "ok" if records else (per_month[0]["status"] if per_month else "empty")
    return records, {
        "status": status,
        "lawdCd": lawd,
        "assetType": asset,
        "months": deal_yms,
        "count": len(records),
        "perMonth": per_month,
    }


def inject_market_comparables(target: Dict[str, Any], records: List[Dict[str, Any]]) -> int:
    """Append fetched comparables to `target['market']['transactionComparables']`.

    MarketabilityAgent reads comparables from the nested `market` dict, so they
    must be injected there (not at the top level)."""
    if not records:
        return 0
    market = target.get("market")
    if not isinstance(market, dict):
        market = {}
        target["market"] = market
    existing = market.get("transactionComparables")
    market["transactionComparables"] = ([*existing] if isinstance(existing, list) else []) + records
    return len(records)
