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
