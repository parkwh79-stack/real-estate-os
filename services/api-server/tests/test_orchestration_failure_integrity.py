from copy import deepcopy
from types import SimpleNamespace

import pytest

from agents.orchestration_agent import OrchestrationAgent
from db.repositories.request_project_repository import project_request_scope
from use_cases import chief_deal_orchestration as chief
from use_cases.pod_execution_coordinator import PodExecutionCoordinator
from use_cases.chief_pod_records import pod_input_snapshot, stable_digest


def _run():
    agent = OrchestrationAgent()
    return agent, {"run_id": "failure-test", "workflow": "pf-review", "steps": [],
                   "handoffs": [], "artifacts": {}, "errors": [],
                   "agentProgress": agent._initial_agent_progress(agent._now())}


def test_failed_step_cannot_be_displayed_as_completed():
    agent, run = _run()
    agent._complete_step(run, "return_metrics", {"status": "error", "reason": "invalid schedule"})
    assert run["steps"][-1]["status"] == "failed"
    assert run["agentProgress"]["return_metrics"]["status"] == "failed"
    assert run["agentProgress"]["return_metrics"]["completedTasks"] == []


def test_execution_completion_keeps_pending_handoff_review():
    agent, run = _run()
    run["agentProgress"]["return_metrics"]["reviewRequired"] = True
    agent._complete_step(run, "return_metrics", {"equityIrr": 0.1})
    assert run["agentProgress"]["return_metrics"]["reviewRequired"] is True


def test_failed_workflow_does_not_mutate_callers_project(monkeypatch):
    agent = OrchestrationAgent()
    project = {"project": {"id": "p1"}, "feasibility_input": {"total_cost": 800}}
    before = deepcopy(project)
    def fail(run, data, step, options):
        data["feasibility_input"]["total_cost"] = 999
        raise RuntimeError("execution failed")
    monkeypatch.setattr(agent, "_wf_feasibility", fail)
    result = agent.run_workflow("p1", project, "feasibility", {})
    assert result["status"] in {"error", "failed"}
    assert project == before


def test_chief_rejects_failed_domain_workflow_before_merging_artifacts(monkeypatch):
    project = {"project": {"id": "p1"}}
    monkeypatch.setattr(OrchestrationAgent, "run_workflow", lambda *args, **kwargs: {
        "status": "error", "artifacts": {"feasibility": {"profit": 123}},
        "errors": [{"message": "not complete"}],
    })
    with pytest.raises(RuntimeError, match="workflow"):
        chief._run_domain_workflow("p1", "feasibility", project, {})
    assert "feasibility" not in project


@pytest.mark.parametrize("status", ["failed", "cancelled", "blocked"])
def test_failed_domain_pod_does_not_persist_or_mutate_project(monkeypatch, status):
    project = {"project": {"id": "p1"}}
    before = deepcopy(project)
    pod = SimpleNamespace(pod_key="land_legal", dependencies=[])
    monkeypatch.setattr(chief, "_missing_inputs", lambda data: [])
    monkeypatch.setattr(chief, "_agent_pods", lambda *args: [pod])
    monkeypatch.setattr(chief, "_pod_blocked", lambda *args: False)
    def failed_runtime(**kwargs):
        kwargs["project_data"]["uncommittedAgentOutput"] = 123
        return {"status": status}, {"status": status, "agentRunId": "failed-run"}
    monkeypatch.setattr(chief, "run_domain_pod_with_contract", failed_runtime)
    def unexpected_write(*args):
        pytest.fail("Failed domain output was persisted as a successful artifact")
    with pytest.raises(RuntimeError, match="runtime"):
        chief.run_chief_deal_pod_use_case(
            "p1", "land_legal", project, {}, artifact_sink=unexpected_write,
            calculation_sink=unexpected_write, handoff_sink=unexpected_write,
        )
    assert project == before


@pytest.mark.parametrize("failure", ["envelope", "exception"])
def test_caught_child_agent_failure_cannot_finalize_successful_pod(monkeypatch, failure):
    monkeypatch.setattr(chief, "_run_domain_workflow", lambda *args: {
        "status": "completed", "artifacts": {"pfStructure": {"ltv": 0.7}},
    })

    def child(**kwargs):
        if kwargs["agent_id"] == "CovenantEodAgent":
            if failure == "exception":
                raise RuntimeError("covenant computation failed")
            return {"status": "failed"}, {"status": "failed"}
        return {"status": "clear"}, {"status": "completed"}

    monkeypatch.setattr(chief, "run_domain_agent_callable_with_contract", child)
    with pytest.raises(RuntimeError, match="failed|successfully"):
        chief._execute_pod_artifact("p1", "pf_credit", {}, {})


