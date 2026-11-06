"""Fail-closed reconciliation for immutable Legal Intelligence raw objects.

The corpus sync path deliberately writes the provider response to immutable
object storage before publishing normalized rows in PostgreSQL.  That ordering
protects source evidence, but a database failure can leave an object that is
not referenced by ``legal_source_fetches``.

This module inventories those objects without ever deleting them.  Objects
that are old enough to be considered orphaned receive an idempotent retention
hold and an append-only reconciliation observation.  A human/operator-owned
retention workflow may later dispose of an object, but this worker cannot.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,240}$")
_ALLOWED_PREFIX = "legal-corpus/raw/"
_ACTIVE_SYNC_STATUSES = frozenset({"pending", "running"})
_OBSERVATION_STATUSES = frozenset(
    {
        "referenced",
        "active_sync",
        "grace_period",
        "orphan_preserved",
        "manual_disposition_required",
    }
)


class RawObjectReconciliationError(RuntimeError):
    """Base error for an incomplete or unsafe reconciliation."""


class RawObjectInventoryConflict(RawObjectReconciliationError):
    """One immutable object identity was reported with conflicting content."""


class RawObjectRetentionError(RawObjectReconciliationError):
    """The object store did not prove the requested retention hold."""


@dataclass(frozen=True, order=True)
class RawObjectIdentity:
    object_key: str
    object_version: str

    def __post_init__(self) -> None:
        _required(self.object_key, "object_key")
        _required(self.object_version, "object_version")
        if (
            not self.object_key.startswith(_ALLOWED_PREFIX)
            or ".." in self.object_key.split("/")
            or any(ord(character) < 32 for character in self.object_key)
        ):
            raise ValueError(
                "object_key must be a safe Legal Intelligence raw-object key"
            )


@dataclass(frozen=True)
class RawObjectInventoryItem:
    identity: RawObjectIdentity
    sha256: str
    size_bytes: int
    created_at: datetime
    retention_until: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        _aware(self.created_at, "created_at")
        if self.retention_until is not None:
            _aware(self.retention_until, "retention_until")


@dataclass(frozen=True)
class RawObjectInventoryPage:
    items: tuple[RawObjectInventoryItem, ...]
    next_cursor: str | None
    inventory_complete: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        if self.inventory_complete and self.next_cursor is not None:
            raise ValueError(
                "a complete inventory page cannot expose a next cursor"
            )
        if not self.inventory_complete:
            _required(self.next_cursor, "next_cursor")


@dataclass(frozen=True)
class RawObjectDatabaseState:
    identity: RawObjectIdentity
    source_fetch_id: str | None = None
    sync_run_id: str | None = None
    sync_status: str | None = None

    @property
    def is_referenced(self) -> bool:
        return self.source_fetch_id is not None

    @property
    def is_active_sync(self) -> bool:
        return self.sync_status in _ACTIVE_SYNC_STATUSES


@dataclass(frozen=True)
class RetentionHoldReceipt:
    identity: RawObjectIdentity
    retained_until: datetime
    immutable: bool

    def __post_init__(self) -> None:
        _aware(self.retained_until, "retained_until")


@dataclass(frozen=True)
class RawObjectReconciliationPolicy:
    grace_period: timedelta = timedelta(hours=24)
    manual_disposition_age: timedelta = timedelta(days=90)
    minimum_retention: timedelta = timedelta(days=365)
    page_size: int = 500
    max_pages: int = 100

    def __post_init__(self) -> None:
        if self.grace_period < timedelta(0):
            raise ValueError("grace_period must not be negative")
        if self.manual_disposition_age < self.grace_period:
            raise ValueError(
                "manual_disposition_age must not be shorter than grace_period"
            )
        if self.minimum_retention <= timedelta(0):
            raise ValueError("minimum_retention must be positive")
        if not 1 <= self.page_size <= 5_000:
            raise ValueError("page_size must be between 1 and 5000")
        if not 1 <= self.max_pages <= 10_000:
            raise ValueError("max_pages must be between 1 and 10000")


@dataclass(frozen=True)
class RawObjectReconciliationObservation:
    identity: RawObjectIdentity
    sha256: str
    size_bytes: int
    created_at: datetime
    observation_status: str
    source_fetch_id: str | None
    sync_run_id: str | None
    sync_status: str | None
    retention_until: datetime | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observation_status not in _OBSERVATION_STATUSES:
            raise ValueError("unsupported reconciliation observation status")
        if not _SHA256_PATTERN.fullmatch(self.sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")
        _aware(self.created_at, "created_at")
        _aware(self.observed_at, "observed_at")
        if self.retention_until is not None:
            _aware(self.retention_until, "retention_until")


@dataclass(frozen=True)
class RawObjectReconciliationReport:
    reconciliation_run_id: str
    object_prefix: str
    started_at: datetime
    completed_at: datetime
    status: str
    start_cursor: str | None
    next_cursor: str | None
    pages_scanned: int
    policy_version: str
    grace_period_seconds: int
    manual_disposition_age_seconds: int
    minimum_retention_seconds: int
    observations: tuple[RawObjectReconciliationObservation, ...]
    inventory_manifest_sha256: str

    def __post_init__(self) -> None:
        _safe_id(self.reconciliation_run_id, "reconciliation_run_id")
        _validate_prefix(self.object_prefix)
        _aware(self.started_at, "started_at")
        _aware(self.completed_at, "completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.status not in {"completed", "partial"}:
            raise ValueError("status must be completed or partial")
        if self.pages_scanned < 1:
            raise ValueError("pages_scanned must be positive")
        _safe_id(self.policy_version, "policy_version")
        if self.grace_period_seconds < 0:
            raise ValueError("grace_period_seconds must not be negative")
        if (
            self.manual_disposition_age_seconds
            < self.grace_period_seconds
        ):
            raise ValueError(
                "manual_disposition_age_seconds must not be shorter than "
                "grace_period_seconds"
            )
        if self.minimum_retention_seconds <= 0:
            raise ValueError(
                "minimum_retention_seconds must be positive"
            )
        object.__setattr__(self, "observations", tuple(self.observations))
        if not _SHA256_PATTERN.fullmatch(self.inventory_manifest_sha256):
            raise ValueError(
                "inventory_manifest_sha256 must be a lowercase SHA-256 digest"
            )
        if self.status == "completed" and self.next_cursor is not None:
            raise ValueError("a completed report cannot have a next cursor")
        if self.status == "partial":
            _required(self.next_cursor, "next_cursor")

    @property
    def counts(self) -> Mapping[str, int]:
        result = {status: 0 for status in sorted(_OBSERVATION_STATUSES)}
        for observation in self.observations:
            result[observation.observation_status] += 1
        return result


class ReconcilableRawObjectStore(Protocol):
    """Inventory and retention boundary; deliberately has no delete method."""

    def list_inventory(
        self,
        *,
        prefix: str,
        cursor: str | None,
        limit: int,
    ) -> RawObjectInventoryPage: ...

    def ensure_retention(
        self,
        *,
        identity: RawObjectIdentity,
        retain_until: datetime,
        reason: str,
        metadata: Mapping[str, str],
    ) -> RetentionHoldReceipt: ...


class RawObjectReconciliationRepository(Protocol):
    """Read reference state and append one immutable reconciliation report."""

    def lookup_database_states(
        self,
        items: Sequence[RawObjectInventoryItem],
    ) -> Mapping[RawObjectIdentity, RawObjectDatabaseState]: ...

    def record_report(
        self,
        report: RawObjectReconciliationReport,
    ) -> None: ...


class LegalRawObjectReconciliationService:
    """Inventory raw objects, retain orphans, and persist an audit report."""

    def __init__(
        self,
        repository: RawObjectReconciliationRepository,
        object_store: ReconcilableRawObjectStore,
        *,
        policy: RawObjectReconciliationPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._object_store = object_store
        self._policy = policy or RawObjectReconciliationPolicy()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def reconcile(
        self,
        *,
        reconciliation_run_id: str,
        object_prefix: str = _ALLOWED_PREFIX,
        start_cursor: str | None = None,
    ) -> RawObjectReconciliationReport:
        _safe_id(reconciliation_run_id, "reconciliation_run_id")
        _validate_prefix(object_prefix)
        started_at = _aware(self._clock(), "clock")
        cursor = start_cursor
        seen_cursors = {start_cursor}
        inventory: dict[RawObjectIdentity, RawObjectInventoryItem] = {}
        observations: list[RawObjectReconciliationObservation] = []
        pages_scanned = 0
        inventory_complete = False

        while pages_scanned < self._policy.max_pages:
            page = self._object_store.list_inventory(
                prefix=object_prefix,
                cursor=cursor,
                limit=self._policy.page_size,
            )
            pages_scanned += 1
            states = self._repository.lookup_database_states(page.items)
            unknown_states = set(states).difference(
                item.identity for item in page.items
            )
            if unknown_states:
                raise RawObjectReconciliationError(
                    "repository returned state for an object outside the page"
                )

            observed_at = _aware(self._clock(), "clock")
            for item in page.items:
                if not item.identity.object_key.startswith(object_prefix):
                    raise RawObjectInventoryConflict(
                        "inventory returned an object outside the requested prefix"
                    )
                existing = inventory.get(item.identity)
                if existing is not None:
                    if (
                        existing.sha256 != item.sha256
                        or existing.size_bytes != item.size_bytes
                        or existing.created_at != item.created_at
                    ):
                        raise RawObjectInventoryConflict(
                            "immutable object identity has conflicting inventory data"
                        )
                    continue
                inventory[item.identity] = item
                state = states.get(
                    item.identity,
                    RawObjectDatabaseState(
                        identity=item.identity,
                        sync_run_id=_metadata_id(
                            item.metadata, "sync-run-id"
                        ),
                    ),
                )
                observation = self._classify_and_retain(
                    item,
                    state=state,
                    observed_at=observed_at,
                    reconciliation_run_id=reconciliation_run_id,
                )
                observations.append(observation)

            if page.inventory_complete:
                inventory_complete = True
                cursor = None
                break
            next_cursor = _required(page.next_cursor, "next_cursor")
            if next_cursor in seen_cursors:
                raise RawObjectReconciliationError(
                    "object inventory cursor did not make progress"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        completed_at = _not_before(
            _aware(self._clock(), "clock"),
            started_at,
            "completed_at",
        )
        ordered_observations = tuple(
            sorted(
                observations,
                key=lambda value: (
                    value.identity.object_key,
                    value.identity.object_version,
                ),
            )
        )
        report = RawObjectReconciliationReport(
            reconciliation_run_id=reconciliation_run_id,
            object_prefix=object_prefix,
            started_at=started_at,
            completed_at=completed_at,
            status="completed" if inventory_complete else "partial",
            start_cursor=start_cursor,
            next_cursor=cursor,
            pages_scanned=pages_scanned,
            policy_version="legal-raw-reconciliation-v1",
            grace_period_seconds=int(
                self._policy.grace_period.total_seconds()
            ),
            manual_disposition_age_seconds=int(
                self._policy.manual_disposition_age.total_seconds()
            ),
            minimum_retention_seconds=int(
                self._policy.minimum_retention.total_seconds()
            ),
            observations=ordered_observations,
            inventory_manifest_sha256=_manifest_sha256(
                ordered_observations
            ),
        )
        self._repository.record_report(report)
        return report

    def _classify_and_retain(
        self,
        item: RawObjectInventoryItem,
        *,
        state: RawObjectDatabaseState,
        observed_at: datetime,
        reconciliation_run_id: str,
    ) -> RawObjectReconciliationObservation:
        if state.identity != item.identity:
            raise RawObjectReconciliationError(
                "database state identity does not match inventory item"
            )
        age = observed_at - item.created_at
        if age < timedelta(0):
            raise RawObjectInventoryConflict(
                "raw object creation time is in the future"
            )

        retention_until: datetime | None = item.retention_until
        if state.is_referenced:
            status = "referenced"
        elif state.is_active_sync:
            status = "active_sync"
        elif age < self._policy.grace_period:
            status = "grace_period"
        else:
            status = (
                "manual_disposition_required"
                if age >= self._policy.manual_disposition_age
                else "orphan_preserved"
            )
            requested_retention = observed_at + self._policy.minimum_retention
            if (
                retention_until is not None
                and retention_until > requested_retention
            ):
                requested_retention = retention_until
            receipt = self._object_store.ensure_retention(
                identity=item.identity,
                retain_until=requested_retention,
                reason="unreferenced_legal_corpus_raw_object",
                metadata={
                    "reconciliation-run-id": reconciliation_run_id,
                    "sha256": item.sha256,
                    "disposition": "preserve_pending_manual_review",
                },
            )
            if (
                receipt.identity != item.identity
                or not receipt.immutable
                or receipt.retained_until < requested_retention
            ):
                raise RawObjectRetentionError(
                    "object store did not attest the required retention hold"
                )
            retention_until = receipt.retained_until

        return RawObjectReconciliationObservation(
            identity=item.identity,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            created_at=item.created_at,
            observation_status=status,
            source_fetch_id=state.source_fetch_id,
            sync_run_id=(
                state.sync_run_id
                or _metadata_id(item.metadata, "sync-run-id")
            ),
            sync_status=state.sync_status,
            retention_until=retention_until,
            observed_at=observed_at,
        )


def _manifest_sha256(
    observations: Sequence[RawObjectReconciliationObservation],
) -> str:
    payload = [
        {
            "objectKey": value.identity.object_key,
            "objectVersion": value.identity.object_version,
            "sha256": value.sha256,
            "sizeBytes": value.size_bytes,
            "createdAt": value.created_at.isoformat(),
        }
        for value in observations
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metadata_id(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized if _SAFE_ID_PATTERN.fullmatch(normalized) else None


def _validate_prefix(value: str) -> None:
    _required(value, "object_prefix")
    if (
        not value.startswith(_ALLOWED_PREFIX)
        or ".." in value.split("/")
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(
            "object_prefix must stay within legal-corpus/raw/"
        )


def _safe_id(value: str, name: str) -> str:
    normalized = _required(value, name)
    if not _SAFE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} contains unsupported characters")
    return normalized


def _required(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _not_before(
    value: datetime,
    lower_bound: datetime,
    name: str,
) -> datetime:
    if value < lower_bound:
        raise ValueError(f"{name} cannot precede the prior event")
    return value


__all__ = [
    "LegalRawObjectReconciliationService",
    "RawObjectDatabaseState",
    "RawObjectIdentity",
    "RawObjectInventoryConflict",
    "RawObjectInventoryItem",
    "RawObjectInventoryPage",
    "RawObjectReconciliationError",
    "RawObjectReconciliationObservation",
    "RawObjectReconciliationPolicy",
    "RawObjectReconciliationReport",
    "RawObjectReconciliationRepository",
    "RawObjectRetentionError",
    "ReconcilableRawObjectStore",
    "RetentionHoldReceipt",
]
