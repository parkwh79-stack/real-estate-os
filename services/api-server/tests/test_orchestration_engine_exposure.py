"""New-engine exposure in the full PF review run and Phase 11 report."""

from agents.orchestration_agent import OrchestrationAgent
from agents.report_agent import ReportAgent


PROJECT = {
    "project": {"id": "exp1", "name": "엔진 노출 테스트"},
    "feasibility_input": {
        "total_revenue": 2800,
        "total_cost": 2050,
        "land_cost": 640,
        "construction_cost": 850,
        "finance_cost": 120,
        "net_operating_income": 180,
    },
    "product_mix": [{"type": "apartment", "units": 200, "avg_unit_price": 10, "total_revenue": 2000}],
    "pf_structure": [
        {"tranche": "Senior", "amount": 1200, "priority": 1, "interest_rate": 0.058, "fee_rate": 0.01},
        {"tranche": "Mezzanine", "amount": 400, "priority": 2, "interest_rate": 0.085},
    ],
    "credit_enhancements": [
        {"type": "responsible_completion", "present": True, "evidenceIds": ["ev-rc"]},
    ],
}


def test_run_exposes_new_engine_artifacts():
    run = OrchestrationAgent().run_pf_review("exp1", dict(PROJECT))
    for key in ("returnMetrics", "stressTest", "creditEnhancement"):
        assert key in run["artifacts"], f"missing artifact {key}"
    assert run["artifacts"]["stressTest"]["scenarioCount"] == 4
    step_keys = {step["key"] for step in run["steps"]}
    assert {"return_metrics", "stress_test", "credit_enhancement_review", "cash_sweep_simulation"} <= step_keys


def test_cash_sweep_runs_when_cashflow_provided():
    project = dict(PROJECT)
    project["monthly_operating_cashflow"] = [200, 200, 200, 200, 200, 200]
    run = OrchestrationAgent().run_pf_review("exp1", project)
    assert "cashSweep" in run["artifacts"]
    cash_sweep_step = next(step for step in run["steps"] if step["key"] == "cash_sweep_simulation")
    assert cash_sweep_step["output"].get("status") != "skipped"


def test_phase11_report_surfaces_returns_stress_and_credit_enhancement():
    run = OrchestrationAgent().run_pf_review("exp1", dict(PROJECT))
    project_with_artifacts = dict(PROJECT)
    project_with_artifacts["returnMetrics"] = run["artifacts"]["returnMetrics"]
    project_with_artifacts["stressTest"] = run["artifacts"]["stressTest"]
    project_with_artifacts["creditEnhancement"] = run["artifacts"]["creditEnhancement"]

    report = ReportAgent().generate_structured_report(project_with_artifacts)
    business = report["sections"]["businessFeasibility"]
    assert business["metrics"]["projectIrr"] is not None
    assert business["metrics"]["icr"] is not None
    assert business["stressTest"]["scenarios"] and len(business["stressTest"]["scenarios"]) == 4
    credit_enhancement = report["sections"]["claimPreservation"]["creditEnhancement"]
    assert credit_enhancement is not None and credit_enhancement["enhancementGrade"]
