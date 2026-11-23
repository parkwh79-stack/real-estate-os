from __future__ import annotations

"""Evaluate one frozen legal golden corpus from the command line.

Exit codes:
  0: all approved thresholds passed
  2: artifact integrity or schema validation failed
  3: evaluation completed but one or more thresholds failed
"""

import argparse
import json
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from legal_intelligence.golden_corpus import (  # noqa: E402
    GoldenCorpusIntegrityError,
)
from use_cases.legal_golden_corpus_evaluation import (  # noqa: E402
    evaluate_legal_golden_corpus_files,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and score a versioned legal golden-corpus prediction run"
        )
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to the approved corpus manifest JSON",
    )
    parser.add_argument(
        "--predictions",
        required=True,
        help="Path to the complete prediction JSONL artifact",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate_legal_golden_corpus_files(
            manifest_path=args.manifest,
            predictions_path=args.predictions,
        )
    except (GoldenCorpusIntegrityError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schemaVersion": (
                        "realytics-legal-golden-evaluation-error-v1"
                    ),
                    "status": "invalid_artifact",
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(
        json.dumps(
            report.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report.passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
