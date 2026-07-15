from __future__ import annotations

import pytest

from parsing_platform.field_registry import (
    field_registry_catalog,
    project_approved_inputs,
    validate_candidate_against_registry,
    validate_projection_registry,
)


def _excel_locator(cell: str = "B12") -> dict:
    return {
        "documentVersionId": "document-version-1",
        "fileSha256": "a" * 64,
        "sheetName": "사업수지",
        "cellReference": cell,
        "rawTextSha256": "b" * 64,
    }


def _api_locator(row_id: str = "row-1") -> dict:
    return {
        "apiEndpoint": "https://example.test/v1/records",
        "apiRowId": row_id,
    }


def _calculation_locator(
    json_path: str = "/summary/maxMinus",
    *,
    output_hash: str = "c" * 64,
) -> dict:
    return {
        "artifactKey": "cash-flow-run-result",
        "jsonPath": json_path,
        "metadata": {
            "calculationRunId": "calculation-run-1",
            "outputHash": output_hash,
        },
    }


def _formula_locator(cell: str = "F124") -> dict:
    return {
        **_excel_locator(cell),
        "workbookFormula": "=F120-F122",
        "workbookCachedValue": -1_000_000,
    }


def test_registry_marks_unknown_type_unit_entity_and_imprecise_evidence_as_blocking():
    unknown = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="custom.unapproved_metric",
        normalized_value=1,
        unit=None,
        entity_id=None,
        evidence=[_excel_locator()],
    )
    assert [item.code for item in unknown] == ["CANONICAL_FIELD_UNREGISTERED"]

    issues = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="land.official_land_price_per_sqm",
        normalized_value="1000000",
        unit="KRW",
        entity_id=None,
        evidence=[{"documentVersionId": "document-version-1", "pageNumber": 1}],
    )
    assert {item.code for item in issues} == {
        "CANONICAL_VALUE_TYPE_MISMATCH",
        "CANONICAL_UNIT_INVALID",
        "CANONICAL_ENTITY_ID_REQUIRED",
        "EXACT_SOURCE_LOCATOR_REQUIRED",
    }
    assert all(item.blocking for item in issues)


def test_projection_registry_matches_active_schema_contracts():
    assert validate_projection_registry() == []
    catalog = field_registry_catalog()
    assert catalog["status"] == "internal_controlled_baseline"
    assert catalog["normativeIndustryStandardCompliance"] is False


def test_registry_catalog_covers_pilot_domains_and_serializes_allowed_values():
    catalog = field_registry_catalog()
    fields = {item["fieldKey"]: item for item in catalog["fields"]}

    assert {
        "land.official_land_price_base_date",
        "land.characteristics.land_category_code",
        "land.zoning.primary_use_district_code",
        "land.characteristics.land_use_situation_code",
        "land.characteristics.terrain_height_code",
        "land.characteristics.terrain_shape_code",
        "land.characteristics.road_side_code",
        "market.comparable.type",
        "market.comparable.price_per_py",
        "financing.tranche.type",
        "financing.tranche.interest_rate",
        "financing.tranche.maturity_date",
        "financing.tranche.priority",
        "cash_flow.monthly_net_cash_flow",
        "cash_flow.max_minus_amount",
        "waterfall.tranche.recovery_rate",
        "risk.unsold_ltv",
        "risk.unsold_ltv_cure.status",
        "exit.required_sales_rate",
        "approval_condition.text",
    } <= set(fields)
    assert fields["financing.tranche.type"]["allowedValues"] == [
        "senior",
        "mezzanine",
        "junior",
    ]
    assert fields["market.comparable.type"]["allowedValues"] == [
        "transaction",
        "presale",
    ]
    assert fields["risk.unsold_ltv"]["metadata"]["mayExceedOne"] is True
    assert catalog["normativeIndustryStandardCompliance"] is False


