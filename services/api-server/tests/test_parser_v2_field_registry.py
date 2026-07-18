from __future__ import annotations

import copy
from dataclasses import replace

import pytest

import parsing_platform.field_registry as field_registry
from parsing_platform.canonical_fields import (
    CANONICAL_FIELD_NAMESPACE_VERSION,
    canonical_field_key,
    canonical_namespace_catalog,
)
from parsing_platform.field_registry import (
    FIELD_REGISTRY_VERSION,
    field_registry_catalog,
    project_approved_inputs,
    validate_candidate_against_registry,
    validate_projection_registry,
)
from schemas.feasibility_study import FeasibilityStudyV2


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


def _revenue_cost_basis(**overrides) -> dict:
    return {
        "basis": {
            "vatTreatment": "included",
            "amountScope": "gross",
            "allocationBasis": "direct",
            "currency": "KRW",
            **overrides,
        }
    }


def _financing_basis(**overrides) -> dict:
    return {
        "basis": {
            "vatTreatment": "not_applicable",
            "amountScope": "not_applicable",
            "allocationBasis": "not_applicable",
            "currency": "KRW",
            "financingBalanceBasis": "commitment",
            **overrides,
        }
    }


def _approved_snapshot(
    input_items: list[dict],
    *,
    snapshot_id: str = "snapshot-1",
) -> dict:
    """Wrap compact projection inputs in the real strict snapshot wire model."""

    timestamp = "2026-07-18T00:00:00+00:00"
    ontology_version = "realytics-real-estate-domain-standard-v1"
    unit_by_field = {
        "program.gross_floor_area_sqm": "sqm",
        "revenue.total_sales_revenue": "krw",
        "cost.total_project_cost": "krw",
        "cost.land_cost": "krw",
        "financing.pf_amount": "krw",
        "land.registered_area_sqm": "sqm",
        "land.official_land_price_per_sqm": "krw_per_sqm",
        "market.proposed_price_per_py": "krw_per_py",
        "market.comparable_median_price_per_py": "krw_per_py",
        "cash_flow.max_minus_amount": "krw",
    }
    selections = []
    approved_items = []
    for index, raw_item in enumerate(input_items, start=1):
        field_key = canonical_field_key(str(raw_item["fieldKey"]))
        selection_id = str(
            raw_item.get("selectionId") or f"selection-{index}"
        )
        candidate_id = str(raw_item.get("candidateId") or f"candidate-{index}")
        review_decision_id = str(
            raw_item.get("reviewDecisionId") or f"review-{index}"
        )
        unit = raw_item.get("unit", unit_by_field.get(field_key))
        metadata = {
            "canonicalFieldNamespaceVersion": (
                CANONICAL_FIELD_NAMESPACE_VERSION
            ),
            **dict(raw_item.get("metadata") or {}),
        }
        if field_key.startswith(("revenue.", "cost.")) and "basis" not in metadata:
            metadata.update(_revenue_cost_basis())
        elif field_key.startswith("financing.") and "basis" not in metadata:
            metadata.update(_financing_basis())
        locator = raw_item.get("sourceLocator") or _excel_locator(f"B{index}")
        entity_id = raw_item.get("entityId")
        selections.append(
            {
                "selectionId": selection_id,
                "tenantId": "tenant-1",
                "projectId": "project-1",
                "fieldKey": field_key,
                "entityId": entity_id,
                "candidateId": candidate_id,
                "reviewDecisionId": review_decision_id,
                "approvalStatus": "approved",
                "conflictStatus": "none",
                "normalizedValue": raw_item.get("normalizedValue"),
                "unit": unit,
                "selectionVersion": 1,
                "selectedBy": "reviewer-1",
                "selectedAt": timestamp,
                "metadata": copy.deepcopy(metadata),
            }
        )
        approved_item = {
            "fieldKey": field_key,
            "canonicalField": field_key,
            "entityId": entity_id,
            "normalizedValue": raw_item.get("normalizedValue"),
            "unit": unit,
            "candidateId": candidate_id,
            "selectionId": selection_id,
            "reviewDecisionId": review_decision_id,
            "parsingRunId": "parsing-run-1",
            "documentVersionId": "document-version-1",
            "sourceHash": "a" * 64,
            "sha256": "a" * 64,
            "sourceLocator": locator,
            "originType": raw_item.get("originType") or "extracted",
            "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
            "versionContext": {
                "parserVersion": "parser-2.0",
                "ontologyVersion": ontology_version,
                "promptVersion": "prompt-2.0",
                "modelVersion": "model-2.0",
            },
            "metadata": copy.deepcopy(metadata),
        }
        for lineage_key in (
            "formulaId",
            "formulaVersion",
            "inputFactIds",
            "calculationRunId",
            "calculationEngineVersion",
            "calculationOutputPath",
            "calculationOutputHash",
            "inputSnapshotHash",
        ):
            if lineage_key in raw_item:
                approved_item[lineage_key] = copy.deepcopy(raw_item[lineage_key])
        approved_items.append(approved_item)
    return {
        "snapshotId": snapshot_id,
        "tenantId": "tenant-1",
        "projectId": "project-1",
        "approvalStatus": "approved",
        "validityStatus": "active",
        "snapshotVersion": 1,
        "purpose": "projection-test",
        "inputSchemaVersion": "realytics-parser-approved-inputs-v1",
        "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
        "canonicalFieldNamespaceVersion": (
            CANONICAL_FIELD_NAMESPACE_VERSION
        ),
        "selectionIds": [item["selectionId"] for item in selections],
        "selections": selections,
        "ontologyVersion": ontology_version,
        "documentVersionIds": ["document-version-1"],
        "sourceHashes": ["a" * 64],
        "sourceDocuments": [
            {
                "documentId": "document-1",
                "documentVersionId": "document-version-1",
                "archiveId": "archive-1",
                "objectVersion": "object-1",
                "sourceHash": "a" * 64,
                "sha256": "a" * 64,
            }
        ],
        "inputItems": approved_items,
        "inputs": {
            item["fieldKey"]: item["normalizedValue"]
            for item in selections
            if item.get("entityId") is None
        },
        "inputsByEntity": {
            entity_id: {
                item["fieldKey"]: item["normalizedValue"]
                for item in selections
                if item.get("entityId") == entity_id
            }
            for entity_id in {
                str(item["entityId"])
                for item in selections
                if item.get("entityId") is not None
            }
        },
        "versionContexts": [
            {
                "parserVersion": "parser-2.0",
                "ontologyVersion": ontology_version,
                "promptVersion": "prompt-2.0",
                "modelVersion": "model-2.0",
            }
        ],
        "sourceParsingRunIds": ["parsing-run-1"],
        "approvedBy": "snapshot-approver-1",
        "approvedAt": timestamp,
        "metadata": {},
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


def test_monetary_fact_basis_is_explicit_and_typed_for_revenue_cost_and_financing():
    assert validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cost.total_project_cost",
        normalized_value=80_000_000_000,
        unit="KRW",
        entity_id=None,
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(),
    ) == []
    assert validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="financing.pf_amount",
        normalized_value=50_000_000_000,
        unit="KRW",
        entity_id=None,
        evidence=[_excel_locator()],
        metadata=_financing_basis(),
    ) == []


