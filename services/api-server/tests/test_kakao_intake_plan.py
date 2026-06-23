from agents.orchestration_planner_agent import OrchestrationPlannerAgent
from integrations.kakao import (
    ALLOWED_NOTIFICATION_EVENTS,
    build_kakao_intake_plan_message,
    build_kakao_workflow_result_message,
)


PLAN_PROJECT = {
    "project": {"id": "kp1", "name": "역세권 주상복합"},
    "feasibility_input": {"total_revenue": 2800, "total_cost": 2050},
    "pf_structure": [{"tranche": "Senior", "amount": 1200, "priority": 1, "interest_rate": 0.058}],
}


def test_new_kakao_events_registered():
    assert "analysis_plan_ready" in ALLOWED_NOTIFICATION_EVENTS
    assert "workflow_result_ready" in ALLOWED_NOTIFICATION_EVENTS


def test_intake_plan_message_is_safe_and_has_intent_menu():
    plan = OrchestrationPlannerAgent().plan("kp1", PLAN_PROJECT)
    msg = build_kakao_intake_plan_message("kp1", "역세권 주상복합", plan)
    assert msg["eventType"] == "analysis_plan_ready"
    assert msg["intentMenu"]
    assert all(item["key"] and item["nameKo"] for item in msg["intentMenu"])
    assert sum(1 for item in msg["intentMenu"] if item["recommended"]) == 1
    assert msg["recommendedMobilePath"] is None or "kakao-intake" in msg["recommendedMobilePath"]


def test_intake_plan_message_blocks_sensitive_payload():
    plan = OrchestrationPlannerAgent().plan("kp1", PLAN_PROJECT)
    msg = build_kakao_intake_plan_message("kp1", "PF 1234567890", plan)
    assert "1234567890" not in msg["templateObject"]["text"]


def test_workflow_result_message_safe():
    msg = build_kakao_workflow_result_message("kp1", "역세권 주상복합", "feasibility", "completed")
    assert msg["eventType"] == "workflow_result_ready"
    assert msg["policy"]["noSensitivePfData"] is True
    assert "REALYTICS" in msg["templateObject"]["text"]
