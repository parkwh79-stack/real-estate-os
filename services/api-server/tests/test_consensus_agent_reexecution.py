"""Offline regression coverage for fresh, isolated formal consensus evaluations.

These fixtures are synthetic analysis inputs, never approved business evidence.
Spies wrap the actual deterministic Agents (and the real durable runtime) so an
idempotency replay or a callback holding old results cannot satisfy the tests.
"""

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from agents.credit_review_agent import CreditReviewAgent
from agents.covenant_eod_agent import CovenantEodAgent
from agents.data_quality_agent import DataQualityAgent
from agents.deal_optimization_agent import DealOptimizationAgent
from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.legal_review_agent import LegalReviewAgent
from agents.market_evidence_agent import MarketEvidenceAgent
from agents.market_product_depth_agent import MarketProductDepthAgent
from agents.marketability_agent import MarketabilityAgent
from agents.orchestration_agent import OrchestrationAgent
from agents.product_planning_agent import ProductPlanningAgent
from agents.restructuring_agent import RestructuringAgent
from agents.risk_register_agent import RiskRegisterAgent
from schemas.orchestration import PF_REVIEW_STEPS, PF_REVIEW_WORKFLOW


_EXAMPLE = Path(__file__).resolve().parents[1] / "data/examples/daegu_bondong_mixed_use_pf.json"
_OPTIONS = {"tenantId": "pytest-consensus", "userId": "pytest-consensus-actor"}


def _source(*, ledger_area=5000):
    source = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    source["project"]["id"] = f"pytest-consensus-{uuid4().hex}"
    source["project"]["name"] = "Synthetic independent consensus evaluation"
    source["_meta"] = {"tenantId": _OPTIONS["tenantId"]}
    source["landLedger"] = {
        "summary": {"parcelCount": 1, "totalAreaSqm": ledger_area},
        "validation": {"status": "ready_for_pf_review", "issues": []},
        "pfSummary": {
            "status": "ready_for_pf_review",
            "pnuCoverageRatio": 1,
            "officialLandPriceCoverageRatio": 1,
            "registryEvidenceCoverageRatio": 1,
        },
        "landAcquisitionStatus": {"acquisitionAreaRatio": 1},
        "parcels": [{"pnu": source["land"]["pnu"], "areaSqm": ledger_area}],
    }
    source["product_mix"] = [{
        "type": "apartment", "units": 10, "exclusiveAreaPy": 24,
        "contractAreaPy": 30, "avgUnitPrice": 15_000_000_000,
        "totalRevenue": 150_000_000_000,
    }]
    return source


def _run(agent, source):
    return {
        "run_id": f"pytest-consensus-run-{uuid4().hex}",
        "project_id": source["project"]["id"],
        "tenant_id": _OPTIONS["tenantId"],
        "workflow": PF_REVIEW_WORKFLOW,
        "steps": [], "handoffs": [], "artifacts": {}, "errors": [],
        "agentRuntimeExecutions": [],
        "agentProgress": agent._initial_agent_progress(agent._now()),
    }


def _spy(monkeypatch, owner, method, calls, label):
    original = getattr(owner, method)

    def observe(self, source, *args, **kwargs):
        item = {"source": deepcopy(source), "args": deepcopy(args)}
        result = original(self, source, *args, **kwargs)
        item["output"] = deepcopy(result)
        calls.setdefault(label, []).append(item)
        return result

    monkeypatch.setattr(owner, method, observe)


def _observe_domains(monkeypatch):
    calls = {}
    for owner, method, label in (
        (OrchestrationAgent, "_review_land_readiness", "landAgentReview"),
        (DevelopmentFeasibilityAgent, "analyze", "developerFeasibility"),
        (LegalReviewAgent, "review", "legalPermitReview"),
        (MarketEvidenceAgent, "review", "marketEvidenceReview"),
        (MarketabilityAgent, "analyze", "marketabilityAnalysis"),
        (MarketProductDepthAgent, "analyze", "marketProductDepth"),
        (OrchestrationAgent, "_calculate_feasibility", "feasibility"),
        (OrchestrationAgent, "_calculate_pf_structure", "pfStructure"),
        (OrchestrationAgent, "_assess_risk", "riskAssessment"),
        (DataQualityAgent, "review", "dataQualityReview"),
        (RiskRegisterAgent, "build", "riskRegister"),
        (CreditReviewAgent, "decide", "creditReviewDecision"),
        (DealOptimizationAgent, "optimize", "dealOptimization"),
        (RestructuringAgent, "build_restructuring_plan", "restructuringPlan"),
    ):
        _spy(monkeypatch, owner, method, calls, label)
    for owner, method, label in (
        (ProductPlanningAgent, "compare", "productPlanning"),
        (CovenantEodAgent, "build", "covenantEod"),
    ):
        _spy_keyword_inputs(monkeypatch, owner, method, calls, label)
    return calls


