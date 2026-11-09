"""Run the isolated, scheduled official legal-corpus worker."""

from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from use_cases.legal_corpus_runtime import (  # noqa: E402
    build_legal_corpus_scheduler,
    build_legal_corpus_worker,
)
from use_cases.legal_corpus_worker import (  # noqa: E402
    load_legal_corpus_runtime_configuration,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run REALYTICS official legal-corpus synchronization using the "
            "dedicated sync role and immutable raw-object store."
        )
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run all configured jobs once and exit.",
    )
    args = parser.parse_args()

    if args.once:
        configuration = load_legal_corpus_runtime_configuration(
            require_schedule=False,
        )
        worker = build_legal_corpus_worker(
            configuration=configuration,
        )
        print(
            json.dumps(
                worker.run_once().as_dict(),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )
        return 0

    configuration = load_legal_corpus_runtime_configuration()
    scheduler = build_legal_corpus_scheduler(
        configuration=configuration,
    )
    stopping = False

    def request_stop(*_args: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)

    scheduler.run_forever(
        should_stop=lambda: stopping,
        on_report=lambda report: print(
            json.dumps(
                report.as_dict(),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ),
            flush=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
