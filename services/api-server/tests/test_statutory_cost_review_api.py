from copy import deepcopy

import pytest

from agents.cash_flow_engine import cash_flow_cost_schedule_for_review

from routers import projects
from test_cash_flow_normalized_route import normalized_cash_flow  # noqa: F401
from test_feasibility_review_authorization import analysis_authority  # noqa: F401
from test_normalized_project_events import normalized_events  # noqa: F401
from use_cases.cash_flow_dependencies import financial_input_hash
from use_cases.cash_flow_comparison import compare_cash_flow_runs


ARCHIVE = "a" * 64
FEE = 25_000


@pytest.fixture
def statutory_api(normalized_cash_flow, monkeypatch):
    authority, _events, client, saved = normalized_cash_flow
    authority.row["source_documents"] = [{"archiveId": ARCHIVE, "originalFileName": "부담금 고지서.pdf"}]
    monkeypatch.setattr(projects.PostgresCalculationRunRepository, "get_run",
                        lambda _repository, project_id, run_id: next(
                            (deepcopy(row) for row in saved
                             if row["projectId"] == project_id and row["calculationRunId"] == run_id), None),
                        raising=False)
    monkeypatch.setattr(projects.UploadArchiveRepository, "resolve_view_metadata",
                        lambda _repository, project_id, document: {"archiveId": document["archiveId"]}
                        if project_id == "p" else None)
    result = client.post("/projects/p/cash-flow/run", json={})
    assert result.status_code == 200, result.text
    base = result.json()
    payload = {
        "baseCalculationRunId": base["calculationRunId"],
        "expectedFinancialInputHash": base["financialInputHash"],
        "items": [{
            "key": "school_site_charge", "inclusion": "additional", "amountKrw": FEE,
            "amountKind": "notified", "paymentMonthIndex": 2,
            "archiveId": ARCHIVE, "location": "고지서 2쪽",
            "reason": "원본 사업비 소계에서 해당 고지액 미포함 확인",
        }],
    }
    return authority, client, saved, base, payload


