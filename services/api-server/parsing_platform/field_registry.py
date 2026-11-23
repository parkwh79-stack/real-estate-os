from __future__ import annotations

"""Governed Parser V2 fields and projections into existing OS schemas.

This registry is intentionally explicit.  It is a controlled baseline,
not a claim that every lender, developer, appraisal, legal, or accounting field
has already been standardized.  Unknown fields remain valid *candidates* but are
blocking for canonical approval until a versioned field definition is added.
"""

import copy
import hashlib
import hmac
import json
import re
import types
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence, Union, get_args, get_origin

from parsing_platform.canonical_fields import (
    CANONICAL_FIELD_NAMESPACE_VERSION,
    LEGACY_CANONICAL_FIELD_ALIASES,
    canonical_field_key,
    resolve_canonical_field,
)
from parsing_platform.fact_basis import validate_monetary_fact_basis


FIELD_REGISTRY_VERSION = "realytics-parser-field-registry-v2"
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
    adapter: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fieldKey": self.field_key,
            "targetSchemaId": self.target_schema_id,
            "targetSchemaVersion": self.target_schema_version,
            "targetPath": self.target_path,
            "valueType": self.value_type,
            "unit": self.unit,
            "adapter": self.adapter,
        }


@dataclass(frozen=True)
class FieldValidationIssue:
    code: str
    message: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}


