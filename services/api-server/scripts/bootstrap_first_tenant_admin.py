"""Safely provision one tenant's first normalized administrator."""

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
from db.postgres_runtime_privileges import (  # noqa: E402
    RUNTIME_SCHEMA_ENV,
)
from db.repositories.first_tenant_admin_bootstrap_repository import (  # noqa: E402
    DEFAULT_OPERATOR_ISSUER,
    FirstTenantAdminBootstrapError,
    PostgresFirstTenantAdminBootstrapRepository,
    build_first_tenant_admin_plan,
)


MIGRATION_DATABASE_URL_ENV = "REALYTICS_MIGRATION_DATABASE_URL"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect or atomically create one tenant's first admin membership. "
            "The target is identified only by the exact Supabase JWT issuer "
            "and subject; email is never an identity key."
        )
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--tenant-name", required=True)
    parser.add_argument(
        "--issuer",
        required=True,
        help="Exact signed-token issuer, for example https://project.supabase.co/auth/v1",
    )
    parser.add_argument(
        "--subject",
        required=True,
        help="Exact Supabase Auth user UUID from the signed token subject claim.",
    )
    parser.add_argument(
        "--provider",
        default="google",
        help="Supabase Auth identity provider that must be linked (default: google).",
    )
    parser.add_argument(
        "--display-name",
        help="Optional non-authoritative profile label stored on a new principal.",
    )
    parser.add_argument(
        "--operator-issuer",
        default=DEFAULT_OPERATOR_ISSUER,
        help="Audited identifier namespace for the platform operator.",
    )
    parser.add_argument(
        "--operator-subject",
        required=True,
        help="Audited operator/change-ticket identity performing this bootstrap.",
    )
    parser.add_argument(
        "--schema",
        default=(
            os.environ.get(RUNTIME_SCHEMA_ENV)
            or "realytics"
        ),
        help=(
            "Private application schema. Defaults to "
            f"{RUNTIME_SCHEMA_ENV} or 'realytics'."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Commit the bootstrap. Without this flag the command performs a "
            "read-only preflight and rolls the transaction back."
        ),
    )
    args = parser.parse_args(argv)

    dsn = str(os.environ.get(MIGRATION_DATABASE_URL_ENV) or "").strip()
    if not dsn:
        raise RuntimeError(
            f"Set {MIGRATION_DATABASE_URL_ENV} to the deployment-role DSN. "
            "Runtime and identity-role DSNs cannot bootstrap a tenant owner."
        )

    try:
        plan = build_first_tenant_admin_plan(
            tenant_id=args.tenant_id,
            tenant_name=args.tenant_name,
            issuer=args.issuer,
            subject=args.subject,
            provider=args.provider,
            operator_issuer=args.operator_issuer,
            operator_subject=args.operator_subject,
            display_name=args.display_name,
        )
        repository = PostgresFirstTenantAdminBootstrapRepository(
            connection_factory=lambda: postgres.connect(dsn),
            schema_name=args.schema,
        )
        result = (
            repository.bootstrap(plan)
            if args.apply
            else repository.inspect(plan)
        )
    except (ValueError, FirstTenantAdminBootstrapError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            result.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