def test_monetary_fact_basis_blocks_missing_inferred_mixed_and_unit_conflicts():
    missing = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cost.total_project_cost",
        normalized_value=80_000_000_000,
        unit="KRW",
        entity_id=None,
        evidence=[_excel_locator()],
    )
    inferred = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cost.total_project_cost",
        normalized_value=80_000_000_000,
        unit="KRW",
        entity_id=None,
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(vatTreatment="inferred"),
    )
    mixed = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cost.total_project_cost",
        normalized_value=80_000_000_000,
        unit="KRW",
        entity_id=None,
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(allocationBasis="mixed"),
    )
    financing_mismatch = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="financing.pf_amount",
        normalized_value=50_000_000_000,
        unit="USD",
        entity_id=None,
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(),
    )

    assert {item.code for item in missing} == {"FACT_BASIS_REQUIRED"}
    assert "FACT_BASIS_INVALID" in {item.code for item in inferred}
    assert "FACT_BASIS_ALLOCATION_METHOD_REQUIRED" in {
        item.code for item in mixed
    }
    assert {item.code for item in financing_mismatch}.issuperset(
        {
            "CANONICAL_UNIT_INVALID",
            "FACT_BASIS_INVALID",
            "FACT_BASIS_UNIT_CONFLICT",
        }
    )
    assert all(
        item.blocking
        for issues in (missing, inferred, mixed, financing_mismatch)
        for item in issues
    )


