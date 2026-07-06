from use_cases.chief_deal_orchestration import (
    run_chief_deal_all_pods_use_case,
    run_chief_deal_pod_use_case,
)


def _full_project(pid: str) -> dict:
    return {
        "project": {"id": pid, "name": "캐스케이드 테스트 PF"},
        "source_documents": [{"documentType": "business_plan", "originalFileName": "b.pdf", "archiveId": "a1"}],
        "land": {"pnu": "1111010100100010000", "officialLandPricePerSqm": 1000000},
        "landEvidence": [{"evidenceType": "official_land_price", "pnu": "1111010100100010000"}],
        "regulation": {"far": 300},
        "legal": {"permitStatus": "pending"},
        "market": {"manualComparables": [{"name": "A"}]},
        "product_mix": [{"type": "apartment", "units": 100}],
        "feasibility_input": {"total_revenue": 1000, "total_cost": 800, "construction_cost": 300},
        "schedule": {"completionMonth": 36},
        "pf_structure": [{"tranche": "Senior", "amount": 500}],
    }


def test_event_sink_receives_durable_recalculation_events():
    project = _full_project("pev")
    captured = []

    run_chief_deal_pod_use_case(
        "pev",
        "cost_schedule",
        project,
        {},
        event_sink=captured.append,
    )

    assert captured, "cost_schedule fires finance_cost_changed"
    event = captured[0]
    assert event["eventType"] == "recalculation_triggered"
    assert event["trigger"] == "finance_cost_changed"
    assert set(event["targetPods"]) == {"feasibility_cashflow", "pf_credit"}


def test_event_sink_failure_never_breaks_the_pod_run():
    project = _full_project("psink")

    def _boom(event):
        raise RuntimeError("audit store down")

    plan = run_chief_deal_pod_use_case("psink", "cost_schedule", project, {}, event_sink=_boom)
    runs = {r["podKey"]: r["status"] for r in plan["podRuns"]}
    assert runs["cost_schedule"] == "completed"


def test_run_all_cascades_needs_recalculation_pods_with_bounded_rounds():
    project = _full_project("pcas")
    captured = []

    # market_product fires market_price_unsupported which targets ITSELF (+feasibility):
    # after the main pass it stays needs_recalculation -> cascade must re-run it,
    # and because each re-run re-fires the trigger, the rounds bound must stop it.
    plan = run_chief_deal_all_pods_use_case(
        "pcas",
        project,
        {"forceTrigger": "market_price_unsupported"},
        event_sink=captured.append,
        max_recalculation_rounds=2,
    )

    summary = plan["runAllSummary"]
    # round1: market_product 재실행(재발화) → round2: market_product + 전파된 feasibility_cashflow
    assert summary["recalculatedCount"] == 3
    rows = summary["recalculationRows"]
    assert [(row["round"], row["podKey"], row["status"]) for row in rows] == [
        (1, "market_product", "recalculated"),
        (2, "market_product", "recalculated"),
        (2, "feasibility_cashflow", "recalculated"),
    ]
    assert all(
        "market_price_unsupported" in (row.get("triggers") or []) for row in rows if row["podKey"] == "market_product"
    )
    # 최초 main-pass 발화 + 캐스케이드 재실행마다 재발화 → durable sink에 3회 이상 기록
    triggers = [e["trigger"] for e in captured if e.get("trigger") == "market_price_unsupported"]
    assert len(triggers) >= 3


def test_run_all_without_triggers_reports_zero_recalculations():
    project = _full_project("pnone")
    plan = run_chief_deal_all_pods_use_case("pnone", project, {})
    assert plan["runAllSummary"]["recalculatedCount"] == 0
    assert plan["runAllSummary"]["recalculationRows"] == []
