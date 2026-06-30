from core.schema_registry import (
    find_canonical_field,
    get_schema_contract,
    list_canonical_fields,
    list_schema_contracts,
    validate_required_fields,
)


def test_schema_registry_lists_core_agent_contracts():
    contracts = list_schema_contracts(status="active")
    schema_ids = {item["schemaId"] for item in contracts}
    assert "realytics-feasibility-study-v2" in schema_ids
    assert "realytics-land-ledger-import-v1" in schema_ids
    assert "realytics-marketability-analysis-v2" in schema_ids
    assert "realytics-cash-flow-run-request-v1" in schema_ids
    assert "realytics-scenario-set-v1" in schema_ids
    assert "realytics-evidence-record-v1" in schema_ids
    assert "realytics-phase11-pf-review-report-v1" in schema_ids


def test_schema_contract_exposes_fields_without_importing_agent_modules():
    contract = get_schema_contract("realytics-marketability-analysis-v2")
    assert contract is not None
    assert contract["ownerAgent"] == "MarketabilityAnalysisAgent"
    assert contract["artifactKey"] == "marketabilityAnalysis"
    field_paths = {item["path"] for item in contract["fields"]}
    assert "pricing.proposedPricePerPy" in field_paths
    assert "discountTrigger" in field_paths


def test_canonical_field_lookup_supports_cross_agent_meaning():
    field = find_canonical_field("land.pnu")
    assert field is not None
    assert "land" in field["owners"]
    assert "legal_review" in field["requiredFor"]
    assert "PNU" in field["aliases"]

    evidence_fields = list_canonical_fields(owner="evidence_graph")
    assert any(item["path"] == "sourceMetadata.sourceLocator" for item in evidence_fields)


def test_required_field_validation_reports_missing_pf_review_inputs():
    payload = {
        "project": {"name": "Bonri Mixed-use"},
        "revenue": {"totalSalesRevenue": 165_600_000_000},
        "cost": {},
        "financing": {"pfAmount": 90_000_000_000},
    }
    result = validate_required_fields(
        "realytics-feasibility-study-v2",
        payload,
        required_for="pf_review",
    )
    assert result["status"] == "missing_required_fields"
    assert "cost.totalProjectCost" in result["missing"]
    assert "revenue.totalSalesRevenue" in result["present"]
    assert "financing.pfAmount" in result["present"]


def test_required_field_validation_handles_repeated_land_ledger_rows():
    payload = {
        "project": {"name": "Land Ledger"},
        "land": {"address": "Daegu", "pnu": "2729011700101230004"},
        "parcels": [
            {
                "rawLotNumber": "123-4",
                "normalizedLotNumber": "123-4",
                "registryEvidence": {"evidenceId": "registry-001"},
            }
        ],
    }
    result = validate_required_fields(
        "realytics-land-ledger-import-v1",
        payload,
        required_for="legal_review",
    )
    assert "land.pnu" in result["present"]
    assert "parcels[].registryEvidence.evidenceId" in result["present"]
    assert "parcels[].officialLandPrice.pricePerSqm" in result["missing"]
