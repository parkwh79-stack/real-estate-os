from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from integrations.market_data import (
    MolitRtmsConnector,
    ValuemapConnector,
    aggregate_by_complex,
    attach_coordinates,
    geocode_query_for,
    lawd_cd_from_pnu,
    market_data_status,
    parse_rtms_items,
    summarize_transactions,
)

# A minimal RTMS 아파트 매매 XML body (public-API shape).
_APT_TRADE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><body><items>
  <item>
    <aptNm>래미안</aptNm><dealAmount>250,000</dealAmount><excluUseAr>84.97</excluUseAr>
    <dealYear>2026</dealYear><dealMonth>3</dealMonth><dealDay>14</dealDay>
    <umdNm>서초동</umdNm><jibun>1373</jibun><floor>12</floor><buildYear>2010</buildYear>
  </item>
  <item>
    <aptNm>방배자이</aptNm><dealAmount>180,000</dealAmount><excluUseAr>59.92</excluUseAr>
    <dealYear>2026</dealYear><dealMonth>3</dealMonth><dealDay>2</dealDay>
    <umdNm>방배동</umdNm><jibun>2-1</jibun><floor>5</floor>
  </item>
</items></body></response>"""

_APT_RENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><body><items>
  <item>
    <aptNm>래미안</aptNm><deposit>70,000</deposit><monthlyRent>0</monthlyRent><excluUseAr>84.97</excluUseAr>
    <dealYear>2026</dealYear><dealMonth>3</dealMonth><dealDay>10</dealDay><umdNm>서초동</umdNm><jibun>1373</jibun>
  </item>
  <item>
    <aptNm>방배자이</aptNm><deposit>20,000</deposit><monthlyRent>150</monthlyRent><excluUseAr>59.92</excluUseAr>
    <dealYear>2026</dealYear><dealMonth>3</dealMonth><dealDay>5</dealDay><umdNm>방배동</umdNm><jibun>2-1</jibun>
  </item>
</items></body></response>"""


def test_parse_rtms_trade_normalizes_price_and_units():
    records = parse_rtms_items(_APT_TRADE_XML, asset_type="apartment", deal_kind="trade", lawd_cd="11650")
    assert len(records) == 2
    first = records[0]
    assert first["totalPrice"] == 250000 * 10000  # 만원 → 원
    assert first["dealType"] == "매매"
    assert first["comparableCategory"] == "transaction"
    assert first["sourceType"] == "public_api"
    assert first["transactionDate"] == "2026-03-14"
    assert first["address"] == "서초동 1373 래미안"
    # 25억 / (84.97/3.305785 ≈ 25.7py) ≈ 9,726만원/py
    assert 90_000_000 < first["pricePerPy"] < 110_000_000


def test_parse_rtms_rent_classifies_jeonse_vs_wolse():
    records = parse_rtms_items(_APT_RENT_XML, asset_type="apartment", deal_kind="rent", lawd_cd="11650")
    assert records[0]["dealType"] == "전세" and records[0]["depositKRW"] == 70000 * 10000
    assert records[1]["dealType"] == "월세" and records[1]["monthlyRentKRW"] == 150 * 10000


def test_summarize_only_averages_trades():
    records = parse_rtms_items(_APT_TRADE_XML, asset_type="apartment", deal_kind="trade", lawd_cd="11650")
    summary = summarize_transactions(records)
    assert summary["tradeCount"] == 2 and summary["rentCount"] == 0
    assert summary["avgPricePerPy"] is not None


def test_connector_gated_without_key():
    connector = MolitRtmsConnector(environ={})  # no DATA_GO_KR key
    records, diag = connector.fetch(lawd_cd="11650", deal_ym="202603")
    assert records == [] and diag["status"] == "skipped"


def test_connector_uses_injected_http_and_key():
    captured = {}

    def fake_http(url):
        captured["url"] = url
        return _APT_TRADE_XML

    connector = MolitRtmsConnector(environ={"DATA_GO_KR_API_KEY": "SECRET"}, http_get=fake_http)
    assert connector.configured() is True
    records, diag = connector.fetch(lawd_cd="11650", deal_ym="202603", asset_type="apartment", deal_kind="trade")
    assert diag["status"] == "ok" and len(records) == 2
    assert "LAWD_CD=11650" in captured["url"] and "DEAL_YMD=202603" in captured["url"]


def test_forbidden_when_api_not_subscribed():
    import urllib.error

    def forbidden_http(url):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    connector = MolitRtmsConnector(environ={"DATA_GO_KR_API_KEY": "K"}, http_get=forbidden_http)
    records, diag = connector.fetch(lawd_cd="11650", deal_ym="202605", asset_type="officetel", deal_kind="trade")
    assert records == [] and diag["status"] == "forbidden" and diag["httpStatus"] == 403
    assert "활용신청" in diag["message"]


