from fastapi.testclient import TestClient

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
    assert len(run["handoffs"]) >= 7
    assert set(run["agentProgress"]) == {key for key, _ in PF_REVIEW_STEPS}
    assert run["agentProgress"]["marketability_analysis"]["status"] == "completed"
    assert run["agentProgress"]["marketability_analysis"]["progressPercent"] == 100
    assert run["agentProgress"]["quality_check"]["lastArtifactKey"] == "qualityCheck"
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
    }
    AgentHandoffEnvelope.model_validate(run["handoffs"][0])
    AgentProgress.model_validate(run["agentProgress"]["marketability_analysis"])
    assert run["artifacts"]["qualityCheck"]["pass"] is True
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