def test_projection_registry_matches_active_schema_contracts():
    assert validate_projection_registry() == []
    catalog = field_registry_catalog()
    assert catalog["status"] == "internal_controlled_baseline"
    assert catalog["normativeIndustryStandardCompliance"] is False


def test_feasibility_projection_registry_uses_runtime_pydantic_paths(monkeypatch):
    feasibility_projection = next(
        item
        for item in field_registry._PROJECTIONS
        if item.target_schema_id == "realytics-feasibility-study-v2"
        and item.field_key == "project.name"
    )
    invalid_projection = replace(
        feasibility_projection,
        target_path="project.name",
    )
    monkeypatch.setattr(
        field_registry,
        "_PROJECTIONS",
        tuple(
            invalid_projection if item is feasibility_projection else item
            for item in field_registry._PROJECTIONS
        ),
    )

    assert (
        "UNKNOWN_RUNTIME_MODEL_PATH:"
        "realytics-feasibility-study-v2:project.name"
    ) in validate_projection_registry()


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


def test_p1_domain_pack_registry_uses_entity_scoped_strict_contracts():
    catalog = field_registry_catalog()
    fields = {item["fieldKey"]: item for item in catalog["fields"]}
    p1_fields = {
        "program.product.asset_type",
        "program.product.name",
        "program.product.unit_count",
        "program.product.exclusive_area_sqm",
        "program.product.saleable_area_sqm",
        "program.product.contract_area_sqm",
        "revenue.product.average_sale_price_per_sqm",
        "revenue.product.average_sale_price_per_py",
        "revenue.product.total_sales_revenue",
        "cost.line_item.name",
        "cost.line_item.category",
        "cost.line_item.amount",
        "schedule.milestone.name",
        "schedule.milestone.type",
        "schedule.milestone.planned_date",
        "schedule.milestone.actual_date",
        "schedule.milestone.month_index",
        "sales_collection.step.stage",
        "sales_collection.step.sequence",
        "sales_collection.step.collection_ratio",
        "sales_collection.step.due_month_index",
        "sales_collection.step.due_date",
        "sales_collection.step.product_entity_id",
        "financing.tranche.name",
        "financing.tranche.interest_rate_type",
        "financing.tranche.base_rate_name",
        "financing.tranche.spread_rate",
        "financing.tranche.fee_rate",
        "financing.tranche.term_months",
        "financing.tranche.draw_priority",
        "financing.tranche.repayment_priority",
        "financing.tranche.repayment_method",
        "financing.tranche.repayment_source",
    }

    assert p1_fields <= set(fields)
    assert all(
        fields[field_key]["entityCardinality"] == "entity_required"
        for field_key in p1_fields
    )
    assert fields["program.product.asset_type"]["allowedValues"] == [
        "apartment",
        "officetel",
        "knowledge_industry_center",
        "mixed_use",
        "commercial",
        "logistics",
        "lodging",
        "residential_land",
        "other",
    ]
    assert fields["sales_collection.step.collection_ratio"][
        "allowedUnits"
    ] == ["ratio"]
    assert fields["financing.tranche.term_months"]["allowedUnits"] == [
        "month"
    ]
    namespace_prefixes = {
        item["prefix"] for item in canonical_namespace_catalog()["rules"]
    }
    assert {"schedule.", "sales_collection."} <= namespace_prefixes
    assert catalog["schemaVersion"] == FIELD_REGISTRY_VERSION


