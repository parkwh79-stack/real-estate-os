from datetime import datetime, timezone
from copy import deepcopy
from typing import Any, Dict, List
from uuid import uuid4

from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.ai_connection import resolve_ai_context, safe_ai_context
from agents.ai_router import describe_selection, select_ai_context
from agents.consensus_engine import ConsensusEngine, build_consensus_coordinator
from agents.credit_document_agent import CreditDocumentAgent
from agents.credit_review_agent import CreditReviewAgent
from agents.data_quality_agent import DataQualityAgent
from agents.deal_optimization_agent import DealOptimizationAgent
from agents.developability_score_agent import DevelopabilityScoreAgent
from schemas.credit_policy import resolve_credit_policy
from agents.evidence_graph_agent import EvidenceGraphAgent
from agents.market_evidence_agent import MarketEvidenceAgent
from agents.marketability_agent import MarketabilityAgent
from integrations.market_data import MolitRtmsConnector
from use_cases.market_comparables import (
    fetch_project_market_comparables,
    inject_market_comparables,
    live_market_comparables_enabled,
    recent_deal_yms,
)
from use_cases.glossary import capture_unmapped_term_surface_candidates_use_case
from use_cases.approval_readiness_gate import build_deal_state_approval_readiness
from use_cases.legal_review import review_legal_and_permits_use_case
from agents.report_agent import ReportAgent
from agents.restructuring_agent import RestructuringAgent
from agents.risk_register_agent import RiskRegisterAgent
from core.calculations import (
    calculate_feasibility_metrics,
    calculate_pf_metrics,
    calculate_return_metrics,
    normalize_tranche_priority,
)
from agents.stress_test_agent import StressTestAgent
from agents.credit_enhancement_agent import CreditEnhancementAgent
from agents.cash_sweep_agent import CashSweepAgent
from schemas.stress_test import StressTestRequest, StressTranche
from schemas.credit_enhancement import CreditEnhancementRequest, CreditEnhancementInput
from schemas.cash_sweep import CashSweepRequest, CashSweepTranche
from core.risk_assessment import build_basic_risk_matrix
from core.secret_redaction import collect_secret_values, redact_secret_text, redact_sensitive_value
from core.agent_execution import require_successful_runtime
from db.repositories.request_project_repository import current_project_scope
from schemas.orchestration import AgentHandoffEnvelope, AgentProgress, PF_REVIEW_STEPS, PF_REVIEW_WORKFLOW
from agent_platform.domain_agent_runtime import run_domain_agent_callable_with_contract
from use_cases.assumption_proposals import build_consensus_assumption_proposals


