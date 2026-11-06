"""Invalid financial requests return an input error before result persistence."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from main import app
from routers import projects


@pytest.mark.parametrize("endpoint,payload", [
    ("cash-sweep/simulate", {
        "monthlyOperatingCashflow": [0],
        "tranches": [
            {"tranche": "Senior", "amount": 100, "priority": 1},
            {"tranche": "Senior", "amount": 200, "priority": 2},
        ],
    }),
    ("credit-enhancement/evaluate", {"baseSeniorLtv": "Infinity"}),
])
def test_invalid_financial_request_returns_422_without_writing(monkeypatch, endpoint, payload):
    with TestClient(app) as client:
        created = client.post("/projects/", json={
            "project": {"name": "Financial boundary audit fixture"},
            "feasibility_input": {"total_revenue": 1000, "total_cost": 800},
        })
        assert created.status_code in {200, 201}
        data = created.json()
        project_id = data.get("id") or data["project"]["id"]
        before = deepcopy(projects._project_store()[project_id])

        def unexpected_write(*args, **kwargs):
            pytest.fail("Invalid financial input persisted a calculation")

        monkeypatch.setattr(projects, "_persist_calculation_run", unexpected_write)
        monkeypatch.setattr(projects, "_persist_scenario_calculation_runs", unexpected_write)
        response = client.post(f"/projects/{project_id}/{endpoint}", json=payload)

        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_financial_input"
        assert response.headers["cache-control"] == "no-store"
        json.dumps(response.json(), allow_nan=False)
        assert projects._project_store()[project_id] == before
