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


def test_missing_logistics_inputs_do_not_manufacture_zero_income_ltv_or_scores():
    project = load_logistics_example()
    project["project"] = {"type": "logistics"}
    project["pf_structure"] = []
    result = DevelopmentFeasibilityAgent().analyze(project)
    logistics = result["logisticsAnalysis"]
    assert logistics["status"] == "partial"
    assert logistics["lease"]["annualNoi"] is None
    assert logistics["exit"]["forwardSaleLtv"] is None
    assert logistics["exit"]["capRateExitValue"] is None
    assert logistics["score"] is None
    assert logistics["tenant"]["stabilityScore"] is None
    assert logistics["location"]["score"] is None
    assert logistics["spec"]["score"] is None
    assert logistics["spec"]["rampAvailable"] is None
    assert all(item["category"] == "Data Quality" for item in logistics["riskItems"])
    assert "TENANT_STABILITY_WEAK" not in logistics["riskFlags"]
    # Missing logistics data does not block independent, supplied feasibility.
    assert result["developerReturns"]["developmentProfit"] == 44_400_000_000


def test_logistics_explicit_zero_false_and_cleared_inputs_do_not_fall_back():
    project = load_logistics_example()
    project["project"].update({"noi": 0, "rent_per_py": 0, "operating_expenses": 0,
                              "distance_to_ic_km": 0, "distance_to_port_km": 0,
                              "ramp_available": False, "tenant_credit_rating": None})
    project["logistics"] = {"noi": 100, "rent_per_py": 100, "distance_to_ic_km": 99,
                            "ramp_available": True, "tenant_credit_rating": "AAA"}
    project["pf_structure"] = [{"amount": 0, "priority": "senior"}]
    analysis = DevelopmentFeasibilityAgent().analyze(project)["logisticsAnalysis"]
    assert analysis["lease"]["annualNoi"] == 0
    assert analysis["lease"]["noiBasis"] == "supplied_analysis_input"
    assert analysis["lease"]["rentPerPy"] == 0
    assert analysis["exit"]["capRateExitValue"] == 0
    assert analysis["exit"]["forwardSaleLtv"] == 0
    assert analysis["location"]["score"] == 100
    assert analysis["spec"]["rampAvailable"] is False
    assert analysis["spec"]["score"] == 70
    assert analysis["tenant"]["creditRating"] is None
    assert analysis["tenant"]["stabilityScore"] is None
    assert analysis["score"] is None


def test_logistics_computes_only_metrics_with_their_own_inputs():
    agent = DevelopmentFeasibilityAgent()
    data = {"project": {"type": "logistics"}, "logistics": {
        "leasable_area_py": 100, "rent_per_py": 20, "operating_expenses": 1000,
        "cap_rate": .05, "forward_sale_price": 100_000}}
    result = agent._logistics_analysis(data, [{"amount": None}])
    assert result["lease"]["annualNoi"] == 23_000
    assert result["exit"]["capRateExitValue"] == 460_000
    assert result["exit"]["forwardSaleLtv"] is None
    assert result["inputSources"]["rent_per_py"] == "logistics.rent_per_py"
    del data["logistics"]["operating_expenses"]
    missing_opex = agent._logistics_analysis(data, [{"amount": 50_000}])
    assert missing_opex["lease"]["annualNoi"] is None
    assert missing_opex["exit"]["capRateExitValue"] is None
    assert missing_opex["exit"]["forwardSaleLtv"] == .5


def test_logistics_child_runtime_recalculates_changed_input_and_replays_within_same_parent():
    from agents.orchestration_agent import OrchestrationAgent
    from agents.development_feasibility_agent import DEVELOPMENT_FEASIBILITY_AGENT_VERSION
    data = load_logistics_example()
    data["project"]["noi"] = None
    data["project"]["operating_expenses"] = 0
    agent = OrchestrationAgent()
    # Idempotency is intentionally scoped to the parent run; new workflows
    # have distinct IDs and are not expected to replay each other's children.
    run = {"run_id": "logistics-parent", "project_id": "logistics-cache-regression"}
    def execute():
        return agent._run_domain_agent(
            run, project_data=data, options={}, agent_id="DevelopmentFeasibilityAgent",
            task_type="development_feasibility_analysis", domain="feasibility_cashflow",
            input_snapshot={}, executor=lambda: DevelopmentFeasibilityAgent().analyze(data),
            output_schema_name="DevelopmentFeasibilityResult", deterministic_authority_required=True,
        )
    first = execute()
    first_noi = first["logisticsAnalysis"]["lease"]["annualNoi"]
    data["project"]["rent_per_py"] = 0
    changed = execute()
    assert first_noi > 0
    assert changed["logisticsAnalysis"]["lease"]["annualNoi"] == 0
    runtime = run["agentRuntimeExecutions"][-1]
    assert runtime["agentVersion"] == DEVELOPMENT_FEASIBILITY_AGENT_VERSION
    assert runtime["replayed"] is False
    again = execute()
    assert run["agentRuntimeExecutions"][-1]["replayed"] is True
    assert again == changed


