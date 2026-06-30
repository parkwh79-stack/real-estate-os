from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from use_cases.glossary import import_term_standard_seed_use_case  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import a REALYTICS term-standard seed JSON into the runtime glossary store."
    )
    parser.add_argument("seed_file", help="Path to realytics_term_seed_v3.json or compatible seed JSON.")
    parser.add_argument("--actor", default="term-standard-importer", help="Audit actor recorded in review history.")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace existing glossary terms with seed values. Omit to skip existing term IDs.",
    )
    args = parser.parse_args()

    seed_path = Path(args.seed_file).expanduser().resolve()
    if not seed_path.exists():
        raise SystemExit(f"Seed file not found: {seed_path}")
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    result = import_term_standard_seed_use_case(
        payload,
        actor=args.actor,
        replace_existing=args.replace_existing,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
