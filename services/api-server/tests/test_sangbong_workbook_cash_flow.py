"""Synthetic source-sheet doubles exercise the source-backed CF import boundary.

No workbook is authored or modified. The actual source pair is additionally
checked locally against independent source net/cumulative rows.
"""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from agents.sangbong_workbook_cash_flow import map_sangbong_cash_flow
from schemas.feasibility_study import MonthlyCashFlowRow


class Sheet:
    title = "2. 캐쉬_분양률100%"
    max_column = 12
    max_row = 240

    def __init__(self, cells):
        self.cells = cells

    def iter_rows(self, *, min_row, max_row, min_col, max_col):
        for row in range(min_row, max_row + 1):
            yield tuple(SimpleNamespace(value=self.cells.get((row, col))) for col in range(min_col, max_col + 1))


def sheets(variant="sale"):
    shared = {(8, 10): datetime(2025, 1, 1), (8, 11): datetime(2025, 2, 1),
              (8, 12): datetime(2025, 3, 1), (7, 10): "기투입", (7, 11): 1, (7, 12): 2}
    if variant == "sale":
        shared.update({(92, 2): "■ 현금 흐름도 (단위:백만)", (115, 4): "투입자본금",
            (116, 4): "PF", (151, 4): "공사비 계", (123, 4): "공동주택",
            (150, 4): "공사비", (162, 4): "매입세액", (221, 4): "PF 상환",
            (115, 10): 10, (116, 10): 5, (150, 10): 9, (151, 11): 3,
            (123, 12): 7, (221, 12): 5, (162, 12): -0.0000015,
            (240, 9): 0, (239, 10): 15, (239, 11): -3, (239, 12): 2.0000015,
            (240, 10): 15, (240, 11): 12, (240, 12): 14.0000015,
            (137, 7): 22, (137, 10): 15, (137, 11): 0, (137, 12): 7,
            (217, 7): 2.9999985, (217, 10): 0, (217, 11): 3, (217, 12): -0.0000015})
    else:
        shared.update({(84, 2): "■ 현금 흐름도 (단위:백만)", (107, 4): "투입자본금",
            (108, 4): "기금대출", (142, 4): "공사비 계", (115, 4): "임대보증금",
            (116, 4): "보증금 증액", (117, 4): "임대료", (118, 4): "전환 분양 잔금",
            (141, 4): "공사비", (107, 10): 3, (108, 10): 5, (110, 10): 15,
            (112, 12): 30, (115, 10): 10, (116, 11): 2, (117, 11): 1,
            (118, 12): 20, (141, 10): 100, (142, 11): 6, (220, 12): 5,
            (221, 12): 15, (239, 9): 0, (238, 10): 33, (238, 11): -3,
            (238, 12): 30, (239, 10): 33, (239, 11): 30, (239, 12): 60,
            (128, 7): 86, (128, 10): 33, (128, 11): 3, (128, 12): 50,
            (216, 7): 6, (216, 10): 0, (216, 11): 6, (216, 12): 0})
    formula = deepcopy(shared)
    formula[(8, 11)] = "=+DATE(YEAR(J8),MONTH(J8)+1,DAY(J8))"
    formula[(8, 12)] = "=+DATE(YEAR(K8),MONTH(K8)+1,DAY(K8))"
    return Sheet(formula), Sheet(deepcopy(shared))


def test_sale_cash_flow_uses_paid_construction_and_preserves_exact_source_rounding():
    formula, values = sheets()
    original = deepcopy((formula.cells, values.cells))
    result = map_sangbong_cash_flow(formula, values, "sale", "synthetic-sale.xlsx")
    assert (formula.cells, values.cells) == original
    rows = result["monthly_cash_flow"]
    assert len(rows) == 3
    assert rows[0]["cash_out"] == {}  # earned construction is not a payment
    assert sum(rows[1]["cash_out"].values()) == 3_000_000
    assert sum(rows[2]["cash_in"].values()) == 7_000_002
    assert rows[2]["net_cash_flow"] == 2_000_002
    assert rows[2]["cumulative_cash_flow"] == 14_000_002
    review = result["cashFlowReview"]
    assert review["periodControlsMatched"] is True
    assert review["transactionMappingComplete"] is True
    assert review["calculationEvidence"]["recalculated"] is False
    assert review["sourceFundedBalanceIsMaxMinus"] is False
    source = review["sourceTransactions"]["L162"]
    assert source["rawValue"] == -0.0000015
    assert source["normalizedKind"] == "cash_in"
    assert source["normalizedKrw"] == 2
    assert source["roundingDeltaKrw"] == -0.5
    for row in rows:
        MonthlyCashFlowRow.model_validate(row)


