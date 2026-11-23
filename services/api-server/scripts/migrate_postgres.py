from __future__ import annotations

"""Apply immutable PostgreSQL migrations with a deployment-only database role."""

import os
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from db import postgres  # noqa: E402
from db.postgres_runtime_privileges import (  # noqa: E402
    DEFAULT_RUNTIME_SCHEMA,
    RUNTIME_SCHEMA_ENV,
    assert_current_schema,
)


def main() -> int:
    dsn = os.environ.get("REALYTICS_MIGRATION_DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "Set REALYTICS_MIGRATION_DATABASE_URL to a deployment-role DSN. "
            "The runtime application DSN must not own or alter Parser V2 tables."
        )
    connection = postgres.connect(dsn)
    try:
        with connection.cursor() as cursor:
            assert_current_schema(
                cursor,
                schema_name=os.environ.get(
                    RUNTIME_SCHEMA_ENV,
                    DEFAULT_RUNTIME_SCHEMA,
                ),
            )
        postgres.ensure_schema(connection)
    finally:
        connection.close()
    print("PostgreSQL migrations are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