def test_financial_and_land_ranges_use_one_canonical_unit_and_format():
    pnu_issues = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="land.pnu",
        normalized_value="123-45",
        unit=None,
        entity_id="parcel-1",
        evidence=[_excel_locator()],
    )
    assert "CANONICAL_STRING_FORMAT_INVALID" in {item.code for item in pnu_issues}

    rate_issues = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="market.completion_sales_rate",
        normalized_value=80,
        unit="percent",
        entity_id=None,
        evidence=[_excel_locator()],
    )
    assert {item.code for item in rate_issues}.issuperset(
        {"CANONICAL_UNIT_INVALID", "CANONICAL_VALUE_ABOVE_MAXIMUM"}
    )


def test_112_parcel_enrichment_fields_require_stable_entities_and_exact_rows():
    for parcel_index in range(1, 113):
        assert validate_candidate_against_registry(
            ontology_version="realytics-real-estate-domain-standard-v1",
            field_key="land.zoning.primary_use_district_code",
            normalized_value="UQA123",
            unit=None,
            entity_id=f"parcel-{parcel_index}",
            evidence=[_api_locator(f"parcel-{parcel_index}")],
        ) == []

    invalid_date = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="land.official_land_price_base_date",
        normalized_value="2026-02-30",
        unit=None,
        entity_id=None,
        evidence=[{"apiEndpoint": "https://example.test/v1/records"}],
    )
    assert {item.code for item in invalid_date} == {
        "CANONICAL_VALUE_TYPE_MISMATCH",
        "CANONICAL_ENTITY_ID_REQUIRED",
        "EXACT_SOURCE_LOCATOR_REQUIRED",
    }


def test_comparable_and_tranche_enums_types_units_ranges_and_entities():
    comparable = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="market.comparable.type",
        normalized_value="listing",
        unit=None,
        entity_id=None,
        evidence=[_api_locator()],
    )
    assert {item.code for item in comparable} == {
        "CANONICAL_VALUE_NOT_ALLOWED",
        "CANONICAL_ENTITY_ID_REQUIRED",
    }

    comparable_price = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="market.comparable.price_per_py",
        normalized_value=-1,
        unit="krw",
        entity_id="comparable-1",
        evidence=[{"documentVersionId": "document-version-1", "pageNumber": 2}],
    )
    assert {item.code for item in comparable_price} == {
        "CANONICAL_VALUE_BELOW_MINIMUM",
        "CANONICAL_UNIT_INVALID",
        "EXACT_SOURCE_LOCATOR_REQUIRED",
    }

    assert validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="financing.tranche.type",
        normalized_value="mezzanine",
        unit=None,
        entity_id="tranche-b",
        evidence=[_excel_locator()],
    ) == []

    interest_rate = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="financing.tranche.interest_rate",
        normalized_value="12.5",
        unit="percent",
        entity_id=None,
        evidence=[{"documentVersionId": "document-version-1", "pageNumber": 1}],
    )
    assert {item.code for item in interest_rate} == {
        "CANONICAL_VALUE_TYPE_MISMATCH",
        "CANONICAL_UNIT_INVALID",
        "CANONICAL_ENTITY_ID_REQUIRED",
        "EXACT_SOURCE_LOCATOR_REQUIRED",
    }

    invalid_ranges = [
        validate_candidate_against_registry(
            ontology_version="realytics-real-estate-domain-standard-v1",
            field_key="financing.tranche.interest_rate",
            normalized_value=1.01,
            unit="ratio",
            entity_id="tranche-a",
            evidence=[_excel_locator()],
        ),
        validate_candidate_against_registry(
            ontology_version="realytics-real-estate-domain-standard-v1",
            field_key="financing.tranche.priority",
            normalized_value=4,
            unit=None,
            entity_id="tranche-a",
            evidence=[_excel_locator()],
        ),
    ]
    assert all(
        "CANONICAL_VALUE_ABOVE_MAXIMUM" in {item.code for item in issues}
        for issues in invalid_ranges
    )


