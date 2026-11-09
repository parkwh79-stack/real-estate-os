"""PostgreSQL audit repository for immutable legal raw-object reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, Sequence

from use_cases.legal_corpus_reconciliation import (
    RawObjectDatabaseState,
    RawObjectIdentity,
    RawObjectInventoryItem,
    RawObjectReconciliationError,
    RawObjectReconciliationReport,
)
from db.legal_sync_database import validate_legal_sync_search_path


_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,240}$")

_LOOKUP_DATABASE_STATES_SQL = """
WITH inventory AS (
    SELECT object_key, object_version, sync_run_id
    FROM jsonb_to_recordset(%s::jsonb) AS input(
        object_key TEXT,
        object_version TEXT,
        sync_run_id TEXT
    )
)
SELECT inventory.object_key,
       inventory.object_version,
       source_fetch.source_fetch_id,
       COALESCE(source_fetch.sync_run_id, inventory.sync_run_id),
       latest_event.to_status
FROM inventory
LEFT JOIN LATERAL (
    SELECT fetch.source_fetch_id, fetch.sync_run_id
    FROM legal_source_fetches AS fetch
    WHERE fetch.raw_object_key = inventory.object_key
      AND fetch.raw_object_version = inventory.object_version
    ORDER BY fetch.fetched_at DESC, fetch.source_fetch_id DESC
    LIMIT 1
) AS source_fetch ON TRUE
LEFT JOIN LATERAL (
    SELECT event.to_status
    FROM legal_sync_run_events AS event
    WHERE event.sync_run_id = COALESCE(
        source_fetch.sync_run_id,
        inventory.sync_run_id
    )
    ORDER BY event.sequence_number DESC
    LIMIT 1
) AS latest_event ON TRUE
ORDER BY inventory.object_key, inventory.object_version
"""

_INSERT_RUN_SQL = """
INSERT INTO legal_raw_object_reconciliation_runs(
    reconciliation_run_id, object_prefix, start_cursor, next_cursor,
    started_at, completed_at, reconciliation_status, pages_scanned,
    objects_scanned, inventory_manifest_sha256, status_counts_json,
    metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING reconciliation_run_id
"""

_SELECT_RUN_SQL = """
SELECT object_prefix, start_cursor, next_cursor, started_at, completed_at,
       reconciliation_status, pages_scanned, objects_scanned,
       inventory_manifest_sha256, status_counts_json
FROM legal_raw_object_reconciliation_runs
WHERE reconciliation_run_id = %s
"""

_INSERT_OBSERVATION_SQL = """
INSERT INTO legal_raw_object_reconciliation_items(
    reconciliation_item_id, reconciliation_run_id, sequence_number,
    raw_object_key, raw_object_version, raw_sha256, raw_size_bytes,
    object_created_at, observation_status, source_fetch_id, sync_run_id,
    sync_status, retention_until, observed_at, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING reconciliation_item_id
"""

_SELECT_OBSERVATION_SQL = """
SELECT reconciliation_run_id, sequence_number, raw_object_key,
       raw_object_version, raw_sha256, raw_size_bytes, object_created_at,
       observation_status, source_fetch_id, sync_run_id, sync_status,
       retention_until, observed_at
FROM legal_raw_object_reconciliation_items
WHERE reconciliation_item_id = %s
"""


class PostgresRawObjectReconciliationRepository:
    """Uses a corpus-maintenance role, never the serving runtime role."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        search_path: str = "realytics,extensions,public,pg_temp",
    ) -> None:
        self._connect = connection_factory
        self._search_path = validate_legal_sync_search_path(search_path)

    def lookup_database_states(
        self,
        items: Sequence[RawObjectInventoryItem],
    ) -> Mapping[RawObjectIdentity, RawObjectDatabaseState]:
        if not items:
            return {}
        payload = [
            {
                "object_key": item.identity.object_key,
                "object_version": item.identity.object_version,
                "sync_run_id": _metadata_id(
                    item.metadata, "sync-run-id"
                ),
            }
            for item in items
        ]
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _LOOKUP_DATABASE_STATES_SQL,
                    (_json(payload),),
                )
                rows = list(cursor.fetchall())
        result: dict[RawObjectIdentity, RawObjectDatabaseState] = {}
        for row in rows:
            identity = RawObjectIdentity(
                object_key=str(row[0]),
                object_version=str(row[1]),
            )
            if identity in result:
                raise RawObjectReconciliationError(
                    "database returned duplicate raw-object state"
                )
            result[identity] = RawObjectDatabaseState(
                identity=identity,
                source_fetch_id=(
                    str(row[2]) if row[2] is not None else None
                ),
                sync_run_id=(
                    str(row[3]) if row[3] is not None else None
                ),
                sync_status=(
                    str(row[4]) if row[4] is not None else None
                ),
            )
        expected = {item.identity for item in items}
        if set(result) != expected:
            raise RawObjectReconciliationError(
                "database did not return state for every inventory object"
            )
        return result

    def record_report(
        self,
        report: RawObjectReconciliationReport,
    ) -> None:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._advisory_lock(
                    cursor, report.reconciliation_run_id
                )
                counts_json = _json(dict(report.counts))
                cursor.execute(
                    _INSERT_RUN_SQL,
                    (
                        report.reconciliation_run_id,
                        report.object_prefix,
                        report.start_cursor,
                        report.next_cursor,
                        report.started_at,
                        report.completed_at,
                        report.status,
                        report.pages_scanned,
                        len(report.observations),
                        report.inventory_manifest_sha256,
                        counts_json,
                        _json(
                            {
                                "deletionPermitted": False,
                                "retentionPolicy": (
                                    "preserve_pending_manual_review"
                                ),
                                "policyVersion": report.policy_version,
                                "gracePeriodSeconds": (
                                    report.grace_period_seconds
                                ),
                                "manualDispositionAgeSeconds": (
                                    report.manual_disposition_age_seconds
                                ),
                                "minimumRetentionSeconds": (
                                    report.minimum_retention_seconds
                                ),
                            }
                        ),
                    ),
                )
                if cursor.fetchone() is None:
                    self._assert_replayed_run(cursor, report)

                for sequence, observation in enumerate(
                    report.observations, start=1
                ):
                    observation_id = _observation_id(
                        report.reconciliation_run_id,
                        observation.identity,
                    )
                    cursor.execute(
                        _INSERT_OBSERVATION_SQL,
                        (
                            observation_id,
                            report.reconciliation_run_id,
                            sequence,
                            observation.identity.object_key,
                            observation.identity.object_version,
                            observation.sha256,
                            observation.size_bytes,
                            observation.created_at,
                            observation.observation_status,
                            observation.source_fetch_id,
                            observation.sync_run_id,
                            observation.sync_status,
                            observation.retention_until,
                            observation.observed_at,
                            _json(
                                {
                                    "deletionPermitted": False,
                                    "disposition": (
                                        "preserve_pending_manual_review"
                                        if observation.observation_status
                                        in {
                                            "orphan_preserved",
                                            "manual_disposition_required",
                                        }
                                        else "none"
                                    ),
                                }
                            ),
                        ),
                    )
                    if cursor.fetchone() is None:
                        self._assert_replayed_observation(
                            cursor,
                            observation_id=observation_id,
                            report=report,
                            sequence=sequence,
                        )

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection = self._connect()
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT set_config('search_path', %s, true)",
                        (self._search_path,),
                    )
                yield connection
        finally:
            close = getattr(connection, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _advisory_lock(cursor: Any, identity: str) -> None:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"legal-raw-reconciliation:{identity}",),
        )

    @staticmethod
    def _assert_replayed_run(
        cursor: Any,
        report: RawObjectReconciliationReport,
    ) -> None:
        cursor.execute(
            _SELECT_RUN_SQL, (report.reconciliation_run_id,)
        )
        existing = cursor.fetchone()
        expected = (
            report.object_prefix,
            report.start_cursor,
            report.next_cursor,
            report.status,
            report.pages_scanned,
            len(report.observations),
        )
        if (
            existing is None
            or (
                existing[0],
                existing[1],
                existing[2],
                existing[5],
                existing[6],
                existing[7],
            )
            != expected
            or not hmac.compare_digest(
                str(existing[8]),
                report.inventory_manifest_sha256,
            )
            or _canonical_json(existing[9])
            != _canonical_json(dict(report.counts))
        ):
            raise RawObjectReconciliationError(
                "reconciliation run id is bound to a different report"
            )

    @staticmethod
    def _assert_replayed_observation(
        cursor: Any,
        *,
        observation_id: str,
        report: RawObjectReconciliationReport,
        sequence: int,
    ) -> None:
        observation = report.observations[sequence - 1]
        cursor.execute(_SELECT_OBSERVATION_SQL, (observation_id,))
        existing = cursor.fetchone()
        expected = (
            report.reconciliation_run_id,
            sequence,
            observation.identity.object_key,
            observation.identity.object_version,
            observation.sha256,
            observation.size_bytes,
            observation.created_at,
            observation.observation_status,
            observation.source_fetch_id,
            observation.sync_run_id,
            observation.sync_status,
        )
        if (
            existing is None
            or tuple(existing[:11]) != expected
        ):
            raise RawObjectReconciliationError(
                "reconciliation item id is bound to different content"
            )


def _observation_id(
    reconciliation_run_id: str,
    identity: RawObjectIdentity,
) -> str:
    payload = json.dumps(
        {
            "reconciliationRunId": reconciliation_run_id,
            "objectKey": identity.object_key,
            "objectVersion": identity.object_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "legal-raw-reconciliation-item:" + hashlib.sha256(
        payload
    ).hexdigest()


def _metadata_id(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized if _SAFE_ID_PATTERN.fullmatch(normalized) else None


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _canonical_json(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
    return _json(value)


__all__ = ["PostgresRawObjectReconciliationRepository"]
