from fastapi.testclient import TestClient

from main import app
from routers import projects
import pytest

from use_cases.reanalyze_on_evidence import (
    ALL_PODS,
    affected_pods_for_document,
    reanalyze_after_evidence_use_case,
)


def test_affected_pods_for_document():
    assert affected_pods_for_document("land_ledger") == ["land_legal"]
    assert affected_pods_for_document("feasibility_workbook") == ["feasibility_cashflow", "cost_schedule"]
    assert affected_pods_for_document("market_evidence") == ["market_product"]
    assert set(affected_pods_for_document("business_plan")) == {"land_legal", "market_product", "feasibility_cashflow"}
    assert affected_pods_for_document("unknown_type") == []


def test_reanalyze_targets_blocked_and_affected_pods():
    calls = []

    def fake_run(pid, pod, data, opts, **kwargs):
        calls.append(pod)
        if pod in {"pf_credit", "public_policy"}:  # still lack evidence → blocked
            raise ValueError("missing land evidence")
        return {"status": "completed"}

    completed = {"market_product", "cost_schedule", "feasibility_cashflow"}
    res = reanalyze_after_evidence_use_case(
        "p1", {}, completed_pod_keys=completed, changed_document_types=["land_ledger"], run_pod=fake_run,
    )
    assert res["triggered"] is True
    # targets = not-completed (land_legal, pf_credit, public_policy) ∪ affected(land_ledger→land_legal)
    assert set(res["targets"]) == {"land_legal", "pf_credit", "public_policy"}
    assert res["newlyUnblocked"] == ["land_legal"]  # was blocked, ran ok now
    assert set(res["stillBlocked"]) == {"pf_credit", "public_policy"}  # honestly still blocked


def test_reanalyze_reruns_affected_even_if_completed():
    calls = []

    def fake_run(pid, pod, data, opts, **kwargs):
        calls.append(pod)
        return {}

    res = reanalyze_after_evidence_use_case(
        "p1", {}, completed_pod_keys=set(ALL_PODS), changed_document_types=["market_evidence"], run_pod=fake_run,
    )
    # all pods completed → not-completed empty; the new market doc re-runs market_product only
    assert res["targets"] == ["market_product"]
    assert calls == ["market_product"]


def test_reanalyze_skips_fresh_project():
    res = reanalyze_after_evidence_use_case(
        "p1", {}, completed_pod_keys=set(), changed_document_types=None, run_pod=lambda *a, **k: {},
    )
    assert res["triggered"] is False
    assert res["reason"] == "no_prior_analysis"


def test_reanalysis_consumes_new_downstream_invalidations():
    calls = []
    data = {"chief_orchestration_state": {"podRuns": {}}}
    def run(pid, pod, project, options, **kwargs):
        calls.append(pod)
        runs = project["chief_orchestration_state"]["podRuns"]
        runs[pod] = {"status": "completed"}
        downstream = {"market_product": "feasibility_cashflow", "feasibility_cashflow": "pf_credit"}.get(pod)
        if downstream:
            runs[downstream] = {"status": "needs_recalculation"}
        return {}
    result = reanalyze_after_evidence_use_case("p", data, completed_pod_keys=set(ALL_PODS), changed_document_types=["market"], run_pod=run)
    assert calls == ["market_product", "feasibility_cashflow", "pf_credit"]
    assert result["cascadeComplete"] is True


def test_manual_refresh_is_not_noop_after_all_pods_completed():
    calls = []
    result = reanalyze_after_evidence_use_case("p", {}, completed_pod_keys=set(ALL_PODS), run_pod=lambda _id, pod, *_args, **_kwargs: calls.append(pod))
    assert calls == ALL_PODS
    assert result["triggered"] is True


def test_execution_error_not_misreported_as_missing_evidence():
    def fail(*args, **kwargs):
        raise RuntimeError("private connection details")
    result = reanalyze_after_evidence_use_case("p", {}, completed_pod_keys=set(ALL_PODS), changed_document_types=["market"], run_pod=fail)
    assert result["failedPods"] == ["market_product"]
    assert result["stillBlocked"] == []
    assert "private" not in str(result)
    assert result["cascadeComplete"] is False


