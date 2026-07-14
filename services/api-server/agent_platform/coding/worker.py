from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from agent_platform.coding.service import CodingPodService
from agent_platform.contracts import CodingPodJob


class IsolatedCodingExecutor(Protocol):
    """Implemented only by a separately deployed sandbox/container worker."""

    def execute(
        self,
        job: CodingPodJob,
        *,
        heartbeat: Callable[[], None],
    ) -> list[str]: ...


class CodingPodWorker:
    """Runs one leased job through an injected isolated execution environment."""

    def __init__(
        self,
        *,
        worker_id: str,
        service: CodingPodService,
        executor: IsolatedCodingExecutor,
        lease_seconds: int = 120,
    ) -> None:
        self.worker_id = worker_id
        self.service = service
        self.executor = executor
        self.lease_seconds = max(30, lease_seconds)

    def run_once(self) -> CodingPodJob | None:
        job = self.service.claim(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if job is None:
            return None
        running = self.service.start(job.job_id, worker_id=self.worker_id)

        def heartbeat() -> None:
            self.service.heartbeat(
                running.job_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )

        try:
            artifact_ids = self.executor.execute(running, heartbeat=heartbeat)
            return self.service.complete(
                running.job_id,
                worker_id=self.worker_id,
                artifact_ids=artifact_ids,
            )
        except Exception as exc:
            self.service.fail(
                running.job_id,
                worker_id=self.worker_id,
                error=str(exc),
            )
            raise
