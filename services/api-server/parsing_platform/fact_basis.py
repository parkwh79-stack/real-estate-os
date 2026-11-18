from __future__ import annotations

"""Typed approval basis for canonical monetary Parser V2 facts.

Amounts remain valid parsing *candidates* when this contract is absent or
invalid.  The registry converts the issues returned here into blocking
validation findings so no ambiguous amount can enter a canonical selection.
No value is inferred or defaulted by this module.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from parsing_platform.canonical_fields import canonical_field_key


class VatTreatment(str, Enum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    EXEMPT = "exempt"
    NOT_APPLICABLE = "not_applicable"


class AmountScope(str, Enum):
    GROSS = "gross"
    NET = "net"


class AllocationBasis(str, Enum):
    DIRECT = "direct"
    ALLOCATED = "allocated"
    MIXED = "mixed"
    NOT_APPLICABLE = "not_applicable"


class FinancingBalanceBasis(str, Enum):
    COMMITMENT = "commitment"
    DRAWN = "drawn"
    OUTSTANDING = "outstanding"


class _FactBasisContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class RevenueCostFactBasis(_FactBasisContract):
    vat_treatment: VatTreatment = Field(alias="vatTreatment")
    amount_scope: AmountScope = Field(alias="amountScope")
    allocation_basis: AllocationBasis = Field(alias="allocationBasis")
    currency: Literal["KRW"]
    allocation_method: str | None = Field(
        default=None,
        alias="allocationMethod",
        min_length=1,
        max_length=500,
    )

    @model_validator(mode="after")
    def require_explicit_mixed_allocation_method(
        self,
    ) -> "RevenueCostFactBasis":
        if (
            self.allocation_basis == AllocationBasis.MIXED
            and not _is_explicit(self.allocation_method)
        ):
            raise ValueError("mixed allocationBasis requires allocationMethod")
        if self.allocation_method is not None and not _is_explicit(
            self.allocation_method
        ):
            raise ValueError("allocationMethod cannot be an inferred placeholder")
        return self


class FinancingFactBasis(_FactBasisContract):
    vat_treatment: Literal["not_applicable"] = Field(alias="vatTreatment")
    amount_scope: Literal["not_applicable"] = Field(alias="amountScope")
    allocation_basis: Literal["not_applicable"] = Field(alias="allocationBasis")
    currency: Literal["KRW"]
    financing_balance_basis: FinancingBalanceBasis = Field(
        alias="financingBalanceBasis"
    )


class FactBasisKind(str, Enum):
    REVENUE_COST = "revenue_cost"
    FINANCING = "financing"


@dataclass(frozen=True)
class FactBasisValidationIssue:
    code: str
    message: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
        }


_FORBIDDEN_PLACEHOLDERS = frozenset(
    {
        "default",
        "inferred",
        "unknown",
        "unspecified",
    }
)


def monetary_fact_basis_kind(
    *,
    field_key: str,
    allowed_units: Sequence[str],
) -> FactBasisKind | None:
    """Return the governed basis kind after canonical legacy-key resolution."""

    if not any(_is_krw_unit(item) for item in allowed_units):
        return None
    try:
        canonical_key = canonical_field_key(field_key)
    except ValueError:
        canonical_key = str(field_key or "").strip()
    if canonical_key.startswith("financing."):
        return FactBasisKind.FINANCING
    if canonical_key.startswith(("revenue.", "cost.")):
        return FactBasisKind.REVENUE_COST
    return None


def validate_monetary_fact_basis(
    *,
    field_key: str,
    allowed_units: Sequence[str],
    unit: str | None,
    metadata: Mapping[str, Any] | None,
) -> list[FactBasisValidationIssue]:
    """Validate explicit monetary meaning without synthesizing any defaults."""

    basis_kind = monetary_fact_basis_kind(
        field_key=field_key,
        allowed_units=allowed_units,
    )
    if basis_kind is None:
        return []

    issues: list[FactBasisValidationIssue] = []
    raw_basis = (metadata or {}).get("basis")
    if raw_basis is None:
        issues.append(
            FactBasisValidationIssue(
                "FACT_BASIS_REQUIRED",
                "A governed monetary fact requires explicit metadata.basis.",
            )
        )
        return _with_unit_issue(
            issues,
            allowed_units=allowed_units,
            unit=unit,
            raw_basis=None,
        )
    if not isinstance(raw_basis, Mapping):
        issues.append(
            FactBasisValidationIssue(
                "FACT_BASIS_INVALID",
                "metadata.basis must be a typed object, not an inferred value.",
            )
        )
        return _with_unit_issue(
            issues,
            allowed_units=allowed_units,
            unit=unit,
            raw_basis=None,
        )

    basis = dict(raw_basis)
    placeholder_fields = sorted(
        str(key)
        for key, value in basis.items()
        if isinstance(value, str)
        and value.strip().casefold() in _FORBIDDEN_PLACEHOLDERS
    )
    if placeholder_fields:
        issues.append(
            FactBasisValidationIssue(
                "FACT_BASIS_INVALID",
                "Monetary basis values must be explicit; placeholder values are "
                "not approval eligible: "
                + ", ".join(placeholder_fields),
            )
        )

    if (
        basis_kind == FactBasisKind.REVENUE_COST
        and str(basis.get("allocationBasis") or "").strip().casefold() == "mixed"
        and not _is_explicit(basis.get("allocationMethod"))
    ):
        issues.append(
            FactBasisValidationIssue(
                "FACT_BASIS_ALLOCATION_METHOD_REQUIRED",
                "allocationBasis=mixed requires an explicit allocationMethod.",
            )
        )

    model_type: type[RevenueCostFactBasis] | type[FinancingFactBasis]
    model_type = (
        RevenueCostFactBasis
        if basis_kind == FactBasisKind.REVENUE_COST
        else FinancingFactBasis
    )
    try:
        model_type.model_validate(basis)
    except ValidationError as exc:
        invalid_fields = sorted(
            {
                str(item["loc"][0])
                for item in exc.errors(include_url=False, include_input=False)
                if item.get("loc")
            }
        )
        if not any(item.code == "FACT_BASIS_INVALID" for item in issues):
            issues.append(
                FactBasisValidationIssue(
                    "FACT_BASIS_INVALID",
                    "metadata.basis violates the governed "
                    f"{basis_kind.value} contract"
                    + (
                        ": " + ", ".join(invalid_fields)
                        if invalid_fields
                        else ""
                    )
                    + ".",
                )
            )

    return _with_unit_issue(
        issues,
        allowed_units=allowed_units,
        unit=unit,
        raw_basis=basis,
    )


def _with_unit_issue(
    issues: list[FactBasisValidationIssue],
    *,
    allowed_units: Sequence[str],
    unit: str | None,
    raw_basis: Mapping[str, Any] | None,
) -> list[FactBasisValidationIssue]:
    normalized_unit = str(unit or "").strip().casefold()
    normalized_allowed_units = {
        str(item).strip().casefold()
        for item in allowed_units
        if str(item).strip()
    }
    currency = str((raw_basis or {}).get("currency") or "").strip()
    if (
        normalized_unit not in normalized_allowed_units
        or not _is_krw_unit(normalized_unit)
        or (currency and currency != "KRW")
    ):
        issues.append(
            FactBasisValidationIssue(
                "FACT_BASIS_UNIT_CONFLICT",
                "A governed KRW monetary fact requires a registry-allowed KRW "
                "unit and basis.currency=KRW.",
            )
        )
    return issues


def _is_krw_unit(value: Any) -> bool:
    normalized = str(value or "").strip().casefold()
    return normalized == "krw" or normalized.startswith("krw_")


def _is_explicit(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return value.strip().casefold() not in _FORBIDDEN_PLACEHOLDERS


__all__ = [
    "AllocationBasis",
    "AmountScope",
    "FactBasisKind",
    "FactBasisValidationIssue",
    "FinancingBalanceBasis",
    "FinancingFactBasis",
    "RevenueCostFactBasis",
    "VatTreatment",
    "monetary_fact_basis_kind",
    "validate_monetary_fact_basis",
]
