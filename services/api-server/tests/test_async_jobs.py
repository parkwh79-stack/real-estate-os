import time

from use_cases.async_jobs import JobStore, submit_pf_review_job, TERMINAL_STATUSES


PROJECT = {
    "project": {"id": "job1", "name": "Async Job Test"},
    "feasibility_input": {"total_revenue": 2800, "total_cost": 2050, "land_cost": 640, "construction_cost": 850},
    "pf_structure": [{"tranche": "Senior", "amount": 1200, "priority": 1, "interest_rate": 0.058}],
}


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
    job = submit_pf_review_job(repo, "job1", dict(PROJECT), {}, background=False, store=store)
    assert job["status"] == "completed"
    assert job["status"] in TERMINAL_STATUSES
    assert job["runId"]  # run_id propagated from the saved run
    assert job["result"]["stepCount"] == 24  # full PF review step count (incl. term annotation + developability score)
    assert repo.saved  # run was persisted through the repository


def test_submit_records_failure():
    store = JobStore()

    class _BoomRepo:
        def save_run(self, *a, **k):
            raise RuntimeError("boom")

    job = submit_pf_review_job(_BoomRepo(), "job1", dict(PROJECT), {}, background=False, store=store)
    assert job["status"] == "failed"
    assert "boom" in job["error"]


def test_submit_background_eventually_completes():
    store = JobStore()
    repo = _RepoStub()
    job = submit_pf_review_job(repo, "job1", dict(PROJECT), {}, background=True, store=store)
    assert job["status"] in {"queued", "running", "completed"}
    # Poll the in-memory store until terminal (threaded run finishes quickly).
    deadline = time.time() + 30
    while time.time() < deadline:
        current = store.get(job["jobId"])
        if current["status"] in TERMINAL_STATUSES:
            break
        time.sleep(0.05)
    assert store.get(job["jobId"])["status"] == "completed"
