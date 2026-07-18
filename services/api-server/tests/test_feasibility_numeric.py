from decimal import Decimal

import pytest

from domains.feasibility.numeric import (
    Area,
    MoneyKRW,
    excel_round,
    excel_round_down,
    excel_round_up,
    to_decimal,
)


def test_excel_round_matches_midpoint_away_from_zero():
    assert excel_round("2.5") == Decimal("3")
    assert excel_round("-2.5") == Decimal("-3")
    assert excel_round("1500", -3) == Decimal("2E+3")
    assert excel_round("1499", -3) == Decimal("1E+3")


def test_excel_round_down_and_up_follow_excel_sign_rules():
    assert excel_round_down("1.239", 2) == Decimal("1.23")
    assert excel_round_down("-1.239", 2) == Decimal("-1.23")
    assert excel_round_up("1.231", 2) == Decimal("1.24")
    assert excel_round_up("-1.231", 2) == Decimal("-1.24")


def test_area_conversion_uses_single_exact_constant():
    sqm = Area(Decimal("100"), "sqm")
    assert sqm.to("py").value == Decimal("30.2500")
    assert sqm.to("py").to("sqm").value == Decimal("100")


def test_money_is_integer_krw_and_external_float_is_decimalized_by_text():
    assert MoneyKRW.from_value("1000.5").amount == 1001
    assert to_decimal(0.1) == Decimal("0.1")
    with pytest.raises(TypeError):
        MoneyKRW(1000.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        to_decimal("NaN")
