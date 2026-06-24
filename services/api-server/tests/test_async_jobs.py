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
    assert job["result"]["stepCount"] == 27  # full PF review step count (incl. consensus + developability + credit documents)
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
