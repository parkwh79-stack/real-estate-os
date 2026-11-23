"""Explicit working-input budgets cannot silently replay a registered source plan."""
from copy import deepcopy

import pytest

from test_cash_flow_normalized_route import normalized_cash_flow
from test_feasibility_review_authorization import analysis_authority  # noqa: F401
from test_normalized_project_events import normalized_events  # noqa: F401
from use_cases import cash_flow
from use_cases.feasibility_workbook_llm import feasibility_review_state


def working_payload(project):
    return {"workingInputReview": {"expectedReviewHash": feasibility_review_state(project)["reviewHash"]}}


def test_saved_correction_recalculates_new_budget_without_mutating_original_plan_or_past_run(normalized_cash_flow, monkeypatch):
    authority, _events, client, saved = normalized_cash_flow
    original_plan = cash_flow.build_cash_flow_request_use_case(deepcopy(authority.row), {})
    frozen_original = deepcopy(original_plan)
    monkeypatch.setattr(cash_flow, "_registered_source_cash_flow_request", lambda _project: deepcopy(frozen_original))
    baseline = client.post("/projects/p/cash-flow/run", json={})
    assert baseline.status_code == 200, baseline.text
    baseline_record = deepcopy(next(row for row in saved if row["calculationRunId"] == baseline.json()["calculationRunId"]))
    delta = 100_000_000
    authority.row["feasibility_input"]["land_cost"] += delta
    authority.row["feasibility_input"]["total_cost"] += delta
    untouched = deepcopy(authority.row)
    changed = client.post("/projects/p/cash-flow/run", json=working_payload(authority.row))
    assert changed.status_code == 200, changed.text
    changed_record = next(row for row in saved if row["calculationRunId"] == changed.json()["calculationRunId"])
    inputs = changed_record["inputSnapshot"]
    assert inputs["sourcesAndUses"]["cost"]["totalProjectCost"] == original_plan["sourcesAndUses"]["cost"]["totalProjectCost"] + delta
    assert inputs["sourceMetadata"]["sourceType"] == "saved_working_input_budget"
    assert inputs["sourceMetadata"]["reportEligible"] is False
    assert inputs["workingInputReview"]["reviewHash"] == feasibility_review_state(authority.row)["reviewHash"]
    assert "inputReview" in inputs["sourceMetadata"]
    assert "monthlyFinancing" not in inputs and "capturedWorkbookCashFlow" not in inputs
    assert next(row for row in saved if row["calculationRunId"] == baseline.json()["calculationRunId"]) == baseline_record
    assert original_plan == frozen_original
    assert authority.row == untouched


def test_stale_working_input_cannot_create_a_calculation(normalized_cash_flow):
    authority, _events, client, saved = normalized_cash_flow
    stale = working_payload(authority.row)
    authority.row["feasibility_input"]["total_revenue"] += 1
    response = client.post("/projects/p/cash-flow/run", json=stale)
    assert response.status_code == 409
    assert saved == []


def test_reviewed_aggregate_pf_is_a_budget_limit_without_inventing_tranche_terms(normalized_cash_flow):
    authority, _events, client, saved = normalized_cash_flow
    authority.row["pf_structure"] = []
    amount = 118_000_000_000
    authority.row["expected_results"] = {"total_pf_amount": amount}
    review = {"value": amount, "origin": "user_assumption", "reason": "검증용 예산 한도, 실제 약정 아님"}
    authority.row["_meta"]["feasibilityFieldReviews"] = {"pf_amount": review}
    response = client.post("/projects/p/cash-flow/run", json=working_payload(authority.row))
    assert response.status_code == 200, response.text
    request = saved[0]["inputSnapshot"]
    assert request["sourcesAndUses"]["funding"]["pfLoan"] == amount
    assert request["sourceMetadata"]["aggregateFundingReview"]["scope"] == "analysis_limit_not_loan_agreement"
    assert request["sourceMetadata"]["reportEligible"] is False
    assert request.get("pfStructure") == []
    assert "monthlyFinancing" not in request
    assert response.json()["summary"]["pfLimit"] == amount


def test_unreviewed_aggregate_never_replaces_detailed_financing(normalized_cash_flow):
    authority, _events, client, saved = normalized_cash_flow
    authority.row["expected_results"] = {"total_pf_amount": 999_000_000_000}
    detailed = deepcopy(authority.row["pf_structure"])
    response = client.post("/projects/p/cash-flow/run", json=working_payload(authority.row))
    assert response.status_code == 200, response.text
    assert "aggregateFundingReview" not in saved[0]["inputSnapshot"]["sourceMetadata"]
    assert authority.row["pf_structure"] == detailed
    assert response.json()["summary"]["pfLimit"] != 999_000_000_000


@pytest.mark.parametrize("payload", [
    {"workingInputReview": {}},
    {"workingInputReview": {"expectedReviewHash": "x", "amount": 1}},
    {"workingInputReview": {"expectedReviewHash": "x"}, "cashFlowRunRequest": {}},
])
def test_working_budget_rejects_unreviewed_overrides(normalized_cash_flow, payload):
    _authority, _events, client, saved = normalized_cash_flow
    response = client.post("/projects/p/cash-flow/run", json=payload)
    assert response.status_code == 400
    assert saved == []


@pytest.mark.parametrize("headers, status", [({"x-test-role": "viewer"}, 403), ({"x-test-tenant": "tenant-b"}, 404)])
def test_working_budget_uses_existing_analysis_and_project_permissions(normalized_cash_flow, headers, status):
    authority, _events, client, saved = normalized_cash_flow
    response = client.post("/projects/p/cash-flow/run", json=working_payload(authority.row), headers=headers)
    assert response.status_code == status
    assert saved == []
