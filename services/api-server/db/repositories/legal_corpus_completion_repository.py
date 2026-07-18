"""Verified PostgreSQL completion attestations for official legal corpora."""

from __future__ import annotations

import hmac
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator, Mapping, Sequence

from db.legal_sync_database import (
    connect_legal_sync_database,
    validate_legal_sync_search_path,
)
from use_cases.legal_corpus_coordinator import (
    LegalCorpusCoordinationError,
    LegalCorpusScopeCompletionAttestation,
)
from use_cases.legal_corpus_sync import _deterministic_id, _stable_hash


_SELECT_CHECKPOINTS_SQL = """
SELECT checkpoint_sequence, requested_scope, sync_run_id,
       checkpoint_before, checkpoint_after, page_hash,
       page_record_count, occurred_at, metadata_json
FROM legal_sync_checkpoint_events
WHERE provider_id = %s
  AND requested_scope_hash = %s
  AND checkpoint_sequence BETWEEN %s AND %s
ORDER BY checkpoint_sequence
"""

_SELECT_FETCHES_SQL = """
SELECT source_fetch_id, provider_id, sync_run_id
FROM legal_source_fetches
WHERE source_fetch_id = ANY(%s)
ORDER BY source_fetch_id
"""

_SELECT_VERSIONS_SQL = """
SELECT legal_resource_version_id, legal_resource_id, normalized_sha256
FROM legal_resource_versions
WHERE legal_resource_version_id = ANY(%s)
ORDER BY legal_resource_version_id
"""

_INSERT_ATTESTATION_SQL = """
INSERT INTO legal_corpus_scope_completion_attestations(
    completion_attestation_id, provider_id, requested_scope,
    requested_scope_hash, first_checkpoint_sequence,
    last_checkpoint_sequence, terminal_checkpoint, page_count,
    record_count, resource_count, page_hashes_json, manifest_sha256,
    started_at, completed_at, attested_by, verification_status,
    metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s,
    %s, %s, %s, 'verified', %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING completion_attestation_id
"""

_SELECT_ATTESTATION_SQL = """
SELECT provider_id, requested_scope, requested_scope_hash,
       first_checkpoint_sequence, last_checkpoint_sequence,
       terminal_checkpoint, page_count, record_count, resource_count,
       page_hashes_json, manifest_sha256, started_at, completed_at,
       attested_by, verification_status
FROM legal_corpus_scope_completion_attestations
WHERE completion_attestation_id = %s
"""


@dataclass(frozen=True)
class CompletionRecordEvidence:
    legal_resource_id: str
    legal_resource_version_id: str
    normalized_sha256: str

    def __post_init__(self) -> None:
        if not self.legal_resource_id.strip():
            raise ValueError("legal_resource_id is required")
        if not self.legal_resource_version_id.strip():
            raise ValueError("legal_resource_version_id is required")
        if not re.fullmatch(r"[0-9a-f]{64}", self.normalized_sha256):
            raise ValueError(
                "normalized_sha256 must be a lowercase SHA-256"
            )


@dataclass(frozen=True)
class CompletionCheckpointEvidence:
    checkpoint_sequence: int
    requested_scope: str
    sync_run_id: str
    source_fetch_id: str
    checkpoint_before: str | None
    checkpoint_after: str
    page_hash: str
    page_record_count: int
    occurred_at: datetime
    records: tuple[CompletionRecordEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))
        if not re.fullmatch(r"[0-9a-f]{64}", self.page_hash):
            raise ValueError("page_hash must be a lowercase SHA-256")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")


@dataclass(frozen=True)
class CompletionFetchEvidence:
    source_fetch_id: str
    provider_id: str
    sync_run_id: str


