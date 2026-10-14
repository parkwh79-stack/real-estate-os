from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from agents.cash_flow_engine import run_cash_flow_engine
from core.reviewed_monthly_financing import MONTHLY_FINANCING_POLICY
from db.repositories.postgres_project_repository import ProjectVersionConflict
from db.repositories.request_project_repository import RequestProjectRepository
from main import app
from routers import cash_flow_comparison as api, projects
from storage import get_project_record, list_calculation_run_records, list_user_activity_logs, upsert_calculation_run_record
from use_cases.cash_flow_comparison import compare_cash_flow_runs, content_hash
from use_cases.cash_flow_dependencies import financial_input_hash


def record(run_id="baseline", *, construction=400, project_id="p", tenant="legacy", end="2026-03-01"):
    snapshot = {
        "currency": "KRW", "unit": "KRW",
        "schedule": {"construction_start": "2026-01-01", "completion": end},
        "sourcesAndUses": {"revenue": {"totalSalesRevenue": 1000},
            "cost": {"landCost": 100, "directConstructionCost": construction, "financialCost": 50,
                     "otherCost": 50, "totalProjectCost": construction + 200}, "funding": {"pfLoan": 900}},
        "productMix": [{"type": "apartment", "totalSalesRevenue": 1000, "defaultCollection": "10-60-30"}],
        "sourceMetadata": {"sourceHash": "a" * 64, "generatedAt": "2026-01-01"},
    }
    digest = financial_input_hash(snapshot)
    return {"calculationRunId": run_id, "projectId": project_id, "tenantId": tenant,
        "engineKey": "cash_flow_engine", "engineVersion": "cash_flow_engine-v1", "status": "completed",
        "createdAt": "2026-01-01T00:00:00Z", "financialInputHash": digest,
        "inputSnapshot": {**snapshot, "financialInputHash": digest}, "resultSnapshot": run_cash_flow_engine(snapshot)}


def rehash(row):
    snapshot = row["inputSnapshot"]
    snapshot.pop("financialInputHash", None)
    digest = financial_input_hash(snapshot)
    snapshot["financialInputHash"] = digest
    row["financialInputHash"] = digest


def recalculate(row):
    request = {key: value for key, value in row["inputSnapshot"].items() if key != "financialInputHash"}
    row["resultSnapshot"] = run_cash_flow_engine(request)
    rehash(row)


def test_compares_persisted_results_without_mutation_or_canonical_promotion():
    base, changed = record(), record("changed", construction=500)
    before = deepcopy((base, changed))
    result = compare_cash_flow_runs(base, changed)
    assert result["status"] == "ready"
    metrics = {item["key"]: item for item in result["metrics"]}
    assert metrics["totalProjectCost"]["delta"] == 100
    assert metrics["developmentProfit"]["delta"] == -100
    assert metrics["financialCost"]["baselineValue"] == 50
    assert metrics["outstandingDebt"]["delta"] is None
    assert result["inputChanges"]
    assert all(item["label"] for item in result["inputChanges"])
    assert result["businessInputsChanged"] is True and result["sourceChanged"] is False
    assert result["reportEligible"] is False and result["approvalStatus"] == "not_requested"
    assert (base, changed) == before


def test_execution_metadata_is_not_a_business_change_but_raw_hashes_are_preserved():
    base, changed = record(), record("changed")
    changed["inputSnapshot"]["sourceMetadata"]["generatedAt"] = "2026-02-01"
    changed["inputSnapshot"]["calculationRunId"] = "b80f3986-d416-419c-bb39-f4da9b2577e8"
    rehash(changed)
    result = compare_cash_flow_runs(base, changed)
    assert result["inputChanges"] == []
    assert result["baseline"]["inputHash"] != result["comparison"]["inputHash"]
    assert result["baseline"]["financialInputHash"] == base["financialInputHash"]
    assert "a" * 64 in result["baseline"]["sourceHashes"]
    assert result["sourceChanged"] is False and result["businessInputsChanged"] is False


