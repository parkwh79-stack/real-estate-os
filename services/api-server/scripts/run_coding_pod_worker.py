from __future__ import annotations

import os
import time
from uuid import uuid4

from agent_platform.coding import CodingPodService, CodingPodWorker
from agent_platform.coding.remote_executor import RemoteIsolatedCodingExecutor
from agent_platform.operational import OperationalRepository


def main() -> None:
    worker_id = os.environ.get("REALYTICS_CODING_WORKER_ID") or f"coding-worker:{uuid4()}"
    poll_seconds = max(0.25, float(os.environ.get("REALYTICS_CODING_WORKER_POLL_SECONDS") or 1))
    lease_seconds = max(30, int(os.environ.get("REALYTICS_CODING_WORKER_LEASE_SECONDS") or 120))
    executor = RemoteIsolatedCodingExecutor(
        timeout_seconds=int(os.environ.get("REALYTICS_CODING_EXECUTOR_TIMEOUT_SECONDS") or 900),
        heartbeat_seconds=max(5, lease_seconds // 3),
    )
    worker = CodingPodWorker(
        worker_id=worker_id,
        service=CodingPodService(OperationalRepository()),
        executor=executor,
        lease_seconds=lease_seconds,
    )
    while True:
        job = worker.run_once()
        if job is None:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
