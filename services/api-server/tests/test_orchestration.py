import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.orchestration_agent import OrchestrationAgent
from main import app
from schemas.orchestration import (
    AgentHandoffEnvelope,
    AgentProgress,
    ChiefDealOrchestrationPlan,
    PF_REVIEW_STEPS,
    PF_REVIEW_WORKFLOW,
)
from storage import (
    upsert_calculation_run_record,
    upsert_deal_state_record,
    upsert_orchestration_run,
)


_DISCOUNT_TRIGGER_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "data" / "examples" / "daegu_bondong_mixed_use_pf.json"
)


def project_with_discount_triggers(client: TestClient) -> str:
    """A project id whose covenants carry discount triggers.

    Always seed an isolated fixture project. Reusing a developer runtime project
    made tests parse real uploads, inherit accumulated risks, and mutate the
    user's deal state while claiming to be hermetic.
    """
    with _DISCOUNT_TRIGGER_EXAMPLE.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    payload.setdefault("project", {})["name"] = "Pytest Discount Trigger Fixture"
    created = client.post("/projects/", json=payload).json()
    return created.get("id") or created["project"]["id"]


def _approval_ready_base_run(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "handoffs": [],
        "artifacts": {
            "qualityCheck": {"pass": True},
            "legalPermitReview": {"status": "clear"},
            "dataQualityReview": {"status": "clear"},
            "reconciliationGate": {"status": "clear"},
            "riskRegister": {"status": "clear", "risks": []},
            "creditReviewDecision": {"decision": "conditional_approval"},
            "landAgentReview": {
                "status": "ready_for_pf_review",
                "evidenceReadiness": {
                    "pnuReady": True,
                    "officialPriceReady": True,
                    "registryEvidenceReady": True,
                    "acquisitionReady": True,
                },
            },
            "evidenceGraph": {"summary": {"riskRowsWithoutExplicitEvidenceCount": 0}},
        },
    }


