"""Apply only the owner-approved profile CRUD/policy to the deployment role."""
from __future__ import annotations

import os
import sys
import argparse
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from db import postgres  # noqa: E402
from db.postgres_runtime_privileges import RUNTIME_ROLE_ENV, RUNTIME_SCHEMA_ENV, assert_current_schema  # noqa: E402
from db.user_ai_profile_privileges import render_user_ai_profile_access_sql, render_user_ai_profile_access_rollback_sql, user_ai_profile_access_issues  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner-approved personal AI profile access only; no schema migrations or other-role grants.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--render", action="store_true", help="Print transaction SQL without a database connection.")
    action.add_argument("--rollback-sql", action="store_true", help="Print guarded permission/policy rollback SQL; never delete profile data.")
    action.add_argument("--apply", action="store_true", help="Apply and audit through the approved deployment DSN.")
    parser.add_argument("--role", default=os.environ.get(RUNTIME_ROLE_ENV))
    parser.add_argument("--schema", default=os.environ.get(RUNTIME_SCHEMA_ENV, "realytics"))
    args = parser.parse_args(argv)
    if not args.role:
        parser.error("An explicit --role or REALYTICS_RUNTIME_DATABASE_ROLE is required")
    role, schema = args.role, args.schema
    statements = (render_user_ai_profile_access_rollback_sql(role_name=role, schema_name=schema) if args.rollback_sql
                  else render_user_ai_profile_access_sql(role_name=role, schema_name=schema))
    if not args.apply:
        print("BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;")
        return 0
    dsn = os.environ.get("REALYTICS_MIGRATION_DATABASE_URL")
    if not dsn:
        raise RuntimeError("An approved deployment DSN is required")
    connection = postgres.connect(dsn)
    try:
        with connection.cursor() as cursor:
            assert_current_schema(cursor, schema_name=schema)
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (postgres.MIGRATION_LOCK_KEY,))
            for statement in statements:
                cursor.execute(statement)
            if user_ai_profile_access_issues(cursor, role_name=role, schema_name=schema):
                raise RuntimeError("Approved user AI policy audit failed")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print("Approved personal AI profile access provisioned and audited.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
