from copy import deepcopy

import pytest

from core.financial_input_validation import FinancialInputError
from core.schedule import schedule_event_map, schedule_month_indices
from agents.report_agent import ReportAgent


def test_schedule_event_map_preserves_legacy_object_values():
    assert schedule_event_map({
        "construction_start": "2020-05-01",
        "completion": "2023-10-01",
    }) == {
        "construction_start": "2020-05-01",
        "completion": "2023-10-01",
    }


def test_schedule_event_map_adapts_canonical_rows_without_losing_source_shape():
    rows = [
        {"key": "construction_start", "planned_date": "2020-05-01", "excel": {"cell": "E25"}},
        {"key": "completion", "plannedDate": "2023-10-01", "excel": {"cell": "E26"}},
    ]

    assert schedule_event_map(rows) == {
        "construction_start": "2020-05-01",
        "completion": "2023-10-01",
    }
    assert rows[0]["excel"]["cell"] == "E25"


def test_report_markdown_accepts_canonical_schedule_event_list():
    markdown = ReportAgent()._build_project_schedule({
        "schedule": [
            {"key": "construction_start", "planned_date": "2020-05-01"},
            {"key": "completion", "planned_date": "2023-10-01"},
        ],
    })

    assert "2020-05-01" in markdown
    assert "2023-10-01" in markdown


@pytest.mark.parametrize("start,end", [("M0", "M24"), (0, 24)])
def test_schedule_month_indices_supports_known_zero_and_legacy_months(start, end):
    assert schedule_month_indices({"construction_start": start, "completion": end}) == {
        "construction_start": 0, "completion": 24,
    }


def test_schedule_month_indices_reads_canonical_offsets_without_mutation():
    rows = [
        {"key": "construction_start", "month_index": 0},
        {"key": "completion", "month_index": 24, "excel": {"cell": "E26"}},
    ]
    original = deepcopy(rows)
    assert schedule_month_indices(rows) == {"construction_start": 0, "completion": 24}
    assert rows == original


def test_schedule_month_indices_preserves_calendar_construction_origin():
    assert schedule_month_indices({
        "land_purchase": "2026-01-01", "construction_start": "2026-04-01", "completion": "2028-04",
    }) == {"land_purchase": -3, "construction_start": 0, "completion": 24}


def test_mixed_calendar_and_month_schedule_requires_supplied_reference():
    with pytest.raises(FinancialInputError, match="기준") as exc:
        schedule_month_indices({"construction_start": "2026-01-01", "completion": "M24"})
    assert exc.value.code == "schedule_reference_required"


def test_mixed_calendar_and_month_schedule_uses_explicit_reference():
    assert schedule_month_indices({
        "construction_start": {"planned_date": "2026-01-01", "month_index": 0},
        "completion": "M24", "loan_maturity": "2028-07-01",
    }) == {"construction_start": 0, "completion": 24, "loan_maturity": 30}


@pytest.mark.parametrize("value", ["2026-02-30", "unknown", "M-1", -1, 1.5, True, {"month_index": -1}])
def test_schedule_month_indices_rejects_unknown_or_invalid_evidence(value):
    with pytest.raises(FinancialInputError) as exc:
        schedule_month_indices({"completion": value})
    assert exc.value.code == "invalid_schedule"


def test_schedule_month_indices_rejects_conflicting_reference_rows():
    with pytest.raises(FinancialInputError, match="기준월"):
        schedule_month_indices({
            "construction_start": {"planned_date": "2026-01-01", "month_index": 0},
            "completion": {"planned_date": "2028-01-01", "month_index": 30},
        })


def test_schedule_month_indices_canonical_clear_does_not_revive_alias():
    assert schedule_month_indices({
        "completion": {"planned_date": None, "plannedDate": "2028-01-01", "value": "M24"},
    }) == {}


def test_schedule_event_map_canonical_clear_does_not_revive_alias():
    assert schedule_event_map([{
        "key": "completion", "planned_date": None, "plannedDate": "2028-01-01",
    }]) == {}


def test_schedule_month_indices_supports_legacy_named_month_rows():
    assert schedule_month_indices([{"name": "completion", "monthIndex": 24}]) == {"completion": 24}


@pytest.mark.parametrize("start,end", [("2028-02", "2028-01"), ("2028-01-31", "2028-01-01"), ("M12", "M6")])
def test_schedule_month_indices_rejects_completion_before_construction(start, end):
    with pytest.raises(FinancialInputError, match="착공"):
        schedule_month_indices({"construction_start": start, "completion": end})


@pytest.mark.parametrize("key", [1, 0, True, [], {}])
def test_schedule_month_indices_rejects_non_text_milestone_keys(key):
    with pytest.raises(FinancialInputError, match="문자열") as exc:
        schedule_month_indices([{"key": key, "month_index": 0}])
    assert exc.value.code == "invalid_schedule"
