from agents.land_ledger_enrichment_agent import LandLedgerEnrichmentAgent
from integrations.land_data_connectors import LandDataConnectorRouter, generate_pnu_from_parcel
from scripts.fetch_land_evidence_from_connectors import build_evidence_payload


def test_generate_pnu_from_legal_dong_and_lot_fields():
    pnu = generate_pnu_from_parcel(
        {"addressFragment": "본리동 123-4", "mainLotNo": 123, "subLotNo": 4},
        "2729011700",
    )

    assert pnu["pnu"] == "2729011700101230004"
    assert pnu["matchConfidence"] == 0.95
    assert pnu["matchMethod"] == "legal_dong_code_and_normalized_lot"


def test_live_connector_router_skips_missing_configuration_without_fabricating_records():
    router = LandDataConnectorRouter(environ={})
    result = router.fetch_records_for_parcels(
        [{"sequence": 1, "addressFragment": "본리동 123-4", "mainLotNo": 123, "subLotNo": 4}],
        legal_dong_code="2729011700",
        base_year=2026,
    )

    assert result["schemaVersion"] == "realytics-land-data-connectors-v1"
    assert result["pnuResolutions"][0]["pnu"] == "2729011700101230004"
    assert result["officialLandPrices"] == []
    assert result["cadastralCharacteristics"] == []
    statuses = {item["status"] for item in result["diagnostics"]}
    assert "skipped_missing_config" in statuses


def test_land_ledger_enrichment_live_connector_mode_records_diagnostics():
    analysis = {
        "parcels": [
            {
                "sequence": 1,
                "addressFragment": "본리동 123-4",
                "mainLotNo": 123,
                "subLotNo": 4,
                "areaSqm": 5000,
            }
        ]
    }

    result = LandLedgerEnrichmentAgent().enrich(
        analysis,
        {
            "legalDongCode": "2729011700",
            "useLiveConnectors": True,
            "connectorTimeoutSeconds": 0.1,
        },
    )

    parcel = result["parcels"][0]
    assert parcel["pnu"] == "2729011700101230004"
    assert result["enrichmentRun"]["liveConnectorResult"]["mode"] == "live_connector_router"
    assert result["enrichmentRun"]["liveConnectorResult"]["diagnostics"]
    assert result["enrichmentRun"]["status"] == "partial_success"


def test_land_characteristics_are_attached_to_parcel_and_supply_price_fallback():
    analysis = {"parcels": [{"sequence": 1, "pnu": "1111010100100020001", "areaSqm": 178.5}]}
    result = LandLedgerEnrichmentAgent().enrich(
        analysis,
        {
            "landCharacteristicsRecords": [{
                "pnu": "1111010100100020001",
                "areaSqm": 178.5,
                "landCategoryName": "대",
                "useDistrictName1": "자연녹지지역",
                "roadSideName": "중로한면",
                "officialLandPricePerSqm": 415000.0,
                "standardYear": "2017",
                "baseDate": "2017-09-07",
                "sourceName": "VWorld land characteristics",
                "sourceUrlOrEvidenceId": "https://api.vworld.kr/ned/data/getLandCharacteristics?pnu=1111010100100020001",
            }],
            "registryEvidence": [{"pnu": "1111010100100020001", "verificationStatus": "attached"}],
        },
    )

    parcel = result["parcels"][0]
    assert parcel["landCharacteristics"]["useDistrictName1"] == "자연녹지지역"
    assert result["enrichmentRun"]["parcelResults"][0]["landCharacteristicsAttached"] is True
    assert parcel["officialLandPricePerSqm"] == 415000.0
    assert any(item["evidenceType"] == "land_characteristics" for item in parcel["evidence"])


def test_connector_evidence_payload_preserves_source_records_and_diagnostics():
    payload = {"project": {"name": "Connector evidence test"}}
    connector_result = {
        "pnuResolutions": [{"pnu": "2729011700100010004"}],
        "officialLandPrices": [
            {
                "pnu": "2729011700100010004",
                "pricePerSqm": 1000000,
                "baseYear": 2026,
                "sourceName": "configured_official_land_price_connector",
                "sourceUrlOrEvidenceId": "https://example.test/official-price",
            }
        ],
        "cadastralCharacteristics": [
            {
                "pnu": "2729011700100010004",
                "zoning": "commercial",
                "sourceName": "configured_cadastral_connector",
                "sourceUrlOrEvidenceId": "https://example.test/cadastral",
            }
        ],
        "registryEvidence": [],
        "diagnostics": [
            {"connector": "official_land_price", "status": "fetched"},
            {"connector": "cadastral", "status": "fetched"},
        ],
        "dataSourcePolicy": {"registryEvidence": "manual evidence required"},
    }

    evidence = build_evidence_payload(payload, connector_result)

    assert evidence["schemaVersion"] == "realytics-land-connector-evidence-v1"
    assert evidence["projectName"] == "Connector evidence test"
    assert evidence["officialLandPrices"][0]["pricePerSqm"] == 1000000
    assert evidence["cadastralCharacteristics"][0]["zoning"] == "commercial"
    assert evidence["sourceMetadata"]["confidence"] == 1.0
    assert evidence["dataSourcePolicy"]["registryEvidence"] == "manual evidence required"


