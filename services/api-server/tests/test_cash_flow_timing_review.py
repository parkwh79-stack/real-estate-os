from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
from core.reviewed_monthly_financing import MONTHLY_FINANCING_POLICY
from main import app
from routers import projects
from storage import get_project_record, list_calculation_run_records
from use_cases.cash_flow_comparison import content_hash
from use_cases.cash_flow_timing import apply_timing_to_monthly_request, timing_source, validate_timing, validate_timing_source


def _project():
    # MOCK-DATA: a tiny independent source sequence with one undated event.
    basis = {"schemaVersion": "cash-flow-period-basis-v1", "kind": "source_sequence",
        "periods": [{"sourcePeriodIndex": index, "label": label, "periodKind": kind,
                     "sourceMonthNumber": month, "sourceLocator": {"sourceName": "합성 시간 검증.xlsx", "sheet": "CF", "cell": cell}}
                    for index, label, kind, month, cell in ((0, "토지계약", "event", None, "F4"),
                        (1, "1", "monthly", 1, "G4"), (2, "2", "monthly", 2, "H4"))],
        "scheduleBindings": {"construction_start": {"sourcePeriodIndex": 1, "sourceMonthNumber": 1},
                             "completion": {"sourcePeriodIndex": 2, "sourceMonthNumber": 2}},
        "warnings": ["사건 날짜 미확인"]}
    study = {"project_name": "합성 시간 검토", "asset_type": "apartment",
        "metrics": {"total_revenue": 300, "total_cost": 109},
        "revenue_items": [{"key": "sales", "label": "분양", "category": "sales", "amount": 300}],
        "cost_items": [{"key": "land", "label": "토지비", "category": "land", "amount": 100},
                       {"key": "finance", "label": "금융비", "category": "finance", "amount": 9}],
        "schedule": [{"key": "construction_start", "label": "착공", "month_index": 1},
                     {"key": "completion", "label": "준공", "month_index": 2}],
        "monthly_cash_flow": [{"month_index": 0, "period": "토지계약", "cash_out": {"토지비": 100}},
                              {"month_index": 1, "period": "1", "cash_out": {"금융비": 9}},
                              {"month_index": 2, "period": "2", "cash_in": {"분양": 300}}],
        "raw_import": {"cashFlowPeriodBasis": basis, "sourceWorkbook": "합성 시간 검증.xlsx"}}
    return {"project": {"name": "합성 시간 검토"}, "feasibilityStudyV2Draft": study,
        "feasibility_input": {"total_revenue": 300, "total_cost": 109, "land_cost": 100,
                              "construction_cost": 0, "finance_cost": 9, "other_cost": 0},
        "schedule": {"construction_start": "2026-02-01", "completion": "2026-03-01"},
        "pf_structure": [{"tranche_id": "senior", "name": "합성 선순위", "priority": "senior", "amount": 200,
                          "interest_rate": .12, "fee_rate": 0, "term_months": 2, "repayment_source": "repayment_reserve"}]}


def _timing(project):
    return {"schemaVersion": "cash-flow-timing-assumptions-v1", "sourceBasisHash": timing_source(project)["sourceBasisHash"],
        "baseDate": "2026-01-01", "firstMonthlyMonthIndex": 1, "unlistedMonthsAreZero": True,
        "eventBindings": [{"sourcePeriodIndex": 0, "plannedDate": "2026-01-15", "monthIndex": 0}],
        "collections": [{"monthIndex": 2, "contract": 0, "interim": 0, "balance": 300}]}


def _monthly(state):
    return {"reviewedMonthlyFinancing": {"policy": dict(MONTHLY_FINANCING_POLICY),
        "reason": "합성 명시 시간·수금 가정으로 기존 월별 정책 검토", "expectedReviewHash": state["reviewHash"]}}


def _save(client, pid, state, timing, **review_changes):
    return client.post(f"/projects/{pid}/feasibility-workbook/apply-input", json={
        "expectedReviewHash": state["reviewHash"], "terms": {"cashFlowTiming": timing},
        "termReviews": {"cashFlowTiming": {"origin": "user_assumption", "reason": "합성 날짜·월차·수금 분석 가정", **review_changes}}})


def _create(client, project):
    created = client.post("/projects/", json={key: value for key, value in project.items()
                                             if key not in {"feasibilityStudyV2Draft", "cash_flow_timing"}})
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    # Fixture registration models a server-extracted source, not a client authority claim.
    projects.project_repository().update(pid, {key: value for key, value in project.items()
                                              if key in {"feasibilityStudyV2Draft", "cash_flow_timing"}})
    return pid


