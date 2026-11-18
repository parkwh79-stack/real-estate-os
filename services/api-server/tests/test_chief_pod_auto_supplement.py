from use_cases.chief_pod_auto_supplement import auto_supplement_pod_inputs


def _fake_resolver_resolved(address):
    return {
        "status": "resolved",
        "pnu": "4146310300100130000",
        "center": {"lng": 127.1, "lat": 37.27},
        "officialLandPricePerSqm": 1466000.0,
        "officialLandPriceBaseYear": "2025",
    }


def _fake_resolver_failed(address):
    return {"status": "geocode_failed"}


class _FakeConnector:
    """MOLIT connector stub returning two trade records."""

    def fetch(self, *, lawd_cd, deal_ym, asset_type, deal_kind, num_rows):
        return (
            [
                {"dealType": "trade", "priceKrw": 500_000_000, "source": "public_api"},
                {"dealType": "trade", "priceKrw": 620_000_000, "source": "public_api"},
            ],
            {"status": "ok"},
        )


class _EmptyConnector:
    def fetch(self, **kwargs):
        return [], {"status": "ok", "reason": "no rows"}


def test_supplements_pnu_and_market_from_parsed_address():
    project = {
        "project": {"name": "용인 상갈 촉진지구"},
        "land": {"address": "경기도 용인시 기흥구 상갈동 13"},
        "source_documents": [{"documentType": "business_plan"}],
    }

    log = auto_supplement_pod_inputs(
        "642", project, resolver=_fake_resolver_resolved, connector_factory=_FakeConnector
    )

    statuses = {a["itemId"]: a["status"] for a in log}
    assert statuses["pnu_or_land_ledger_missing"] == "supplemented"
    assert statuses["market_evidence_missing"] == "supplemented"
    # values actually landed on the project (pods read these)
    assert project["land"]["pnu"] == "4146310300100130000"
    assert project["land"]["officialLandPricePerSqm"] == 1466000.0
    # 3 recent months x 2 rows from the stub connector
    assert len(project["market"]["transactionComparables"]) == 6
    # provenance is auditable
    assert project["autoSupplementLog"][0]["attempts"][0]["source"].startswith("VWorld")


def test_no_address_reports_needs_user_material_without_fabricating():
    project = {"project": {"name": "무주소 프로젝트"}, "land": {}}

    log = auto_supplement_pod_inputs(
        "1", project, resolver=_fake_resolver_resolved, connector_factory=_FakeConnector,
        address_miner=lambda pid, data: None,
    )

    land_attempt = next(a for a in log if a["itemId"] == "pnu_or_land_ledger_missing")
    assert land_attempt["status"] == "needs_user_material"
    assert "pnu" not in project.get("land", {})
    market_attempt = next(a for a in log if a["itemId"] == "market_evidence_missing")
    assert market_attempt["status"] == "blocked_by_dependency"
    assert "market" not in project


def test_geocode_failure_is_honest_fetch_failed():
    project = {"land": {"address": "존재하지 않는 주소 999-999"}}

    log = auto_supplement_pod_inputs(
        "1", project, resolver=_fake_resolver_failed, connector_factory=_EmptyConnector,
        address_miner=lambda pid, data: None,
    )

    land_attempt = next(a for a in log if a["itemId"] == "pnu_or_land_ledger_missing")
    assert land_attempt["status"] == "fetch_failed"
    assert project["land"].get("pnu") is None


def test_conflicting_public_parcel_never_overwrites_existing_pnu():
    project = {"land": {"address": "합성 주소", "pnu": "1111010100100010000"}}
    log = auto_supplement_pod_inputs("1", project, resolver=_fake_resolver_resolved, connector_factory=_EmptyConnector)
    assert log[0]["status"] == "needs_user_material"
    assert project["land"]["pnu"] == "1111010100100010000"
    assert "officialLandPricePerSqm" not in project["land"]


def test_failed_public_lookup_keeps_partial_analysis_available():
    def unavailable(_):
        raise ConnectionError("unavailable")
    project = {"land": {"address": "합성 주소"}}
    log = auto_supplement_pod_inputs("1", project, resolver=unavailable, connector_factory=_EmptyConnector)
    assert log[0]["status"] == "fetch_failed"
    assert "pnu" not in project["land"]


def test_empty_market_rows_reported_not_invented():
    project = {"land": {"address": "경기도 용인시 기흥구 상갈동 13"}}

    log = auto_supplement_pod_inputs(
        "1", project, resolver=_fake_resolver_resolved, connector_factory=_EmptyConnector,
        address_miner=lambda pid, data: None,
    )

    market_attempt = next(a for a in log if a["itemId"] == "market_evidence_missing")
    assert market_attempt["status"] == "fetch_failed"
    assert not (project.get("market") or {}).get("transactionComparables")
