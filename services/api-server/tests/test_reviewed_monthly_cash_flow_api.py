from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core.reviewed_monthly_financing import MONTHLY_FINANCING_POLICY
from main import app
from routers import projects
from storage import get_project_record, list_calculation_run_records
from use_cases.cash_flow import build_cash_flow_request_use_case
from use_cases.feasibility_workbook_llm import FeasibilityReviewConflict, feasibility_review_state
from use_cases.approved_workbook_authority import GOVERNED_WORKBOOK_CORE_FIELDS


def working_project():
    project = json.loads((Path(__file__).parents[1] / "data/examples/daegu_bondong_mixed_use_pf.json").read_text(encoding="utf-8"))
    project["schedule"] = {"construction_start": "M0", "completion": "M2"}
    project["pf_structure"] = [{"tranche_id": "reviewed-senior", "name": "선순위", "priority": "senior",
        "amount": 100_000_000_000, "interest_rate": 0.12, "fee_rate": 0.01,
        "term_months": 2, "repayment_source": "분양대금"}]
    return project


def payload(project):
    return {"reviewedMonthlyFinancing": {"policy": dict(MONTHLY_FINANCING_POLICY),
        "reason": "월초 잔액·M0 기준 만기 조건의 검토", "expectedReviewHash": feasibility_review_state(project)["reviewHash"]}}


def test_reviewed_run_save_comparison_reopen_and_repeat_preserve_one_input_basis():
    client = TestClient(app)
    created = client.post("/projects/", json=working_project())
    assert created.status_code == 200, created.text
    pid = created.json().get("id") or created.json()["project"]["id"]
    original = deepcopy(get_project_record(pid))
    base = client.post(f"/projects/{pid}/cash-flow/run", json={})
    assert base.status_code == 200, base.text
    review = payload(get_project_record(pid))
    changed = client.post(f"/projects/{pid}/cash-flow/run", json=review)
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["analysisOnly"] is True
    assert body["reportEligible"] is False
    assert body["dependentExecutionStatus"] == "partial"
    assert body["dependentFailures"][0]["reason"] == "reviewed_monthly_dependent_policy_required"
    assert list(body["calculationRunIds"]) == ["cash_flow_engine"]
    assert body["monthlyFinancing"]["debtReconciliationDifference"] == pytest.approx(0)
    assert body["monthlyFinancing"]["costReconciliationDifference"] == pytest.approx(0, abs=0.001)
    rows = list_calculation_run_records(pid, engine_key="cash_flow_engine", limit=20)
    saved_run = next(row for row in rows if row["calculationRunId"] == body["calculationRunId"])
    assert saved_run["inputSnapshot"]["monthlyFinancing"] == MONTHLY_FINANCING_POLICY
    assert saved_run["inputSnapshot"]["reviewedMonthlyFinancingReview"]["reason"] == review["reviewedMonthlyFinancing"]["reason"]
    assert saved_run["inputSnapshot"]["sourceMetadata"]["approvalStatus"] == "unapproved"
    state = client.get(f"/projects/{pid}/cash-flow/comparison")
    assert state.status_code == 200, state.text
    saved_selection = client.post(f"/projects/{pid}/cash-flow/comparison", json={
        "baselineRunId": base.json()["calculationRunId"], "comparisonRunId": body["calculationRunId"],
        "expectedComparisonHash": state.json()["comparisonHash"],
    })
    assert saved_selection.status_code == 200, saved_selection.text
    reopened = client.get(f"/projects/{pid}/cash-flow/comparison").json()
    assert reopened["selection"]["saved"] is True
    assert reopened["comparison"]["comparison"]["policyMode"] == "reviewed_monthly_v1"
    assert reopened["comparison"]["status"] == "partial"
    assert "financing_policy_mismatch" in reopened["comparison"]["reasons"]
    assert get_project_record(pid)["feasibility_input"] == original["feasibility_input"]
    assert get_project_record(pid)["pf_structure"] == original["pf_structure"]
    # A selection/audit update does not invalidate the financial review hash.
    repeated = client.post(f"/projects/{pid}/cash-flow/run", json=review)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["calculationRunId"] != body["calculationRunId"]
    assert repeated.json()["summary"] == body["summary"]
    comparison = client.get(f"/projects/{pid}/cash-flow/comparison", params={
        "baselineRunId": body["calculationRunId"], "comparisonRunId": repeated.json()["calculationRunId"],
    }).json()["comparison"]
    assert comparison["businessInputsChanged"] is False
    assert comparison["inputChanges"] == []
    assert all(row["delta"] == 0 for row in comparison["metrics"] if row["status"] == "available")


