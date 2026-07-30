"""Durable asynchronous PF review queue.

Production requests are persisted in ``async_jobs`` and claimed by an external
worker. Each claimed job executes through ``DurableAgentRuntime`` so lifecycle,
steps, idempotency, retry, cancellation and outbox events are stored in the same
Agent ledger. A daemon thread is retained only as a development convenience;
hardened profiles require ``REALYTICS_ASYNC_JOB_MODE=external_worker``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from agent_platform.pf_review_runtime import run_pf_review_with_durable_runtime


JOB_SCHEMA_VERSION = "realytics-async-job-v2"
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
NON_TERMINAL_STATUSES = {"queued", "running", "retrying"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Async job repository.

    ``persistent=True`` uses the configured REALYTICS storage backend. ``path``
    and the default in-memory mode exist for isolated unit tests only.
    """

    def __init__(self, path: Optional[str | Path] = None, *, persistent: bool = False) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._path = Path(path) if path else None
        self._persistent = bool(persistent)
        if self._path:
            self._load_file()

    def _load_file(self) -> None:
        try:
            if self._path and self._path.exists():
                with self._path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    self._jobs = {key: value for key, value in data.items() if isinstance(value, dict)}
        except Exception:
            self._jobs = {}

    def _persist_file_locked(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._jobs, fh, ensure_ascii=False)
        tmp.replace(self._path)

    def create(
        self,
        kind: str,
        project_id: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        tenant_id: str | None = None,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        job = {
            "schemaVersion": JOB_SCHEMA_VERSION,
            "jobId": str(uuid4()),
            "kind": kind,
            "projectId": project_id,
            "tenantId": tenant_id or "default",
            "status": "queued",
            "params": _without_secrets(params or {}),
            "createdAt": _now(),
            "updatedAt": _now(),
            "startedAt": None,
            "completedAt": None,
            "leaseOwner": None,
            "leaseExpiresAt": None,
            "attemptCount": 0,
            "maxAttempts": max(1, int(max_attempts)),
            "agentRunId": None,
            "runId": None,
            "result": None,
            "error": None,
            "recoveredFrom": None,
            "cancellationRequested": False,
        }
        return self._save(job)

    def update(self, job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
        current = self.get(job_id)
        if current is None:
            return None
        current.update(fields)
        current["updatedAt"] = _now()
        return self._save(current)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self._persistent:
            from storage import get_async_job_record

            return get_async_job_record(job_id)
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, project_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if self._persistent:
            from storage import list_async_job_records

            return list_async_job_records(project_id=project_id, limit=limit)
        with self._lock:
            jobs = [dict(job) for job in self._jobs.values()]
        if project_id is not None:
            jobs = [job for job in jobs if job.get("projectId") == project_id]
        jobs.sort(key=lambda job: job.get("createdAt") or "", reverse=True)
        return jobs[:limit]

    def claim(self, *, worker_id: str, lease_seconds: int = 1800) -> Optional[Dict[str, Any]]:
        if self._persistent:
            from storage import claim_async_job_record

            return claim_async_job_record(worker_id=worker_id, lease_seconds=lease_seconds)
        with self._lock:
            candidates = [
                item for item in self._jobs.values()
                if item.get("status") == "queued"
                and int(item.get("attemptCount") or 0) < int(item.get("maxAttempts") or 3)
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: item.get("createdAt") or "")
            job = dict(candidates[0])
            job.update(
                {
                    "status": "running",
                    "startedAt": job.get("startedAt") or _now(),
                    "attemptCount": int(job.get("attemptCount") or 0) + 1,
                    "leaseOwner": worker_id,
                    "leaseExpiresAt": None,
                }
            )
            return self._save(job)

    def _save(self, job: Dict[str, Any]) -> Dict[str, Any]:
        if self._persistent:
            from storage import upsert_async_job_record

            return upsert_async_job_record(job)
        with self._lock:
            self._jobs[str(job["jobId"])] = dict(job)
            self._persist_file_locked()
            return dict(job)


_STORE = JobStore(persistent=True)


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
    executor: Any = None,
    resume_payload: Optional[Dict[str, Any]] = None,
    recovered_from: Optional[str] = None,
) -> Dict[str, Any]:
    """Persist a PF review job and execute inline, locally, or by external worker."""
    if executor is not None:
        raise ValueError("ThreadPoolExecutor is no longer supported; use the durable PF review worker")
    store = store or _STORE
    options = dict(options or {})
    tenant_id = str(
        options.get("tenantId")
        or ((project_data.get("_meta") or {}).get("tenantId") if isinstance(project_data.get("_meta"), dict) else "")
        or "default"
    )
    job = store.create(
        "pf_review",
        project_id,
        {
            "options": options,
            "inputData": resume_payload or {},
            "projectData": project_data,
        },
        tenant_id=tenant_id,
        max_attempts=int(options.get("maxAttempts") or 3),
    )
    if recovered_from:
        job = store.update(job["jobId"], recoveredFrom=recovered_from) or job
    if not background:
        return _execute_job(job, orchestration_repository, store)

    mode = (os.environ.get("REALYTICS_ASYNC_JOB_MODE") or "development_thread").lower()
    if mode == "external_worker":
        return job
    thread = threading.Thread(
        target=_execute_job,
        args=(job, orchestration_repository, store),
        name=f"pf-review-job-{job['jobId'][:8]}",
        daemon=True,
    )
    thread.start()
    return store.get(job["jobId"]) or job


def process_next_pf_review_job(
    orchestration_repository: Any | None,
    *,
    worker_id: str,
    store: Optional[JobStore] = None,
    repository_factory: Callable[[Dict[str, Any]], Any] | None = None,
) -> Dict[str, Any] | None:
    store = store or _STORE
    job = store.claim(worker_id=worker_id)
    if job is None:
        return None
    try:
        repository = (
            repository_factory(job)
            if repository_factory is not None
            else orchestration_repository
        )
        if repository is None:
            raise RuntimeError("PF review worker repository is not configured")
    except Exception as exc:
        return store.update(
            job["jobId"],
            status="failed",
            completedAt=_now(),
            leaseOwner=None,
            leaseExpiresAt=None,
            error=f"repository_configuration_failed:{exc.__class__.__name__}",
        )
    return _execute_job(job, repository, store, already_claimed=True)


def cancel_pf_review_job(job_id: str, *, reason: str, store: Optional[JobStore] = None) -> Dict[str, Any] | None:
    store = store or _STORE
    job = store.get(job_id)
    if job is None or job.get("status") in TERMINAL_STATUSES:
        return job
    agent_run_id = job.get("agentRunId")
    cancellation_warning = None
    if agent_run_id:
        from agent_platform.runtime import StorageAgentRunRepository

        try:
            StorageAgentRunRepository().request_cancellation(agent_run_id, reason=reason)
        except Exception as exc:
            # The job owns the cancellation decision. The Agent run row may not
            # exist yet during the short claim-to-runtime creation window; the
            # worker re-check below prevents a later completed overwrite.
            cancellation_warning = f"agent_run_cancellation_deferred:{exc.__class__.__name__}"
    return store.update(
        job_id,
        status="cancelled",
        cancellationRequested=True,
        cancellationReason=reason,
        completedAt=_now(),
        leaseOwner=None,
        leaseExpiresAt=None,
        cancellationWarning=cancellation_warning,
    )


def recover_interrupted_jobs(
    resubmit_fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    *,
    project_id: Optional[str] = None,
    store: Optional[JobStore] = None,
) -> Dict[str, Any]:
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


class PFReviewJobWorker:
    def __init__(
        self,
        repository_factory: Callable[[Dict[str, Any]], Any],
        *,
        worker_id: str | None = None,
        store: Optional[JobStore] = None,
    ) -> None:
        self.repository_factory = repository_factory
        self.worker_id = worker_id or f"pf-review-worker:{uuid4()}"
        self.store = store

    def run_once(self) -> Dict[str, Any] | None:
        return process_next_pf_review_job(
            None,
            worker_id=self.worker_id,
            store=self.store,
            repository_factory=self.repository_factory,
        )

    def run_forever(self, *, poll_seconds: float = 1.0) -> None:
        while True:
            if self.run_once() is None:
                time.sleep(max(0.1, poll_seconds))


def _execute_job(
    job: Dict[str, Any],
    orchestration_repository: Any,
    store: JobStore,
    already_claimed: bool = False,
) -> Dict[str, Any]:
    job_id = str(job["jobId"])
    agent_run_id = str(job.get("agentRunId") or f"agent-run:pf-review:{uuid4()}")
    if not already_claimed:
        job = store.update(
            job_id,
            status="running",
            startedAt=job.get("startedAt") or _now(),
            attemptCount=int(job.get("attemptCount") or 0) + 1,
            agentRunId=agent_run_id,
        ) or job
    elif job.get("agentRunId") != agent_run_id:
        job = store.update(job_id, agentRunId=agent_run_id) or {**job, "agentRunId": agent_run_id}
    try:
        params = dict(job.get("params") or {})
        durable = run_pf_review_with_durable_runtime(
            orchestration_repository,
            str(job["projectId"]),
            dict(params.get("projectData") or {}),
            dict(params.get("options") or {}),
            agent_run_id=agent_run_id,
        )
        output = durable["output"]
        current = store.get(job_id) or job
        if current.get("cancellationRequested") or durable.get("agentRunStatus") == "cancelled":
            return store.update(
                job_id,
                status="cancelled",
                completedAt=current.get("completedAt") or _now(),
                leaseOwner=None,
                leaseExpiresAt=None,
                agentRunId=durable.get("agentRunId") or agent_run_id,
                result=None,
            ) or current
        return store.update(
            job_id,
            status="completed",
            completedAt=_now(),
            leaseOwner=None,
            leaseExpiresAt=None,
            agentRunId=durable.get("agentRunId") or agent_run_id,
            runId=_extract_run_id(output),
            result=_result_summary(output),
            error=None,
        ) or job
    except Exception as exc:
        current = store.get(job_id) or job
        exhausted = int(current.get("attemptCount") or 0) >= int(current.get("maxAttempts") or 3)
        return store.update(
            job_id,
            status="failed" if exhausted or not _retryable(exc) else "queued",
            completedAt=_now() if exhausted else None,
            leaseOwner=None,
            leaseExpiresAt=None,
            error=f"{exc.__class__.__name__}: {exc}",
        ) or current


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
        "executionStatus": inner.get("executionStatus") if isinstance(inner, dict) else None,
        "approvalStatus": inner.get("approvalStatus") if isinstance(inner, dict) else None,
        "reportStatus": inner.get("reportStatus") if isinstance(inner, dict) else None,
        "stepCount": len(inner.get("steps", [])) if isinstance(inner, dict) else None,
        "approvalReadiness": gate.get("status") or (gate.get("summary") or {}).get("readiness"),
        "creditDecision": credit.get("decision"),
        "errorCount": len(inner.get("errors", [])) if isinstance(inner, dict) else None,
    }


def _without_secrets(value: Any) -> Any:
    secret_keys = {"apikey", "api_key", "authorization", "token", "secret", "password"}
    if isinstance(value, dict):
        return {
            key: _without_secrets(item)
            for key, item in value.items()
            if str(key).lower() not in secret_keys
        }
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


def _retryable(exc: Exception) -> bool:
    return bool(getattr(exc, "retryable", False))