def test_pf_review_orchestration_completes_and_saves_report(monkeypatch):
    # Force the deterministic consensus coordinator so this test verifies the
    # deterministic path regardless of any AI provider key in the dev .env
    # (with a key present the orchestrator legitimately picks the LLM coordinator).
    monkeypatch.setattr("agents.orchestration_agent.select_ai_context", lambda **kwargs: None)
    client = TestClient(app)
    project_id = project_with_discount_triggers(client)

    res = client.post(
        f"/projects/{project_id}/orchestrations/pf-review",
        json={"options": {"requested_by": "pytest"}},
    )

    assert res.status_code == 200
    run = res.json()
    assert run["workflow"] == PF_REVIEW_WORKFLOW
    assert run["status"] == "completed"
    assert run["user_id"] == "pytest"
    assert run["ai_connection"]["user_id"] == "pytest"
    assert len(run["steps"]) == len(PF_REVIEW_STEPS)
    assert len(run["handoffs"]) >= 8
    assert set(run["agentProgress"]) == {key for key, _ in PF_REVIEW_STEPS}
    assert run["agentProgress"]["marketability_analysis"]["status"] == "completed"
    assert run["agentProgress"]["marketability_analysis"]["progressPercent"] == 100
    assert run["agentProgress"]["quality_check"]["lastArtifactKey"] == "qualityCheck"
    assert run["agentProgress"]["approval_readiness_gate"]["lastArtifactKey"] == "approvalReadinessGate"
    assert {
        handoff["handoffType"]
        for handoff in run["handoffs"]
    } >= {
        "development_feasibility_to_cash_flow",
        "land_to_feasibility_legal_pf",
        "market_evidence_to_marketability",
        "marketability_to_feasibility",
        "legal_to_credit_review",
        "risk_register_to_credit_review",
        "credit_review_to_report",
        "restructuring_to_credit_review",
    }
    AgentHandoffEnvelope.model_validate(run["handoffs"][0])
    AgentProgress.model_validate(run["agentProgress"]["marketability_analysis"])
    assert run["artifacts"]["qualityCheck"]["pass"] is True
    assert run["artifacts"]["qualityCheck"]["gateType"] == "report_completeness"
    assert run["artifacts"]["approvalReadinessGate"]["analysisType"] == "approval_readiness_gate"
    assert run["artifacts"]["approvalReadinessGate"]["gateType"] == "approval_readiness"
    assert isinstance(run["artifacts"]["approvalReadinessGate"]["pass"], bool)
    assert run["artifacts"]["landAgentReview"]["analysisType"] == "land_agent_orchestration_review_v1"
    assert run["agentProgress"]["land_agent_review"]["status"] == "completed"
    assert run["artifacts"]["reconciliationGate"]["analysisType"] == "cross_agent_reconciliation_gate_v1"
    assert run["agentProgress"]["cross_agent_reconciliation"]["status"] == "completed"
    deal_opt = run["artifacts"]["dealOptimization"]
    assert deal_opt["analysisType"] == "deal_optimization"
    assert deal_opt["verdict"] in {"approvable", "negotiable_conditional", "not_recommended"}
    assert "verdictLabel" in deal_opt
    assert run["agentProgress"]["deal_optimization"]["status"] == "completed"
    consensus = run["artifacts"]["consensusResolution"]
    assert consensus["analysisType"] == "consensus_resolution"
    assert consensus["status"] in {"converged", "partial"}
    assert consensus["coordinator"] == "deterministic"
    assert "roundLog" in consensus and isinstance(consensus["roundLog"], list)
    assert run["agentProgress"]["consensus_resolution"]["status"] == "completed"
    assert run["artifacts"]["dataQualityReview"]["analysisType"] == "data_quality_review"
    assert run["agentProgress"]["data_quality_review"]["status"] == "completed"
    assert run["artifacts"]["evidenceGraph"]["analysisType"] == "evidence_graph"
    assert run["agentProgress"]["evidence_graph_generation"]["status"] == "completed"
    assert run["artifacts"]["developerFeasibility"]["analysisType"] == "developer_and_pf_feasibility"
    assert run["artifacts"]["marketabilityAnalysis"]["applicable"] is True
    assert run["artifacts"]["legalPermitReview"]["mcp"]["requested"] is True
    assert run["artifacts"]["riskRegister"]["analysisType"] == "risk_register"
    assert run["agentProgress"]["risk_register_generation"]["status"] == "completed"
    assert run["artifacts"]["creditReviewDecision"]["analysisType"] == "credit_review_decision"
    assert run["agentProgress"]["credit_review_decision"]["status"] == "completed"
    assert run["artifacts"]["restructuringPlan"]["analysisType"] == "pf_restructuring_plan"
    assert run["agentProgress"]["scenario_restructuring_plan"]["status"] == "completed"
    developability = run["artifacts"]["developabilityScore"]
    assert developability["analysisType"] == "developability_score_v1"
    assert developability["grade"] in {"A", "B", "C", "D", "E"}
    assert isinstance(developability["overallScore"], (int, float))
    assert run["agentProgress"]["developability_score"]["status"] == "completed"
    assert (
        run["artifacts"]["phase11Report"]["report"]["sections"]["businessFeasibility"]["developabilityScore"]["grade"]
        == developability["grade"]
    )
    term_sheet = run["artifacts"]["termSheet"]
    assert term_sheet["documentType"].startswith("거래조건서")
    assert term_sheet["markdown"].startswith("# 거래조건서")
    ic_memo = run["artifacts"]["icMemo"]
    assert ic_memo["documentType"].startswith("여신심사보고서")
    assert ic_memo["recommendation"]["decisionLabel"] == run["artifacts"]["creditReviewDecision"]["decisionLabel"]
    assert "# 여신심사보고서" in ic_memo["markdown"]
    assert run["agentProgress"]["credit_document_generation"]["status"] == "completed"

    credit_docs = client.get(f"/projects/{project_id}/orchestrations/{run['run_id']}/credit-documents").json()
    assert credit_docs["termSheet"]["documentType"].startswith("거래조건서")
    assert credit_docs["icMemo"]["documentType"].startswith("여신심사보고서")
    pdf_dl = client.get(f"/projects/{project_id}/orchestrations/{run['run_id']}/credit-documents/ic-memo/export", params={"format": "pdf"})
    assert pdf_dl.status_code == 200
    assert pdf_dl.headers["content-type"] == "application/pdf"
    assert pdf_dl.content[:4] == b"%PDF"
    docx_dl = client.get(f"/projects/{project_id}/orchestrations/{run['run_id']}/credit-documents/term-sheet/export", params={"format": "docx"})
    assert docx_dl.status_code == 200
    assert docx_dl.content[:2] == b"PK"  # docx is a zip container
    bad_doc = client.get(f"/projects/{project_id}/orchestrations/{run['run_id']}/credit-documents/unknown/export")
    assert bad_doc.status_code == 400
    assert run["agentProgress"]["scenario_restructuring_plan"]["lastArtifactKey"] == "restructuringPlan"
    assert run["artifacts"]["phase11Report"]["report"]["qualityChecks"]["actualSectionCount"] == 20
    assert run["artifacts"]["reportRecord"]["run_id"] == run["run_id"]
    assert run["artifacts"]["projectSnapshot"]["runId"] == run["run_id"]

    reports = client.get(f"/projects/{project_id}/reports").json()
    assert any(report["run_id"] == run["run_id"] for report in reports)

    snapshots = client.get(f"/projects/{project_id}/snapshots").json()
    assert any(snapshot["runId"] == run["run_id"] for snapshot in snapshots)
    snapshot_id = next(snapshot["snapshotId"] for snapshot in snapshots if snapshot["runId"] == run["run_id"])
    snapshot = client.get(f"/projects/{project_id}/snapshots/{snapshot_id}").json()
    assert snapshot["runId"] == run["run_id"]
    assert snapshot["schemaVersion"] == "realytics-project-snapshot-v1"
    assert snapshot["caseComparisonSeed"]["totalRevenue"] is not None

    risk_registers = client.get(f"/projects/{project_id}/risk-registers").json()
    assert any(record["runId"] == run["run_id"] for record in risk_registers["records"])
    persisted_risk_register = client.get(
        f"/projects/{project_id}/orchestrations/{run['run_id']}/risk-register"
    ).json()
    assert persisted_risk_register["runId"] == run["run_id"]
    assert persisted_risk_register["risks"] == run["artifacts"]["riskRegister"]["risks"]

    evidence_graphs = client.get(f"/projects/{project_id}/evidence-graphs").json()
    assert any(record["runId"] == run["run_id"] for record in evidence_graphs["records"])
    persisted_evidence_graph = client.get(
        f"/projects/{project_id}/orchestrations/{run['run_id']}/evidence-graph"
    ).json()
    assert persisted_evidence_graph["runId"] == run["run_id"]
    assert persisted_evidence_graph["summary"] == run["artifacts"]["evidenceGraph"]["summary"]
    assert persisted_evidence_graph["nodes"] == run["artifacts"]["evidenceGraph"]["nodes"]


def test_orchestration_plan_persists_prompt_artifact():
    client = TestClient(app)
    project_id = project_with_discount_triggers(client)

    res = client.post(
        f"/projects/{project_id}/orchestration/plan",
        json={},
        headers={"X-REALYTICS-USER-ID": "planner-user"},
    )

    assert res.status_code == 200
    plan = res.json()
    assert plan["promptDraft"]["body"]
    assert plan["promptArtifact"]["artifactId"]

    artifacts = client.get(f"/projects/{project_id}/prompt-artifacts").json()
    assert any(record["artifactId"] == plan["promptArtifact"]["artifactId"] for record in artifacts["records"])
    artifact = client.get(
        f"/projects/{project_id}/prompt-artifacts/{plan['promptArtifact']['artifactId']}"
    ).json()
    assert artifact["artifactType"] == "ai_work_order"
    assert artifact["createdBy"] == "planner-user"
    assert artifact["body"] == plan["promptDraft"]["body"]