def test_unsupported_asset_combo():
    connector = MolitRtmsConnector(environ={"DATA_GO_KR_API_KEY": "K"})
    _, diag = connector.fetch(lawd_cd="11650", deal_ym="202603", asset_type="land", deal_kind="rent")
    assert diag["status"] == "unsupported"


def test_valuemap_mock_fallback_is_labelled():
    records, diag = ValuemapConnector(environ={}).fetch(lat=37.5, lng=127.0)
    assert diag["status"] == "mock"
    assert all(r["sourceType"] == "partner_api" and r["comparableCategory"] == "listing" for r in records)
    assert all("mock" in r["source"] for r in records)


def test_attach_coordinates_geocodes_and_caches():
    records = parse_rtms_items(_APT_TRADE_XML, asset_type="apartment", deal_kind="trade", lawd_cd="11650")
    # inject 시군구 so the geocode query is buildable
    for r in records:
        r["sggNm"] = "서초구"
    calls = []

    def fake_geocoder(address):
        calls.append(address)
        return {"lng": 127.02, "lat": 37.49}

    located = attach_coordinates(records, fake_geocoder, limit=30)
    assert located == 2
    assert records[0]["lat"] == 37.49 and records[0]["lng"] == 127.02
    assert geocode_query_for(records[0]) == "서초구 서초동 1373"
    # distinct addresses → two geocode calls (no dupes here)
    assert len(calls) == 2


def test_attach_coordinates_skips_when_no_query_and_on_none():
    records = [{"umdNm": None, "jibun": None}, {"sggNm": "강남구", "umdNm": "역삼동", "jibun": "1"}]
    located = attach_coordinates(records, lambda a: None, limit=30)
    assert located == 0
    assert "lat" not in records[1]


def test_aggregate_by_complex_medians_and_centroid():
    records = [
        {"buildingName": "래미안", "umdNm": "서초동", "dealKind": "trade", "pricePerPy": 6000, "transactionDate": "2026-03", "lat": 37.50, "lng": 127.02},
        {"buildingName": "래미안", "umdNm": "서초동", "dealKind": "trade", "pricePerPy": 8000, "transactionDate": "2026-05", "lat": 37.52, "lng": 127.04},
        {"buildingName": "래미안", "umdNm": "서초동", "dealKind": "rent", "depositKRW": 700000000, "transactionDate": "2026-04"},
        {"buildingName": "방배자이", "umdNm": "방배동", "dealKind": "trade", "pricePerPy": 5000, "transactionDate": "2026-02", "lat": 37.48, "lng": 127.00},
    ]
    complexes = aggregate_by_complex(records)
    assert [c["complexName"] for c in complexes] == ["래미안", "방배자이"]  # sorted by tradeCount desc
    ramian = complexes[0]
    assert ramian["count"] == 3 and ramian["tradeCount"] == 2
    assert ramian["medianPricePerPy"] == 7000  # median(6000, 8000)
    assert ramian["minPricePerPy"] == 6000 and ramian["maxPricePerPy"] == 8000
    assert ramian["latestDealDate"] == "2026-05"
    # centroid of the two geocoded members
    assert round(ramian["lat"], 2) == 37.51 and round(ramian["lng"], 2) == 127.03


def test_aggregate_handles_no_trades():
    complexes = aggregate_by_complex([{"buildingName": "X", "umdNm": "동", "dealKind": "rent", "depositKRW": 1}])
    assert complexes[0]["medianPricePerPy"] is None and complexes[0]["tradeCount"] == 0


def test_lawd_cd_from_pnu():
    assert lawd_cd_from_pnu("1165010800113730000") == "11650"  # 서울 서초구
    assert lawd_cd_from_pnu("116") is None
    assert lawd_cd_from_pnu(None) is None


def test_market_status_is_secret_safe():
    status = market_data_status({"DATA_GO_KR_API_KEY": "SECRET-KEY", "VALUEMAP_API_URL": "https://x", "VALUEMAP_API_KEY": "PK"})
    assert status["molitRtms"]["configured"] is True
    assert status["valuemap"]["configured"] is True
    assert "SECRET-KEY" not in str(status) and "PK" not in str(status)


def test_market_endpoints_smoke():
    client = TestClient(app)
    assert client.get("/api/market/status").status_code == 200
    # without a key the transactions endpoint still returns 200 with a diagnostic
    res = client.get("/api/market/transactions?lawd_cd=11650&deal_ym=202603")
    assert res.status_code == 200
    assert res.json()["schemaVersion"] == "realytics-market-transactions-v1"
