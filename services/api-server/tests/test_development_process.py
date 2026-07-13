from use_cases.development_process import get_development_process
from use_cases.chief_deal_orchestration import plan_chief_deal_orchestration_use_case


def test_development_process_contains_lifecycle_and_feedback_loops():
    process = get_development_process("residential")
    assert [item["key"] for item in process["planningStages"]] == [
        "direction_setting",
        "development_concretization",
        "design_and_product",
        "sales_or_leasing",
        "business_analysis",
    ]
    assert len(process["executionStages"]) == 4
    assert any(item["event"] == "market_demand_changed" for item in process["feedbackLoops"])
    assert process["assetTypeMapping"]["residential"]["primaryOutput"] == "sales_plan"


def test_chief_task_graph_exposes_lifecycle_process():
    plan = plan_chief_deal_orchestration_use_case(
        "development-process-test",
        {"project": {"type": "residential"}},
        {"workflowTrack": "development"},
    )
    graph = plan["taskGraph"]
    assert graph["lifecycleProcess"]["version"] == "realytics-development-process-v1"
    assert graph["feedbackLoops"]
