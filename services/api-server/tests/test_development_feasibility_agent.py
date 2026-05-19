import json
from pathlib import Path

from fastapi.testclient import TestClient

from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from main import app
from schemas.feasibility_study import FeasibilityStudyV2


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
    assert result["agentRole"] == "development_feasibility_orchestrator"
    assert result["pfReadiness"]["isPfReviewReady"] is True
    assert result["pfReadiness"]["requiresCashFlowScenario"] is True
    assert result["pfReadiness"]["requiresWaterfallAnalysis"] is True
    assert result["pfReadiness"]["requiresUnsoldLtvMatrix"] is True
    assert result["downstreamEngineReadiness"]["canonicalPath"] == [
        "DevelopmentFeasibilityAgent",
        "FeasibilityStudyV2",
        "CashFlowRunRequest",
        "CashFlowEngine",
        "CalculationScenarioEngine",
    ]
    assert result["feasibilityStudyV2Integration"]["mapperStatus"] == "cash_flow_request_draft_available"
    assert result["feasibilityStudyV2Draft"]["version"] == "realytics-feasibility-study-v2"
    assert result["feasibilityStudyV2Draft"]["metrics"]["total_revenue"] == 150_000_000_000
    assert result["feasibilityStudyV2Draft"]["financing"][0]["priority"] == "senior"
    assert result["assetCalculationProfiles"]["profiles"]["mixed_use"]["revenueMode"] == "mixed_presale_by_component"
    assert result["feasibilityStudyV2Draft"]["raw_import"]["assetCalculationProfiles"]["profileVersion"] == "asset-calculation-profile-v1"
    assert result["cashFlowRunRequestDraft"]["requestType"] == "cash_flow_run_request_v1"
    assert result["cashFlowRunRequestDraft"]["productMix"][0]["type"] == "mixed_use"
    assert result["cashFlowRunRequestDraft"]["productMix"][0]["revenueMode"] == "mixed_presale_by_component"
    assert result["cashFlowRunRequestDraft"]["pfStructure"][0]["repaymentPriority"] == 1
    assert result["cashFlowRunResult"]["analysisType"] == "realytics_cash_flow_engine_v1"
    assert result["cashFlowRunResult"]["summary"]["requiredPfLimit"] > 0
    assert result["cashFlowRunResult"]["pfDrawSchedule"]
    assert FeasibilityStudyV2.model_validate(result["feasibilityStudyV2Draft"]).metrics.development_profit == 30_000_000_000
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
    assert body["downstreamEngineReadiness"]["canRunCashFlow"] is True


def test_cash_flow_run_endpoint_uses_development_feasibility_request_draft():
    client = TestClient(app)
    project_id = next(
        item["project"]["id"]
        for item in client.get("/projects/").json()
        if item["project"]["name"] == "Daegu Bondong Mixed-Use PF"
    )

    res = client.post(f"/projects/{project_id}/cash-flow/run", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["analysisType"] == "realytics_cash_flow_engine_v1"
    assert body["summary"]["pfLimit"] == 70_000_000_000
    assert body["monthlyCashFlow"]
    assert body["reportFragments"]["cashFlowSummary"]
