from __future__ import annotations

import argparse
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from core.runtime_profile import is_hardened_environment  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the REALYTICS Agent Platform outbox worker. "
            "Available for local development and tests only."
        )
    )
    parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args()

    if is_hardened_environment():
        raise SystemExit(
            "Agent Platform outbox worker is disabled in production/prod/staging: "
            "tenant-scoped job claiming and a dedicated least-privilege worker "
            "connection are not implemented."
        )

    # Import only after the deployment gate, before any repository/handler setup.
    from agent_platform.operational import AgentPlatformWorker

    worker = AgentPlatformWorker(worker_id=args.worker_id)
    if args.once:
        print(worker.run_once(limit=max(1, min(args.limit, 100))))
        return
    worker.run_forever(
        poll_seconds=max(0.1, args.poll_seconds),
        limit=max(1, min(args.limit, 100)),
    )


if __name__ == "__main__":
    main()
