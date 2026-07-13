from fastapi.testclient import TestClient

from main import app
from routers import projects
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
        return {"ok": True}

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
