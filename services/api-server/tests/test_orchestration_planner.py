from agents.orchestration_agent import OrchestrationAgent
from agents.orchestration_planner_agent import OrchestrationPlannerAgent


FULL_PROJECT = {
    "project": {"id": "p1", "name": "Mixed-use PF"},
    "feasibility_input": {"total_revenue": 2800, "total_cost": 2050, "land_cost": 640, "construction_cost": 850},
    "product_mix": [{"type": "apartment", "units": 200, "avg_unit_price": 10, "total_revenue": 2000}],
    "pf_structure": [
        {"tranche": "Senior", "amount": 1200, "priority": 1, "interest_rate": 0.058, "fee_rate": 0.01},
        {"tranche": "Mezzanine", "amount": 400, "priority": 2, "interest_rate": 0.085},
    ],
    "land_evidence": [{"type": "pnu_resolution", "pnu": "1111010100100010000"}],
}


def test_plan_lists_all_workflows_with_recommendation():
    plan = OrchestrationPlannerAgent().plan("p1", FULL_PROJECT)
    keys = {workflow["key"] for workflow in plan["workflows"]}
    assert keys == {"land_review", "feasibility", "marketability", "returns_stress", "pf_structure_review", "full_pf_review"}
    assert plan["recommendedWorkflow"] == "full_pf_review"
    assert sum(1 for workflow in plan["workflows"] if workflow["recommended"]) == 1
    assert plan["dataAvailability"]["feasibilityInputs"] is True


def test_plan_reports_missing_data_when_sparse():
    plan = OrchestrationPlannerAgent().plan("p2", {"project": {"id": "p2"}})
    feasibility = next(workflow for workflow in plan["workflows"] if workflow["key"] == "feasibility")
    assert feasibility["availableNow"] is False
    assert any("total_revenue" in item for item in feasibility["missingData"])
    land = next(workflow for workflow in plan["workflows"] if workflow["key"] == "land_review")
    assert land["availableNow"] is False
    full = next(workflow for workflow in plan["workflows"] if workflow["key"] == "full_pf_review")
    assert full["availableNow"] is True
    assert full["missingData"]


def test_plan_recommends_feasibility_when_only_feasibility_present():
    plan = OrchestrationPlannerAgent().plan(
        "p3",
        {
            "project": {"id": "p3"},
            "feasibility_input": {"total_revenue": 1000, "total_cost": 800},
        },
    )
    assert plan["recommendedWorkflow"] == "feasibility"


def test_run_workflow_feasibility_runs_only_feasibility_agents():
    run = OrchestrationAgent().run_workflow("p1", FULL_PROJECT, "feasibility")
    assert run["workflow"] == "feasibility"
    assert run["status"] in {"completed", "completed_with_warnings"}
    step_ids = {step["step"] for step in run["steps"]}
    assert step_ids == {"development_feasibility_analysis", "feasibility_calculation", "return_metrics"}
    assert "returnMetrics" in run["artifacts"]
    assert "marketabilityAnalysis" not in run["artifacts"]
    assert "phase11Report" not in run["artifacts"]


def test_run_workflow_returns_stress_uses_new_engines():
    run = OrchestrationAgent().run_workflow("p1", FULL_PROJECT, "returns_stress")
    assert {step["step"] for step in run["steps"]} == {"return_metrics", "stress_test"}
    assert run["artifacts"]["stressTest"]["scenarioCount"] == 4


def test_run_workflow_pf_structure_includes_credit_enhancement():
    project = dict(FULL_PROJECT)
    project["credit_enhancements"] = [{"type": "responsible_completion", "present": True, "evidenceIds": ["ev1"]}]
    run = OrchestrationAgent().run_workflow("p1", project, "pf_structure_review")
    assert {step["step"] for step in run["steps"]} == {"pf_structure_calculation", "credit_enhancement"}
    credit_enhancement = run["artifacts"]["creditEnhancement"]
    assert credit_enhancement["activeCount"] == 1
    assert credit_enhancement["adjustedSeniorLtv"] < credit_enhancement["baseSeniorLtv"]


def test_run_workflow_unknown_returns_error():
    run = OrchestrationAgent().run_workflow("p1", FULL_PROJECT, "nope")
    assert run["status"] == "error"
    assert run["errors"]