@pytest.mark.parametrize("section,value", [
    ("product_mix", [{"type": "apartment", "units": 99}]),
    ("sales_assumptions", {"other_revenue": 12}),
    ("cost_assumptions", {"cost_per_sqm": 999}),
    ("canonicalFeasibilityStudyV2", {"raw_import": {"productMonthlySalesRateScenarios": {"changed": True}}}),
])
def test_additional_working_sources_invalidate_the_review_before_calculation(section, value, monkeypatch):
    project = working_project()
    review = payload(project)
    project[section] = value
    monkeypatch.setattr("use_cases.cash_flow.analyze_development_feasibility_use_case",
        lambda *args: (_ for _ in ()).throw(AssertionError("stale input must not calculate")))
    with pytest.raises(FeasibilityReviewConflict):
        build_cash_flow_request_use_case(project, review)


@pytest.mark.parametrize("changes", [{"reason": ""}, {"policy": None}, {"policy": {"method": "unverified"}},
    {"expectedReviewHash": None}, {"reason": "a" * 4001}])
def test_invalid_or_unselected_policy_is_rejected_without_overwriting_project(changes):
    project = working_project()
    before = deepcopy(project)
    review = payload(project)
    review["reviewedMonthlyFinancing"].update(changes)
    with pytest.raises(ValueError):
        build_cash_flow_request_use_case(project, review)
    assert project == before


@pytest.mark.parametrize("option", [
    "drawSchedule", "draw_schedule", "repaymentSchedule", "repayment_schedule", "amortization",
    "interestCapitalization", "interest_capitalization", "penaltyRate", "penalty_rate",
    "variableRate", "variable_rate", "interestRateSchedule", "interest_rate_schedule",
    "rateResetSchedule", "rate_reset_schedule",
])
@pytest.mark.parametrize("raw_shape", ["list", "dict"])
def test_reviewed_candidate_rejects_raw_advanced_conditions_before_normalization_without_saving(option, raw_shape):
    client = TestClient(app)
    project = working_project()
    project["pf_structure"][0][option] = {"specified": True}
    if raw_shape == "dict":
        project["pf_structure"] = project["pf_structure"][0]
    created = client.post("/projects/", json=project)
    assert created.status_code == 200, created.text
    pid = created.json().get("id") or created.json()["project"]["id"]
    original = deepcopy(get_project_record(pid))

    response = client.post(f"/projects/{pid}/cash-flow/run", json=payload(original))

    assert response.status_code == 400, response.text
    assert "지원하지 않습니다" in response.json()["detail"]
    assert get_project_record(pid) == original
    assert list_calculation_run_records(pid, engine_key="cash_flow_engine", limit=20) == []


def test_reviewed_candidate_keeps_benign_original_metadata_and_default_budget_calculation():
    client = TestClient(app)
    project = working_project()
    project["pf_structure"][0].update(
        notes="계약 설명", sourceMetadata={"drawSchedule": "원본 문서의 인용 항목"},
    )
    created = client.post("/projects/", json=project).json()
    pid = created.get("id") or created["project"]["id"]
    original = deepcopy(get_project_record(pid))

    response = client.post(f"/projects/{pid}/cash-flow/run", json=payload(original))

    assert response.status_code == 200, response.text
    assert response.json()["analysisOnly"] is True
    assert get_project_record(pid) == original
    projects.project_repository().update(pid, {"pf_structure": [{**original["pf_structure"][0], "draw_schedule": [{"monthIndex": 1}]}]})
    default = client.post(f"/projects/{pid}/cash-flow/run", json={})
    assert default.status_code == 200, default.text
    assert "monthlyFinancing" not in default.json()
    assert get_project_record(pid)["pf_structure"][0]["draw_schedule"] == [{"monthIndex": 1}]


