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
