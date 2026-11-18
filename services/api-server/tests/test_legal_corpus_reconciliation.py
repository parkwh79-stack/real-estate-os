from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from use_cases.legal_corpus_reconciliation import (
    LegalRawObjectReconciliationService,
    RawObjectDatabaseState,
    RawObjectIdentity,
    RawObjectInventoryConflict,
    RawObjectInventoryItem,
    RawObjectInventoryPage,
    RawObjectReconciliationPolicy,
    RawObjectRetentionError,
    RetentionHoldReceipt,
)


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def item(
    name: str,
    *,
    age: timedelta,
    sha: str | None = None,
    sync_run_id: str | None = None,
    retention_until: datetime | None = None,
) -> RawObjectInventoryItem:
    metadata = {}
    if sync_run_id is not None:
        metadata["sync-run-id"] = sync_run_id
    return RawObjectInventoryItem(
        identity=RawObjectIdentity(
            object_key=f"legal-corpus/raw/provider/{name}",
            object_version=f"version-{name}",
        ),
        sha256=sha or (name[0].lower() * 64),
        size_bytes=100 + len(name),
        created_at=NOW - age,
        retention_until=retention_until,
        metadata=metadata,
    )


class FakeObjectStore:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []
        self.holds = []
        self.bad_receipt = False

    def list_inventory(self, *, prefix, cursor, limit):
        self.calls.append((prefix, cursor, limit))
        return self.pages.pop(0)

    def ensure_retention(
        self,
        *,
        identity,
        retain_until,
        reason,
        metadata,
    ):
        self.holds.append(
            (identity, retain_until, reason, dict(metadata))
        )
        return RetentionHoldReceipt(
            identity=(
                RawObjectIdentity(
                    "legal-corpus/raw/provider/wrong",
                    "wrong-version",
                )
                if self.bad_receipt
                else identity
            ),
            retained_until=retain_until,
            immutable=True,
        )


class FakeRepository:
    def __init__(self, states=None):
        self.states = dict(states or {})
        self.reports = []
        self.lookup_batches = []

    def lookup_database_states(self, items):
        self.lookup_batches.append(tuple(items))
        identities = {value.identity for value in items}
        return {
            identity: state
            for identity, state in self.states.items()
            if identity in identities
        }

    def record_report(self, report):
        self.reports.append(report)


def test_reconciliation_classifies_and_preserves_orphans_without_delete():
    referenced = item("a", age=timedelta(days=120))
    active = item(
        "b",
        age=timedelta(days=120),
        sync_run_id="sync-run-b",
    )
    grace = item("c", age=timedelta(hours=2))
    orphan = item("d", age=timedelta(days=3))
    old_orphan = item(
        "e",
        age=timedelta(days=120),
        retention_until=NOW + timedelta(days=500),
    )
    pages = [
        RawObjectInventoryPage(
            items=(referenced, active, grace, orphan, old_orphan),
            next_cursor=None,
            inventory_complete=True,
        )
    ]
    states = {
        referenced.identity: RawObjectDatabaseState(
            identity=referenced.identity,
            source_fetch_id="source-fetch-a",
            sync_run_id="sync-run-a",
            sync_status="completed",
        ),
        active.identity: RawObjectDatabaseState(
            identity=active.identity,
            sync_run_id="sync-run-b",
            sync_status="running",
        ),
    }
    store = FakeObjectStore(pages)
    repository = FakeRepository(states)
    service = LegalRawObjectReconciliationService(
        repository,
        store,
        clock=lambda: NOW,
    )

    report = service.reconcile(
        reconciliation_run_id="reconcile-20260718"
    )

    assert report.status == "completed"
    assert report.counts == {
        "active_sync": 1,
        "grace_period": 1,
        "manual_disposition_required": 1,
        "orphan_preserved": 1,
        "referenced": 1,
    }
    assert len(store.holds) == 2
    held_identities = {call[0] for call in store.holds}
    assert held_identities == {orphan.identity, old_orphan.identity}
    assert next(
        call for call in store.holds if call[0] == orphan.identity
    )[1] == NOW + timedelta(days=365)
    assert next(
        call for call in store.holds if call[0] == old_orphan.identity
    )[1] == NOW + timedelta(days=500)
    assert all(
        call[2] == "unreferenced_legal_corpus_raw_object"
        for call in store.holds
    )
    assert repository.reports == [report]
    assert not hasattr(store, "delete")