@pytest.mark.parametrize(
    ("field_key", "value", "unit", "entity_id", "metadata"),
    [
        ("program.product.asset_type", "apartment", None, "product-1", None),
        ("program.product.unit_count", 112, "count", "product-1", None),
        (
            "program.product.exclusive_area_sqm",
            84.92,
            "sqm",
            "product-1",
            None,
        ),
        (
            "revenue.product.total_sales_revenue",
            75_000_000_000,
            "krw",
            "product-1",
            _revenue_cost_basis(),
        ),
        (
            "revenue.product.average_sale_price_per_sqm",
            15_000_000,
            "krw_per_sqm",
            "product-1",
            _revenue_cost_basis(vatTreatment="excluded"),
        ),
        (
            "revenue.product.average_sale_price_per_py",
            49_586_776,
            "krw_per_py",
            "product-1",
            _revenue_cost_basis(vatTreatment="excluded"),
        ),
        (
            "cost.line_item.amount",
            20_000_000_000,
            "krw",
            "cost-line-1",
            _revenue_cost_basis(vatTreatment="excluded"),
        ),
        (
            "schedule.milestone.planned_date",
            "2027-03-31",
            None,
            "milestone-1",
            None,
        ),
        (
            "sales_collection.step.collection_ratio",
            0.6,
            "ratio",
            "collection-step-2",
            None,
        ),
        (
            "financing.tranche.loan_amount",
            50_000_000_000,
            "krw",
            "tranche-a",
            _financing_basis(),
        ),
        (
            "financing.tranche.fee_rate",
            0.01,
            "ratio",
            "tranche-a",
            None,
        ),
        (
            "financing.tranche.term_months",
            36,
            "month",
            "tranche-a",
            None,
        ),
        (
            "financing.tranche.repayment_method",
            "cash_sweep",
            None,
            "tranche-a",
            None,
        ),
    ],
)
def test_p1_domain_pack_fields_accept_only_normalized_values(
    field_key,
    value,
    unit,
    entity_id,
    metadata,
):
    assert validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key=field_key,
        normalized_value=value,
        unit=unit,
        entity_id=entity_id,
        evidence=[_excel_locator()],
        metadata=metadata,
    ) == []


