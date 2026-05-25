from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.ai_connection import resolve_ai_context, safe_ai_context
from agents.credit_review_agent import CreditReviewAgent
from agents.data_quality_agent import DataQualityAgent
from agents.evidence_graph_agent import EvidenceGraphAgent
from agents.legal_review_agent import LegalReviewAgent
from agents.market_evidence_agent import MarketEvidenceAgent
from agents.marketability_agent import MarketabilityAgent
from agents.report_agent import ReportAgent
from agents.risk_register_agent import RiskRegisterAgent
from core.calculations import (
    calculate_feasibility_metrics,
    calculate_pf_metrics,
)
from core.risk_assessment import build_basic_risk_matrix
from schemas.orchestration import AgentHandoffEnvelope, AgentProgress, PF_REVIEW_STEPS, PF_REVIEW_WORKFLOW


class OrchestrationAgent:
    def run_pf_review(
        self,
        project_id: str,
        project_data: Dict[str, Any],
        options: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        options = options or {}
        ai_connection = resolve_ai_context(
            options.get("ai_context"),
            user_id=options.get("user_id") or options.get("requested_by"),
            host=options.get("host") or "api",
        )
        now = self._now()
        run = {
            "run_id": str(uuid4()),
            "project_id": project_id,
            "workflow": PF_REVIEW_WORKFLOW,
            "status": "running",
            "started_at": now,
            "completed_at": None,
            "user_id": ai_connection["user_id"],
            "ai_connection": ai_connection,
            "options": self._sanitize_options(options),
            "steps": [],
            "agentProgress": self._initial_agent_progress(now),
            "handoffs": [],
            "artifacts": {},
            "errors": [],
            "approval": None,
        }

        try:
            self._complete_step(run, "project_load", {
                "project": project_data.get("project", {}),
                "source": "PROJECTS_DB",
            }, artifact_key="project")

            land_review = self._review_land_readiness(project_data)
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

            developer_feasibility = DevelopmentFeasibilityAgent().analyze(project_data)
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
            try:
                from schemas.scenario import ScenarioSetRequest
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
                if not product_mix:
                    product_mix.append({
                        "type": "General",
                        "units": 100,
                        "avgUnitPrice": (project_data.get("feasibility_input", {}).get("total_revenue", 0.0) / 100.0) if project_data.get("feasibility_input", {}).get("total_revenue") else 1000000.0,
                        "totalRevenue": project_data.get("feasibility_input", {}).get("total_revenue") or 100000000.0
                    })

                tranches = project_data.get("pf_structure", [])
                pf_structure_list = []
                for idx, item in enumerate(tranches):
                    if isinstance(item, dict):
                        pf_structure_list.append({
                            "tranche": item.get("tranche") or item.get("name") or f"Tr.{idx+1}",
                            "amount": float(item.get("amount") or 0.0),
                            "priority": int(item.get("priority") or idx + 1),
                            "interestRate": float(item.get("interest_rate") or item.get("interestRate") or 0.0),
                            "feeRate": float(item.get("fee_rate") or item.get("feeRate") or 0.0)
                        })
                if not pf_structure_list:
                    pf_structure_list.append({
                        "tranche": "Senior",
                        "amount": sum(t.get("amount", 0) for t in tranches) or 10000000.0,
                        "priority": 1,
                        "interestRate": 0.065,
                        "feeRate": 0.01
                    })

                scenario_req = ScenarioSetRequest(
                    scenarioSetId=f"run-{run['run_id']}",
                    scenarioSetName="Orchestration Run Scenario Set",
                    productMix=product_mix,
                    pfStructure=pf_structure_list
                )
                scenario_run_result = ScenarioEngineAgent().run_scenario_set(scenario_req)
                run["artifacts"]["scenariosEvaluation"] = scenario_run_result.model_dump(by_alias=True)

                # Include scenario fragments directly in the pf_structure artifact
                pf_structure["scenariosSummary"] = scenario_run_result.reportFragments.waterfallSummary
                pf_structure["unsoldLtvSummary"] = scenario_run_result.reportFragments.unsoldLtvSummary
                pf_structure["trancheRecoverySummary"] = scenario_run_result.reportFragments.trancheRecoverySummary
            except Exception as exc:
                run["errors"].append({"message": f"Scenario calculation failed: {str(exc)}", "type": exc.__class__.__name__})

            run["artifacts"]["pfStructure"] = pf_structure
            self._complete_step(run, "pf_structure_calculation", pf_structure, artifact_key="pfStructure")

            market_evidence = MarketEvidenceAgent().review(
                project_data,
                developer_feasibility,
                feasibility,
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
            marketability = MarketabilityAgent().analyze(marketability_input)
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

            data_quality = DataQualityAgent().review(
                project_data,
                {
                    "landAgentReview": land_review,
                    "developerFeasibility": developer_feasibility,
                    "feasibility": feasibility,
                    "pfStructure": pf_structure,
                    "marketEvidenceReview": market_evidence,
                    "marketabilityAnalysis": marketability,
                },
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

            legal_review = LegalReviewAgent().review(
                project_data,
                options.get("law_context"),
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
                    "unblocks": ["risk_assessment", "quality_check"],
                    "downstreamAgents": ["RiskAssessmentEngine", "CreditReviewAgent"],
                },
            )

            risk = self._assess_risk(project_data)
            run["artifacts"]["riskAssessment"] = risk
            self._complete_step(run, "risk_assessment", risk, artifact_key="riskAssessment")

            risk_register = RiskRegisterAgent().build(project_data, run["artifacts"])
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

            credit_review = CreditReviewAgent().decide(project_data, run["artifacts"])
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

            report_agent = ReportAgent()
            structured_report = report_agent.generate_structured_report(project_data)
            report_markdown = report_agent.generate_report(project_data)
            report_artifact = {
                "report": structured_report,
                "report_markdown": report_markdown,
            }
            run["artifacts"]["phase11Report"] = report_artifact
            self._complete_step(run, "phase11_report_generation", {
                "template": structured_report["template"],
                "sectionCount": structured_report["qualityChecks"]["actualSectionCount"],
            }, artifact_key="phase11Report")

            evidence_graph = EvidenceGraphAgent().build(project_data, run)
            run["artifacts"]["evidenceGraph"] = evidence_graph
            self._complete_step(run, "evidence_graph_generation", {
                "schemaVersion": evidence_graph["schemaVersion"],
                "summary": evidence_graph["summary"],
                "auditWarnings": evidence_graph["auditWarnings"],
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
                    "title": "AI PF 심사보고서",
                    "report": structured_report,
                    "report_markdown": report_markdown,
                }
                pdf_bytes = export_agent._render_pdf(report_record)

                qa_agent = VisualQAAgent()
                structure_results = qa_agent.analyze_pdf_structure(pdf_bytes)
                visual_results = qa_agent.run_visual_regression(pdf_bytes, report_id=f"report-{run['run_id']}")
            except Exception as qa_exc:
                run["errors"].append({
                    "message": f"Visual QA execution failed: {str(qa_exc)}",
                    "type": qa_exc.__class__.__name__
                })

            quality = self._quality_check(run, structure_results, visual_results)
            run["artifacts"]["qualityCheck"] = quality
            self._complete_step(run, "quality_check", quality, artifact_key="qualityCheck")

            run["status"] = "completed" if quality["pass"] else "completed_with_warnings"
        except Exception as exc:
            run["status"] = "failed"
            run["errors"].append({"message": str(exc), "type": exc.__class__.__name__})
            self._mark_failed_progress(run, str(exc))

        run["completed_at"] = self._now()
        return run

    def _review_land_readiness(self, data: Dict[str, Any]) -> Dict[str, Any]:
        land_ledger = data.get("landLedger") or data.get("land_ledger") or {}
        land = data.get("land") or data.get("site") or {}
        project = data.get("project") or {}

        if not isinstance(land_ledger, dict) or not land_ledger:
            pnu = land.get("pnu") or data.get("pnu")
            official_price = land.get("official_land_price") or land.get("officialLandPrice")
            status = "basic_site_data_only" if pnu or official_price else "land_ledger_not_provided"
            limitations = [
                "Land ledger import is not attached to this orchestration run.",
                "Parcel-level owner, acquisition, official price, and registry evidence cannot be validated yet.",
            ]
            if pnu:
                limitations = limitations[1:]
            return {
                "analysisType": "land_agent_orchestration_review_v1",
                "status": status,
                "summary": {
                    "projectName": project.get("name"),
                    "pnu": pnu,
                    "officialLandPrice": official_price,
                    "parcelCount": 0,
                    "totalAreaSqm": land.get("land_area_sqm") or land.get("landAreaSqm"),
                    "totalPurchaseAmount": None,
                    "pnuCoverageRatio": 1.0 if pnu else 0.0,
                    "officialLandPriceCoverageRatio": 1.0 if official_price else 0.0,
                    "registryEvidenceCoverageRatio": 0.0,
                },
                "landLedgerStatus": status,
                "landAcquisitionStatus": {},
                "pfSummary": {},
                "evidenceReadiness": {
                    "pnuReady": bool(pnu),
                    "officialPriceReady": bool(official_price),
                    "registryEvidenceReady": False,
                    "acquisitionReady": False,
                    "sourceDataReady": False,
                },
                "blockingIssues": [],
                "dataLimitations": limitations,
                "requiresHumanReview": False,
                "confidence": 0.35 if pnu or official_price else 0.15,
                "handoffReadiness": "limited_patch",
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
            "sourceMetadata": land_ledger.get("importMetadata", {}),
            "reviewMemo": pf_summary.get("reviewMemo"),
        }

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
            "phase11Report",
            "evidenceGraph",
        ]
        missing_artifacts = [
            artifact for artifact in required_artifacts if artifact not in run["artifacts"]
        ]
        legal_status = run["artifacts"]["legalPermitReview"]["status"]
        reconciliation_status = run["artifacts"]["reconciliationGate"]["status"]
        data_quality_status = run["artifacts"]["dataQualityReview"]["status"]
        credit_review_status = run["artifacts"]["creditReviewDecision"]["status"]

        pdf_structure_pass = True
        pdf_visual_pass = True
        if structure_results:
            pdf_structure_pass = structure_results.get("status") != "failed"
        if visual_results:
            pdf_visual_pass = visual_results.get("status") != "failed"

        is_passed = (
            report_quality["isComplete"]
            and not missing_artifacts
            and legal_status != "blocking_issue"
            and reconciliation_status != "blocking"
            and data_quality_status != "blocking"
            and pdf_structure_pass
            and pdf_visual_pass
        )

        return {
            "pass": is_passed,
            "reportComplete": report_quality["isComplete"],
            "legalReviewStatus": legal_status,
            "dataQualityStatus": data_quality_status,
            "creditReviewStatus": credit_review_status,
            "reconciliationStatus": reconciliation_status,
            "missingReportSections": report_quality["missingSections"],
            "missingArtifacts": missing_artifacts,
            "pdfStructureQA": structure_results or {"status": "skipped", "reason": "No PDF rendered"},
            "pdfVisualQA": visual_results or {"status": "skipped", "reason": "No PDF rendered"},
        }

    def _complete_step(
        self,
        run: Dict[str, Any],
        key: str,
        output: Dict[str, Any],
        artifact_key: str | None = None,
    ) -> None:
        title = self._step_title(key)
        run["steps"].append({
            "key": key,
            "title": title,
            "status": "completed",
            "started_at": self._now(),
            "completed_at": self._now(),
            "output": output,
        })
        self._update_agent_progress(
            run,
            key,
            status="completed",
            progress_percent=100,
            completed_tasks=[title],
            pending_tasks=[],
            last_artifact_key=artifact_key or key,
            next_action=self._next_action_after(key),
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
            reviewRequired=review_required,
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
            return flag in {"NO_COMPARABLE_DATA", "PRICE_PREMIUM_EXCESSIVE", "EXIT_RATE_HIGH"}
        if isinstance(flag, dict):
            severity = str(flag.get("severity", "")).lower()
            code = flag.get("code")
            return severity in {"critical", "high"} or code in {
                "NO_COMPARABLE_DATA",
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
            "land_agent_review": "LandAgent",
            "development_feasibility_analysis": "DevelopmentFeasibilityAgent",
            "feasibility_calculation": "FeasibilityCalculator",
            "pf_structure_calculation": "PFStructuringCalculator",
            "market_evidence_reconciliation": "MarketEvidenceAgent",
            "marketability_analysis": "MarketabilityAgent",
            "data_quality_review": "DataQualityAgent",
            "cross_agent_reconciliation": "ReconciliationGate",
            "legal_permit_review": "LegalReviewAgent",
            "risk_assessment": "RiskAssessmentEngine",
            "risk_register_generation": "RiskRegisterAgent",
            "credit_review_decision": "CreditReviewAgent",
            "phase11_report_generation": "ReportAgent",
            "evidence_graph_generation": "EvidenceGraphAgent",
            "quality_check": "OrchestrationQualityGate",
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
            "RiskAssessmentEngine": "risk_assessment",
            "RiskRegisterAgent": "risk_register_generation",
            "ReportAgent": "phase11_report_generation",
            "EvidenceGraphAgent": "evidence_graph_generation",
            "OrchestrationQualityGate": "quality_check",
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
