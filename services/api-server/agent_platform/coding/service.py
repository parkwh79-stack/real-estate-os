from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_platform.contracts import CodingPodJob, CodingPodJobStatus
from agent_platform.operational import OperationalRepository


class CodingPodLifecycleError(RuntimeError):
    pass


class CodingPodService:
    """Queues isolated coding work; the API process never executes repository commands."""

    def __init__(self, repository: OperationalRepository) -> None:
        self._repository = repository

    def submit(self, job: CodingPodJob) -> CodingPodJob:
        existing = self._repository.list(
            CodingPodJob,
            filters={"tenant_id": job.tenant_id, "idempotency_key": job.idempotency_key},
            limit=1,
        )
        if existing:
            return existing[0]
        validated = job.model_copy(update={
            "status": CodingPodJobStatus.QUEUED,
            "updated_at": datetime.now(timezone.utc),
        })
        try:
            return self._repository.save(validated)
        except Exception:
            # The database unique key is the cross-process idempotency lock.
            existing = self._repository.list(
                CodingPodJob,
                filters={
                    "tenant_id": job.tenant_id,
                    "idempotency_key": job.idempotency_key,
                },
                limit=1,
            )
            if existing:
                return existing[0]
            raise

    def claim(self, *, worker_id: str, lease_seconds: int = 120) -> CodingPodJob | None:
        return self._repository.claim_coding_job(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )

    def start(self, job_id: str, *, worker_id: str) -> CodingPodJob:
        return self._owned_transition(job_id, worker_id, CodingPodJobStatus.RUNNING)

    def heartbeat(self, job_id: str, *, worker_id: str, lease_seconds: int = 120) -> CodingPodJob:
        job = self._owned(job_id, worker_id)
        now = datetime.now(timezone.utc)
        return self._repository.save(job.model_copy(update={
            "heartbeat_at": now,
            "lease_expires_at": now + timedelta(seconds=max(30, lease_seconds)),
            "state_version": job.state_version + 1,
            "updated_at": now,
        }))

    def complete(
        self,
        job_id: str,
        *,
        worker_id: str,
        artifact_ids: list[str],
    ) -> CodingPodJob:
        if not artifact_ids:
            raise CodingPodLifecycleError("CODING_POD_RESULT_ARTIFACT_REQUIRED")
        job = self._owned(job_id, worker_id)
        return self._repository.save(job.model_copy(update={
            "status": CodingPodJobStatus.SUCCEEDED,
            "result_artifact_ids": artifact_ids,
            "lease_owner": None,
            "lease_expires_at": None,
            "state_version": job.state_version + 1,
            "updated_at": datetime.now(timezone.utc),
        }))

    def fail(self, job_id: str, *, worker_id: str, error: str) -> CodingPodJob:
        job = self._owned(job_id, worker_id)
        return self._repository.save(job.model_copy(update={
            "status": CodingPodJobStatus.FAILED,
            "error_message": error[:2000],
            "lease_owner": None,
            "lease_expires_at": None,
            "state_version": job.state_version + 1,
            "updated_at": datetime.now(timezone.utc),
        }))

    def cancel(self, job_id: str, *, requested_by: str, reason: str) -> CodingPodJob:
        job = self._repository.get(CodingPodJob, job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status in {
            CodingPodJobStatus.SUCCEEDED,
            CodingPodJobStatus.FAILED,
            CodingPodJobStatus.CANCELLED,
            CodingPodJobStatus.EXPIRED,
        }:
            if job.status == CodingPodJobStatus.CANCELLED:
                return job
            raise CodingPodLifecycleError("CODING_POD_TERMINAL_JOB_CANNOT_BE_CANCELLED")
        now = datetime.now(timezone.utc)
        return self._repository.save(job.model_copy(update={
            "status": CodingPodJobStatus.CANCELLED,
            "lease_owner": None,
            "lease_expires_at": None,
            "error_message": f"Cancelled by {requested_by}: {reason}"[:2000],
            "state_version": job.state_version + 1,
            "updated_at": now,
        }))

    def _owned_transition(
        self,
        job_id: str,
        worker_id: str,
        status: CodingPodJobStatus,
    ) -> CodingPodJob:
        job = self._owned(job_id, worker_id)
        return self._repository.save(job.model_copy(update={
            "status": status,
            "state_version": job.state_version + 1,
            "updated_at": datetime.now(timezone.utc),
        }))

    def _owned(self, job_id: str, worker_id: str) -> CodingPodJob:
        job = self._repository.get(CodingPodJob, job_id)
        if job is None:
            raise KeyError(job_id)
        if job.lease_owner != worker_id or job.status not in {
            CodingPodJobStatus.LEASED,
            CodingPodJobStatus.RUNNING,
        }:
            raise CodingPodLifecycleError("CODING_POD_LEASE_NOT_OWNED")
        if job.lease_expires_at and job.lease_expires_at <= datetime.now(timezone.utc):
            raise CodingPodLifecycleError("CODING_POD_LEASE_EXPIRED")
        return job
