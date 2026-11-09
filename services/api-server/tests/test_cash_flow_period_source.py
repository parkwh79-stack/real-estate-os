from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from agents.feasibility_workbook_importer import (
    _legacy_cash_flow_period_basis,
    import_feasibility_study_from_workbook,
)


class HeaderSheet:
    """Read-only source header double; no workbook is authored or saved."""

    def __init__(self, months=None, labels=None):
        self.months = [None] * 4 + list(range(1, 61)) if months is None else months
        self.labels = (["토지계약", "교평등", "사업승인", "분양"] +
                       [f"준공+{month - 42}" if 43 <= month <= 45 else month for month in range(1, 61)]) if labels is None else labels
        self.max_column = len(self.labels) + 5

    def iter_rows(self, min_row, max_row, min_col, max_col, values_only):
        assert values_only is True
        assert min_col == 6
        count = max_col - min_col + 1
        for row in range(min_row, max_row + 1):
            values = self.months if row == 2 else self.labels if row == 4 else []
            yield tuple((values + [None] * count)[:count])


def source_schedule():
    return [
        {"key": "construction_start", "label": "착공", "planned_date": "2020-05-01", "month_index": 1},
        {"key": "completion", "label": "준공", "planned_date": "2023-10-01", "month_index": 42},
    ]


def source_study():
    return {
        "project_name": "Source order and monthly clock",
        "schedule": source_schedule(),
        "monthly_cash_flow": [
            {"month_index": 0, "period": "토지계약", "cash_out": {"land_cost": 10.5},
             "source_locator": {"sourceDocument": "source.xlsx", "sheetName": "2.현금흐름", "cell": "F35:F119"}},
            {"month_index": 4, "period": "1", "cash_out": {"construction_cost": 20},
             "source_locator": {"sourceDocument": "source.xlsx", "sheetName": "2.현금흐름", "cell": "J35:J119"}},
        ],
        "raw_import": {"profile": "legacy-bonri-mixed-use-pf-feasibility", "sourceWorkbook": "source.xlsx"},
    }


def test_bonri_header_contract_separates_events_from_60_months_and_binds_schedule():
    schedule = source_schedule()
    original = deepcopy(schedule)
    basis = _legacy_cash_flow_period_basis(HeaderSheet(), "source.xlsx", schedule)
    assert basis["schemaVersion"] == "cash-flow-period-basis-v1"
    assert basis["kind"] == "source_sequence"
    assert len(basis["periods"]) == 64
    assert [row["sourcePeriodIndex"] for row in basis["periods"]] == list(range(64))
    assert [row["periodKind"] for row in basis["periods"][:4]] == ["event"] * 4
    assert [row["sourceMonthNumber"] for row in basis["periods"][:4]] == [None] * 4
    assert [row["sourceMonthNumber"] for row in basis["periods"][4:]] == list(range(1, 61))
    assert basis["periods"][4] == {
        "sourcePeriodIndex": 4, "label": "1", "periodKind": "monthly", "sourceMonthNumber": 1,
        "sourceLocator": {"sourceName": "source.xlsx", "sheet": "2.현금흐름", "cell": "J4", "monthCell": "J2"},
    }
    assert basis["periods"][46]["label"] == "준공+1"
    assert basis["periods"][46]["sourceMonthNumber"] == 43
    assert basis["scheduleBindings"] == {
        "construction_start": {"sourcePeriodIndex": 4, "sourceMonthNumber": 1},
        "completion": {"sourcePeriodIndex": 45, "sourceMonthNumber": 42},
    }
    assert "경과 기간은 미확인" in basis["warnings"][0]
    assert "plannedDate" not in repr(basis)
    assert schedule == original


@pytest.mark.parametrize("replacement", [None, "", 1, 3, 0, -1, True, 2.5, "월2"])
def test_invalid_missing_duplicate_or_discontinuous_month_header_is_not_bound(replacement):
    sheet = HeaderSheet()
    sheet.months[5] = replacement
    basis = _legacy_cash_flow_period_basis(sheet, "source.xlsx", source_schedule())
    assert basis["periods"][4]["periodKind"] == "unknown"
    assert basis["periods"][5]["periodKind"] == "unknown"
    assert all(binding == {"sourcePeriodIndex": None, "sourceMonthNumber": None}
               for binding in basis["scheduleBindings"].values())
    assert any("월 헤더가 누락·중복·불연속" in warning for warning in basis["warnings"])
    if replacement in (1, 3) and not isinstance(replacement, bool):
        assert basis["periods"][5]["sourceMonthNumber"] == replacement


def test_missing_row2_is_not_reconstructed_from_numeric_or_completion_display_labels():
    basis = _legacy_cash_flow_period_basis(HeaderSheet(months=[None] * 64), "source.xlsx", source_schedule())
    assert basis["periods"][4]["label"] == "1"
    assert basis["periods"][4]["sourceMonthNumber"] is None
    assert basis["periods"][46]["label"] == "준공+1"
    assert basis["periods"][46]["periodKind"] == "unknown"


