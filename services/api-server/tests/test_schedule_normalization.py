from core.schedule import schedule_event_map
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