def test_land_use_planning_connector_maps_statutory_fields_and_source_locator():
    router = LandDataConnectorRouter(environ={
        "REALYTICS_LAND_USE_URL_TEMPLATE": "https://example.test/land-use?pnu={pnu}&key={service_key}",
        "REALYTICS_LAND_USE_API_KEY": "test-key",
    })
    router._fetch_template = lambda *args, **kwargs: {
        "url": "https://example.test/land-use?pnu=REDACTED&key=REDACTED",
        "body": '{"items":[{"pnu":"2729011700101230004","zoning":"제2종일반주거지역","legalFar":200,"legalBcr":60,"districtUnitPlan":"미지정","effectiveDate":"2026-01-01"}]}'
    }

    result = router.fetch_records_for_parcels(
        [{"sequence": 1, "pnu": "2729011700101230004", "addressFragment": "본리동 123-4"}],
    )

    record = result["landUsePlanningRecords"][0]
    assert record["zoning"] == "제2종일반주거지역"
    assert record["legalFar"] == 200.0
    assert record["legalBcr"] == 60.0
    assert record["coverage"] == "complete"
    assert record["sourceUrlOrEvidenceId"].startswith("https://example.test/land-use")
    assert any(item["connector"] == "land_use_planning" and item["status"] == "fetched" for item in result["diagnostics"])


def test_vworld_cadastral_fallback_exposes_zoning_but_not_inferred_far_or_bcr(monkeypatch):
    monkeypatch.setattr(
        "integrations.land_data_connectors.fetch_parcel_by_pnu",
        lambda *args, **kwargs: (
            {"prposArea1Nm": "제2종일반주거지역", "lndpclAr": "500"},
            {"status": "resolved"},
        ),
    )
    monkeypatch.setattr(
        LandDataConnectorRouter,
        "_fetch_vworld_land_use_attr",
        lambda *args, **kwargs: {"error": "test fallback", "url": "https://example.test/vworld-land-use"},
    )
    router = LandDataConnectorRouter(environ={"VWORLD_API_KEY": "test-key"})
    result = router.fetch_records_for_parcels(
        [{"sequence": 1, "pnu": "2729011700101230004"}],
    )

    land_use = result["landUsePlanningRecords"][0]
    assert land_use["zoning"] == "제2종일반주거지역"
    assert land_use["legalFar"] is None
    assert land_use["legalBcr"] is None
    assert land_use["coverage"] == "zoning_only"
    assert any(item["status"] == "partial_fetched" for item in result["diagnostics"])


def test_vworld_land_use_attr_fetch_maps_designation_without_inventing_far_or_bcr(monkeypatch):
    router = LandDataConnectorRouter(environ={"VWORLD_API_KEY": "test-key"})
    monkeypatch.setattr(
        router,
        "_fetch_vworld_land_use_attr",
        lambda *args, **kwargs: {
            "url": "https://api.vworld.kr/ned/data/getLandUseAttr?pnu=2729011700101230004&key=***",
            "body": (
                '{"response":{"body":{"items":{"item":[{'
                '"pnu":"2729011700101230004",'
                '"prposAreaDstrcCode":"UQP110",'
                '"prposAreaDstrcCodeNm":"apartment_zone",'
                '"cnflcAt":"1",'
                '"cnflcAtNm":"included",'
                '"registDt":"2016-08-15",'
                '"lastUpdtDt":"2016-08-15"}]}}}}'
            ),
        },
    )

    result = router.fetch_records_for_parcels([{"sequence": 1, "pnu": "2729011700101230004"}])

    record = result["landUsePlanningRecords"][0]
    assert record["zoning"] is None
    assert record["landUseZones"] == ["apartment_zone"]
    assert record["legalFar"] is None
    assert record["legalBcr"] is None
    assert record["landUsePlanItems"][0]["designationCode"] == "UQP110"
    assert record["landUsePlanItems"][0]["registeredDate"] == "2016-08-15"
    assert any(item["connector"] == "land_use_planning" and item["status"] == "fetched" for item in result["diagnostics"])


