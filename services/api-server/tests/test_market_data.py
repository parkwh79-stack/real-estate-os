from __future__ import annotations

from fastapi.testclient import TestClient

from main import app
from integrations.market_data import (
    MolitRtmsConnector,
    ValuemapConnector,
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


def test_unsupported_asset_combo():
    connector = MolitRtmsConnector(environ={"DATA_GO_KR_API_KEY": "K"})
    _, diag = connector.fetch(lawd_cd="11650", deal_ym="202603", asset_type="land", deal_kind="rent")
    assert diag["status"] == "unsupported"


def test_valuemap_mock_fallback_is_labelled():
    records, diag = ValuemapConnector(environ={}).fetch(lat=37.5, lng=127.0)
    assert diag["status"] == "mock"
    assert all(r["sourceType"] == "partner_api" and r["comparableCategory"] == "listing" for r in records)
    assert all("mock" in r["source"] for r in records)


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
