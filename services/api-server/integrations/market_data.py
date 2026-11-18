from __future__ import annotations

"""Market transaction / comparable data providers for Map Intelligence + Marketability.

Two legitimate sources, normalized into the Marketability Agent's comparable
shape (see marketability_agent._collect_market_comparables):

- **국토부 실거래가 (MOLIT RTMS)** — the public OpenAPI on data.go.kr. Actual
  closed transactions (매매/전월세) for 아파트/오피스텔/연립다세대/단독다가구/토지/
  상업업무용. Free key. Maps to comparableCategory "transaction" (public_api).
- **밸류맵 (Valuemap)** — partner API (license-based, not scraped). Config-driven
  (VALUEMAP_API_URL + key); mock fallback until the partnership spec/credentials
  land. Maps to comparableCategory "listing" (partner_api).

Non-fabricating: no key / network error / unknown spec → empty result + a clear
diagnostic. Mock data is always explicitly labelled as such (source=*_mock).
"""

import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional, Tuple

SQM_PER_PY = 3.305785

# data.go.kr agency 1613000 RTMS endpoints, keyed by (asset, deal_kind).
RTMS_BASE = "https://apis.data.go.kr/1613000"
RTMS_ENDPOINTS: Dict[Tuple[str, str], str] = {
    ("apartment", "trade"): f"{RTMS_BASE}/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
    ("apartment", "rent"): f"{RTMS_BASE}/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
    ("officetel", "trade"): f"{RTMS_BASE}/RTMSDataSvcOffiTrade/getRTMSDataSvcOffiTrade",
    ("officetel", "rent"): f"{RTMS_BASE}/RTMSDataSvcOffiRent/getRTMSDataSvcOffiRent",
    ("row_house", "trade"): f"{RTMS_BASE}/RTMSDataSvcRHTrade/getRTMSDataSvcRHTrade",
    ("detached_house", "trade"): f"{RTMS_BASE}/RTMSDataSvcSHTrade/getRTMSDataSvcSHTrade",
    ("land", "trade"): f"{RTMS_BASE}/RTMSDataSvcLandTrade/getRTMSDataSvcLandTrade",
    ("commercial", "trade"): f"{RTMS_BASE}/RTMSDataSvcNrgTrade/getRTMSDataSvcNrgTrade",
}

_RTMS_KEY_ENV = ["DATA_GO_KR_API_KEY", "REALYTICS_PUBLIC_DATA_API_KEY", "MOLIT_API_KEY"]

DEAL_TYPE_LABEL = {"trade": "매매", "rent": "전월세"}


def _env(environ: Optional[Dict[str, str]]) -> Dict[str, str]:
    return environ if environ is not None else os.environ


def _rtms_key(environ: Optional[Dict[str, str]] = None) -> Optional[str]:
    env = _env(environ)
    for key in _RTMS_KEY_ENV:
        value = env.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _text(node: Optional[ET.Element]) -> Optional[str]:
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    return text or None