def test_vworld_building_use_fetch_maps_building_program_fields(monkeypatch):
    router = LandDataConnectorRouter(environ={"VWORLD_API_KEY": "test-key"})
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _FakeHttpResponse(
            '{"response":{"body":{"items":{"item":[{'
            '"gisIdntfcNo":"gis-1",'
            '"pnu":"2729011700101230004",'
            '"buldIdntfcNo":"building-1",'
            '"buldPlotAr":"453",'
            '"buldBildngAr":"233.3",'
            '"buldTotar":"1154.16",'
            '"measrmtRt":"198.45",'
            '"btlRt":"51.5",'
            '"mainPrposCodeNm":"제2종근린생활시설",'
            '"detailPrposCodeNm":"일반음식점",'
            '"groundFloorCo":"5",'
            '"undgrndFloorCo":"1",'
            '"prmisnDe":"2004-11-30",'
            '"useConfmDe":"2005-07-19",'
            '"lastUpdtDt":"2026-07-01"}]}}}}'
        ),
    )

    result = router.fetch_records_for_parcels([{"sequence": 1, "pnu": "2729011700101230004"}])

    record = result["buildingUseRecords"][0]
    assert record["buildingId"] == "building-1"
    assert record["siteAreaSqm"] == 453.0
    assert record["buildingAreaSqm"] == 233.3
    assert record["totalFloorAreaSqm"] == 1154.16
    assert record["floorAreaRatio"] == 198.45
    assert record["buildingCoverageRatio"] == 51.5
    assert record["mainUseName"] == "제2종근린생활시설"
    assert record["detailUseName"] == "일반음식점"
    assert record["approvalDate"] == "2005-07-19"
    assert any(item["connector"] == "building_use" and item["status"] == "fetched" for item in result["diagnostics"])


def test_vworld_region_land_price_change_maps_monthly_and_cumulative_rates(monkeypatch):
    router = LandDataConnectorRouter(environ={"VWORLD_API_KEY": "test-key"})
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _FakeHttpResponse(
            '{"byRegions":{"field":[{'
            '"stdrYear":"2026","stdrMt":"06",'
            '"ldCtprvnCode":"51","ldCtprvnNm":"강원특별자치도",'
            '"ldCpsgCode":"51150","ldCpsgCodeNm":"강릉시",'
            '"pclndIndex":"100.885","pclndChgRt":"0.218",'
            '"acmtlPclndChgRt":"1.234","lastUpdtDt":"2026-07-07"}]}}'
        ),
    )

    result = router._fetch_region_land_price_change(
        "5115034022",
        standard_year=2026,
        standard_month=6,
        scope_div="A",
        timeout_seconds=1,
    )

    record = result["records"][0]
    assert record["regionCode"] == "5115034022"
    assert record["cityCountyName"] == "강릉시"
    assert record["landPriceIndex"] == 100.885
    assert record["monthlyChangeRate"] == 0.218
    assert record["cumulativeChangeRate"] == 1.234
    assert record["baseDate"] == "2026-07-07"
    assert result["diagnostics"][0]["status"] == "fetched"


def test_vworld_land_characteristics_maps_parcel_area_use_terrain_road_and_price(monkeypatch):
    router = LandDataConnectorRouter(environ={"VWORLD_API_KEY": "test-key"})
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: _FakeHttpResponse(
            '{"response":{"body":{"items":{"item":[{'
            '"pnu":"1111010100100020001","ldCode":"1111010100",'
            '"ldCodeNm":"서울특별시 종로구 청운동","mnnmSlno":"2-1",'
            '"stdrYear":"2017","stdrMt":"01",'
            '"lndcgrCode":"08","lndcgrCodeNm":"대",'
            '"lndpclAr":"178.5","prposArea1":"43",'
            '"prposArea1Nm":"자연녹지지역","prposArea2":"00",'
            '"prposArea2Nm":"지정되지않음","ladUseSittn":"930",'
            '"ladUseSittnNm":"공원등","tpgrphHgCode":"04",'
            '"tpgrphHgCodeNm":"급경사","tpgrphFrmCode":"04",'
            '"tpgrphFrmCodeNm":"사다리형","roadSideCode":"04",'
            '"roadSideCodeNm":"중로한면","pblntfPclnd":"415000",'
            '"lastUpdtDt":"2017-09-07"}]}}}}'
        ),
    )

    result = router._fetch_land_characteristics_record(
        "1111010100100020001",
        standard_year=2017,
        parcel={"sequence": 1, "pnu": "1111010100100020001"},
        timeout_seconds=1,
    )

    record = result["record"]
    assert record["areaSqm"] == 178.5
    assert record["landCategoryName"] == "대"
    assert record["useDistrictName1"] == "자연녹지지역"
    assert record["landUseSituationName"] == "공원등"
    assert record["terrainHeightName"] == "급경사"
    assert record["roadSideName"] == "중로한면"
    assert record["officialLandPricePerSqm"] == 415000.0
    assert result["diagnostics"][0]["status"] == "fetched"


class _FakeHttpResponse:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body
