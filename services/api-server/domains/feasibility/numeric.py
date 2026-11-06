"""Exact numeric and unit primitives for formula-auditable feasibility runs.

Python's built-in ``round`` uses bankers' rounding. Excel ``ROUND`` instead
rounds midpoint values away from zero, so workbook reconciliation must use an
explicit Decimal policy. Legacy V2 adapters may still expose JSON numbers, but
all new domain calculations can share these canonical primitives.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Any, Literal


PY_PER_SQM = Decimal("0.3025")


def to_decimal(value: Any) -> Decimal:
    """Convert an external numeric value without inheriting binary-float noise."""
    if isinstance(value, bool) or value is None:
        raise TypeError("a finite numeric value is required")
    if isinstance(value, Decimal):
        result = value
    else:
        text = str(value).strip().replace(",", "")
        if not text:
            raise ValueError("a finite numeric value is required")
        try:
            result = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("a finite numeric value is required")
    return result


def _quantum(digits: int) -> Decimal:
    if isinstance(digits, bool) or not isinstance(digits, int):
        raise TypeError("digits must be an integer")
    return Decimal(1).scaleb(-digits)


def excel_round(value: Any, digits: int = 0) -> Decimal:
    """Excel ROUND: midpoint values round away from zero."""
    return to_decimal(value).quantize(_quantum(digits), rounding=ROUND_HALF_UP)


def excel_round_down(value: Any, digits: int = 0) -> Decimal:
    """Excel ROUNDDOWN: truncate toward zero."""
    return to_decimal(value).quantize(_quantum(digits), rounding=ROUND_DOWN)


def excel_round_up(value: Any, digits: int = 0) -> Decimal:
    """Excel ROUNDUP: round every non-zero discarded digit away from zero."""
    return to_decimal(value).quantize(_quantum(digits), rounding=ROUND_UP)


def excel_round_int(value: Any) -> int:
    return int(excel_round(value, 0))


@dataclass(frozen=True, slots=True)
class MoneyKRW:
    amount: int

    def __post_init__(self) -> None:
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise TypeError("MoneyKRW.amount must be an integer KRW value")

    @classmethod
    def from_value(cls, value: Any) -> "MoneyKRW":
        return cls(excel_round_int(value))


@dataclass(frozen=True, slots=True)
class Area:
    value: Decimal
    unit: Literal["sqm", "py"]

    def __post_init__(self) -> None:
        normalized = to_decimal(self.value)
        if normalized < 0:
            raise ValueError("area cannot be negative")
        if self.unit not in {"sqm", "py"}:
            raise ValueError("area unit must be sqm or py")
        object.__setattr__(self, "value", normalized)

    def to(self, unit: Literal["sqm", "py"]) -> "Area":
        if unit == self.unit:
            return self
        if unit == "py" and self.unit == "sqm":
            return Area(self.value * PY_PER_SQM, "py")
        if unit == "sqm" and self.unit == "py":
            return Area(self.value / PY_PER_SQM, "sqm")
        raise ValueError("area unit must be sqm or py")


@dataclass(frozen=True, slots=True)
class Rate:
    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", to_decimal(self.value))


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        normalized = to_decimal(self.value)
        if not str(self.unit).strip():
            raise ValueError("quantity unit is required")
        object.__setattr__(self, "value", normalized)