def test_api_partial_save_reopen_complete_monthly_run_and_old_budget_remain_separate():
    client = TestClient(app)
    original = _project()
    pid = _create(client, original)
    try:
        state = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        assert state["cashFlowTimingSource"]["basis"]["kind"] == "source_sequence"
        budget = client.post(f"/projects/{pid}/cash-flow/run", json={})
        assert budget.status_code == 200, budget.text
        old_run = next(row for row in list_calculation_run_records(pid, engine_key="cash_flow_engine")
                       if row["calculationRunId"] == budget.json()["calculationRunId"])
        old_hash = content_hash(old_run)
        # A partial assumption is useful saved work, not permission to invent dates.
        incomplete = _timing(original)
        incomplete["eventBindings"][0]["plannedDate"] = None
        saved = _save(client, pid, state, incomplete)
        assert saved.status_code == 200, saved.text
        reopened = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        assert reopened["terms"]["cashFlowTiming"] == incomplete
        review = reopened["termReviews"]["cashFlowTiming"]
        assert review["origin"] == "user_assumption" and review["needsReview"] is True
        assert review["approvalStatus"] == "not_requested"
        count = len(list_calculation_run_records(pid, engine_key="cash_flow_engine"))
        rejected = client.post(f"/projects/{pid}/cash-flow/run", json=_monthly(reopened))
        assert rejected.status_code == 400 and "모든 사건 날짜" in rejected.text
        assert len(list_calculation_run_records(pid, engine_key="cash_flow_engine")) == count
        assert client.post(f"/projects/{pid}/cash-flow/run", json={}).status_code == 200
        stale = _save(client, pid, state, _timing(original))
        assert stale.status_code == 409
        saved = _save(client, pid, reopened, _timing(original))
        assert saved.status_code == 200, saved.text
        state = saved.json()["reviewState"]
        calculated = client.post(f"/projects/{pid}/cash-flow/run", json=_monthly(state))
        assert calculated.status_code == 200, calculated.text
        run = next(row for row in list_calculation_run_records(pid, engine_key="cash_flow_engine")
                   if row["calculationRunId"] == calculated.json()["calculationRunId"])
        assert run["inputSnapshot"]["periodBasis"]["kind"] == "calendar_monthly"
        assert run["inputSnapshot"]["periodBasis"]["timingAssumptions"] == _timing(original)
        assert run["inputSnapshot"]["sourceMetadata"]["inputReview"]["termReviews"]["cashFlowTiming"] == state["termReviews"]["cashFlowTiming"]
        assert run["resultSnapshot"]["periodBasis"]["timingReview"]["reason"] == review["reason"]
        assert run["resultSnapshot"]["monthlyFinancing"]["budgetFinancialCost"] == 9
        assert run["resultSnapshot"]["monthlyFinancing"]["calculatedInterest"] > 0
        assert run["resultSnapshot"]["analysisOnly"] is True and run["resultSnapshot"]["reportEligible"] is False
        assert get_project_record(pid)["feasibilityStudyV2Draft"] == original["feasibilityStudyV2Draft"]
        assert not get_project_record(pid).get("canonicalFeasibilityStudyV2")
        reloaded_old = next(row for row in list_calculation_run_records(pid, engine_key="cash_flow_engine")
                            if row["calculationRunId"] == old_run["calculationRunId"])
        assert content_hash(reloaded_old) == old_hash
        cleared = _save(client, pid, state, None)
        assert cleared.status_code == 200, cleared.text
        cleared_state = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        assert cleared_state["terms"]["cashFlowTiming"] is None
        assert get_project_record(pid)["feasibilityStudyV2Draft"] == original["feasibilityStudyV2Draft"]
    finally:
        client.delete(f"/projects/{pid}")


@pytest.mark.parametrize("change", [
    {"firstMonthlyMonthIndex": -1}, {"firstMonthlyMonthIndex": 240}, {"baseDate": "2026-02-30"},
    {"eventBindings": [{"sourcePeriodIndex": 0, "plannedDate": "2026-02-01", "monthIndex": 0}]},
    {"eventBindings": [{"sourcePeriodIndex": 99, "plannedDate": None, "monthIndex": None}]},
    {"collections": [{"monthIndex": 2, "contract": 0, "interim": 0, "balance": 300}] * 2},
    {"collections": [{"monthIndex": 2, "contract": -1, "interim": 0, "balance": 300}]},
    {"sourceBasisHash": "f" * 64},
])
def test_invalid_timing_rejected_before_project_write(change):
    project = _project()
    before = deepcopy(project)
    with pytest.raises(ValueError):
        timing = validate_timing({**_timing(project), **change})
        validate_timing_source(project, timing)
    assert project == before


