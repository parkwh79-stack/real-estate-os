from __future__ import annotations

"""Inventory and preserve unreferenced Legal Intelligence raw objects.

This operator command is read/append-only for PostgreSQL and has no object
deletion capability.  Use a dedicated corpus-maintenance DSN and an S3/MinIO
bucket with versioning and Object Lock enabled.
"""

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from db import postgres  # noqa: E402
from db.legal_raw_object_storage import (  # noqa: E402
    get_immutable_legal_raw_object_store,
)
from db.postgres_legal_sync_privileges import (  # noqa: E402
    audit_legal_sync_role,
)
from db.postgres_runtime_privileges import assert_current_schema  # noqa: E402
from db.repositories.legal_raw_object_reconciliation_repository import (  # noqa: E402
    PostgresRawObjectReconciliationRepository,
)
from use_cases.legal_corpus_reconciliation import (  # noqa: E402
    LegalRawObjectReconciliationService,
    RawObjectReconciliationPolicy,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Preserve and audit unreferenced immutable legal raw objects"
        )
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--prefix",
        default="legal-corpus/raw/",
    )
    parser.add_argument("--start-cursor")
    parser.add_argument(
        "--page-size",
        type=int,
        default=_integer_env(
            "REALYTICS_LEGAL_RECONCILIATION_PAGE_SIZE", 500
        ),
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=_integer_env(
            "REALYTICS_LEGAL_RECONCILIATION_MAX_PAGES", 100
        ),
    )
    parser.add_argument(
        "--grace-hours",
        type=int,
        default=_integer_env(
            "REALYTICS_LEGAL_ORPHAN_GRACE_HOURS", 24
        ),
    )
    parser.add_argument(
        "--manual-review-days",
        type=int,
        default=_integer_env(
            "REALYTICS_LEGAL_ORPHAN_MANUAL_REVIEW_DAYS", 90
        ),
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=_integer_env(
            "REALYTICS_LEGAL_RAW_RETENTION_DAYS", 365
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dsn = (
        os.environ.get("REALYTICS_LEGAL_SYNC_DATABASE_URL") or ""
    ).strip()
    if not dsn:
        raise RuntimeError(
            "Set REALYTICS_LEGAL_SYNC_DATABASE_URL to the dedicated "
            "corpus-maintenance role DSN."
        )
    schema_name = (
        os.environ.get("REALYTICS_DATABASE_SCHEMA") or "realytics"
    ).strip()
    postgis_schema_name = (
        os.environ.get("REALYTICS_POSTGIS_SCHEMA") or "extensions"
    ).strip()
    readiness_connection = postgres.connect(dsn)
    try:
        with readiness_connection.cursor() as cursor:
            assert_current_schema(cursor, schema_name=schema_name)
            missing, forbidden = audit_legal_sync_role(
                cursor,
                schema_name=schema_name,
                postgis_schema_name=postgis_schema_name,
            )
        if missing or forbidden:
            raise RuntimeError(
                "Legal sync database role readiness failed: "
                f"missing={missing!r}, forbidden={forbidden!r}"
            )
    finally:
        readiness_connection.close()

    object_store = get_immutable_legal_raw_object_store()
    object_store.verify_configuration()
    repository = PostgresRawObjectReconciliationRepository(
        lambda: postgres.connect(dsn),
        search_path=os.environ.get(
            "REALYTICS_LEGAL_SYNC_SEARCH_PATH",
            "realytics,extensions,public,pg_temp",
        ),
    )
    policy = RawObjectReconciliationPolicy(
        grace_period=timedelta(hours=args.grace_hours),
        manual_disposition_age=timedelta(
            days=args.manual_review_days
        ),
        minimum_retention=timedelta(days=args.retention_days),
        page_size=args.page_size,
        max_pages=args.max_pages,
    )
    report = LegalRawObjectReconciliationService(
        repository,
        object_store,
        policy=policy,
    ).reconcile(
        reconciliation_run_id=args.run_id,
        object_prefix=args.prefix,
        start_cursor=args.start_cursor,
    )
    print(
        json.dumps(
            {
                "reconciliationRunId": report.reconciliation_run_id,
                "status": report.status,
                "pagesScanned": report.pages_scanned,
                "objectsScanned": len(report.observations),
                "counts": dict(report.counts),
                "nextCursor": report.next_cursor,
                "inventoryManifestSha256": (
                    report.inventory_manifest_sha256
                ),
                "deletionPermitted": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _integer_env(name: str, default: int) -> int:
    value = (os.environ.get(name) or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


if __name__ == "__main__":
    raise SystemExit(main())
