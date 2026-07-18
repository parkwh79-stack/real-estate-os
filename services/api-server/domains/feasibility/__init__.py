"""Feasibility calculation primitives shared by workbook and API adapters."""

from domains.feasibility.numeric import (
    Area,
    MoneyKRW,
    Quantity,
    Rate,
    excel_round,
    excel_round_down,
    excel_round_int,
    excel_round_up,
    to_decimal,
)
from domains.feasibility.regulatory import (
    build_preliminary_buildable_envelope,
    evaluate_program_eligibility,
)

__all__ = [
    "Area",
    "MoneyKRW",
    "Quantity",
    "Rate",
    "excel_round",
    "excel_round_down",
    "excel_round_int",
    "excel_round_up",
    "to_decimal",
    "build_preliminary_buildable_envelope",
    "evaluate_program_eligibility",
]
