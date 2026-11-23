"""Actual reviewed calculations keep their own numbers and assumptions in reports."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from main import app
from routers import projects
from storage import get_project_record, list_calculation_run_records
from use_cases.cash_flow_comparison import compare_cash_flow_runs, content_hash
from test_cash_flow_timing_review import _create, _monthly, _project, _save, _timing


def _state(client, pid):
    response = client.get(f"/projects/{pid}/feasibility-workbook/review")
    assert response.status_code == 200, response.text
    return response.json()


def _run(client, pid, state, *, monthly=False):
    payload = _monthly(state) if monthly else {"workingInputReview": {"expectedReviewHash": state["reviewHash"]}}
    response = client.post(f"/projects/{pid}/cash-flow/run", json=payload)
    assert response.status_code == 200, response.text
    return next(row for row in list_calculation_run_records(pid, engine_key="cash_flow_engine")
                if row["calculationRunId"] == response.json()["calculationRunId"])


def _field_review(client, pid, state, values, reason):
    response = client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json={
        "expectedReviewHash": state["reviewHash"], "feasibility_input": values,
        "fieldReviews": {key: {"origin": "user_assumption", "reason": reason} for key in values}})
    assert response.status_code == 200, response.text
    return response.json()["reviewState"]


def _comparison_report(client, pid, baseline, changed):
    response = client.get(f"/projects/{pid}/cash-flow/comparison")
    assert response.status_code == 200, response.text
    saved = client.post(f"/projects/{pid}/cash-flow/comparison", json={
        "baselineRunId": baseline["calculationRunId"], "comparisonRunId": changed["calculationRunId"],
        "expectedComparisonHash": response.json()["comparisonHash"]})
    assert saved.status_code == 200, saved.text
    response = client.post(f"/projects/{pid}/reports/cash-flow-comparison", json={"expectedComparisonHash": saved.json()["comparisonHash"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_real_working_budget_numbers_reviews_and_timing_survive_both_reports_and_downloads():
    client, original = TestClient(app), _project()
    pid = _create(client, original)
    try:
        state = _field_review(client, pid, _state(client, pid), {"finance_cost": 9}, "금융비 9원은 합성 원본 대조 전 분석 예산")
        saved = _save(client, pid, state, _timing(original))
        assert saved.status_code == 200, saved.text
        state = saved.json()["reviewState"]
        baseline = _run(client, pid, state)
        state = _field_review(client, pid, state, {"land_cost": 101, "total_cost": 110}, "토지비 1원 증가를 검토하는 합성 사용자 가정")
        changed = _run(client, pid, state)
        assert changed["inputSnapshot"]["sourceMetadata"]["sourceType"] == "saved_working_input_budget"
        assert "monthlyFinancing" not in changed["inputSnapshot"]
        frozen_runs = deepcopy((baseline, changed))
        comparison = _comparison_report(client, pid, baseline, changed)
        snapshot = comparison["report"]["cashFlowComparisonSnapshot"]
        assert snapshot["baselineRun"] == baseline and snapshot["comparisonRun"] == changed
        metrics = {row["key"]: row for row in snapshot["comparison"]["metrics"]}
        for side, run in (("baseline", baseline), ("comparison", changed)):
            summary, inputs = run["resultSnapshot"]["summary"], run["inputSnapshot"]
            for key in ("totalRevenue", "totalProjectCost", "requiredPfLimit", "totalPfDraw"):
                assert metrics[key][side + "Value"] == summary[key]
            assert metrics["financialCost"][side + "Value"] == sum(row["cashOut"]["financialCost"] for row in run["resultSnapshot"]["monthlyCashFlow"])
            assert metrics["totalPrincipalRepaid"][side + "Value"] is None
            assert metrics["outstandingDebt"][side + "Value"] is None
            assert summary["totalRevenue"] == inputs["sourcesAndUses"]["revenue"]["totalSalesRevenue"]
            assert summary["totalProjectCost"] == inputs["sourcesAndUses"]["cost"]["totalProjectCost"]
            frozen_key = "comparisonRun" if side == "comparison" else "baselineRun"
            assert snapshot[frozen_key]["inputSnapshot"]["sourceMetadata"]["inputReview"]["termReviews"]["cashFlowTiming"] == state["termReviews"]["cashFlowTiming"]
        assert metrics["totalProjectCost"]["delta"] == 1
        assert metrics["totalPrincipalRepaid"]["status"] == "unavailable"
        assert "사건·월차·분양수금 가정" in comparison["report_markdown"]
        assert "토지비 1원 증가를 검토하는 합성 사용자 가정" in comparison["report_markdown"]
        selection = {"calculationRunId": changed["calculationRunId"], "inputHash": content_hash(changed["inputSnapshot"]),
                     "resultHash": content_hash(changed["resultSnapshot"])}
        generated = client.post(f"/projects/{pid}/reports/generate", json={"cashFlowBasis": selection})
        assert generated.status_code == 200, generated.text
        pf = generated.json()
        assert pf["report"]["cashFlowCalculationSnapshot"] == changed
        assert pf["report"]["sections"]["summaryCashFlow"]["summary"] == changed["resultSnapshot"]["summary"]
        for row in pf["report"]["calculationReconciliation"]["rows"]:
            value = row["values"][0]
            assert value["calculationRunId"] == changed["calculationRunId"]
            assert value["value"] is not None
        assert "사건·월차·분양수금 가정" in pf["report_markdown"]
        # New project edits and a new comparison cannot silently rebind saved reports.
        state = _field_review(client, pid, state, {"total_revenue": 999}, "보고서 생성 뒤 후속 합성 가정")
        projects.project_repository().update(pid, {"_meta": {"cashFlowComparison": None}})
        for created in (comparison, pf):
            report_id = created["report_id"]
            reopened = client.get(f"/projects/{pid}/reports/{report_id}")
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()["report"] == created["report"]
            for fmt in ("json", "markdown", "html"):
                exported = client.post(f"/projects/{pid}/reports/{report_id}/exports", json={"format": fmt})
                assert exported.status_code == 200, exported.text
                download = client.get(f"/projects/{pid}/reports/{report_id}/exports/{exported.json()['export_id']}/download")
                assert download.status_code == 200
                if fmt == "json":
                    assert download.json()["report"] == created["report"]
                else:
                    assert "토지비 1원 증가를 검토하는 합성 사용자 가정" in download.text
                    assert "합성 날짜·월차·수금 분석 가정" in download.text
        assert (baseline, changed) == frozen_runs
        assert get_project_record(pid)["feasibilityStudyV2Draft"] == original["feasibilityStudyV2Draft"]
        assert not get_project_record(pid).get("canonicalFeasibilityStudyV2")
    finally:
        client.delete(f"/projects/{pid}")


@pytest.mark.parametrize("monthly", [False, True])
def test_review_hash_and_timing_review_metadata_are_not_financial_business_changes(monthly):
    client, project = TestClient(app), _project()
    pid = _create(client, project)
    try:
        state = _state(client, pid)
        saved = _save(client, pid, state, _timing(project))
        assert saved.status_code == 200, saved.text
        state = saved.json()["reviewState"]
        baseline = _run(client, pid, state, monthly=monthly)
        saved = _save(client, pid, state, _timing(project), reason="값은 같고 검토 설명만 추가한 합성 메모")
        assert saved.status_code == 200, saved.text
        changed = _run(client, pid, saved.json()["reviewState"], monthly=monthly)
        result = compare_cash_flow_runs(baseline, changed)
        assert result["businessInputsChanged"] is False
        assert result["inputChanges"] == []
        assert result["baseline"]["inputHash"] != result["comparison"]["inputHash"]
        assert result["baseline"]["businessInputHash"] == result["comparison"]["businessInputHash"]
        draft = _comparison_report(client, pid, baseline, changed)
        assert "값은 같고 검토 설명만 추가한 합성 메모" in draft["report_markdown"]
    finally:
        client.delete(f"/projects/{pid}")


def test_actual_timing_and_collections_still_change_business_inputs_and_show_saved_principal_repayment():
    client, project = TestClient(app), _project()
    pid = _create(client, project)
    try:
        state = _state(client, pid)
        saved = _save(client, pid, state, _timing(project))
        assert saved.status_code == 200, saved.text
        baseline = _run(client, pid, saved.json()["reviewState"], monthly=True)
        timing = _timing(project)
        timing.update(baseDate="2026-02-01", collections=[{"monthIndex": 1, "contract": 300, "interim": 0, "balance": 0}])
        timing["eventBindings"][0]["plannedDate"] = "2026-02-15"
        saved = _save(client, pid, _state(client, pid), timing)
        assert saved.status_code == 200, saved.text
        changed = _run(client, pid, saved.json()["reviewState"], monthly=True)
        result = compare_cash_flow_runs(baseline, changed)
        assert result["businessInputsChanged"] is True
        assert any("plannedDate" in row["path"] for row in result["inputChanges"])
        assert any("monthlyCollectionAmounts" in row["path"] for row in result["inputChanges"])
        metric = next(row for row in result["metrics"] if row["key"] == "totalPrincipalRepaid")
        assert metric["status"] == "available"
        assert metric["baselineValue"] == baseline["resultSnapshot"]["summary"]["totalPrincipalRepaid"]
        assert metric["comparisonValue"] == changed["resultSnapshot"]["summary"]["totalPrincipalRepaid"]
        draft = _comparison_report(client, pid, baseline, changed)
        assert "총 원금상환" in draft["report_markdown"]
        assert draft["report"]["cashFlowComparisonSnapshot"]["comparisonRun"]["inputSnapshot"]["periodBasis"]["timingAssumptions"] == timing
    finally:
        client.delete(f"/projects/{pid}")