def test_monthly_cash_flow_rows_and_max_minus_keep_canonical_sign_conventions():
    assert validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cash_flow.monthly_net_cash_flow",
        normalized_value=-100_000_000,
        unit="krw",
        entity_id="month-12",
        evidence=[_calculation_locator("/monthlyCashFlow/11/netCashFlow")],
        origin_type="derived",
    ) == []

    monthly_inflow = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cash_flow.monthly_inflow",
        normalized_value=-1,
        unit="usd",
        entity_id=None,
        evidence=[{"documentVersionId": "document-version-1", "pageNumber": 3}],
    )
    assert {item.code for item in monthly_inflow} == {
        "CANONICAL_VALUE_BELOW_MINIMUM",
        "CANONICAL_UNIT_INVALID",
        "CANONICAL_ENTITY_ID_REQUIRED",
        "EXACT_SOURCE_LOCATOR_REQUIRED",
    }

    max_minus = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cash_flow.max_minus_amount",
        normalized_value=1,
        unit="krw",
        entity_id=None,
        evidence=[_calculation_locator()],
        origin_type="derived",
    )
    assert {item.code for item in max_minus} == {
        "CANONICAL_VALUE_ABOVE_MAXIMUM"
    }


def test_calculated_rates_preserve_risk_signals_but_bound_actual_and_recovery_rates():
    for field_key, value in (
        ("risk.unsold_ltv", 1.35),
        ("exit.required_sales_rate", 1.20),
    ):
        assert validate_candidate_against_registry(
            ontology_version="realytics-real-estate-domain-standard-v1",
            field_key=field_key,
            normalized_value=value,
            unit="ratio",
            entity_id=None,
            evidence=[_calculation_locator(f"/metrics/{field_key}")],
            origin_type="derived",
        ) == []

    actual_rate = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="exit.actual_sales_rate",
        normalized_value=1.01,
        unit="ratio",
        entity_id=None,
        evidence=[_excel_locator()],
    )
    recovery_rate = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="waterfall.tranche.recovery_rate",
        normalized_value=-0.01,
        unit="ratio",
        entity_id="tranche-a",
        evidence=[_calculation_locator("/rows/0/recoveryRate")],
        origin_type="derived",
    )
    assert "CANONICAL_VALUE_ABOVE_MAXIMUM" in {
        item.code for item in actual_rate
    }
    assert "CANONICAL_VALUE_BELOW_MINIMUM" in {
        item.code for item in recovery_rate
    }


def test_cure_and_approval_condition_statuses_are_controlled_values():
    cure = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="risk.unsold_ltv_cure.status",
        normalized_value="done",
        unit=None,
        entity_id="cure-1",
        evidence=[_excel_locator()],
    )
    condition = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="approval_condition.status",
        normalized_value="approved",
        unit=None,
        entity_id="condition-1",
        evidence=[_excel_locator()],
    )
    assert {item.code for item in cure} == {"CANONICAL_VALUE_NOT_ALLOWED"}
    assert {item.code for item in condition} == {"CANONICAL_VALUE_NOT_ALLOWED"}


@pytest.mark.parametrize("origin_type", ["extracted", "inferred", None])
def test_workbook_formula_cached_values_cannot_be_approved_as_source_values(origin_type):
    issues = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cash_flow.max_minus_amount",
        normalized_value=-1_000_000,
        unit="krw",
        entity_id=None,
        evidence=[_formula_locator()],
        origin_type=origin_type,
    )
    assert "WORKBOOK_FORMULA_RECALCULATION_REQUIRED" in {
        item.code for item in issues
    }


def test_sanitized_workbook_formula_hash_still_requires_recalculation():
    locator = _excel_locator()
    locator["metadata"] = {
        "workbookFormulaSha256": "d" * 64,
        "workbookFormulaPersisted": False,
        "formulaCacheAuthoritative": False,
        "workbookCachedValueApprovalEligible": False,
    }
    issues = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cash_flow.max_minus_amount",
        normalized_value=-1_000_000,
        unit="krw",
        entity_id=None,
        evidence=[locator],
        origin_type="extracted",
    )
    assert "WORKBOOK_FORMULA_RECALCULATION_REQUIRED" in {
        item.code for item in issues
    }