def test_p1_domain_pack_fields_fail_closed_on_unit_enum_entity_and_basis():
    invalid_asset_type = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="program.product.asset_type",
        normalized_value="villa",
        unit=None,
        entity_id="product-1",
        evidence=[_excel_locator()],
    )
    invalid_ratio = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="sales_collection.step.collection_ratio",
        normalized_value=60,
        unit="percent",
        entity_id="collection-step-1",
        evidence=[_excel_locator()],
    )
    missing_entity = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="schedule.milestone.type",
        normalized_value="construction_start",
        unit=None,
        entity_id=None,
        evidence=[_excel_locator()],
    )
    missing_revenue_basis = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="revenue.product.total_sales_revenue",
        normalized_value=75_000_000_000,
        unit="krw",
        entity_id="product-1",
        evidence=[_excel_locator()],
    )
    wrong_cost_basis = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cost.line_item.amount",
        normalized_value=20_000_000_000,
        unit="krw",
        entity_id="cost-line-1",
        evidence=[_excel_locator()],
        metadata=_financing_basis(),
    )
    wrong_financing_basis = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="financing.tranche.loan_amount",
        normalized_value=50_000_000_000,
        unit="krw",
        entity_id="tranche-a",
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(),
    )
    wrong_amount_unit = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="cost.line_item.amount",
        normalized_value=20_000_000_000,
        unit="usd",
        entity_id="cost-line-1",
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(),
    )
    invalid_repayment = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key="financing.tranche.repayment_method",
        normalized_value="whenever_possible",
        unit=None,
        entity_id="tranche-a",
        evidence=[_excel_locator()],
    )

    assert {item.code for item in invalid_asset_type} == {
        "CANONICAL_VALUE_NOT_ALLOWED"
    }
    assert {item.code for item in invalid_ratio} == {
        "CANONICAL_UNIT_INVALID",
        "CANONICAL_VALUE_ABOVE_MAXIMUM",
    }
    assert {item.code for item in missing_entity} == {
        "CANONICAL_ENTITY_ID_REQUIRED"
    }
    assert {item.code for item in missing_revenue_basis} == {
        "FACT_BASIS_REQUIRED"
    }
    assert "FACT_BASIS_INVALID" in {item.code for item in wrong_cost_basis}
    assert "FACT_BASIS_INVALID" in {
        item.code for item in wrong_financing_basis
    }
    assert {item.code for item in wrong_amount_unit}.issuperset(
        {"CANONICAL_UNIT_INVALID", "FACT_BASIS_UNIT_CONFLICT"}
    )
    assert {item.code for item in invalid_repayment} == {
        "CANONICAL_VALUE_NOT_ALLOWED"
    }
    assert all(
        item.blocking
        for issues in (
            invalid_asset_type,
            invalid_ratio,
            missing_entity,
            missing_revenue_basis,
            wrong_cost_basis,
            wrong_financing_basis,
            wrong_amount_unit,
            invalid_repayment,
        )
        for item in issues
    )


