from __future__ import annotations

import os
import time
import argparse
import sys
from pathlib import Path
from uuid import uuid4


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from core.runtime_profile import is_hardened_environment  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Coding Pod worker for local development or tests.")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    args = parser.parse_args()
    if is_hardened_environment():
        raise SystemExit(
            "Coding Pod worker is disabled in production/prod/staging: "
            "a dedicated tenant-scoped least-privilege connection and sandbox "
            "deployment have not been qualified."
        )
    from agent_platform.coding import CodingPodService, CodingPodWorker
    from agent_platform.coding.remote_executor import RemoteIsolatedCodingExecutor
    from agent_platform.operational import OperationalRepository
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
        if args.once:
            return
        if job is None:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()
