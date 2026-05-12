import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from main import app


def load_example():
    path = Path(__file__).parents[1] / "data" / "examples" / "daegu_bondong_mixed_use_pf.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_development_feasibility_agent_builds_developer_and_pf_metrics():
    result = DevelopmentFeasibilityAgent().analyze(load_example())

    assert result["analysisType"] == "developer_and_pf_feasibility"
    assert result["revenue"]["totalRevenue"] == 150_000_000_000
    assert result["costs"]["totalCost"] == 120_000_000_000
    assert result["developerReturns"]["developmentProfit"] == 30_000_000_000
    assert result["pfReadiness"]["totalPfAmount"] == 70_000_000_000
    assert result["sensitivity"]


def test_development_feasibility_endpoint_returns_analysis():
    client = TestClient(app)
    project_id = next(
        item["project"]["id"]
        for item in client.get("/projects/").json()
        if item["project"]["name"] == "Daegu Bondong Mixed-Use PF"
    )

    res = client.post(f"/projects/{project_id}/development-feasibility/analyze", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["developerReturns"]["profitMargin"] == 0.2
    assert body["pfReadiness"]["ltvByRevenue"] == 70_000_000_000 / 150_000_000_000