_PRODUCT_ASSET_TYPES = (
    "apartment",
    "officetel",
    "knowledge_industry_center",
    "mixed_use",
    "commercial",
    "logistics",
    "lodging",
    "residential_land",
    "other",
)
_COST_LINE_ITEM_CATEGORIES = (
    "land",
    "demolition",
    "site_preparation",
    "construction",
    "design_engineering",
    "professional",
    "permit_fee",
    "tax",
    "marketing_sales",
    "financing",
    "contingency",
    "operating",
    "other",
)
_SCHEDULE_MILESTONE_TYPES = (
    "land_acquisition",
    "zoning_application",
    "zoning_approval",
    "building_permit_application",
    "building_permit_approval",
    "financing_close",
    "construction_start",
    "presale_start",
    "construction_completion",
    "occupancy_approval",
    "sales_completion",
    "other",
)
_SALES_COLLECTION_STAGES = (
    "reservation",
    "contract",
    "interim",
    "balance",
    "other",
)
_TRANCHE_REPAYMENT_METHODS = (
    "bullet",
    "amortizing",
    "balloon",
    "cash_sweep",
    "sculpted",
    "other",
)
_TRANCHE_REPAYMENT_SOURCES = (
    "sales_proceeds",
    "refinancing",
    "asset_sale",
    "operating_cash_flow",
    "sponsor_support",
    "mixed",
    "other",
)


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
        "program.gross_floor_area_sqm",
        "development_feasibility",
        "number",
        ("sqm",),
        minimum=0,
    ),
    FieldDefinition(
        "revenue.total_sales_revenue",
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
    # P1 feasibility domain pack.  Every field in a repeatable group requires
    # the same stable entityId so values from different product/line/schedule
    # rows cannot silently overwrite one another.
    FieldDefinition(
        "program.product.asset_type",
        "development_feasibility",
        "string",
        entity_cardinality="entity_required",
        allowed_values=_PRODUCT_ASSET_TYPES,
    ),
    FieldDefinition(
        "program.product.name",
        "development_feasibility",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "program.product.unit_count",
        "development_feasibility",
        "integer",
        ("count",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "program.product.exclusive_area_sqm",
        "development_feasibility",
        "number",
        ("sqm",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "program.product.saleable_area_sqm",
        "development_feasibility",
        "number",
        ("sqm",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "program.product.contract_area_sqm",
        "development_feasibility",
        "number",
        ("sqm",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "revenue.product.average_sale_price_per_sqm",
        "development_feasibility",
        "number",
        ("krw_per_sqm",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "revenue.product.average_sale_price_per_py",
        "development_feasibility",
        "number",
        ("krw_per_py",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "revenue.product.total_sales_revenue",
        "development_feasibility",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "cost.line_item.name",
        "cost_schedule",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "cost.line_item.category",
        "cost_schedule",
        "string",
        entity_cardinality="entity_required",
        allowed_values=_COST_LINE_ITEM_CATEGORIES,
    ),
    FieldDefinition(
        "cost.line_item.amount",
        "cost_schedule",
        "number",
        ("krw",),
        "entity_required",
        minimum=0,
    ),
    FieldDefinition(
        "schedule.milestone.name",
        "cost_schedule",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "schedule.milestone.type",
        "cost_schedule",
        "string",
        entity_cardinality="entity_required",
        allowed_values=_SCHEDULE_MILESTONE_TYPES,
    ),
    FieldDefinition(
        "schedule.milestone.planned_date",
        "cost_schedule",
        "date",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "schedule.milestone.actual_date",
        "cost_schedule",
        "date",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "schedule.milestone.month_index",
        "cost_schedule",
        "integer",
        ("month",),
        "entity_required",
        minimum=0,
        maximum=600,
    ),
    FieldDefinition(
        "sales_collection.step.stage",
        "development_feasibility",
        "string",
        entity_cardinality="entity_required",
        allowed_values=_SALES_COLLECTION_STAGES,
    ),
    FieldDefinition(
        "sales_collection.step.sequence",
        "development_feasibility",
        "integer",
        entity_cardinality="entity_required",
        minimum=1,
        maximum=100,
    ),
    FieldDefinition(
        "sales_collection.step.collection_ratio",
        "development_feasibility",
        "number",
        ("ratio",),
        "entity_required",
        minimum=0,
        maximum=1,
    ),
    FieldDefinition(
        "sales_collection.step.due_month_index",
        "development_feasibility",
        "integer",
        ("month",),
        "entity_required",
        minimum=0,
        maximum=600,
    ),
    FieldDefinition(
        "sales_collection.step.due_date",
        "development_feasibility",
        "date",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "sales_collection.step.product_entity_id",
        "development_feasibility",
        "string",
        entity_cardinality="entity_required",
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
    FieldDefinition(
        "financing.tranche.name",
        "pf_credit",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "financing.tranche.interest_rate_type",
        "pf_credit",
        "string",
        entity_cardinality="entity_required",
        allowed_values=("fixed", "floating", "zero_coupon", "other"),
    ),
    FieldDefinition(
        "financing.tranche.base_rate_name",
        "pf_credit",
        "string",
        entity_cardinality="entity_required",
    ),
    FieldDefinition(
        "financing.tranche.spread_rate",
        "pf_credit",
        "number",
        ("ratio",),
        "entity_required",
        minimum=0,
        maximum=1,
    ),
    FieldDefinition(
        "financing.tranche.fee_rate",
        "pf_credit",
        "number",
        ("ratio",),
        "entity_required",
        minimum=0,
        maximum=1,
        metadata={"rateEncoding": "decimal_fraction"},
    ),
    FieldDefinition(
        "financing.tranche.term_months",
        "pf_credit",
        "integer",
        ("month",),
        "entity_required",
        minimum=1,
        maximum=600,
    ),
    FieldDefinition(
        "financing.tranche.draw_priority",
        "pf_credit",
        "integer",
        entity_cardinality="entity_required",
        minimum=1,
        maximum=99,
    ),
    FieldDefinition(
        "financing.tranche.repayment_priority",
        "pf_credit",
        "integer",
        entity_cardinality="entity_required",
        minimum=1,
        maximum=99,
    ),
    FieldDefinition(
        "financing.tranche.repayment_method",
        "pf_credit",
        "string",
        entity_cardinality="entity_required",
        allowed_values=_TRANCHE_REPAYMENT_METHODS,
    ),
    FieldDefinition(
        "financing.tranche.repayment_source",
        "pf_credit",
        "string",
        entity_cardinality="entity_required",
        allowed_values=_TRANCHE_REPAYMENT_SOURCES,
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
        "project.name",
        "realytics-feasibility-study-v2",
        "2.0",
        "project_name",
        "string",
    ),
    FieldProjection(
        "project.type",
        "realytics-feasibility-study-v2",
        "2.0",
        "asset_type",
        "string",
    ),
    FieldProjection(
        "program.gross_floor_area_sqm",
        "realytics-feasibility-study-v2",
        "2.0",
        "program.gross_floor_area_sqm.value",
        "number",
        "sqm",
        "feasibility_value",
    ),
    FieldProjection(
        "revenue.total_sales_revenue",
        "realytics-feasibility-study-v2",
        "2.0",
        "metrics.total_revenue",
        "number",
        "krw",
    ),
    FieldProjection(
        "cost.total_project_cost",
        "realytics-feasibility-study-v2",
        "2.0",
        "metrics.total_cost",
        "number",
        "krw",
    ),
    FieldProjection(
        "financing.pf_amount",
        "realytics-feasibility-study-v2",
        "2.0",
        "financing[].amount",
        "number",
        "krw",
        "feasibility_pf_aggregate",
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
        "revenue.total_sales_revenue",
        "realytics-cash-flow-run-request-v1",
        "1.0",
        "sourcesAndUses.revenue.totalSalesRevenue",
        "number",
        "krw",
    ),
    FieldProjection(
        "cost.land_cost",
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
        "canonicalFieldNamespaceVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
        "legacyFieldAliases": dict(LEGACY_CANONICAL_FIELD_ALIASES),
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
    metadata: Mapping[str, Any] | None = None,
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
    field_key = canonical_field_key(field_key)
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
    if presence_status == "present":
        issues.extend(
            FieldValidationIssue(
                item.code,
                item.message,
                item.blocking,
            )
            for item in validate_monetary_fact_basis(
                field_key=field_key,
                allowed_units=definition.allowed_units,
                unit=unit,
                metadata=metadata,
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
    target_schema_id: str,
    target_schema_version: str,
    approved_snapshot: Any | None = None,
    requested_selection_ids: Sequence[str] | None = None,
    expected_snapshot_hash: str | None = None,
    # Retained as assertion-only compatibility parameters.  They no longer
    # establish an authoritative boundary without the strict snapshot itself.
    snapshot_id: str | None = None,
    ontology_version: str | None = None,
    input_items: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project a strict, active ``ApprovedInputSnapshot`` without data loss.

    ``snapshot_id``/``ontology_version``/``input_items`` used to let callers
    assert an approved boundary using ad-hoc mappings.  The parameters remain
    in the signature so callers receive an actionable migration error, but the
    snapshot model (or its exact wire payload) is now the sole authority.

    The output includes a content hash and lineage sidecar because target
    schemas predate Parser V2 and cannot themselves carry
    selection/candidate/evidence identities.
    """

    # Imported lazily because contracts imports this module to validate
    # ApprovedInputItem against the same registry.
    from parsing_platform.contracts import ApprovedInputSnapshot

    if approved_snapshot is None:
        raise ValueError(
            "approved_snapshot is required; ad-hoc input_items cannot establish "
            "an approved_snapshot_only projection boundary"
        )
    if input_items is not None:
        raise ValueError(
            "input_items is no longer accepted as projection authority; use "
            "approved_snapshot and optional requested_selection_ids"
        )
    if isinstance(approved_snapshot, ApprovedInputSnapshot):
        snapshot = approved_snapshot
    elif isinstance(approved_snapshot, Mapping):
        snapshot = ApprovedInputSnapshot.model_validate(dict(approved_snapshot))
    else:
        raise TypeError(
            "approved_snapshot must be an ApprovedInputSnapshot or its wire payload"
        )

    if snapshot.approval_status.value != "approved":
        raise ValueError("projection requires approvalStatus=approved")
    if snapshot.validity_status != "active":
        raise ValueError("projection requires validityStatus=active")
    if snapshot.field_registry_version != FIELD_REGISTRY_VERSION:
        raise ValueError(
            "projection requires the current fieldRegistryVersion="
            f"{FIELD_REGISTRY_VERSION}"
        )
    if (
        snapshot.canonical_field_namespace_version
        != CANONICAL_FIELD_NAMESPACE_VERSION
    ):
        raise ValueError(
            "projection requires the current canonicalFieldNamespaceVersion="
            f"{CANONICAL_FIELD_NAMESPACE_VERSION}"
        )
    if snapshot.ontology_version not in SUPPORTED_ONTOLOGY_VERSIONS:
        raise ValueError("ontology version is not registered for projection")
    required_snapshot_collections = {
        "selectionIds": snapshot.selection_ids,
        "documentVersionIds": snapshot.document_version_ids,
        "sourceHashes": snapshot.source_hashes,
        "sourceDocuments": snapshot.source_documents,
        "versionContexts": snapshot.version_contexts,
    }
    missing_snapshot_collections = [
        name
        for name, value in required_snapshot_collections.items()
        if not value
    ]
    if snapshot.input_schema_version is None or missing_snapshot_collections:
        details = ["inputSchemaVersion"] if snapshot.input_schema_version is None else []
        details.extend(missing_snapshot_collections)
        raise ValueError(
            "projection requires complete approved snapshot lineage: "
            + ", ".join(details)
        )
    if snapshot_id is not None and snapshot_id != snapshot.snapshot_id:
        raise ValueError("snapshot_id does not match approved_snapshot.snapshotId")
    if ontology_version is not None and ontology_version != snapshot.ontology_version:
        raise ValueError(
            "ontology_version does not match approved_snapshot.ontologyVersion"
        )

    snapshot_wire = snapshot.model_dump(
        by_alias=True,
        mode="json",
        exclude_none=True,
    )
    snapshot_hash = _approved_snapshot_content_hash(snapshot_wire)
    if expected_snapshot_hash is not None:
        normalized_expected_hash = str(expected_snapshot_hash).strip().casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_expected_hash):
            raise ValueError("expected_snapshot_hash must be a SHA-256 hex digest")
        if not hmac.compare_digest(normalized_expected_hash, snapshot_hash):
            raise ValueError("approved snapshot content hash mismatch")

    snapshot_items = list(snapshot.input_items or [])
    if not snapshot_items:
        raise ValueError(
            "approved_snapshot.inputItems is required for projection authority"
        )
    items_by_selection_id = {
        item.selection_id: item for item in snapshot_items
    }
    if len(items_by_selection_id) != len(snapshot_items):
        # The contract currently catches this through selection alignment, but
        # keep the projection boundary independently fail-closed.
        raise ValueError("approved_snapshot.inputItems repeats a selectionId")
    if requested_selection_ids is None:
        selected_items = snapshot_items
    else:
        normalized_selection_ids = [
            str(item).strip() for item in requested_selection_ids
        ]
        if not normalized_selection_ids or any(
            not item for item in normalized_selection_ids
        ):
            raise ValueError("requested_selection_ids must not be empty")
        if len(set(normalized_selection_ids)) != len(normalized_selection_ids):
            raise ValueError("requested_selection_ids must not contain duplicates")
        missing_selection_ids = [
            item
            for item in normalized_selection_ids
            if item not in items_by_selection_id
        ]
        if missing_selection_ids:
            raise ValueError(
                "requested selection is absent from approved_snapshot.inputItems: "
                + ", ".join(missing_selection_ids)
            )
        selected_items = [
            items_by_selection_id[item] for item in normalized_selection_ids
        ]

    projections = {
        item.field_key: item
        for item in _PROJECTIONS
        if item.target_schema_id == target_schema_id
        and item.target_schema_version == target_schema_version
    }
    if not projections:
        raise ValueError(
            "target schema/version has no registered field projections: "
            f"{target_schema_id}@{target_schema_version}"
        )
    missing_projection_fields: list[str] = []
    for item in selected_items:
        resolution = resolve_canonical_field(item.field_key)
        if (
            resolution.was_legacy_alias
            or resolution.canonical_field_key != item.field_key
        ):
            raise ValueError(
                "approved snapshot input must use the current canonical field key: "
                f"{item.field_key}"
            )
        if (
            item.metadata.get("canonicalFieldNamespaceVersion")
            != CANONICAL_FIELD_NAMESPACE_VERSION
        ):
            raise ValueError(
                "approved snapshot input does not preserve the current "
                f"canonicalFieldNamespaceVersion: {item.selection_id}"
            )
        if (
            item.source_locator.document_version_id is not None
            and item.source_locator.document_version_id
            != item.document_version_id
        ):
            raise ValueError(
                "approved input sourceLocator.documentVersionId does not match "
                f"its lineage: {item.selection_id}"
            )
        if (
            item.source_locator.file_sha256 is not None
            and item.source_locator.file_sha256 != item.source_hash
        ):
            raise ValueError(
                "approved input sourceLocator.fileSha256 does not match its "
                f"sourceHash: {item.selection_id}"
            )
        if item.field_key not in projections:
            missing_projection_fields.append(item.field_key)
    if missing_projection_fields:
        raise ValueError(
            "requested approved input has no registered projection for "
            f"{target_schema_id}@{target_schema_version}: "
            + ", ".join(sorted(set(missing_projection_fields)))
        )

    payload = _projection_seed(
        target_schema_id=target_schema_id,
        snapshot_id=snapshot.snapshot_id,
    )
    lineage: list[dict[str, Any]] = []
    for approved_item in selected_items:
        item = approved_item.model_dump(
            by_alias=True,
            mode="json",
            exclude_none=True,
        )
        field_resolution = resolve_canonical_field(approved_item.field_key)
        field_key = field_resolution.canonical_field_key
        projection = projections.get(field_key)
        value = copy.deepcopy(item.get("normalizedValue"))
        if not _matches_value_type(value, projection.value_type):
            raise ValueError(f"projection value type mismatch for {field_key}")
        _set_projection_value(
            payload,
            projection,
            value,
            entity_id=item.get("entityId"),
        )
        lineage_item = {
            "fieldKey": field_key,
            "inputFieldKey": field_resolution.input_field_key,
            "canonicalFieldNamespaceVersion": (
                CANONICAL_FIELD_NAMESPACE_VERSION
            ),
            "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
            "fieldKeyNormalization": "identity",
            "targetPath": projection.target_path,
            "selectionId": approved_item.selection_id,
            "candidateId": approved_item.candidate_id,
            "reviewDecisionId": approved_item.review_decision_id,
            "parsingRunId": approved_item.parsing_run_id,
            "documentVersionId": approved_item.document_version_id,
            "sourceHash": approved_item.source_hash,
            "sourceApprovedSnapshotHash": snapshot_hash,
            "entityId": approved_item.entity_id,
            "unit": approved_item.unit,
            "originType": approved_item.origin_type.value,
            "sourceLocator": copy.deepcopy(item["sourceLocator"]),
        }
        basis = approved_item.metadata.get("basis")
        if isinstance(basis, Mapping):
            lineage_item["factBasis"] = copy.deepcopy(dict(basis))
        if approved_item.origin_type.value == "derived":
            lineage_item["calculationLineage"] = {
                "formulaId": approved_item.formula_id,
                "formulaVersion": approved_item.formula_version,
                "inputFactIds": list(approved_item.input_fact_ids),
                "calculationRunId": approved_item.calculation_run_id,
                "calculationEngineVersion": (
                    approved_item.calculation_engine_version
                ),
                "calculationOutputPath": approved_item.calculation_output_path,
                "calculationOutputHash": approved_item.calculation_output_hash,
                "inputSnapshotHash": approved_item.input_snapshot_hash,
            }
        lineage.append(lineage_item)
    if not lineage:
        raise ValueError("projection requires at least one approved input")

    payload = _validate_and_canonicalize_runtime_projection(
        target_schema_id=target_schema_id,
        payload=payload,
    )
    return {
        "schemaVersion": target_schema_version,
        "schemaId": target_schema_id,
        "projectionRegistryVersion": FIELD_REGISTRY_VERSION,
        "canonicalFieldNamespaceVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
        "sourceApprovedSnapshotId": snapshot.snapshot_id,
        "sourceApprovedSnapshotHash": snapshot_hash,
        "payload": payload,
        "lineage": lineage,
        "authoritativeInputBoundary": "approved_snapshot_only",
    }


def _approved_snapshot_content_hash(snapshot_wire: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        snapshot_wire,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_projection_registry() -> list[str]:
    """Return drift codes against active registry and runtime Pydantic models."""

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
        runtime_model = _runtime_projection_model(projection.target_schema_id)
        if runtime_model is not None:
            if not _runtime_model_has_path(runtime_model, projection.target_path):
                errors.append(
                    "UNKNOWN_RUNTIME_MODEL_PATH:"
                    f"{projection.target_schema_id}:{projection.target_path}"
                )
            continue
        known_paths = {str(item.get("path")) for item in contract.get("fields") or []}
        if projection.target_path not in known_paths:
            errors.append(
                f"UNKNOWN_TARGET_PATH:{projection.target_schema_id}:{projection.target_path}"
            )
    return errors


def _projection_seed(
    *,
    target_schema_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    if target_schema_id != "realytics-feasibility-study-v2":
        return {}
    from schemas.feasibility_study import FEASIBILITY_STUDY_SCHEMA_VERSION

    return {
        "version": FEASIBILITY_STUDY_SCHEMA_VERSION,
        "input_references": {
            "approved_input_snapshot_id": snapshot_id,
        },
    }


def _set_projection_value(
    payload: dict[str, Any],
    projection: FieldProjection,
    value: Any,
    *,
    entity_id: Any,
) -> None:
    if projection.adapter == "feasibility_value":
        value_path, separator, leaf = projection.target_path.rpartition(".")
        if not separator or leaf != "value":
            raise ValueError(
                "feasibility_value projection must target a FeasibilityValue.value path"
            )
        value_container = _ensure_object_path(payload, value_path)
        value_container.setdefault("key", projection.field_key)
        value_container.setdefault(
            "label",
            projection.field_key.rsplit(".", 1)[-1].replace("_", " ").title(),
        )
        amount_unit = {
            "krw": "KRW",
            "sqm": "SQM",
            "py": "PY",
            "percent": "PERCENT",
            "count": "COUNT",
            "month": "MONTH",
        }.get(str(projection.unit or "").casefold())
        if amount_unit is not None:
            value_container.setdefault("unit", amount_unit)
        _set_projected_path(
            payload,
            projection.target_path,
            value,
            entity_id=entity_id,
        )
        return

    if projection.adapter == "feasibility_pf_aggregate":
        if projection.target_path != "financing[].amount":
            raise ValueError(
                "feasibility_pf_aggregate projection must target financing[].amount"
            )
        rows = payload.setdefault("financing", [])
        if not isinstance(rows, list):
            raise ValueError("projection target collision at financing")
        aggregate = next(
            (
                row
                for row in rows
                if isinstance(row, dict)
                and row.get("tranche_id") == "parser-v2-pf-aggregate"
            ),
            None,
        )
        if aggregate is None:
            aggregate = {
                "tranche_id": "parser-v2-pf-aggregate",
                "name": "Approved aggregate PF amount",
                "priority": "other",
            }
            rows.append(aggregate)
        if "amount" in aggregate and aggregate["amount"] != value:
            raise ValueError(
                f"projection would silently overwrite {projection.target_path}"
            )
        aggregate["amount"] = value
        return

    if projection.adapter is not None:
        raise ValueError(f"unknown projection adapter: {projection.adapter}")
    _set_projected_path(
        payload,
        projection.target_path,
        value,
        entity_id=entity_id,
    )


def _ensure_object_path(root: dict[str, Any], path: str) -> dict[str, Any]:
    current = root
    for part in path.split("."):
        if part.endswith("[]"):
            raise ValueError(f"object projection seed cannot target an array: {path}")
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"projection target collision at {part}")
        current = child
    return current


def _runtime_projection_model(target_schema_id: str) -> type[Any] | None:
    if target_schema_id == "realytics-feasibility-study-v2":
        from schemas.feasibility_study import FeasibilityStudyV2

        return FeasibilityStudyV2
    return None


def _validate_and_canonicalize_runtime_projection(
    *,
    target_schema_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    runtime_model = _runtime_projection_model(target_schema_id)
    if runtime_model is None:
        return payload
    validated = runtime_model.model_validate(payload)
    canonical_payload = validated.model_dump(mode="json", exclude_unset=True)
    # Exercise the serialized boundary as well: Python-mode validation alone
    # can conceal enum/date serialization drift seen by API and worker callers.
    runtime_model.model_validate(canonical_payload)
    return canonical_payload


def _runtime_model_has_path(model_type: type[Any], path: str) -> bool:
    current_type: Any = model_type
    for raw_part in path.split("."):
        is_array = raw_part.endswith("[]")
        part = raw_part[:-2] if is_array else raw_part
        current_type = _unwrap_optional_type(current_type)
        model_fields = getattr(current_type, "model_fields", None)
        if not isinstance(model_fields, Mapping) or part not in model_fields:
            return False
        current_type = _unwrap_optional_type(model_fields[part].annotation)
        if is_array:
            if get_origin(current_type) is not list:
                return False
            arguments = get_args(current_type)
            if len(arguments) != 1:
                return False
            current_type = arguments[0]
    return True


def _unwrap_optional_type(annotation: Any) -> Any:
    current = annotation
    while get_origin(current) in (Union, types.UnionType):
        arguments = tuple(item for item in get_args(current) if item is not type(None))
        if len(arguments) != 1:
            return current
        current = arguments[0]
    return current


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