def test_event_with_a_numeric_header_or_event_between_months_does_not_prove_month_order():
    numeric_event = HeaderSheet()
    numeric_event.months[0] = 99
    basis = _legacy_cash_flow_period_basis(numeric_event, "source.xlsx", source_schedule())
    assert basis["periods"][0]["periodKind"] == "unknown"
    assert basis["periods"][0]["sourceMonthNumber"] == 99
    between = HeaderSheet(months=[None, 1, None, 2], labels=["토지계약", "1", "분양", "2"])
    basis = _legacy_cash_flow_period_basis(between, "source.xlsx", source_schedule())
    assert all(row["periodKind"] == "unknown" for row in basis["periods"][1:])
    assert any("월별 기간 사이" in warning for warning in basis["warnings"])


def test_metadata_does_not_change_original_source_amounts_schedule_or_locator():
    study = source_study()
    study["raw_import"]["cashFlowPeriodBasis"] = _legacy_cash_flow_period_basis(HeaderSheet(), "source.xlsx", study["schedule"])
    original = deepcopy(study)
    request = build_cash_flow_run_request_from_feasibility_study(study)
    assert study == original
    assert request["periodBasis"] == study["raw_import"]["cashFlowPeriodBasis"]
    assert request["schedule"]["construction_start"]["monthIndex"] == 1
    assert request["schedule"]["completion"]["monthIndex"] == 42
    assert [(row["monthIndex"], row["landCost"], row["constructionCost"]) for row in request["costSchedule"]["rows"]] == [(0, 10.5, 0), (4, 0, 20)]
    assert request["costSchedule"]["rows"][0]["sourceLocator"] == study["monthly_cash_flow"][0]["source_locator"]
    request["periodBasis"]["periods"][0]["label"] = "changed display"
    assert study == original


def test_legacy_saved_capture_is_recovered_conservatively_without_guessing_headers():
    study = source_study()
    original = deepcopy(study)
    basis = build_cash_flow_run_request_from_feasibility_study(study)["periodBasis"]
    assert study == original
    assert basis["periods"][0]["periodKind"] == "event"
    assert basis["periods"][1]["label"] == "1"
    assert basis["periods"][1]["periodKind"] == "unknown"
    assert all(row["sourceMonthNumber"] is None for row in basis["periods"])
    assert basis["periods"][1]["sourceLocator"]["cell"] == "J35:J119"
    assert all("monthCell" not in row["sourceLocator"] for row in basis["periods"])
    assert basis["scheduleBindings"]["construction_start"]["sourcePeriodIndex"] is None
    assert any("다시 가져와" in warning for warning in basis["warnings"])


@pytest.mark.parametrize("change", ["ordinary_profile", "no_event", "no_rows", "working_budget_copy"])
def test_ordinary_or_independent_monthly_drafts_do_not_inherit_source_sequence(change):
    study = source_study()
    if change == "ordinary_profile":
        study["raw_import"]["profile"] = "realytics-formula-cash-flow-export"
    elif change == "no_event":
        study["monthly_cash_flow"][0]["period"] = "2020-05"
    else:
        if change == "working_budget_copy":
            study["raw_import"]["cashFlowPeriodBasis"] = _legacy_cash_flow_period_basis(HeaderSheet(), "source.xlsx", study["schedule"])
        study["monthly_cash_flow"] = []
    assert "periodBasis" not in build_cash_flow_run_request_from_feasibility_study(study)


SAMPLE_PATH = Path(__file__).resolve().parents[4] / "sample" / "19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx"


@pytest.mark.skipif(not SAMPLE_PATH.exists(), reason="original workspace workbook is unavailable in CI")
def test_original_sample_period_basis_preserves_all_64_source_rows_and_original_bytes():
    digest = hashlib.sha256(SAMPLE_PATH.read_bytes()).hexdigest()
    study = import_feasibility_study_from_workbook(SAMPLE_PATH, project_id="period-source-read-only")
    basis = study["raw_import"]["cashFlowPeriodBasis"]
    assert len(study["monthly_cash_flow"]) == len(basis["periods"]) == 64
    assert [row["month_index"] for row in study["monthly_cash_flow"]] == list(range(64))
    assert basis["periods"][45]["sourceLocator"]["monthCell"] == "AY2"
    assert basis["periods"][45]["sourceMonthNumber"] == 42
    assert basis["periods"][46]["label"] == "준공+1"
    assert basis["periods"][46]["sourceMonthNumber"] == 43
    assert basis["scheduleBindings"]["construction_start"]["sourcePeriodIndex"] == 4
    assert basis["scheduleBindings"]["completion"]["sourcePeriodIndex"] == 45
    assert study["schedule"][0]["month_index"] == 1
    assert study["schedule"][1]["month_index"] == 42
    request = build_cash_flow_run_request_from_feasibility_study(study)
    assert request["periodBasis"] == basis
    assert sum(sum(row["cash_out"].values()) for row in study["monthly_cash_flow"]) == 151_841_565_315
    assert hashlib.sha256(SAMPLE_PATH.read_bytes()).hexdigest() == digest