def test_reconciliation_paginates_and_records_partial_cursor():
    first = item("a", age=timedelta(hours=1))
    second = item("b", age=timedelta(hours=1))
    store = FakeObjectStore(
        [
            RawObjectInventoryPage(
                items=(first,),
                next_cursor="cursor-2",
                inventory_complete=False,
            ),
            RawObjectInventoryPage(
                items=(second,),
                next_cursor="cursor-3",
                inventory_complete=False,
            ),
        ]
    )
    repository = FakeRepository()
    service = LegalRawObjectReconciliationService(
        repository,
        store,
        policy=RawObjectReconciliationPolicy(max_pages=2),
        clock=lambda: NOW,
    )

    report = service.reconcile(
        reconciliation_run_id="reconcile-partial",
        start_cursor="cursor-1",
    )

    assert report.status == "partial"
    assert report.start_cursor == "cursor-1"
    assert report.next_cursor == "cursor-3"
    assert report.pages_scanned == 2
    assert [call[1] for call in store.calls] == [
        "cursor-1",
        "cursor-2",
    ]


def test_reconciliation_manifest_is_order_independent():
    first = item("a", age=timedelta(hours=1))
    second = item("b", age=timedelta(hours=1))

    def run(values, run_id):
        repository = FakeRepository()
        service = LegalRawObjectReconciliationService(
            repository,
            FakeObjectStore(
                [
                    RawObjectInventoryPage(
                        items=tuple(values),
                        next_cursor=None,
                        inventory_complete=True,
                    )
                ]
            ),
            clock=lambda: NOW,
        )
        return service.reconcile(reconciliation_run_id=run_id)

    left = run((first, second), "reconcile-left")
    right = run((second, first), "reconcile-right")

    assert (
        left.inventory_manifest_sha256
        == right.inventory_manifest_sha256
    )


def test_reconciliation_rejects_conflicting_duplicate_inventory_identity():
    original = item("a", age=timedelta(hours=1))
    conflicting = replace(original, sha256="f" * 64)
    store = FakeObjectStore(
        [
            RawObjectInventoryPage(
                items=(original,),
                next_cursor="cursor-2",
                inventory_complete=False,
            ),
            RawObjectInventoryPage(
                items=(conflicting,),
                next_cursor=None,
                inventory_complete=True,
            ),
        ]
    )
    service = LegalRawObjectReconciliationService(
        FakeRepository(),
        store,
        clock=lambda: NOW,
    )

    with pytest.raises(
        RawObjectInventoryConflict,
        match="conflicting inventory data",
    ):
        service.reconcile(reconciliation_run_id="reconcile-conflict")


def test_reconciliation_fails_closed_on_invalid_retention_receipt():
    orphan = item("a", age=timedelta(days=3))
    store = FakeObjectStore(
        [
            RawObjectInventoryPage(
                items=(orphan,),
                next_cursor=None,
                inventory_complete=True,
            )
        ]
    )
    store.bad_receipt = True
    repository = FakeRepository()
    service = LegalRawObjectReconciliationService(
        repository,
        store,
        clock=lambda: NOW,
    )

    with pytest.raises(
        RawObjectRetentionError,
        match="required retention hold",
    ):
        service.reconcile(reconciliation_run_id="reconcile-bad-hold")

    assert repository.reports == []


def test_reconciliation_rejects_inventory_cursor_loop():
    store = FakeObjectStore(
        [
            RawObjectInventoryPage(
                items=(),
                next_cursor="cursor-1",
                inventory_complete=False,
            )
        ]
    )
    service = LegalRawObjectReconciliationService(
        FakeRepository(),
        store,
        clock=lambda: NOW,
    )

    with pytest.raises(
        RuntimeError,
        match="cursor did not make progress",
    ):
        service.reconcile(
            reconciliation_run_id="reconcile-loop",
            start_cursor="cursor-1",
        )


def test_reconciliation_rejects_object_outside_legal_raw_prefix():
    with pytest.raises(ValueError, match="raw-object key"):
        RawObjectIdentity(
            object_key="uploads/project-secret.pdf",
            object_version="version-1",
        )
