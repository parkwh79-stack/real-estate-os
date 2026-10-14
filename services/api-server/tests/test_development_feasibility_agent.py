import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from main import app
from schemas.feasibility_study import FeasibilityStudyV2
from use_cases.cash_flow import build_cash_flow_request_use_case, run_cash_flow_use_case


def load_example():
    path = Path(__file__).parents[1] / "data" / "examples" / "daegu_bondong_mixed_use_pf.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_logistics_example():
    path = Path(__file__).parents[1] / "data" / "examples" / "dudong_lg_logistics_pf.json"
    return json.loads(path.read_text(encoding="utf-8"))


def bondong_project_id(client: TestClient) -> str:
    """Find (or seed) the Daegu Bondong example project id — hermetic against a
    drifted runtime DB. Uses .get() so a project without project.name never
    raises KeyError while scanning the shared store."""
    for item in client.get("/projects/").json():
        if (item.get("project") or {}).get("name") == "Daegu Bondong Mixed-Use PF":
            return item["project"]["id"]
    created = client.post("/projects/", json=load_example()).json()
    return created.get("id") or created["project"]["id"]


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
    assert result["feasibilityStudyV2Draft"]["financing"][1]["priority"] == "mezzanine"
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


def test_development_feasibility_agent_adds_logistics_analysis():
    result = DevelopmentFeasibilityAgent().analyze(load_logistics_example())

    logistics = result["logisticsAnalysis"]
    assert logistics["applicable"] is True
    assert logistics["schemaVersion"] == "realytics-logistics-analysis-v1"
    assert logistics["lease"]["annualNoi"] == 9_720_000_000
    assert logistics["exit"]["forwardSaleLtv"] == 100_000_000_000 / 194_400_000_000
    assert logistics["tenant"]["stabilityScore"] >= 80
    assert logistics["score"] >= 80
    assert result["feasibilityStudyV2Draft"]["raw_import"]["logisticsAnalysis"]["applicable"] is True


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (
            {"interest_rate": 0, "interestRate": 0.09, "fee_rate": 0, "feeRate": 0.02, "term_months": 0, "maturityMonths": 24},
            (0, 0, 0),
        ),
        ({"interestRate": 0, "feeRate": 0, "maturityMonths": 0}, (0, 0, 0)),
        (
            {"interest_rate": None, "interestRate": 0.05, "fee_rate": None, "feeRate": 0.01, "term_months": None, "maturityMonths": 24},
            (0.05, 0.01, 24),
        ),
        ({}, (None, None, None)),
    ],
)
def test_financing_terms_retain_explicit_zero_and_unknown_values_in_cash_flow_request(terms, expected):
    project = load_example()
    project["pf_structure"] = [{"tranche_id": "reviewed-senior", "name": "Reviewed senior", "priority": "senior", "amount": 70_000_000_000, **terms}]

    result = DevelopmentFeasibilityAgent().analyze(project)

    financing = result["feasibilityStudyV2Draft"]["financing"][0]
    mapped = result["cashFlowRunRequestDraft"]["pfStructure"][0]
    assert (financing["interest_rate"], financing["fee_rate"], financing["term_months"]) == expected
    assert (mapped["interestRate"], mapped["feeRate"], mapped["maturityMonths"]) == expected
    assert financing["repayment_source"] is None
    assert mapped["repaymentSource"] is None


def test_financing_terms_retain_reviewed_repayment_source():
    project = load_example()
    project["pf_structure"][0]["repayment_source"] = "reviewed_rental_income"

    result = DevelopmentFeasibilityAgent().analyze(project)

    assert result["feasibilityStudyV2Draft"]["financing"][0]["repayment_source"] == "reviewed_rental_income"
    assert result["cashFlowRunRequestDraft"]["pfStructure"][0]["repaymentSource"] == "reviewed_rental_income"