def _spy_keyword_inputs(monkeypatch, owner, method, calls, label):
    original = getattr(owner, method)

    def observe(self, **kwargs):
        item = {"inputs": deepcopy(kwargs)}
        result = original(self, **kwargs)
        item["output"] = deepcopy(result)
        calls.setdefault(label, []).append(item)
        return result

    monkeypatch.setattr(owner, method, observe)


def test_consensus_cycle_reexecutes_raw_agents_and_exchanges_fresh_results(monkeypatch):
    from agents.consensus_review_cycle import recompute_consensus_cycle

    calls = _observe_domains(monkeypatch)
    agent = OrchestrationAgent()
    source = _source()
    # Persisted derived values must not become inputs to a fresh evaluation.
    source["marketabilityAnalysis"] = {"staleSentinel": True}
    source["returnMetrics"] = {"staleSentinel": True}
    original = deepcopy(source)
    run = _run(agent, source)
    run["artifacts"] = {"staleOriginal": {"keep": True}}
    original_run = deepcopy(run)

    baseline = recompute_consensus_cycle(agent, run, source, _OPTIONS, evaluation_index=0)
    # Same source must still execute each raw Agent, not replay the first result.
    repeated = recompute_consensus_cycle(agent, run, source, _OPTIONS, evaluation_index=1)
    changed = deepcopy(source)
    changed["land"]["area_sqm"] = 6000
    changed["landLedger"]["summary"]["totalAreaSqm"] = 6000
    changed["landLedger"]["parcels"][0]["areaSqm"] = 6000
    changed["feasibility_input"]["total_revenue"] = 180_000_000_000
    changed["product_mix"][0]["totalRevenue"] = 180_000_000_000
    changed["product_mix"][0]["avgUnitPrice"] = 18_000_000_000
    final = recompute_consensus_cycle(agent, run, changed, _OPTIONS, evaluation_index=2)

    assert source == original
    assert run == original_run
    assert baseline["inputHash"] == repeated["inputHash"]
    assert final["inputHash"] != baseline["inputHash"]
    for name, observed in calls.items():
        assert len(observed) == 3, f"{name} reused a cached/captured result"
        if name == "restructuringPlan":
            assert [sum(item.totalRevenue for item in record["source"].productMix)
                    for record in observed] == [150_000_000_000, 150_000_000_000, 180_000_000_000]
            continue
        if name == "productPlanning":
            assert [item["inputs"]["land_area_sqm"] for item in observed] == [5000, 5000, 6000]
            assert [item["inputs"]["applied_far"] for item in observed] == [750, 750, 750]
            continue
        if name == "covenantEod":
            continue  # Compared to the exact fresh Developer artifact below.
        assert [item["source"]["land"]["area_sqm"] for item in observed] == [5000, 5000, 6000]
        assert [item["source"]["feasibility_input"]["total_revenue"] for item in observed] == [
            150_000_000_000, 150_000_000_000, 180_000_000_000,
        ]
    assert final["artifacts"]["landAgentReview"]["summary"]["totalAreaSqm"] == 6000
    assert final["artifacts"]["feasibility"]["development_profit"] == 60_000_000_000
    assert baseline["artifacts"]["feasibility"]["development_profit"] == 30_000_000_000
    assert final["artifacts"]["marketabilityAnalysis"]["projectSummary"]["totalSalesRevenue"] == 180_000_000_000
    assert final["artifacts"]["pfStructure"] != baseline["artifacts"]["pfStructure"]
    assert final["artifacts"]["productPlanning"]["landAreaSqm"] == 6000

    for index, evaluation in enumerate((baseline, repeated, final)):
        artifacts = evaluation["artifacts"]
        assert calls["covenantEod"][index]["inputs"]["analysis"] == artifacts["developerFeasibility"]
        assert calls["covenantEod"][index]["inputs"]["pf_structure"] == source["pf_structure"]
        assert calls["covenantEod"][index]["output"]["covenants"] == artifacts["covenantEod"]["covenants"]
        assert artifacts["productPlanningReadiness"]["executed"] is True
        assert artifacts["covenantEodReadiness"]["executed"] is True
        # The evidence reviewer receives *this* developer and calculation result.
        market_inputs = calls["marketEvidenceReview"][index]["args"]
        assert market_inputs[0] == artifacts["developerFeasibility"]
        assert market_inputs[1] == artifacts["feasibility"]
        dq_inputs = calls["dataQualityReview"][index]["args"][0]
        credit_inputs = calls["creditReviewDecision"][index]["args"][0]
        for key in ("landAgentReview", "developerFeasibility", "feasibility", "pfStructure", "marketEvidenceReview", "marketabilityAnalysis"):
            assert dq_inputs[key] == artifacts[key]
        for key in ("dataQualityReview", "riskRegister", "legalPermitReview", "pfStructure"):
            assert credit_inputs[key] == artifacts[key]
        assert "staleSentinel" not in json.dumps(artifacts)
        assert all(record["evaluationIndex"] == index for record in evaluation["executions"])
        assert len({record["executionScope"] for record in evaluation["executions"]}) >= 1
        assert artifacts["scenarioEngineReadiness"]["executed"] is True
        assert "scenariosEvaluation" in artifacts
        assert calls["restructuringPlan"][index]["args"][0].model_dump(mode="json") == artifacts["scenariosEvaluation"]
        assert calls["restructuringPlan"][index]["output"] == artifacts["restructuringPlan"]
        assert set(evaluation["podCoverage"]) == {
            "land_legal", "market_product", "cost_schedule",
            "feasibility_cashflow", "pf_credit", "public_policy",
        }
        assert all(coverage["sourceInputHash"] == evaluation["inputHash"]
                   for coverage in evaluation["podCoverage"].values())
        execution_ids = {record["agentId"] for record in evaluation["executions"]
                         if record["executed"]}
        assert execution_ids >= {
            "LandReadinessAgent", "LegalReviewAgent", "DevelopmentFeasibilityAgent",
            "MarketEvidenceAgent", "MarketabilityAgent", "MarketProductDepthAgent",
            "PFStructuringAgent", "ScenarioEngineAgent", "ReturnMetricsEngine",
            "StressTestAgent", "CreditEnhancementAgent", "DataQualityAgent",
            "RiskRegisterAgent", "CreditReviewAgent", "RestructuringAgent", "DealOptimizationAgent",
            "ProductPlanningAgent", "CovenantEodAgent",
        }
        assert "restructuringPlan" in artifacts
        for check in artifacts["dataQualityReview"]["checks"]:
            if check["status"] in {"warning", "blocking", "missing_data"}:
                assert any(item["key"] == f"data_quality:{check['key']}"
                           for item in evaluation["reconciliation"]["checks"])
        assert evaluation["handoffs"]