def test_logistics_partial_result_is_persisted_with_the_consumed_inputs():
    from storage import list_calculation_run_records
    client = TestClient(app)
    data = load_logistics_example()
    data["project"] = {"name": "Synthetic partial logistics", "type": "logistics"}
    created = client.post("/projects/", json=data)
    assert created.status_code == 200, created.text
    project_id = created.json()["project"]["id"]
    response = client.post(f"/projects/{project_id}/development-feasibility/analyze", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    saved = next(row for row in list_calculation_run_records(project_id, engine_key="feasibility")
                 if row["calculationRunId"] == body["calculationRunId"])
    assert saved["resultSnapshot"]["logisticsAnalysis"] == body["logisticsAnalysis"]
    assert saved["inputSnapshot"]["project"]["type"] == "logistics"
    assert "rent_per_py" not in saved["inputSnapshot"]["project"]
    assert body["logisticsAnalysis"]["lease"]["annualNoi"] is None
    override = {"project": {"noi": 0, "cap_rate": .05}}
    changed = client.post(f"/projects/{project_id}/development-feasibility/analyze", json=override)
    assert changed.status_code == 200, changed.text
    runs = list_calculation_run_records(project_id, engine_key="feasibility")
    latest = next(row for row in runs if row["calculationRunId"] == changed.json()["calculationRunId"])
    assert latest["inputSnapshot"]["project"]["type"] == "logistics"
    assert latest["inputSnapshot"]["project"]["noi"] == 0
    replay = DevelopmentFeasibilityAgent().analyze(latest["inputSnapshot"])
    assert replay["logisticsAnalysis"] == latest["resultSnapshot"]["logisticsAnalysis"]
    assert next(row for row in runs if row["calculationRunId"] == body["calculationRunId"]) == saved


@pytest.mark.parametrize("key,value,section,metric", [
    ("cap_rate", 0, "exit", "capRateExitValue"),
    ("forward_sale_price", 0, "exit", "forwardSaleLtv"),
    ("distance_to_ic_km", -1, "location", "score"),
    ("lease_term_months", 12.5, "tenant", "stabilityScore"),
    ("ramp_available", "false", "spec", "score"),
    ("noi", "bad input", "lease", "annualNoi"),
    ("noi", "NaN", "lease", "annualNoi"),
    ("clear_height", True, "spec", "score"),
])
def test_invalid_logistics_inputs_remain_local_to_dependent_metrics(key, value, section, metric):
    project = load_logistics_example()
    project["project"][key] = value
    result = DevelopmentFeasibilityAgent().analyze(project)
    analysis = result["logisticsAnalysis"]
    assert analysis[section][metric] is None
    assert {"field": key, "reason": "invalid"} in analysis["inputIssues"]
    assert result["revenue"]["totalRevenue"] == 194_400_000_000


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


def test_pf_readiness_matches_canonical_debt_and_equity_classification():
    project = load_example()
    project["pf_structure"] = [
        {"tranche_id": "senior", "name": "Tr.A", "priority": "senior", "amount": 50_000_000_000},
        {"tranche_id": "bridge", "name": "Bridge", "priority": "bridge", "amount": 5_000_000_000},
        {"tranche_id": "equity", "name": "Equity", "priority": "equity", "amount": 10_000_000_000},
    ]

    result = DevelopmentFeasibilityAgent().analyze(project)

    pf = result["pfReadiness"]
    funding = result["cashFlowRunRequestDraft"]["sourcesAndUses"]["funding"]
    assert pf["totalPfAmount"] == funding["pfLoan"] + funding["bridgeLoan"] == 55_000_000_000
    assert pf["pfLoanAmount"] == funding["pfLoan"] == 50_000_000_000
    assert pf["bridgeLoanAmount"] == funding["bridgeLoan"] == 5_000_000_000
    assert pf["committedEquityAmount"] == funding["equity"] == 10_000_000_000
    assert pf["ltvByRevenue"] == 55_000_000_000 / 150_000_000_000
    assert pf["requiredEquity"] == 65_000_000_000


def test_unknown_equity_does_not_hide_known_debt_but_keeps_calculation_blocked():
    project = load_example()
    project["pf_structure"].append({"tranche_id": "missing-equity", "name": "Equity", "priority": "equity", "amount": None})

    result = DevelopmentFeasibilityAgent().analyze(project)

    assert result["pfReadiness"]["totalPfAmount"] == 70_000_000_000
    assert result["pfReadiness"]["committedEquityAmount"] is None
    assert result["pfReadiness"]["status"] == "needs_input"
    assert result["cashFlowRunRequestDraft"] is None


def test_logistics_ltv_uses_debt_without_counting_equity_as_a_loan():
    project = load_logistics_example()
    baseline = DevelopmentFeasibilityAgent().analyze(project)
    project["pf_structure"].append({"tranche_id": "owner-equity", "name": "Owner equity", "priority": "equity", "amount": 50_000_000_000})

    result = DevelopmentFeasibilityAgent().analyze(project)

    assert result["pfReadiness"]["totalPfAmount"] == baseline["pfReadiness"]["totalPfAmount"]
    assert result["logisticsAnalysis"]["exit"]["forwardSaleLtv"] == baseline["logisticsAnalysis"]["exit"]["forwardSaleLtv"]


@pytest.mark.parametrize("schedule", [
    {"construction_start": "M0", "completion": "M24"},
    {"construction_start": 0, "completion": 24},
    [{"key": "construction_start", "month_index": 0}, {"key": "completion", "month_index": 24}],
])
def test_month_schedule_reaches_cash_flow_calculation_without_calendar_fabrication(schedule):
    project = load_example()
    project["schedule"] = schedule

    result = DevelopmentFeasibilityAgent().analyze(project)

    assert len(result["cashFlowRunResult"]["monthlyCashFlow"]) == 25
    assert result["cashFlowRunResult"]["monthlyCashFlow"][-1]["period"] == "M24"
    assert all(not (row["planned_date"] or "").startswith("20") for row in result["feasibilityStudyV2Draft"]["schedule"])


def test_schedule_mapping_retains_provenance_and_explicit_calendar_reference():
    project = load_example()
    project["schedule"] = [
        {"key": "construction_start", "planned_date": "2026-01-01", "month_index": 0, "excel": {"sheet_name": "Schedule", "cell": "E25"}, "provenance": {"source_type": "xlsx", "cell": "E25"}},
        {"key": "completion", "planned_date": "M24"},
    ]

    result = DevelopmentFeasibilityAgent().analyze(project)

    start = result["feasibilityStudyV2Draft"]["schedule"][0]
    assert start["month_index"] == 0
    assert start["excel"] == {"sheet_name": "Schedule", "cell": "E25"}
    assert start["provenance"] == {"source_type": "xlsx", "cell": "E25"}
    assert result["cashFlowRunRequestDraft"]["schedule"]["construction_start"]["monthIndex"] == 0
    assert len(result["cashFlowRunResult"]["monthlyCashFlow"]) == 25


def test_invalid_mixed_schedule_returns_input_error_instead_of_server_failure():
    client = TestClient(app)
    project = load_example()
    created = client.post("/projects/", json=project)
    project_id = created.json().get("id") or created.json()["project"]["id"]

    response = client.post(f"/projects/{project_id}/development-feasibility/analyze", json={"schedule": {"construction_start": "2026-01-01", "completion": "M24"}})

    assert response.status_code == 422
    assert "기준" in response.json()["detail"]["message"]


@pytest.mark.parametrize("key", [1, 0, True])
def test_non_text_schedule_key_returns_supported_analysis_and_cash_flow_errors(key):
    client = TestClient(app)
    created = client.post("/projects/", json=load_example())
    project_id = created.json().get("id") or created.json()["project"]["id"]
    payload = {"schedule": [{"key": key, "month_index": 0}]}

    analysis = client.post(f"/projects/{project_id}/development-feasibility/analyze", json=payload)
    assert analysis.status_code == 422
    assert "문자열" in analysis.json()["detail"]["message"]
    cash_flow = client.post(f"/projects/{project_id}/cash-flow/run", json=payload)
    assert cash_flow.status_code == 400
    assert "문자열" in cash_flow.json()["detail"]


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