@pytest.mark.parametrize("priority", ["senior", "mezzanine", "junior", "equity", "bridge", "other"])
def test_explicit_financing_priority_wins_over_legacy_name_and_position(priority):
    project = load_example()
    project["pf_structure"] = [{"tranche_id": "reviewed-tranche", "name": "Senior facility", "priority": priority, "amount": 70_000_000_000}]

    result = DevelopmentFeasibilityAgent().analyze(project)

    assert result["feasibilityStudyV2Draft"]["financing"][0]["priority"] == priority
    request = result["cashFlowRunRequestDraft"]
    if priority == "equity":
        assert request["pfStructure"] == []
        assert request["sourcesAndUses"]["funding"]["equity"] == 70_000_000_000
        assert request["sourcesAndUses"]["funding"]["pfLoan"] == 0
    elif priority == "bridge":
        assert request["pfStructure"][0]["name"] == "bridge"
        assert request["sourcesAndUses"]["funding"]["bridgeLoan"] == 70_000_000_000
        assert request["sourcesAndUses"]["funding"]["pfLoan"] == 0
    else:
        assert request["pfStructure"][0]["name"] == priority
        assert request["sourcesAndUses"]["funding"]["pfLoan"] == 70_000_000_000


@pytest.mark.parametrize("amount_fields", [{"amount": None}, {}])
def test_missing_financing_amount_keeps_independent_analysis_and_blocks_pf_totals(amount_fields):
    project = load_example()
    project["pf_structure"].append({"tranche_id": "reviewed-missing", "name": "미입력 대출", "priority": "junior", **amount_fields})

    result = DevelopmentFeasibilityAgent().analyze(project)

    assert result["status"] == "completed_with_review"
    assert result["dataStatus"] == "partial"
    assert result["revenue"]["totalRevenue"] == 150_000_000_000
    assert result["costs"]["totalCost"] == 120_000_000_000
    pf = result["pfReadiness"]
    for key in ("totalPfAmount", "salesValueLtv", "ltvByRevenue", "ltcByCost", "equityRatio", "exitSaleRate", "estimatedExitSalesRate", "requiredEquity", "headroomToRecommendedLimit"):
        assert pf[key] is None, key
    assert pf["status"] == "needs_input"
    assert pf["isPfReviewReady"] is False
    assert pf["recommendedPfLimit"] > 0
    assert pf["landCostRatio"] > 0
    missing = "pf_structure.reviewed-missing.amount"
    assert pf["missingFields"] == [missing]
    assert missing in result["dataQuality"]["missingFields"]
    assert missing in result["downstreamEngineReadiness"]["blockingFields"]
    for key in ("canRunCashFlow", "canRunPfScenarios", "canRunUnsoldLtvMatrix"):
        assert result["downstreamEngineReadiness"][key] is False
    assert result["feasibilityStudyV2Draft"]["metrics"]["ltv"] is None
    assert result["feasibilityStudyV2Draft"]["financing"][-1]["amount"] is None
    assert result["cashFlowRunRequestDraft"] is None
    assert result["cashFlowRunResult"] is None
    assert result["feasibilityStudyV2Integration"]["mapperStatus"] == "financing_amount_required"
    with pytest.raises(ValueError, match="reviewed-missing.amount"):
        run_cash_flow_use_case(project, result)


def test_zero_financing_amount_remains_known_and_can_build_cash_flow_request():
    project = load_example()
    project["pf_structure"] = [{"tranche_id": "reviewed-zero", "name": "알려진 0", "priority": "senior", "amount": 0}]

    result = DevelopmentFeasibilityAgent().analyze(project)

    assert result["pfReadiness"]["totalPfAmount"] == 0
    assert result["pfReadiness"]["ltvByRevenue"] == 0
    assert result["pfReadiness"]["missingFields"] == []
    assert result["pfReadiness"]["status"] == "ready"
    assert result["cashFlowRunRequestDraft"]["pfStructure"][0]["amount"] == 0
    assert run_cash_flow_use_case(project)["summary"]["pfLimit"] == 0


