"""Verify a least-privilege identity DSN without printing credentials."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from db import postgres  # noqa: E402


IDENTITY_DATABASE_URL_ENV = "REALYTICS_IDENTITY_DATABASE_URL"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify that the dedicated identity DSN can resolve one exact "
            "issuer + subject membership without exposing its credential."
        )
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument(
        "--expected-role",
        default="realytics_identity",
        help="Expected PostgreSQL current_user (default: realytics_identity).",
    )
    args = parser.parse_args(argv)

    dsn = str(os.environ.get(IDENTITY_DATABASE_URL_ENV) or "").strip()
    if not dsn:
        parser.error(f"{IDENTITY_DATABASE_URL_ENV} is required")

    connection = postgres.connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    current_user,
                    session_user,
                    pg_catalog.has_schema_privilege(
                        current_user,
                        'realytics',
                        'USAGE'
                    ),
                    pg_catalog.has_table_privilege(
                        current_user,
                        'realytics.principals',
                        'SELECT'
                    ),
                    pg_catalog.has_table_privilege(
                        current_user,
                        'realytics.tenant_memberships',
                        'SELECT'
                    ),
                    pg_catalog.has_table_privilege(
                        current_user,
                        'realytics.tenant_memberships',
                        'UPDATE'
                    ),
                    pg_catalog.has_table_privilege(
                        current_user,
                        'realytics.tenant_memberships',
                        'DELETE'
                    )
                """
            )
            privileges = cursor.fetchone()
            if privileges is None:
                raise RuntimeError("identity role privilege query returned no row")

            cursor.execute(
                """
                SELECT
                    principal.principal_type,
                    principal.status,
                    membership.role,
                    membership.status,
                    membership.membership_version
                FROM realytics.principals AS principal
                JOIN realytics.tenant_memberships AS membership
                  ON membership.issuer = principal.issuer
                 AND membership.subject = principal.subject
                WHERE membership.tenant_id = %s
                  AND principal.issuer = %s
                  AND principal.subject = %s
                """,
                (
                    str(args.tenant_id).strip(),
                    str(args.issuer).strip().rstrip("/"),
                    str(args.subject).strip(),
                ),
            )
            authority = cursor.fetchone()
        connection.rollback()
    finally:
        connection.close()

    current_user = str(privileges[0])
    result = {
        "currentUser": current_user,
        "sessionUser": str(privileges[1]),
        "schemaUsage": bool(privileges[2]),
        "principalSelect": bool(privileges[3]),
        "membershipSelect": bool(privileges[4]),
        "membershipUpdate": bool(privileges[5]),
        "membershipDelete": bool(privileges[6]),
        "authority": (
            {
                "principalType": str(authority[0]),
                "principalStatus": str(authority[1]),
                "role": str(authority[2]),
                "membershipStatus": str(authority[3]),
                "membershipVersion": int(authority[4]),
            }
            if authority is not None
            else None
        ),
    }
    safe = (
        current_user == str(args.expected_role).strip()
        and result["schemaUsage"]
        and result["principalSelect"]
        and result["membershipSelect"]
        and not result["membershipUpdate"]
        and not result["membershipDelete"]
        and result["authority"]
        == {
            "principalType": "human",
            "principalStatus": "active",
            "role": "admin",
            "membershipStatus": "active",
            "membershipVersion": 1,
        }
    )
    result["safe"] = safe
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0 if safe else 1


if __name__ == "__main__":
    raise SystemExit(main())
