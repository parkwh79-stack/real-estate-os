from __future__ import annotations

import time
from uuid import uuid4

from agent_platform.operational.handlers import build_default_outbox_handlers
from agent_platform.operational.outbox import RuntimeOutboxDispatcher
from agent_platform.operational.repository import OperationalRepository


class AgentPlatformWorker:
    """Small process-safe worker facade for outbox delivery and recovery projections."""

    def __init__(self, *, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"agent-platform-worker:{uuid4()}"
        self.dispatcher = RuntimeOutboxDispatcher(
            repository=OperationalRepository(),
            handlers=build_default_outbox_handlers(),
        )

    def run_once(self, *, limit: int = 20) -> dict[str, int]:
        return self.dispatcher.publish_batch(worker_id=self.worker_id, limit=limit)

    def run_forever(self, *, poll_seconds: float = 1.0, limit: int = 20) -> None:
        while True:
            result = self.run_once(limit=limit)
            if result["claimed"] == 0:
                time.sleep(max(0.1, poll_seconds))
