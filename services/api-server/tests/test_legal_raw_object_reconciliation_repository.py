from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from db.repositories import (
    legal_raw_object_reconciliation_repository as repository_module,
)
from db.repositories.legal_raw_object_reconciliation_repository import (
    PostgresRawObjectReconciliationRepository,
)
from use_cases.legal_corpus_reconciliation import (
    RawObjectIdentity,
    RawObjectInventoryItem,
    RawObjectReconciliationError,
    RawObjectReconciliationObservation,
    RawObjectReconciliationReport,
)


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
IDENTITY = RawObjectIdentity(
    "legal-corpus/raw/provider/aa/" + "a" * 64,
    "version-1",
)


class FakeCursor:
    def __init__(self):
        self.calls = []
        self.lookup_rows = []
        self.run_inserted = True
        self.item_inserted = True
        self.existing_run = None
        self.existing_item = None
        self.statement = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.statement = statement
        self.calls.append((statement, params))

    def fetchall(self):
        if "WITH inventory AS" in self.statement:
            return list(self.lookup_rows)
        return []

    def fetchone(self):
        if "INSERT INTO legal_raw_object_reconciliation_runs" in self.statement:
            return ("run-1",) if self.run_inserted else None
        if "INSERT INTO legal_raw_object_reconciliation_items" in self.statement:
            return ("item-1",) if self.item_inserted else None
        if "FROM legal_raw_object_reconciliation_runs" in self.statement:
            return self.existing_run
        if "FROM legal_raw_object_reconciliation_items" in self.statement:
            return self.existing_item
        return None


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def inventory_item():
    return RawObjectInventoryItem(
        identity=IDENTITY,
        sha256="a" * 64,
        size_bytes=123,
        created_at=NOW - timedelta(days=3),
        metadata={"sync-run-id": "sync-run-1"},
    )


def report():
    observation = RawObjectReconciliationObservation(
        identity=IDENTITY,
        sha256="a" * 64,
        size_bytes=123,
        created_at=NOW - timedelta(days=3),
        observation_status="orphan_preserved",
        source_fetch_id=None,
        sync_run_id="sync-run-1",
        sync_status="failed",
        retention_until=NOW + timedelta(days=365),
        observed_at=NOW,
    )
    return RawObjectReconciliationReport(
        reconciliation_run_id="reconcile-1",
        object_prefix="legal-corpus/raw/",
        started_at=NOW,
        completed_at=NOW,
        status="completed",
        start_cursor=None,
        next_cursor=None,
        pages_scanned=1,
        policy_version="legal-raw-reconciliation-v1",
        grace_period_seconds=86_400,
        manual_disposition_age_seconds=7_776_000,
        minimum_retention_seconds=31_536_000,
        observations=(observation,),
        inventory_manifest_sha256="b" * 64,
    )


def test_lookup_returns_reference_and_latest_sync_state():
    cursor = FakeCursor()
    cursor.lookup_rows = [
        (
            IDENTITY.object_key,
            IDENTITY.object_version,
            "source-fetch-1",
            "sync-run-1",
            "completed",
        )
    ]
    connection = FakeConnection(cursor)
    repository = PostgresRawObjectReconciliationRepository(
        lambda: connection
    )

    states = repository.lookup_database_states((inventory_item(),))

    state = states[IDENTITY]
    assert state.is_referenced is True
    assert state.source_fetch_id == "source-fetch-1"
    assert state.sync_status == "completed"
    assert cursor.calls[0][1] == (
        "realytics,extensions,public,pg_temp",
    )
    assert connection.closed is True


def test_record_report_is_append_only_and_marks_deletion_forbidden():
    cursor = FakeCursor()
    repository = PostgresRawObjectReconciliationRepository(
        lambda: FakeConnection(cursor)
    )

    repository.record_report(report())

    statements = "\n".join(call[0] for call in cursor.calls)
    assert "INSERT INTO legal_raw_object_reconciliation_runs" in statements
    assert "INSERT INTO legal_raw_object_reconciliation_items" in statements
    assert " UPDATE " not in f" {statements.upper()} "
    assert " DELETE " not in f" {statements.upper()} "
    serialized_params = repr([call[1] for call in cursor.calls])
    assert '"deletionPermitted":false' in serialized_params
    assert "preserve_pending_manual_review" in serialized_params


def test_replayed_run_ignores_clock_fields_but_requires_same_manifest():
    value = report()
    cursor = FakeCursor()
    cursor.run_inserted = False
    cursor.existing_run = (
        value.object_prefix,
        value.start_cursor,
        value.next_cursor,
        value.started_at - timedelta(hours=1),
        value.completed_at - timedelta(hours=1),
        value.status,
        value.pages_scanned,
        len(value.observations),
        value.inventory_manifest_sha256,
        dict(value.counts),
    )
    repository = PostgresRawObjectReconciliationRepository(
        lambda: FakeConnection(cursor)
    )

    repository.record_report(value)


def test_replayed_run_rejects_manifest_conflict():
    value = report()
    cursor = FakeCursor()
    cursor.run_inserted = False
    cursor.existing_run = (
        value.object_prefix,
        value.start_cursor,
        value.next_cursor,
        value.started_at,
        value.completed_at,
        value.status,
        value.pages_scanned,
        len(value.observations),
        "f" * 64,
        dict(value.counts),
    )
    repository = PostgresRawObjectReconciliationRepository(
        lambda: FakeConnection(cursor)
    )

    with pytest.raises(
        RawObjectReconciliationError,
        match="different report",
    ):
        repository.record_report(value)


def test_repository_requires_pg_temp_last():
    with pytest.raises(ValueError, match="pg_temp"):
        PostgresRawObjectReconciliationRepository(
            lambda: None,
            search_path="realytics,pg_temp,public",
        )


def test_lookup_sql_uses_exact_object_key_and_version():
    sql = repository_module._LOOKUP_DATABASE_STATES_SQL

    assert "fetch.raw_object_key = inventory.object_key" in sql
    assert "fetch.raw_object_version = inventory.object_version" in sql
    assert "ORDER BY event.sequence_number DESC" in sql