def test_changed_review_reason_keeps_business_input_hash_and_retains_authority_hashes():
    base, changed = record(), record("changed")
    for row, reason in ((base, "기준 금융조건 검토"), (changed, "같은 금융조건의 확인 사유 보완")):
        row["inputSnapshot"]["pfStructure"] = [{
            "trancheId": "senior", "tranche": "선순위", "name": "senior",
            "amount": 900, "interestRate": 0.12, "feeRate": 0.01, "maturityMonths": 2,
            "repaymentPriority": 1, "repaymentSource": "repayment_reserve",
        }]
        row["inputSnapshot"]["monthlyFinancing"] = dict(MONTHLY_FINANCING_POLICY)
        row["inputSnapshot"]["reviewedMonthlyFinancingReview"] = {"reason": reason, "reviewHash": "a" * 64}
        recalculate(row)
    original = deepcopy((base, changed))

    result = compare_cash_flow_runs(base, changed)

    assert result["status"] == "ready"
    assert result["businessInputsChanged"] is False
    assert result["inputChanges"] == []
    assert result["baseline"]["businessInputHash"] == result["comparison"]["businessInputHash"]
    assert result["baseline"]["inputHash"] != result["comparison"]["inputHash"]
    assert result["baseline"]["financialInputHash"] == base["financialInputHash"]
    assert result["comparison"]["financialInputHash"] == changed["financialInputHash"]
    assert base["financialInputHash"] != changed["financialInputHash"]
    assert all(metric["delta"] in (None, 0) for metric in result["metrics"])
    assert (base, changed) == original


def test_changed_source_is_distinct_from_identical_business_values():
    base, changed = record(), record("changed")
    changed["inputSnapshot"]["sourceMetadata"]["sourceHash"] = "b" * 64
    rehash(changed)
    result = compare_cash_flow_runs(base, changed)
    assert result["sourceChanged"] is True and result["businessInputsChanged"] is False
    assert result["inputChanges"] == []
    assert result["baseline"]["businessInputHash"] == result["comparison"]["businessInputHash"]
    assert result["comparison"]["sourceHashes"] == ["b" * 64]


def test_changed_horizon_is_partial_and_keeps_whole_project_metrics():
    result = compare_cash_flow_runs(record(), record("changed", end="2026-04-01"))
    assert result["status"] == "partial" and result["reasons"] == ["period_mismatch"]
    assert result["metrics"] and result["baseline"]["period"]["rowCount"] == 3
    assert result["comparison"]["period"]["rowCount"] == 4


def test_schedule_epoch_uses_shared_anchor_and_does_not_invent_dates():
    base, changed = record(), record("changed")
    base["inputSnapshot"]["schedule"] = {"construction_start": "M0", "completion": "M2"}
    changed["inputSnapshot"]["schedule"] = [
        {"key": "construction_start", "planned_date": "M0"},
        {"key": "completion", "planned_date": "2026-03", "month_index": 2},
    ]
    recalculate(base)
    recalculate(changed)
    result = compare_cash_flow_runs(base, changed)
    assert result["status"] == "partial" and result["reasons"] == ["period_mismatch"]
    assert result["baseline"]["period"]["baseMonth"] is None
    assert result["baseline"]["period"]["axis"] == "relative"
    assert result["comparison"]["period"]["baseMonth"] == "2026-01"
    assert result["comparison"]["period"]["axis"] == "calendar"
    assert result["metrics"]  # Aggregate comparison only; no month-alignment result.
    assert "monthlyComparison" not in result


def test_budget_and_reviewed_monthly_policy_compare_actual_finance_totals_as_partial():
    base, changed = record(), record("changed")
    for row in (base, changed):
        row["inputSnapshot"]["pfStructure"] = [{"trancheId": "senior", "tranche": "선순위", "name": "senior",
            "amount": 900, "interestRate": 0.12, "feeRate": 0.01, "maturityMonths": 2,
            "repaymentPriority": 1, "repaymentSource": "repayment_reserve"}]
    changed["inputSnapshot"]["monthlyFinancing"] = dict(MONTHLY_FINANCING_POLICY)
    recalculate(base)
    recalculate(changed)
    result = compare_cash_flow_runs(base, changed)
    assert result["status"] == "partial" and result["reasons"] == ["financing_policy_mismatch"]
    assert result["baseline"]["policyMode"] == "budget_finance_allocation"
    assert result["comparison"]["policyMode"] == "reviewed_monthly_v1"
    metrics = {item["key"]: item for item in result["metrics"]}
    assert metrics["financialCost"]["comparisonValue"] == changed["resultSnapshot"]["summary"]["financialCost"]
    assert metrics["outstandingDebt"]["comparisonValue"] == changed["resultSnapshot"]["summary"]["outstandingDebt"]
    assert metrics["outstandingDebt"]["baselineValue"] is None
    assert {item["label"] for item in result["inputChanges"]} == {"금융비용 계산방식"}