class OrchestrationAgent:
    def run_pf_review(
        self,
        project_id: str,
        project_data: Dict[str, Any],
        options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        project_data = deepcopy(project_data)
        options, tenant_id = self._runtime_context(project_data, options or {})
        ai_connection = resolve_ai_context(
            options.get("ai_context"),
            user_id=options.get("user_id") or options.get("requested_by"),
            host=options.get("host") or "api",
        )
        now = self._now()
        run = {
            "run_id": str(uuid4()),
            "project_id": project_id,
            "tenant_id": tenant_id,
            "workflow": PF_REVIEW_WORKFLOW,
            "status": "running",
            "executionStatus": "running",
            "approvalStatus": "not_requested",
            "approvalReadinessStatus": "not_started",
            "reportStatus": "not_started",
            "started_at": now,
            "completed_at": None,
            "user_id": ai_connection["user_id"],
            "ai_connection": safe_ai_context(ai_connection),
            "options": self._sanitize_options(options),
            "steps": [],
            "agentProgress": self._initial_agent_progress(now),
            "handoffs": [],
            "artifacts": {},
            "errors": [],
            "approval": None,
            "agentRuntimeExecutions": [],
        }

        try:
            self._complete_step(run, "project_load", {
                "project": project_data.get("project", {}),
                "source": "PROJECTS_DB",
            }, artifact_key="project")

            self._run_term_annotation_step(run, project_data)

            land_review = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="LandReadinessAgent",
                task_type="land_readiness_review",
                domain="land_legal",
                input_snapshot={
                    "land": project_data.get("land"),
                    "landLedger": project_data.get("landLedger"),
                    "landEvidence": project_data.get("landEvidence"),
                },
                executor=lambda: self._review_land_readiness(project_data),
                output_schema_name="LandReadinessReview",
            )
            run["artifacts"]["landAgentReview"] = land_review
            self._complete_step(run, "land_agent_review", {
                "status": land_review["status"],
                "summary": land_review["summary"],
                "issueCount": len(land_review["blockingIssues"]) + len(land_review["dataLimitations"]),
                "handoffReadiness": land_review["handoffReadiness"],
            }, artifact_key="landAgentReview")
            self._add_handoff(
                run,
                handoff_type="land_to_feasibility_legal_pf",
                from_agent="LandAgent",
                to_agent="DevelopmentFeasibilityAgent",
                payload_schema="LandReadinessPatchV1",
                payload=self._land_handoff_payload(land_review),
                evidence_ids=self._collect_evidence_ids(land_review),
                confidence=land_review.get("confidence"),
                blocking_issues=land_review.get("blockingIssues", []),
                data_limitations=land_review.get("dataLimitations", []),
                requires_human_review=bool(land_review.get("requiresHumanReview")),
                assumptions=[
                    "Land Agent output is a readiness patch and does not overwrite approved site, cost, or legal assumptions.",
                    "A land ledger import remains preliminary until PNU, official price, cadastral, and registry evidence are attached.",
                ],
                progress_impact={
                    "unblocks": [
                        "development_feasibility_analysis",
                        "legal_permit_review",
                        "risk_assessment",
                    ],
                    "downstreamAgents": [
                        "DevelopmentFeasibilityAgent",
                        "LegalReviewAgent",
                        "PFStructuringCalculator",
                        "CreditReviewAgent",
                    ],
                },
            )

            developer_feasibility = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="DevelopmentFeasibilityAgent",
                task_type="development_feasibility_analysis",
                domain="feasibility_cashflow",
                input_snapshot={
                    "feasibilityInput": project_data.get("feasibility_input"),
                    "feasibilityStudyV2": project_data.get("feasibilityStudyV2"),
                    "productMix": project_data.get("productMix") or project_data.get("product_mix"),
                    "schedule": project_data.get("schedule"),
                },
                executor=lambda: DevelopmentFeasibilityAgent().analyze(project_data),
                output_schema_name="DevelopmentFeasibilityResult",
                deterministic_authority_required=True,
            )
            run["artifacts"]["developerFeasibility"] = developer_feasibility
            self._complete_step(run, "development_feasibility_analysis", {
                "summary": developer_feasibility["summary"],
                "profitMargin": developer_feasibility["developerReturns"]["profitMargin"],
                "profitOnCost": developer_feasibility["developerReturns"]["profitOnCost"],
                "pfStatus": developer_feasibility["pfReadiness"]["status"],
                "confidence": developer_feasibility["dataQuality"]["confidence"],
            }, artifact_key="developerFeasibility")
            self._add_handoff(
                run,
                handoff_type="development_feasibility_to_cash_flow",
                from_agent="DevelopmentFeasibilityAgent",
                to_agent="CashFlowEngine",
                payload_schema="CashFlowRunRequestV1",
                payload={
                    "cashFlowRunRequestDraft": developer_feasibility.get("cashFlowRunRequestDraft"),
                    "cashFlowReadiness": developer_feasibility.get("downstreamEngineReadiness", {}),
                    "pfReadiness": developer_feasibility.get("pfReadiness", {}),
                },
                evidence_ids=self._collect_evidence_ids(developer_feasibility),
                confidence=developer_feasibility.get("dataQuality", {}).get("confidence"),
                blocking_issues=developer_feasibility.get("dataQuality", {}).get("warnings", []),
                requires_human_review=bool(developer_feasibility.get("dataQuality", {}).get("warnings")),
                progress_impact={
                    "unblocks": ["cash_flow_engine"],
                    "downstreamAgents": ["CashFlowEngine", "CalculationScenarioEngine"],
                },
            )

            feasibility = self._calculate_feasibility(project_data)
            run["artifacts"]["feasibility"] = feasibility
            self._complete_step(run, "feasibility_calculation", feasibility, artifact_key="feasibility")

            pf_structure = self._calculate_pf_structure(project_data)

            # Execute Scenario Engine during orchestration run to dynamically construct sensitivity analyses
            scenario_req = None
            scenario_run_result = None
            try:
                from schemas.scenario import ScenarioDrivers, ScenarioSetRequest
                from agents.scenario_engine_agent import ScenarioEngineAgent

                raw_mix = project_data.get("product_mix") or project_data.get("productMix") or []
                product_mix = []
                for item in raw_mix:
                    if isinstance(item, dict):
                        product_mix.append({
                            "type": item.get("type") or item.get("name") or "A",
                            "units": int(item.get("units") or item.get("unit_count") or 0),
                            "avgUnitPrice": float(item.get("avgUnitPrice") or item.get("avg_unit_price") or 0.0),
                            "totalRevenue": float(item.get("totalRevenue") or item.get("total_revenue") or 0.0)
                        })
                tranches = project_data.get("pf_structure", [])
                pf_structure_list = []
                for idx, item in enumerate(tranches):
                    if isinstance(item, dict):
                        pf_structure_list.append({
                            "tranche": item.get("tranche") or item.get("name") or f"Tr.{idx+1}",
                            "amount": float(item.get("amount") or 0.0),
                            "priority": normalize_tranche_priority(item.get("priority"), idx + 1),
                            "interestRate": float(item.get("interest_rate") or item.get("interestRate") or 0.0),
                            "feeRate": float(item.get("fee_rate") or item.get("feeRate") or 0.0)
                        })
                raw_scenario_drivers = (
                    project_data.get("scenarioDrivers")
                    or project_data.get("scenario_drivers")
                    or project_data.get("scenarioAssumptions")
                    or project_data.get("scenario_assumptions")
                    or {}
                )
                scenario_drivers = None
                if isinstance(raw_scenario_drivers, dict) and raw_scenario_drivers:
                    scenario_drivers = ScenarioDrivers.model_validate(raw_scenario_drivers)

                missing_scenario_inputs = []
                if not product_mix:
                    missing_scenario_inputs.append("product_mix")
                if not pf_structure_list:
                    missing_scenario_inputs.append("pf_structure")
                if missing_scenario_inputs:
                    run["artifacts"]["scenarioEngineReadiness"] = {
                        "status": "not_ready",
                        "reason": "deterministic_input_missing",
                        "missingInputs": missing_scenario_inputs,
                        "message": "Scenario Engine was not executed because verified product mix or PF structure inputs are missing.",
                    }
                else:
                    scenario_req = ScenarioSetRequest(
                        scenarioSetId=f"run-{run['run_id']}",
                        scenarioSetName="Orchestration Run Scenario Set",
                        productMix=product_mix,
                        pfStructure=pf_structure_list,
                        scenarioDrivers=scenario_drivers,
                    )
                    scenario_run_result = ScenarioEngineAgent().run_scenario_set(scenario_req)
                    run["artifacts"]["scenariosEvaluation"] = scenario_run_result.model_dump(by_alias=True)

                    # Include scenario fragments directly in the pf_structure artifact.
                    pf_structure["scenariosSummary"] = scenario_run_result.reportFragments.waterfallSummary
                    pf_structure["unsoldLtvSummary"] = scenario_run_result.reportFragments.unsoldLtvSummary
                    pf_structure["trancheRecoverySummary"] = scenario_run_result.reportFragments.trancheRecoverySummary
            except Exception as exc:
                run["errors"].append({"message": f"Scenario calculation failed: {str(exc)}", "type": exc.__class__.__name__})

            run["artifacts"]["pfStructure"] = pf_structure
            self._complete_step(run, "pf_structure_calculation", pf_structure, artifact_key="pfStructure")

            # --- Deck headline engines (surfaced into the run and Phase 11 report) ---
            self._run_return_metrics_step(run, project_data)
            self._run_stress_test_step(run, project_data)
            self._run_credit_enhancement_step(run, project_data, pf_structure)
            self._run_cash_sweep_step(run, project_data)

            market_evidence = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="MarketEvidenceAgent",
                task_type="market_evidence_reconciliation",
                domain="market_product",
                input_snapshot={
                    "market": project_data.get("market"),
                    "marketEvidence": project_data.get("marketEvidence") or project_data.get("market_evidence"),
                    "developerFeasibility": developer_feasibility,
                    "feasibility": feasibility,
                },
                executor=lambda: MarketEvidenceAgent().review(
                    project_data,
                    developer_feasibility,
                    feasibility,
                ),
                output_schema_name="MarketEvidenceReview",
            )
            run["artifacts"]["marketEvidenceReview"] = market_evidence
            self._complete_step(run, "market_evidence_reconciliation", {
                "status": market_evidence["status"],
                "evidenceSummary": market_evidence["evidenceSummary"],
                "issueCount": len(market_evidence["issues"]),
                "agentMessages": market_evidence["agentMessages"],
            }, artifact_key="marketEvidenceReview")
            self._add_handoff(
                run,
                handoff_type="market_evidence_to_marketability",
                from_agent="MarketEvidenceAgent",
                to_agent="MarketabilityAgent",
                payload_schema="MarketEvidencePatchV1",
                payload={
                    "marketPayloadPatch": market_evidence.get("marketPayloadPatch", {}),
                    "evidenceSummary": market_evidence.get("evidenceSummary", {}),
                    "missingRequiredEvidence": market_evidence.get("missingRequiredEvidence", []),
                    "agentMessages": market_evidence.get("agentMessages", {}),
                },
                evidence_ids=self._collect_evidence_ids(market_evidence),
                confidence=market_evidence.get("evidenceSummary", {}).get("confidence"),
                blocking_issues=market_evidence.get("issues", []),
                data_limitations=market_evidence.get("missingRequiredEvidence", []),
                requires_human_review=bool(market_evidence.get("issues")),
                progress_impact={
                    "unblocks": ["marketability_analysis"],
                    "downstreamAgents": ["MarketabilityAgent"],
                },
            )

            marketability_input = self._deep_merge(
                project_data,
                market_evidence.get("marketPayloadPatch", {}),
            )
            marketability_input["marketEvidenceReview"] = market_evidence
            self._maybe_inject_live_market_comparables(run, project_data, marketability_input)
            marketability = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="MarketabilityAgent",
                task_type="marketability_analysis",
                domain="market_product",
                input_snapshot={
                    "marketEvidenceReview": market_evidence,
                    "market": marketability_input.get("market"),
                    "productMix": marketability_input.get("productMix") or marketability_input.get("product_mix"),
                },
                executor=lambda: MarketabilityAgent().analyze(marketability_input),
                output_schema_name="MarketabilityAnalysis",
            )
            run["artifacts"]["marketabilityAnalysis"] = marketability
            self._complete_step(run, "marketability_analysis", marketability, artifact_key="marketabilityAnalysis")
            self._add_handoff(
                run,
                handoff_type="marketability_to_feasibility",
                from_agent="MarketabilityAgent",
                to_agent="DevelopmentFeasibilityAgent",
                payload_schema="FeasibilityStudyV2Patch",
                payload=self._marketability_feasibility_patch(marketability),
                evidence_ids=self._collect_evidence_ids(marketability),
                confidence=self._marketability_confidence(marketability),
                blocking_issues=self._marketability_blockers(marketability),
                data_limitations=marketability.get("missingRequiredData", []),
                requires_human_review=bool(self._marketability_blockers(marketability)),
                assumptions=[
                    "Marketability output is a proposed feasibility patch and does not overwrite approved business-plan assumptions.",
                ],
                progress_impact={
                    "requiresReconciliation": ["development_feasibility_analysis", "cash_flow_engine"],
                    "downstreamAgents": ["DevelopmentFeasibilityAgent", "CashFlowEngine", "CreditReviewAgent"],
                },
            )

            data_quality_inputs = {
                    "landAgentReview": land_review,
                    "developerFeasibility": developer_feasibility,
                    "feasibility": feasibility,
                    "pfStructure": pf_structure,
                    "marketEvidenceReview": market_evidence,
                    "marketabilityAnalysis": marketability,
                }
            data_quality = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="DataQualityAgent",
                task_type="cross_document_data_quality_review",
                domain="cross_agent_reconciliation",
                input_snapshot=data_quality_inputs,
                executor=lambda: DataQualityAgent().review(project_data, data_quality_inputs),
                output_schema_name="DataQualityReview",
            )
            run["artifacts"]["dataQualityReview"] = data_quality
            self._complete_step(run, "data_quality_review", {
                "status": data_quality["status"],
                "summary": data_quality["summary"],
                "nextActions": data_quality["nextActions"],
            }, artifact_key="dataQualityReview")

            reconciliation = self._reconcile_agent_outputs(
                project_data=project_data,
                land_review=land_review,
                developer_feasibility=developer_feasibility,
                feasibility=feasibility,
                pf_structure=pf_structure,
                market_evidence=market_evidence,
                marketability=marketability,
            )
            run["artifacts"]["reconciliationGate"] = reconciliation
            self._complete_step(run, "cross_agent_reconciliation", {
                "status": reconciliation["status"],
                "summary": reconciliation["summary"],
                "nextActions": reconciliation["nextActions"],
            }, artifact_key="reconciliationGate")

            # Iterative cross-agent consensus: agents re-analyse (patch -> recompute
            # -> re-reconcile) until their cross-checked numbers agree. Calculations
            # stay deterministic; a coordinator only sequences/judges the loop.
            def _reconcile_with(working_pd: Dict[str, Any], _art: Dict[str, Any]) -> Dict[str, Any]:
                return self._reconcile_agent_outputs(
                    project_data=working_pd,
                    land_review=land_review,
                    developer_feasibility=developer_feasibility,
                    feasibility=feasibility,
                    pf_structure=self._calculate_pf_structure(working_pd),
                    market_evidence=market_evidence,
                    marketability=marketability,
                )

            consensus_ai = select_ai_context(tier="coordination")
            consensus_coordinator = build_consensus_coordinator(consensus_ai)
            consensus = ConsensusEngine().reach_consensus(
                project_data, run["artifacts"], _reconcile_with, coordinator=consensus_coordinator
            )
            consensus["aiSelection"] = describe_selection(consensus_ai)
            consensus_artifact = {k: v for k, v in consensus.items() if k != "agreedProjectData"}
            proposals = build_consensus_assumption_proposals(
                project_id,
                run["run_id"],
                project_data,
                consensus,
            )
            consensus_artifact["assumptionProposals"] = proposals
            consensus_artifact["proposalStatus"] = "pending_approval" if proposals else "not_required"
            consensus_artifact["calculationPolicy"] = (
                "Original project assumptions remain authoritative until proposals are approved into a new project version."
            )
            run["artifacts"]["assumptionProposals"] = {
                "schemaVersion": "realytics-assumption-proposals-v1",
                "projectId": project_id,
                "runId": run["run_id"],
                "status": consensus_artifact["proposalStatus"],
                "proposals": proposals,
            }
            run["artifacts"]["consensusResolution"] = consensus_artifact
            self._complete_step(run, "consensus_resolution", {
                "status": consensus_artifact["status"],
                "rounds": consensus_artifact["rounds"],
                "resolvedCount": consensus_artifact["summary"]["resolvedCount"],
                "unresolvedCount": consensus_artifact["summary"]["unresolvedCount"],
                "reconciliationStatus": reconciliation["status"],
                "pendingProposalCount": len(proposals),
            }, artifact_key="consensusResolution")

            supplied_law_context = options.get("law_context")
            candidate_law_context = (
                supplied_law_context
                if isinstance(supplied_law_context, dict)
                else None
            )
            legal_review = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="LegalReviewAgent",
                task_type="legal_permit_review",
                domain="land_legal",
                input_snapshot={
                    "land": project_data.get("land"),
                    "landLedger": project_data.get("landLedger"),
                    "candidateLawContextSupplied": bool(
                        candidate_law_context
                    ),
                    "sourceDocuments": project_data.get("source_documents"),
                },
                executor=lambda: review_legal_and_permits_use_case(
                    project_data,
                    {
                        "lawContext": candidate_law_context,
                    },
                ),
                output_schema_name="LegalReviewResult",
            )
            run["artifacts"]["legalPermitReview"] = legal_review
            self._complete_step(run, "legal_permit_review", {
                "status": legal_review["status"],
                "mcp": legal_review["mcp"],
                "requiredFollowUps": legal_review["requiredFollowUps"],
            }, artifact_key="legalPermitReview")
            self._add_handoff(
                run,
                handoff_type="legal_to_credit_review",
                from_agent="LegalReviewAgent",
                to_agent="CreditReviewAgent",
                payload_schema="LegalReadinessPatchV1",
                payload={
                    "status": legal_review.get("status"),
                    "requiredFollowUps": legal_review.get("requiredFollowUps", []),
                    "mcp": legal_review.get("mcp", {}),
                },
                evidence_ids=self._collect_evidence_ids(legal_review),
                confidence=legal_review.get("confidence"),
                blocking_issues=legal_review.get("requiredFollowUps", [])
                if legal_review.get("status") == "blocking_issue"
                else [],
                requires_human_review=legal_review.get("status") != "clear",
                progress_impact={
                    "unblocks": ["risk_assessment", "approval_readiness_gate"],
                    "downstreamAgents": ["RiskAssessmentEngine", "CreditReviewAgent"],
                },
            )

            risk = self._assess_risk(project_data)
            run["artifacts"]["riskAssessment"] = risk
            self._complete_step(run, "risk_assessment", risk, artifact_key="riskAssessment")

            risk_register = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="RiskRegisterAgent",
                task_type="risk_register_aggregation",
                domain="pf_credit",
                input_snapshot={
                    "artifactKeys": sorted(run["artifacts"]),
                    "legalReview": legal_review,
                    "marketability": marketability,
                    "pfStructure": pf_structure,
                },
                executor=lambda: RiskRegisterAgent().build(project_data, run["artifacts"]),
                output_schema_name="RiskRegister",
            )
            run["artifacts"]["riskRegister"] = risk_register
            self._complete_step(run, "risk_register_generation", {
                "status": risk_register["status"],
                "summary": risk_register["summary"],
                "approvalConditionCount": len(risk_register["approvalConditions"]),
            }, artifact_key="riskRegister")
            self._add_handoff(
                run,
                handoff_type="risk_register_to_credit_review",
                from_agent="RiskRegisterAgent",
                to_agent="CreditReviewAgent",
                payload_schema="RiskRegisterV1",
                payload={
                    "status": risk_register.get("status"),
                    "summary": risk_register.get("summary", {}),
                    "topRisks": risk_register.get("topRisks", []),
                    "approvalConditions": risk_register.get("approvalConditions", []),
                    "creditReviewInputs": risk_register.get("creditReviewInputs", {}),
                },
                evidence_ids=self._collect_evidence_ids(risk_register),
                confidence=0.9,
                blocking_issues=[
                    risk["description"]
                    for risk in risk_register.get("risks", [])
                    if risk.get("severity") == "critical"
                ],
                data_limitations=risk_register.get("dataLimitations", []),
                requires_human_review=bool(risk_register.get("creditReviewInputs", {}).get("humanReviewRequiredCount")),
                progress_impact={
                    "unblocks": ["phase11_report_generation"],
                    "downstreamAgents": ["CreditReviewAgent", "ReportAgent"],
                },
            )

            credit_review = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="CreditReviewAgent",
                task_type="credit_decision_review",
                domain="pf_credit",
                input_snapshot={
                    "riskRegister": risk_register,
                    "pfStructure": pf_structure,
                    "scenariosEvaluation": run["artifacts"].get("scenariosEvaluation"),
                },
                executor=lambda: CreditReviewAgent().decide(project_data, run["artifacts"]),
                output_schema_name="CreditReviewDecision",
            )
            run["artifacts"]["creditReviewDecision"] = credit_review
            self._complete_step(run, "credit_review_decision", {
                "decision": credit_review["decision"],
                "decisionLabel": credit_review["decisionLabel"],
                "status": credit_review["status"],
                "approvalConditionCount": len(credit_review["approvalConditions"]),
                "requiredFollowUpCount": len(credit_review["requiredFollowUps"]),
            }, artifact_key="creditReviewDecision")
            self._add_handoff(
                run,
                handoff_type="credit_review_to_report",
                from_agent="CreditReviewAgent",
                to_agent="ReportAgent",
                payload_schema="CreditReviewDecisionV1",
                payload={
                    "decision": credit_review.get("decision"),
                    "decisionLabel": credit_review.get("decisionLabel"),
                    "summary": credit_review.get("summary", {}),
                    "decisionReasons": credit_review.get("decisionReasons", []),
                    "keyRisks": credit_review.get("keyRisks", []),
                    "approvalConditions": credit_review.get("approvalConditions", []),
                    "reportParagraphs": credit_review.get("reportParagraphs", {}),
                },
                evidence_ids=self._collect_evidence_ids(credit_review),
                confidence=credit_review.get("confidence"),
                blocking_issues=credit_review.get("requiredFollowUps", [])
                if credit_review.get("decision") in {"rework_required", "reject_recommended"}
                else [],
                data_limitations=credit_review.get("dataLimitations", []),
                requires_human_review=credit_review.get("humanReview", {}).get("required", False),
                progress_impact={
                    "unblocks": ["phase11_report_generation"],
                    "downstreamAgents": ["ReportAgent", "OrchestrationQualityGate"],
                },
            )

            restructuring_plan = self._build_restructuring_plan(scenario_req, scenario_run_result, project_data)
            run["artifacts"]["restructuringPlan"] = restructuring_plan
            restructuring_blockers = self._restructuring_blockers(restructuring_plan)
            self._complete_step(run, "scenario_restructuring_plan", {
                "status": restructuring_plan.get("status"),
                "recommendationCount": len(restructuring_plan.get("recommendations", [])),
                "approvalPath": restructuring_plan.get("approvalPath", {}).get("currentStatus"),
                "seniorShortfallStatus": restructuring_plan.get("seniorShortfallFix", {}).get("status"),
                "unsoldLtvStatus": restructuring_plan.get("unsoldLtvFix", {}).get("status"),
            }, artifact_key="restructuringPlan")
            self._add_handoff(
                run,
                handoff_type="restructuring_to_credit_review",
                from_agent="RestructuringAgent",
                to_agent="CreditReviewAgent",
                payload_schema="PFRestructuringPlanV1",
                payload={
                    "approvalPath": restructuring_plan.get("approvalPath", {}),
                    "seniorShortfallFix": restructuring_plan.get("seniorShortfallFix", {}),
                    "unsoldLtvFix": restructuring_plan.get("unsoldLtvFix", {}),
                    "adoptedCures": restructuring_plan.get("adoptedCures", {}),
                    "recommendations": restructuring_plan.get("recommendations", []),
                },
                evidence_ids=self._collect_evidence_ids(restructuring_plan),
                confidence=restructuring_plan.get("sourceMetadata", {}).get("confidence"),
                blocking_issues=restructuring_blockers,
                data_limitations=restructuring_plan.get("dataLimitations", []),
                requires_human_review=bool(restructuring_blockers),
                assumptions=[
                    "Restructuring recommendations are scenario outputs and do not waive credit risk.",
                    "Any senior reduction, DSRA, equity, collateral, forward-sale, or refinancing cure must be source-backed and rerun before final approval.",
                ],
                progress_impact={
                    "requiresReconciliation": ["credit_review_decision", "approval_readiness_gate"],
                    "downstreamAgents": ["CreditReviewAgent", "ApprovalReadinessGate"],
                },
            )

            self._run_deal_optimization_step(run, project_data)

            self._run_developability_score_step(run, project_data)

            def generate_report_artifact() -> Dict[str, Any]:
                report_agent = ReportAgent()
                return {
                    "report": report_agent.generate_structured_report(project_data),
                    "report_markdown": report_agent.generate_report(project_data),
                }

            report_artifact = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="ReportAgent",
                task_type="phase11_report_generation",
                domain="report_studio",
                input_snapshot={
                    "creditReviewDecision": credit_review,
                    "riskRegister": risk_register,
                    "artifactKeys": sorted(run["artifacts"]),
                },
                executor=generate_report_artifact,
                output_schema_name="Phase11ReportArtifact",
            )
            structured_report = report_artifact["report"]
            report_markdown = report_artifact["report_markdown"]
            run["artifacts"]["phase11Report"] = report_artifact
            self._complete_step(run, "phase11_report_generation", {
                "template": structured_report["template"],
                "sectionCount": structured_report["qualityChecks"]["actualSectionCount"],
            }, artifact_key="phase11Report")

            evidence_graph = self._run_domain_agent(
                run,
                project_data=project_data,
                options=options,
                agent_id="EvidenceGraphAgent",
                task_type="evidence_graph_generation",
                domain="evidence_governance",
                input_snapshot={
                    "artifactKeys": sorted(run["artifacts"]),
                    "handoffIds": [item.get("handoffId") for item in run.get("handoffs") or []],
                },
                executor=lambda: EvidenceGraphAgent().build(project_data, run),
                output_schema_name="EvidenceGraph",
            )
            run["artifacts"]["evidenceGraph"] = evidence_graph
            glossary_candidate_capture = capture_unmapped_term_surface_candidates_use_case(
                [
                    node.get("metadata") or {}
                    for node in evidence_graph.get("nodes", [])
                    if node.get("type") == "term_surface"
                ],
                project_id=project_id,
                run_id=run["run_id"],
                source="evidence_graph.term_surface",
                tenant_id=str((run.get("ai_connection") or {}).get("tenant_id") or "default"),
                created_by=str(run.get("user_id") or "system_learning_loop"),
            )
            run["artifacts"]["glossaryCandidateCapture"] = glossary_candidate_capture
            self._complete_step(run, "evidence_graph_generation", {
                "schemaVersion": evidence_graph["schemaVersion"],
                "summary": evidence_graph["summary"],
                "auditWarnings": evidence_graph["auditWarnings"],
                "glossaryCandidateCapture": {
                    "unmappedSurfaceCount": glossary_candidate_capture.get("unmappedSurfaceCount"),
                    "capturedCount": glossary_candidate_capture.get("capturedCount"),
                    "matchedCount": glossary_candidate_capture.get("matchedCount"),
                },
            }, artifact_key="evidenceGraph")

            structure_results = None
            visual_results = None
            try:
                from agents.report_export_agent import ReportExportAgent
                from agents.visual_qa_agent import VisualQAAgent

                export_agent = ReportExportAgent()
                report_record = {
                    "report_id": f"report-{run['run_id']}",
                    "version": 1,
                    "title": "AI PF Review Report",
                    "report": structured_report,
                    "report_markdown": report_markdown,
                }
                pdf_bytes = export_agent._render_pdf(report_record)

                qa_agent = VisualQAAgent()
                structure_results = qa_agent.analyze_pdf_structure(pdf_bytes)
                visual_results = qa_agent.run_visual_regression(pdf_bytes, report_id=f"report-{run['run_id']}",
                    baseline_id=qa_agent.baseline_key(str(run.get("tenant_id") or "legacy"), str(project_id), str(structured_report["template"])))
            except Exception as qa_exc:
                run["errors"].append({
                    "message": f"Visual QA execution failed: {str(qa_exc)}",
                    "type": qa_exc.__class__.__name__
                })

            quality = self._quality_check(run, structure_results, visual_results)
            run["artifacts"]["qualityCheck"] = quality
            self._complete_step(run, "quality_check", quality, artifact_key="qualityCheck")

            approval_readiness = self._approval_readiness_gate(run)
            run["artifacts"]["approvalReadinessGate"] = approval_readiness
            readiness_pass = approval_readiness.get("status") in {
                "ready",
                "pass",
                "ready_for_approval",
            }
            run["approvalReadinessStatus"] = (
                "ready_for_approval" if readiness_pass else "not_ready_for_approval"
            )
            # Readiness is not approval.  Keep approvalStatus in its canonical
            # lifecycle and let the separate, authenticated maker-checker
            # action move it to approved/rejected.
            run["approvalStatus"] = "pending" if readiness_pass else "not_requested"
            self._complete_step(run, "approval_readiness_gate", approval_readiness, artifact_key="approvalReadinessGate")

            # Credit documents synthesise every prior artifact (incl. the approval
            # readiness gate), so they are generated last.
            self._run_credit_document_step(run, project_data)

            self._finalize_pf_execution(run, quality)
        except Exception as exc:
            safe_error = redact_secret_text(
                exc,
                secrets=collect_secret_values(ai_connection),
            )
            run["executionStatus"] = "failed"
            run["reportStatus"] = "failed" if run.get("reportStatus") == "not_started" else run.get("reportStatus")
            run["status"] = run["executionStatus"]
            run["errors"].append({"message": safe_error, "type": exc.__class__.__name__})
            self._mark_failed_progress(run, safe_error)

        run["completed_at"] = self._now()
        return redact_sensitive_value(run, secrets=collect_secret_values((ai_connection, options)))

    # ------------------------------------------------------------------
    # Intent-driven workflow executor
    # ------------------------------------------------------------------
    def run_workflow(
        self,
        project_id: str,
        project_data: Dict[str, Any],
        workflow: str,
        options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Run only the agents required by a selected workflow.

        Lets the Orchestration Planner route a user-chosen intent (land review,
        feasibility, marketability, returns/stress, PF-structure) to the right
        agents instead of always running the full 18-step PF review. The full
        review still delegates to ``run_pf_review``.
        """
        project_data = deepcopy(project_data)
        options, tenant_id = self._runtime_context(project_data, options or {})
        if workflow == "full_pf_review":
            return self.run_pf_review(project_id, project_data, options)

        run: Dict[str, Any] = {
            "run_id": str(uuid4()),
            "project_id": project_id,
            "workflow": workflow,
            "tenant_id": tenant_id,
            "status": "running",
            "started_at": self._now(),
            "completed_at": None,
            "steps": [],
            "artifacts": {},
            "nextActions": [],
            "errors": [],
            "agentRuntimeExecutions": [],
        }

        def step(step_id: str, status: str, detail: Dict[str, Any], artifact_key: str | None = None, artifact: Any = None) -> None:
            run["steps"].append({"step": step_id, "status": status, "detail": detail, "at": self._now()})
            if artifact_key and artifact is not None:
                run["artifacts"][artifact_key] = artifact

        handlers = {
            "land_review": self._wf_land_review,
            "feasibility": self._wf_feasibility,
            "marketability": self._wf_marketability,
            "returns_stress": self._wf_returns_stress,
            "pf_structure_review": self._wf_pf_structure_review,
        }
        handler = handlers.get(workflow)
        if handler is None:
            run["status"] = "error"
            run["errors"].append({"message": f"Unknown workflow: {workflow}", "type": "ValueError"})
            run["completed_at"] = self._now()
            return run

        try:
            handler(run, project_data, step, options)
            run["status"] = "completed" if not run["errors"] else "completed_with_warnings"
        except Exception as exc:  # pragma: no cover - defensive
            run["status"] = "error"
            run["errors"].append({"message": str(exc), "type": exc.__class__.__name__})
        run["completed_at"] = self._now()
        return redact_sensitive_value(run, secrets=collect_secret_values(options))

    @staticmethod
    def _runtime_context(project_data: Dict[str, Any], options: Dict[str, Any]):
        options = dict(options)
        scope = current_project_scope()
        metadata = project_data.get("_meta") or {}
        declared = [str(value).strip() for value in (
            options.get("tenantId"), options.get("tenant_id"),
            metadata.get("tenantId"), metadata.get("tenant_id"),
        ) if value is not None and str(value).strip()]
        tenant = str(scope.tenant_id) if scope is not None else (declared[0] if declared else None)
        if tenant is not None and any(value != tenant for value in declared):
            raise ValueError("Orchestration tenant does not match its runtime scope")
        if tenant is not None:
            options["tenantId"] = tenant
        return options, tenant

    def _finalize_pf_execution(self, run: Dict[str, Any], quality: Dict[str, Any]) -> None:
        failed = [step for step in run.get("steps", []) if step.get("status") == "failed"]
        if failed:
            gate = run["artifacts"].setdefault("approvalReadinessGate", {})
            check = self._approval_readiness_check(
                "AGENT_EXECUTION_SUCCEEDED", False,
                "All required Agent executions must succeed before approval.", "blocking",
            )
            for key in ("gates", "blockers"):
                gate[key] = [item for item in gate.get(key, []) if item.get("code") != check["code"]] + [check]
            gate.update({"pass": False, "status": "not_ready_for_approval"})
            gate.setdefault("summary", {})["blockingIssueCount"] = len(gate["blockers"])
            run["approvalReadinessStatus"] = "not_ready_for_approval"
            run["approvalStatus"] = "not_requested"
        run["executionStatus"] = "failed" if failed else (
            "completed" if quality["pass"] and not run.get("errors") else "completed_with_warnings"
        )
        run["reportStatus"] = "failed" if failed else ("quality_pass" if quality["pass"] else "quality_warning")
        run["status"] = run["executionStatus"]

    def _wf_land_review(self, run, data, step, options) -> None:
        land = self._run_domain_agent(
            run,
            project_data=data,
            options=options,
            agent_id="LandReadinessAgent",
            task_type="land_readiness_review",
            domain="land_legal",
            input_snapshot={"land": data.get("land"), "landLedger": data.get("landLedger")},
            executor=lambda: self._review_land_readiness(data),
            output_schema_name="LandReadinessReview",
        )
        step("land_agent_review", land.get("status", "completed"),
             {"status": land.get("status"), "summary": land.get("summary")},
             "landAgentReview", land)
        run["nextActions"].extend(land.get("nextActions", []) or [])

    def _wf_feasibility(self, run, data, step, options) -> None:
        dev = self._run_domain_agent(
            run,
            project_data=data,
            options=options,
            agent_id="DevelopmentFeasibilityAgent",
            task_type="development_feasibility_analysis",
            domain="feasibility_cashflow",
            input_snapshot={
                "feasibilityInput": data.get("feasibility_input"),
                "feasibilityStudyV2": data.get("feasibilityStudyV2"),
                "productMix": data.get("productMix") or data.get("product_mix"),
                "schedule": data.get("schedule"),
            },
            executor=lambda: DevelopmentFeasibilityAgent().analyze(data),
            output_schema_name="DevelopmentFeasibilityResult",
            deterministic_authority_required=True,
        )
        step("development_feasibility_analysis", "completed",
             {"summary": dev.get("summary"), "pfStatus": dev.get("pfReadiness", {}).get("status")},
             "developerFeasibility", dev)
        feas = self._calculate_feasibility(data)
        step("feasibility_calculation", "completed", {"status": feas.get("status")}, "feasibility", feas)
        ret = calculate_return_metrics(data.get("feasibility_input", {}) or {})
        step("return_metrics", "completed", {"summary": ret.get("summary")}, "returnMetrics", ret)

    def _wf_marketability(self, run, data, step, options) -> None:
        dev = self._run_domain_agent(
            run,
            project_data=data,
            options=options,
            agent_id="DevelopmentFeasibilityAgent",
            task_type="marketability_feasibility_context",
            domain="feasibility_cashflow",
            input_snapshot={
                "feasibilityInput": data.get("feasibility_input"),
                "productMix": data.get("productMix") or data.get("product_mix"),
            },
            executor=lambda: DevelopmentFeasibilityAgent().analyze(data),
            output_schema_name="DevelopmentFeasibilityResult",
            deterministic_authority_required=True,
        )
        feas = self._calculate_feasibility(data)
        market_evidence = self._run_domain_agent(
            run,
            project_data=data,
            options=options,
            agent_id="MarketEvidenceAgent",
            task_type="market_evidence_reconciliation",
            domain="market_product",
            input_snapshot={
                "market": data.get("market"),
                "marketEvidence": data.get("marketEvidence") or data.get("market_evidence"),
                "developerFeasibility": dev,
                "feasibility": feas,
            },
            executor=lambda: MarketEvidenceAgent().review(data, dev, feas),
            output_schema_name="MarketEvidenceReview",
        )
        step("market_evidence_reconciliation", market_evidence.get("status", "completed"),
             {"status": market_evidence.get("status"), "evidenceSummary": market_evidence.get("evidenceSummary")},
             "marketEvidenceReview", market_evidence)
        mk_input = self._deep_merge(data, market_evidence.get("marketPayloadPatch", {}))
        mk_input["marketEvidenceReview"] = market_evidence
        self._maybe_inject_live_market_comparables(run, data, mk_input)
        marketability = self._run_domain_agent(
            run,
            project_data=data,
            options=options,
            agent_id="MarketabilityAgent",
            task_type="marketability_analysis",
            domain="market_product",
            input_snapshot={
                "marketEvidenceReview": market_evidence,
                "market": mk_input.get("market"),
                "productMix": mk_input.get("productMix") or mk_input.get("product_mix"),
            },
            executor=lambda: MarketabilityAgent().analyze(mk_input),
            output_schema_name="MarketabilityAnalysis",
        )
        step("marketability_analysis", "completed",
             {"saleable": marketability.get("isSaleable", marketability.get("applicable"))},
             "marketabilityAnalysis", marketability)

    def _wf_returns_stress(self, run, data, step, options) -> None:
        feasibility_input = data.get("feasibility_input", {}) or {}
        ret = calculate_return_metrics(feasibility_input)
        step("return_metrics", "completed", {"summary": ret.get("summary")}, "returnMetrics", ret)
        stress_request = self._build_stress_request(data)
        stress = self._run_domain_agent(
            run,
            project_data=data,
            options=options,
            agent_id="StressTestAgent",
            task_type="returns_stress_test",
            domain="pf_credit",
            input_snapshot=stress_request.model_dump(by_alias=True, mode="json"),
            executor=lambda: StressTestAgent().run_stress_test(stress_request),
            output_schema_name="StressTestResult",
            deterministic_authority_required=True,
        )
        step("stress_test", "completed",
             {"worstCaseKey": stress.get("worstCaseKey"), "seniorBreakScenarioKey": stress.get("seniorBreakScenarioKey")},
             "stressTest", stress)

    def _wf_pf_structure_review(self, run, data, step, options) -> None:
        pf = self._run_domain_agent(
            run,
            project_data=data,
            options=options,
            agent_id="PFStructuringAgent",
            task_type="pf_structure_calculation",
            domain="pf_credit",
            input_snapshot={
                "feasibilityInput": data.get("feasibility_input"),
                "pfStructure": data.get("pf_structure") or data.get("pfStructure"),
                "cashFlowRunResult": data.get("cashFlowRunResult"),
            },
            executor=lambda: self._calculate_pf_structure(data),
            output_schema_name="PFStructureResult",
            deterministic_authority_required=True,
        )
        step("pf_structure_calculation", "completed",
             {"ltv": pf.get("ltv"), "ltc": pf.get("ltc"), "exit_sale_rate": pf.get("exit_sale_rate")},
             "pfStructure", pf)
        ce_inputs = [CreditEnhancementInput(**e) for e in (data.get("credit_enhancements") or []) if isinstance(e, dict)]
        ce_req = CreditEnhancementRequest(baseSeniorLtv=float(pf.get("ltv") or 0.0), enhancements=ce_inputs)
        ce = self._run_domain_agent(
            run,
            project_data=data,
            options=options,
            agent_id="CreditEnhancementAgent",
            task_type="credit_enhancement_analysis",
            domain="pf_credit",
            input_snapshot=ce_req.model_dump(by_alias=True, mode="json"),
            executor=lambda: CreditEnhancementAgent().evaluate(ce_req),
            output_schema_name="CreditEnhancementResult",
            deterministic_authority_required=True,
        )
        step("credit_enhancement", "completed",
             {"enhancementGrade": ce.get("enhancementGrade"), "adjustedSeniorLtv": ce.get("adjustedSeniorLtv")},
             "creditEnhancement", ce)

    def _run_domain_agent(
        self,
        run: Dict[str, Any],
        *,
        project_data: Dict[str, Any],
        options: Dict[str, Any],
        agent_id: str,
        task_type: str,
        domain: str,
        input_snapshot: Dict[str, Any],
        executor,
        output_schema_name: str,
        deterministic_authority_required: bool = False,
        llm_required: bool = False,
    ) -> Any:
        output, runtime_envelope = run_domain_agent_callable_with_contract(
            project_id=str(run.get("project_id") or project_data.get("projectId") or ""),
            agent_id=agent_id,
            task_type=task_type,
            domain=domain,
            input_snapshot=input_snapshot,
            executor=executor,
            options=options,
            parent_run_id=str(run.get("run_id") or "") or None,
            output_schema_name=output_schema_name,
            deterministic_authority_required=deterministic_authority_required,
            llm_required=llm_required,
        )
        run.setdefault("agentRuntimeExecutions", []).append(runtime_envelope)
        require_successful_runtime(runtime_envelope, label=agent_id)
        return output

    @staticmethod
    def _build_stress_request(data: Dict[str, Any]) -> StressTestRequest:
        fi = data.get("feasibility_input", {}) or {}
        tranches = []
        for index, item in enumerate(data.get("pf_structure", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            tranches.append(StressTranche(
                tranche=str(item.get("tranche") or item.get("name") or f"Tr.{index}"),
                amount=float(item.get("amount") or 0.0),
                priority=normalize_tranche_priority(item.get("priority"), index),
                interestRate=float(item.get("interest_rate") or item.get("interestRate") or 0.0),
                feeRate=float(item.get("fee_rate") or item.get("feeRate") or 0.0),
            ))
        total_cost = float(fi.get("total_cost") or 0.0)
        land = float(fi.get("land_cost") or 0.0)
        constr = float(fi.get("construction_cost") or 0.0)
        fin = float(fi.get("finance_cost") or 0.0)
        other = max(0.0, total_cost - land - constr - fin) if total_cost > 0 else float(fi.get("other_cost") or 0.0)
        product_mix = data.get("product_mix") or data.get("productMix") or []
        sales_collection = next(
            (
                str(p.get("defaultCollection") or p.get("default_collection"))
                for p in product_mix
                if isinstance(p, dict) and (p.get("defaultCollection") or p.get("default_collection"))
            ),
            "10-60-30",
        )
        return StressTestRequest(
            totalRevenue=float(fi.get("total_revenue") or fi.get("sales_revenue") or 0.0),
            landCost=land, constructionCost=constr, otherCost=other, financeCost=fin,
            pfStructure=tranches,
            salesCollection=sales_collection,
        )

    def _run_term_annotation_step(self, run: Dict[str, Any], project_data: Dict[str, Any]) -> None:
        try:
            from use_cases.term_annotation import annotate_terms_use_case

            term_annotations = annotate_terms_use_case(
                project_data,
                project_id=run.get("project_id"),
                run_id=run.get("run_id"),
            )
            project_data["termAnnotations"] = term_annotations
            run["artifacts"]["termAnnotations"] = term_annotations
            summary = term_annotations.get("summary", {})
            output = {
                "mapped": summary.get("mappedCount"),
                "distinctTerms": summary.get("distinctTerms"),
                "distinctCanonicalFields": summary.get("distinctCanonicalFields"),
                "unmappedCandidates": summary.get("unmappedCandidateCount"),
            }
        except Exception as exc:
            run["errors"].append({"message": f"Term annotation failed: {exc}", "type": exc.__class__.__name__})
            output = {"status": "error", "reason": str(exc)}
        self._complete_step(run, "term_annotation", output, artifact_key="termAnnotations")

    def _run_credit_document_step(self, run: Dict[str, Any], project_data: Dict[str, Any]) -> None:
        try:
            agent = CreditDocumentAgent()
            term_sheet = agent.build_term_sheet(project_data, run["artifacts"])
            ic_memo = agent.build_ic_memo(project_data, run["artifacts"])
            stage_sheets = {
                "bridge": agent.build_term_sheet(project_data, run["artifacts"], stage="bridge"),
                "main_pf": agent.build_term_sheet(project_data, run["artifacts"], stage="main_pf"),
            }
            run["artifacts"]["termSheet"] = term_sheet
            run["artifacts"]["termSheetStages"] = stage_sheets
            run["artifacts"]["icMemo"] = ic_memo
            self._add_term_sheet_contribution_handoffs(run, term_sheet)
            output = {
                "termSheetStage": term_sheet.get("stage"),
                "termSheetTrancheCount": len(term_sheet.get("facility", {}).get("tranches", [])),
                "termSheetContributors": [c.get("agent") for c in term_sheet.get("contributions", [])],
                "icMemoDecision": ic_memo.get("recommendation", {}).get("decisionLabel"),
                "icMemoApprovalReady": ic_memo.get("recommendation", {}).get("approvalReady"),
            }
        except Exception as exc:
            run["errors"].append({"message": f"Credit document generation failed: {exc}", "type": exc.__class__.__name__})
            output = {"status": "error", "reason": str(exc)}
        self._complete_step(run, "credit_document_generation", output, artifact_key="icMemo")

    def _add_term_sheet_contribution_handoffs(self, run: Dict[str, Any], term_sheet: Dict[str, Any]) -> None:
        """Record the per-agent contributions to the term sheet as typed handoffs.

        Makes the term sheet's multi-agent division of labour explicit in the run
        graph: the Land Agent contributes 대지위치·지번·공시지가·면적, the Feasibility
        Agent contributes 사업수지·한도, etc.
        """
        sections = term_sheet.get("sections") or []
        contributor_handoff = {
            "LandAgent": "land_to_term_sheet",
            "DevelopmentFeasibilityAgent": "feasibility_to_term_sheet",
        }
        for agent, handoff_type in contributor_handoff.items():
            owned = [
                {"label": s.get("label"), "value": s.get("value")}
                for s in sections
                if s.get("sourceAgent") == agent
            ]
            if not owned:
                continue
            evidence_ids: List[str] = []
            for s in sections:
                if s.get("sourceAgent") == agent:
                    evidence_ids.extend((s.get("evidence") or {}).get("evidenceIds") or [])
            self._add_handoff(
                run,
                handoff_type=handoff_type,
                from_agent=agent,
                to_agent="TermSheetAgent",
                payload_schema="TermSheetContributionV1",
                payload={"stage": term_sheet.get("stage"), "sections": owned},
                evidence_ids=evidence_ids,
                assumptions=[
                    "Term sheet values are indicative; [TBD]/[숫자] placeholders are finalised in negotiation.",
                ],
            )

    def _run_deal_optimization_step(self, run: Dict[str, Any], project_data: Dict[str, Any]) -> None:
        try:
            policy = resolve_credit_policy(project_data)
            optimization = DealOptimizationAgent().optimize(project_data, run["artifacts"], policy=policy)
            run["artifacts"]["dealOptimization"] = optimization
            output = {
                "verdict": optimization.get("verdict"),
                "verdictLabel": optimization.get("verdictLabel"),
                "leverCount": len(optimization.get("levers", [])),
                "actionCount": len(optimization.get("borrowerActions", [])),
            }
        except Exception as exc:
            run["errors"].append({"message": f"Deal optimization failed: {exc}", "type": exc.__class__.__name__})
            output = {"status": "error", "reason": str(exc)}
        self._complete_step(run, "deal_optimization", output, artifact_key="dealOptimization")

    def _run_developability_score_step(self, run: Dict[str, Any], project_data: Dict[str, Any]) -> None:
        try:
            # Marketability is only kept in run artifacts; expose it to the score
            # agent (which reads project_data) without disturbing other steps.
            marketability = run["artifacts"].get("marketabilityAnalysis")
            if marketability is not None:
                project_data["marketabilityAnalysis"] = marketability
            score = DevelopabilityScoreAgent().score(
                project_data,
                return_metrics=project_data.get("returnMetrics"),
            )
            project_data["developabilityScore"] = score
            run["artifacts"]["developabilityScore"] = score
            output = {
                "overallScore": score.get("overallScore"),
                "grade": score.get("grade"),
                "gaps": score.get("gaps", []),
            }
        except Exception as exc:
            run["errors"].append({"message": f"Developability score failed: {exc}", "type": exc.__class__.__name__})
            output = {"status": "error", "reason": str(exc)}
        self._complete_step(run, "developability_score", output, artifact_key="developabilityScore")

    def _maybe_inject_live_market_comparables(
        self,
        run: Dict[str, Any],
        project_data: Dict[str, Any],
        marketability_input: Dict[str, Any],
    ) -> None:
        """Gated: pull recent 국토부 실거래가 for the project's 법정동/asset type and
        add them as Marketability `transactionComparables` so pricing/Exit판단 use
        real closed transactions. No-op unless REALYTICS_LIVE_MARKET_COMPARABLES is
        on AND a data.go.kr key is configured. Never breaks the run / fabricates."""
        if not live_market_comparables_enabled():
            return
        connector = MolitRtmsConnector()
        if not connector.configured():
            return
        try:
            records, diagnostic = fetch_project_market_comparables(
                project_data, connector=connector, deal_yms=recent_deal_yms(3)
            )
            injected = inject_market_comparables(marketability_input, records)
            run.setdefault("artifacts", {})["liveMarketComparables"] = {
                "schemaVersion": "realytics-live-market-comparables-v1",
                "source": "molit_rtms",
                "injected": injected,
                "diagnostic": diagnostic,
            }
        except Exception as exc:  # external data must never break the credit run
            run.setdefault("errors", []).append(
                {"message": f"Live market comparables fetch failed: {exc}", "type": exc.__class__.__name__}
            )

    def _run_return_metrics_step(self, run: Dict[str, Any], project_data: Dict[str, Any]) -> None:
        try:
            feasibility = dict(project_data.get("feasibility_input", {}) or {})
            pf_amount = sum(
                float(t.get("amount") or 0)
                for t in (project_data.get("pf_structure") or [])
                if isinstance(t, dict)
            )
            inputs = self._derive_return_inputs(feasibility, pf_amount)
            return_metrics = calculate_return_metrics(inputs)
            project_data["returnMetrics"] = return_metrics
            run["artifacts"]["returnMetrics"] = return_metrics
            metrics = return_metrics.get("metrics", {})
            output = {
                "summary": return_metrics.get("summary"),
                "projectIrr": metrics.get("projectIrr", {}).get("value"),
                "equityMultiple": metrics.get("equityMultiple", {}).get("value"),
                "icr": metrics.get("icr", {}).get("value"),
            }
        except Exception as exc:
            run["errors"].append({"message": f"Return metrics failed: {exc}", "type": exc.__class__.__name__})
            output = {"status": "error", "reason": str(exc)}
        self._complete_step(run, "return_metrics", output, artifact_key="returnMetrics")

    @staticmethod
    def _derive_return_inputs(fi: Dict[str, Any], pf_amount: float) -> Dict[str, Any]:
        """Derive UNLEVERED project + levered equity cash flows from feasibility.

        Project IRR is unlevered (finance cost excluded, per convention); equity
        IRR is levered (equity = total cost − PF). ICR uses operating profit over
        finance cost for sale-type development PF where there is no rental NOI.
        """
        def _num(*keys):
            for k in keys:
                v = fi.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
            return 0.0

        months, cstart, cend, sstart, send = 36, 3, 30, 7, 36
        revenue = _num("total_revenue", "sales_revenue")
        total_cost = _num("total_cost")
        land = _num("land_cost", "land_purchase_cost")
        construction = _num("construction_cost")
        finance = _num("finance_cost")
        other = max(0.0, total_cost - land - construction - finance) if total_cost else _num("other_cost")

        outflow = [0.0] * months
        inflow = [0.0] * months
        if land:
            outflow[0] += land
        OrchestrationAgent._spread_amount(outflow, construction + other, cstart, cend, months)  # unlevered: no finance
        OrchestrationAgent._spread_amount(inflow, revenue, sstart, send, months)
        project_cashflows = [inflow[m] - outflow[m] for m in range(months)]

        profit = revenue - total_cost
        equity = max(0.0, total_cost - pf_amount)
        equity_cashflows = [0.0] * months
        if equity > 0:
            OrchestrationAgent._spread_amount(equity_cashflows, -equity, 1, 6, months)
            equity_cashflows[months - 1] += equity + profit  # capital return + residual profit after debt

        operating_profit = _num("operating_profit") or (revenue - (land + construction + other))
        return {
            "project_cashflows": project_cashflows,
            "equity_cashflows": equity_cashflows if equity > 0 else [],
            "net_operating_income": operating_profit,
            "interest_expense": finance,
            "periods_per_year": 12,
        }

    @staticmethod
    def _spread_amount(series: List[float], amount: float, start_month: int, end_month: int, months: int) -> None:
        if amount == 0:
            return
        start = max(1, start_month)
        end = min(months, max(start, end_month))
        span = end - start + 1
        if span <= 0:
            return
        per = amount / span
        for month in range(start, end + 1):
            series[month - 1] += per

    def _run_stress_test_step(self, run: Dict[str, Any], project_data: Dict[str, Any]) -> None:
        try:
            stress = StressTestAgent().run_stress_test(self._build_stress_request(project_data)).model_dump()
            project_data["stressTest"] = stress
            run["artifacts"]["stressTest"] = stress
            base = next((s for s in stress.get("scenarios", []) if s.get("key") == "base"), None)
            output = {
                "baseProjectIrr": base.get("projectIrr") if base else None,
                "worstCaseKey": stress.get("worstCaseKey"),
                "seniorBreakScenarioKey": stress.get("seniorBreakScenarioKey"),
            }
        except Exception as exc:
            run["errors"].append({"message": f"Stress test failed: {exc}", "type": exc.__class__.__name__})
            output = {"status": "error", "reason": str(exc)}
        self._complete_step(run, "stress_test", output, artifact_key="stressTest")

    def _run_credit_enhancement_step(self, run: Dict[str, Any], project_data: Dict[str, Any], pf_structure: Dict[str, Any]) -> None:
        try:
            base_senior_ltv = float((pf_structure or {}).get("ltv") or 0.0)
            base_dscr = ((pf_structure or {}).get("metrics", {}).get("dscr", {}) or {}).get("value")
            ce_inputs = [
                CreditEnhancementInput(**e)
                for e in (project_data.get("credit_enhancements") or [])
                if isinstance(e, dict)
            ]
            credit_enh = CreditEnhancementAgent().evaluate(
                CreditEnhancementRequest(baseSeniorLtv=base_senior_ltv, baseDscr=base_dscr, enhancements=ce_inputs)
            ).model_dump()
            project_data["creditEnhancement"] = credit_enh
            run["artifacts"]["creditEnhancement"] = credit_enh
            output = {
                "enhancementGrade": credit_enh.get("enhancementGrade"),
                "adjustedSeniorLtv": credit_enh.get("adjustedSeniorLtv"),
                "activeCount": credit_enh.get("activeCount"),
                "missingCriticalMeasures": credit_enh.get("missingCriticalMeasures"),
            }
        except Exception as exc:
            run["errors"].append({"message": f"Credit enhancement failed: {exc}", "type": exc.__class__.__name__})
            output = {"status": "error", "reason": str(exc)}
        self._complete_step(run, "credit_enhancement_review", output, artifact_key="creditEnhancement")

    def _run_cash_sweep_step(self, run: Dict[str, Any], project_data: Dict[str, Any]) -> None:
        monthly_ops = project_data.get("monthly_operating_cashflow")
        if not (isinstance(monthly_ops, list) and monthly_ops):
            self._complete_step(run, "cash_sweep_simulation", {
                "status": "skipped",
                "reason": "monthly_operating_cashflow not provided; cash sweep requires a monthly cash-flow series.",
            }, artifact_key="cashSweep")
            return
        try:
            tranches = []
            for index, item in enumerate(project_data.get("pf_structure", []) or [], start=1):
                if not isinstance(item, dict):
                    continue
                tranches.append(CashSweepTranche(
                    tranche=str(item.get("tranche") or item.get("name") or f"Tr.{index}"),
                    amount=float(item.get("amount") or 0.0),
                    priority=normalize_tranche_priority(item.get("priority"), index),
                    interestRate=float(item.get("interest_rate") or item.get("interestRate") or 0.0),
                    feeRate=float(item.get("fee_rate") or item.get("feeRate") or 0.0),
                ))
            cash_sweep = CashSweepAgent().simulate(CashSweepRequest(
                monthlyOperatingCashflow=[float(x) for x in monthly_ops],
                tranches=tranches,
                reserveTarget=float(project_data.get("reserve_target") or 0.0),
            )).model_dump()
            project_data["cashSweep"] = cash_sweep
            run["artifacts"]["cashSweep"] = cash_sweep
            output = {
                "totalPrincipalSwept": cash_sweep.get("totalPrincipalSwept"),
                "endingTotalBalance": cash_sweep.get("endingTotalBalance"),
            }
        except Exception as exc:
            run["errors"].append({"message": f"Cash sweep failed: {exc}", "type": exc.__class__.__name__})
            output = {"status": "error", "reason": str(exc)}
        self._complete_step(run, "cash_sweep_simulation", output, artifact_key="cashSweep")

    def _review_land_readiness(self, data: Dict[str, Any]) -> Dict[str, Any]:
        land_ledger = data.get("landLedger") or data.get("land_ledger") or {}
        land_evidence = data.get("landEvidence") or data.get("land_evidence") or {}
        land = data.get("land") or data.get("site") or {}
        project = data.get("project") or {}

        if not isinstance(land_ledger, dict) or not land_ledger:
            pnu_resolutions = land_evidence.get("pnuResolutions") or land_evidence.get("pnu_resolutions") or []
            first_pnu_resolution = next((item for item in pnu_resolutions if isinstance(item, dict)), {})
            pnu = land.get("pnu") or data.get("pnu") or first_pnu_resolution.get("pnu") or first_pnu_resolution.get("resolvedPnu")
            official_price = (
                land.get("official_land_price")
                or land.get("officialLandPrice")
                or land_evidence.get("officialLandPrice")
                or land_evidence.get("official_land_price")
            )
            total_area = (
                land.get("land_area_sqm")
                or land.get("landAreaSqm")
                or land.get("area_sqm")
                or land.get("areaSqm")
            )
            official_price_evidence = (
                land_evidence.get("officialLandPriceEvidence")
                or land_evidence.get("official_land_price_evidence")
            )
            registry_evidence = (
                land_evidence.get("registryEvidence")
                or land_evidence.get("registry_evidence")
                or land_evidence.get("encumbranceEvidence")
                or land_evidence.get("encumbrance_evidence")
            )
            acquisition_ratio = self._basic_acquisition_ratio(data, total_area, land_evidence)
            source_documents = (
                land_evidence.get("sourceDocuments")
                or land_evidence.get("source_documents")
                or []
            )
            pnu_ready = bool(pnu)
            official_ready = bool(official_price or official_price_evidence)
            registry_ready = bool(registry_evidence)
            acquisition_ready = acquisition_ratio >= 1
            source_ready = bool(land_evidence or source_documents)
            ready_for_pf = pnu_ready and official_ready and registry_ready and acquisition_ready
            status = (
                "ready_for_pf_review"
                if ready_for_pf
                else "basic_site_data_only"
                if pnu or official_price or land_evidence
                else "land_ledger_not_provided"
            )
            limitations = []
            if not land_evidence:
                limitations.append("Land ledger import or flat land evidence package is not attached to this orchestration run.")
            if not pnu_ready:
                limitations.append("PNU evidence is missing.")
            if not official_ready:
                limitations.append("Official land price evidence is missing.")
            if not registry_ready:
                limitations.append("Registry and encumbrance evidence is missing.")
            if not acquisition_ready:
                limitations.append("Land acquisition area ratio is below 100%.")
            return {
                "analysisType": "land_agent_orchestration_review_v1",
                "status": status,
                "summary": {
                    "projectName": project.get("name"),
                    "pnu": pnu,
                    "officialLandPrice": official_price,
                    "officialLandPriceBaseYear": land_evidence.get("officialLandPriceBaseYear")
                    or land_evidence.get("official_land_price_base_year"),
                    "parcelCount": land_evidence.get("parcelCount") or land_evidence.get("parcel_count") or 0,
                    "totalAreaSqm": total_area,
                    "totalPurchaseAmount": None,
                    "pnuCoverageRatio": 1.0 if pnu_ready else 0.0,
                    "officialLandPriceCoverageRatio": 1.0 if official_ready else 0.0,
                    "registryEvidenceCoverageRatio": 1.0 if registry_ready else 0.0,
                    "acquisitionAreaRatio": acquisition_ratio,
                },
                "landLedgerStatus": status,
                "landAcquisitionStatus": {
                    "acquisitionAreaRatio": acquisition_ratio,
                    "source": "landEvidence" if land_evidence else "land_acquisition",
                },
                "pfSummary": {},
                "evidenceReadiness": {
                    "pnuReady": pnu_ready,
                    "officialPriceReady": official_ready,
                    "registryEvidenceReady": registry_ready,
                    "acquisitionReady": acquisition_ready,
                    "sourceDataReady": source_ready,
                },
                "blockingIssues": [],
                "dataLimitations": limitations,
                "requiresHumanReview": not ready_for_pf,
                "confidence": 0.85 if ready_for_pf else 0.45 if pnu or official_price or land_evidence else 0.15,
                "handoffReadiness": "pf_ready_patch" if ready_for_pf else "limited_patch",
                "evidenceIds": self._collect_evidence_ids(land_evidence),
                "sourceLocators": self._land_source_locators(land_evidence),
                "sourceMetadata": land_evidence.get("sourceMetadata") or land_evidence.get("source_metadata") or {},
            }

        summary = land_ledger.get("summary", {})
        validation = land_ledger.get("validation", {})
        pf_summary = land_ledger.get("pfSummary", {})
        acquisition = land_ledger.get("landAcquisitionStatus", {})
        workflow_counts = summary.get("workflowStatusCounts", {})
        status = validation.get("status") or pf_summary.get("status") or "parsed"
        pnu_coverage = pf_summary.get("pnuCoverageRatio", self._coverage_ratio(summary, "missingPnuCount"))
        official_price_coverage = pf_summary.get(
            "officialLandPriceCoverageRatio",
            self._coverage_ratio(summary, "missingOfficialLandPriceCount"),
        )
        registry_coverage = pf_summary.get(
            "registryEvidenceCoverageRatio",
            self._coverage_ratio(summary, "registryEvidenceMissingCount"),
        )
        acquisition_ratio = acquisition.get("acquisitionAreaRatio", 0)

        blocking_issues = [
            self._stringify_issue(issue)
            for issue in validation.get("issues", [])
            if isinstance(issue, dict) and issue.get("severity") in {"blocking", "blocking_for_pf_review"}
        ]
        data_limitations: List[str] = []
        if pnu_coverage < 1:
            data_limitations.append("PNU coverage is below 100%; cadastral and legal enrichment is incomplete.")
        if official_price_coverage < 1:
            data_limitations.append("Official land price coverage is below 100%; base-year value evidence is incomplete.")
        if registry_coverage < 1:
            data_limitations.append("Registry and encumbrance evidence coverage is below 100%; legal review cannot be final.")
        if acquisition_ratio < 1:
            data_limitations.append("Land acquisition area ratio is below 100%; closing readiness requires human review.")
        if acquisition.get("publicLandDetected"):
            data_limitations.append("Public land is detected and must be tracked as a separate acquisition condition.")

        ready_for_pf = pf_summary.get("status") == "ready_for_pf_review" and not blocking_issues
        requires_review = bool(blocking_issues) or not ready_for_pf
        confidence_inputs = [pnu_coverage, official_price_coverage, registry_coverage]
        if acquisition_ratio:
            confidence_inputs.append(acquisition_ratio)
        confidence = sum(confidence_inputs) / len(confidence_inputs) if confidence_inputs else 0.2

        return {
            "analysisType": "land_agent_orchestration_review_v1",
            "status": "ready_for_pf_review" if ready_for_pf else status,
            "summary": {
                "projectName": project.get("name"),
                "parcelCount": summary.get("parcelCount", 0),
                "totalAreaSqm": summary.get("totalAreaSqm"),
                "totalPurchaseAmount": summary.get("totalContractAmount"),
                "workflowStatusCounts": workflow_counts,
                "pnuCoverageRatio": pnu_coverage,
                "officialLandPriceCoverageRatio": official_price_coverage,
                "registryEvidenceCoverageRatio": registry_coverage,
                "acquisitionAreaRatio": acquisition_ratio,
                "publicLandDetected": acquisition.get("publicLandDetected", False),
            },
            "landLedgerStatus": status,
            "landAcquisitionStatus": acquisition,
            "pfSummary": pf_summary,
            "evidenceReadiness": {
                "pnuReady": pnu_coverage >= 1,
                "officialPriceReady": official_price_coverage >= 1,
                "registryEvidenceReady": registry_coverage >= 1,
                "acquisitionReady": acquisition_ratio >= 1,
                "sourceDataReady": validation.get("status") not in {"needs_review", "parse_failed"},
            },
            "blockingIssues": blocking_issues,
            "dataLimitations": data_limitations,
            "requiresHumanReview": requires_review,
            "confidence": self._normalize_confidence(confidence),
            "handoffReadiness": "pf_ready_patch" if ready_for_pf else "review_required_patch",
            "evidenceIds": self._collect_evidence_ids(land_ledger),
            "sourceLocators": self._land_source_locators(land_ledger),
            "sourceMetadata": land_ledger.get("importMetadata", {}),
            "reviewMemo": pf_summary.get("reviewMemo"),
        }

    def _land_source_locators(self, value: Dict[str, Any]) -> List[Dict[str, Any]]:
        locators: List[Dict[str, Any]] = []
        records = value.get("records") if isinstance(value, dict) else None
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                source = record.get("sourceUrlOrEvidenceId") or record.get("evidenceId")
                if source:
                    locators.append({
                        "locatorId": source,
                        "source": source,
                        "label": f"{record.get('evidenceType', 'land_evidence')} {record.get('pnu') or record.get('parcelKey') or ''}".strip(),
                        "path": f"landEvidence.records[{record.get('recordKey') or record.get('evidenceId')}]",
                        "evidenceType": record.get("evidenceType"),
                        "pnu": record.get("pnu"),
                    })
        return locators

    def _land_handoff_payload(self, land_review: Dict[str, Any]) -> Dict[str, Any]:
        summary = land_review.get("summary", {})
        return {
            "sitePatch": {
                "parcelCount": summary.get("parcelCount"),
                "landAreaSqm": summary.get("totalAreaSqm"),
                "pnuCoverageRatio": summary.get("pnuCoverageRatio"),
                "officialLandPriceCoverageRatio": summary.get("officialLandPriceCoverageRatio"),
                "registryEvidenceCoverageRatio": summary.get("registryEvidenceCoverageRatio"),
            },
            "landCostPatch": {
                "totalPurchaseAmount": summary.get("totalPurchaseAmount"),
                "acquisitionAreaRatio": summary.get("acquisitionAreaRatio"),
                "publicLandDetected": summary.get("publicLandDetected"),
            },
            "legalEvidencePatch": {
                "evidenceReadiness": land_review.get("evidenceReadiness", {}),
                "blockingIssues": land_review.get("blockingIssues", []),
                "dataLimitations": land_review.get("dataLimitations", []),
            },
            "pfLandSummary": land_review.get("pfSummary", {}),
            "landAcquisitionStatus": land_review.get("landAcquisitionStatus", {}),
            "handoffReadiness": land_review.get("handoffReadiness"),
        }

    def _coverage_ratio(self, summary: Dict[str, Any], missing_key: str) -> float:
        parcel_count = summary.get("parcelCount") or 0
        if not parcel_count:
            return 0.0
        missing = summary.get(missing_key) or 0
        return max(0.0, min(1.0, 1 - (missing / parcel_count)))

    def _basic_acquisition_ratio(
        self,
        data: Dict[str, Any],
        total_area: Any,
        land_evidence: Dict[str, Any],
    ) -> float:
        explicit_ratio = (
            land_evidence.get("acquisitionAreaRatio")
            or land_evidence.get("acquisition_area_ratio")
        )
        if isinstance(explicit_ratio, (int, float)):
            return max(0.0, min(1.0, float(explicit_ratio)))
        parcels = data.get("land_acquisition") or data.get("landAcquisition") or []
        if not isinstance(parcels, list) or not parcels:
            return 0.0
        acquired_area = sum(
            float(parcel.get("area_sqm") or parcel.get("areaSqm") or parcel.get("area") or 0)
            for parcel in parcels
            if isinstance(parcel, dict)
        )
        if isinstance(total_area, (int, float)) and total_area > 0:
            return max(0.0, min(1.0, acquired_area / float(total_area)))
        shares = [
            float(parcel.get("share") or 0)
            for parcel in parcels
            if isinstance(parcel, dict)
        ]
        return 1.0 if shares and sum(shares) >= 1 else 0.0

    def _calculate_feasibility(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return calculate_feasibility_metrics(data.get("feasibility_input", {}))

    def _calculate_pf_structure(self, data: Dict[str, Any]) -> Dict[str, Any]:
        feasibility = data.get("feasibility_input", {})
        tranches = data.get("pf_structure", [])
        result = calculate_pf_metrics({
            "tranches": tranches,
            "total_revenue": feasibility.get("total_revenue", 0),
            "total_cost": feasibility.get("total_cost", 0),
            "finance_cost": feasibility.get("finance_cost", 0),
        })
        return {
            **result,
            "ltv": result["metrics"]["ltv"]["value"],
            "ltc": result["metrics"]["ltc"]["value"],
            "exit_sale_rate": result["metrics"]["exit_sale_rate"]["value"],
            "tranche_count": len(tranches),
        }

    def _assess_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return build_basic_risk_matrix(data)

    def _build_restructuring_plan(self, scenario_req: Any, scenario_run_result: Any, project_data: Dict[str, Any] | None = None) -> Dict[str, Any]:
        adopted_cures = []
        if isinstance(project_data, dict):
            adopted_cures = (
                project_data.get("adoptedRestructuringCures")
                or project_data.get("adopted_restructuring_cures")
                or []
            )
        if scenario_req is None or scenario_run_result is None:
            return {
                "schemaVersion": "realytics-restructuring-agent-v1",
                "analysisType": "pf_restructuring_plan",
                "status": "skipped",
                "reason": "Scenario Engine result is unavailable; restructuring plan could not be generated.",
                "recommendations": [],
                # Persisted cures remain part of the review record even when the
                # deterministic scenario engine cannot run. They are evidence of
                # an adopted action, not proof that the financial blocker is cured.
                "adoptedCures": RestructuringAgent().summarize_adopted_cures(adopted_cures),
                "approvalPath": {
                    "currentStatus": "scenario_result_missing",
                    "remainingBlockers": [],
                    "nextSteps": ["Run Scenario Engine before restructuring analysis."],
                    "conditionDrafts": [],
                },
                "dataLimitations": ["Scenario Engine output is missing."],
                "sourceMetadata": {
                    "sourceName": "Orchestration Agent",
                    "sourceType": "agent_artifact",
                    "confidence": 0.0,
                },
            }
        return RestructuringAgent().build_restructuring_plan(
            scenario_req,
            scenario_run_result,
            {"adoptedRestructuringCures": adopted_cures},
        )

    def _restructuring_blockers(self, restructuring_plan: Dict[str, Any]) -> List[str]:
        blockers = []
        approval_path = restructuring_plan.get("approvalPath") or {}
        for code in approval_path.get("remainingBlockers") or []:
            if code in {"SENIOR_SHORTFALL", "STRESS_SENIOR_SHORTFALL"}:
                blockers.append(f"{code}: senior repayment cure must be applied and rerun before approval.")
            elif code == "UNSOLD_LTV_BREACH":
                blockers.append(
                    "UNSOLD_LTV_BREACH: PF reduction, equity increase, collateral, forward sale, bulk sale, or refinancing evidence is required."
                )
            else:
                blockers.append(str(code))
        return blockers

    def _quality_check(
        self,
        run: Dict[str, Any],
        structure_results: Dict[str, Any] = None,
        visual_results: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        report = run["artifacts"]["phase11Report"]["report"]
        report_quality = report["qualityChecks"]
        required_artifacts = [
            "landAgentReview",
            "developerFeasibility",
            "feasibility",
            "pfStructure",
            "marketEvidenceReview",
            "marketabilityAnalysis",
            "dataQualityReview",
            "reconciliationGate",
            "legalPermitReview",
            "riskAssessment",
            "riskRegister",
            "creditReviewDecision",
            "restructuringPlan",
            "phase11Report",
            "evidenceGraph",
        ]
        missing_artifacts = [
            artifact for artifact in required_artifacts if artifact not in run["artifacts"]
        ]
        pdf_structure_pass = bool(structure_results) and structure_results.get("status") in {"passed", "warnings"}
        pdf_visual_pass = bool(visual_results) and visual_results.get("status") == "passed" and visual_results.get("baseline_reviewed") is True

        is_passed = (
            report_quality["isComplete"]
            and not missing_artifacts
            and pdf_structure_pass
            and pdf_visual_pass
        )

        return {
            "pass": is_passed,
            "gateType": "report_completeness",
            "reportComplete": report_quality["isComplete"],
            "missingReportSections": report_quality["missingSections"],
            "missingArtifacts": missing_artifacts,
            "pdfStructureQA": structure_results or {"status": "skipped", "reason": "No PDF rendered"},
            "pdfVisualQA": visual_results or {"status": "skipped", "reason": "No PDF rendered"},
        }

    def _approval_readiness_gate(self, run: Dict[str, Any]) -> Dict[str, Any]:
        artifacts = run.get("artifacts", {})
        risk_register = artifacts.get("riskRegister") or {}
        credit_review = artifacts.get("creditReviewDecision") or {}
        legal_review = artifacts.get("legalPermitReview") or {}
        legal_intelligence = legal_review.get("legalIntelligence") or {}
        verified_reference_count = legal_intelligence.get(
            "verifiedReferenceCount"
        )
        verified_citation_count = legal_intelligence.get(
            "verifiedCitationCount"
        )
        legal_evidence_trusted = (
            legal_intelligence.get("evidenceStatus") == "verified"
            and isinstance(verified_reference_count, int)
            and not isinstance(verified_reference_count, bool)
            and verified_reference_count > 0
            and isinstance(verified_citation_count, int)
            and not isinstance(verified_citation_count, bool)
            and verified_citation_count > 0
            and legal_intelligence.get("citationCoverageComplete") is True
            and legal_intelligence.get("claimCoverageComplete") is True
        )
        data_quality = artifacts.get("dataQualityReview") or {}
        reconciliation = artifacts.get("reconciliationGate") or {}
        quality = artifacts.get("qualityCheck") or {}
        land_review = artifacts.get("landAgentReview") or {}
        evidence_graph = artifacts.get("evidenceGraph") or {}
        restructuring = artifacts.get("restructuringPlan") or {}
        assumption_proposals = (artifacts.get("assumptionProposals") or {}).get("proposals") or []
        pending_assumption_proposals = [
            item for item in assumption_proposals
            if isinstance(item, dict) and item.get("status") == "pending"
        ]
        restructuring_blockers = set((restructuring.get("approvalPath") or {}).get("remainingBlockers") or [])

        gates = [
            self._approval_readiness_check(
                "REPORT_COMPLETENESS_PASS",
                bool(quality.get("pass")),
                "Report completeness quality check must pass.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "LEGAL_NOT_BLOCKING",
                legal_review.get("status") in {"clear", "caution"},
                f"Legal review status is {legal_review.get('status')}.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "LEGAL_EVIDENCE_TRUSTED",
                legal_evidence_trusted,
                (
                    "Legal evidence must be verified, exact-citation complete, "
                    "and complete for every required research claim. "
                    "Caller-supplied law_context remains candidate-only."
                ),
                severity="blocking",
            ),
            self._approval_readiness_check(
                "DATA_QUALITY_NOT_BLOCKING",
                data_quality.get("status") in {"clear", "needs_review"},
                f"Data Quality status is {data_quality.get('status')}.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "RECONCILIATION_NOT_BLOCKING",
                reconciliation.get("status") in {"clear", "needs_review", "warning"},
                f"Reconciliation status is {reconciliation.get('status')}.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "RISK_REGISTER_NOT_BLOCKING",
                risk_register.get("status") in {"clear", "watch", "high_risk"},
                f"Risk Register status is {risk_register.get('status')}.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "CREDIT_DECISION_NOT_REJECT_OR_REWORK",
                credit_review.get("decision") in {"approve", "conditional_approval"},
                f"Credit Review decision is {credit_review.get('decision')}.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "SENIOR_RESTRUCTURING_CLEARED",
                not {"SENIOR_SHORTFALL", "STRESS_SENIOR_SHORTFALL"}.intersection(restructuring_blockers),
                "Senior shortfall restructuring blockers must be cured and rerun before approval.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "ASSUMPTION_PROPOSALS_RESOLVED",
                not pending_assumption_proposals,
                f"{len(pending_assumption_proposals)} consensus assumption proposal(s) require approval into a new project version.",
                severity="blocking",
            ),
        ]

        unresolved_handoffs = self._unresolved_handoff_reviews(run)
        gates.append(self._approval_readiness_check(
            "AGENT_EXECUTION_SUCCEEDED",
            not any(step.get("status") == "failed" for step in run.get("steps", [])),
            "Agent execution failures must be resolved before approval.",
            severity="blocking",
        ))
        unresolved_risks = self._unresolved_risk_reviews(run)
        gates.append(self._approval_readiness_check(
            "HANDOFF_REVIEWS_RESOLVED",
            not unresolved_handoffs,
            f"{len(unresolved_handoffs)} handoff review(s) remain unresolved.",
            severity="blocking",
        ))
        gates.append(self._approval_readiness_check(
            "RISK_REVIEWS_RESOLVED",
            not unresolved_risks,
            f"{len(unresolved_risks)} material risk review(s) remain unresolved.",
            severity="blocking",
        ))

        land_readiness = land_review.get("evidenceReadiness") or {}
        gates.extend([
            self._approval_readiness_check(
                "LAND_PNU_READY",
                bool(land_readiness.get("pnuReady")),
                "Parcel-level PNU evidence must be attached before PF approval.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "LAND_OFFICIAL_PRICE_READY",
                bool(land_readiness.get("officialPriceReady")),
                "Official land price evidence with base year/source must be attached before PF approval.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "LAND_REGISTRY_EVIDENCE_READY",
                bool(land_readiness.get("registryEvidenceReady")),
                "Registry and encumbrance evidence must be attached before PF approval.",
                severity="blocking",
            ),
            self._approval_readiness_check(
                "LAND_ACQUISITION_READY",
                bool(land_readiness.get("acquisitionReady")),
                "Land acquisition or closing readiness must be complete before PF approval.",
                severity="blocking",
            ),
        ])

        land_status = land_review.get("status")
        if land_status in {"land_ledger_not_provided", "basic_site_data_only"}:
            gates.append(self._approval_readiness_check(
                "LAND_EVIDENCE_READY",
                False,
                f"Land evidence status is {land_status}; parcel-level PNU, official price, acquisition, and registry evidence remain preliminary.",
                severity="warning",
            ))

        risk_rows_without_evidence = (
            evidence_graph.get("summary", {}).get("riskRowsWithoutExplicitEvidenceCount")
            if isinstance(evidence_graph, dict)
            else None
        )
        if isinstance(risk_rows_without_evidence, int) and risk_rows_without_evidence > 0:
            gates.append(self._approval_readiness_check(
                "RISK_SOURCE_LOCATORS_ATTACHED",
                False,
                f"{risk_rows_without_evidence} risk row(s) lack explicit evidence IDs or source locators.",
                severity="warning",
            ))

        if "UNSOLD_LTV_BREACH" in restructuring_blockers:
            gates.append(self._approval_readiness_check(
                "UNSOLD_LTV_RESTRUCTURING_REQUIRED",
                False,
                "Unsold LTV breach remains in restructuring plan; PF reduction, equity, collateral, forward-sale, bulk-sale, or refinancing evidence is required.",
                severity="warning",
            ))

        project_id = run.get("project_id") or run.get("projectId")
        deal_state_context = (
            build_deal_state_approval_readiness(
                str(project_id),
                tenant_id=(
                    str(
                        run.get("tenant_id")
                        or run.get("tenantId")
                        or ""
                    ).strip()
                    or None
                ),
            )
            if project_id
            else self._deal_state_approval_context(run)
        )
        gates.extend(deal_state_context.get("gates", []))

        blockers = [gate for gate in gates if not gate["pass"] and gate["severity"] == "blocking"]
        warnings = [gate for gate in gates if not gate["pass"] and gate["severity"] == "warning"]
        readiness_pass = not blockers
        return {
            "schemaVersion": "realytics-approval-readiness-gate-v1",
            "analysisType": "approval_readiness_gate",
            "decisionBasis": "deal_state_records",
            "pass": readiness_pass,
            "status": "ready_for_approval" if readiness_pass else "not_ready_for_approval",
            "gateType": "approval_readiness",
            "summary": {
                "blockingIssueCount": len(blockers),
                "warningCount": len(warnings),
                "unresolvedHandoffReviewCount": len(unresolved_handoffs),
                "unresolvedRiskReviewCount": len(unresolved_risks),
                "riskRegisterStatus": risk_register.get("status"),
                "creditDecision": credit_review.get("decision"),
                "legalStatus": legal_review.get("status"),
                "dataQualityStatus": data_quality.get("status"),
                "reconciliationStatus": reconciliation.get("status"),
                "landEvidenceReady": all(
                    bool(land_readiness.get(key))
                    for key in ["pnuReady", "officialPriceReady", "registryEvidenceReady", "acquisitionReady"]
                ),
                "dealStateRecordCount": deal_state_context["recordCount"],
                "dealStateRiskIssueCount": deal_state_context["riskIssueCount"],
                "dealStateBlockingRiskCount": deal_state_context["blockingRiskCount"],
                "dealStateEvidenceCount": deal_state_context["evidenceCount"],
                "dealStateMetricCount": deal_state_context["metricCount"],
                "dealStateAssumptionCount": deal_state_context["assumptionCount"],
                "dealStateOpenAssumptionCandidateCount": deal_state_context.get("openAssumptionCandidateCount", 0),
                "dealStateAutoSupplementAttemptCount": deal_state_context.get("autoSupplementAttemptCount", 0),
                "dealStateOpenAutoSupplementRiskCount": deal_state_context.get("openAutoSupplementRiskCount", 0),
                "dealStateOpenAutoSupplementGapCount": deal_state_context.get("openAutoSupplementGapCount", 0),
                "dealStateActiveAssumptionOverrideCount": deal_state_context["activeAssumptionOverrideCount"],
                "dealStateLatestCalculationRunCount": deal_state_context["latestCalculationRunCount"],
                "dealStateLatestMetricCount": deal_state_context["latestMetricCount"],
                "dealStateStaleMetricCount": deal_state_context["staleMetricCount"],
                "dealStateSourceLocatorMissingCount": deal_state_context["sourceLocatorMissingCount"],
                "dealStateOpenTraceabilityRiskCount": deal_state_context.get("openTraceabilityRiskCount", 0),
            },
            "dealStateApproval": deal_state_context,
            "gates": gates,
            "blockers": blockers,
            "warnings": warnings,
            "requiredActions": self._approval_readiness_actions(blockers, warnings),
        }

    def _approval_readiness_check(self, code: str, passed: bool, message: str, severity: str) -> Dict[str, Any]:
        return {
            "code": code,
            "pass": passed,
            "severity": severity,
            "message": message,
        }

    def _deal_state_approval_context(self, run: Dict[str, Any]) -> Dict[str, Any]:
        project_id = run.get("project_id") or run.get("projectId")
        if not project_id:
            return {
                "schemaVersion": "realytics-deal-state-approval-readiness-v1",
                "basis": "deal_state_records",
                "projectId": None,
                "readable": False,
                "recordCount": 0,
                "riskIssueCount": 0,
                "blockingRiskCount": 0,
                "evidenceCount": 0,
                "metricCount": 0,
                "assumptionCount": 0,
                "openAssumptionCandidateCount": 0,
                "autoSupplementAttemptCount": 0,
                "openAutoSupplementRiskCount": 0,
                "openAutoSupplementGapCount": 0,
                "activeAssumptionOverrideCount": 0,
                "latestCalculationRunCount": 0,
                "latestMetricCount": 0,
                "staleMetricCount": 0,
                "sourceLocatorMissingCount": 0,
                "openTraceabilityRiskCount": 0,
                "gates": [],
                "pass": False,
                "status": "not_ready",
                "blockers": [],
                "warnings": [],
                "requiredActions": [],
            }
        return build_deal_state_approval_readiness(
            str(project_id),
            tenant_id=(
                str(
                    run.get("tenant_id")
                    or run.get("tenantId")
                    or ""
                ).strip()
                or None
            ),
        )

    def _unresolved_handoff_reviews(self, run: Dict[str, Any]) -> List[Dict[str, Any]]:
        unresolved = []
        for handoff in run.get("handoffs", []):
            review_status = handoff.get("reviewStatus", "not_required")
            status = handoff.get("status")
            requires_review = bool(handoff.get("requiresHumanReview"))
            if review_status in {"rejected", "needs_revision"}:
                unresolved.append(handoff)
            elif requires_review and review_status != "approved":
                unresolved.append(handoff)
            elif status in {"blocked", "needs_review"} and review_status != "approved":
                unresolved.append(handoff)
        return unresolved

    def _unresolved_risk_reviews(self, run: Dict[str, Any]) -> List[Dict[str, Any]]:
        risk_register = run.get("artifacts", {}).get("riskRegister") or {}
        unresolved = []
        for risk in risk_register.get("risks", []):
            if not risk.get("requiresHumanReview"):
                continue
            review_status = risk.get("reviewStatus") or risk.get("status") or "open"
            if review_status not in {"approved", "waived", "resolved"}:
                unresolved.append(risk)
        return unresolved

    def _approval_readiness_actions(
        self,
        blockers: List[Dict[str, Any]],
        warnings: List[Dict[str, Any]],
    ) -> List[str]:
        action_by_code = {
            "REPORT_COMPLETENESS_PASS": "Fix missing report sections, missing artifacts, or PDF QA failures.",
            "LEGAL_NOT_BLOCKING": "Resolve legal and permit blocking issues or attach counsel-approved waiver.",
            "DATA_QUALITY_NOT_BLOCKING": "Resolve blocking data-quality conflicts or select an authoritative source.",
            "RECONCILIATION_NOT_BLOCKING": "Resolve cross-agent assumption conflicts before approval.",
            "RISK_REGISTER_NOT_BLOCKING": "Resolve or waive critical Risk Register rows before approval.",
            "CREDIT_DECISION_NOT_REJECT_OR_REWORK": "Rework the transaction or obtain a revised Credit Review decision.",
            "SENIOR_RESTRUCTURING_CLEARED": "Apply senior tranche reduction, DSRA/equity injection, or source-backed repayment support, then rerun Scenario Engine and Credit Review.",
            "HANDOFF_REVIEWS_RESOLVED": "Approve or revise unresolved Agent handoffs.",
            "RISK_REVIEWS_RESOLVED": "Approve, waive, or resolve material Risk Register rows.",
            "LAND_EVIDENCE_READY": "Attach parcel-level PNU, official land price, acquisition, and registry evidence.",
            "RISK_SOURCE_LOCATORS_ATTACHED": "Attach evidence IDs, document locators, Excel cells, API records, or scenario matrix locators to risk rows.",
            "UNSOLD_LTV_RESTRUCTURING_REQUIRED": "Reduce PF exposure, increase equity, add collateral, or attach forward-sale/bulk-sale/refinancing evidence for the unsold LTV cure.",
            "DEAL_STATE_RECORDS_READABLE": "Repair Deal State storage access before approval readiness can be evaluated.",
            "DEAL_STATE_RECORDS_PRESENT": "Run the required Agent Pods so metrics, risks, evidence, and assumptions are materialized into Deal State.",
            "DEAL_STATE_BLOCKING_RISKS_CLEARED": "Approve, waive, resolve, or cure blocking Deal State risk issues before approval.",
            "DEAL_STATE_REQUIRED_EVIDENCE_PRESENT": "Attach or auto-supplement source-backed evidence records before approval.",
            "DEAL_STATE_REPORT_GRADE_METRICS_PRESENT": "Run deterministic calculation engines so report-grade metrics are persisted.",
            "DEAL_STATE_CORE_METRICS_LATEST": "Rerun downstream calculations or refresh stale metrics so the approval gate uses the latest CalculationRun per engine.",
            "DEAL_STATE_ASSUMPTION_OVERRIDES_APPROVED": "Approve the relevant assumption or create an active assumption override for unresolved data-quality conflicts.",
            "DEAL_STATE_SOURCE_LOCATORS_ATTACHED": "Attach document page, Excel cell, API row, evidence ID, or Agent artifact locators to Deal State records.",
            "DEAL_STATE_SOURCE_LOCATOR_COMPLETENESS_SUFFICIENT": "Resolve SOURCE_LOCATOR_MISSING or EVIDENCE_TRACE_WEAK risks before approval.",
        }
        actions = [
            action_by_code.get(gate["code"], gate["message"])
            for gate in blockers + warnings
        ]
        return list(dict.fromkeys(actions))

    def _complete_step(
        self,
        run: Dict[str, Any],
        key: str,
        output: Dict[str, Any],
        artifact_key: str | None = None,
    ) -> None:
        title = self._step_title(key)
        output_status = str(output.get("status") or "").casefold()
        status = "failed" if output_status in {"failed", "error", "cancelled", "timed_out"} else (
            "skipped" if output_status in {"skipped", "not_run"} else "completed"
        )
        run["steps"].append({
            "key": key,
            "title": title,
            "status": status,
            "started_at": self._now(),
            "completed_at": self._now(),
            "output": output,
        })
        self._update_agent_progress(
            run,
            key,
            status=status,
            progress_percent=100,
            completed_tasks=[title] if status == "completed" else [],
            pending_tasks=[],
            last_artifact_key=artifact_key or key,
            next_action=self._next_action_after(key),
            blocked_tasks=[title] if status == "failed" else [],
            review_required=status == "failed",
        )

    def _add_handoff(
        self,
        run: Dict[str, Any],
        handoff_type: str,
        from_agent: str,
        to_agent: str,
        payload: Dict[str, Any],
        payload_schema: str | None = None,
        evidence_ids: List[str] | None = None,
        confidence: float | None = None,
        blocking_issues: List[Any] | None = None,
        assumptions: List[str] | None = None,
        data_limitations: List[Any] | None = None,
        requires_human_review: bool = False,
        progress_impact: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized_blockers = [self._stringify_issue(issue) for issue in (blocking_issues or [])]
        normalized_limitations = [self._stringify_issue(issue) for issue in (data_limitations or [])]
        handoff = AgentHandoffEnvelope(
            handoffId=str(uuid4()),
            runId=run["run_id"],
            workflow=run["workflow"],
            handoffType=handoff_type,
            fromAgent=from_agent,
            toAgent=to_agent,
            status="needs_review" if normalized_blockers or requires_human_review else "created",
            payloadSchema=payload_schema,
            payload=payload or {},
            evidenceIds=sorted(set(evidence_ids or [])),
            confidence=self._normalize_confidence(confidence),
            blockingIssues=normalized_blockers,
            assumptions=assumptions or [],
            dataLimitations=normalized_limitations,
            requiresHumanReview=requires_human_review,
            reviewStatus="pending" if requires_human_review else "not_required",
            createdAt=self._now(),
            progressImpact=progress_impact or {},
        ).model_dump(by_alias=True, exclude_none=True)
        run["handoffs"].append(handoff)
        self._attach_handoff_to_progress(run, from_agent, handoff["handoffId"])
        self._attach_handoff_to_progress(run, to_agent, handoff["handoffId"])
        return handoff

    def _initial_agent_progress(self, timestamp: str) -> Dict[str, Dict[str, Any]]:
        return {
            key: AgentProgress(
                agentKey=key,
                agentName=self._agent_name_for_step(key),
                status="pending",
                progressPercent=0,
                completedTasks=[],
                pendingTasks=[title],
                blockedTasks=[],
                dependsOn=self._depends_on_for_step(key),
                downstreamDependencies=self._downstream_for_step(key),
                nextAction=f"Waiting to run {title}.",
                updatedAt=timestamp,
            ).model_dump(by_alias=True, exclude_none=True)
            for key, title in PF_REVIEW_STEPS
        }

    def _update_agent_progress(
        self,
        run: Dict[str, Any],
        key: str,
        status: str,
        progress_percent: int,
        completed_tasks: List[str],
        pending_tasks: List[str],
        last_artifact_key: str | None = None,
        next_action: str | None = None,
        blocked_tasks: List[str] | None = None,
        review_required: bool = False,
    ) -> None:
        current = run["agentProgress"].get(key, {})
        existing_handoffs = current.get("handoffIds", [])
        run["agentProgress"][key] = AgentProgress(
            agentKey=key,
            agentName=current.get("agentName") or self._agent_name_for_step(key),
            status=status,
            progressPercent=progress_percent,
            completedTasks=completed_tasks,
            pendingTasks=pending_tasks,
            blockedTasks=blocked_tasks or [],
            dependsOn=current.get("dependsOn", self._depends_on_for_step(key)),
            downstreamDependencies=current.get("downstreamDependencies", self._downstream_for_step(key)),
            lastArtifactKey=last_artifact_key,
            nextAction=next_action,
            reviewRequired=review_required or bool(current.get("reviewRequired")),
            handoffIds=existing_handoffs,
            updatedAt=self._now(),
        ).model_dump(by_alias=True, exclude_none=True)

    def _attach_handoff_to_progress(self, run: Dict[str, Any], agent_name: str, handoff_id: str) -> None:
        key = self._step_key_for_agent(agent_name)
        if not key or key not in run["agentProgress"]:
            return
        progress = dict(run["agentProgress"][key])
        progress["handoffIds"] = sorted(set(progress.get("handoffIds", []) + [handoff_id]))
        if any(handoff.get("handoffId") == handoff_id and handoff.get("requiresHumanReview") for handoff in run["handoffs"]):
            progress["reviewRequired"] = True
        progress["updatedAt"] = self._now()
        run["agentProgress"][key] = progress

    def _mark_failed_progress(self, run: Dict[str, Any], message: str) -> None:
        for key, progress in run.get("agentProgress", {}).items():
            if progress.get("status") in {"pending", "running"}:
                self._update_agent_progress(
                    run,
                    key,
                    status="failed",
                    progress_percent=progress.get("progressPercent", 0),
                    completed_tasks=progress.get("completedTasks", []),
                    pending_tasks=progress.get("pendingTasks", []),
                    blocked_tasks=[message],
                    next_action="Resolve orchestration failure before rerun.",
                    review_required=True,
                )

    def _deep_merge(self, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    def _sanitize_options(self, options: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = dict(options)
        if isinstance(sanitized.get("ai_context"), dict):
            sanitized["ai_context"] = safe_ai_context(sanitized["ai_context"])
        return sanitized

    def _reconcile_agent_outputs(
        self,
        *,
        project_data: Dict[str, Any],
        land_review: Dict[str, Any],
        developer_feasibility: Dict[str, Any],
        feasibility: Dict[str, Any],
        pf_structure: Dict[str, Any],
        market_evidence: Dict[str, Any],
        marketability: Dict[str, Any],
    ) -> Dict[str, Any]:
        checks = [
            self._reconcile_numeric_check(
                key="revenue_feasibility_vs_marketability",
                label="Feasibility revenue vs Marketability revenue",
                left_source="feasibility_input.total_revenue",
                left_value=project_data.get("feasibility_input", {}).get("total_revenue"),
                right_source="marketability.projectSummary.totalSalesRevenue",
                right_value=self._marketability_total_revenue(marketability),
                warning_threshold=0.05,
                blocking_threshold=0.10,
                unit="KRW",
            ),
            self._reconcile_numeric_check(
                key="land_area_project_vs_land_agent",
                label="Project land area vs Land Agent parcel area",
                left_source="project.land.area_sqm",
                left_value=self._project_land_area(project_data),
                right_source="landAgentReview.summary.totalAreaSqm",
                right_value=land_review.get("summary", {}).get("totalAreaSqm"),
                warning_threshold=0.01,
                blocking_threshold=0.03,
                unit="sqm",
            ),
            self._readiness_check(
                key="official_price_evidence_ready",
                label="Official land price evidence readiness",
                ready=land_review.get("evidenceReadiness", {}).get("officialPriceReady"),
                severity="warning",
                message="Official land price evidence is incomplete or not attached.",
            ),
            self._readiness_check(
                key="registry_evidence_ready",
                label="Registry and encumbrance evidence readiness",
                ready=land_review.get("evidenceReadiness", {}).get("registryEvidenceReady"),
                severity="warning",
                message="Registry and encumbrance evidence is incomplete or not attached.",
            ),
            self._readiness_check(
                key="market_evidence_ready",
                label="Market evidence readiness",
                ready=market_evidence.get("status") not in {"needs_review", "insufficient", "missing_required_data"},
                severity="warning",
                message="Market evidence still has issues or missing required comparable data.",
            ),
            self._readiness_check(
                key="pf_structure_metrics_ready",
                label="PF structure metrics readiness",
                ready=all(
                    isinstance(pf_structure.get(key), (int, float))
                    for key in ["ltv", "ltc", "exit_sale_rate"]
                ),
                severity="blocking",
                message="PF structure metrics are missing and cannot be reconciled with feasibility outputs.",
            ),
        ]
        checks = [check for check in checks if check]
        blocking = [check for check in checks if check["status"] == "blocking"]
        warnings = [check for check in checks if check["status"] == "warning"]
        status = "blocking" if blocking else "needs_review" if warnings else "clear"
        return {
            "analysisType": "cross_agent_reconciliation_gate_v1",
            "status": status,
            "summary": {
                "checkCount": len(checks),
                "blockingCount": len(blocking),
                "warningCount": len(warnings),
                "clearCount": sum(1 for check in checks if check["status"] == "clear"),
            },
            "checks": checks,
            "nextActions": self._reconciliation_next_actions(blocking, warnings),
            "sourceArtifacts": {
                "landAgentReview": land_review.get("analysisType"),
                "developerFeasibility": developer_feasibility.get("analysisType"),
                "feasibility": "core_feasibility_metrics",
                "marketEvidenceReview": market_evidence.get("analysisType"),
                "marketabilityAnalysis": marketability.get("analysisType"),
                "dataQualityReview": "data_quality_review",
            },
        }

    def _reconcile_numeric_check(
        self,
        *,
        key: str,
        label: str,
        left_source: str,
        left_value: Any,
        right_source: str,
        right_value: Any,
        warning_threshold: float,
        blocking_threshold: float,
        unit: str,
    ) -> Dict[str, Any] | None:
        if not isinstance(left_value, (int, float)) or not isinstance(right_value, (int, float)):
            return {
                "key": key,
                "label": label,
                "status": "warning",
                "severity": "warning",
                "message": f"{label} cannot be fully reconciled because one side is missing.",
                "leftSource": left_source,
                "leftValue": left_value,
                "rightSource": right_source,
                "rightValue": right_value,
                "unit": unit,
            }
        denominator = max(abs(float(left_value)), abs(float(right_value)), 1.0)
        diff = float(right_value) - float(left_value)
        diff_ratio = abs(diff) / denominator
        if diff_ratio >= blocking_threshold:
            status = "blocking"
        elif diff_ratio >= warning_threshold:
            status = "warning"
        else:
            status = "clear"
        return {
            "key": key,
            "label": label,
            "status": status,
            "severity": status,
            "message": (
                f"{label} diff ratio is {diff_ratio:.2%}."
                if status != "clear"
                else f"{label} is within tolerance."
            ),
            "leftSource": left_source,
            "leftValue": left_value,
            "rightSource": right_source,
            "rightValue": right_value,
            "diff": diff,
            "diffRatio": diff_ratio,
            "warningThreshold": warning_threshold,
            "blockingThreshold": blocking_threshold,
            "unit": unit,
        }

    def _readiness_check(
        self,
        *,
        key: str,
        label: str,
        ready: Any,
        severity: str,
        message: str,
    ) -> Dict[str, Any]:
        is_ready = bool(ready)
        return {
            "key": key,
            "label": label,
            "status": "clear" if is_ready else severity,
            "severity": "clear" if is_ready else severity,
            "message": f"{label} is ready." if is_ready else message,
            "ready": is_ready,
        }

    def _marketability_total_revenue(self, marketability: Dict[str, Any]) -> float | None:
        product_mix = marketability.get("productMix", {})
        if isinstance(product_mix, dict) and isinstance(product_mix.get("totalSalesRevenue"), (int, float)):
            return product_mix["totalSalesRevenue"]
        project_summary = marketability.get("projectSummary", {})
        if isinstance(project_summary.get("totalSalesRevenue"), (int, float)):
            return project_summary["totalSalesRevenue"]
        return None

    def _project_land_area(self, project_data: Dict[str, Any]) -> float | None:
        land = project_data.get("land") or {}
        for key in ["area_sqm", "land_area_sqm", "landAreaSqm"]:
            value = land.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        site = project_data.get("site") or {}
        for key in ["landAreaSqm", "areaSqm"]:
            value = site.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    def _reconciliation_next_actions(
        self,
        blocking: List[Dict[str, Any]],
        warnings: List[Dict[str, Any]],
    ) -> List[str]:
        if blocking:
            return [
                "Resolve blocking cross-agent mismatches before final PF review approval.",
                "Rerun affected upstream agents or approve an explicit assumption override.",
            ]
        if warnings:
            return [
                "Review reconciliation warnings and document accepted assumptions before approval.",
                "Attach missing evidence or keep the limitation visible in the Phase 11 report.",
            ]
        return ["No cross-agent reconciliation action required."]

    def _marketability_feasibility_patch(self, marketability: Dict[str, Any]) -> Dict[str, Any]:
        pricing = marketability.get("pricing", {})
        product_mix = marketability.get("productMix", {})
        product_mix_summary = product_mix if isinstance(product_mix, dict) else {}
        absorption = marketability.get("absorptionForecast", {})
        exit_sale_rate = marketability.get("exitSaleRate", {})
        return {
            "revenueAssumptionPatch": {
                "weightedAveragePricePerPy": product_mix_summary.get("weightedAveragePricePerPy")
                or pricing.get("proposedPricePerPy"),
                "totalSalesRevenue": product_mix_summary.get("totalSalesRevenue")
                or marketability.get("projectSummary", {}).get("totalSalesRevenue"),
                "pricingJudgement": pricing.get("judgement"),
                "premiumToComparableMedian": pricing.get("premiumToMedian"),
                "evidenceIds": pricing.get("evidenceIds", []),
            },
            "absorptionScenarioPatch": {
                "baseCompletionSalesRate": absorption.get("completion")
                or absorption.get("baseCase", {}).get("completionSalesRate"),
                "downsideCompletionSalesRate": absorption.get("downsideCase", {}).get("completionSalesRate"),
                "stressCompletionSalesRate": absorption.get("stressCase", {}).get("completionSalesRate"),
            },
            "discountScenarioPatch": exit_sale_rate.get("discountScenarios")
            or {
                "base": exit_sale_rate.get("base"),
                "discount5": exit_sale_rate.get("discount5"),
                "discount10": exit_sale_rate.get("discount10"),
                "discount15": exit_sale_rate.get("discount15"),
                "discount20": exit_sale_rate.get("discount20"),
            },
            "discountTrigger": marketability.get("discountTrigger", []),
            "riskFlags": marketability.get("riskFlags", []),
            "reportParagraphs": marketability.get("reportParagraphs", {}),
        }

    def _marketability_confidence(self, marketability: Dict[str, Any]) -> float | None:
        source_metadata = marketability.get("sourceMetadata") or marketability.get("dataSources") or []
        confidences = [
            item.get("confidence")
            for item in source_metadata
            if isinstance(item, dict) and isinstance(item.get("confidence"), (int, float))
        ]
        if confidences:
            return sum(confidences) / len(confidences)
        score = marketability.get("score")
        if isinstance(score, (int, float)):
            return max(0.0, min(1.0, score / 100))
        return None

    def _marketability_blockers(self, marketability: Dict[str, Any]) -> List[str]:
        blockers: List[str] = []
        if marketability.get("dataStatus") in {"missing_required_data", "insufficient", "partial"}:
            blockers.append(f"Marketability data status is {marketability.get('dataStatus')}.")
        blockers.extend(self._stringify_issue(flag) for flag in marketability.get("riskFlags", []) if self._is_blocking_risk(flag))
        return blockers

    def _is_blocking_risk(self, flag: Any) -> bool:
        if isinstance(flag, str):
            return flag in {
                "NO_COMPARABLE_DATA",
                "PROPOSED_PRICE_BASIS_MISSING",
                "PRICE_PREMIUM_EXCESSIVE",
                "EXIT_RATE_HIGH",
            }
        if isinstance(flag, dict):
            severity = str(flag.get("severity", "")).lower()
            code = flag.get("code")
            return severity in {"critical", "high"} or code in {
                "NO_COMPARABLE_DATA",
                "PROPOSED_PRICE_BASIS_MISSING",
                "PRICE_PREMIUM_EXCESSIVE",
                "EXIT_RATE_HIGH",
            }
        return False

    def _collect_evidence_ids(self, value: Any) -> List[str]:
        evidence_ids: List[str] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                for key, item in node.items():
                    if key in {"evidenceId", "evidence_id", "sourceUrlOrEvidenceId"} and isinstance(item, str):
                        evidence_ids.append(item)
                    elif key in {"evidenceIds", "evidence_ids"} and isinstance(item, list):
                        evidence_ids.extend(str(entry) for entry in item if entry)
                    else:
                        visit(item)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(value)
        return sorted(set(evidence_ids))

    def _normalize_confidence(self, confidence: Any) -> float | None:
        if not isinstance(confidence, (int, float)):
            return None
        return max(0.0, min(1.0, float(confidence)))

    def _stringify_issue(self, issue: Any) -> str:
        if isinstance(issue, str):
            return issue
        if isinstance(issue, dict):
            code = issue.get("code") or issue.get("field") or issue.get("type")
            message = issue.get("message") or issue.get("description") or issue.get("finding")
            if code and message:
                return f"{code}: {message}"
            if message:
                return str(message)
            if code:
                return str(code)
        return str(issue)

    def _agent_name_for_step(self, key: str) -> str:
        return {
            "project_load": "ProjectLoader",
            "term_annotation": "TermAnnotationAgent",
            "land_agent_review": "LandAgent",
            "development_feasibility_analysis": "DevelopmentFeasibilityAgent",
            "feasibility_calculation": "FeasibilityCalculator",
            "pf_structure_calculation": "PFStructuringCalculator",
            "return_metrics": "ReturnMetricsEngine",
            "stress_test": "StressTestAgent",
            "credit_enhancement_review": "CreditEnhancementAgent",
            "cash_sweep_simulation": "CashSweepAgent",
            "market_evidence_reconciliation": "MarketEvidenceAgent",
            "marketability_analysis": "MarketabilityAgent",
            "data_quality_review": "DataQualityAgent",
            "cross_agent_reconciliation": "ReconciliationGate",
            "legal_permit_review": "LegalReviewAgent",
            "risk_assessment": "RiskAssessmentEngine",
            "risk_register_generation": "RiskRegisterAgent",
            "credit_review_decision": "CreditReviewAgent",
            "scenario_restructuring_plan": "RestructuringAgent",
            "phase11_report_generation": "ReportAgent",
            "evidence_graph_generation": "EvidenceGraphAgent",
            "quality_check": "OrchestrationQualityGate",
            "approval_readiness_gate": "ApprovalReadinessGate",
        }.get(key, key)

    def _step_key_for_agent(self, agent_name: str) -> str | None:
        return {
            "ProjectLoader": "project_load",
            "LandAgent": "land_agent_review",
            "DevelopmentFeasibilityAgent": "development_feasibility_analysis",
            "CashFlowEngine": "development_feasibility_analysis",
            "CalculationScenarioEngine": "pf_structure_calculation",
            "FeasibilityCalculator": "feasibility_calculation",
            "PFStructuringCalculator": "pf_structure_calculation",
            "MarketEvidenceAgent": "market_evidence_reconciliation",
            "MarketabilityAgent": "marketability_analysis",
            "DataQualityAgent": "data_quality_review",
            "ReconciliationGate": "cross_agent_reconciliation",
            "LegalReviewAgent": "legal_permit_review",
            "CreditReviewAgent": "credit_review_decision",
            "RestructuringAgent": "scenario_restructuring_plan",
            "RiskAssessmentEngine": "risk_assessment",
            "RiskRegisterAgent": "risk_register_generation",
            "ReportAgent": "phase11_report_generation",
            "EvidenceGraphAgent": "evidence_graph_generation",
            "OrchestrationQualityGate": "quality_check",
            "ApprovalReadinessGate": "approval_readiness_gate",
        }.get(agent_name)

    def _depends_on_for_step(self, key: str) -> List[str]:
        order = [step_key for step_key, _ in PF_REVIEW_STEPS]
        if key not in order:
            return []
        index = order.index(key)
        return [order[index - 1]] if index > 0 else []

    def _downstream_for_step(self, key: str) -> List[str]:
        order = [step_key for step_key, _ in PF_REVIEW_STEPS]
        if key not in order:
            return []
        index = order.index(key)
        return [order[index + 1]] if index + 1 < len(order) else []

    def _next_action_after(self, key: str) -> str:
        downstream = self._downstream_for_step(key)
        if not downstream:
            return "Workflow complete; ready for human approval if quality gates pass."
        return f"Next dependency: {self._step_title(downstream[0])}."

    def _step_title(self, key: str) -> str:
        titles = dict(PF_REVIEW_STEPS)
        return titles.get(key, key)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
