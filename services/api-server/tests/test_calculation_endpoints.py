from fastapi.testclient import TestClient
import pytest

from main import app


def first_project_id(client: TestClient) -> str:
    return client.get("/projects/").json()[0]["project"]["id"]


def test_cashflow_endpoint_returns_monthly_rows_and_max_minus():
    client = TestClient(app)
    project_id = first_project_id(client)

    res = client.post(
        f"/projects/{project_id}/cashflow/calculate",
        json={
            "initial_cash": 0,
            "revenue_schedule": [{"period": "M1", "amount": 100}, {"period": "M2", "amount": 40}],
            "cost_schedule": [{"period": "M1", "amount": 150}, {"period": "M2", "amount": 20}],
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["period_count"] == 2
    assert body["max_minus"] == -50
    assert body["max_minus_period"] == "M1"
    assert body["funding_gap"] == 50

    runs = client.get(f"/projects/{project_id}/calculation-runs?engine_key=cashflow").json()["records"]
    run = next(record for record in runs if record["calculationRunId"] == body["calculationRunId"])
    assert run["resultSnapshot"]["period_count"] == 2
    monthly = client.get(
        f"/projects/{project_id}/deal-state-records?record_type=metric&calculation_run_id={body['calculationRunId']}"
    ).json()["records"]
    assert any(row["key"] == "cashflow.monthly.M1.netCashFlow" for row in monthly)


def test_pf_structure_endpoint_returns_metric_statuses():
    client = TestClient(app)
    project_id = first_project_id(client)

    res = client.post(
        f"/projects/{project_id}/pf/structure",
        json={
            "tranches": [{"tranche": "A", "amount": 700, "interest_rate": 0.06, "fee_rate": 0.01, "priority": 1}],
            "total_revenue": 1000,
            "total_cost": 900,
            "net_operating_income": 70,
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["total_pf_amount"] == 700
    assert body["metrics"]["ltv"]["value"] == 0.7
    assert body["metrics"]["ltv"]["status"] == "caution"
    assert body["tranches"][0]["all_in_cost"] == pytest.approx(0.07)


def test_risk_assess_endpoint_returns_basic_matrix():
    client = TestClient(app)
    project_id = first_project_id(client)

    res = client.post(
        f"/projects/{project_id}/risk/assess",
        json={
            "risk_items": [
                {
                    "category": "Financing Risk",
                    "item": "High leverage",
                    "severity": 5,
                    "probability": 4,
                    "mitigation": "Increase equity.",
                }
            ]
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["matrixType"] == "basic_real_estate_pf_risk_matrix"
    assert body["overall"]["overall_grade"] == "Critical"
    assert body["categories"]
    assert len(body["heatmap"]) == 25


def test_scenario_restructuring_endpoint_returns_cure_plan():
    client = TestClient(app)
    project_id = first_project_id(client)

    res = client.post(
        f"/projects/{project_id}/scenarios/restructuring",
        json={
            "scenarioSetId": "api-restructuring-test",
            "baseDate": "2026-05-27",
            "productMix": [
                {
                    "type": "Apartment",
                    "units": 100,
                    "avgUnitPrice": 1_200_000_000,
                    "totalRevenue": 120_000_000_000,
                }
            ],
            "pfStructure": [
                {
                    "tranche": "Senior",
                    "amount": 100_000_000_000,
                    "priority": 1,
                    "interestRate": 0.06,
                    "feeRate": 0.01,
                },
                {
                    "tranche": "Mezzanine",
                    "amount": 20_000_000_000,
                    "priority": 2,
                    "interestRate": 0.08,
                    "feeRate": 0.02,
                },
            ],
            "scenarioDrivers": {
                "saleRates": [0.4, 0.5, 0.7, 1.0],
                "discountRates": [0.0, 0.1, 0.2],
            },
            "options": {"seniorBuffer": 1_000_000_000, "unsoldLtvTarget": 0.90},
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["analysisType"] == "pf_restructuring_plan"
    assert body["seniorShortfallFix"]["status"] == "action_required"
    assert body["seniorShortfallFix"]["requiredSeniorReduction"] > 0
    assert body["unsoldLtvFix"]["status"] == "action_required"
    assert body["unsoldLtvFix"]["requiredPfReductionOrEquityIncrease"] > 0
    assert body["approvalPath"]["currentStatus"] in {"rework_required", "conditional_approval_candidate"}


def test_discount_stress_endpoint_persists_calculation_run_and_metrics():
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Discount Stress Registry Test", "type": "mixed_use"},
            "feasibility_input": {
                "total_revenue": 120_000_000_000,
                "land_cost": 30_000_000_000,
                "construction_cost": 45_000_000_000,
                "finance_cost": 5_000_000_000,
                "total_cost": 90_000_000_000,
            },
            "pf_structure": [
                {"tranche": "Senior", "amount": 95_000_000_000, "priority": 1, "interestRate": 0.06, "feeRate": 0.01}
            ],
        },
    )
    project_id = created.json()["project"]["id"]

    res = client.post(
        f"/projects/{project_id}/stress-test/run",
        json={
            "pfAmount": 95_000_000_000,
            "inputEvidenceIds": ["evidence:discount-stress-input"],
            "assumptionIds": ["assumption:discount-stress-base"],
            "sourceLocator": {"documentId": "stress-input", "page": 4},
        },
    )

    assert res.status_code == 200
    body = res.json()
    run_id = body["calculationRunId"]
    assert run_id

    runs_res = client.get(f"/projects/{project_id}/calculation-runs?engine_key=discount_stress")
    assert runs_res.status_code == 200
    runs = runs_res.json()["records"]
    run = next(record for record in runs if record["calculationRunId"] == run_id)
    assert run["engineKey"] == "discount_stress"
    assert run["engineVersion"] == "discount_stress_v1"
    assert run["inputEvidenceIds"] == ["evidence:discount-stress-input"]
    assert run["assumptionIds"] == ["assumption:discount-stress-base"]
    assert run["sourceLocator"] == {"documentId": "stress-input", "page": 4}

    metrics_res = client.get(f"/projects/{project_id}/deal-state-records?record_type=metric&calculation_run_id={run_id}")
    assert metrics_res.status_code == 200
    metrics = metrics_res.json()["records"]
    metric_keys = {row["key"] for row in metrics}
    assert "discount_stress.summary.discountStressStageCount" in metric_keys
    discount10 = next(row for row in metrics if row["key"] == "discount_stress.discount10.exitSaleRate")
    assert discount10["value"] == 0.6
    assert discount10["sourceLocator"]["sourceType"] == "discount_stress_scenario"

    client.delete(f"/projects/{project_id}")


def test_recovery_scenario_persists_engine_runs_and_risk_locators():
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Scenario Engine Registry Test", "type": "mixed_use"},
            "feasibility_input": {"total_revenue": 120_000_000_000, "total_cost": 90_000_000_000},
            "pf_structure": [
                {"tranche": "Senior", "amount": 100_000_000_000, "priority": 1, "interestRate": 0.06, "feeRate": 0.01},
                {"tranche": "Mezzanine", "amount": 20_000_000_000, "priority": 2, "interestRate": 0.08, "feeRate": 0.02},
            ],
        },
    )
    project_id = created.json()["project"]["id"]

    res = client.post(
        f"/projects/{project_id}/scenarios/recovery",
        json={
            "scenarioSetId": "calculation-run-registry-test",
            "productMix": [
                {"type": "Apartment", "units": 100, "avgUnitPrice": 1_200_000_000, "totalRevenue": 120_000_000_000}
            ],
            "pfStructure": [
                {"tranche": "Senior", "amount": 100_000_000_000, "priority": 1, "interestRate": 0.06, "feeRate": 0.01},
                {"tranche": "Mezzanine", "amount": 20_000_000_000, "priority": 2, "interestRate": 0.08, "feeRate": 0.02},
            ],
            "scenarioDrivers": {
                "saleRates": [0.4, 0.5, 0.7],
                "discountRates": [0.0, 0.1, 0.2],
            },
            "inputEvidenceIds": ["evidence:scenario-input"],
            "assumptionIds": ["assumption:scenario-base"],
        },
    )

    assert res.status_code == 200
    body = res.json()
    run_ids = body["calculationRunIds"]
    assert {"waterfall", "unsold_ltv", "tranche_recovery"} <= set(run_ids)

    for engine_key, run_id in run_ids.items():
        runs_res = client.get(f"/projects/{project_id}/calculation-runs?engine_key={engine_key}")
        assert runs_res.status_code == 200
        run = next(record for record in runs_res.json()["records"] if record["calculationRunId"] == run_id)
        assert run["engineVersion"] == "calculation_scenario_engine_v1"
        assert run["inputEvidenceIds"] == ["evidence:scenario-input"]
        assert run["assumptionIds"] == ["assumption:scenario-base"]
        assert run["sourceLocator"]["scenarioSetId"] == "calculation-run-registry-test"

    tranche_metrics_res = client.get(
        f"/projects/{project_id}/deal-state-records?record_type=metric&calculation_run_id={run_ids['tranche_recovery']}"
    )
    assert tranche_metrics_res.status_code == 200
    tranche_metrics = tranche_metrics_res.json()["records"]
    senior_metric = next(row for row in tranche_metrics if row["key"] == "tranche_recovery.senior")
    assert 0 <= senior_metric["value"] <= 1
    assert senior_metric["sourceLocator"]["sourceType"] == "scenario_tranche_recovery_row"

    waterfall_risks_res = client.get(
        f"/projects/{project_id}/deal-state-records?record_type=risk_issue&calculation_run_id={run_ids['waterfall']}"
    )
    assert waterfall_risks_res.status_code == 200
    waterfall_risks = waterfall_risks_res.json()["records"]
    senior_shortfall = next(row for row in waterfall_risks if row["key"] == "SENIOR_SHORTFALL")
    assert senior_shortfall["sourceLocator"]["sourceType"] == "scenario_waterfall_row"
    assert "waterfallScenarios" in senior_shortfall["sourceLocator"]["path"]

    unsold_risks_res = client.get(
        f"/projects/{project_id}/deal-state-records?record_type=risk_issue&calculation_run_id={run_ids['unsold_ltv']}"
    )
    assert unsold_risks_res.status_code == 200
    unsold_risks = unsold_risks_res.json()["records"]
    unsold_breach = next(row for row in unsold_risks if row["key"] == "UNSOLD_LTV_BREACH")
    assert unsold_breach["sourceLocator"]["sourceType"] == "scenario_matrix_cell"
    assert "unsoldLtvMatrix" in unsold_breach["sourceLocator"]["path"]

    client.delete(f"/projects/{project_id}")