@pytest.mark.parametrize(
    ("field_key", "unit", "other_krw_unit"),
    [
        (
            "revenue.product.average_sale_price_per_sqm",
            "krw_per_sqm",
            "krw_per_py",
        ),
        (
            "revenue.product.average_sale_price_per_py",
            "krw_per_py",
            "krw_per_sqm",
        ),
    ],
)
def test_product_unit_prices_require_basis_and_exact_krw_derived_unit(
    field_key,
    unit,
    other_krw_unit,
):
    valid = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key=field_key,
        normalized_value=15_000_000,
        unit=unit,
        entity_id="product-1",
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(vatTreatment="excluded"),
    )
    missing_basis = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key=field_key,
        normalized_value=15_000_000,
        unit=unit,
        entity_id="product-1",
        evidence=[_excel_locator()],
    )
    invalid_basis = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key=field_key,
        normalized_value=15_000_000,
        unit=unit,
        entity_id="product-1",
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(vatTreatment="inferred"),
    )
    mismatched_derived_unit = validate_candidate_against_registry(
        ontology_version="realytics-real-estate-domain-standard-v1",
        field_key=field_key,
        normalized_value=15_000_000,
        unit=other_krw_unit,
        entity_id="product-1",
        evidence=[_excel_locator()],
        metadata=_revenue_cost_basis(vatTreatment="excluded"),
    )

    assert valid == []
    assert {item.code for item in missing_basis} == {
        "FACT_BASIS_REQUIRED"
    }
    assert "FACT_BASIS_INVALID" in {
        item.code for item in invalid_basis
    }
    assert {item.code for item in mismatched_derived_unit} == {
        "CANONICAL_UNIT_INVALID",
        "FACT_BASIS_UNIT_CONFLICT",
    }
    assert all(
        item.blocking
        for issues in (
            missing_basis,
            invalid_basis,
            mismatched_derived_unit,
        )
        for item in issues
    )


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
    snapshot = _approved_snapshot(
        [
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
        ]
    )
    result = project_approved_inputs(
        target_schema_id="realytics-land-ledger-import-v1",
        target_schema_version="1.0",
        approved_snapshot=snapshot,
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
    assert len(result["sourceApprovedSnapshotHash"]) == 64
    assert all(
        item["sourceApprovedSnapshotHash"]
        == result["sourceApprovedSnapshotHash"]
        for item in result["lineage"]
    )


def test_projection_requires_strict_snapshot_instead_of_ad_hoc_items():
    with pytest.raises(ValueError, match="approved_snapshot is required"):
        project_approved_inputs(
            snapshot_id="snapshot-legacy",
            ontology_version="realytics-real-estate-domain-standard-v1",
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            input_items=[
                {
                    "fieldKey": "project.name",
                    "normalizedValue": "Untrusted ad-hoc value",
                    "selectionId": "selection-1",
                    "candidateId": "candidate-1",
                }
            ],
        )


def test_projection_rejects_invalidated_or_non_current_snapshot():
    invalidated = _approved_snapshot(
        [{"fieldKey": "project.name", "normalizedValue": "Old project"}]
    )
    invalidated["approvalStatus"] = "superseded"
    invalidated["validityStatus"] = "invalidated"
    with pytest.raises(ValueError, match="approvalStatus=approved"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=invalidated,
        )

    old_namespace = _approved_snapshot(
        [{"fieldKey": "project.name", "normalizedValue": "Old namespace"}]
    )
    old_namespace["canonicalFieldNamespaceVersion"] = (
        "realytics-canonical-fact-namespace-v99"
    )
    old_namespace["selections"][0]["metadata"][
        "canonicalFieldNamespaceVersion"
    ] = "realytics-canonical-fact-namespace-v99"
    old_namespace["inputItems"][0]["metadata"][
        "canonicalFieldNamespaceVersion"
    ] = "realytics-canonical-fact-namespace-v99"
    with pytest.raises(ValueError, match="current canonicalFieldNamespaceVersion"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=old_namespace,
        )


def test_projection_rejects_snapshot_with_missing_basis_or_imprecise_locator():
    missing_basis = _approved_snapshot(
        [
            {
                "fieldKey": "revenue.total_sales_revenue",
                "normalizedValue": 100_000_000,
            }
        ]
    )
    missing_basis["selections"][0]["metadata"].pop("basis")
    missing_basis["inputItems"][0]["metadata"].pop("basis")
    with pytest.raises(ValueError, match="FACT_BASIS_REQUIRED"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=missing_basis,
        )

    imprecise_locator = _approved_snapshot(
        [{"fieldKey": "project.type", "normalizedValue": "mixed_use"}]
    )
    imprecise_locator["inputItems"][0]["sourceLocator"] = {
        "documentVersionId": "document-version-1",
        "pageNumber": 1,
    }
    with pytest.raises(ValueError, match="EXACT_SOURCE_LOCATOR_REQUIRED"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=imprecise_locator,
        )

    mismatched_locator_hash = _approved_snapshot(
        [{"fieldKey": "project.type", "normalizedValue": "mixed_use"}]
    )
    mismatched_locator_hash["inputItems"][0]["sourceLocator"][
        "fileSha256"
    ] = "c" * 64
    with pytest.raises(ValueError, match="fileSha256.*sourceHash"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=mismatched_locator_hash,
        )


def test_projection_requires_complete_snapshot_source_lineage():
    snapshot = _approved_snapshot(
        [{"fieldKey": "project.name", "normalizedValue": "Missing manifest"}]
    )
    snapshot.pop("sourceDocuments")
    with pytest.raises(ValueError, match="sourceDocuments"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=snapshot,
        )


def test_projection_rejects_unprojected_requested_input_instead_of_skipping():
    snapshot = _approved_snapshot(
        [
            {
                "fieldKey": "project.name",
                "normalizedValue": "Scoped projection",
                "selectionId": "selection-project-name",
            },
            {
                "fieldKey": "land.pnu",
                "normalizedValue": "1168010100101230000",
                "entityId": "parcel-1",
                "selectionId": "selection-land-pnu",
            },
        ]
    )
    with pytest.raises(ValueError, match="land.pnu"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=snapshot,
        )

    scoped = project_approved_inputs(
        target_schema_id="realytics-feasibility-study-v2",
        target_schema_version="2.0",
        approved_snapshot=snapshot,
        requested_selection_ids=["selection-project-name"],
    )
    assert scoped["payload"]["project_name"] == "Scoped projection"
    assert [item["selectionId"] for item in scoped["lineage"]] == [
        "selection-project-name"
    ]


def test_projection_preserves_complete_derived_calculation_lineage():
    derived_locator = {
        "artifactKey": "calculation-run-1",
        "jsonPath": "/summary/maxMinus",
        "metadata": {
            "calculationRunId": "calculation-run-1",
            "outputHash": "c" * 64,
        },
    }
    snapshot = _approved_snapshot(
        [
            {
                "fieldKey": "project.name",
                "normalizedValue": "Calculation input",
                "candidateId": "candidate-input-1",
                "selectionId": "selection-input-1",
            },
            {
                "fieldKey": "cash_flow.max_minus_amount",
                "normalizedValue": -1_000_000,
                "candidateId": "candidate-derived-1",
                "selectionId": "selection-derived-1",
                "sourceLocator": derived_locator,
                "originType": "derived",
                "formulaId": "cash-flow.max-minus",
                "formulaVersion": "1.0",
                "inputFactIds": ["candidate-input-1"],
                "calculationRunId": "calculation-run-1",
                "calculationEngineVersion": "cash-flow-engine-1.0",
                "calculationOutputPath": "/summary/maxMinus",
                "calculationOutputHash": "c" * 64,
                "inputSnapshotHash": "d" * 64,
            },
        ]
    )
    result = project_approved_inputs(
        target_schema_id="realytics-cash-flow-run-request-v1",
        target_schema_version="1.0",
        approved_snapshot=snapshot,
        requested_selection_ids=["selection-derived-1"],
    )
    assert result["lineage"][0]["calculationLineage"] == {
        "formulaId": "cash-flow.max-minus",
        "formulaVersion": "1.0",
        "inputFactIds": ["candidate-input-1"],
        "calculationRunId": "calculation-run-1",
        "calculationEngineVersion": "cash-flow-engine-1.0",
        "calculationOutputPath": "/summary/maxMinus",
        "calculationOutputHash": "c" * 64,
        "inputSnapshotHash": "d" * 64,
    }


def test_projection_binds_deterministic_snapshot_hash_and_checks_expectation():
    snapshot = _approved_snapshot(
        [{"fieldKey": "project.name", "normalizedValue": "Hashed project"}]
    )
    first = project_approved_inputs(
        target_schema_id="realytics-feasibility-study-v2",
        target_schema_version="2.0",
        approved_snapshot=snapshot,
    )
    second = project_approved_inputs(
        target_schema_id="realytics-feasibility-study-v2",
        target_schema_version="2.0",
        approved_snapshot=copy.deepcopy(snapshot),
        expected_snapshot_hash=first["sourceApprovedSnapshotHash"],
    )
    assert second["sourceApprovedSnapshotHash"] == first[
        "sourceApprovedSnapshotHash"
    ]

    with pytest.raises(ValueError, match="content hash mismatch"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=snapshot,
            expected_snapshot_hash="f" * 64,
        )


def test_feasibility_projection_validates_and_round_trips_runtime_model():
    input_items = [
        {
            "fieldKey": field_key,
            "normalizedValue": value,
            "selectionId": f"selection-{index}",
            "candidateId": f"candidate-{index}",
            "sourceLocator": _excel_locator(f"B{index}"),
        }
        for index, (field_key, value) in enumerate(
            (
                ("project.name", "Runtime Projection Project"),
                ("project.type", "mixed_use"),
                ("feasibility.gross_floor_area_sqm", 12_000.0),
                ("feasibility.total_revenue", 100_000_000_000.0),
                ("cost.total_project_cost", 80_000_000_000.0),
                ("financing.pf_amount", 50_000_000_000.0),
            ),
            start=1,
        )
    ]

    result = project_approved_inputs(
        target_schema_id="realytics-feasibility-study-v2",
        target_schema_version="2.0",
        approved_snapshot=_approved_snapshot(
            input_items,
            snapshot_id="snapshot-runtime-projection",
        ),
    )

    payload = result["payload"]
    assert payload["project_name"] == "Runtime Projection Project"
    assert payload["asset_type"] == "mixed_use"
    assert payload["input_references"]["approved_input_snapshot_id"] == (
        "snapshot-runtime-projection"
    )
    assert payload["program"]["gross_floor_area_sqm"] == {
        "key": "program.gross_floor_area_sqm",
        "label": "Gross Floor Area Sqm",
        "value": 12_000.0,
        "unit": "SQM",
    }
    assert payload["metrics"] == {
        "total_revenue": 100_000_000_000.0,
        "total_cost": 80_000_000_000.0,
    }
    assert payload["financing"] == [
        {
            "tranche_id": "parser-v2-pf-aggregate",
            "name": "Approved aggregate PF amount",
            "priority": "other",
            "amount": 50_000_000_000.0,
        }
    ]
    revenue_lineage = next(
        item
        for item in result["lineage"]
        if item["fieldKey"] == "revenue.total_sales_revenue"
    )
    assert revenue_lineage["factBasis"] == _revenue_cost_basis()["basis"]
    assert revenue_lineage["sourceHash"] == "a" * 64
    assert revenue_lineage["reviewDecisionId"].startswith("review-")

    runtime_study = FeasibilityStudyV2.model_validate(payload)
    serialized = runtime_study.model_dump(mode="json")
    round_tripped = FeasibilityStudyV2.model_validate(serialized)
    assert round_tripped.model_dump(mode="json") == serialized


def test_feasibility_projection_rejects_value_outside_runtime_enum():
    with pytest.raises(ValueError, match="asset_type"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=_approved_snapshot(
                [
                    {
                        "fieldKey": "project.type",
                        "normalizedValue": "not-a-runtime-asset-type",
                        "selectionId": "selection-type",
                        "candidateId": "candidate-type",
                    }
                ],
                snapshot_id="snapshot-invalid-asset",
            ),
        )


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

    with pytest.raises(ValueError, match="same fieldKey|silently overwrite"):
        project_approved_inputs(
            target_schema_id="realytics-feasibility-study-v2",
            target_schema_version="2.0",
            approved_snapshot=_approved_snapshot(items),
        )


def test_max_minus_projects_only_to_the_registered_existing_schema_path():
    snapshot = _approved_snapshot(
        [
            {
                "fieldKey": "cash_flow.max_minus_amount",
                "normalizedValue": -1_000_000,
                "selectionId": "selection-max-minus",
                "candidateId": "candidate-max-minus",
                "sourceLocator": _calculation_locator(),
            }
        ]
    )
    result = project_approved_inputs(
        target_schema_id="realytics-cash-flow-run-request-v1",
        target_schema_version="1.0",
        approved_snapshot=snapshot,
    )

    assert result["payload"]["maxMinus"]["amount"] == -1_000_000
    assert result["lineage"][0]["targetPath"] == "maxMinus.amount"
    assert result["lineage"][0]["sourceLocator"]["artifactKey"] == (
        "cash-flow-run-result"
    )
    assert result["lineage"][0]["sourceLocator"]["metadata"][
        "outputHash"
    ] == "c" * 64
