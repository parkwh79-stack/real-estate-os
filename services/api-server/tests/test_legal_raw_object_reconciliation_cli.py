from __future__ import annotations

import pytest

from scripts import reconcile_legal_raw_objects as command


def test_reconciliation_command_requires_dedicated_database_dsn(
    monkeypatch,
):
    monkeypatch.delenv(
        "REALYTICS_LEGAL_SYNC_DATABASE_URL", raising=False
    )

    with pytest.raises(
        RuntimeError,
        match="dedicated corpus-maintenance role DSN",
    ):
        command.main(["--run-id", "reconcile-1"])


def test_reconciliation_parser_has_bounded_safe_defaults(monkeypatch):
    for name in (
        "REALYTICS_LEGAL_RECONCILIATION_PAGE_SIZE",
        "REALYTICS_LEGAL_RECONCILIATION_MAX_PAGES",
        "REALYTICS_LEGAL_ORPHAN_GRACE_HOURS",
        "REALYTICS_LEGAL_ORPHAN_MANUAL_REVIEW_DAYS",
        "REALYTICS_LEGAL_RAW_RETENTION_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)

    args = command.build_parser().parse_args(
        ["--run-id", "reconcile-1"]
    )

    assert args.page_size == 500
    assert args.max_pages == 100
    assert args.grace_hours == 24
    assert args.manual_review_days == 90
    assert args.retention_days == 365
