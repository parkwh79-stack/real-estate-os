from __future__ import annotations

"""Provision the dedicated append-only Legal Intelligence sync role."""

import os
import sys
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from db import postgres  # noqa: E402
from db.postgres_legal_sync_privileges import (  # noqa: E402
    LEGAL_SYNC_POSTGIS_SCHEMA_ENV,
    LEGAL_SYNC_ROLE_ENV,
    LEGAL_SYNC_SCHEMA_ENV,
    provision_legal_sync_role,
)
from db.postgres_runtime_privileges import assert_current_schema  # noqa: E402


def main() -> int:
    migration_dsn = (
        os.environ.get("REALYTICS_MIGRATION_DATABASE_URL") or ""
    ).strip()
    if not migration_dsn:
        raise RuntimeError(
            "Set REALYTICS_MIGRATION_DATABASE_URL to the deployment-role DSN."
        )
    role_name = (os.environ.get(LEGAL_SYNC_ROLE_ENV) or "").strip()
    if not role_name:
        raise RuntimeError(
            f"Set {LEGAL_SYNC_ROLE_ENV} to the dedicated sync role name."
        )
    schema_name = (
        os.environ.get(LEGAL_SYNC_SCHEMA_ENV) or "realytics"
    ).strip()
    postgis_schema_name = (
        os.environ.get(LEGAL_SYNC_POSTGIS_SCHEMA_ENV) or "extensions"
    ).strip()

    connection = postgres.connect(migration_dsn)
    try:
        with connection.cursor() as cursor:
            assert_current_schema(cursor, schema_name=schema_name)
        postgres.ensure_schema(connection)
        try:
            provision_legal_sync_role(
                connection,
                role_name=role_name,
                schema_name=schema_name,
                postgis_schema_name=postgis_schema_name,
                migration_lock_key=postgres.MIGRATION_LOCK_KEY,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()

    print(
        "Legal Intelligence sync privileges are current for "
        f"role={role_name!r}, schema={schema_name!r}."
    )
    print(
        "The role remains NOLOGIN until an operator activates LOGIN and "
        "injects its password through the approved secret manager."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