@pytest.mark.parametrize("mutation,reason", [
    (lambda r: r.update(engineKey="waterfall"), "unsupported_engine"),
    (lambda r: r.update(engineVersion="future-engine-v2"), "engine_version_mismatch"),
    (lambda r: r.update(status="failed"), "calculation_not_completed"),
    (lambda r: r["inputSnapshot"].update(unit="KRW_MILLION"), "unsupported_currency_or_unit"),
    (lambda r: r["resultSnapshot"]["monthlyCashFlow"][0].update(monthIndex=1), "invalid_month_indices"),
    (lambda r: r.update(financialInputHash="bad"), "financial_input_hash_mismatch"),
    (lambda r: r.update(inputSnapshot=[]), "calculation_snapshot_missing"),
    (lambda r: r["resultSnapshot"]["summary"].pop("totalProjectCost"), "calculation_summary_missing"),
    (lambda r: r["resultSnapshot"]["summary"].update(totalRevenue=float("nan")), "invalid_calculation_values"),
])
def test_incompatible_records_do_not_produce_metric_deltas(mutation, reason):
    base, changed = record(), record("changed")
    mutation(changed)
    result = compare_cash_flow_runs(base, changed)
    assert result["status"] == "incompatible" and reason in result["reasons"]
    assert result["metrics"] == []


@pytest.fixture
def project_client():
    client = TestClient(app)
    response = client.post("/projects/", json={"project": {"name": "CF comparison fixture"}})
    assert response.status_code == 200
    pid = response.json()["id"]
    rows = [upsert_calculation_run_record(pid, record(name, project_id=pid, construction=cost))
            for name, cost in (("baseline", 400), ("changed", 500))]
    yield client, pid, rows
    client.delete(f"/projects/{pid}")


def test_save_and_reopen_selection_only_without_changing_calculation_runs(project_client):
    client, pid, _ = project_client
    before = deepcopy(list_calculation_run_records(pid))
    projects.project_repository().update(pid, {"_meta": {"customProvenance": {"sourceHash": "preserved"}},
        "feasibilityStudyV2": {"review": {"status": "pending"}}})
    project_before = deepcopy(get_project_record(pid))
    url = f"/projects/{pid}/cash-flow/comparison"
    state = client.get(url).json()
    assert state["selection"] is None and len(state["availableRuns"]) == 2
    preview = client.get(url, params={"baselineRunId": "baseline", "comparisonRunId": "changed"})
    assert preview.status_code == 200 and preview.json()["selection"]["saved"] is False
    assert client.get(url).json()["selection"] is None
    saved = client.post(url, json={"baselineRunId": "baseline", "comparisonRunId": "changed", "expectedComparisonHash": state["comparisonHash"]})
    assert saved.status_code == 200, saved.text
    reopened = client.get(url)
    assert reopened.json() == saved.json()
    assert reopened.headers["cache-control"] == "no-store"
    stored = get_project_record(pid)["_meta"]["cashFlowComparison"]
    assert stored["baselineRunId"] == "baseline" and stored["comparisonRunId"] == "changed"
    assert "metrics" not in stored and "inputSnapshot" not in stored
    assert list_calculation_run_records(pid) == before
    project_after = deepcopy(get_project_record(pid))
    project_after["_meta"].pop("cashFlowComparison")
    project_after["_meta"]["updated_at"] = project_before["_meta"]["updated_at"]
    assert project_after == project_before
    audits = [row for row in list_user_activity_logs(project_id=pid) if row.get("eventType") == "cash_flow_comparison_saved"]
    assert len(audits) == 1
    assert audits[0]["metadata"]["baselineRunId"] == "baseline"
    assert "inputSnapshot" not in audits[0]["metadata"]