def verify_scope_completion_evidence(
    attestation: LegalCorpusScopeCompletionAttestation,
    *,
    checkpoints: Sequence[CompletionCheckpointEvidence],
    fetches: Mapping[str, CompletionFetchEvidence],
) -> None:
    """Rebuild the exact ordered manifest before making coverage visible."""

    ordered = tuple(checkpoints)
    expected_scope_hash = _stable_hash(
        {
            "providerId": attestation.provider_id,
            "requestedScope": attestation.requested_scope,
        }
    )
    if not hmac.compare_digest(
        expected_scope_hash, attestation.requested_scope_hash
    ):
        raise LegalCorpusCoordinationError(
            "requested_scope_hash does not match provider and scope"
        )
    if len(ordered) != attestation.page_count:
        raise LegalCorpusCoordinationError(
            "checkpoint count does not match completion page_count"
        )
    expected_sequences = tuple(
        range(
            attestation.first_checkpoint_sequence,
            attestation.last_checkpoint_sequence + 1,
        )
    )
    if (
        len(expected_sequences) != attestation.page_count
        or tuple(item.checkpoint_sequence for item in ordered)
        != expected_sequences
    ):
        raise LegalCorpusCoordinationError(
            "completion checkpoints are missing, duplicated, or unordered"
        )
    if tuple(item.page_hash for item in ordered) != attestation.page_hashes:
        raise LegalCorpusCoordinationError(
            "completion page hashes differ from the checkpoint ledger"
        )
    if any(
        item.requested_scope != attestation.requested_scope
        for item in ordered
    ):
        raise LegalCorpusCoordinationError(
            "checkpoint requested_scope differs from the attestation"
        )
    for previous, current in zip(ordered, ordered[1:]):
        if current.checkpoint_before != previous.checkpoint_after:
            raise LegalCorpusCoordinationError(
                "completion checkpoint chain is discontinuous"
            )
    if ordered[-1].checkpoint_after != attestation.terminal_checkpoint:
        raise LegalCorpusCoordinationError(
            "terminal checkpoint differs from the checkpoint ledger"
        )
    if (
        attestation.started_at > ordered[0].occurred_at
        or attestation.completed_at < ordered[-1].occurred_at
    ):
        raise LegalCorpusCoordinationError(
            "completion time does not enclose checkpoint publication"
        )

    record_count = sum(item.page_record_count for item in ordered)
    if (
        record_count != attestation.record_count
        or any(
            len(item.records) != item.page_record_count
            for item in ordered
        )
    ):
        raise LegalCorpusCoordinationError(
            "checkpoint record counts differ from the completion manifest"
        )

    seen_versions: set[str] = set()
    resources: set[str] = set()
    for checkpoint in ordered:
        fetch = fetches.get(checkpoint.source_fetch_id)
        if (
            fetch is None
            or fetch.provider_id != attestation.provider_id
            or fetch.sync_run_id != checkpoint.sync_run_id
        ):
            raise LegalCorpusCoordinationError(
                "checkpoint source fetch is absent or belongs to another run"
            )
        for record in checkpoint.records:
            if record.legal_resource_version_id in seen_versions:
                raise LegalCorpusCoordinationError(
                    "a legal resource version appears on multiple pages"
                )
            seen_versions.add(record.legal_resource_version_id)
            resources.add(record.legal_resource_id)
    if len(resources) != attestation.resource_count:
        raise LegalCorpusCoordinationError(
            "resource_count differs from the ordered checkpoint manifest"
        )

    manifest_payload = {
        "providerId": attestation.provider_id,
        "requestedScope": attestation.requested_scope,
        "requestedScopeHash": attestation.requested_scope_hash,
        "terminalCheckpoint": attestation.terminal_checkpoint,
        "pages": [
            {
                "checkpointSequence": checkpoint.checkpoint_sequence,
                "checkpointBefore": checkpoint.checkpoint_before,
                "checkpointAfter": checkpoint.checkpoint_after,
                "pageHash": checkpoint.page_hash,
                "syncRunId": checkpoint.sync_run_id,
                "sourceFetchId": checkpoint.source_fetch_id,
                "records": [
                    {
                        "legalResourceId": record.legal_resource_id,
                        "legalResourceVersionId": (
                            record.legal_resource_version_id
                        ),
                        "normalizedSha256": record.normalized_sha256,
                    }
                    for record in checkpoint.records
                ],
            }
            for checkpoint in ordered
        ],
    }
    rebuilt_hash = _stable_hash(manifest_payload)
    if not hmac.compare_digest(
        rebuilt_hash, attestation.manifest_sha256
    ):
        raise LegalCorpusCoordinationError(
            "completion manifest hash does not match checkpoint evidence"
        )
    expected_attestation_id = _deterministic_id(
        "legal-corpus-completion",
        {
            "providerId": attestation.provider_id,
            "requestedScopeHash": attestation.requested_scope_hash,
            "terminalCheckpoint": attestation.terminal_checkpoint,
            "manifestSha256": attestation.manifest_sha256,
        },
    )
    if not hmac.compare_digest(
        expected_attestation_id,
        attestation.completion_attestation_id,
    ):
        raise LegalCorpusCoordinationError(
            "completion attestation id is not evidence-derived"
        )


