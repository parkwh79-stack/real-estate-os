from __future__ import annotations

"""Versioned canonical Fact namespace and legacy-key normalization contract."""

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


CANONICAL_FIELD_NAMESPACE_VERSION = "realytics-canonical-fact-namespace-v1"
CANONICAL_FIELD_KEY_PATTERN = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
)


@dataclass(frozen=True)
class NamespaceRule:
    prefix: str
    semantic_owner: str
    allowed_content: str
    prohibited_content: str

    def to_dict(self) -> dict[str, str]:
        return {
            "prefix": self.prefix,
            "semanticOwner": self.semantic_owner,
            "allowedContent": self.allowed_content,
            "prohibitedContent": self.prohibited_content,
        }


@dataclass(frozen=True)
class CanonicalFieldResolution:
    input_field_key: str
    canonical_field_key: str
    namespace_version: str = CANONICAL_FIELD_NAMESPACE_VERSION

    @property
    def was_legacy_alias(self) -> bool:
        return self.input_field_key != self.canonical_field_key

    def lineage_metadata(self) -> dict[str, Any]:
        return {
            "namespaceVersion": self.namespace_version,
            "inputFieldKey": self.input_field_key,
            "canonicalFieldKey": self.canonical_field_key,
            "normalization": (
                "legacy_alias" if self.was_legacy_alias else "identity"
            ),
        }


_RULES = (
    NamespaceRule(
        "program.",
        "physical_program",
        "Physical development program, areas, quantities, and product composition.",
        "Revenue, cost, financing, liquidity, or calculated feasibility verdicts.",
    ),
    NamespaceRule(
        "revenue.",
        "revenue",
        "Sales, lease, operating, and other project revenue facts.",
        "Physical program, cost, financing, liquidity, or calculated verdicts.",
    ),
    NamespaceRule(
        "cost.",
        "cost",
        "Land, construction, professional, tax, financing, and other cost facts.",
        "Revenue, funding sources, cash timing, or calculated feasibility verdicts.",
    ),
    NamespaceRule(
        "financing.",
        "financing",
        "Funding sources, tranche terms, fees, covenants, and repayment terms.",
        "Project revenue, physical program, or monthly cash movement.",
    ),
    NamespaceRule(
        "schedule.",
        "project_schedule",
        "Dated development milestones and their planned or actual timing.",
        "Sales collection terms, funding terms, or monthly cash movement.",
    ),
    NamespaceRule(
        "sales_collection.",
        "sales_collection",
        "Contract, interim, and balance collection timing and ratios.",
        "Revenue recognition, physical product facts, or funding terms.",
    ),
    NamespaceRule(
        "cash_flow.",
        "cash_flow",
        "Monthly cash movement, balances, liquidity, and max-minus timing facts.",
        "Static revenue totals, static cost totals, or final feasibility verdicts.",
    ),
    NamespaceRule(
        "feasibility.",
        "feasibility",
        "Calculated KPIs, ratios, sensitivities, and feasibility decisions.",
        "Physical program, raw revenue/cost facts, funding terms, or cash movements.",
    ),
)


CANONICAL_NAMESPACE_RULES = MappingProxyType(
    {item.prefix: item for item in _RULES}
)
LEGACY_CANONICAL_FIELD_ALIASES = MappingProxyType(
    {
        "feasibility.gross_floor_area_sqm": "program.gross_floor_area_sqm",
        "feasibility.product_mix": "program.product_mix",
        "feasibility.total_revenue": "revenue.total_sales_revenue",
        "revenue.product_mix": "program.product_mix",
        "cash_flow.total_sales_revenue": "revenue.total_sales_revenue",
        "cash_flow.land_cost": "cost.land_cost",
    }
)


def resolve_canonical_field(value: Any) -> CanonicalFieldResolution:
    field_key = str(value or "").strip()
    if CANONICAL_FIELD_KEY_PATTERN.fullmatch(field_key) is None:
        raise ValueError(
            "canonical field must be a lower snake_case dotted path"
        )
    return CanonicalFieldResolution(
        input_field_key=field_key,
        canonical_field_key=LEGACY_CANONICAL_FIELD_ALIASES.get(
            field_key,
            field_key,
        ),
    )


def canonical_field_key(value: Any) -> str:
    return resolve_canonical_field(value).canonical_field_key


def canonical_namespace_catalog() -> dict[str, Any]:
    return {
        "schemaVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
        "rules": [item.to_dict() for item in _RULES],
        "legacyAliases": dict(LEGACY_CANONICAL_FIELD_ALIASES),
    }


__all__ = [
    "CANONICAL_FIELD_KEY_PATTERN",
    "CANONICAL_FIELD_NAMESPACE_VERSION",
    "CANONICAL_NAMESPACE_RULES",
    "LEGACY_CANONICAL_FIELD_ALIASES",
    "CanonicalFieldResolution",
    "NamespaceRule",
    "canonical_field_key",
    "canonical_namespace_catalog",
    "resolve_canonical_field",
]