def test_failed_consensus_cycle_does_not_reuse_previous_success(monkeypatch):
    from agents.consensus_review_cycle import recompute_consensus_cycle

    agent = OrchestrationAgent()
    source = _source()
    run = _run(agent, source)
    success = recompute_consensus_cycle(agent, run, source, _OPTIONS, evaluation_index=0)
    run["artifacts"] = deepcopy(success["artifacts"])
    original_source, original_run = deepcopy(source), deepcopy(run)

    def fail_review(self, project_data):
        raise ValueError("synthetic independent market evaluation failed")

    monkeypatch.setattr(MarketabilityAgent, "analyze", fail_review)
    with pytest.raises(Exception, match="MarketabilityAgent|market evaluation failed|runtime"):
        recompute_consensus_cycle(agent, run, source, _OPTIONS, evaluation_index=1)
    assert source == original_source
    assert run == original_run


def test_consensus_legacy_candidate_does_not_replace_approved_cash_flow_source(monkeypatch):
    from agents.consensus_review_cycle import recompute_consensus_cycle
    from agents.report_agent import ReportAgent
    from use_cases.approved_workbook_authority import GOVERNED_WORKBOOK_CORE_FIELDS

    risk_cash_flow_inputs = []
    cash_flow_risks = RiskRegisterAgent._cash_flow_risks

    def observe_cash_flow_risks(self, cash_flow, source_artifact):
        risk_cash_flow_inputs.append((deepcopy(cash_flow), source_artifact))
        return cash_flow_risks(self, cash_flow, source_artifact)

    monkeypatch.setattr(RiskRegisterAgent, "_cash_flow_risks", observe_cash_flow_risks)
    source = _source()
    canonical = {
        "project_name": "Synthetic approved-source boundary fixture",
        "metrics": {"total_revenue": 200_000_000_000, "total_cost": 80_000_000_000},
        "monthly_cash_flow": [{
            "month_index": 0, "period": "M0", "cash_out": {"other_cost": 80_000_000_000},
            "source_locator": {"sheetName": "Synthetic input", "cell": "B10"},
        }],
        "schedule": [
            {"key": "construction_start", "label": "start", "planned_date": "2026-01-01"},
            {"key": "completion", "label": "completion", "planned_date": "2026-03-01"},
        ],
        "input_references": {
            "approved_input_snapshot_id": "synthetic-approved-source",
            "approved_input_snapshot_hash": "a" * 64,
        },
        "raw_import": {
            "canonicalStatus": "approved",
            "parserV2Bridge": {
                "status": "approved", "approvedInputSnapshotId": "synthetic-approved-source",
                "approvedInputSnapshotHash": "a" * 64,
                "approvedFields": list(GOVERNED_WORKBOOK_CORE_FIELDS),
            },
        },
    }
    source["canonicalFeasibilityStudyV2"] = deepcopy(canonical)
    agent = OrchestrationAgent()
    run = _run(agent, source)
    baseline = recompute_consensus_cycle(agent, run, source, _OPTIONS, evaluation_index=0)
    candidate = deepcopy(source)
    candidate["feasibility_input"]["total_revenue"] = 180_000_000_000
    final = recompute_consensus_cycle(agent, run, candidate, _OPTIONS, evaluation_index=1)

    assert len(risk_cash_flow_inputs) == 2
    for index, result in enumerate((baseline, final)):
        artifacts = result["artifacts"]
        selected = artifacts["cashFlowRunResult"]
        summary = selected["summary"]
        assert summary["totalRevenue"] == 200_000_000_000
        assert summary["totalProjectCost"] == 80_000_000_000
        assert risk_cash_flow_inputs[index] == (selected, "cashFlowRunResult")
        basis_review = artifacts["cashFlowBasisReview"]
        assert basis_review["status"] == "blocking"
        assert basis_review["sourceMode"] == "approved_canonical"
        assert basis_review["comparisonRequired"] is True
        assert basis_review["selectedTotals"] == {
            "totalRevenue": 200_000_000_000, "totalProjectCost": 80_000_000_000,
        }
        assert basis_review["workingTotals"]["totalRevenue"] == [150_000_000_000, 180_000_000_000][index]
        assert basis_review["conflicts"]
        assert result["reconciliation"]["status"] == "blocking"
        check = next(item for item in result["reconciliation"]["checks"]
                     if item["key"] == "cash_flow_input_basis_matching")
        assert check["status"] == "blocking"
        assert check["sourceArtifact"] == "cashFlowBasisReview"
        assert artifacts["creditReviewDecision"]["decision"] == "rework_required"

    # A read-only report must disclose that its working feasibility assumptions
    # and selected approved workbook cash flow use conflicting source totals.
    report = ReportAgent().generate_structured_report({
        **deepcopy(source),
        "cashFlowRunResult": deepcopy(baseline["artifacts"]["cashFlowRunResult"]),
        "cashFlowBasisReview": deepcopy(baseline["artifacts"]["cashFlowBasisReview"]),
    })
    cash_flow_section = report["sections"]["summaryCashFlow"]
    assert report["qualityChecks"]["inputBasisReview"] == baseline["artifacts"]["cashFlowBasisReview"]
    assert "inputBasisReview" not in cash_flow_section
    assert report["qualityChecks"]["inputBasisConsistent"] is False
    warnings = report["qualityChecks"]["inputBasisWarnings"]
    assert warnings
    assert any(warning in cash_flow_section["assumption"] for warning in warnings)
    assert any(warning in report["sections"]["businessFeasibility"]["summary"] for warning in warnings)

    quality_run = deepcopy(run)
    quality_run["artifacts"] = deepcopy(baseline["artifacts"])
    quality_run["artifacts"]["phase11Report"] = {"report": report}
    # This unit gate inspects artifact presence and supplied QA states only;
    # these inputs do not represent a rendered PDF or an approved evidence graph.
    quality_run["artifacts"]["evidenceGraph"] = {}
    quality = agent._quality_check(
        quality_run, {"status": "passed"},
        {"status": "passed", "baseline_reviewed": True},
    )
    assert quality["reportComplete"] is True
    assert quality["missingArtifacts"] == []
    assert quality["pdfStructureQA"]["status"] == "passed"
    assert quality["pdfVisualQA"] == {"status": "passed", "baseline_reviewed": True}
    assert quality["pass"] is False
    assert source["canonicalFeasibilityStudyV2"] == canonical
    assert candidate["canonicalFeasibilityStudyV2"] == canonical
    assert source["feasibility_input"]["total_revenue"] == 150_000_000_000