def test_stale_state_hash_cannot_overwrite_selection(project_client):
    client, pid, _ = project_client
    url = f"/projects/{pid}/cash-flow/comparison"
    state = client.get(url).json()
    projects.project_repository().update(pid, {"feasibility_input": {"total_revenue": 999}})
    response = client.post(url, json={"baselineRunId": "baseline", "comparisonRunId": "changed", "expectedComparisonHash": state["comparisonHash"]})
    assert response.status_code == 409
    assert "cashFlowComparison" not in get_project_record(pid).get("_meta", {})


def test_new_calculation_invalidates_previous_selection_state_hash(project_client):
    client, pid, _ = project_client
    url = f"/projects/{pid}/cash-flow/comparison"
    state = client.get(url).json()
    upsert_calculation_run_record(pid, record("new-run", project_id=pid))
    response = client.post(url, json={"baselineRunId": "baseline", "comparisonRunId": "changed", "expectedComparisonHash": state["comparisonHash"]})
    assert response.status_code == 409
    assert "cashFlowComparison" not in get_project_record(pid).get("_meta", {})


def test_tenant_metadata_is_preserved_and_mismatched_actor_cannot_read(project_client, monkeypatch):
    client, pid, _ = project_client
    projects.project_repository().update(pid, {"_meta": {"tenantId": "tenant-a", "stateVersion": 9}})
    for name in ("baseline", "changed"):
        upsert_calculation_run_record(pid, record(name, project_id=pid, tenant="tenant-a"))
    monkeypatch.setattr(api, "enforce_project_tenant_scope", lambda *args: {"user": {"tenantId": "tenant-a"}})
    url = f"/projects/{pid}/cash-flow/comparison"
    state = client.get(url).json()
    response = client.post(url, json={"baselineRunId": "baseline", "comparisonRunId": "changed", "expectedComparisonHash": state["comparisonHash"]})
    assert response.status_code == 200, response.text
    meta = get_project_record(pid)["_meta"]
    assert meta["tenantId"] == "tenant-a" and meta["stateVersion"] == 9
    monkeypatch.setattr(api, "enforce_project_tenant_scope", lambda *args: {"user": {"tenantId": "tenant-b"}})
    assert client.get(url).status_code == 403


def test_missing_foreign_project_and_foreign_tenant_runs_are_not_comparable(project_client):
    client, pid, _ = project_client
    url = f"/projects/{pid}/cash-flow/comparison"
    upsert_calculation_run_record("foreign-project", record("foreign", project_id="foreign-project"))
    upsert_calculation_run_record(pid, record("foreign-tenant", project_id=pid, tenant="another-tenant"))
    for run_id in ("does-not-exist", "foreign", "foreign-tenant"):
        response = client.get(url, params={"baselineRunId": "baseline", "comparisonRunId": run_id})
        assert response.status_code == 404
    assert all(row["calculationRunId"] != "foreign-tenant" for row in client.get(url).json()["availableRuns"])


def test_saved_selection_detects_changed_original_run(project_client):
    client, pid, _ = project_client
    url = f"/projects/{pid}/cash-flow/comparison"
    state = client.get(url).json()
    saved = client.post(url, json={"baselineRunId": "baseline", "comparisonRunId": "changed", "expectedComparisonHash": state["comparisonHash"]})
    assert saved.status_code == 200
    altered = record("changed", project_id=pid, construction=700)
    upsert_calculation_run_record(pid, altered)
    resumed = client.get(url).json()
    assert resumed["comparison"]["status"] == "incompatible"
    assert "saved_calculation_changed" in resumed["comparison"]["reasons"]


def test_write_permission_is_required_and_input_cannot_supply_result(project_client, monkeypatch):
    client, pid, _ = project_client
    url = f"/projects/{pid}/cash-flow/comparison"
    state = client.get(url).json()
    payload = {"baselineRunId": "baseline", "comparisonRunId": "changed", "expectedComparisonHash": state["comparisonHash"]}
    assert client.post(url, json={**payload, "resultSnapshot": {"status": "completed"}}).status_code == 422
    original = api.authorize_request
    def read_only(request, permission):
        if permission == "project:write":
            from fastapi import HTTPException
            raise HTTPException(403, {"error": "forbidden"})
        return original(request, permission)
    monkeypatch.setattr(api, "authorize_request", read_only)
    assert client.post(url, json=payload).status_code == 403