@pytest.mark.parametrize("cleared_schedule", [[], {"construction_start": None, "completion": None},
    {"construction_start": {"planned_date": None, "value": "2026-01-01", "provenance": {"cell": "B2"}},
     "completion": {"planned_date": None, "value": "2028-01-01"}},
    [{"key": "completion", "planned_date": None}]])
def test_all_cleared_schedule_dates_do_not_crash_or_claim_ready(cleared_schedule):
    project = load_example()
    project["schedule"] = cleared_schedule
    for key in ("cashflow_input", "sales_schedule", "salesCollectionSchedule"):
        project.pop(key, None)

    result = DevelopmentFeasibilityAgent().analyze(project)

    assert result["feasibilityStudyV2Draft"]["schedule"] == []
    assert result["downstreamEngineReadiness"]["canRunCashFlow"] is False
    assert "schedule_or_cashflow_input" in result["downstreamEngineReadiness"]["blockingFields"]
    assert result["cashFlowRunResult"]["dataStatus"] == "partial"


def test_schedule_dates_with_metadata_are_normalized_without_stringifying_the_record():
    project = load_example()
    project["schedule"] = {
        "construction_start": {"planned_date": "2026-01-01", "provenance": {"cell": "B2"}},
        "completion": {"plannedDate": "2028-01-01", "provenance": {"cell": "B3"}},
    }
    result = DevelopmentFeasibilityAgent().analyze(project)
    schedule = {row["key"]: row["planned_date"] for row in result["feasibilityStudyV2Draft"]["schedule"]}
    assert schedule == {"construction_start": "2026-01-01", "completion": "2028-01-01"}
    assert result["cashFlowRunResult"]["monthlyCashFlow"]
    assert project["schedule"]["construction_start"]["provenance"] == {"cell": "B2"}


def test_missing_financing_amount_returns_partial_analysis_and_cash_flow_400():
    client = TestClient(app)
    project = load_example()
    project["pf_structure"].append({"tranche_id": "saved-missing", "name": "저장된 미입력", "priority": "junior", "amount": None})
    project["schedule"] = []
    created = client.post("/projects/", json=project)
    assert created.status_code == 200
    project_id = created.json().get("id") or created.json()["project"]["id"]

    analysis = client.post(f"/projects/{project_id}/development-feasibility/analyze", json={})
    assert analysis.status_code == 200
    assert analysis.json()["pfReadiness"]["status"] == "needs_input"
    assert analysis.json()["cashFlowRunResult"] is None
    cash_flow = client.post(f"/projects/{project_id}/cash-flow/run", json={})
    assert cash_flow.status_code == 400
    assert "pf_structure.saved-missing.amount" in cash_flow.json()["detail"]


def test_partial_financing_override_cannot_turn_missing_amount_into_zero():
    project = load_example()
    with pytest.raises(ValueError, match="override-missing.amount"):
        build_cash_flow_request_use_case(project, {"pf_structure": [{"tranche_id": "override-missing", "name": "검토 중", "priority": "senior", "amount": None}]})


def test_development_feasibility_endpoint_returns_analysis():
    client = TestClient(app)
    project_id = bondong_project_id(client)

    res = client.post(f"/projects/{project_id}/development-feasibility/analyze", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["developerReturns"]["profitMargin"] == 0.2
    assert body["pfReadiness"]["ltvByRevenue"] == 70_000_000_000 / 150_000_000_000
    assert body["downstreamEngineReadiness"]["canRunCashFlow"] is True


def test_cash_flow_run_endpoint_uses_development_feasibility_request_draft():
    client = TestClient(app)
    project_id = bondong_project_id(client)

    res = client.post(f"/projects/{project_id}/cash-flow/run", json={})

    assert res.status_code == 200
    body = res.json()
    assert body["analysisType"] == "realytics_cash_flow_engine_v1"
    assert body["summary"]["pfLimit"] == 70_000_000_000
    assert body["monthlyCashFlow"]
    assert body["reportFragments"]["cashFlowSummary"]
