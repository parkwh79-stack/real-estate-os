"""Durable async job runner for orchestration.

Lets the API enqueue a PF review run and return immediately, so clients poll
for status instead of blocking on the full synchronous run. Jobs are persisted
write-through to a JSON file under the runtime dir, so they survive a process
restart and can be recovered/re-submitted via recover_interrupted_jobs.
Execution is still in-process (ThreadPoolExecutor); a distributed Redis/DB-backed
worker queue remains the production target (see docs/architecture.md and
docs/product/ir-deck-implementation-gap.md).
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from use_cases.pf_review import run_and_save_pf_review_use_case

JOB_SCHEMA_VERSION = "realytics-async-job-v1"
TERMINAL_STATUSES = {"completed", "failed"}
NON_TERMINAL_STATUSES = {"queued", "running"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_jobs_file() -> Path:
    """Durable jobs file under the runtime dir (follows REALYTICS_SQLITE_FILE)."""
    override = os.environ.get("REALYTICS_JOBS_FILE")
    if override:
        return Path(override)
    from storage import sqlite_file_path

    return sqlite_file_path().parent / "jobs.json"


class JobStore:
    """Thread-safe job registry.

    In-memory by default (process-local). When `path` is given the store is
    durable: every change is written through to a JSON file and reloaded on
    construction, so jobs survive a process restart and can be recovered.
    """

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._path = Path(path) if path else None
        if self._path:
            self._load()

    def _load(self) -> None:
        try:
            if self._path and self._path.exists():
                with self._path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    self._jobs = {k: v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            self._jobs = {}

    def _persist_locked(self) -> None:
        if not self._path:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._jobs, fh, ensure_ascii=False)
            tmp.replace(self._path)
        except Exception:
            pass  # durability is best-effort; in-memory state remains authoritative

    def create(self, kind: str, project_id: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        job = {
            "schemaVersion": JOB_SCHEMA_VERSION,
            "jobId": str(uuid4()),
            "kind": kind,
            "projectId": project_id,
            "status": "queued",
            "params": params or {},
            "createdAt": _now(),
            "startedAt": None,
            "completedAt": None,
            "runId": None,
            "result": None,
            "error": None,
            "recoveredFrom": None,
        }
        with self._lock:
            self._jobs[job["jobId"]] = job
            self._persist_locked()
        return dict(job)

    def update(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.update(fields)
            self._persist_locked()
            return dict(job)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, project_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = [dict(job) for job in self._jobs.values()]
        if project_id is not None:
            jobs = [job for job in jobs if job.get("projectId") == project_id]
        jobs.sort(key=lambda job: job.get("createdAt") or "", reverse=True)
        return jobs[:limit]


# Module-level singletons used by the API. The shared store is durable so jobs
# survive a process restart and can be recovered.
_STORE = JobStore(path=_default_jobs_file())
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pf-review-job")


def get_job_store() -> JobStore:
    return _STORE


def submit_pf_review_job(
    orchestration_repository: Any,
    project_id: str,
    project_data: Dict[str, Any],
    options: Optional[Dict[str, Any]] = None,
    *,
    background: bool = True,
    store: Optional[JobStore] = None,
    executor: Optional[ThreadPoolExecutor] = None,
    resume_payload: Optional[Dict[str, Any]] = None,
    recovered_from: Optional[str] = None,
) -> Dict[str, Any]:
    """Enqueue a PF review run. Returns the job record immediately.

    background=True schedules on the thread pool (non-blocking); background=False
    runs inline (used by tests for deterministic assertions). `resume_payload`
    persists the original request input so an interrupted job can be recovered
    after a restart; `recovered_from` links a recovery attempt to its origin.
    """
    store = store or _STORE
    job = store.create(
        "pf_review",
        project_id,
        {"hasOptions": bool(options), "options": options or {}, "inputData": resume_payload or {}},
    )
    job_id = job["jobId"]
    if recovered_from:
        store.update(job_id, recoveredFrom=recovered_from)

    def _work() -> None:
        store.update(job_id, status="running", startedAt=_now())
        try:
            run = run_and_save_pf_review_use_case(orchestration_repository, project_id, project_data, options or {})
            run_id = _extract_run_id(run)
            store.update(
                job_id,
                status="completed",
                completedAt=_now(),
                runId=run_id,
                result=_result_summary(run),
            )
        except Exception as exc:  # pragma: no cover - defensive
            store.update(job_id, status="failed", completedAt=_now(), error=f"{exc.__class__.__name__}: {exc}")

    if background:
        (executor or _EXECUTOR).submit(_work)
        return store.get(job_id) or job
    _work()
    return store.get(job_id) or job


def recover_interrupted_jobs(
    resubmit_fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    *,
    project_id: Optional[str] = None,
    store: Optional[JobStore] = None,
) -> Dict[str, Any]:
    """Recover jobs left non-terminal by a crash/restart.

    A job still in queued/running on startup means the process died mid-run.
    Each is marked 'interrupted' (never silently lost) and re-submitted via
    resubmit_fn, which rebuilds project_data from the persisted request input.
    Returns the interrupted and freshly-resubmitted job ids.
    """
    store = store or _STORE
    candidates = [
        job for job in store.list(project_id=project_id, limit=1000)
        if job.get("status") in NON_TERMINAL_STATUSES
    ]
    interrupted: List[str] = []
    resubmitted: List[str] = []
    for job in candidates:
        store.update(job["jobId"], status="interrupted", completedAt=_now())
        interrupted.append(job["jobId"])
        try:
            new_job = resubmit_fn(job)
        except Exception:
            new_job = None
        if new_job and new_job.get("jobId"):
            resubmitted.append(new_job["jobId"])
    return {
        "schemaVersion": JOB_SCHEMA_VERSION,
        "interrupted": interrupted,
        "resubmitted": resubmitted,
        "recoveredAt": _now(),
    }


def _extract_run_id(run: Any) -> Optional[str]:
    if not isinstance(run, dict):
        return None
    nested = run.get("run")
    if isinstance(nested, dict) and nested.get("run_id"):
        return nested.get("run_id")
    return run.get("run_id")


def _result_summary(run: Any) -> Dict[str, Any]:
    if not isinstance(run, dict):
        return {}
    inner = run.get("run") if isinstance(run.get("run"), dict) else run
    artifacts = inner.get("artifacts", {}) if isinstance(inner, dict) else {}
    gate = artifacts.get("approvalReadinessGate") or {}
    credit = artifacts.get("creditReviewDecision") or {}
    return {
        "status": inner.get("status") if isinstance(inner, dict) else None,
        "stepCount": len(inner.get("steps", [])) if isinstance(inner, dict) else None,
        "approvalReadiness": gate.get("status") or (gate.get("summary") or {}).get("readiness"),
        "creditDecision": credit.get("decision"),
        "errorCount": len(inner.get("errors", [])) if isinstance(inner, dict) else None,
    }
