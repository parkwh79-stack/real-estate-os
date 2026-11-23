from copy import deepcopy

import pytest

from test_cash_flow_normalized_route import normalized_cash_flow  # noqa: F401
from test_dated_financing import _scenario as dated_scenario
from test_feasibility_review_authorization import analysis_authority  # noqa: F401
from test_normalized_project_events import normalized_events  # noqa: F401
from test_rental_exit import scenario as rental_scenario


@pytest.mark.parametrize("model,assumptions,metric,expected", [
    ("dated_financing", dated_scenario(), "peakMaturityShortfall", 36_620),
    ("rental_exit", rental_scenario(), "distributableCash", 67),
])
def test_model_review_runs_server_calculation_and_reopens_exact_snapshot(
    normalized_cash_flow, model, assumptions, metric, expected,
):
    authority, _events, client, saved = normalized_cash_flow
    original = deepcopy(authority.row)
    payload = {"model": model, "assumptions": assumptions, "reason": "날짜와 금액 대사"}
    response = client.post("/projects/p/feasibility/model-review", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["summary"][metric] == expected
    assert result["analysisOnly"] and not result["reportEligible"]
    record = saved[0]
    assert record["inputSnapshot"]["assumptions"] == assumptions
    assert record["inputSnapshot"]["financialInputHash"] == result["financialInputHash"]
    assert record["resultSnapshot"]["summary"] == result["summary"]
    assert record["calculationRunId"] == result["calculationRunId"]
    reopened = client.get(f"/projects/p/calculation-runs?engine_key={model}&limit=5")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["records"] == [record]
    assert authority.row == original


@pytest.mark.parametrize("headers,status", [
    ({"x-test-role": "viewer"}, 403), ({"x-test-tenant": "tenant-b"}, 404),
])
def test_model_review_enforces_project_analysis_permissions(normalized_cash_flow, headers, status):
    _authority, _events, client, saved = normalized_cash_flow
    result = client.post("/projects/p/feasibility/model-review", headers=headers, json={
        "model": "dated_financing", "assumptions": dated_scenario(), "reason": "상환 검토",
    })
    assert result.status_code == status
    assert saved == []
    if status == 404:
        report = client.get("/projects/p/feasibility-input/workbook-reconciliation", headers=headers)
        assert report.status_code == 404


def test_model_review_rejects_unknown_policy_and_missing_inputs(normalized_cash_flow):
    _authority, _events, client, saved = normalized_cash_flow
    for assumptions in ({}, {**dated_scenario(), "reportEligible": True}):
        response = client.post("/projects/p/feasibility/model-review", json={
            "model": "dated_financing", "assumptions": assumptions, "reason": "대출 검토",
        })
        assert response.status_code == 422, response.text
    result = client.get("/projects/p/feasibility-input/workbook-reconciliation")
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "source_unavailable"
    assert saved == []