def test_rental_deposits_are_financing_and_mortgage_phase_is_not_a_sales_receipt():
    formula, values = sheets("rental")
    result = map_sangbong_cash_flow(formula, values, "rental", "synthetic-rental.xlsx")
    rows = result["monthly_cash_flow"]
    assert sum(rows[0]["other_financing_inflow"].values()) == 10_000_000
    assert sum(rows[1]["other_financing_inflow"].values()) == 2_000_000
    assert rows[0]["cash_in"] == {}
    assert sum(rows[2]["cash_in"].values()) == 20_000_000
    assert sum(rows[2]["pf_draw"].values()) == 30_000_000
    assert sum(rows[2]["loan_repayment"].values()) == 20_000_000
    assert rows[0]["cash_out"] == {}  # earned, unpaid construction excluded
    assert sum(rows[1]["cash_out"].values()) == 6_000_000
    assert result["cashFlowReview"]["periodControlsMatched"] is True
    assert any("반환 의무" in text for text in result["cashFlowReview"]["warnings"])


def test_calendar_is_verified_from_the_date_chain_not_resetting_phase_counters():
    formula, values = sheets()
    result = map_sangbong_cash_flow(formula, values, "sale", "synthetic.xlsx")
    basis = result["cashFlowPeriodBasis"]
    assert basis["kind"] == "calendar_monthly"
    assert [row["label"] for row in basis["periods"]] == ["2025-01", "2025-02", "2025-03"]
    assert basis["periods"][0]["sourcePhaseMonth"] == "기투입"
    assert basis["periods"][1]["sourcePhaseMonth"] == 1
    assert [row["month_index"] for row in result["monthly_cash_flow"]] == [0, 1, 2]
    formula.cells[(8, 11)] = "=TODAY()"
    uncertain = map_sangbong_cash_flow(formula, values, "sale", "synthetic.xlsx")
    assert uncertain["cashFlowPeriodBasis"]["kind"] == "source_sequence"
    assert all(row["sourceMonthNumber"] is None for row in uncertain["cashFlowPeriodBasis"]["periods"])
    assert any(item["code"] == "cash_flow_calendar_header_unverified" for item in uncertain["cashFlowReview"]["issues"])


@pytest.mark.parametrize("cached", [None, "#REF!", "미정"])
def test_unavailable_or_invalid_amount_never_becomes_a_zero_period(cached):
    formula, values = sheets()
    formula.cells[(151, 11)] = "='1. 사업수지'!K61/1000"
    values.cells[(151, 11)] = cached
    result = map_sangbong_cash_flow(formula, values, "sale", "synthetic.xlsx")
    assert result["monthly_cash_flow"][1]["net_cash_flow"] is None
    assert result["monthly_cash_flow"][1]["cumulative_cash_flow"] is None
    assert result["monthly_cash_flow"][2]["cumulative_cash_flow"] is None
    assert result["cashFlowReview"]["transactionMappingComplete"] is False
    assert any(item["cell"] == "K151" for item in result["cashFlowReview"]["issues"] if "cell" in item)


def test_declared_source_total_difference_is_visible_without_changing_transactions():
    formula, values = sheets()
    values.cells[(217, 7)] += 0.7
    result = map_sangbong_cash_flow(formula, values, "sale", "synthetic.xlsx")
    control = next(item for item in result["cashFlowReview"]["sourceControls"] if item["key"] == "totalOperatingOutgoings")
    assert abs(control["differenceKrwBeforeRounding"] + 700_000) < 0.001
    assert result["cashFlowReview"]["periodControlsMatched"] is True
    assert result["cashFlowReview"]["sourceDeclaredTotalsMatched"] is False
    assert control["requiresReview"] is True
    assert sum(result["monthly_cash_flow"][1]["cash_out"].values()) == 3_000_000
    assert any("합계와 기간별" in item for item in result["cashFlowReview"]["warnings"])


def test_layout_or_units_must_match_before_source_values_are_scaled():
    formula, values = sheets()
    values.cells[(92, 2)] = "단위: 천원"
    result = map_sangbong_cash_flow(formula, values, "sale", "synthetic.xlsx")
    assert result["monthly_cash_flow"] == []
    assert result["cashFlowReview"]["mappingStatus"] == "unsupported"
    formula, values = sheets()
    result = map_sangbong_cash_flow(formula, values, "rental", "synthetic.xlsx")
    assert result["monthly_cash_flow"] == []
    assert any(item["code"] == "cash_flow_layout_mismatch" for item in result["cashFlowReview"]["issues"])


def test_excessive_source_columns_are_rejected_before_allocating_the_profile_grid():
    formula, values = sheets()
    formula.max_column = 16_384
    with pytest.raises(ValueError, match="supported profile range"):
        map_sangbong_cash_flow(formula, values, "sale", "synthetic.xlsx")
