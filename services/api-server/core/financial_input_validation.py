"""Small input guards shared by deterministic financial agents.

These guards reject non-finite supplied numbers; they do not fill missing values,
convert currency, or select financial policy thresholds.
"""
import math
from typing import Any


class FinancialInputError(ValueError):
    """A supported financial input contract was not satisfied (HTTP 422)."""

    def __init__(self, message: str, *, code: str = "invalid_financial_input") -> None:
        super().__init__(message)
        self.code = code


def require_finite_numbers(value: Any, path: str = "input") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise FinancialInputError(f"{path} must be finite; NaN and infinity cannot be calculated.")
    if isinstance(value, dict):
        for key, item in value.items():
            require_finite_numbers(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            require_finite_numbers(item, f"{path}[{index}]")
