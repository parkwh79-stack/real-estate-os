import pytest

from use_cases.chief_deal_orchestration import (
    run_chief_deal_all_pods_use_case,
    run_chief_deal_pod_use_case,
    run_chief_deal_pod_with_cascade_use_case,
)


def _full_project(pid: str) -> dict:
    """MOCK-DATA: explicit revenue, cost allocation and dates for event tests."""
    return {
        "project": {"id": pid, "name": "Cascade test PF"},
        "source_documents": [
            {"documentType": "business_plan", "originalFileName": "b.pdf", "archiveId": "a1"}
        ],
        "land": {"pnu": "1111010100100010000", "officialLandPricePerSqm": 1_000_000},
        "landEvidence": {
            "officialLandPriceEvidence": {"evidenceType": "official_land_price", "pnu": "1111010100100010000"},
        },
        "regulation": {"far": 300},
        "legal": {"permitStatus": "pending"},
        "market": {"manualComparables": [{"name": "A"}]},
        "product_mix": [{"type": "apartment", "units": 100, "totalRevenue": 1000}],
        "feasibility_input": {
            "total_revenue": 1_000,
            "total_cost": 800,
            "construction_cost": 300,
            "land_cost": 500,
        },
        "schedule": {"construction_start": "2026-01", "completion": "2029-01"},
        "pf_structure": [{"tranche": "Senior", "amount": 500}],
    }


def _mark_completed(project: dict, *pod_keys: str) -> None:
    runs = project.setdefault("chief_orchestration_state", {}).setdefault("podRuns", {})
    for pod_key in pod_keys:
        runs[pod_key] = {
            "podKey": pod_key,
            "status": "completed",
            "artifactKeys": [f"artifact:{pod_key}:seed"],
            "outputHash": f"seed-{pod_key}-output",
            "recalculationEvents": [],
        }


def test_event_sink_receives_durable_recalculation_events():
    project = _full_project("pev")
    _mark_completed(project, "land_legal")
    captured = []

    run_chief_deal_pod_use_case(
        "pev",
        "cost_schedule",
        project,
        {"forceTrigger": "finance_cost_changed"},
        event_sink=captured.append,
    )

    event = captured[0]
    assert event["eventType"] == "recalculation_triggered"
    assert event["trigger"] == "finance_cost_changed"
    assert set(event["targetPods"]) == {"feasibility_cashflow", "pf_credit"}
    assert event["status"] == "pending_recalculation"


def test_event_sink_failure_is_not_hidden_for_audit_critical_flow():
    project = _full_project("psink")
    _mark_completed(project, "land_legal")

    def _boom(event):
        raise RuntimeError("audit store down")

    with pytest.raises(RuntimeError, match="audit store down"):
        run_chief_deal_pod_use_case(
            "psink",
            "cost_schedule",
            project,
            {"forceTrigger": "finance_cost_changed"},
            event_sink=_boom,
        )


def test_run_all_consumes_trigger_without_repeating_unchanged_output():
    project = _full_project("pcas")
    captured = []

    plan = run_chief_deal_all_pods_use_case(
        "pcas",
        project,
        {"forceTrigger": "market_price_unsupported"},
        event_sink=captured.append,
        max_recalculation_rounds=2,
    )

    summary = plan["runAllSummary"]
    assert summary["recalculatedCount"] == 0
    assert summary["recalculationRows"] == []
    lifecycle = [
        event["status"]
        for event in captured
        if event.get("trigger") == "market_price_unsupported"
    ]
    assert "pending_recalculation" in lifecycle
    assert "running" in lifecycle
    assert "completed" in lifecycle


def test_run_all_without_triggers_reports_zero_recalculations():
    project = _full_project("pnone")
    plan = run_chief_deal_all_pods_use_case("pnone", project, {})
    assert plan["runAllSummary"]["recalculatedCount"] == 0
    assert plan["runAllSummary"]["recalculationRows"] == []


def test_single_pod_auto_cascade_consumes_downstream_recalculation_queue():
    project = _full_project("pauto")
    _mark_completed(project, "land_legal", "market_product")

    plan = run_chief_deal_pod_with_cascade_use_case(
        "pauto",
        "cost_schedule",
        project,
        {"forceTrigger": "finance_cost_changed"},
        max_rounds=1,
    )

    summary = plan["autoCascadeSummary"]
    assert summary["recalculatedCount"] >= 1
    assert any(row["podKey"] == "feasibility_cashflow" for row in summary["rows"])
    statuses = {row["podKey"]: row["status"] for row in plan["podRuns"]}
    assert statuses["cost_schedule"] == "completed"