@pytest.mark.parametrize("change,match", [
    ({"collections": [{"monthIndex": 3, "contract": 0, "interim": 0, "balance": 300}]}, "기간"),
    ({"collections": [{"monthIndex": 2, "contract": 0, "interim": 0, "balance": 299}]}, "총매출"),
    ({"unlistedMonthsAreZero": False}, "0원"),
])
def test_incomplete_or_outside_collections_do_not_fall_back_to_asset_templates(change, match):
    project = _project()
    project["cash_flow_timing"] = {**_timing(project), **change}
    request = build_cash_flow_run_request_from_feasibility_study(project["feasibilityStudyV2Draft"])
    before = deepcopy(request)
    with pytest.raises(ValueError, match=match):
        apply_timing_to_monthly_request(project, request)
    assert request == before


def test_same_month_source_events_add_costs_without_overwriting_and_source_changes_invalidate():
    project = _project()
    study = project["feasibilityStudyV2Draft"]
    basis = study["raw_import"]["cashFlowPeriodBasis"]
    for period in basis["periods"][1:]:
        period["sourcePeriodIndex"] += 1
    for binding in basis["scheduleBindings"].values():
        binding["sourcePeriodIndex"] += 1
    basis["periods"].insert(1, {"sourcePeriodIndex": 1, "label": "토지잔금", "periodKind": "event", "sourceMonthNumber": None})
    study["monthly_cash_flow"][0]["cash_out"]["토지비"] = 40
    for row in study["monthly_cash_flow"][1:]:
        row["month_index"] += 1
    study["monthly_cash_flow"].append({"month_index": 1, "period": "토지잔금", "cash_out": {"토지비": 60}})
    timing = _timing(project)
    timing["eventBindings"].append({"sourcePeriodIndex": 1, "plannedDate": "2026-01-20", "monthIndex": 0})
    invalid = deepcopy(timing)
    invalid["eventBindings"][0]["plannedDate"] = "2026-01-25"
    with pytest.raises(ValueError, match="사건 날짜"):
        validate_timing_source(project, invalid)
    invalid = deepcopy(timing)
    invalid["eventBindings"][1].update(plannedDate="2026-03-01", monthIndex=2)
    with pytest.raises(ValueError, match="기간 순서"):
        validate_timing_source(project, invalid)
    project["cash_flow_timing"] = timing
    request = build_cash_flow_run_request_from_feasibility_study(study)
    mapped = apply_timing_to_monthly_request(project, request)
    assert sum(row["landCost"] for row in mapped["costSchedule"]["rows"] if row["monthIndex"] == 0) == 100
    assert [row["monthIndex"] for row in request["costSchedule"]["rows"]] == [0, 2, 1]
    changed = deepcopy(project)
    changed["feasibilityStudyV2Draft"]["raw_import"]["cashFlowPeriodBasis"]["periods"][0]["label"] = "다른 원본"
    with pytest.raises(ValueError, match="원본 기간이 바뀌었습니다"):
        apply_timing_to_monthly_request(changed, request)


def test_timing_save_cannot_claim_document_authority_or_silently_change_costs():
    project = _project()
    project["cash_flow_timing"] = _timing(project)
    request = build_cash_flow_run_request_from_feasibility_study(project["feasibilityStudyV2Draft"])
    request["sourcesAndUses"]["cost"]["landCost"] = 101
    with pytest.raises(ValueError, match="비용 배분"):
        apply_timing_to_monthly_request(project, request)
    client = TestClient(app)
    pid = _create(client, project)
    try:
        before = deepcopy(get_project_record(pid))
        forged_create = client.post("/projects/", json={"project": {"name": "합성 우회 차단"}, "cash_flow_timing": _timing(project)})
        assert forged_create.status_code == 400 and "server_managed_project_fields" in forged_create.text
        forged_update = client.put(f"/projects/{pid}", json={"cash_flow_timing": None})
        assert forged_update.status_code == 400 and "server_managed_project_fields" in forged_update.text
        assert get_project_record(pid) == before
        state = client.get(f"/projects/{pid}/feasibility-workbook/review").json()
        rejected = _save(client, pid, state, _timing(project), origin="document_correction")
        assert rejected.status_code == 400
        assert "분석용 사용자 가정" in rejected.text
    finally:
        client.delete(f"/projects/{pid}")
