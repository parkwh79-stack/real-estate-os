"""Deterministic reruns use normalized CalculationRun storage, never legacy AI state."""
from copy import deepcopy

import pytest

from db.repositories import calculation_run_repository
from routers import projects
from test_feasibility_review_authorization import analysis_authority  # noqa: F401
from test_normalized_project_events import normalized_events  # noqa: F401
from test_reviewed_monthly_cash_flow_api import working_project


@pytest.fixture
def normalized_cash_flow(analysis_authority, monkeypatch):
    authority, events, client = analysis_authority
    saved = []
    original_meta = deepcopy(authority.row["_meta"])
    authority.row.update(working_project())
    authority.row.update({"id": "p", "project": {"id": "p", "name": "결정론 계산 검증"}, "_meta": original_meta})

    class Runs:
        def __init__(self, *, tenant_id, **_kwargs):
            assert tenant_id == "tenant-a"

        def upsert_run_with_outbox(self, project_id, record, outbox, *, deal_state_records):
            assert project_id == "p" and record["tenantId"] == outbox["tenantId"] == "tenant-a"
            assert all(row["tenantId"] == "tenant-a" for row in deal_state_records)
            saved.append(deepcopy(record))
            return deepcopy(record)

        def list_runs(self, project_id, *, engine_key=None, limit=100):
            assert project_id == "p"
            return [deepcopy(row) for row in reversed(saved) if not engine_key or row["engineKey"] == engine_key][:limit]

    def forbidden_optional_path(*_args, **_kwargs):
        pytest.fail("Cash Flow must not read AI profiles or run optional Chief Pods")

    monkeypatch.setattr(calculation_run_repository, "PostgresCalculationRunRepository", Runs)
    monkeypatch.setattr(projects, "PostgresCalculationRunRepository", Runs)
    monkeypatch.setattr(projects, "ai_context_from_request", forbidden_optional_path)
    monkeypatch.setattr(projects, "run_chief_deal_pod_use_case", forbidden_optional_path)
    return authority, events, client, saved


def test_analyst_rerun_saves_and_reopens_the_exact_calculation_without_ai(normalized_cash_flow):
    authority, _events, client, saved = normalized_cash_flow
    original = deepcopy(authority.row)
    response = client.post("/projects/p/cash-flow/run", json={})
    assert response.status_code == 200, response.text
    result = response.json()
    anchor = next(row for row in saved if row["engineKey"] == "cash_flow_engine")
    assert anchor["calculationRunId"] == result["calculationRunId"]
    assert anchor["resultSnapshot"]["summary"] == result["summary"]
    assert anchor["inputSnapshot"]["financialInputHash"] == result["financialInputHash"]
    assert anchor["resultSnapshot"]["sourceMetadata"]["reportEligible"] is False
    reopened = client.get("/projects/p/calculation-runs?engine_key=cash_flow_engine&limit=10")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["records"][0] == anchor
    assert authority.row == original


@pytest.mark.parametrize("headers, status", [({"x-test-role": "viewer"}, 403), ({"x-test-tenant": "tenant-b"}, 404)])
def test_rerun_keeps_analysis_permission_and_tenant_boundary(normalized_cash_flow, headers, status):
    _authority, _events, client, saved = normalized_cash_flow
    response = client.post("/projects/p/cash-flow/run", json={}, headers=headers)
    assert response.status_code == status, response.text
    assert saved == []


def test_missing_loan_amount_is_explained_without_saving_a_false_calculation(normalized_cash_flow):
    authority, _events, client, saved = normalized_cash_flow
    authority.row["pf_structure"] = [{"tranche": "Senior", "priority": "senior"}]
    response = client.post("/projects/p/cash-flow/run", json={})
    assert response.status_code == 400, response.text
    assert "미입력" in response.json()["detail"]
    assert saved == []
