from __future__ import annotations

"""Provision the least-privilege Parser V2 PostgreSQL runtime role."""

import os
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from db import postgres  # noqa: E402
from db.postgres_runtime_privileges import (  # noqa: E402
    DEFAULT_POSTGIS_SCHEMA,
    DEFAULT_RUNTIME_SCHEMA,
    POSTGIS_SCHEMA_ENV,
    RUNTIME_ROLE_ENV,
    RUNTIME_SCHEMA_ENV,
    assert_current_schema,
    provision_parser_v2_runtime_role,
    revoke_public_database_temporary_enabled,
)


def main() -> int:
    migration_dsn = os.environ.get("REALYTICS_MIGRATION_DATABASE_URL")
    if not migration_dsn:
        raise RuntimeError(
            "Set REALYTICS_MIGRATION_DATABASE_URL to a deployment-role DSN."
        )
    role_name = os.environ.get(RUNTIME_ROLE_ENV)
    if not role_name:
        raise RuntimeError(f"Set {RUNTIME_ROLE_ENV} to the existing runtime role name.")
    schema_name = os.environ.get(RUNTIME_SCHEMA_ENV, DEFAULT_RUNTIME_SCHEMA)
    postgis_schema_name = os.environ.get(
        POSTGIS_SCHEMA_ENV,
        DEFAULT_POSTGIS_SCHEMA,
    )

    connection = postgres.connect(migration_dsn)
    try:
        with connection.cursor() as cursor:
            assert_current_schema(cursor, schema_name=schema_name)
        # Applying grants to a partially migrated schema is unsafe.  The same
        # deployment role advances/verifies the immutable ledger first.
        postgres.ensure_schema(connection)
        try:
            provision_parser_v2_runtime_role(
                connection,
                role_name=role_name,
                schema_name=schema_name,
                postgis_schema_name=postgis_schema_name,
                migration_lock_key=postgres.MIGRATION_LOCK_KEY,
                revoke_public_database_temporary=(
                    revoke_public_database_temporary_enabled()
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()

    print(
        "Parser V2 PostgreSQL runtime privileges are current for "
        f"role={role_name!r}, schema={schema_name!r}, "
        f"postgis_schema={postgis_schema_name!r}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
