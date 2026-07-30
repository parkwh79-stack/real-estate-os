import time
from uuid import uuid4

from use_cases.async_jobs import (
    JobStore,
    PFReviewJobWorker,
    TERMINAL_STATUSES,
    cancel_pf_review_job,
    process_next_pf_review_job,
    submit_pf_review_job,
)
from agent_platform.pf_review_runtime import _idempotency_key


PROJECT = {
    "project": {"id": "job1", "name": "Async Job Test"},
    "feasibility_input": {"total_revenue": 2800, "total_cost": 2050, "land_cost": 640, "construction_cost": 850},
    "pf_structure": [{"tranche": "Senior", "amount": 1200, "priority": 1, "interest_rate": 0.058}],
}


def test_pf_review_idempotency_key_changes_with_agent_version():
    first = _idempotency_key("project-1", PROJECT, {}, agent_version="1.0.0")
    upgraded = _idempotency_key("project-1", PROJECT, {}, agent_version="1.0.1")

    assert first != upgraded


class _RepoStub:
    """Minimal orchestration repository: returns the run as save_run does."""

    def __init__(self):
        self.saved = []

    def save_run(self, project_id, run, project_data):
        self.saved.append((project_id, run))
        return run


def test_job_store_lifecycle():
    store = JobStore()
    job = store.create("pf_review", "p1", {"k": 1})
    assert job["status"] == "queued"
    assert store.get(job["jobId"])["jobId"] == job["jobId"]
    store.update(job["jobId"], status="running")
    assert store.get(job["jobId"])["status"] == "running"
    assert store.list(project_id="p1")[0]["jobId"] == job["jobId"]
    assert store.list(project_id="other") == []
    assert store.get("missing") is None


def test_submit_sync_runs_and_completes_with_run_id():
    store = JobStore()
    repo = _RepoStub()
    project_id = f"job-sync-{uuid4()}"
    project = {**PROJECT, "project": {**PROJECT["project"], "id": project_id}}
    job = submit_pf_review_job(repo, project_id, project, {}, background=False, store=store)
    assert job["status"] == "completed"
    assert job["status"] in TERMINAL_STATUSES
    assert job["runId"]  # run_id propagated from the saved run
    assert job["result"]["stepCount"] == 27  # full PF review step count (incl. consensus + developability + credit documents)
    assert repo.saved  # run was persisted through the repository


def test_submit_records_failure():
    store = JobStore()

    class _BoomRepo:
        def save_run(self, *a, **k):
            raise RuntimeError("boom")

    project_id = f"job-failure-{uuid4()}"
    failing_project = {
        **PROJECT,
        "project": {**PROJECT["project"], "id": project_id},
    }
    job = submit_pf_review_job(
        _BoomRepo(), project_id, failing_project, {}, background=False, store=store,
    )
    assert job["status"] == "failed"
    assert "boom" in job["error"]


def test_submit_background_eventually_completes():
    store = JobStore()
    repo = _RepoStub()
    project_id = f"job-background-{uuid4()}"
    project = {**PROJECT, "project": {**PROJECT["project"], "id": project_id}}
    job = submit_pf_review_job(repo, project_id, project, {}, background=True, store=store)
    assert job["status"] in {"queued", "running", "completed"}
    # Poll the in-memory store until terminal (threaded run finishes quickly).
    deadline = time.time() + 30
    while time.time() < deadline:
        current = store.get(job["jobId"])
        if current["status"] in TERMINAL_STATUSES:
            break
        time.sleep(0.05)
    assert store.get(job["jobId"])["status"] == "completed"


def test_durable_store_survives_restart(tmp_path):
    path = tmp_path / "jobs.json"
    store = JobStore(path=path)
    job = store.create("pf_review", "p1", {"inputData": {"options": {"requested_by": "x"}}})
    store.update(job["jobId"], status="running", startedAt="t")
    # simulate a process restart: a fresh store loads from the same file
    reloaded = JobStore(path=path)
    got = reloaded.get(job["jobId"])
    assert got is not None
    assert got["status"] == "running"
    assert got["params"]["inputData"]["options"]["requested_by"] == "x"