def test_normalized_authority_failure_does_not_fall_back_to_local_records(project_client, monkeypatch):
    client, pid, _ = project_client
    def unavailable(*args, **kwargs):
        raise api.CalculationRunRepositoryError("private DSN")
    monkeypatch.setattr(api, "list_calculation_run_records", unavailable)
    response = client.get(f"/projects/{pid}/cash-flow/comparison")
    assert response.status_code == 503 and "private DSN" not in response.text


def test_corrupt_nonfinite_stored_record_fails_closed_without_json_or_hash_crash(project_client, monkeypatch):
    client, pid, _ = project_client
    invalid = record(project_id=pid)
    invalid["resultSnapshot"]["summary"]["totalRevenue"] = float("inf")
    monkeypatch.setattr(api, "list_calculation_run_records", lambda *args, **kwargs: [invalid])
    response = client.get(f"/projects/{pid}/cash-flow/comparison")
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "invalid_calculation_run_authority"


def test_selection_write_preserves_request_repository_version_guard(project_client, monkeypatch):
    client, pid, _ = project_client
    stored = deepcopy(get_project_record(pid))
    stored["_meta"].update(tenantId="legacy", stateVersion=7)
    calls = []
    class CompetingAuthority:
        tenant_id = "legacy"
        def get(self, project_id):
            assert project_id == pid
            return stored
        def update(self, project_id, payload, *, expected_state_version):
            calls.append((project_id, payload, expected_state_version))
            raise ProjectVersionConflict(project_id=pid, expected_state_version=expected_state_version, actual_state_version=8)
    scope = RequestProjectRepository(lambda: CompetingAuthority())
    monkeypatch.setattr(api, "_context", lambda *args: (scope, scope.get(pid), "legacy", "reviewer"))
    url = f"/projects/{pid}/cash-flow/comparison"
    state = client.get(url).json()
    response = client.post(url, json={"baselineRunId": "baseline", "comparisonRunId": "changed", "expectedComparisonHash": state["comparisonHash"]})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "project_version_conflict"
    assert len(calls) == 1 and calls[0][2] == 7
    assert set(calls[0][1]) == {"_meta"}
    assert set(calls[0][1]["_meta"]) == {"cashFlowComparison"}
    assert "cashFlowComparison" not in get_project_record(pid).get("_meta", {})


def test_exact_saved_run_lookup_is_independent_of_available_runs_limit(project_client, monkeypatch):
    client, pid, records = project_client
    real_list = api.list_calculation_run_records
    def recent_only(project_id, **kwargs):
        if kwargs.get("calculation_run_id"):
            return real_list(project_id, **kwargs)
        return [row for row in records if row["calculationRunId"] == "changed"]
    monkeypatch.setattr(api, "list_calculation_run_records", recent_only)
    response = client.get(f"/projects/{pid}/cash-flow/comparison", params={"baselineRunId": "baseline", "comparisonRunId": "changed"})
    assert response.status_code == 200
    assert {run["calculationRunId"] for run in response.json()["availableRuns"]} == {"baseline", "changed"}
    assert response.json()["comparison"]["baseline"]["calculationRunId"] == "baseline"
    selection = {"baselineRunId": "baseline", "comparisonRunId": "changed",
                 "expectedComparisonHash": response.json()["comparisonHash"]}
    saved = client.post(f"/projects/{pid}/cash-flow/comparison", json=selection)
    assert saved.status_code == 200, saved.text
    reopened = client.get(f"/projects/{pid}/cash-flow/comparison").json()
    assert {run["calculationRunId"] for run in reopened["availableRuns"]} == {"baseline", "changed"}
    old_hash = saved.json()["comparisonHash"]
    upsert_calculation_run_record(pid, record("baseline", project_id=pid, construction=750))
    stale_save = client.post(f"/projects/{pid}/cash-flow/comparison", json={**selection, "expectedComparisonHash": old_hash})
    assert stale_save.status_code == 409
    assert "saved_calculation_changed" in client.get(f"/projects/{pid}/cash-flow/comparison").json()["comparison"]["reasons"]
