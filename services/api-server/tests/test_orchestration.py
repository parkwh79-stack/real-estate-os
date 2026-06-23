import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.orchestration_agent import OrchestrationAgent
from main import app
from schemas.orchestration import AgentHandoffEnvelope, AgentProgress, PF_REVIEW_STEPS, PF_REVIEW_WORKFLOW


def project_with_discount_triggers(client: TestClient) -> str:
    projects = client.get("/projects/").json()
    return next(
        item["project"]["id"]
        for item in projects
        if item.get("covenants", {}).get("discount_triggers")
    )


def test_pf_review_orchestration_completes_and_saves_report():
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
    assert approved["status"] == "approved"
    assert approved["approval"]["approved_by"] == "credit_officer"

    summaries = client.get(f"/projects/{project_id}/orchestrations").json()
    assert any(item["run_id"] == run["run_id"] and item["status"] == "approved" for item in summaries)


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
