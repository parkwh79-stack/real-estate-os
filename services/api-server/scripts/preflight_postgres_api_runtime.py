from __future__ import annotations

"""Read-only, secret-safe preflight for the dedicated API PostgreSQL DSN."""

import json
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from db.postgres_api_runtime_preflight import (  # noqa: E402
    run_api_runtime_preflight,
)


def main() -> int:
    result = run_api_runtime_preflight()
    print(
        json.dumps(
            result.to_safe_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