def test_nonexception_results_do_not_claim_completion():
    for status in ("blocked", "failed", "pending_review", "running", None):
        result = reanalyze_after_evidence_use_case(
            "p", {}, completed_pod_keys=set(ALL_PODS), changed_document_types=["market"],
            run_pod=lambda *a, **k: {"status": status},
        )
        assert result["cascadeComplete"] is False
        assert result["rows"][0]["after"] == (status or "awaiting_result")


def test_old_completed_state_cannot_satisfy_new_evidence():
    data = {"chief_orchestration_state": {"podRuns": {"market_product": {
        "status": "completed", "inputHash": "old", "artifactKeys": ["old-artifact"],
    }}}}
    result = reanalyze_after_evidence_use_case(
        "p", data, completed_pod_keys=set(ALL_PODS), changed_document_types=["market"],
        run_pod=lambda *a, **k: {},
    )
    assert result["cascadeComplete"] is False
    assert result["pendingRecalculation"] == ["market_product"]


def test_round_limit_preserves_downstream_invalidation():
    data = {"chief_orchestration_state": {"podRuns": {}}}
    def run(pid, pod, project, options, **kwargs):
        runs = project["chief_orchestration_state"]["podRuns"]
        runs[pod] = {"status": "completed"}
        runs["pf_credit"] = {"status": "needs_recalculation"}
        return {}
    result = reanalyze_after_evidence_use_case(
        "p", data, completed_pod_keys=set(ALL_PODS), changed_document_types=["market"],
        run_pod=run, max_recalculation_rounds=1,
    )
    assert result["cascadeComplete"] is False
    assert result["pendingRecalculation"] == ["pf_credit"]


def test_reanalyze_endpoint_skips_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "reanalyze.sqlite3"))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    project_id = client.post("/projects/", json={"project": {"name": "Reanalyze QA", "type": "residential"}}).json()["project"]["id"]
    # No pods have run → no completed artifacts → auto-reanalyze skips (does not run heavy pods).
    res = client.post(f"/projects/{project_id}/chief-orchestration/reanalyze", json={})
    assert res.status_code == 200
    assert res.json()["triggered"] is False
    client.delete(f"/projects/{project_id}")


@pytest.mark.parametrize('validation_fails', [False, True])
def test_reanalysis_uses_standard_parsed_fact_validation(monkeypatch, validation_fails):
    from routers import projects
    from use_cases.reanalyze_on_evidence import ALL_PODS
    project={'chief_orchestration_state':{'podRuns':{}}}
    events=[]
    monkeypatch.setattr(projects,'PROJECTS_DB',{'validation-p':project})
    monkeypatch.setattr(projects,'_completed_pod_keys',lambda _:set(ALL_PODS))
    monkeypatch.setattr(projects,'upsert_project_record',lambda *_:True)
    def run(pid,pod,data,options,**kw):
        data['chief_orchestration_state']['podRuns'][pod]={'status':'completed','revision':1}
        return {'status':'completed'}
    def validate(pid,pod,**kwargs):
        events.append(pod)
        if validation_fails:
            raise RuntimeError('validation storage failure')
        return {'status':'verified'}
    monkeypatch.setattr(projects,'run_chief_deal_pod_use_case',run)
    monkeypatch.setattr(projects,'_validate_supplement_and_revalidate_parsed_facts',validate)
    monkeypatch.setattr(projects,'_persist_shared_deal_state_graph',lambda *_args,**_kw:None)
    result=projects._reanalyze_after_evidence('validation-p',changed_document_types=['permit'])
    assert events==['land_legal']
    assert result['cascadeComplete'] is (not validation_fails)


def test_upload_reanalysis_failure_is_returned_without_private_exception(monkeypatch):
    monkeypatch.setattr(projects,'PROJECTS_DB',{'p':{}})
    monkeypatch.setattr(projects,'_completed_pod_keys',lambda _: {'land_legal'})
    def fail(*_args,**_kwargs):
        raise RuntimeError('private connection details')
    monkeypatch.setattr(projects,'_reanalyze_after_evidence',fail)
    result=projects._maybe_auto_reanalyze('p',['permit'])
    assert result=={'triggered':True,'status':'failed','cascadeComplete':False,'failureCode':'auto_reanalysis_failed'}
