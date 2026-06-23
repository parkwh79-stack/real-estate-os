"""In-process async job runner for orchestration.

Lets the API enqueue a PF review run and return immediately, so clients poll
for status instead of blocking on the full synchronous run. This is the
baseline foundation for real-time monitoring and mid-run human-in-the-loop
control. It is intentionally an in-process MVP (ThreadPoolExecutor + in-memory
store), NOT a distributed queue — production should move to a DB/Redis-backed
queue (see docs/architecture.md and docs/product/ir-deck-implementation-gap.md).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from use_cases.pf_review import run_and_save_pf_review_use_case

JOB_SCHEMA_VERSION = "realytics-async-job-v1"
TERMINAL_STATUSES = {"completed", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Thread-safe in-memory job registry (process-local)."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

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
        }
        with self._lock:
            self._jobs[job["jobId"]] = job
        return dict(job)

    def update(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.update(fields)
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


# Module-level singletons used by the API.
_STORE = JobStore()
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
) -> Dict[str, Any]:
    """Enqueue a PF review run. Returns the job record immediately.

    background=True schedules on the thread pool (non-blocking); background=False
    runs inline (used by tests for deterministic assertions).
    """
    store = store or _STORE
    job = store.create("pf_review", project_id, {"hasOptions": bool(options)})
    job_id = job["jobId"]

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
