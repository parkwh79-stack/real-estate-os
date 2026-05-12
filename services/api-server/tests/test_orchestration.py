from fastapi.testclient import TestClient

from main import app
from schemas.orchestration import PF_REVIEW_STEPS, PF_REVIEW_WORKFLOW


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
    assert run["artifacts"]["qualityCheck"]["pass"] is True
    assert run["artifacts"]["developerFeasibility"]["analysisType"] == "developer_and_pf_feasibility"
    assert run["artifacts"]["marketabilityAnalysis"]["applicable"] is True
    assert run["artifacts"]["legalPermitReview"]["mcp"]["requested"] is True
    assert run["artifacts"]["phase11Report"]["report"]["qualityChecks"]["actualSectionCount"] == 20
    assert run["artifacts"]["reportRecord"]["run_id"] == run["run_id"]

    reports = client.get(f"/projects/{project_id}/reports").json()
    assert any(report["run_id"] == run["run_id"] for report in reports)


def test_pf_review_orchestration_can_be_retrieved_and_approved():
    client = TestClient(app)
    project_id = project_with_discount_triggers(client)
    run = client.post(f"/projects/{project_id}/orchestrations/pf-review", json={}).json()

    fetched = client.get(f"/projects/{project_id}/orchestrations/{run['run_id']}").json()
    assert fetched["run_id"] == run["run_id"]

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
