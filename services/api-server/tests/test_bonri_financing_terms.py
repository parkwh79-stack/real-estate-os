import json

import pytest
from openpyxl import Workbook, load_workbook

from agents.feasibility_workbook_importer import _legacy_financing
from schemas.feasibility_study import FinancingTranche
from use_cases.feasibility_workbook_llm import feasibility_review_state


def workbook_terms():
    book = Workbook()
    inputs = book.active
    inputs.title = "입력"
    cash = book.create_sheet("2.현금흐름")
    inputs["C130"] = "금액(백만원)"
    for col, name, amount, rate, fee, target in (
        ("D", "Tr.A", 50000, .055, .02, "E48"),
        ("E", "Tr.B", 0, .065, .05, "E49"),
    ):
        inputs[f"{col}129"] = name
        inputs[f"{col}130"] = amount
        inputs[f"{col}131"] = rate
        inputs[f"{col}132"] = fee
        inputs[f"{col}131"].number_format = "0.0%"
        inputs[f"{col}132"].number_format = "0.00%"
        cash[target] = amount
    cash["E55"] = 5000
    return inputs, cash


def test_source_rates_zero_commitment_and_unknown_contract_terms_stay_distinct():
    inputs, cash = workbook_terms()
    rows, review = _legacy_financing(cash, inputs, "source.xlsx")
    rows = {row["tranche_id"]: FinancingTranche.model_validate(row).model_dump() for row in rows}
    assert rows["tr_a"]["amount"] == 50_000_000_000
    assert rows["tr_a"]["interest_rate"] == .055
    assert rows["tr_a"]["fee_rate"] == .02
    assert rows["tr_a"]["term_months"] is None
    assert rows["tr_b"]["amount"] == 0
    assert "tr_c" not in rows
    assert rows["equity"]["amount"] == 5_000_000_000
    source = review["terms"][0]
    assert source["fields"]["interest_rate"]["rawValue"] == .055
    assert source["fields"]["interest_rate"]["sourceCell"] == "입력!D131"
    assert source["priorityBasis"] == "legacy_profile_assumption"
    assert review["approvalStatus"] == "not_requested"


@pytest.mark.parametrize("changed_cell,value", [("D129", "Other loan"), ("C130", "금액(천원)"), ("D130", 40000)])
def test_unmatched_tranche_never_borrows_rates(changed_cell, value):
    inputs, cash = workbook_terms()
    inputs[changed_cell] = value
    rows, review = _legacy_financing(cash, inputs, "source.xlsx")
    assert rows[0]["amount"] == 50_000_000_000
    assert rows[0]["interest_rate"] is None
    assert rows[0]["fee_rate"] is None
    assert review["terms"][0]["principalReconciled"] is False


@pytest.mark.parametrize("raw,expected,status", [(0, 0, "read"), (None, None, "missing"),
    (5.5, None, "needs_review"), (True, None, "needs_review"), ("#REF!", None, "needs_review"),
    (float("nan"), None, "needs_review")])
def test_missing_invalid_and_zero_rates_are_not_interchangeable(raw, expected, status):
    inputs, cash = workbook_terms()
    inputs["D131"] = raw
    rows, review = _legacy_financing(cash, inputs, "source.xlsx")
    assert rows[0]["interest_rate"] == expected
    assert review["terms"][0]["fields"]["interest_rate"]["status"] == status
    json.dumps(review, allow_nan=False)


def test_general_format_rate_requires_unit_review():
    inputs, cash = workbook_terms()
    inputs["D131"].number_format = "General"
    rows, review = _legacy_financing(cash, inputs, "source.xlsx")
    assert rows[0]["interest_rate"] is None
    assert review["terms"][0]["fields"]["interest_rate"]["rawValue"] == .055


def test_read_only_blank_rate_without_a_style_keeps_the_other_terms(tmp_path):
    inputs, _ = workbook_terms()
    del inputs["D131"]
    path = tmp_path / "missing-rate.xlsx"
    inputs.parent.save(path)
    with_book = load_workbook(path, read_only=True, data_only=True)
    try:
        rows, review = _legacy_financing(with_book["2.현금흐름"], with_book["입력"], "missing-rate.xlsx")
        assert rows[0]["amount"] == 50_000_000_000
        assert rows[0]["interest_rate"] is None
        assert rows[0]["fee_rate"] == .02
        assert review["terms"][0]["fields"]["interest_rate"]["status"] == "missing"
    finally:
        with_book.close()


def test_source_candidates_are_visible_without_changing_working_terms():
    inputs, cash = workbook_terms()
    _, source = _legacy_financing(cash, inputs, "source.xlsx")
    project = {"feasibilityStudyV2Draft": {"raw_import": {"financingTermReview": source,
        "sourceDocument": {"archiveId": "source-archive", "originalFileName": "source.xlsx"}}},
        "pf_structure": [{"tranche_id": "saved", "amount": 1, "interest_rate": .1}]}
    state = feasibility_review_state(project)
    assert state["terms"]["financing"][0]["interest_rate"] == .1
    assert state["workbookFinancingReview"]["terms"][0]["fields"]["interest_rate"]["value"] == .055
    assert state["workbookFinancingReview"]["sourceDocument"]["archiveId"] == "source-archive"
    state["workbookFinancingReview"]["terms"].clear()
    assert len(source["terms"]) == 4
