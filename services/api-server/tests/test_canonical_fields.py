from __future__ import annotations

import pytest

from parsing_platform.canonical_fields import (
    CANONICAL_FIELD_NAMESPACE_VERSION,
    CANONICAL_NAMESPACE_RULES,
    LEGACY_CANONICAL_FIELD_ALIASES,
    canonical_field_key,
    canonical_namespace_catalog,
    resolve_canonical_field,
)


def test_namespace_contract_assigns_each_financial_fact_family_one_owner() -> None:
    assert set(CANONICAL_NAMESPACE_RULES) == {
        "program.",
        "revenue.",
        "cost.",
        "financing.",
        "schedule.",
        "sales_collection.",
        "cash_flow.",
        "feasibility.",
    }
    assert (
        canonical_namespace_catalog()["schemaVersion"]
        == CANONICAL_FIELD_NAMESPACE_VERSION
    )


@pytest.mark.parametrize(
    ("legacy", "canonical"),
    [
        (
            "feasibility.gross_floor_area_sqm",
            "program.gross_floor_area_sqm",
        ),
        (
            "feasibility.total_revenue",
            "revenue.total_sales_revenue",
        ),
        (
            "cash_flow.total_sales_revenue",
            "revenue.total_sales_revenue",
        ),
        ("cash_flow.land_cost", "cost.land_cost"),
    ],
)
def test_required_legacy_aliases_resolve_to_canonical_namespace(
    legacy: str,
    canonical: str,
) -> None:
    resolution = resolve_canonical_field(legacy)

    assert LEGACY_CANONICAL_FIELD_ALIASES[legacy] == canonical
    assert canonical_field_key(legacy) == canonical
    assert resolution.was_legacy_alias is True
    assert resolution.lineage_metadata() == {
        "namespaceVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
        "inputFieldKey": legacy,
        "canonicalFieldKey": canonical,
        "normalization": "legacy_alias",
    }


def test_namespace_rejects_noncanonical_key_shape() -> None:
    with pytest.raises(ValueError, match="lower snake_case"):
        canonical_field_key("cashFlow.maxMinus")