def test_endpoint_stale_hash_returns_409_without_a_new_calculation():
    client = TestClient(app)
    created = client.post("/projects/", json=working_project()).json()
    pid = created.get("id") or created["project"]["id"]
    review = payload(get_project_record(pid))
    review["reviewedMonthlyFinancing"]["expectedReviewHash"] = "stale"
    response = client.post(f"/projects/{pid}/cash-flow/run", json=review)
    assert response.status_code == 409, response.text
    assert list_calculation_run_records(pid, engine_key="cash_flow_engine", limit=20) == []


def test_both_reviewed_and_complete_direct_monthly_requests_require_assumption_permission(monkeypatch):
    client = TestClient(app)
    created = client.post("/projects/", json=working_project()).json()
    pid = created.get("id") or created["project"]["id"]
    project = get_project_record(pid)
    review = payload(project)
    direct = build_cash_flow_request_use_case(project, review)
    def deny(request, permission):
        assert permission == "assumption:override"
        raise HTTPException(403, "가정 변경 권한이 없습니다.")
    monkeypatch.setattr(projects, "authorize_request", deny)
    for request_body in (review, direct):
        response = client.post(f"/projects/{pid}/cash-flow/run", json=request_body)
        assert response.status_code == 403, response.text
    assert list_calculation_run_records(pid, engine_key="cash_flow_engine", limit=20) == []


def test_explicit_working_review_keeps_the_approved_original_and_default_authority():
    project = working_project()
    project["canonicalFeasibilityStudyV2"] = {
        "project_name": "승인 기준안", "metrics": {"total_revenue": 1, "total_cost": 0},
        "input_references": {"approved_input_snapshot_id": "approved", "approved_input_snapshot_hash": "a" * 64},
        "raw_import": {"canonicalStatus": "approved", "parserV2Bridge": {"status": "approved",
            "approvedInputSnapshotId": "approved", "approvedInputSnapshotHash": "a" * 64,
            "approvedFields": list(GOVERNED_WORKBOOK_CORE_FIELDS)}},
    }
    original = deepcopy(project)
    default = build_cash_flow_request_use_case(project)
    assert default["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == 1
    reviewed = build_cash_flow_request_use_case(project, payload(project))
    assert reviewed["sourcesAndUses"]["revenue"]["totalSalesRevenue"] == project["feasibility_input"]["total_revenue"]
    assert reviewed["authorityStatus"] == "candidate"
    assert reviewed["sourceMetadata"]["approvalStatus"] == "unapproved"
    assert reviewed["reportEligible"] is False
    assert project == original


def test_direct_scenario_cannot_forge_stored_input_review_and_stale_reviews_are_not_attached():
    project = working_project()
    project["_meta"] = {"feasibilityFieldReviews": {"total_revenue": {
        "value": -999, "origin": "document_correction", "reason": "old unrelated input",
        "sourceCell": "old!A1", "sourceDocument": {"archiveId": "old"}}}}
    prepared = build_cash_flow_request_use_case(project, payload(project))
    assert prepared["sourceMetadata"]["inputReview"]["fieldReviews"] == {}
    prepared["sourceMetadata"]["inputReview"]["fieldReviews"] = {"total_revenue": {
        "origin": "document_correction", "sourceDocument": {"archiveId": "forged"}, "sourceCell": "fake!A1"}}
    external = build_cash_flow_request_use_case(project, prepared)
    assert "inputReview" not in external["sourceMetadata"]