def test_formal_consensus_keeps_candidate_cycles_out_of_authoritative_report(monkeypatch):
    from agents.report_agent import ReportAgent

    monkeypatch.setattr("agents.orchestration_agent.select_ai_context", lambda **kwargs: None)
    calls = _observe_domains(monkeypatch)
    report_calls = {}
    _spy(monkeypatch, ReportAgent, "generate_structured_report", report_calls, "report")
    source = _source(ledger_area=6000)
    original = deepcopy(source)
    run = OrchestrationAgent().run_pf_review(source["project"]["id"], source, _OPTIONS)

    assert run["status"] != "failed", run["errors"]
    assert source == original
    assert len(run["steps"]) == len(PF_REVIEW_STEPS) == 27
    consensus = run["artifacts"]["consensusResolution"]
    candidate = consensus["candidateReview"]
    assert candidate["analysisOnly"] is True
    assert candidate["reportEligible"] is False
    assert candidate["approvalStatus"] == "pending_approval"
    assert consensus["assumptionProposals"]
    assert candidate["evaluations"]
    assert candidate["inputHash"] == candidate["evaluations"][-1]["inputHash"]
    assert any(item["source"]["land"]["area_sqm"] == 6000 for item in calls["developerFeasibility"])
    assert any(item["source"]["land"]["area_sqm"] == 5000 for item in calls["developerFeasibility"])
    # A baseline evaluation is already a fresh run. A separate pre-loop analysis
    # or post-loop repeat would perform duplicate physical Agent work.
    for name, observed in calls.items():
        assert len(observed) == len(candidate["evaluations"]), name
    assert len(report_calls["report"]) == 1
    report_source = report_calls["report"][0]["source"]
    assert report_source["land"]["area_sqm"] == 5000
    assert report_source["feasibility_input"] == original["feasibility_input"]
    assert report_source["cashFlowBasisReview"] == run["artifacts"]["cashFlowBasisReview"]
    assert "agreedProjectData" not in consensus
    assert "finalArtifacts" not in consensus
    assert run["artifacts"]["feasibility"]["total_revenue"] == 150_000_000_000
    # The original land/ledger discrepancy must remain visible until approval.
    land_check = next(check for check in run["artifacts"]["reconciliationGate"]["checks"]
                      if check["key"] == "land_area_project_vs_land_agent")
    assert land_check["status"] in {"warning", "blocking"}
    assert candidate["artifacts"]["marketabilityAnalysis"]["projectSummary"]["landAreaSqm"] == 6000


def test_formal_consensus_agent_failure_cannot_publish_old_report(monkeypatch):
    monkeypatch.setattr("agents.orchestration_agent.select_ai_context", lambda **kwargs: None)
    original_analyze = MarketabilityAgent.analyze
    observed_areas = []

    def fail_on_proposed_input(self, source):
        observed_areas.append(source["land"]["area_sqm"])
        if source["land"]["area_sqm"] == 6000:
            raise ValueError("synthetic later-round market failure")
        return original_analyze(self, source)

    monkeypatch.setattr(MarketabilityAgent, "analyze", fail_on_proposed_input)
    source = _source(ledger_area=6000)
    original = deepcopy(source)
    run = OrchestrationAgent().run_pf_review(source["project"]["id"], source, _OPTIONS)

    assert 5000 in observed_areas and 6000 in observed_areas
    assert source == original
    assert run["status"] == "failed"
    assert run["executionStatus"] == "failed"
    assert run["errors"]
    assert "phase11Report" not in run["artifacts"]
    assert "consensusResolution" not in run["artifacts"]
    assert run["agentProgress"]["phase11_report_generation"]["status"] != "completed"
