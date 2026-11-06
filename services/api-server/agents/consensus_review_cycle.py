"""Fresh, isolated domain evaluation for a formal PF consensus round.

Only source inputs and pinned public references cross the round boundary.  The
caller decides whether the returned analysis is an original-input calculation
or an unapproved proposal; this module never publishes a project/report version.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, Callable

from agents.credit_review_agent import CreditReviewAgent
from agents.cash_flow_engine import run_cash_flow_engine
from agents.covenant_eod_agent import CovenantEodAgent
from agents.data_quality_agent import DataQualityAgent
from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.legal_review_agent import LegalReviewAgent
from agents.market_evidence_agent import MarketEvidenceAgent
from agents.market_product_depth_agent import MarketProductDepthAgent
from agents.marketability_agent import MarketabilityAgent
from agents.risk_register_agent import RiskRegisterAgent
from agents.scenario_engine_agent import ScenarioEngineAgent
from core.calculations import normalize_tranche_priority
from domains.feasibility.regulatory import build_preliminary_buildable_envelope
from schemas.scenario import ScenarioDrivers, ScenarioSetRequest, ScenarioSetResult
from use_cases.chief_pod_records import stable_digest
from use_cases.approved_workbook_authority import has_authoritative_workbook_snapshot_binding
from use_cases.cash_flow import build_cash_flow_request_use_case
from use_cases.covenant_eod import _resolve_asset_type
from use_cases.development_feasibility import development_feasibility_input_basis
from use_cases.product_planning import compare_product_planning_use_case


# These are calculation/review outputs, not source evidence.  In particular the
# ordinary pipeline has already attached several of them to project_data before
# entering consensus.  Letting them into a new round would be stale reuse.
_DERIVED_KEYS = {
    "artifacts", "agentRuntimeExecutions", "chief_orchestration_state",
    "landAgentReview", "developerFeasibility", "feasibility", "pfStructure",
    "pfStructureAnalysis", "marketEvidenceReview", "marketabilityAnalysis", "marketability",
    "marketProductDepth", "productPlanning", "productPlanningReadiness", "legalPermitReview",
    "covenantEod", "covenantEodReadiness",
    "cashFlowRunResult", "cashFlowRunRequest", "cashFlowReadiness",
    "returnMetrics", "stressTest", "creditEnhancement", "cashSweep",
    "cashSweepReadiness", "developabilityScore", "publicPolicyReview",
    "costScheduleReview", "riskAssessment", "riskRegister",
    "creditReviewDecision", "scenariosEvaluation", "scenarioEngineReadiness",
    "dataQualityReview", "reconciliationGate", "consensusResolution",
    "assumptionProposals", "dealOptimization", "restructuringPlan",
    "phase11Report", "evidenceGraph", "approvalReadinessGate",
}
_MARKET_REFERENCE_KEYS = (
    "transactionComparables", "closedTransactionComparables", "leaseComparables",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if isfinite(result) else None


def _first(row: dict[str, Any], *names: str) -> Any:
    return next((row[key] for key in names if row.get(key) is not None), None)


def _source_inputs(project_data: dict[str, Any], frozen_market: dict | None) -> dict[str, Any]:
    source = deepcopy({key: value for key, value in project_data.items() if key not in _DERIVED_KEYS})
    if isinstance(frozen_market, dict):
        references = frozen_market.get("market", frozen_market)
        if isinstance(references, dict):
            market = source.setdefault("market", {})
            if not isinstance(market, dict):
                raise ValueError("Consensus market input must be a mapping")
            for key in _MARKET_REFERENCE_KEYS:
                # This snapshot was fetched once before consensus. Never freeze
                # the old derived price, absorption, or total-sales estimates.
                if key in references:
                    market[key] = deepcopy(references[key])
    return source


def _scenario_request(source: dict[str, Any], run_id: str) -> tuple[ScenarioSetRequest | None, dict]:
    mix_key = "product_mix" if source.get("product_mix") else "productMix"
    raw_mix = source.get("product_mix") or source.get("productMix") or []
    raw_tranches = source.get("pf_structure") or []
    missing: list[str] = []
    derived_inputs: list[dict] = []
    mix: list[dict] = []
    tranches: list[dict] = []
    if not isinstance(raw_mix, list) or not raw_mix:
        missing.append("product_mix")
    else:
        for index, item in enumerate(raw_mix):
            path = f"{mix_key}[{index}]"
            if not isinstance(item, dict):
                missing.append(path)
                continue
            name = _first(item, "type", "name")
            units = _number(_first(item, "units", "unit_count"))
            raw_price = _first(item, "avgUnitPrice", "avg_unit_price")
            price = _number(raw_price)
            revenue = _number(_first(item, "totalRevenue", "total_revenue"))
            if raw_price in (None, "") and units is not None and units > 0 and revenue is not None and revenue > 0:
                # ScenarioEngine already uses this exact revenue/unit ratio.
                # Derive only a redundant schema field, never a market price
                # assumption, and keep its source paths visible in readiness.
                price = revenue / units
                derived_inputs.append({
                    "path": f"{path}.avgUnitPrice", "value": price,
                    "derivedFrom": [
                        f"{path}.{'totalRevenue' if item.get('totalRevenue') is not None else 'total_revenue'}",
                        f"{path}.{'units' if item.get('units') is not None else 'unit_count'}",
                    ],
                    "method": "totalRevenue / units",
                })
            for field, valid in (
                ("type", bool(name)),
                ("units", units is not None and units > 0 and units.is_integer()),
                ("avgUnitPrice", price is not None and price >= 0),
                ("totalRevenue", revenue is not None and revenue > 0),
            ):
                if not valid:
                    missing.append(f"{path}.{field}")
            if name and units is not None and units > 0 and units.is_integer() and price is not None and price >= 0 and revenue is not None and revenue > 0:
                mix.append({"type": str(name), "units": int(units), "avgUnitPrice": price, "totalRevenue": revenue})
    if not isinstance(raw_tranches, list) or not raw_tranches:
        missing.append("pf_structure")
    else:
        for index, item in enumerate(raw_tranches, start=1):
            if not isinstance(item, dict):
                missing.append(f"pf_structure[{index - 1}]")
                continue
            amount = _number(item.get("amount"))
            if amount is None or amount <= 0:
                missing.append(f"pf_structure[{index - 1}].amount")
                continue
            # Preserve the existing engine's rate/priority conventions. A zero
            # contractual rate is valid and is not replaced using truthiness.
            tranches.append({
                "tranche": _first(item, "tranche", "name") or f"Tr.{index}",
                "amount": amount,
                "priority": normalize_tranche_priority(item.get("priority"), index),
                "interestRate": _first(item, "interest_rate", "interestRate") or 0.0,
                "feeRate": _first(item, "fee_rate", "feeRate") or 0.0,
            })
    readiness = {
        "status": "needs_input" if missing else "ready",
        "reason": "deterministic_input_missing" if missing else None,
        "missingInputs": missing,
        "derivedInputs": derived_inputs,
        "executed": False,
    }
    if missing:
        return None, readiness
    drivers = (source.get("scenarioDrivers") or source.get("scenario_drivers")
               or source.get("scenarioAssumptions") or source.get("scenario_assumptions"))
    request = ScenarioSetRequest(
        scenarioSetId=f"run-{run_id}-consensus",
        scenarioSetName="Consensus source-input scenario set",
        productMix=mix,
        pfStructure=tranches,
        scenarioDrivers=ScenarioDrivers.model_validate(drivers) if drivers else None,
    )
    return request, readiness


def _status(output: dict[str, Any]) -> str:
    status = output.get("status") or output.get("executionStatus")
    if status in {"failed", "error", "cancelled", "timed_out"}:
        raise RuntimeError("Consensus domain calculation did not complete")
    return str(status or "completed")


def _complete_reconciliation(reconciliation: dict, artifacts: dict) -> dict:
    """Numeric agreement cannot erase independently discovered missing evidence."""
    result = deepcopy(reconciliation)
    checks = result.setdefault("checks", [])
    existing = {check.get("key") for check in checks}
    for check in artifacts["dataQualityReview"].get("checks", []):
        state = check.get("status")
        if state not in {"blocking", "warning", "missing_data"}:
            continue
        key = f"data_quality:{check['key']}"
        if key not in existing:
            checks.append({
                **deepcopy(check), "key": key,
                "status": "blocking" if state == "blocking" else "warning",
                "severity": "blocking" if state == "blocking" else "warning",
                "sourceArtifact": "dataQualityReview",
                "sourceStatus": state,
            })
            existing.add(key)
    for artifact, key, message in (
        ("cashFlowReadiness", "cash_flow_engine_ready", "Monthly cash-flow inputs are incomplete; no fresh calculation is available."),
        ("scenarioEngineReadiness", "scenario_engine_ready", "PF recovery scenarios need verified product mix and tranche inputs."),
    ):
        readiness = artifacts[artifact]
        if readiness.get("executed") is not True:
            checks.append({
                "key": key, "label": message, "message": message,
                "status": "warning", "severity": "warning", "ready": False,
                "sourceArtifact": artifact,
                "missingInputs": deepcopy(readiness.get("missingInputs", [])),
            })
    blocking = [check for check in checks if check.get("status") == "blocking"]
    warnings = [check for check in checks if check.get("status") == "warning"]
    result["status"] = "blocking" if blocking else "needs_review" if warnings else "clear"
    result["summary"] = {
        "checkCount": len(checks), "blockingCount": len(blocking),
        "warningCount": len(warnings),
        "clearCount": sum(check.get("status") == "clear" for check in checks),
    }
    if blocking or warnings:
        result["nextActions"] = list(dict.fromkeys([
            *(result.get("nextActions") or []),
            *(str(check.get("message")) for check in checks if check.get("status") in {"blocking", "warning"} and check.get("message")),
        ]))
    return result


def recompute_consensus_cycle(
    orchestrator: Any,
    run: dict[str, Any],
    project_data: dict[str, Any],
    options: dict[str, Any],
    *,
    evaluation_index: int,
    frozen_market: dict | None = None,
    frozen_legal_resolution: dict | None = None,
) -> dict[str, Any]:
    """Run every implemented review boundary anew against one working snapshot.

    No network connector is called here. The runtime scope is trusted server
    metadata, separate from source authority, and prevents an earlier durable
    execution from satisfying a later round by idempotency replay.
    """
    source = _source_inputs(project_data, frozen_market)
    source_hash = stable_digest(source)
    execution_scope = f"{run.get('run_id') or 'pf-review'}:consensus:{evaluation_index}"
    now = orchestrator._now()
    cycle_run = {
        key: deepcopy(value) for key, value in run.items()
        if key in {"run_id", "project_id", "tenant_id", "workflow", "user_id", "ai_connection"}
    }
    cycle_run.setdefault("run_id", "pf-review")
    cycle_run.setdefault("workflow", "pf-review")
    cycle_run.update({"artifacts": {}, "errors": [], "steps": [], "handoffs": [],
                      "agentRuntimeExecutions": [],
                      "agentProgress": orchestrator._initial_agent_progress(now)})
    artifacts = cycle_run["artifacts"]
    executions: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []
    safe_options = deepcopy(options)

    def record(agent_id: str, key: str, pod: str, inputs: Any, output: dict,
               *, kind: str = "direct", executed: bool = True) -> dict:
        if not isinstance(output, dict):
            raise TypeError(f"{agent_id} did not return a domain artifact")
        state = _status(output)
        artifacts[key] = deepcopy(output)
        executions.append({
            "agentId": agent_id, "artifactKey": key, "pod": pod,
            "status": state, "executed": executed, "executionKind": kind,
            "inputHash": stable_digest(inputs), "sourceInputHash": source_hash,
            "outputHash": stable_digest(output), "evaluationIndex": evaluation_index,
            "executionScope": execution_scope,
        })
        return output

    def call(agent_id: str, key: str, pod: str, inputs: dict,
             executor: Callable[[], dict], *, authority: bool = False) -> dict:
        output = orchestrator._run_domain_agent(
            cycle_run, project_data=deepcopy(source), options=safe_options,
            agent_id=agent_id, task_type=f"consensus_{key}", domain=pod,
            input_snapshot=deepcopy(inputs), executor=executor,
            output_schema_name=key, deterministic_authority_required=authority,
            execution_scope=execution_scope,
        )
        return record(agent_id, key, pod, inputs, output)

    def handoff(from_agent: str, to_agent: str, key: str) -> None:
        output = artifacts[key]
        payload = {"sourceArtifact": key, "sourceInputHash": source_hash,
                   "sourceOutputHash": stable_digest(output),
                   "evaluationIndex": evaluation_index, "executionScope": execution_scope}
        envelope = orchestrator._add_handoff(
            cycle_run, handoff_type="consensus_domain_result",
            from_agent=from_agent, to_agent=to_agent, payload=payload,
            payload_schema="ConsensusDomainReferenceV1",
            evidence_ids=orchestrator._collect_evidence_ids(output),
            requires_human_review=_status(output) not in {"completed", "clear", "ready"},
        )
        handoffs.append(envelope)

    land = call("LandReadinessAgent", "landAgentReview", "land_legal", source,
                lambda: orchestrator._review_land_readiness(deepcopy(source)))

    def legal_review() -> dict:
        # Preserve the opaque server-issued context object. Serializing and
        # reconstructing a plain caller mapping would lose its trust marker.
        resolution = frozen_legal_resolution or {}
        result = LegalReviewAgent().review(deepcopy(source), resolution.get("lawContext"))
        intelligence = resolution.get("legalIntelligence")
        if isinstance(intelligence, dict):
            result["legalIntelligence"] = deepcopy(intelligence)
            result["lawReferences"] = deepcopy(intelligence.get("references") or [])
            result["citationVerification"] = deepcopy(intelligence.get("citationVerification"))
        result.update({"analysisOnly": True, "reportEligible": False, "humanReviewStatus": "pending"})
        return result

    legal = call("LegalReviewAgent", "legalPermitReview", "land_legal",
                 {"project": source, "landAgentReview": land,
                  "lawContext": dict((frozen_legal_resolution or {}).get("lawContext") or {})}, legal_review)
    handoff("LandReadinessAgent", "LegalReviewAgent", "landAgentReview")

    development_basis = development_feasibility_input_basis(source)
    developer = call("DevelopmentFeasibilityAgent", "developerFeasibility", "feasibility_cashflow",
                     development_basis, lambda: DevelopmentFeasibilityAgent().analyze(deepcopy(development_basis)),
                     authority=True)
    feasibility = call("FeasibilityEngine", "feasibility", "feasibility_cashflow",
                       {"feasibility_input": source.get("feasibility_input")},
                       lambda: orchestrator._calculate_feasibility(deepcopy(source)), authority=True)
    cash_flow = developer.get("cashFlowRunResult")
    cash_flow_request = developer.get("cashFlowRunRequestDraft")
    canonical = source.get("canonicalFeasibilityStudyV2")
    canonical_cash_flow = isinstance(canonical, dict) and has_authoritative_workbook_snapshot_binding(canonical)
    if canonical_cash_flow:
        # Approved workbook inputs have their own authority. A proposal to the
        # legacy feasibility fields cannot overwrite that approved source.
        cash_flow_request = build_cash_flow_request_use_case(deepcopy(source))
        cash_flow = call("CashFlowEngineAgent", "cashFlowRunResult", "feasibility_cashflow", cash_flow_request,
                         lambda: run_cash_flow_engine(deepcopy(cash_flow_request)), authority=True)
    artifacts["cashFlowReadiness"] = {
        "status": "completed" if isinstance(cash_flow, dict) else "needs_input",
        "executed": isinstance(cash_flow, dict),
        "sourceMode": "approved_canonical" if canonical_cash_flow else "working_inputs",
        "missingInputs": [] if isinstance(cash_flow, dict) else deepcopy((developer.get("downstreamEngineReadiness") or {}).get("blockingFields") or []),
        "dataQuality": deepcopy(developer.get("dataQuality") or {}),
    }
    if isinstance(cash_flow, dict):
        artifacts["cashFlowRunRequest"] = deepcopy(cash_flow_request)
        if not canonical_cash_flow:
            record("CashFlowEngine", "cashFlowRunResult", "feasibility_cashflow",
                   cash_flow_request, cash_flow, kind="nested")
    else:
        record("CashFlowEngine", "cashFlowReadiness", "feasibility_cashflow", development_basis,
               artifacts["cashFlowReadiness"], kind="not_executed", executed=False)
    handoff("DevelopmentFeasibilityAgent", "CashFlowEngine", "developerFeasibility")

    # The existing cost/schedule boundary uses this same developer engine. Its
    # cost projection comes from this round's physical calculation; it is not a
    # second claimed contractor/VE execution or an earlier Pod artifact.
    cost = record("DevelopmentFeasibilityAgent", "costScheduleReview", "cost_schedule", development_basis, {
        "costBreakdown": deepcopy(developer.get("costs") or {}),
        "schedule": deepcopy(source.get("schedule") or source.get("project_schedule") or {}),
        "dataQuality": deepcopy(developer.get("dataQuality") or {}),
        "engineReadiness": deepcopy(developer.get("downstreamEngineReadiness") or {}),
    }, kind="projection")
    handoff("DevelopmentFeasibilityAgent", "MarketEvidenceAgent", "developerFeasibility")
    market_evidence = call("MarketEvidenceAgent", "marketEvidenceReview", "market_product",
                           {"project": source, "developerFeasibility": developer, "feasibility": feasibility},
                           lambda: MarketEvidenceAgent().review(deepcopy(source), deepcopy(developer), deepcopy(feasibility)))
    market_input = orchestrator._deep_merge(source, market_evidence.get("marketPayloadPatch") or {})
    market_input["marketEvidenceReview"] = deepcopy(market_evidence)
    marketability = call("MarketabilityAgent", "marketabilityAnalysis", "market_product", market_input,
                         lambda: MarketabilityAgent().analyze(deepcopy(market_input)))
    depth = call("MarketProductDepthAgent", "marketProductDepth", "market_product",
                 {"project": market_input, "marketabilityAnalysis": marketability},
                 lambda: MarketProductDepthAgent().analyze(deepcopy(market_input), deepcopy(marketability),
                                                         options={"podAiReviewMode": "deterministic"}))
    planning_land = source.get("land") or {}
    planning_regulation = source.get("regulation") or {}
    planning_area = _number(planning_land.get("area_sqm"))
    planning_envelope = build_preliminary_buildable_envelope(source, declared_land_area_sqm=planning_area)
    if planning_land.get("area_sqm") in (None, "", 0):
        planning_area = _number(((planning_envelope or {}).get("siteParcelSnapshot") or {}).get("totalIncludedAreaSqm"))
    planning_missing = [path for path, value in (
        ("land.area_sqm", planning_area),
        ("regulation.applied_far", _number(planning_regulation.get("applied_far"))),
        ("regulation.applied_bcr", _number(planning_regulation.get("applied_bcr"))),
    ) if value is None or value <= 0]
    if planning_missing:
        record("ProductPlanningAgent", "productPlanningReadiness", "market_product", source, {
            "status": "needs_input", "executed": False,
            "missingInputs": planning_missing, "analysisOnly": True, "reportEligible": False,
        }, kind="not_executed", executed=False)
    else:
        def product_planning() -> dict:
            result = compare_product_planning_use_case(deepcopy(market_input))
            # Geometric benchmarks and caller price assumptions remain planning
            # analysis, even when the separate legal envelope is approved.
            result.update({"analysisOnly": True, "reportEligible": False,
                           "humanReviewStatus": "pending"})
            return result

        planning = call("ProductPlanningAgent", "productPlanning", "market_product", market_input,
                        product_planning, authority=True)
        artifacts["productPlanningReadiness"] = {
            "status": "completed_with_review", "executed": True, "missingInputs": [],
            "recommendationStatus": planning.get("recommendationStatus"),
            "analysisOnly": True, "reportEligible": False,
            "limitations": ["Planning benchmarks and price/rent assumptions require review; they are not approved project inputs."],
        }
        handoff("ProductPlanningAgent", "DataQualityAgent", "productPlanning")
    handoff("MarketEvidenceAgent", "MarketabilityAgent", "marketEvidenceReview")
    handoff("MarketabilityAgent", "DataQualityAgent", "marketabilityAnalysis")

    pf = call("PFStructuringAgent", "pfStructure", "pf_credit",
              {"feasibility_input": source.get("feasibility_input"), "pf_structure": source.get("pf_structure")},
              lambda: orchestrator._calculate_pf_structure(deepcopy(source)), authority=True)
    request, scenario_readiness = _scenario_request(source, str(cycle_run["run_id"]))
    scenario_result = None
    if request is not None:
        scenario_result = call("ScenarioEngineAgent", "scenariosEvaluation", "pf_credit", request.model_dump(mode="json"),
                               lambda: ScenarioEngineAgent().run_scenario_set(request).model_dump(mode="json"), authority=True)
        scenario_readiness.update({"status": "completed", "executed": True})
        fragments = scenario_result.get("reportFragments") or {}
        for target, key in (("scenariosSummary", "waterfallSummary"), ("unsoldLtvSummary", "unsoldLtvSummary"),
                            ("trancheRecoverySummary", "trancheRecoverySummary")):
            pf[target] = fragments.get(key)
        artifacts["pfStructure"] = deepcopy(pf)
        pf_execution = next(item for item in executions if item["artifactKey"] == "pfStructure")
        pf_execution["calculationOutputHash"] = pf_execution["outputHash"]
        pf_execution["outputHash"] = stable_digest(pf)
        pf_execution["scenarioOutputHash"] = stable_digest(scenario_result)
        handoff("ScenarioEngineAgent", "RiskRegisterAgent", "scenariosEvaluation")
    else:
        record("ScenarioEngineAgent", "scenarioEngineReadiness", "pf_credit", source,
               scenario_readiness, kind="not_executed", executed=False)
    artifacts["scenarioEngineReadiness"] = scenario_readiness

    # Auxiliary engines retain their established formulas. They write only to
    # this scratch run/source; they cannot add steps to the parent's 27-step run.
    scratch = deepcopy(source)

    def auxiliary(agent_id: str, key: str, method: Callable[[], None], pod: str) -> None:
        inputs = {"project": deepcopy(scratch), "pfStructure": deepcopy(pf)}
        if key == "developabilityScore":
            inputs["marketabilityAnalysis"] = deepcopy(artifacts["marketabilityAnalysis"])
        elif key == "dealOptimization":
            inputs["artifacts"] = deepcopy(artifacts)

        def execute() -> dict:
            previous_errors = len(cycle_run["errors"])
            method()
            if len(cycle_run["errors"]) != previous_errors:
                raise RuntimeError(f"{agent_id} fresh consensus calculation failed")
            result = artifacts.get(key)
            if not isinstance(result, dict):
                raise RuntimeError(f"{agent_id} fresh consensus artifact is missing")
            return result

        call(agent_id, key, pod, inputs, execute, authority=True)

    auxiliary("ReturnMetricsEngine", "returnMetrics", lambda: orchestrator._run_return_metrics_step(cycle_run, scratch), "feasibility_cashflow")
    auxiliary("StressTestAgent", "stressTest", lambda: orchestrator._run_stress_test_step(cycle_run, scratch), "pf_credit")
    auxiliary("CreditEnhancementAgent", "creditEnhancement", lambda: orchestrator._run_credit_enhancement_step(cycle_run, scratch, pf), "pf_credit")
    if isinstance(source.get("monthly_operating_cashflow"), list) and source["monthly_operating_cashflow"]:
        auxiliary("CashSweepAgent", "cashSweep", lambda: orchestrator._run_cash_sweep_step(cycle_run, scratch), "pf_credit")
        artifacts["cashSweepReadiness"] = {"status": "completed", "executed": True, "missingInputs": []}
    else:
        orchestrator._run_cash_sweep_step(cycle_run, scratch)
        record("CashSweepAgent", "cashSweepReadiness", "pf_credit", source,
               {"status": "needs_input", "executed": False, "missingInputs": ["monthly_operating_cashflow"]},
               kind="not_executed", executed=False)
    auxiliary("DevelopabilityScoreAgent", "developabilityScore", lambda: orchestrator._run_developability_score_step(cycle_run, scratch), "public_policy")
    public = record("DevelopabilityScoreAgent", "publicPolicyReview", "public_policy",
                    {"developabilityScore": artifacts["developabilityScore"], "legalPermitReview": legal}, {
                        "artifactType": "public_policy_pod_result", "status": "completed_with_review",
                        "developabilityScore": deepcopy(artifacts["developabilityScore"]),
                        "legalReferences": deepcopy(legal.get("lawReferences") or []),
                        "policyNotes": ["Dedicated public-contribution and advanced policy simulations are not implemented; current review uses fresh developability and legal evidence."],
                    }, kind="projection")
    handoff("LegalReviewAgent", "DevelopabilityScoreAgent", "legalPermitReview")

    quality_inputs = deepcopy(artifacts)
    quality = call("DataQualityAgent", "dataQualityReview", "cross_agent_reconciliation",
                   {"project": source, "artifacts": quality_inputs},
                   lambda: DataQualityAgent().review(deepcopy(source), deepcopy(quality_inputs)))
    reconciliation = orchestrator._reconcile_agent_outputs(
        project_data=deepcopy(source), land_review=land, developer_feasibility=developer,
        feasibility=feasibility, pf_structure=pf, market_evidence=market_evidence, marketability=marketability,
    )
    reconciliation = _complete_reconciliation(reconciliation, artifacts)
    record("ReconciliationGate", "reconciliationGate", "cross_agent_reconciliation",
           {"project": source, "dataQualityReview": quality, "sourceArtifactHashes": {
               key: stable_digest(artifacts[key]) for key in ("landAgentReview", "developerFeasibility", "feasibility", "pfStructure", "marketEvidenceReview", "marketabilityAnalysis")}},
           reconciliation)
    call("RiskAssessmentEngine", "riskAssessment", "pf_credit", source,
         lambda: orchestrator._assess_risk(deepcopy(source)))
    risk_inputs = deepcopy(artifacts)
    call("RiskRegisterAgent", "riskRegister", "pf_credit", {"project": source, "artifacts": risk_inputs},
         lambda: RiskRegisterAgent().build(deepcopy(source), deepcopy(risk_inputs)))
    handoff("RiskRegisterAgent", "CreditReviewAgent", "riskRegister")
    credit_inputs = deepcopy(artifacts)
    call("CreditReviewAgent", "creditReviewDecision", "pf_credit", {"project": source, "artifacts": credit_inputs},
         lambda: CreditReviewAgent().decide(deepcopy(source), deepcopy(credit_inputs)))
    covenant_asset_type = _resolve_asset_type(source)
    covenant_policy = safe_options.get("covenantPolicy")
    covenant_policy = deepcopy(covenant_policy) if isinstance(covenant_policy, dict) else None
    covenant_inputs = {
        "analysis": developer, "asset_type": covenant_asset_type,
        "pf_structure": source.get("pf_structure") or [], "policy": covenant_policy,
        "project_name": (source.get("project") or {}).get("name"),
    }

    def covenant_eod() -> dict:
        # The public use case recalculates this very same developer analysis.
        # Consume this round's physical result at its exact build contract, so
        # covenant derivation cannot replay an old review or recalculate it twice.
        result = CovenantEodAgent().build(**deepcopy(covenant_inputs))
        result.update({"analysisOnly": True, "reportEligible": False,
                       "humanReviewStatus": "pending"})
        return result

    covenant = call("CovenantEodAgent", "covenantEod", "pf_credit", covenant_inputs, covenant_eod, authority=True)
    artifacts["covenantEodReadiness"] = {
        "status": "completed_with_review" if covenant.get("dataQualityNotes") else "completed",
        "executed": True,
        "missingInputs": [key for key, present in (covenant.get("inputsPresent") or {}).items() if not present],
        "analysisOnly": True, "reportEligible": False,
    }
    handoff("DevelopmentFeasibilityAgent", "CovenantEodAgent", "developerFeasibility")
    restructuring_inputs = {
        "project": source,
        "scenarioRequest": request.model_dump(mode="json") if request is not None else None,
        "scenariosEvaluation": scenario_result,
    }

    def restructure() -> dict:
        return orchestrator._build_restructuring_plan(
            request, ScenarioSetResult.model_validate(scenario_result) if scenario_result else None,
            deepcopy(source),
        )

    if scenario_result is not None:
        restructuring = call("RestructuringAgent", "restructuringPlan", "pf_credit", restructuring_inputs,
                             restructure, authority=True)
        handoff("ScenarioEngineAgent", "RestructuringAgent", "scenariosEvaluation")
    else:
        restructuring = record("RestructuringAgent", "restructuringPlan", "pf_credit", restructuring_inputs,
                               restructure(), kind="not_executed", executed=False)
    orchestrator._complete_step(cycle_run, "scenario_restructuring_plan", {
        "status": restructuring.get("status"),
        "recommendationCount": len(restructuring.get("recommendations") or []),
        "approvalPath": (restructuring.get("approvalPath") or {}).get("currentStatus"),
        "seniorShortfallStatus": (restructuring.get("seniorShortfallFix") or {}).get("status"),
        "unsoldLtvStatus": (restructuring.get("unsoldLtvFix") or {}).get("status"),
    }, artifact_key="restructuringPlan")
    auxiliary("DealOptimizationAgent", "dealOptimization", lambda: orchestrator._run_deal_optimization_step(cycle_run, scratch), "pf_credit")
    handoff("CreditReviewAgent", "DealOptimizationAgent", "creditReviewDecision")

    pod_artifacts = {
        "land_legal": ["landAgentReview", "legalPermitReview"],
        "market_product": ["marketEvidenceReview", "marketabilityAnalysis", "marketProductDepth", "productPlanningReadiness"] + (["productPlanning"] if "productPlanning" in artifacts else []),
        "cost_schedule": ["costScheduleReview"],
        "feasibility_cashflow": ["developerFeasibility", "feasibility", "cashFlowReadiness", "returnMetrics"],
        "pf_credit": ["pfStructure", "scenarioEngineReadiness", "stressTest", "creditEnhancement", "cashSweepReadiness", "riskRegister", "creditReviewDecision", "covenantEod", "covenantEodReadiness", "restructuringPlan", "dealOptimization"],
        "public_policy": ["developabilityScore", "publicPolicyReview"],
    }
    limitations = {
        "land_legal": list(legal.get("requiredFollowUps") or []),
        "market_product": list(marketability.get("missingRequiredData") or []) + (["Product planning requires land area, applied FAR and BCR."] if planning_missing else list(artifacts["productPlanningReadiness"]["limitations"])),
        "cost_schedule": list((cost.get("dataQuality") or {}).get("missingFields") or []) + ["Dedicated contractor-risk, VE and detailed schedule specialists are not implemented; current review covers the fresh developer cost split and supplied schedule."],
        "feasibility_cashflow": [] if artifacts["cashFlowReadiness"]["executed"] else ["Monthly cash-flow calculation requires additional inputs."],
        "pf_credit": ([] if scenario_readiness["executed"] else ["PF recovery scenario inputs are incomplete."]) + ([] if artifacts["cashSweepReadiness"]["executed"] else ["Cash sweep requires an explicit monthly operating cash-flow series."]) + [str(item.get("reason")) for item in covenant.get("dataQualityNotes") or [] if item.get("reason")],
        "public_policy": list(public["policyNotes"]),
    }
    coverage = {pod: {
        "status": "partial" if limitations[pod] else "completed",
        "artifactKeys": keys, "agentIds": list(dict.fromkeys(item["agentId"] for item in executions if item["pod"] == pod)),
        "limitations": limitations[pod], "sourceInputHash": source_hash,
    } for pod, keys in pod_artifacts.items()}
    return {
        "artifacts": artifacts, "reconciliation": reconciliation,
        "executions": executions, "handoffs": handoffs, "podCoverage": coverage,
        "agentRuntimeExecutions": cycle_run["agentRuntimeExecutions"],
        "stepOutputs": {step["key"]: deepcopy(step["output"]) for step in cycle_run["steps"]},
        "inputHash": source_hash, "outputHash": stable_digest(artifacts),
        "evaluationIndex": evaluation_index, "executionScope": execution_scope,
    }