def test_workbook_formula_requires_derived_exact_calculation_output_binding():
    valid = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cash_flow.max_minus_amount",
        normalized_value=-1_000_000,
        unit="krw",
        entity_id=None,
        evidence=[_formula_locator(), _calculation_locator()],
        origin_type="derived",
    )
    assert valid == []

    invalid = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cash_flow.max_minus_amount",
        normalized_value=-1_000_000,
        unit="krw",
        entity_id=None,
        evidence=[
            _formula_locator(),
            _calculation_locator(output_hash="not-a-sha256"),
        ],
        origin_type="derived",
    )
    assert {item.code for item in invalid}.issuperset(
        {
            "EXACT_SOURCE_LOCATOR_REQUIRED",
            "WORKBOOK_FORMULA_RECALCULATION_REQUIRED",
        }
    )


def test_approved_inputs_project_to_scalar_and_entity_paths_with_lineage():
    result = project_approved_inputs(
        snapshot_id="snapshot-1",
        ontology_version="realytics-real-estate-domain-standard-v1",
        target_schema_id="realytics-land-ledger-import-v1",
        target_schema_version="1.0",
        input_items=[
            {
                "fieldKey": "land.pnu",
                "normalizedValue": "1168010100101230000",
                "entityId": "parcel-112",
                "selectionId": "selection-pnu",
                "candidateId": "candidate-pnu",
                "sourceLocator": _excel_locator("A112"),
            },
            {
                "fieldKey": "land.official_land_price_per_sqm",
                "normalizedValue": 3_500_000,
                "entityId": "parcel-112",
                "selectionId": "selection-price",
                "candidateId": "candidate-price",
                "sourceLocator": _excel_locator("D112"),
            },
        ],
    )

    parcel = result["payload"]["parcels"][0]
    assert parcel["_entityId"] == "parcel-112"
    assert parcel["pnu"] == "1168010100101230000"
    assert parcel["officialLandPrice"]["pricePerSqm"] == 3_500_000
    assert {item["selectionId"] for item in result["lineage"]} == {
        "selection-pnu",
        "selection-price",
    }
    assert result["authoritativeInputBoundary"] == "approved_snapshot_only"


def test_projection_rejects_silent_target_overwrite():
    items = [
        {
            "fieldKey": "feasibility.gross_floor_area_sqm",
            "normalizedValue": 100,
            "selectionId": "selection-1",
            "candidateId": "candidate-1",
        },
        {
            "fieldKey": "feasibility.gross_floor_area_sqm",
            "normalizedValue": 101,
            "selectionId": "selection-2",
            "candidateId": "candidate-2",
        },
    ]

    with pytest.raises(ValueError, match="silently overwrite"):
        project_approved_inputs(
            snapshot_id="snapshot-1",
            ontology_version="realytics-real-estate-domain-standard-v1",
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            input_items=items,
        )


def test_max_minus_projects_only_to_the_registered_existing_schema_path():
    result = project_approved_inputs(
        snapshot_id="snapshot-1",
        ontology_version="realytics-real-estate-domain-standard-v1",
        target_schema_id="realytics-cash-flow-run-request-v1",
        target_schema_version="1.0",
        input_items=[
            {
                "fieldKey": "cash_flow.max_minus_amount",
                "normalizedValue": -1_000_000,
                "selectionId": "selection-max-minus",
                "candidateId": "candidate-max-minus",
                "sourceLocator": _calculation_locator(),
            }
        ],
    )

    assert result["payload"]["maxMinus"]["amount"] == -1_000_000
    assert result["lineage"][0]["targetPath"] == "maxMinus.amount"
    assert result["lineage"][0]["sourceLocator"]["artifactKey"] == (
        "cash-flow-run-result"
    )
    assert result["lineage"][0]["sourceLocator"]["metadata"][
        "outputHash"
    ] == "c" * 64