class PostgresLegalCorpusCompletionRepository:
    """Append a completion only after rebuilding it from immutable evidence."""

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
        *,
        search_path: str = "realytics,extensions,public,pg_temp",
    ) -> None:
        self._connect = (
            connection_factory or connect_legal_sync_database
        )
        self._search_path = validate_legal_sync_search_path(search_path)

    def attest_scope_completion(
        self,
        attestation: LegalCorpusScopeCompletionAttestation,
    ) -> LegalCorpusScopeCompletionAttestation:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(%s, 0))",
                    (
                        "legal-corpus-completion:"
                        f"{attestation.provider_id}:"
                        f"{attestation.requested_scope_hash}",
                    ),
                )
                checkpoints = self._load_checkpoints(
                    cursor, attestation
                )
                fetches = self._load_fetches(cursor, checkpoints)
                verify_scope_completion_evidence(
                    attestation,
                    checkpoints=checkpoints,
                    fetches=fetches,
                )
                cursor.execute(
                    _INSERT_ATTESTATION_SQL,
                    (
                        attestation.completion_attestation_id,
                        attestation.provider_id,
                        attestation.requested_scope,
                        attestation.requested_scope_hash,
                        attestation.first_checkpoint_sequence,
                        attestation.last_checkpoint_sequence,
                        attestation.terminal_checkpoint,
                        attestation.page_count,
                        attestation.record_count,
                        attestation.resource_count,
                        _json(attestation.page_hashes),
                        attestation.manifest_sha256,
                        attestation.started_at,
                        attestation.completed_at,
                        attestation.attested_by,
                        _json(
                            {
                                "verificationPolicy": (
                                    "ordered-checkpoint-manifest/v1"
                                ),
                                "replayed": False,
                            }
                        ),
                    ),
                )
                if cursor.fetchone() is None:
                    self._assert_exact_replay(cursor, attestation)
        return attestation

    @staticmethod
    def _load_checkpoints(
        cursor: Any,
        attestation: LegalCorpusScopeCompletionAttestation,
    ) -> tuple[CompletionCheckpointEvidence, ...]:
        cursor.execute(
            _SELECT_CHECKPOINTS_SQL,
            (
                attestation.provider_id,
                attestation.requested_scope_hash,
                attestation.first_checkpoint_sequence,
                attestation.last_checkpoint_sequence,
            ),
        )
        rows = tuple(cursor.fetchall())
        version_ids: list[str] = []
        parsed: list[tuple[Any, str, tuple[str, ...]]] = []
        for row in rows:
            metadata = _json_object(row[8], "checkpoint metadata")
            source_fetch_id = str(
                metadata.get("sourceFetchId") or ""
            ).strip()
            raw_version_ids = metadata.get(
                "legalResourceVersionIds"
            )
            if (
                not source_fetch_id
                or not isinstance(raw_version_ids, list)
                or not raw_version_ids
                or any(
                    not isinstance(value, str) or not value.strip()
                    for value in raw_version_ids
                )
            ):
                raise LegalCorpusCoordinationError(
                    "checkpoint has no exact source/version manifest"
                )
            page_version_ids = tuple(
                value.strip() for value in raw_version_ids
            )
            if len(page_version_ids) != int(row[6]):
                raise LegalCorpusCoordinationError(
                    "checkpoint version manifest length is inconsistent"
                )
            version_ids.extend(page_version_ids)
            parsed.append((row, source_fetch_id, page_version_ids))

        if len(set(version_ids)) != len(version_ids):
            raise LegalCorpusCoordinationError(
                "checkpoint manifest repeats a legal resource version"
            )
        version_map = PostgresLegalCorpusCompletionRepository._load_versions(
            cursor, version_ids
        )
        checkpoints: list[CompletionCheckpointEvidence] = []
        for row, source_fetch_id, page_version_ids in parsed:
            records = tuple(
                version_map[version_id] for version_id in page_version_ids
            )
            checkpoints.append(
                CompletionCheckpointEvidence(
                    checkpoint_sequence=int(row[0]),
                    requested_scope=str(row[1]),
                    sync_run_id=str(row[2]),
                    checkpoint_before=(
                        str(row[3]) if row[3] is not None else None
                    ),
                    checkpoint_after=str(row[4]),
                    page_hash=str(row[5]),
                    page_record_count=int(row[6]),
                    occurred_at=row[7],
                    source_fetch_id=source_fetch_id,
                    records=records,
                )
            )
        return tuple(checkpoints)

    @staticmethod
    def _load_versions(
        cursor: Any, version_ids: Sequence[str]
    ) -> Mapping[str, CompletionRecordEvidence]:
        if not version_ids:
            return {}
        cursor.execute(_SELECT_VERSIONS_SQL, (list(version_ids),))
        result = {
            str(row[0]): CompletionRecordEvidence(
                legal_resource_version_id=str(row[0]),
                legal_resource_id=str(row[1]),
                normalized_sha256=str(row[2]),
            )
            for row in cursor.fetchall()
        }
        if set(result) != set(version_ids):
            raise LegalCorpusCoordinationError(
                "checkpoint references absent legal resource versions"
            )
        return result

    @staticmethod
    def _load_fetches(
        cursor: Any,
        checkpoints: Sequence[CompletionCheckpointEvidence],
    ) -> Mapping[str, CompletionFetchEvidence]:
        fetch_ids = sorted(
            {checkpoint.source_fetch_id for checkpoint in checkpoints}
        )
        if not fetch_ids:
            return {}
        cursor.execute(_SELECT_FETCHES_SQL, (fetch_ids,))
        result = {
            str(row[0]): CompletionFetchEvidence(
                source_fetch_id=str(row[0]),
                provider_id=str(row[1]),
                sync_run_id=str(row[2]),
            )
            for row in cursor.fetchall()
        }
        if set(result) != set(fetch_ids):
            raise LegalCorpusCoordinationError(
                "checkpoint references absent source fetches"
            )
        return result

    @staticmethod
    def _assert_exact_replay(
        cursor: Any,
        attestation: LegalCorpusScopeCompletionAttestation,
    ) -> None:
        cursor.execute(
            _SELECT_ATTESTATION_SQL,
            (attestation.completion_attestation_id,),
        )
        row = cursor.fetchone()
        expected = (
            attestation.provider_id,
            attestation.requested_scope,
            attestation.requested_scope_hash,
            attestation.first_checkpoint_sequence,
            attestation.last_checkpoint_sequence,
            attestation.terminal_checkpoint,
            attestation.page_count,
            attestation.record_count,
            attestation.resource_count,
        )
        if (
            row is None
            or tuple(row[:9]) != expected
            or tuple(_json_array(row[9], "page hashes"))
            != attestation.page_hashes
            or not hmac.compare_digest(
                str(row[10]), attestation.manifest_sha256
            )
            or row[11] != attestation.started_at
            or row[12] != attestation.completed_at
            or str(row[13]) != attestation.attested_by
            or str(row[14]) != "verified"
        ):
            raise LegalCorpusCoordinationError(
                "completion attestation id is bound to different evidence"
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


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_object(value: Any, label: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise LegalCorpusCoordinationError(
            f"{label} is not valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise LegalCorpusCoordinationError(f"{label} must be an object")
    return parsed


def _json_array(value: Any, label: str) -> Sequence[Any]:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise LegalCorpusCoordinationError(
            f"{label} is not valid JSON"
        ) from exc
    if not isinstance(parsed, (list, tuple)):
        raise LegalCorpusCoordinationError(f"{label} must be an array")
    return parsed


__all__ = [
    "CompletionCheckpointEvidence",
    "CompletionFetchEvidence",
    "CompletionRecordEvidence",
    "PostgresLegalCorpusCompletionRepository",
    "verify_scope_completion_evidence",
]
