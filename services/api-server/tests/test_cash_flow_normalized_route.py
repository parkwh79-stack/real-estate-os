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


def test_program_area_drawing_uses_project_archive_and_survives_reopen(normalized_cash_flow, monkeypatch):
    authority, _events, client, saved = normalized_cash_flow
    archive = "a" * 64
    authority.row.update({
        "land": {"area_sqm": 1000},
        "architectural_plan": {"gross_floor_area_sqm": 1500},
        "landLedger": {"parcels": [
            {"pnu": "1111010100100010000", "areaSqm": 1000, "legalFar": 200, "officialLandPricePerSqm": 100_000},
            {"pnu": "1111010100100020000", "areaSqm": 100, "legalFar": 200, "officialLandPricePerSqm": 100_000,
             "excludedFromProject": True},
        ]},
        "feasibility_input": {"total_revenue": 3_000_000_000},
        "source_documents": [{"archiveId": archive, "originalFileName": "건축 도면.pdf"}],
    })
    monkeypatch.setattr(projects, "list_deal_state_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(projects.UploadArchiveRepository, "resolve_view_metadata",
                        lambda _self, _project_id, document: {"archiveId": document["archiveId"]})

    endpoint = "/projects/p/feasibility/build-input"
    drawing = {"grossFloorAreaSqm": 1800.5, "archiveId": archive, "revision": "R2", "location": "A-10 면적표"}
    foreign = client.post(endpoint, json={"drawing": {**drawing, "archiveId": "f" * 64}})
    assert foreign.status_code == 422, foreign.text
    prior = client.post(endpoint, json={"apply": False, "drawing": drawing, "options": {"construction_cost_per_sqm": 1_000_000}})
    assert prior.status_code == 200, prior.text
    preview = prior.json()
    assert preview["applied"] is False
    assert authority.row["feasibility_input"] == {"total_revenue": 3_000_000_000}

    applied = client.post(endpoint, json={"apply": True, "drawing": drawing, "options": {"construction_cost_per_sqm": 1_000_000}})
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["applied"]
    assert body["feasibilityInput"]["land_cost"] == 100_000_000
    assert body["feasibilityInput"]["construction_cost"] == 1_800_500_000

    run = client.post("/projects/p/cash-flow/run", json={})
    assert run.status_code == 200, run.text
    reopened = client.get("/projects/p/calculation-runs?engine_key=cash_flow_engine&limit=1").json()["records"][0]
    assert reopened["calculationRunId"] == run.json()["calculationRunId"]
    assert reopened["inputSnapshot"]["project"]["grossFloorAreaSqm"] == 1800.5
    basis = reopened["inputSnapshot"]["programAreaBasis"]
    assert basis["land"]["parcels"][1]["excluded"]
    assert basis["construction"]["revision"] == "R2"
    assert basis["construction"]["sourceLocator"] == {
        "archiveId": archive, "fileName": "건축 도면.pdf", "location": "A-10 면적표",
    }
    assert saved

    change = client.post(endpoint, json={"drawing": {**drawing, "grossFloorAreaSqm": 1750, "revision": "R3"}})
    assert change.status_code == 200, change.text
    assert change.json()["feasibilityInput"]["construction_cost"] == 1_750_000_000
    rerun = client.post("/projects/p/cash-flow/run", json={})
    assert rerun.status_code == 200, rerun.text
    from use_cases.cash_flow_comparison import compare_cash_flow_runs
    latest, original = client.get("/projects/p/calculation-runs?engine_key=cash_flow_engine&limit=2").json()["records"]
    comparison = compare_cash_flow_runs(original, latest)
    assert original["inputSnapshot"]["programAreaBasis"]["construction"]["revision"] == "R2"
    assert latest["inputSnapshot"]["programAreaBasis"]["construction"]["revision"] == "R3"
    assert any(row["label"] == "토지·시공 면적 근거" for row in comparison["inputChanges"])
    assert next(row["delta"] for row in comparison["metrics"] if row["key"] == "totalProjectCost") == -50_500_000
