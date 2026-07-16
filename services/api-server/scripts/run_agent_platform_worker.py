from __future__ import annotations

import argparse
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from agent_platform.operational import AgentPlatformWorker  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the REALYTICS Agent Platform outbox worker.")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--worker-id", default=None)
    args = parser.parse_args()

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
