from __future__ import annotations

from use_cases.market_comparables import (
    derive_asset_type,
    derive_lawd_cd,
    fetch_project_market_comparables,
    inject_market_comparables,
    live_market_comparables_enabled,
    recent_deal_yms,
)


class FakeConnector:
    def __init__(self, per_ym):
        self.per_ym = per_ym
        self.calls = []

    def fetch(self, *, lawd_cd, deal_ym, asset_type, deal_kind, num_rows):
        self.calls.append((lawd_cd, deal_ym, asset_type, deal_kind))
        recs = self.per_ym.get(deal_ym, [])
        return recs, {"status": "ok" if recs else "empty"}


def _rec(price_per_py):
    return {"sourceName": "국토교통부 실거래가", "sourceType": "public_api", "comparableCategory": "transaction", "pricePerPy": price_per_py, "areaSqm": 84.0, "dealKind": "trade"}


def test_enable_flag_parsing():
    assert live_market_comparables_enabled({"REALYTICS_LIVE_MARKET_COMPARABLES": "1"}) is True
    assert live_market_comparables_enabled({"REALYTICS_LIVE_MARKET_COMPARABLES": "true"}) is True
    assert live_market_comparables_enabled({"REALYTICS_LIVE_MARKET_COMPARABLES": "off"}) is False
    assert live_market_comparables_enabled({}) is False


def test_recent_deal_yms_wraps_year():
    assert recent_deal_yms(3, base=(2026, 1)) == ["202601", "202512", "202511"]
    assert recent_deal_yms(2, base=(2026, 6)) == ["202606", "202605"]


def test_derive_lawd_cd_from_pnu_then_field():
    assert derive_lawd_cd({"land_acquisition": [{"pnu": "1165010800113730000"}]}) == "11650"
    assert derive_lawd_cd({"project": {"lawdCd": "11680"}}) == "11680"
    assert derive_lawd_cd({}) is None


def test_derive_asset_type_maps_and_defaults():
    assert derive_asset_type({"project": {"type": "officetel"}}) == "officetel"
    assert derive_asset_type({"project": {"type": "주상복합"}}) == "apartment"
    assert derive_asset_type({"marketability": {"productType": "지식산업센터"}}) == "commercial"
    assert derive_asset_type({}) == "apartment"


def test_fetch_caps_at_max_records_and_stops_early():
    conn = FakeConnector({"202606": [_rec(8000_0000)] * 30, "202605": [_rec(7000_0000)] * 30})
    project = {"land_acquisition": [{"pnu": "1165010800113730000"}], "project": {"type": "apartment"}}
    records, diag = fetch_project_market_comparables(project, connector=conn, deal_yms=["202606", "202605", "202604"], max_records=40)
    assert len(records) == 40
    assert diag["status"] == "ok" and diag["lawdCd"] == "11650" and diag["assetType"] == "apartment"
    # stopped after 2 months (40 reached) — 3rd month not queried
    assert [c[1] for c in conn.calls] == ["202606", "202605"]


def test_fetch_skipped_without_lawd_cd():
    records, diag = fetch_project_market_comparables({}, connector=FakeConnector({}), deal_yms=["202605"])
    assert records == [] and diag["status"] == "skipped" and diag["reason"] == "no_lawd_cd"


def test_inject_merges_into_market_dict():
    target = {"market": {"transactionComparables": [_rec(1)]}}
    n = inject_market_comparables(target, [_rec(2), _rec(3)])
    assert n == 2 and len(target["market"]["transactionComparables"]) == 3
    # creates the market dict when absent
    fresh = {}
    assert inject_market_comparables(fresh, [_rec(4)]) == 1
    assert fresh["market"]["transactionComparables"][0]["pricePerPy"] == 4
    assert inject_market_comparables({}, []) == 0
