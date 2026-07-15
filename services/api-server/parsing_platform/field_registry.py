from __future__ import annotations

"""Governed Parser V2 fields and projections into existing OS schemas.

This registry is intentionally explicit.  It is a controlled baseline,
not a claim that every lender, developer, appraisal, legal, or accounting field
has already been standardized.  Unknown fields remain valid *candidates* but are
blocking for canonical approval until a versioned field definition is added.
"""

import copy
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence


FIELD_REGISTRY_VERSION = "realytics-parser-field-registry-v1"
SUPPORTED_ONTOLOGY_VERSIONS = frozenset(
    {
        "realytics-real-estate-domain-standard-v1",
        # Existing Parser V2 record tests and controlled migration fixtures use
        # this version.  It is an explicit compatibility alias, not a wildcard.
        "ontology-2.0.0",
    }
)


@dataclass(frozen=True)
class FieldDefinition:
    field_key: str
    domain: str
    value_type: str
    allowed_units: tuple[str, ...] = ()
    entity_cardinality: str = "project"
    critical: bool = True
    required_locator_precision: str = "exact"
    minimum: float | None = None
    maximum: float | None = None
    string_pattern: str | None = None
    allowed_values: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fieldKey": self.field_key,
            "domain": self.domain,
            "valueType": self.value_type,
            "allowedUnits": list(self.allowed_units),
            "entityCardinality": self.entity_cardinality,
            "critical": self.critical,
            "requiredLocatorPrecision": self.required_locator_precision,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "stringPattern": self.string_pattern,
            "allowedValues": list(self.allowed_values),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class FieldProjection:
    field_key: str
    target_schema_id: str
    target_schema_version: str
    target_path: str
    value_type: str
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fieldKey": self.field_key,
            "targetSchemaId": self.target_schema_id,
            "targetSchemaVersion": self.target_schema_version,
            "targetPath": self.target_path,
            "valueType": self.value_type,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class FieldValidationIssue:
    code: str
    message: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}


