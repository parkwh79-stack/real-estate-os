"""Saved requests reproduce calculations without consulting a changed project."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from agents.scenario_engine_agent import ScenarioEngineAgent
from agents.stress_test_agent import StressTestAgent
from core.calculations import calculate_pf_metrics, calculate_return_metrics
from main import app
from schemas.scenario import ScenarioSetRequest
from schemas.stress_test import StressTestRequest
from use_cases.calculation_run_capture import scenario_engine_run_payloads, stress_test_run_payload


@pytest.fixture
def project():
    client = TestClient(app)
    source = {
        "project": {"name": "Synthetic calculation snapshot regression", "type": "mixed_use"},
        "feasibility_input": {
            "total_revenue": 2800, "total_cost": 1800, "land_cost": 640,
            "construction_cost": 850, "finance_cost": 60, "other_cost": 250,
            "net_operating_income": 70, "interest_expense": 10,
            "project_cashflows": [-100, 0, 120], "equity_cashflows": [-30, 0, 40],
            "periods_per_year": 1,
        },
        "pf_structure": [{"tranche": "Senior", "amount": 1200, "priority": 1,
                          "interest_rate": .058, "fee_rate": .01}],
        "product_mix": [{"name": "Apartment", "unit_count": 100, "avg_unit_price": 28,
                         "total_revenue": 2800}],
    }
    created = client.post("/projects/", json=source)
    assert created.status_code == 200, created.text
    project_id = created.json()["project"]["id"]
    yield client, project_id, source
    assert client.delete(f"/projects/{project_id}").status_code == 200


def saved_run(client, project_id, run_id):
    response = client.get(f"/projects/{project_id}/calculation-runs")
    assert response.status_code == 200, response.text
    return next(run for run in response.json()["records"] if run["calculationRunId"] == run_id)


def change_project(project):
    client, project_id, source = project
    changed = deepcopy(source)
    changed["feasibility_input"]["total_revenue"] = 9000
    changed["feasibility_input"]["project_cashflows"] = [-100, 0, 200]
    changed["pf_structure"][0]["amount"] = 50
    changed["product_mix"][0]["total_revenue"] = 9000
    response = client.put(f"/projects/{project_id}", json=changed)
    assert response.status_code == 200, response.text


def test_pf_and_child_runs_freeze_merged_input_and_zero_override(project):
    client, project_id, source = project
    response = client.post(f"/projects/{project_id}/pf/structure", json={"finance_cost": 0, "noi": 0})
    assert response.status_code == 200, response.text
    body = response.json()
    records = [saved_run(client, project_id, run_id) for run_id in body["calculationRunIds"].values()]
    change_project(project)
    for run in records:
        snapshot = run["inputSnapshot"]
        assert snapshot["tranches"] == source["pf_structure"]
        assert snapshot["total_revenue"] == 2800
        assert snapshot["total_cost"] == 1800
        assert snapshot["finance_cost"] == snapshot["other_priority_costs"] == snapshot["net_operating_income"] == 0
        assert run["inputDigest"]["capturePolicy"] == "effective_request_v1"
        assert calculate_pf_metrics(snapshot)["metrics"] == run["resultSnapshot"]["metrics"]
        assert saved_run(client, project_id, run["calculationRunId"]) == run
    newer = client.post(f"/projects/{project_id}/pf/structure", json={}).json()
    assert newer["calculationRunId"] != body["calculationRunId"]
    assert newer["ltv"] != body["ltv"]
    assert saved_run(client, project_id, body["calculationRunId"]) == records[0]


def test_returns_freeze_full_cashflow_series_and_project_inputs(project):
    client, project_id, _ = project
    response = client.post(f"/projects/{project_id}/returns/calculate", json={"net_operating_income": 0})
    assert response.status_code == 200, response.text
    run = saved_run(client, project_id, response.json()["calculationRunId"])
    change_project(project)
    assert run["inputSnapshot"]["project_cashflows"] == [-100, 0, 120]
    assert run["inputSnapshot"]["net_operating_income"] == 0
    assert calculate_return_metrics(run["inputSnapshot"]) == run["resultSnapshot"]
    assert saved_run(client, project_id, run["calculationRunId"]) == run


@pytest.mark.parametrize("route,engine", [("waterfall", "waterfall"), ("unsold-ltv-matrix", "unsold_ltv"), ("recovery", None)])
def test_scenarios_freeze_resolved_project_inputs_and_default_drivers(project, route, engine):
    client, project_id, _ = project
    response = client.post(f"/projects/{project_id}/scenarios/{route}", json={
        "scenarioDrivers": {"additionalPriorityCosts": 0},
        "inputEvidenceIds": ["synthetic:evidence"], "assumptionIds": ["synthetic:assumption"],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    run_ids = {engine: body["calculationRunId"]} if engine else body["calculationRunIds"]
    records = [saved_run(client, project_id, run_id) for run_id in run_ids.values()]
    change_project(project)
    for run in records:
        snapshot = run["inputSnapshot"]
        assert snapshot["productMix"][0]["totalRevenue"] == 2800
        assert snapshot["pfStructure"][0]["amount"] == 1200
        assert snapshot["scenarioSetId"] == body["scenarioSetId"]
        assert snapshot["scenarioDrivers"]["saleRates"] == [.4, .5, .6, .7, .8, 1]
        assert snapshot["waterfallRule"]["includeInterest"] is True
        assert run["inputEvidenceIds"] == ["synthetic:evidence"]
        assert run["assumptionIds"] == ["synthetic:assumption"]
        replay = ScenarioEngineAgent().run_scenario_set(ScenarioSetRequest(**snapshot)).model_dump()
        payload = scenario_engine_run_payloads(replay, engines=[run["engineKey"]])[0]
        assert payload["result"] == run["resultSnapshot"]
        assert saved_run(client, project_id, run["calculationRunId"]) == run


@pytest.mark.parametrize("overrides", [{}, {"hurdleRate": 0, "schedule": {"months": 24},
    "scenarios": [{"key": "user", "name": "사용자 가정", "saleRate": 1, "discountRate": 0}]}])
def test_stress_preserves_schedule_drivers_and_all_result_rows(project, overrides):
    client, project_id, _ = project
    response = client.post(f"/projects/{project_id}/stress-test/run", json=overrides)
    assert response.status_code == 200, response.text
    body = response.json()
    run = saved_run(client, project_id, body["calculationRunId"])
    snapshot = run["inputSnapshot"]
    change_project(project)
    assert snapshot["totalRevenue"] == 2800
    assert snapshot["pfStructure"][0]["amount"] == 1200
    assert snapshot["schedule"]["months"] == (24 if overrides else 36)
    assert snapshot["scenarios"][0]["key"] == ("user" if overrides else "base")
    assert snapshot["hurdleRate"] == (0 if overrides else .12)
    assert run["resultSnapshot"]["scenarios"] == body["scenarios"]
    replay = StressTestAgent().run_stress_test(StressTestRequest(**snapshot)).model_dump()
    replay["createdAt"] = body["createdAt"]  # Execution time is not a financial result.
    assert stress_test_run_payload(replay)["result"] == run["resultSnapshot"]
    assert saved_run(client, project_id, run["calculationRunId"]) == run


def test_cashflow_dependents_keep_parent_lineage_and_replay_their_saved_requests(project):
    from test_cash_flow_dependencies import request_data

    client, project_id, _ = project
    response = client.post(f"/projects/{project_id}/cash-flow/run", json=request_data())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dependentExecutionStatus"] == "completed"
    anchor = saved_run(client, project_id, body["calculationRunId"])
    change_project(project)
    for engine, run_id in body["calculationRunIds"].items():
        if engine == "cash_flow_engine":
            continue
        run = saved_run(client, project_id, run_id)
        snapshot = run["inputSnapshot"]
        assert snapshot["parentCalculationRunId"] == anchor["calculationRunId"]
        assert snapshot["financialInputHash"] == anchor["financialInputHash"]
        assert snapshot["baseCashFlowRequest"]["sourcesAndUses"] == anchor["inputSnapshot"]["sourcesAndUses"]
        assert run["inputDigest"]["capturePolicy"] == "effective_request_v1"
        if engine == "discount_stress":
            replay = StressTestAgent().run_stress_test(StressTestRequest(**snapshot)).model_dump()
            replay["createdAt"] = run["resultSnapshot"]["createdAt"]
            expected = stress_test_run_payload(replay)["result"]
        else:
            replay = ScenarioEngineAgent().run_scenario_set(ScenarioSetRequest(**snapshot)).model_dump()
            expected = scenario_engine_run_payloads(replay, engines=[engine])[0]["result"]
        assert expected == run["resultSnapshot"]