def test_chief_deal_orchestration_plan_endpoint_returns_ai_deal_room_contract():
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Chief Deal Test", "status": "draft"},
            "source_documents": [{"documentType": "business_plan", "originalFileName": "business.pdf"}],
            "land": {"pnu": "1111010100100010000"},
            "feasibility_input": {"total_revenue": 1000, "total_cost": 800},
        },
        headers={"X-REALYTICS-USER-ID": "chief-user"},
    )
    assert created.status_code == 200
    project_id = created.json()["project"]["id"]

    res = client.post(
        f"/projects/{project_id}/chief-orchestration/plan",
        json={"analysisPurpose": "pf_credit_review"},
        headers={"X-REALYTICS-USER-ID": "chief-user"},
    )

    assert res.status_code == 200
    plan = res.json()
    ChiefDealOrchestrationPlan.model_validate(plan)
    assert plan["analysisPurpose"] == "pf_credit_review"
    assert len(plan["agentPods"]) == 6
    assert plan["taskGraph"]["nodes"]
    assert "missingInputRegister" in plan["sharedDealStateGraph"]
    assert plan["decisionGate"]["decision"] in {"PASS", "CONDITIONAL_PASS", "HOLD", "FAIL"}


def test_approval_readiness_gate_blocks_rejected_or_unreviewed_runs():
    run = {
        "handoffs": [
            {
                "requiresHumanReview": True,
                "reviewStatus": "pending",
                "status": "needs_review",
            }
        ],
        "artifacts": {
            "qualityCheck": {"pass": True},
            "legalPermitReview": {"status": "clear"},
            "dataQualityReview": {"status": "clear"},
            "reconciliationGate": {"status": "clear"},
            "riskRegister": {
                "status": "blocking",
                "risks": [
                    {
                        "riskId": "R-FIN-001",
                        "requiresHumanReview": True,
                        "reviewStatus": "pending_review",
                    }
                ],
            },
            "creditReviewDecision": {"decision": "reject_recommended"},
            "landAgentReview": {"status": "basic_site_data_only"},
            "evidenceGraph": {"summary": {"riskRowsWithoutExplicitEvidenceCount": 2}},
        },
    }

    gate = OrchestrationAgent()._approval_readiness_gate(run)

    assert gate["schemaVersion"] == "realytics-approval-readiness-gate-v1"
    assert gate["pass"] is False
    assert gate["status"] == "not_ready_for_approval"
    assert {item["code"] for item in gate["blockers"]} >= {
        "RISK_REGISTER_NOT_BLOCKING",
        "CREDIT_DECISION_NOT_REJECT_OR_REWORK",
        "HANDOFF_REVIEWS_RESOLVED",
        "RISK_REVIEWS_RESOLVED",
        "LAND_PNU_READY",
        "LAND_OFFICIAL_PRICE_READY",
        "LAND_REGISTRY_EVIDENCE_READY",
        "LAND_ACQUISITION_READY",
    }


def test_approval_readiness_gate_clears_land_evidence_blockers_when_ready():
    run = {
        "handoffs": [],
        "artifacts": {
            "qualityCheck": {"pass": True},
            "legalPermitReview": {"status": "clear"},
            "dataQualityReview": {"status": "clear"},
            "reconciliationGate": {"status": "clear"},
            "riskRegister": {"status": "clear", "risks": []},
            "creditReviewDecision": {"decision": "conditional_approval"},
            "landAgentReview": {
                "status": "ready_for_pf_review",
                "evidenceReadiness": {
                    "pnuReady": True,
                    "officialPriceReady": True,
                    "registryEvidenceReady": True,
                    "acquisitionReady": True,
                },
            },
            "evidenceGraph": {"summary": {"riskRowsWithoutExplicitEvidenceCount": 0}},
        },
    }

    gate = OrchestrationAgent()._approval_readiness_gate(run)
    land_gates = {
        item["code"]: item
        for item in gate["gates"]
        if item["code"].startswith("LAND_")
    }

    assert land_gates["LAND_PNU_READY"]["pass"] is True
    assert land_gates["LAND_OFFICIAL_PRICE_READY"]["pass"] is True
    assert land_gates["LAND_REGISTRY_EVIDENCE_READY"]["pass"] is True
    assert land_gates["LAND_ACQUISITION_READY"]["pass"] is True
    assert gate["summary"]["landEvidenceReady"] is True