def test_pf_run_carries_authoritative_tenant_before_execution(monkeypatch):
    agent = OrchestrationAgent()
    monkeypatch.setattr(agent, "_run_term_annotation_step", lambda *args: None)
    def stop(*args, **kwargs):
        raise RuntimeError("stop after scope binding")
    monkeypatch.setattr(agent, "_run_domain_agent", stop)
    with project_request_scope(lambda: SimpleNamespace(tenant_id="tenant-a")):
        run = agent.run_pf_review("p1", {"project": {"id": "p1"}}, {})
    assert run["tenant_id"] == "tenant-a"


@pytest.mark.parametrize("artifact,gate", [
    ("dataQualityReview", "DATA_QUALITY_NOT_BLOCKING"),
    ("reconciliationGate", "RECONCILIATION_NOT_BLOCKING"),
    ("riskRegister", "RISK_REGISTER_NOT_BLOCKING"),
    ("creditReviewDecision", "CREDIT_DECISION_NOT_REJECT_OR_REWORK"),
])
def test_missing_review_cannot_pass_its_approval_gate(artifact, gate):
    agent, run = _run()
    run["artifacts"].pop(artifact, None)
    result = agent._approval_readiness_gate(run)
    check = next(item for item in result["gates"] if item["code"] == gate)
    assert check["pass"] is False


@pytest.mark.parametrize("qa", [None, {"status": "failed"}, {"status": "regression_detected"}, {"status": "error"},
                              {"status": "passed"}, {"status": "passed", "baseline_reviewed": False},
                              {"status": "baseline_created"}, {"status": "baseline_unreviewed"}])
def test_missing_or_failed_visual_qa_cannot_pass_report_quality(qa):
    agent, run = _run()
    for key in ("landAgentReview", "developerFeasibility", "feasibility", "pfStructure",
                "marketEvidenceReview", "marketabilityAnalysis", "dataQualityReview",
                "reconciliationGate", "legalPermitReview", "riskAssessment", "riskRegister",
                "creditReviewDecision", "restructuringPlan", "evidenceGraph"):
        run["artifacts"][key] = {"status": "complete"}
    run["artifacts"]["phase11Report"] = {"report": {"qualityChecks": {"isComplete": True, "missingSections": []}}}
    assert agent._quality_check(run, {"status": "passed"}, qa)["pass"] is False


def test_dependency_graph_rejects_duplicate_pod_identity():
    with pytest.raises(ValueError, match="Duplicate"):
        PodExecutionCoordinator().topological_order([
            SimpleNamespace(pod_key="land_legal", dependencies=[]),
            SimpleNamespace(pod_key="land_legal", dependencies=[]),
        ])


def test_pod_input_snapshot_keeps_the_values_hashed_before_execution():
    project = {"feasibility_input": {"total_cost": 800}, "activeAssumptionOverrides": [{"value": 1}]}
    dependencies = {"market_product": {"outputHash": "before"}}
    snapshot = pod_input_snapshot("cost_schedule", project, dependencies)
    original_hash = stable_digest(snapshot)
    project["feasibility_input"]["total_cost"] = 999
    project["activeAssumptionOverrides"][0]["value"] = 2
    dependencies["market_product"]["outputHash"] = "after"
    assert stable_digest(snapshot) == original_hash


def test_credit_document_failure_invalidates_readiness_already_computed():
    agent, run = _run()
    run["artifacts"]["approvalReadinessGate"] = {"pass": True, "status": "ready_for_approval", "gates": [], "blockers": []}
    run.update(approvalStatus="pending", approvalReadinessStatus="ready_for_approval")
    agent._complete_step(run, "credit_document_generation", {"status": "error"})
    agent._finalize_pf_execution(run, {"pass": True})
    assert run["status"] == "failed"
    assert run["approvalStatus"] == "not_requested"
    assert run["artifacts"]["approvalReadinessGate"]["pass"] is False
    assert run["artifacts"]["approvalReadinessGate"]["summary"]["blockingIssueCount"] == 1