def _http_get(url: str, timeout: float = 10.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 (trusted gov host)
        return response.read().decode("utf-8")


def _diag(source: str, status: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"source": source, "status": status, "message": message, **extra}


def _per_py_from(total_price: Optional[float], area_sqm: Optional[float]) -> Optional[float]:
    if not total_price or not area_sqm:
        return None
    return round(total_price / (area_sqm / SQM_PER_PY))


def parse_rtms_items(xml_text: str, *, asset_type: str, deal_kind: str, lawd_cd: str) -> List[Dict[str, Any]]:
    """Parse an RTMS XML body into normalized comparable records.

    Defensive: reads child tags by name, tolerates the public API's varied tag
    casing, and skips rows that carry no usable price.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    records: List[Dict[str, Any]] = []
    for item in root.iter("item"):
        fields = {child.tag: (child.text or "").strip() for child in item}

        def pick(*names: str) -> Optional[str]:
            for name in names:
                if fields.get(name):
                    return fields[name]
            return None

        area_sqm = _to_float(pick("excluUseAr", "totalFloorAr", "plottageAr", "dealArea"))
        year = pick("dealYear")
        month = pick("dealMonth")
        day = pick("dealDay")
        deal_date = None
        if year and month:
            deal_date = f"{int(year):04d}-{int(month):02d}" + (f"-{int(day):02d}" if day else "")
        umd = pick("umdNm")
        jibun = pick("jibun")
        building = pick("aptNm", "offiNm", "mhouseNm", "bldgNm")
        sgg = pick("estateAgentSggNm")  # 시군구명 (중개사 소재; 통상 물건과 동일 구)
        if sgg and " " in sgg:  # e.g. "서울 서초구" → 서초구
            sgg = sgg.split()[-1]
        address = " ".join(part for part in [umd, jibun, building] if part) or umd

        record: Dict[str, Any] = {
            "source": "molit_rtms",
            "sourceName": "국토교통부 실거래가",
            "sourceType": "public_api",
            "comparableCategory": "transaction",
            "dealType": DEAL_TYPE_LABEL.get(deal_kind, deal_kind),
            "dealKind": deal_kind,
            "productType": asset_type,
            "lawdCd": lawd_cd,
            "umdNm": umd,
            "jibun": jibun,
            "sggNm": sgg,
            "buildingName": building,
            "address": address,
            "areaSqm": area_sqm,
            "areaPy": round(area_sqm / SQM_PER_PY, 2) if area_sqm else None,
            "floor": _to_float(pick("floor")),
            "buildYear": pick("buildYear"),
            "transactionDate": deal_date,
            "baseDate": deal_date,
            "confidence": 0.9,
            "sourceUrlOrEvidenceId": f"molit_rtms:{asset_type}:{deal_kind}:{lawd_cd}:{deal_date}:{jibun or ''}",
            "lat": None,
            "lng": None,
        }
        if deal_kind == "trade":
            total = _to_float(pick("dealAmount"))
            if total is not None:
                total *= 10000  # 만원 → 원
            record["totalPrice"] = total
            record["pricePerPy"] = _per_py_from(total, area_sqm)
            if record["pricePerPy"] is None:
                continue
        else:  # rent (전월세)
            deposit = _to_float(pick("deposit"))
            monthly = _to_float(pick("monthlyRent"))
            record["depositKRW"] = deposit * 10000 if deposit is not None else None
            record["monthlyRentKRW"] = monthly * 10000 if monthly is not None else None
            record["dealType"] = "월세" if (monthly or 0) > 0 else "전세"
            # rent comps are reported as deposit/monthly, not per-py sale price
            if deposit is None and monthly is None:
                continue
        records.append(record)
    return records


class MolitRtmsConnector:
    source = "molit_rtms"

    def __init__(self, environ: Optional[Dict[str, str]] = None, http_get: Callable[[str], str] = _http_get) -> None:
        self.environ = environ
        self.http_get = http_get

    def configured(self) -> bool:
        return _rtms_key(self.environ) is not None

    def fetch(
        self,
        *,
        lawd_cd: str,
        deal_ym: str,
        asset_type: str = "apartment",
        deal_kind: str = "trade",
        num_rows: int = 200,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        key = _rtms_key(self.environ)
        if not key:
            return [], _diag(self.source, "skipped", "data.go.kr 실거래가 키가 설정되지 않았습니다 (DATA_GO_KR_API_KEY).")
        endpoint = RTMS_ENDPOINTS.get((asset_type, deal_kind))
        if not endpoint:
            return [], _diag(self.source, "unsupported", f"지원하지 않는 조합: {asset_type}/{deal_kind}")
        if not (lawd_cd and deal_ym):
            return [], _diag(self.source, "skipped", "법정동코드(LAWD_CD)와 거래년월(DEAL_YMD)이 필요합니다.")
        params = {
            "serviceKey": key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ym,
            "pageNo": "1",
            "numOfRows": str(num_rows),
        }
        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        try:
            body = self.http_get(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                return [], _diag(
                    self.source,
                    "forbidden",
                    f"해당 실거래가 API 미신청/권한 없음 — data.go.kr에서 '{asset_type}/{deal_kind}' 활용신청이 필요합니다.",
                    httpStatus=403,
                )
            return [], _diag(self.source, "error", f"실거래가 요청 실패: HTTP {exc.code}", httpStatus=exc.code)
        except Exception as exc:  # network/timeout — degrade
            return [], _diag(self.source, "error", f"실거래가 요청 실패: {type(exc).__name__}")
        # RTMS can return HTTP 200 for gateway/authentication errors. Only a
        # successful response envelope may establish that there are no trades.
        # Current RTMS uses 000; retain the public gateway's legacy 00 success.
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, TypeError):
            return [], _diag(self.source, "error", "실거래가 응답 형식을 확인하지 못했습니다.", errorCode="RTMS_INVALID_XML")
        result_code = _text(root.find("./header/resultCode"))
        gateway_code = _text(root.find(".//returnReasonCode"))
        if gateway_code or root.tag == "OpenAPI_ServiceResponse" or (result_code and result_code not in {"00", "000"}):
            code = gateway_code or result_code or ""
            # Never echo resultMsg/errMsg, response bodies, URLs, or arbitrary
            # upstream code strings: they can contain the service key.
            safe_code = code if code.isascii() and code.isdigit() and 1 <= len(code) <= 3 else None
            extra = {"errorCode": "RTMS_PROVIDER_ERROR"}
            if safe_code is not None:
                extra["providerCode"] = safe_code
            return [], _diag(
                self.source, "forbidden" if safe_code in {"20", "30", "31"} else "error",
                "실거래가 제공기관에서 요청을 처리하지 못했습니다. 이용 권한과 연결 상태를 확인해 주세요.",
                **extra,
            )
        if root.tag != "response" or result_code not in {"00", "000"} or root.find("./body") is None:
            return [], _diag(self.source, "error", "실거래가 정상 응답을 확인하지 못했습니다.", errorCode="RTMS_INVALID_RESPONSE")
        try:
            records = parse_rtms_items(body, asset_type=asset_type, deal_kind=deal_kind, lawd_cd=lawd_cd)
        except (ValueError, TypeError, OverflowError):
            return [], _diag(self.source, "error", "실거래가 항목을 읽지 못했습니다.", errorCode="RTMS_INVALID_RECORDS")
        if not records and (root.find("./body/items/item") is not None or _text(root.find("./body/totalCount")) != "0"):
            return [], _diag(self.source, "error", "실거래가 조회 결과의 거래 수를 확인하지 못했습니다.", errorCode="RTMS_UNVERIFIED_EMPTY")
        return records, _diag(self.source, "ok" if records else "empty", f"{len(records)}건", count=len(records))


# Mock partner comparables (clearly labelled). Replaced by the live Valuemap
# partnership API once VALUEMAP_API_URL + key are configured.
_VALUEMAP_MOCK = [
    {"name": "역삼동 826-1", "address": "역삼동 826-1", "pricePerPy": 160000000, "areaPy": 218.0, "productType": "commercial", "transactionDate": "2026-02"},
    {"name": "역삼동 825-9", "address": "역삼동 825-9", "pricePerPy": 145000000, "areaPy": 193.0, "productType": "commercial", "transactionDate": "2025-11"},
]


class ValuemapConnector:
    source = "valuemap"

    def __init__(self, environ: Optional[Dict[str, str]] = None, http_get: Callable[[str], str] = _http_get) -> None:
        self.environ = environ
        self.http_get = http_get

    def configured(self) -> bool:
        env = _env(self.environ)
        return bool((env.get("VALUEMAP_API_URL") or "").strip() and (env.get("VALUEMAP_API_KEY") or "").strip())

    def fetch(self, *, lat: Optional[float] = None, lng: Optional[float] = None, radius_m: int = 500) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not self.configured():
            records = [
                {**item, "source": "valuemap_mock", "sourceName": "밸류맵(샘플)", "sourceType": "partner_api",
                 "comparableCategory": "listing", "confidence": 0.4}
                for item in _VALUEMAP_MOCK
            ]
            return records, _diag(self.source, "mock", "밸류맵 제휴 API 미설정 — 샘플 데이터(명시) 반환. VALUEMAP_API_URL/KEY 설정 시 라이브 전환.")
        # Live partner path — exact mapping finalized once the partnership spec lands.
        env = _env(self.environ)
        params = {"key": env["VALUEMAP_API_KEY"], "lat": lat, "lng": lng, "radius": radius_m}
        url = f"{env['VALUEMAP_API_URL']}?{urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})}"
        try:
            self.http_get(url)
        except Exception as exc:
            return [], _diag(self.source, "error", f"밸류맵 요청 실패: {type(exc).__name__}")
        # Mapping the partner payload is intentionally deferred to the live spec;
        # never fabricate listings from an unknown response shape.
        return [], _diag(self.source, "spec_pending", "밸류맵 응답 매핑은 제휴 스펙 확정 후 연결됩니다.")


def geocode_query_for(record: Dict[str, Any]) -> Optional[str]:
    """Build a geocodable 지번 address from an RTMS record (시군구 읍면동 지번)."""
    parts = [record.get("sggNm"), record.get("umdNm"), record.get("jibun")]
    query = " ".join(p for p in parts if p)
    return query or None


def attach_coordinates(records: List[Dict[str, Any]], geocoder: Callable[[str], Optional[Dict[str, float]]], *, limit: int = 30) -> int:
    """Geocode up to `limit` records in place (sets lat/lng). Returns count located.

    `geocoder(address)->{lng,lat}|None` is injected (e.g. VWorld) so this is
    testable offline. Duplicate addresses are geocoded once (cached per call).
    """
    cache: Dict[str, Optional[Dict[str, float]]] = {}
    located = 0
    attempted = 0
    for record in records:
        if attempted >= limit:
            break
        query = geocode_query_for(record)
        if not query:
            continue
        attempted += 1
        if query not in cache:
            cache[query] = geocoder(query)
        point = cache[query]
        if point and "lng" in point and "lat" in point:
            record["lng"] = point["lng"]
            record["lat"] = point["lat"]
            located += 1
    return located


def lawd_cd_from_pnu(pnu: Optional[str]) -> Optional[str]:
    """RTMS LAWD_CD is the 5-digit 시군구 code — the first 5 digits of a PNU."""
    if not pnu:
        return None
    digits = "".join(ch for ch in str(pnu) if ch.isdigit())
    return digits[:5] if len(digits) >= 5 else None


def molit_display_connector(environ: Optional[Dict[str, str]] = None) -> MolitRtmsConnector:
    # A display credential must never enable the Agent evidence ingestion path.
    env = _env(environ)
    display_key = (env.get("REALYTICS_MOLIT_DISPLAY_API_KEY") or "").strip()
    return MolitRtmsConnector({"DATA_GO_KR_API_KEY": display_key}) if display_key else MolitRtmsConnector(environ)


def market_data_status(environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Secret-safe readiness for the market data providers (no keys echoed)."""
    return {
        "molitRtms": {"configured": molit_display_connector(environ).configured(), "source": "data.go.kr 실거래가"},
        "valuemap": {"configured": ValuemapConnector(environ).configured(), "source": "밸류맵 제휴 API"},
        "supportedAssetTypes": sorted({asset for asset, _ in RTMS_ENDPOINTS}),
    }


def aggregate_by_complex(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group transactions into 단지(complex) summaries (Naver-style markers).

    Groups by 단지명(buildingName)+읍면동, computes median/min/max 평단가 from
    trade records, a representative coordinate (centroid of geocoded members),
    and the latest deal date. Sorted by trade count desc."""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for record in records:
        name = record.get("buildingName") or record.get("umdNm") or "기타"
        groups.setdefault((name, record.get("umdNm")), []).append(record)

    complexes: List[Dict[str, Any]] = []
    for (name, umd), recs in groups.items():
        trade_prices = sorted(r["pricePerPy"] for r in recs if r.get("dealKind") == "trade" and r.get("pricePerPy"))
        coords = [(r["lng"], r["lat"]) for r in recs if _is_num(r.get("lat")) and _is_num(r.get("lng"))]
        center = None
        if coords:
            center = {
                "lng": sum(c[0] for c in coords) / len(coords),
                "lat": sum(c[1] for c in coords) / len(coords),
            }
        latest = max((r.get("transactionDate") or "" for r in recs), default="")
        complexes.append({
            "complexName": name,
            "umdNm": umd,
            "count": len(recs),
            "tradeCount": len(trade_prices),
            "medianPricePerPy": round(statistics.median(trade_prices)) if trade_prices else None,
            "minPricePerPy": trade_prices[0] if trade_prices else None,
            "maxPricePerPy": trade_prices[-1] if trade_prices else None,
            "latestDealDate": latest or None,
            "lat": center["lat"] if center else None,
            "lng": center["lng"] if center else None,
        })
    complexes.sort(key=lambda c: (c["tradeCount"] or 0, c["count"]), reverse=True)
    return complexes


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def summarize_transactions(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    trade_prices = [r["pricePerPy"] for r in records if r.get("dealKind") == "trade" and r.get("pricePerPy")]
    avg = round(sum(trade_prices) / len(trade_prices)) if trade_prices else None
    return {
        "totalCount": len(records),
        "tradeCount": sum(1 for r in records if r.get("dealKind") == "trade"),
        "rentCount": sum(1 for r in records if r.get("dealKind") == "rent"),
        "avgPricePerPy": avg,
    }
