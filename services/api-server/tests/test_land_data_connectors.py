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