@pytest.mark.parametrize("inclusion,delta", [("additional", FEE), ("included", 0), ("not_applicable", 0)])
def test_cost_review_saves_month_totals_and_reopens_exact_input(statutory_api, inclusion, delta):
    authority, client, saved, base, payload = statutory_api
    existing_project = deepcopy(authority.row)
    preview = client.get("/projects/p/statutory-cost-review")
    assert preview.status_code == 200, preview.text
    assert preview.json()["documents"] == [{"archiveId": ARCHIVE, "fileName": "부담금 고지서.pdf"}]
    assert preview.json()["basisRuns"][0]["calculationRunId"] == base["calculationRunId"]
    assert all(item["amountKrw"] is None for item in preview.json()["items"])
    before = len(saved)

    payload["items"][0]["inclusion"] = inclusion
    if inclusion == "not_applicable":
        payload["items"][0].update(amountKrw=None, amountKind=None, paymentMonthIndex=None)
    response = client.post("/projects/p/statutory-cost-review/calculate", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysisOnly"] and not result["reportEligible"]
    assert result["summary"]["totalProjectCost"] == pytest.approx(base["summary"]["totalProjectCost"] + delta)
    for base_row, next_row in zip(base["monthlyCashFlow"], result["monthlyCashFlow"], strict=True):
        assert next_row["cashOut"]["otherCost"] == pytest.approx(
            base_row["cashOut"]["otherCost"] + (delta if base_row["monthIndex"] == 2 else 0)
        )

    run_id = result["calculationRunId"]
    opened = client.get(f"/projects/p/calculation-runs/{run_id}")
    assert opened.status_code == 200, opened.text
    snapshot = opened.json()["inputSnapshot"]
    assert opened.json()["resultSnapshot"]["summary"] == result["summary"]
    base_record = next(row for row in saved if row["calculationRunId"] == base["calculationRunId"])
    comparison = compare_cash_flow_runs(base_record, opened.json())
    assert comparison["status"] != "incompatible"
    metrics = {row["key"]: row for row in comparison["metrics"]}
    assert metrics["totalProjectCost"]["delta"] == pytest.approx(delta)
    assert metrics["developmentProfit"]["delta"] == pytest.approx(-delta)
    assert snapshot["statutoryCostSelection"]["additionalCostKrw"] == delta
    assert snapshot["statutoryCostSelection"]["baseCalculationRunId"] == base["calculationRunId"]
    assert snapshot["statutoryCostSelection"]["baseFinancialInputHash"] == base["financialInputHash"]
    item = snapshot["statutoryCostSelection"]["items"][0]
    assert item["sourceLocator"] == {"archiveId": ARCHIVE, "fileName": "부담금 고지서.pdf", "location": "고지서 2쪽"}
    assert item["inclusion"] == inclusion
    assert snapshot.pop("financialInputHash") == financial_input_hash(snapshot) == result["financialInputHash"]
    assert authority.row == existing_project
    assert len(saved) > before
    # Retrying from the original basis never adds previously selected amounts.
    repeat = client.post("/projects/p/statutory-cost-review/calculate", json=payload)
    assert repeat.status_code == 200
    assert repeat.json()["summary"] == result["summary"]


@pytest.mark.parametrize(("change", "status"), [
    ({"expectedFinancialInputHash": "b" * 64}, 409),
    ({"baseCalculationRunId": "some-other-project-run"}, 404),
    ({"paymentMonthIndex": 600}, 422),
    ({"paymentMonthIndex": None}, 422),
    ({"archiveId": "b" * 64}, 422),
    ({"amountKrw": 0}, 422),
    ({"inclusion": "not_applicable"}, 422),
])
def test_rejects_unbound_or_invalid_selections_without_a_saved_run(statutory_api, change, status):
    _authority, client, saved, _base, payload = statutory_api
    before = len(saved)
    target = payload if next(iter(change)) in payload else payload["items"][0]
    target.update(change)
    response = client.post("/projects/p/statutory-cost-review/calculate", json=payload)
    assert response.status_code == status, response.text
    assert len(saved) == before


def test_cannot_compound_selected_scenario_or_reassert_adoption_through_generic_replay(statutory_api):
    _authority, client, saved, _base, payload = statutory_api
    accepted = client.post("/projects/p/statutory-cost-review/calculate", json=payload)
    assert accepted.status_code == 200, accepted.text
    stored = next(row for row in saved if row["calculationRunId"] == accepted.json()["calculationRunId"])
    payload.update(baseCalculationRunId=stored["calculationRunId"], expectedFinancialInputHash=stored["financialInputHash"])
    assert client.post("/projects/p/statutory-cost-review/calculate", json=payload).status_code == 409

    forged_document = deepcopy(stored["inputSnapshot"])
    forged_document.pop("financialInputHash")
    generic = client.post("/projects/p/cash-flow/run", json=forged_document)
    assert generic.status_code == 400, generic.text


@pytest.mark.parametrize(("headers", "status"), [({"x-test-role": "viewer"}, 403), ({"x-test-tenant": "tenant-b"}, 404)])
def test_selection_is_project_and_role_scoped(statutory_api, headers, status):
    _authority, client, saved, base, payload = statutory_api
    before = len(saved)
    response = client.post("/projects/p/statutory-cost-review/calculate", json=payload, headers=headers)
    assert response.status_code == status, response.text
    if status == 404:
        assert client.get(f"/projects/p/calculation-runs/{base['calculationRunId']}", headers=headers).status_code == 404
    assert len(saved) == before


@pytest.mark.parametrize(("source_period", "accepted"), [("사업승인", False), ("M0", True)])
def test_adoption_requires_months_and_keeps_existing_explicit_row_sources(statutory_api, source_period, accepted):
    _authority, client, saved, base, payload = statutory_api
    snapshot = deepcopy(next(row["inputSnapshot"] for row in saved if row["calculationRunId"] == base["calculationRunId"]))
    snapshot.pop("financialInputHash")
    snapshot.pop("periodBasis", None)
    schedule, _provenance = cash_flow_cost_schedule_for_review(snapshot)
    locator = {"sheetName": "Cash", "cell": "K5", "sourceDocument": "원본.xlsx"}
    rows = [{"monthIndex": index, **row} for index, row in enumerate(schedule)]
    rows[0].update(period=source_period, sourceLocator=locator)
    snapshot["costSchedule"] = {"rows": rows}
    stored = client.post("/projects/p/cash-flow/run", json=snapshot)
    assert stored.status_code == 200, stored.text
    run_id = stored.json()["calculationRunId"]
    available = client.get("/projects/p/statutory-cost-review").json()["basisRuns"]
    assert any(row["calculationRunId"] == run_id for row in available) is accepted
    payload.update(baseCalculationRunId=run_id, expectedFinancialInputHash=stored.json()["financialInputHash"])
    result = client.post("/projects/p/statutory-cost-review/calculate", json=payload)
    if not accepted:
        assert result.status_code == 409, result.text
    else:
        assert result.status_code == 200, result.text
        adopted = next(row for row in saved if row["calculationRunId"] == result.json()["calculationRunId"])
        assert adopted["inputSnapshot"]["costSchedule"]["rows"][0] == rows[0]
        assert adopted["inputSnapshot"]["costSchedule"]["rows"][-1]["monthIndex"] == 2


@pytest.mark.parametrize(("included", "status"), [(0, 200), (FEE, 422)])
def test_an_existing_positive_charge_blocks_double_count_but_zero_does_not(statutory_api, included, status):
    _authority, client, saved, base, payload = statutory_api
    snapshot = deepcopy(next(row["inputSnapshot"] for row in saved if row["calculationRunId"] == base["calculationRunId"]))
    snapshot.pop("financialInputHash")
    schedule, _provenance = cash_flow_cost_schedule_for_review(snapshot)
    rows = [{"monthIndex": index, **row} for index, row in enumerate(schedule)]
    rows[1]["sourceBreakdown"] = {"학교 용지 부담금": included}
    snapshot["costSchedule"] = rows
    stored = client.post("/projects/p/cash-flow/run", json=snapshot)
    assert stored.status_code == 200, stored.text
    payload.update(baseCalculationRunId=stored.json()["calculationRunId"], expectedFinancialInputHash=stored.json()["financialInputHash"])
    assert client.post("/projects/p/statutory-cost-review/calculate", json=payload).status_code == status