def test_approval_readiness_gate_reads_deal_state_records(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-deal-state.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    project_id = "approval-deal-state-project"
    base_run = {
        "project_id": project_id,
        "handoffs": [],
        "artifacts": {
            "qualityCheck": {"pass": True},
            "legalPermitReview": {"status": "clear"},
            "dataQualityReview": {"status": "clear"},
            "reconciliationGate": {"status": "clear"},
            "riskRegister": {"status": "clear", "risks": []},
            "creditReviewDecision": {"decision": "conditional_approval"},
            "landAgentReview": {
                "status": "ready_for_pf_review",
                "evidenceReadiness": {
                    "pnuReady": True,
                    "officialPriceReady": True,
                    "registryEvidenceReady": True,
                    "acquisitionReady": True,
                },
            },
            "evidenceGraph": {"summary": {"riskRowsWithoutExplicitEvidenceCount": 0}},
        },
    }

    upsert_deal_state_record(project_id, {
        "recordId": "risk-senior-shortfall",
        "recordType": "risk_issue",
        "key": "SENIOR_SHORTFALL",
        "severity": "critical",
        "reviewStatus": "open",
        "sourceType": "scenario_engine",
        "sourceLocator": {"artifactKey": "scenario-artifact"},
    })
    upsert_deal_state_record(project_id, {
        "recordId": "metric-ltv",
        "recordType": "metric",
        "key": "pf_metrics.ltv",
        "value": 0.62,
        "sourceType": "calculation_run",
        "calculationRunId": "calc-pf-1",
        "sourceLocator": {"calculationRunId": "calc-pf-1"},
    })

    gate = OrchestrationAgent()._approval_readiness_gate(base_run)
    blocker_codes = {item["code"] for item in gate["blockers"]}

    assert "DEAL_STATE_BLOCKING_RISKS_CLEARED" in blocker_codes
    assert "DEAL_STATE_REQUIRED_EVIDENCE_PRESENT" in blocker_codes
    assert gate["summary"]["dealStateRecordCount"] == 2
    assert gate["summary"]["dealStateBlockingRiskCount"] == 1


def test_approval_readiness_gate_clears_deal_state_when_records_are_reviewed(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-deal-state-clear.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    project_id = "approval-deal-state-clear-project"
    for record in [
        {
            "recordId": "risk-resolved",
            "recordType": "risk_issue",
            "key": "SENIOR_SHORTFALL",
            "severity": "critical",
            "reviewStatus": "resolved",
            "sourceType": "risk_register",
            "sourceLocator": {"artifactKey": "risk-register"},
        },
        {
            "recordId": "metric-exit",
            "recordType": "metric",
            "key": "pf_metrics.exitSaleRate",
            "value": 0.67,
            "sourceType": "calculation_run",
            "calculationRunId": "calc-exit-1",
            "sourceLocator": {"calculationRunId": "calc-exit-1"},
        },
        {
            "recordId": "evidence-registry",
            "recordType": "evidence",
            "key": "registry-pnu-1",
            "sourceType": "land_evidence",
            "sourceLocator": {"evidenceId": "registry-pnu-1"},
        },
        {
            "recordId": "assumption-base",
            "recordType": "assumption",
            "key": "assumption:base-case",
            "status": "approved",
            "sourceType": "assumption_override",
            "sourceLocator": {"overrideId": "assumption:base-case"},
        },
    ]:
        upsert_deal_state_record(project_id, record)

    run = {
        "project_id": project_id,
        "handoffs": [],
        "artifacts": {
            "qualityCheck": {"pass": True},
            "legalPermitReview": {"status": "clear"},
            "dataQualityReview": {"status": "clear"},
            "reconciliationGate": {"status": "clear"},
            "riskRegister": {"status": "clear", "risks": []},
            "creditReviewDecision": {"decision": "conditional_approval"},
            "landAgentReview": {
                "status": "ready_for_pf_review",
                "evidenceReadiness": {
                    "pnuReady": True,
                    "officialPriceReady": True,
                    "registryEvidenceReady": True,
                    "acquisitionReady": True,
                },
            },
            "evidenceGraph": {"summary": {"riskRowsWithoutExplicitEvidenceCount": 0}},
        },
    }

    gate = OrchestrationAgent()._approval_readiness_gate(run)
    deal_state_codes = {
        item["code"]: item
        for item in gate["gates"]
        if item["code"].startswith("DEAL_STATE_")
    }

    assert deal_state_codes["DEAL_STATE_BLOCKING_RISKS_CLEARED"]["pass"] is True
    assert deal_state_codes["DEAL_STATE_REQUIRED_EVIDENCE_PRESENT"]["pass"] is True
    assert deal_state_codes["DEAL_STATE_REPORT_GRADE_METRICS_PRESENT"]["pass"] is True
    assert deal_state_codes["DEAL_STATE_ASSUMPTION_OVERRIDES_APPROVED"]["pass"] is True
    assert gate["decisionBasis"] == "deal_state_records"
    assert gate["dealStateApproval"]["basis"] == "deal_state_records"
    assert gate["summary"]["dealStateSourceLocatorMissingCount"] == 0


def test_approval_readiness_gate_blocks_stale_calculation_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-stale-metric.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    project_id = "approval-stale-metric-project"

    old_run = upsert_calculation_run_record(project_id, {
        "calculationRunId": "calc:pf_metrics:old",
        "engineKey": "pf_metrics",
        "engineVersion": "pf_metrics-v1",
        "createdAt": "2026-07-01T00:00:00+00:00",
        "sourceLocator": {"calculationRunId": "calc:pf_metrics:old", "engineKey": "pf_metrics"},
        "outputMetrics": [
            {
                "key": "pf_metrics.ltv",
                "value": 0.72,
                "sourceLocator": {"calculationRunId": "calc:pf_metrics:old", "engineKey": "pf_metrics"},
            }
        ],
    })
    upsert_calculation_run_record(project_id, {
        "calculationRunId": "calc:pf_metrics:new",
        "engineKey": "pf_metrics",
        "engineVersion": "pf_metrics-v1",
        "createdAt": "2026-07-02T00:00:00+00:00",
        "sourceLocator": {"calculationRunId": "calc:pf_metrics:new", "engineKey": "pf_metrics"},
        "outputMetrics": [],
    })
    upsert_deal_state_record(project_id, {
        "recordId": "evidence-price",
        "recordType": "evidence",
        "key": "evidence:price",
        "sourceType": "manual_evidence",
        "sourceLocator": {"evidenceId": "evidence:price"},
    })
    assert old_run["calculationRunId"] == "calc:pf_metrics:old"

    run = _approval_ready_base_run(project_id)
    gate = OrchestrationAgent()._approval_readiness_gate(run)
    deal_state_codes = {
        item["code"]: item
        for item in gate["gates"]
        if item["code"].startswith("DEAL_STATE_")
    }

    assert deal_state_codes["DEAL_STATE_CORE_METRICS_LATEST"]["pass"] is False
    assert "DEAL_STATE_CORE_METRICS_LATEST" in {item["code"] for item in gate["blockers"]}
    assert gate["summary"]["dealStateStaleMetricCount"] == 1


def test_approval_readiness_gate_blocks_open_source_locator_audit_risks(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-source-locator-risk.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    project_id = "approval-source-locator-risk-project"

    upsert_calculation_run_record(project_id, {
        "calculationRunId": "calc:discount_stress:latest",
        "engineKey": "discount_stress",
        "engineVersion": "discount_stress_v1",
        "sourceLocator": {"calculationRunId": "calc:discount_stress:latest", "engineKey": "discount_stress"},
        "outputMetrics": [
            {
                "key": "discount_stress.discount10.exitSaleRate",
                "value": 0.6,
                "sourceLocator": {
                    "calculationRunId": "calc:discount_stress:latest",
                    "engineKey": "discount_stress",
                    "path": "scenarios[key=severe].saleRate",
                },
            }
        ],
    })
    upsert_deal_state_record(project_id, {
        "recordId": "evidence-market",
        "recordType": "evidence",
        "key": "evidence:market",
        "sourceType": "manual_evidence",
        "sourceLocator": {"evidenceId": "evidence:market"},
    })
    upsert_deal_state_record(project_id, {
        "recordId": "risk_issue:SOURCE_LOCATOR_MISSING",
        "recordType": "risk_issue",
        "key": "SOURCE_LOCATOR_MISSING",
        "severity": "high",
        "reviewStatus": "open",
        "sourceType": "source_locator_audit",
        "sourceLocator": {"artifactKey": "source-locator-audit"},
    })

    gate = OrchestrationAgent()._approval_readiness_gate(_approval_ready_base_run(project_id))
    deal_state_codes = {
        item["code"]: item
        for item in gate["gates"]
        if item["code"].startswith("DEAL_STATE_")
    }

    assert deal_state_codes["DEAL_STATE_SOURCE_LOCATOR_COMPLETENESS_SUFFICIENT"]["pass"] is False
    assert "DEAL_STATE_SOURCE_LOCATOR_COMPLETENESS_SUFFICIENT" in {item["code"] for item in gate["blockers"]}


def test_approval_readiness_gate_blocks_auto_supplement_candidates_until_reviewed(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-auto-supplement.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    project_id = "approval-auto-supplement-project"

    upsert_calculation_run_record(project_id, {
        "calculationRunId": "calc:waterfall:latest",
        "engineKey": "waterfall",
        "engineVersion": "calculation_scenario_engine_v1",
        "sourceLocator": {"calculationRunId": "calc:waterfall:latest", "engineKey": "waterfall"},
        "outputMetrics": [
            {
                "key": "waterfall.summary.shortfallFlagCount",
                "value": 0,
                "sourceLocator": {
                    "calculationRunId": "calc:waterfall:latest",
                    "engineKey": "waterfall",
                    "path": "summary.shortfallFlagCount",
                },
            }
        ],
    })
    upsert_deal_state_record(project_id, {
        "recordId": "evidence-vworld-pnu",
        "recordType": "evidence",
        "key": "land.pnu",
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": {"sourceName": "VWorld cadastral data", "pnu": "1111010100100010000"},
    })
    upsert_deal_state_record(project_id, {
        "recordId": "assumption:auto-market-price",
        "recordType": "assumption",
        "key": "market.proposedPricePerPy",
        "assumptionType": "benchmark_proxy_not_project_proposed_price",
        "approvalStatus": "candidate_needs_review",
        "reviewStatus": "needs_revision",
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": {"sourceName": "Market comparable benchmark", "comparableCount": 3},
        "value": 35000000,
    })
    upsert_deal_state_record(project_id, {
        "recordId": "risk:auto-market-review",
        "recordType": "risk_issue",
        "key": "auto_supplement.market_product.market.proposedPricePerPy.PROPOSED_PRICE_BASIS_SUPPLEMENTED_NEEDS_REVIEW",
        "riskCode": "PROPOSED_PRICE_BASIS_SUPPLEMENTED_NEEDS_REVIEW",
        "category": "data_quality",
        "severity": "medium",
        "reviewStatus": "needs_revision",
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": {"sourceName": "Market comparable benchmark", "comparableCount": 3},
    })

    gate = OrchestrationAgent()._approval_readiness_gate(_approval_ready_base_run(project_id))
    blocker_codes = {item["code"] for item in gate["blockers"]}

    assert "DEAL_STATE_ASSUMPTION_CANDIDATES_REVIEWED" in blocker_codes
    assert "DEAL_STATE_AUTO_SUPPLEMENT_REVIEW_ITEMS_CLEARED" in blocker_codes
    assert gate["summary"]["dealStateOpenAssumptionCandidateCount"] == 1
    assert gate["summary"]["dealStateOpenAutoSupplementRiskCount"] == 1

    upsert_deal_state_record(project_id, {
        "recordId": "assumption:auto-market-price",
        "recordType": "assumption",
        "key": "market.proposedPricePerPy",
        "assumptionType": "benchmark_proxy_not_project_proposed_price",
        "approvalStatus": "approved",
        "reviewStatus": "approved",
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": {"sourceName": "Market comparable benchmark", "comparableCount": 3},
        "value": 35000000,
    })
    upsert_deal_state_record(project_id, {
        "recordId": "risk:auto-market-review",
        "recordType": "risk_issue",
        "key": "auto_supplement.market_product.market.proposedPricePerPy.PROPOSED_PRICE_BASIS_SUPPLEMENTED_NEEDS_REVIEW",
        "riskCode": "PROPOSED_PRICE_BASIS_SUPPLEMENTED_NEEDS_REVIEW",
        "category": "data_quality",
        "severity": "medium",
        "reviewStatus": "resolved",
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": {"sourceName": "Market comparable benchmark", "comparableCount": 3},
    })

    cleared = OrchestrationAgent()._approval_readiness_gate(_approval_ready_base_run(project_id))
    cleared_codes = {item["code"]: item for item in cleared["gates"]}
    assert cleared_codes["DEAL_STATE_ASSUMPTION_CANDIDATES_REVIEWED"]["pass"] is True
    assert cleared_codes["DEAL_STATE_AUTO_SUPPLEMENT_REVIEW_ITEMS_CLEARED"]["pass"] is True


def test_approval_readiness_gate_blocks_failed_auto_supplement_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "approval-auto-supplement-gap.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    project_id = "approval-auto-supplement-gap-project"

    upsert_calculation_run_record(project_id, {
        "calculationRunId": "calc:discount_stress:latest",
        "engineKey": "discount_stress",
        "engineVersion": "discount_stress_v1",
        "sourceLocator": {"calculationRunId": "calc:discount_stress:latest", "engineKey": "discount_stress"},
        "outputMetrics": [
            {
                "key": "discount_stress.discount10.exitSaleRate",
                "value": 0.62,
                "sourceLocator": {
                    "calculationRunId": "calc:discount_stress:latest",
                    "engineKey": "discount_stress",
                    "path": "scenarios[key=severe].saleRate",
                },
            }
        ],
    })
    upsert_deal_state_record(project_id, {
        "recordId": "evidence-market",
        "recordType": "evidence",
        "key": "market.comparable",
        "sourceType": "manual_evidence",
        "sourceLocator": {"evidenceId": "market-comp-1"},
    })
    upsert_deal_state_record(project_id, {
        "recordId": "auto_supplement_attempt:land_legal:failed",
        "recordType": "auto_supplement_attempt",
        "key": "auto_supplement.land_legal",
        "sourceType": "auto_evidence_supplement",
        "sourceLocator": {"type": "parsed_fact_validation", "podKey": "land_legal"},
        "summary": {
            "attemptCount": 2,
            "createdRecordCount": 0,
            "supplementedCount": 0,
            "needsUserMaterialCount": 1,
            "failedCount": 1,
        },
    })

    gate = OrchestrationAgent()._approval_readiness_gate(_approval_ready_base_run(project_id))
    blocker_codes = {item["code"] for item in gate["blockers"]}

    assert "DEAL_STATE_AUTO_SUPPLEMENT_GAPS_RESOLVED" in blocker_codes
    assert gate["summary"]["dealStateOpenAutoSupplementGapCount"] == 1


def test_land_readiness_uses_flat_land_evidence_package():
    review = OrchestrationAgent()._review_land_readiness({
        "project": {"name": "Flat Land Evidence Pilot"},
        "land": {
            "pnu": "2729011700100010001",
            "area_sqm": 5000.0,
        },
        "land_acquisition": [
            {"owner": "Owner A", "share": 1.0, "area_sqm": 5000.0},
        ],
        "landEvidence": {
            "officialLandPrice": 1250000,
            "officialLandPriceBaseYear": 2026,
            "officialLandPriceEvidence": {
                "evidenceId": "official-price-2026-pnu-2729011700100010001",
            },
            "registryEvidence": [
                {"evidenceId": "registry-pnu-2729011700100010001"},
            ],
            "sourceMetadata": {
                "sourceName": "Manual pilot evidence package",
                "baseDate": "2026-05-27",
            },
        },
    })

    assert review["status"] == "ready_for_pf_review"
    assert review["summary"]["totalAreaSqm"] == 5000.0
    assert review["summary"]["officialLandPriceCoverageRatio"] == 1.0
    assert review["summary"]["registryEvidenceCoverageRatio"] == 1.0
    assert review["summary"]["acquisitionAreaRatio"] == 1.0
    assert review["evidenceReadiness"]["officialPriceReady"] is True
    assert review["evidenceReadiness"]["registryEvidenceReady"] is True
    assert review["handoffReadiness"] == "pf_ready_patch"
    assert set(review["evidenceIds"]) == {
        "official-price-2026-pnu-2729011700100010001",
        "registry-pnu-2729011700100010001",
    }


def test_pilot_input_package_template_runs_orchestration():
    fixture_path = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "pilot_project_input_package_template.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    run = OrchestrationAgent().run_pf_review("pilot-template-test", payload)

    assert run["status"] == "completed"
    assert len(run["steps"]) == len(PF_REVIEW_STEPS)
    assert run["artifacts"]["qualityCheck"]["pass"] is True
    assert run["artifacts"]["approvalReadinessGate"]["analysisType"] == "approval_readiness_gate"
    assert run["artifacts"]["creditReviewDecision"]["decision"] in {
        "approve",
        "conditional_approval",
        "rework_required",
        "reject_recommended",
    }
    assert run["artifacts"]["restructuringPlan"]["analysisType"] == "pf_restructuring_plan"
    assert run["errors"] == []


def test_restructuring_cure_is_attached_to_next_pf_review_run():
    client = TestClient(app)
    project_id = project_with_discount_triggers(client)

    saved = client.post(
        f"/projects/{project_id}/restructuring-cures",
        json={
            "cureId": "test-dsra-cure",
            "cureType": "equity_dsra_injection",
            "status": "active",
            "riskCodes": ["SENIOR_SHORTFALL", "UNSOLD_LTV_BREACH"],
            "amount": 2_000_000_000,
            "sourceUrlOrEvidenceId": "dsra-confirmation-test-p1",
            "reviewedBy": "credit_officer",
        },
        headers={"X-REALYTICS-USER-ID": "credit_officer"},
    )

    assert saved.status_code == 200
    assert saved.json()["records"][0]["cureId"] == "test-dsra-cure"

    listed = client.get(f"/projects/{project_id}/restructuring-cures", params={"status": "active"}).json()
    assert any(record["cureId"] == "test-dsra-cure" for record in listed["records"])

    run = client.post(
        f"/projects/{project_id}/orchestrations/pf-review",
        json={"options": {"requested_by": "pytest-cure"}},
    ).json()
    adopted = run["artifacts"]["restructuringPlan"]["adoptedCures"]

    assert adopted["activeCount"] >= 1
    assert adopted["sourceBackedActiveCount"] >= 1
    assert any(record["cureId"] == "test-dsra-cure" for record in adopted["records"])
    restructuring_handoff = next(
        handoff
        for handoff in run["handoffs"]
        if handoff["handoffType"] == "restructuring_to_credit_review"
    )
    assert restructuring_handoff["payload"]["adoptedCures"]["sourceBackedActiveCount"] >= 1


def test_pf_review_orchestration_can_be_retrieved_and_approved():
    client = TestClient(app)
    project_id = project_with_discount_triggers(client)
    run = client.post(f"/projects/{project_id}/orchestrations/pf-review", json={}).json()

    fetched = client.get(f"/projects/{project_id}/orchestrations/{run['run_id']}").json()
    assert fetched["run_id"] == run["run_id"]

    handoff_id = fetched["handoffs"][0]["handoffId"]
    needs_revision = client.post(
        f"/projects/{project_id}/orchestrations/{run['run_id']}/handoffs/{handoff_id}/review",
        json={
            "review_status": "needs_revision",
            "reviewed_by": "credit_officer",
            "comment": "Handoff needs workflow QA follow-up",
        },
    ).json()
    needs_revision_handoff = next(item for item in needs_revision["handoffs"] if item["handoffId"] == handoff_id)
    assert needs_revision_handoff["reviewStatus"] == "needs_revision"
    assert needs_revision_handoff["status"] == "needs_review"

    blocked_approval = client.post(
        f"/projects/{project_id}/orchestrations/{run['run_id']}/approve",
        json={"approved_by": "credit_officer", "comment": "Should be blocked"},
    )
    assert blocked_approval.status_code == 409
    assert "Unresolved agent handoff reviews" in blocked_approval.json()["detail"]

    reviewed = client.post(
        f"/projects/{project_id}/orchestrations/{run['run_id']}/handoffs/{handoff_id}/review",
        json={
            "review_status": "approved",
            "reviewed_by": "credit_officer",
            "comment": "Handoff accepted for workflow QA",
        },
    ).json()
    reviewed_handoff = next(item for item in reviewed["handoffs"] if item["handoffId"] == handoff_id)
    assert reviewed_handoff["reviewStatus"] == "approved"
    assert reviewed_handoff["status"] == "accepted"
    assert reviewed_handoff["reviewedBy"] == "credit_officer"
    assert reviewed_handoff["reviewHistory"][-1]["reviewComment"] == "Handoff accepted for workflow QA"

    for handoff in reviewed["handoffs"]:
        if handoff["reviewStatus"] in {"pending", "rejected", "needs_revision"}:
            reviewed = client.post(
                f"/projects/{project_id}/orchestrations/{run['run_id']}/handoffs/{handoff['handoffId']}/review",
                json={
                    "review_status": "approved",
                    "reviewed_by": "credit_officer",
                    "comment": "Remaining handoff accepted for workflow QA",
                },
            ).json()

    for risk in reviewed["artifacts"]["riskRegister"]["risks"]:
        if risk.get("requiresHumanReview"):
            reviewed = client.post(
                f"/projects/{project_id}/orchestrations/{run['run_id']}/risk-register/{risk['riskId']}/review",
                json={
                    "review_status": "waived",
                    "reviewed_by": "credit_officer",
                    "comment": "Risk accepted for workflow QA.",
                    "waiver_reason": "Test run waiver.",
                },
            ).json()

    persisted_after_review = client.get(
        f"/projects/{project_id}/orchestrations/{run['run_id']}/risk-register"
    ).json()
    assert all(
        risk.get("reviewStatus") in {"waived", "approved", "resolved"} or not risk.get("requiresHumanReview")
        for risk in persisted_after_review["risks"]
    )

    approved = client.post(
        f"/projects/{project_id}/orchestrations/{run['run_id']}/approve",
        json={"approved_by": "credit_officer", "comment": "Reviewed for workflow QA"},
    ).json()
    assert approved["status"] == "completed"
    assert approved["executionStatus"] == "completed"
    assert approved["approvalStatus"] == "approved"
    assert approved["approval"]["approved_by"] == "credit_officer"

    summaries = client.get(f"/projects/{project_id}/orchestrations").json()
    assert any(
        item["run_id"] == run["run_id"]
        and item["executionStatus"] == "completed"
        and item["approvalStatus"] == "approved"
        for item in summaries
    )


def test_orchestration_written_by_external_worker_is_visible(monkeypatch, tmp_path):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "external-worker.sqlite3"))
    client = TestClient(app)
    project_id = project_with_discount_triggers(client)
    run_id = "external-worker-run"
    run = {
        "run_id": run_id,
        "project_id": project_id,
        "workflow": PF_REVIEW_WORKFLOW,
        "status": "completed",
        "executionStatus": "completed",
        "approvalStatus": "not_ready",
        "reportStatus": "quality_pass",
        "started_at": "2026-07-15T12:00:00+00:00",
        "completed_at": "2026-07-15T12:01:00+00:00",
        "steps": [],
        "errors": [],
        "artifacts": {},
    }
    assert upsert_orchestration_run(project_id, run_id, run) is True

    fetched = client.get(f"/projects/{project_id}/orchestrations/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["executionStatus"] == "completed"

    listed = client.get(f"/projects/{project_id}/orchestrations")
    assert listed.status_code == 200
    assert any(item["run_id"] == run_id for item in listed.json())


def test_upload_and_run_creates_orchestration(monkeypatch):
    monkeypatch.delenv("REALYTICS_AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Upload Run Test", "type": "mixed_use"},
            "feasibility_input": {
                "total_revenue": 120_000_000_000,
                "total_cost": 95_000_000_000,
                "finance_cost": 5_000_000_000,
            },
            "pf_structure": [{"tranche": "Tranche A", "amount": 65_000_000_000}],
            "risk_items": [],
        },
    ).json()
    project_id = created["id"]

    res = client.post(
        f"/projects/{project_id}/upload-and-run",
        files={"file": ("business-plan.pdf", b"not-a-real-pdf", "application/pdf")},
        headers={"X-REALYTICS-USER-ID": "upload-test"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "completed"
    assert body["run"]["status"] == "completed"
    assert body["run"]["user_id"] == "upload-test"
    assert body["run"]["artifacts"]["reportRecord"]["run_id"] == body["run"]["run_id"]
    assert body["run"]["artifacts"]["projectSnapshot"]["runId"] == body["run"]["run_id"]
    assert body["project"]["_meta"]["upload"]["originalFileName"] == "business-plan.pdf"
    assert body["project"]["_meta"]["upload"]["originalFileHash"]
    assert body["project"]["_meta"]["upload"]["archiveStored"] is True
    assert body["project"]["_meta"]["upload"]["archiveId"] == body["project"]["_meta"]["upload"]["originalFileHash"]
    assert body["project"]["_meta"]["upload"]["downloadUrl"].endswith("/download")
    assert body["project"]["_meta"]["parser_version"] == "realytics-parser-v2.4"

    source_documents = client.get(f"/projects/{project_id}/source-documents").json()
    assert len(source_documents) == 1
    assert source_documents[0]["archiveId"] == body["project"]["_meta"]["upload"]["archiveId"]

    downloaded = client.get(source_documents[0]["downloadUrl"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"not-a-real-pdf"

    deleted = client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 200


def test_pf_review_persists_market_evidence_records():
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Market Evidence Storage Test", "type": "officetel"},
            "sales_assumptions": {"avg_price_per_sqm": 9_075_000},
            "feasibility_input": {
                "total_revenue": 72_000_000_000,
                "total_cost": 58_000_000_000,
                "finance_cost": 3_000_000_000,
            },
            "pf_structure": [{"tranche": "Tranche A", "amount": 35_000_000_000, "priority": 1}],
            "sales_schedule": {"contract_deposit": 0.1},
            "document_market_evidence": {
                "sourceDocument": {
                    "originalFileName": "market-study.pdf",
                    "originalFileHash": "hash-market-study",
                    "parserVersion": "realytics-parser-v2.4",
                },
                "productMix": [
                    {
                        "type": "A",
                        "productType": "officetel_residential",
                        "exclusiveAreaPy": 12,
                        "contractAreaPy": 20,
                        "units": 100,
                        "pricePerPy": 36_000_000,
                        "totalRevenue": 72_000_000_000,
                        "evidenceIds": ["doc-p12-table3"],
                    }
                ],
                "comparables": [
                    {
                        "caseId": "DOC-COMP-001",
                        "name": "Document Comparable A",
                        "productType": "officetel_residential",
                        "distanceM": 250,
                        "pricePerPy": 30_000_000,
                        "sourceName": "uploaded business plan",
                        "sourceUrlOrEvidenceId": "doc-p20",
                        "baseDate": "2026-05-15",
                    }
                ],
            },
        },
    ).json()
    project_id = created["project"]["id"]

    run = client.post(f"/projects/{project_id}/orchestrations/pf-review", json={}).json()
    records = client.get(f"/projects/{project_id}/market-evidence", params={"run_id": run["run_id"]}).json()

    assert run["artifacts"]["marketEvidenceReview"]["evidenceSummary"]["evidenceRecordCount"] == 2
    assert len(records) == 2
    assert {record["evidenceType"] for record in records} == {"product_mix", "sale_comparable"}
    assert all(record["runId"] == run["run_id"] for record in records)
    assert all(record["sourceDocument"]["originalFileHash"] == "hash-market-study" for record in records)

    reviewed = client.post(
        f"/projects/{project_id}/market-evidence/review",
        json={
            "record_key": records[0]["recordKey"],
            "review_status": "approved",
            "reviewed_by": "market_analyst",
            "comment": "Comparable evidence accepted.",
        },
    ).json()

    assert reviewed["reviewStatus"] == "approved"
    assert reviewed["reviewedBy"] == "market_analyst"
    assert reviewed["reviewHistory"][-1]["reviewComment"] == "Comparable evidence accepted."


def test_approved_assumption_override_is_attached_to_pf_review_run():
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "DQ Override API Test", "type": "mixed_use"},
            "feasibility_input": {
                "total_revenue": 100_000,
                "total_cost": 80_000,
                "finance_cost": 5_000,
            },
            "product_mix": [{"type": "A", "units": 10, "totalRevenue": 70_000}],
            "pf_structure": [{"tranche": "Tranche A", "amount": 50_000, "priority": 1}],
            "expected_results": {"total_pf_amount": 50_000},
        },
    ).json()
    project_id = created["project"]["id"]

    override_response = client.post(
        f"/projects/{project_id}/assumption-overrides",
        json={
            "overrideId": "ovr-sales-revenue-api",
            "checkKey": "sales_revenue_consistency",
            "canonicalField": "feasibility_input.total_revenue",
            "approvedValue": 100_000,
            "unit": "KRW",
            "approvedBy": "credit_officer",
            "reason": "Signed feasibility workbook is authoritative for the pilot.",
            "sourceLocators": [{"sourceType": "excel_cell", "sheet": "Summary", "cell": "B12"}],
        },
    )
    assert override_response.status_code == 200
    assert override_response.json()["records"][0]["overrideId"] == "ovr-sales-revenue-api"

    listed = client.get(f"/projects/{project_id}/assumption-overrides").json()
    assert listed["records"][0]["checkKey"] == "sales_revenue_consistency"

    run = client.post(f"/projects/{project_id}/orchestrations/pf-review", json={}).json()
    data_quality = run["artifacts"]["dataQualityReview"]
    sales_check = next(check for check in data_quality["checks"] if check["key"] == "sales_revenue_consistency")
    assert sales_check["status"] == "overridden"
    assert data_quality["appliedOverrides"][0]["overrideId"] == "ovr-sales-revenue-api"

    revoke_response = client.post(
        f"/projects/{project_id}/assumption-overrides",
        json={
            **override_response.json()["records"][0],
            "status": "revoked",
            "statusReason": "Reviewer revoked the approved assumption during QA.",
            "statusChangedBy": "credit_officer",
        },
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["records"][0]["status"] == "revoked"

    revoked_run = client.post(f"/projects/{project_id}/orchestrations/pf-review", json={}).json()
    revoked_data_quality = revoked_run["artifacts"]["dataQualityReview"]
    revoked_sales_check = next(check for check in revoked_data_quality["checks"] if check["key"] == "sales_revenue_consistency")
    assert revoked_sales_check["status"] != "overridden"
    assert all(item["overrideId"] != "ovr-sales-revenue-api" for item in revoked_data_quality["appliedOverrides"])