_FIELDS: tuple[FieldDefinition, ...] = (
    FieldDefinition("project.name", "project_identity", "string", critical=False),
    FieldDefinition("project.type", "project_identity", "string"),
    FieldDefinition("land.address", "land_legal", "string", entity_cardinality="entity_optional"),
    FieldDefinition(
        "land.pnu",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
        string_pattern=r"^[0-9]{19}$",
    ),
    FieldDefinition(
        "land.area_sqm",
        "land_legal",
        "number",
        ("sqm",),
        "entity_optional",
        minimum=0,
    ),
    FieldDefinition(
        "land.registered_area_sqm",
        "land_legal",
        "number",
        ("sqm",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "land.official_land_price_per_sqm",
        "land_legal",
        "number",
        ("krw_per_sqm",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "feasibility.gross_floor_area_sqm",
        "development_feasibility",
        "number",
        ("sqm",),
        minimum=0,
    ),
    FieldDefinition(
        "feasibility.total_revenue",
        "development_feasibility",
        "number",
        ("krw",),
        minimum=0,
    ),
    FieldDefinition(
        "cost.total_project_cost", "cost_schedule", "number", ("krw",), minimum=0
    ),
    FieldDefinition("cost.land_cost", "cost_schedule", "number", ("krw",), minimum=0),
    FieldDefinition("financing.pf_amount", "pf_credit", "number", ("krw",), minimum=0),
    FieldDefinition(
        "financing.senior.loan_amount", "pf_credit", "number", ("krw",), minimum=0
    ),
    FieldDefinition(
        "market.proposed_price_per_py",
        "market_product",
        "number",
        ("krw_per_py",),
        minimum=0,
    ),
    FieldDefinition(
        "market.comparable_median_price_per_py",
        "market_product",
        "number",
        ("krw_per_py",),
        minimum=0,
    ),
    FieldDefinition(
        "market.completion_sales_rate",
        "market_product",
        "number",
        ("ratio",),
        minimum=0,
        maximum=1,
    ),
    FieldDefinition(
        "cash_flow.total_sales_revenue",
        "cashflow_waterfall",
        "number",
        ("krw",),
        minimum=0,
    ),
    FieldDefinition(
        "cash_flow.land_cost", "cashflow_waterfall", "number", ("krw",), minimum=0
    ),
    # Parcel-level enrichment.  Codes and display names are kept separately so
    # a source codebook can be changed without rewriting the source wording.
    FieldDefinition(
        "land.official_land_price_base_date",
        "land_legal",
        "date",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.base_date",
        "land_legal",
        "date",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.land_category_code",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.land_category_name",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.zoning.primary_use_district_code",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.zoning.primary_use_district_name",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.zoning.secondary_use_district_code",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.zoning.secondary_use_district_name",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.land_use_situation_code",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.land_use_situation_name",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.terrain_height_code",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.terrain_height_name",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.terrain_shape_code",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.terrain_shape_name",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.road_side_code",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "land.characteristics.road_side_name",
        "land_legal",
        "string",
        entity_cardinality="entity_required",
    ),
    # Transaction and presale comparables share one repeatable entity contract.
    FieldDefinition(
        "market.comparable.type",
        "market_product",
        "string",
        entity_cardinality="entity_required",
        allowed_values=("transaction", "presale"),
    ),
    FieldDefinition(
        "market.comparable.project_name",
        "market_product",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "market.comparable.address",
        "market_product",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "market.comparable.base_date",
        "market_product",
        "date",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "market.comparable.price_krw",
        "market_product",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "market.comparable.price_per_py",
        "market_product",
        "number",
        ("krw_per_py",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "market.comparable.area_sqm",
        "market_product",
        "number",
        ("sqm",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "market.comparable.distance_km",
        "market_product",
        "number",
        ("km",),
        "entity_required",
        minimum=0,
    ),
    # A generic tranche entity replaces product-specific one-off field keys.
    FieldDefinition(
        "financing.tranche.type",
        "pf_credit",
        "string",
        entity_cardinality="entity_required",
        allowed_values=("senior", "mezzanine", "junior"),
    ),
    FieldDefinition(
        "financing.tranche.loan_amount",
        "pf_credit",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "financing.tranche.interest_rate",
        "pf_credit",
        "number",
        ("ratio",),
        "entity_required",
        minimum=0,
        maximum=1,
    ),
    FieldDefinition(
        "financing.tranche.maturity_date",
        "pf_credit",
        "date",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "financing.tranche.priority",
        "pf_credit",
        "integer",
        entity_cardinality="entity_required",
        minimum=1,
        maximum=3,
    ),
    # Monthly schedule values remain individual repeatable facts.  A stable
    # entityId binds all fields from the same month without embedding a table.
    FieldDefinition(
        "cash_flow.month_index",
        "cashflow_waterfall",
        "integer",
        entity_cardinality="entity_required",
        minimum=1,
    ),
    FieldDefinition(
        "cash_flow.period_start_date",
        "cashflow_waterfall",
        "date",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "cash_flow.monthly_inflow",
        "cashflow_waterfall",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "cash_flow.monthly_outflow",
        "cashflow_waterfall",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "cash_flow.monthly_net_cash_flow",
        "cashflow_waterfall",
        "number",
        ("krw",),
        "entity_required",
    ),
    FieldDefinition(
        "cash_flow.monthly_cumulative_cash",
        "cashflow_waterfall",
        "number",
        ("krw",),
        "entity_required",
    ),
    FieldDefinition(
        "cash_flow.max_minus_amount",
        "cashflow_waterfall",
        "number",
        ("krw",),
        maximum=0,
        metadata={"originPolicy": "deterministic_calculation"},
    ),
    FieldDefinition(
        "cash_flow.max_minus_month_index",
        "cashflow_waterfall",
        "integer",
        minimum=0,
        metadata={
            "originPolicy": "deterministic_calculation",
            "zeroMeans": "opening_balance",
        },
    ),
    FieldDefinition(
        "waterfall.tranche.payment_amount",
        "cashflow_waterfall",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
        metadata={"originPolicy": "deterministic_calculation"},
    ),
    FieldDefinition(
        "waterfall.tranche.shortfall_amount",
        "cashflow_waterfall",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
        metadata={"originPolicy": "deterministic_calculation"},
    ),
    FieldDefinition(
        "waterfall.tranche.recovery_rate",
        "cashflow_waterfall",
        "number",
        ("ratio",),
        "entity_required",
        minimum=0,
        maximum=1,
        metadata={"originPolicy": "deterministic_calculation"},
    ),
    # Unsold LTV and the required Exit sales rate may legitimately exceed 1.0;
    # that value is a risk result, not an invalid percentage encoding.
    FieldDefinition(
        "risk.unsold_ltv",
        "risk_approval",
        "number",
        ("ratio",),
        minimum=0,
        metadata={
            "originPolicy": "deterministic_calculation",
            "mayExceedOne": True,
        },
    ),
    FieldDefinition(
        "risk.unsold_ltv_cure.type",
        "risk_approval",
        "string",
        entity_cardinality="entity_required",
        allowed_values=(
            "pf_reduction",
            "equity_injection",
            "additional_collateral",
            "forward_sale",
            "bulk_sale",
            "refinancing",
            "other",
        ),
    ),
    FieldDefinition(
        "risk.unsold_ltv_cure.amount",
        "risk_approval",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "risk.unsold_ltv_cure.status",
        "risk_approval",
        "string",
        entity_cardinality="entity_required",
        allowed_values=(
            "proposed",
            "evidence_pending",
            "evidenced",
            "satisfied",
            "waived",
            "rejected",
        ),
    ),
    FieldDefinition(
        "exit.actual_sales_rate",
        "valuation_exit",
        "number",
        ("ratio",),
        minimum=0,
        maximum=1,
    ),
    FieldDefinition(
        "exit.required_sales_rate",
        "valuation_exit",
        "number",
        ("ratio",),
        minimum=0,
        metadata={
            "originPolicy": "deterministic_calculation",
            "mayExceedOne": True,
        },
    ),
    FieldDefinition(
        "approval_condition.text",
        "risk_approval",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "approval_condition.status",
        "risk_approval",
        "string",
        entity_cardinality="entity_required",
        allowed_values=(
            "pending",
            "satisfied",
            "waived",
            "rejected",
            "not_applicable",
        ),
    ),
    FieldDefinition(
        "approval_condition.owner",
        "risk_approval",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "approval_condition.due_date",
        "risk_approval",
        "date",
        entity_cardinality="entity_required",
    ),
)


_PROJECTIONS: tuple[FieldProjection, ...] = (
    FieldProjection(
        "project.name", "realytics-feasibility-study-v2", "2.0", "project.name", "string"
    ),
    FieldProjection(
        "project.type", "realytics-feasibility-study-v2", "2.0", "project.type", "string"
    ),
    FieldProjection(
        "feasibility.gross_floor_area_sqm",
        "realytics-feasibility-study-v2",
        "2.0",
        "program.grossFloorAreaSqm",
        "number",
        "sqm",
    ),
    FieldProjection(
        "feasibility.total_revenue",
        "realytics-feasibility-study-v2",
        "2.0",
        "revenue.totalSalesRevenue",
        "number",
        "krw",
    ),
    FieldProjection(
        "cost.total_project_cost",
        "realytics-feasibility-study-v2",
        "2.0",
        "cost.totalProjectCost",
        "number",
        "krw",
    ),
    FieldProjection(
        "financing.pf_amount",
        "realytics-feasibility-study-v2",
        "2.0",
        "financing.pfAmount",
        "number",
        "krw",
    ),
    FieldProjection(
        "land.pnu",
        "realytics-land-ledger-import-v1",
        "1.0",
        "parcels[].pnu",
        "string",
    ),
    FieldProjection(
        "land.registered_area_sqm",
        "realytics-land-ledger-import-v1",
        "1.0",
        "parcels[].registeredAreaSqm",
        "number",
        "sqm",
    ),
    FieldProjection(
        "land.official_land_price_per_sqm",
        "realytics-land-ledger-import-v1",
        "1.0",
        "parcels[].officialLandPrice.pricePerSqm",
        "number",
        "krw_per_sqm",
    ),
    FieldProjection(
        "market.proposed_price_per_py",
        "realytics-marketability-analysis-v2",
        "2.0",
        "pricing.proposedPricePerPy",
        "number",
        "krw_per_py",
    ),
    FieldProjection(
        "market.comparable_median_price_per_py",
        "realytics-marketability-analysis-v2",
        "2.0",
        "pricing.comparableMedianPricePerPy",
        "number",
        "krw_per_py",
    ),
    FieldProjection(
        "market.completion_sales_rate",
        "realytics-marketability-analysis-v2",
        "2.0",
        "absorptionForecast.completion",
        "number",
    ),
    FieldProjection(
        "cash_flow.total_sales_revenue",
        "realytics-cash-flow-run-request-v1",
        "1.0",
        "sourcesAndUses.revenue.totalSalesRevenue",
        "number",
        "krw",
    ),
    FieldProjection(
        "cash_flow.land_cost",
        "realytics-cash-flow-run-request-v1",
        "1.0",
        "sourcesAndUses.cost.landCost",
        "number",
        "krw",
    ),
    FieldProjection(
        "cash_flow.max_minus_amount",
        "realytics-cash-flow-run-request-v1",
        "1.0",
        "maxMinus.amount",
        "number",
        "krw",
    ),
)


FIELD_DEFINITIONS = {item.field_key: item for item in _FIELDS}


def field_registry_catalog() -> dict[str, Any]:
    return {
        "schemaVersion": FIELD_REGISTRY_VERSION,
        "status": "internal_controlled_baseline",
        "normativeIndustryStandardCompliance": False,
        "supportedOntologyVersions": sorted(SUPPORTED_ONTOLOGY_VERSIONS),
        "fields": [item.to_dict() for item in _FIELDS],
        "projections": [item.to_dict() for item in _PROJECTIONS],
    }


def validate_candidate_against_registry(
    *,
    ontology_version: str,
    field_key: str,
    normalized_value: Any,
    unit: str | None,
    entity_id: str | None,
    evidence: Sequence[Mapping[str, Any]],
    presence_status: str = "present",
    origin_type: str | None = None,
) -> list[FieldValidationIssue]:
    issues: list[FieldValidationIssue] = []
    if ontology_version not in SUPPORTED_ONTOLOGY_VERSIONS:
        issues.append(
            FieldValidationIssue(
                "ONTOLOGY_VERSION_UNREGISTERED",
                "The parsing run ontology version is not registered for approval.",
            )
        )
        return issues
    definition = FIELD_DEFINITIONS.get(field_key)
    if definition is None:
        issues.append(
            FieldValidationIssue(
                "CANONICAL_FIELD_UNREGISTERED",
                "The candidate field is not present in the governed field registry.",
            )
        )
        return issues
    value_type_matches = _matches_value_type(normalized_value, definition.value_type)
    if presence_status == "present" and not value_type_matches:
        issues.append(
            FieldValidationIssue(
                "CANONICAL_VALUE_TYPE_MISMATCH",
                f"The candidate value does not match type {definition.value_type}.",
            )
        )
    if (
        presence_status == "present"
        and value_type_matches
        and definition.allowed_values
        and normalized_value not in definition.allowed_values
    ):
        issues.append(
            FieldValidationIssue(
                "CANONICAL_VALUE_NOT_ALLOWED",
                "The candidate value is not in the governed canonical value set.",
            )
        )
    if (
        presence_status == "present"
        and definition.string_pattern
        and isinstance(normalized_value, str)
        and re.fullmatch(definition.string_pattern, normalized_value) is None
    ):
        issues.append(
            FieldValidationIssue(
                "CANONICAL_STRING_FORMAT_INVALID",
                "The candidate string does not match the governed field format.",
            )
        )
    if (
        presence_status == "present"
        and isinstance(normalized_value, (int, float, Decimal))
        and not isinstance(normalized_value, bool)
    ):
        numeric_value = float(normalized_value)
        if definition.minimum is not None and numeric_value < definition.minimum:
            issues.append(
                FieldValidationIssue(
                    "CANONICAL_VALUE_BELOW_MINIMUM",
                    "The candidate value is below the governed minimum.",
                )
            )
        if definition.maximum is not None and numeric_value > definition.maximum:
            issues.append(
                FieldValidationIssue(
                    "CANONICAL_VALUE_ABOVE_MAXIMUM",
                    "The candidate value is above the governed maximum.",
                )
            )
    if definition.allowed_units:
        normalized_unit = str(unit or "").strip().casefold()
        allowed = {item.casefold() for item in definition.allowed_units}
        if normalized_unit not in allowed:
            issues.append(
                FieldValidationIssue(
                    "CANONICAL_UNIT_INVALID",
                    "The candidate unit is absent or not allowed by the field definition.",
                )
            )
    if definition.entity_cardinality == "entity_required" and not str(entity_id or "").strip():
        issues.append(
            FieldValidationIssue(
                "CANONICAL_ENTITY_ID_REQUIRED",
                "This repeating field requires a stable entityId.",
            )
        )
    if definition.entity_cardinality == "project" and entity_id is not None:
        issues.append(
            FieldValidationIssue(
                "CANONICAL_ENTITY_ID_NOT_ALLOWED",
                "This project-level field must not carry an entityId.",
            )
        )
    if definition.critical and presence_status == "present":
        if not evidence or not all(_has_exact_locator(item) for item in evidence):
            issues.append(
                FieldValidationIssue(
                    "EXACT_SOURCE_LOCATOR_REQUIRED",
                    "A PF-critical field requires exact page/table, cell, API-row, "
                    "or deterministic calculation anchors.",
                )
            )
    if presence_status == "present" and any(
        _has_workbook_formula(item) for item in evidence
    ):
        normalized_origin_type = str(
            getattr(origin_type, "value", origin_type) or ""
        ).strip().casefold()
        has_derived_output_binding = any(
            _has_deterministic_calculation_locator(item) for item in evidence
        )
        if normalized_origin_type != "derived" or not has_derived_output_binding:
            issues.append(
                FieldValidationIssue(
                    "WORKBOOK_FORMULA_RECALCULATION_REQUIRED",
                    "A workbook cached formula value requires a derived candidate "
                    "bound to a deterministic calculation output.",
                )
            )
    return issues


def project_approved_inputs(
    *,
    snapshot_id: str,
    ontology_version: str,
    target_schema_id: str,
    target_schema_version: str,
    input_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project approved snapshot inputs without silent overwrite.

    The output includes a lineage sidecar because target schemas predate Parser
    V2 and cannot themselves carry selection/candidate/evidence identities.
    """

    if ontology_version not in SUPPORTED_ONTOLOGY_VERSIONS:
        raise ValueError("ontology version is not registered for projection")
    projections = {
        item.field_key: item
        for item in _PROJECTIONS
        if item.target_schema_id == target_schema_id
        and item.target_schema_version == target_schema_version
    }
    payload: dict[str, Any] = {}
    lineage: list[dict[str, Any]] = []
    for raw_item in input_items:
        item = dict(raw_item)
        field_key = str(item.get("fieldKey") or "")
        projection = projections.get(field_key)
        if projection is None:
            continue
        selection_id = str(item.get("selectionId") or "").strip()
        candidate_id = str(item.get("candidateId") or "").strip()
        if not selection_id or not candidate_id:
            raise ValueError("projection accepts approved selection items only")
        value = copy.deepcopy(item.get("normalizedValue"))
        if not _matches_value_type(value, projection.value_type):
            raise ValueError(f"projection value type mismatch for {field_key}")
        _set_projected_path(
            payload,
            projection.target_path,
            value,
            entity_id=item.get("entityId"),
        )
        lineage.append(
            {
                "fieldKey": field_key,
                "targetPath": projection.target_path,
                "selectionId": selection_id,
                "candidateId": candidate_id,
                "entityId": item.get("entityId"),
                "sourceLocator": copy.deepcopy(item.get("sourceLocator") or {}),
            }
        )
    return {
        "schemaVersion": target_schema_version,
        "schemaId": target_schema_id,
        "projectionRegistryVersion": FIELD_REGISTRY_VERSION,
        "sourceApprovedSnapshotId": snapshot_id,
        "payload": payload,
        "lineage": lineage,
        "authoritativeInputBoundary": "approved_snapshot_only",
    }


def validate_projection_registry() -> list[str]:
    """Return drift codes against the active OS schema registry."""

    from core.schema_registry import get_schema_contract

    errors: list[str] = []
    for projection in _PROJECTIONS:
        contract = get_schema_contract(projection.target_schema_id)
        if contract is None:
            errors.append(f"UNKNOWN_SCHEMA:{projection.target_schema_id}")
            continue
        if str(contract.get("version")) != projection.target_schema_version:
            errors.append(
                f"SCHEMA_VERSION_MISMATCH:{projection.target_schema_id}:{projection.field_key}"
            )
        known_paths = {str(item.get("path")) for item in contract.get("fields") or []}
        if projection.target_path not in known_paths:
            errors.append(
                f"UNKNOWN_TARGET_PATH:{projection.target_schema_id}:{projection.target_path}"
            )
    return errors


def _matches_value_type(value: Any, value_type: str) -> bool:
    if value is None:
        return False
    if value_type == "string":
        return isinstance(value, str) and bool(value.strip())
    if value_type == "number":
        return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "boolean":
        return isinstance(value, bool)
    if value_type == "date":
        if isinstance(value, date):
            return True
        if not isinstance(value, str):
            return False
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if value_type == "object":
        return isinstance(value, Mapping) and bool(value)
    if value_type == "array":
        return (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
            and bool(value)
        )
    return False


def _locator_value(locator: Mapping[str, Any], *keys: str) -> Any:
    metadata = locator.get("metadata")
    containers: tuple[Mapping[str, Any], ...] = (
        locator,
        metadata if isinstance(metadata, Mapping) else {},
    )
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value
    return None


def _has_workbook_formula(locator: Mapping[str, Any]) -> bool:
    return bool(
        _locator_value(
            locator,
            "workbookFormula",
            "workbook_formula",
            "workbookFormulaSha256",
            "workbook_formula_sha256",
        )
    )


def _has_deterministic_calculation_locator(locator: Mapping[str, Any]) -> bool:
    artifact_key = _locator_value(locator, "artifactKey", "artifact_key")
    json_path = _locator_value(locator, "jsonPath", "json_path")
    calculation_run_id = _locator_value(
        locator,
        "calculationRunId",
        "calculation_run_id",
    )
    output_hash = _locator_value(
        locator,
        "outputHash",
        "output_hash",
        "calculationOutputHash",
        "calculation_output_hash",
    )
    return bool(
        artifact_key
        and isinstance(json_path, str)
        and json_path.startswith("/")
        and calculation_run_id
        and isinstance(output_hash, str)
        and re.fullmatch(r"[0-9a-f]{64}", output_hash)
    )


def _has_exact_locator(locator: Mapping[str, Any]) -> bool:
    if _has_deterministic_calculation_locator(locator):
        return True
    if locator.get("sheetName") and (
        locator.get("cellReference") or locator.get("cellRange")
    ):
        return True
    if locator.get("apiEndpoint") and locator.get("apiRowId"):
        return True
    if (
        locator.get("documentVersionId")
        and isinstance(locator.get("jsonPath"), str)
        and str(locator.get("jsonPath")).startswith("/")
        and locator.get("rawTextSha256")
        and locator.get("parserMethod")
    ):
        return True
    if locator.get("pageNumber") is not None:
        if locator.get("bbox") and locator.get("coordinateSpace"):
            return True
        if locator.get("tableId") and locator.get("rowIndex") is not None:
            return True
        if locator.get("paragraphIndex") is not None and locator.get("rawTextSha256"):
            return True
    return False


def _set_projected_path(
    root: dict[str, Any],
    path: str,
    value: Any,
    *,
    entity_id: Any,
) -> None:
    parts = path.split(".")
    current: dict[str, Any] = root
    for index, raw_part in enumerate(parts):
        is_array = raw_part.endswith("[]")
        part = raw_part[:-2] if is_array else raw_part
        last = index == len(parts) - 1
        if is_array:
            stable_entity_id = str(entity_id or "").strip()
            if not stable_entity_id:
                raise ValueError(f"projection path {path} requires entityId")
            rows = current.setdefault(part, [])
            if not isinstance(rows, list):
                raise ValueError(f"projection target collision at {part}")
            row = next(
                (
                    item
                    for item in rows
                    if isinstance(item, dict) and item.get("_entityId") == stable_entity_id
                ),
                None,
            )
            if row is None:
                row = {"_entityId": stable_entity_id}
                rows.append(row)
            current = row
            continue
        if last:
            if part in current and current[part] != value:
                raise ValueError(f"projection would silently overwrite {path}")
            current[part] = value
            continue
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"projection target collision at {part}")
        current = child


__all__ = [
    "FIELD_DEFINITIONS",
    "FIELD_REGISTRY_VERSION",
    "FieldDefinition",
    "FieldProjection",
    "FieldValidationIssue",
    "field_registry_catalog",
    "project_approved_inputs",
    "validate_candidate_against_registry",
    "validate_projection_registry",
]