def test_recover_interrupted_jobs_marks_and_resubmits(tmp_path):
    from use_cases.async_jobs import recover_interrupted_jobs

    store = JobStore(path=tmp_path / "jobs.json")
    running = store.create("pf_review", "p1", {"inputData": {}})
    store.update(running["jobId"], status="running")
    done = store.create("pf_review", "p1", {})
    store.update(done["jobId"], status="completed")

    resubmitted_ids = []

    def _fake_resubmit(old_job):
        new = store.create("pf_review", old_job["projectId"], old_job.get("params"))
        resubmitted_ids.append(new["jobId"])
        return new

    result = recover_interrupted_jobs(_fake_resubmit, project_id="p1", store=store)
    assert running["jobId"] in result["interrupted"]
    assert done["jobId"] not in result["interrupted"]  # terminal jobs are untouched
    assert len(result["resubmitted"]) == 1
    assert store.get(running["jobId"])["status"] == "interrupted"


def test_external_worker_mode_queues_then_claims(monkeypatch):
    monkeypatch.setenv("REALYTICS_ASYNC_JOB_MODE", "external_worker")
    store = JobStore()
    repo = _RepoStub()
    project_id = f"external-worker-{uuid4()}"
    project = {**PROJECT, "project": {**PROJECT["project"], "id": project_id}}

    queued = submit_pf_review_job(
        repo, project_id, project, {}, background=True, store=store,
    )
    assert queued["status"] == "queued"

    completed = process_next_pf_review_job(repo, worker_id="worker:test", store=store)
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["agentRunId"]


def test_pf_worker_builds_repository_after_claim_from_persisted_tenant(monkeypatch):
    monkeypatch.setenv("REALYTICS_ASYNC_JOB_MODE", "external_worker")
    store = JobStore()
    project_id = f"tenant-worker-{uuid4()}"
    project = {**PROJECT, "project": {**PROJECT["project"], "id": project_id}}
    queued = submit_pf_review_job(
        _RepoStub(),
        project_id,
        project,
        {"tenantId": "tenant-a"},
        background=True,
        store=store,
    )
    observed = {}

    def repository_factory(job):
        observed["tenantId"] = job["tenantId"]
        observed["jobId"] = job["jobId"]
        return _RepoStub()

    completed = PFReviewJobWorker(
        repository_factory,
        worker_id="worker:tenant-a",
        store=store,
    ).run_once()

    assert completed is not None
    assert completed["status"] == "completed"
    assert observed == {"tenantId": "tenant-a", "jobId": queued["jobId"]}


def test_pf_worker_repository_factory_failure_is_secret_safe_and_terminal(monkeypatch):
    monkeypatch.setenv("REALYTICS_ASYNC_JOB_MODE", "external_worker")
    store = JobStore()
    project_id = f"tenant-worker-failure-{uuid4()}"
    project = {**PROJECT, "project": {**PROJECT["project"], "id": project_id}}
    submit_pf_review_job(
        _RepoStub(),
        project_id,
        project,
        {"tenantId": "tenant-a"},
        background=True,
        store=store,
    )

    def failing_factory(_job):
        raise RuntimeError("postgresql://user:secret@database.invalid/postgres")

    failed = PFReviewJobWorker(
        failing_factory,
        worker_id="worker:tenant-a",
        store=store,
    ).run_once()

    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error"] == "repository_configuration_failed:RuntimeError"
    assert "secret" not in failed["error"]


def test_cancel_queued_job_is_terminal():
    store = JobStore()
    job = store.create("pf_review", "cancel-project", {})

    cancelled = cancel_pf_review_job(job["jobId"], reason="test", store=store)

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancellationRequested"] is True
    assert cancelled["cancellationReason"] == "test"


def test_worker_does_not_overwrite_cancellation_with_completed(monkeypatch):
    monkeypatch.setenv("REALYTICS_ASYNC_JOB_MODE", "external_worker")
    store = JobStore()
    project_id = f"cancel-running-{uuid4()}"
    project = {**PROJECT, "project": {**PROJECT["project"], "id": project_id}}
    queued = submit_pf_review_job(
        _RepoStub(), project_id, project, {}, background=True, store=store,
    )

    def _cancel_during_execution(*args, **kwargs):
        current = store.get(queued["jobId"])
        assert current and current["agentRunId"]
        cancel_pf_review_job(queued["jobId"], reason="operator_cancelled", store=store)
        return {
            "output": {"run_id": "must-not-be-saved"},
            "agentRunId": current["agentRunId"],
            "agentRunStatus": "completed",
            "replayed": False,
            "stepIds": [],
        }

    monkeypatch.setattr("use_cases.async_jobs.run_pf_review_with_durable_runtime", _cancel_during_execution)
    result = process_next_pf_review_job(_RepoStub(), worker_id="worker:cancel", store=store)

    assert result is not None
    assert result["status"] == "cancelled"
    assert result["result"] is None
    assert result["runId"] is None
