from __future__ import annotations

"""Append-only PostgreSQL publisher for the verified Legal Intelligence corpus.

This repository is deliberately *not* a runtime repository.  Its connection
factory must authenticate as the separately provisioned migration/sync-worker
role which owns ingestion privileges for the global legal corpus.  API runtime
roles should only receive the read privileges defined by deployment policy.

Raw provider payloads are never accepted here.  The sync use case must first
write the bytes to immutable object storage and pass the verified receipt in a
``CorpusPublication``.  A publication transaction then appends the source
fetch, resource/version/provision rows, and the completed run event atomically.
No statement updates or deletes an existing corpus row.
"""

import hashlib
import hmac
import json
import os
import re
import threading
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from db.legal_sync_database import (
    connect_legal_sync_database,
    validate_legal_sync_search_path,
)
from legal_intelligence.effective_time import (
    EffectiveLineageDecision,
    EffectiveTimeConflict,
    EffectiveVersionCandidate,
    EffectiveVersionWindow,
    validate_effective_time_lineage,
)
from legal_intelligence.corpus_coverage import (
    OfficialCorpusCoverageIdentity,
)


class LegalCorpusRepositoryError(RuntimeError):
    """Base failure for the migration-owned legal corpus publisher."""


class LegalCorpusImmutableConflict(LegalCorpusRepositoryError):
    """A deterministic identity is already bound to different content."""


class LegalCorpusRunTerminalError(LegalCorpusRepositoryError):
    """A failed/cancelled immutable run cannot be restarted in place."""


class LegalCorpusCheckpointConflict(LegalCorpusRepositoryError):
    """The requested checkpoint is no longer the provider/scope head."""


class LegalCorpusLeaseConflict(LegalCorpusRepositoryError):
    """Another unexpired fenced worker owns the provider/scope."""


class LegalCorpusEffectiveTimeConflict(LegalCorpusImmutableConflict):
    """A candidate violates the official effective-time lineage."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LegalSourceProviderRecord:
    provider_id: str
    provider_key: str
    provider_revision: int
    source_authority: str
    authority_tier: str
    provider_kind: str
    canonical_base_url: str | None
    terms_version: str | None
    adapter_version: str
    configured_by: str
    configured_at: datetime
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class LegalSyncRunRecord:
    sync_run_id: str
    provider: LegalSourceProviderRecord
    sync_kind: str
    requested_scope: str
    requested_scope_hash: str
    checkpoint_before: str | None
    page_hash: str
    page_record_count: int
    idempotency_key: str
    input_hash: str
    requested_by: str
    lease_owner_id: str
    lease_ttl_seconds: int
    requested_at: datetime
    metadata: Mapping[str, Any]
    coverage_contract_id: str | None = None


@dataclass(frozen=True)
class SourceFetchRecord:
    source_fetch_id: str
    provider_id: str
    sync_run_id: str
    transport: str
    endpoint_key: str
    sanitized_source_locator: str
    request_fingerprint: str
    response_status: int | None
    raw_object_key: str
    raw_object_version: str
    raw_sha256: str
    raw_size_bytes: int
    response_content_type: str | None
    provider_etag: str | None
    provider_last_modified: str | None
    fetched_at: datetime
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class LegalResourceRecord:
    legal_resource_id: str
    provider_id: str
    cross_revision_key: str
    resource_type: str
    official_identifier: str
    jurisdiction_code: str
    issuing_authority: str
    identity_hash: str
    first_observed_at: datetime
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class LegalResourceCrosswalkRecord:
    legal_resource_crosswalk_id: str
    legal_resource_id: str
    provider_id: str
    first_observed_source_fetch_id: str
    provider_resource_type: str
    provider_official_identifier: str
    provider_jurisdiction_code: str
    cross_revision_key: str
    observed_at: datetime
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class LegalResourceVersionRecord:
    legal_resource_version_id: str
    legal_resource_id: str
    provider_id: str
    source_fetch_id: str
    official_version_identifier: str
    canonical_title: str
    alternative_titles_text: str
    publication_date: date | None
    promulgation_date: date | None
    effective_from: date | None
    effective_to: date | None
    valid_time_start: date | None
    valid_time_end: date | None
    observed_at: datetime
    fetched_at: datetime
    verified_at: datetime
    verification_status: str
    normalized_sha256: str
    parser_schema_version: str
    declared_predecessor_official_version_identifier: str | None
    effective_continuity_policy: str
    effective_gap_reason: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class LegalProvisionRecord:
    legal_provision_id: str
    legal_resource_version_id: str
    legal_resource_id: str
    parent_provision_id: str | None
    provision_type: str
    provision_identifier: str
    display_path: str
    ordinal_path: tuple[int, ...]
    heading_text: str | None
    body_text: str
    provision_sha256: str
    source_locator: str
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class LegalSyncLease:
    sync_run_id: str
    provider_id: str
    requested_scope: str
    requested_scope_hash: str
    lease_id: str
    fencing_token: int
    event_sequence: int
    owner_id: str
    acquired_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class CorpusRecordPublication:
    resource: LegalResourceRecord
    crosswalk: LegalResourceCrosswalkRecord
    version: LegalResourceVersionRecord
    provisions: tuple[LegalProvisionRecord, ...]


@dataclass(frozen=True)
class CorpusPublication:
    sync_run_id: str
    provider_id: str
    lease: LegalSyncLease
    fetch: SourceFetchRecord
    records: tuple[CorpusRecordPublication, ...]
    page_hash: str
    checkpoint_before: str | None
    checkpoint_after: str | None
    completed_at: datetime

    @property
    def resource(self) -> LegalResourceRecord:
        return self._single_record().resource

    @property
    def version(self) -> LegalResourceVersionRecord:
        return self._single_record().version

    @property
    def provisions(self) -> tuple[LegalProvisionRecord, ...]:
        return self._single_record().provisions

    def _single_record(self) -> CorpusRecordPublication:
        if len(self.records) != 1:
            raise LegalCorpusRepositoryError(
                "single-record compatibility property used for a page"
            )
        return self.records[0]


@dataclass(frozen=True)
class SyncRunState:
    sync_run_id: str
    status: str
    lease: LegalSyncLease | None = None


@dataclass(frozen=True)
class PublishedCorpusVersion:
    sync_run_id: str
    source_fetch_id: str
    legal_resource_id: str
    legal_resource_version_id: str
    version_sequence: int
    supersedes_version_id: str | None
    normalized_sha256: str
    checkpoint_after: str | None
    reused_existing_version: bool
    run_replayed: bool = False
    effective_predecessor_version_id: str | None = None
    effective_lineage_type: str = "initial"
    effective_continuity_status: str = "initial"


@dataclass(frozen=True)
class PublishedCorpusPage:
    sync_run_id: str
    provider_id: str
    requested_scope_hash: str
    source_fetch_id: str
    page_hash: str
    checkpoint_before: str | None
    checkpoint_after: str | None
    checkpoint_sequence: int
    records: tuple[PublishedCorpusVersion, ...]
    run_replayed: bool = False


class LegalCorpusSyncRepository(Protocol):
    """Storage boundary used by the sync application service."""

    def begin_sync(self, run: LegalSyncRunRecord) -> SyncRunState: ...

    def get_completed_publication(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        expected_resource_id: str,
        expected_version_id: str,
    ) -> PublishedCorpusVersion: ...

    def get_completed_page(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        page_hash: str,
        expected_records: Sequence[tuple[str, str]],
    ) -> PublishedCorpusPage: ...

    def renew_lease(
        self,
        lease: LegalSyncLease,
        *,
        occurred_at: datetime,
        lease_ttl_seconds: int,
    ) -> LegalSyncLease: ...

    def publish(self, publication: CorpusPublication) -> PublishedCorpusVersion: ...

    def publish_page(
        self, publication: CorpusPublication
    ) -> PublishedCorpusPage: ...

    def mark_failed(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        error_code: str,
        occurred_at: datetime,
    ) -> None: ...


_INSERT_PROVIDER_SQL = """
INSERT INTO legal_source_providers(
    provider_id, provider_key, provider_revision, source_authority,
    authority_tier, provider_kind, canonical_base_url, terms_version,
    adapter_version, provider_status, supersedes_provider_id, configured_by,
    configured_at, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING provider_id
"""
_SELECT_PROVIDER_SQL = """
SELECT provider_key, provider_revision, source_authority, authority_tier,
       provider_kind, canonical_base_url, terms_version, adapter_version,
       provider_status, supersedes_provider_id
FROM legal_source_providers
WHERE provider_id = %s
"""
_SELECT_PREVIOUS_PROVIDER_SQL = """
SELECT provider_id, provider_revision
FROM legal_source_providers
WHERE provider_key = %s AND provider_revision < %s
ORDER BY provider_revision DESC
LIMIT 1
"""
_SELECT_CURRENT_PROVIDER_REVISION_SQL = """
SELECT provider_id, provider_revision
FROM legal_source_providers
WHERE provider_key = %s
ORDER BY provider_revision DESC
LIMIT 1
"""
_INSERT_RUN_SQL = """
INSERT INTO legal_sync_runs(
    sync_run_id, provider_id, sync_kind, requested_scope,
    requested_scope_hash, checkpoint_before, page_hash, page_record_count,
    idempotency_key, input_hash, requested_by, requested_at,
    coverage_contract_id, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING sync_run_id
"""
_SELECT_RUN_SQL = """
SELECT provider_id, input_hash, idempotency_key, requested_scope,
       requested_scope_hash, checkpoint_before, page_hash, page_record_count,
       coverage_contract_id
FROM legal_sync_runs
WHERE sync_run_id = %s
"""
_SELECT_RUN_SCOPE_SQL = """
SELECT requested_scope, requested_scope_hash, checkpoint_before,
       page_hash, page_record_count
FROM legal_sync_runs
WHERE sync_run_id = %s AND provider_id = %s
"""
_INSERT_EVENT_SQL = """
INSERT INTO legal_sync_run_events(
    sync_event_id, sync_run_id, provider_id, sequence_number, from_status,
    to_status, checkpoint_after, records_seen, records_written, error_code,
    occurred_at, metadata_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING sync_event_id
"""
_SELECT_EVENT_SEQUENCE_SQL = """
SELECT to_status
FROM legal_sync_run_events
WHERE sync_run_id = %s AND sequence_number = %s
"""
_SELECT_LATEST_EVENT_SQL = """
SELECT sequence_number, to_status, checkpoint_after
FROM legal_sync_run_events
WHERE sync_run_id = %s
ORDER BY sequence_number DESC
LIMIT 1
"""
_SELECT_LATEST_CHECKPOINT_SQL = """
SELECT checkpoint_sequence, checkpoint_before, checkpoint_after,
       checkpoint_event_id, sync_run_id, page_hash, page_record_count
FROM legal_sync_checkpoint_events
WHERE provider_id = %s AND requested_scope_hash = %s
ORDER BY checkpoint_sequence DESC
LIMIT 1
"""
_INSERT_CHECKPOINT_SQL = """
INSERT INTO legal_sync_checkpoint_events(
    checkpoint_event_id, provider_id, requested_scope,
    requested_scope_hash, checkpoint_sequence, sync_run_id, lease_id,
    fencing_token, checkpoint_before, checkpoint_after, page_hash,
    page_record_count, occurred_at, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING checkpoint_event_id
"""
_SELECT_CHECKPOINT_EVENT_SQL = """
SELECT provider_id, requested_scope_hash, checkpoint_sequence, sync_run_id,
       lease_id, fencing_token, checkpoint_before, checkpoint_after,
       page_hash, page_record_count
FROM legal_sync_checkpoint_events
WHERE checkpoint_event_id = %s
"""
_SELECT_CHECKPOINT_FOR_RUN_SQL = """
SELECT checkpoint_sequence, checkpoint_before, checkpoint_after,
       page_hash, page_record_count
FROM legal_sync_checkpoint_events
WHERE sync_run_id = %s AND provider_id = %s
LIMIT 1
"""
_SELECT_LATEST_LEASE_SQL = """
SELECT lease_event_id, event_sequence, lease_id, fencing_token, sync_run_id,
       lease_owner_id, event_type, occurred_at, expires_at,
       expires_at > clock_timestamp() AS is_active
FROM legal_sync_scope_lease_events
WHERE provider_id = %s AND requested_scope_hash = %s
ORDER BY event_sequence DESC
LIMIT 1
"""
_INSERT_LEASE_EVENT_SQL = """
INSERT INTO legal_sync_scope_lease_events(
    lease_event_id, provider_id, requested_scope, requested_scope_hash,
    event_sequence, lease_id, fencing_token, sync_run_id, lease_owner_id,
    event_type, occurred_at, expires_at, prior_lease_event_id, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING lease_event_id
"""
_SELECT_LEASE_EVENT_SQL = """
SELECT provider_id, requested_scope_hash, event_sequence, lease_id,
       fencing_token, sync_run_id, lease_owner_id, event_type,
       occurred_at, expires_at, prior_lease_event_id
FROM legal_sync_scope_lease_events
WHERE lease_event_id = %s
"""
_INSERT_FETCH_SQL = """
INSERT INTO legal_source_fetches(
    source_fetch_id, provider_id, sync_run_id, transport, endpoint_key,
    sanitized_source_locator, request_fingerprint, response_status,
    fetch_status, raw_object_key, raw_object_version, raw_sha256,
    raw_size_bytes, response_content_type, provider_etag,
    provider_last_modified, fetched_at, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, 'succeeded', %s, %s, %s, %s, %s,
    %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING source_fetch_id
"""
_SELECT_FETCH_SQL = """
SELECT provider_id, sync_run_id, request_fingerprint, fetch_status,
       raw_object_key, raw_object_version, raw_sha256, raw_size_bytes
FROM legal_source_fetches
WHERE source_fetch_id = %s
"""
_SELECT_FETCH_FOR_RUN_SQL = """
SELECT source_fetch_id
FROM legal_source_fetches
WHERE sync_run_id = %s AND provider_id = %s
ORDER BY fetched_at DESC, source_fetch_id
LIMIT 1
"""
_INSERT_RESOURCE_SQL = """
INSERT INTO legal_resources(
    legal_resource_id, provider_id, resource_type, official_identifier,
    jurisdiction_code, issuing_authority, identity_hash, first_observed_at,
    metadata_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING legal_resource_id
"""
_SELECT_RESOURCE_SQL = """
SELECT provider_id, resource_type, official_identifier, jurisdiction_code,
       issuing_authority, identity_hash, cross_revision_key
FROM legal_resources
WHERE legal_resource_id = %s
"""
_LOCK_RESOURCE_SQL = """
SELECT legal_resource_id
FROM legal_resources
WHERE legal_resource_id = %s
FOR UPDATE
"""
_INSERT_RESOURCE_CROSSWALK_SQL = """
INSERT INTO legal_resource_identity_crosswalks(
    legal_resource_crosswalk_id, legal_resource_id, provider_id,
    first_observed_source_fetch_id, provider_resource_type,
    provider_official_identifier, provider_jurisdiction_code,
    cross_revision_key, observed_at, metadata_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING legal_resource_crosswalk_id
"""
_SELECT_RESOURCE_CROSSWALK_SQL = """
SELECT legal_resource_id, provider_id, provider_resource_type,
       provider_official_identifier, provider_jurisdiction_code,
       cross_revision_key
FROM legal_resource_identity_crosswalks
WHERE legal_resource_crosswalk_id = %s
"""
_SELECT_VERSION_BY_HASH_SQL = """
SELECT version.legal_resource_version_id, version.version_sequence,
       version.supersedes_version_id, version.normalized_sha256,
       lineage.effective_predecessor_version_id, lineage.lineage_type,
       lineage.continuity_status
FROM legal_resource_versions AS version
LEFT JOIN legal_resource_version_lineages AS lineage
  ON lineage.legal_resource_version_id =
     version.legal_resource_version_id
WHERE version.legal_resource_id = %s
  AND version.normalized_sha256 = %s
ORDER BY version.version_sequence DESC
LIMIT 1
"""
_SELECT_LATEST_VERSION_SQL = """
SELECT legal_resource_version_id, version_sequence
FROM legal_resource_versions
WHERE legal_resource_id = %s
ORDER BY version_sequence DESC
LIMIT 1
"""
_SELECT_EFFECTIVE_WINDOWS_SQL = """
SELECT legal_resource_version_id, official_version_identifier,
       version_sequence, effective_from, effective_to,
       valid_time_start, valid_time_end
FROM legal_resource_versions
WHERE legal_resource_id = %s
ORDER BY version_sequence
"""
_INSERT_VERSION_SQL = """
INSERT INTO legal_resource_versions(
    legal_resource_version_id, legal_resource_id, provider_id,
    source_fetch_id, official_version_identifier, version_sequence,
    canonical_title, alternative_titles_text, publication_date,
    promulgation_date, effective_from, effective_to, valid_time_start,
    valid_time_end, observed_at, fetched_at, verified_at,
    verification_status, normalized_sha256, parser_schema_version,
    supersedes_version_id, metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING legal_resource_version_id
"""
_SELECT_VERSION_IDENTITY_SQL = """
SELECT legal_resource_id, provider_id, official_version_identifier,
       version_sequence, normalized_sha256, supersedes_version_id
FROM legal_resource_versions
WHERE legal_resource_version_id = %s
"""
_INSERT_VERSION_LINEAGE_SQL = """
INSERT INTO legal_resource_version_lineages(
    legal_resource_version_lineage_id, legal_resource_id,
    legal_resource_version_id, effective_predecessor_version_id,
    lineage_type, continuity_status, validation_policy,
    predecessor_effective_to, successor_effective_from,
    derived_predecessor_effective_to, gap_days, validated_at,
    metadata_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING legal_resource_version_lineage_id
"""
_SELECT_VERSION_LINEAGE_SQL = """
SELECT legal_resource_id, legal_resource_version_id,
       effective_predecessor_version_id, lineage_type,
       continuity_status, validation_policy,
       predecessor_effective_to, successor_effective_from,
       derived_predecessor_effective_to, gap_days
FROM legal_resource_version_lineages
WHERE legal_resource_version_lineage_id = %s
"""
_INSERT_PROVISION_SQL = """
INSERT INTO legal_provisions(
    legal_provision_id, legal_resource_version_id, legal_resource_id,
    parent_provision_id, provision_type, provision_identifier, display_path,
    ordinal_path, heading_text, body_text, provision_sha256, source_locator,
    metadata_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING legal_provision_id
"""
_SELECT_PROVISION_SQL = """
SELECT legal_resource_version_id, legal_resource_id, parent_provision_id,
       provision_type, provision_identifier, display_path, ordinal_path,
       provision_sha256
FROM legal_provisions
WHERE legal_provision_id = %s
"""
_SELECT_VERSION_PROVISIONS_SQL = """
SELECT provision_identifier, provision_sha256
FROM legal_provisions
WHERE legal_resource_version_id = %s
ORDER BY provision_identifier
"""
_SELECT_COMPLETED_RESULT_SQL = """
SELECT version.legal_resource_version_id, version.version_sequence,
       version.supersedes_version_id, version.normalized_sha256,
       fetch.source_fetch_id, version.source_fetch_id,
       lineage.effective_predecessor_version_id, lineage.lineage_type,
       lineage.continuity_status
FROM legal_resource_versions AS version
JOIN legal_source_fetches AS fetch
  ON fetch.source_fetch_id = %s
JOIN legal_resource_version_lineages AS lineage
  ON lineage.legal_resource_version_id =
     version.legal_resource_version_id
WHERE version.legal_resource_id = %s
  AND version.legal_resource_version_id = %s
  AND fetch.sync_run_id = %s
  AND fetch.provider_id = %s
"""


class PostgresLegalCorpusSyncRepository:
    """Migration/sync-worker repository for the global immutable corpus.

    The supplied connection factory must never be the ordinary API runtime
    connection.  It needs INSERT/SELECT on migration ``0008`` global tables and
    no tenant impersonation.  Every operation explicitly selects the private
    schema search path and uses append-only statements.
    """

    def __init__(
        self,
        connection_factory: Callable[[], Any] | None = None,
        *,
        search_path: str | None = None,
    ) -> None:
        self._connect = (
            connection_factory or connect_legal_sync_database
        )
        self._search_path = validate_legal_sync_search_path(
            search_path
            or os.environ.get(
                "REALYTICS_LEGAL_SYNC_SEARCH_PATH",
                "realytics,extensions,public,pg_temp",
            )
        )

    def begin_sync(self, run: LegalSyncRunRecord) -> SyncRunState:
        _validate_global_provider(run.provider)
        _validate_sync_run_envelope(run)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._advisory_lock(cursor, run.sync_run_id)
                self._advisory_lock(
                    cursor,
                    f"legal-provider-lineage:{run.provider.provider_key}",
                )
                self._advisory_lock(
                    cursor,
                    _scope_lock_key(
                        run.provider.provider_id,
                        run.requested_scope_hash,
                    ),
                )
                self._insert_provider(cursor, run.provider)
                cursor.execute(
                    _INSERT_RUN_SQL,
                    (
                        run.sync_run_id,
                        run.provider.provider_id,
                        run.sync_kind,
                        run.requested_scope,
                        run.requested_scope_hash,
                        run.checkpoint_before,
                        run.page_hash,
                        run.page_record_count,
                        run.idempotency_key,
                        run.input_hash,
                        run.requested_by,
                        run.requested_at,
                        run.coverage_contract_id,
                        _json(run.metadata),
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(_SELECT_RUN_SQL, (run.sync_run_id,))
                    existing = cursor.fetchone()
                    if (
                        existing is None
                        or str(existing[0]) != run.provider.provider_id
                        or not hmac.compare_digest(
                            str(existing[1]), run.input_hash
                        )
                        or str(existing[2]) != run.idempotency_key
                        or str(existing[3]) != run.requested_scope
                        or str(existing[4]) != run.requested_scope_hash
                        or not _same_checkpoint(
                            existing[5], run.checkpoint_before
                        )
                        or str(existing[6]) != run.page_hash
                        or int(existing[7]) != run.page_record_count
                        or (
                            str(existing[8])
                            if existing[8] is not None
                            else None
                        )
                        != run.coverage_contract_id
                    ):
                        raise LegalCorpusImmutableConflict(
                            "sync run identity is bound to different input"
                        )

                latest = self._latest_event(cursor, run.sync_run_id)
                if latest is not None and latest[1] == "completed":
                    return SyncRunState(run.sync_run_id, "completed", None)

                self._assert_checkpoint_head(cursor, run)
                lease = self._acquire_or_resume_lease(cursor, run)
                if latest is None:
                    self._append_event(
                        cursor,
                        sync_run_id=run.sync_run_id,
                        provider_id=run.provider.provider_id,
                        sequence_number=1,
                        from_status=None,
                        to_status="pending",
                        occurred_at=run.requested_at,
                    )
                    latest = (1, "pending", None)
                if latest[1] == "pending":
                    self._append_event(
                        cursor,
                        sync_run_id=run.sync_run_id,
                        provider_id=run.provider.provider_id,
                        sequence_number=int(latest[0]) + 1,
                        from_status="pending",
                        to_status="running",
                        occurred_at=run.requested_at,
                    )
                    return SyncRunState(
                        run.sync_run_id, "running", lease
                    )
                if latest[1] == "running":
                    return SyncRunState(
                        run.sync_run_id, "running", lease
                    )
                if latest[1] == "failed":
                    self._append_event(
                        cursor,
                        sync_run_id=run.sync_run_id,
                        provider_id=run.provider.provider_id,
                        sequence_number=int(latest[0]) + 1,
                        from_status="failed",
                        to_status="running",
                        occurred_at=run.requested_at,
                        metadata={"retry": True},
                    )
                    return SyncRunState(
                        run.sync_run_id, "running", lease
                    )
                raise LegalCorpusRunTerminalError(
                    f"sync run is terminal ({latest[1]}); use a new idempotency key"
                )

    def _assert_checkpoint_head(
        self, cursor: Any, run: LegalSyncRunRecord
    ) -> None:
        cursor.execute(
            _SELECT_LATEST_CHECKPOINT_SQL,
            (
                run.provider.provider_id,
                run.requested_scope_hash,
            ),
        )
        head = cursor.fetchone()
        if head is not None and not _same_checkpoint(
            head[2], run.checkpoint_before
        ):
            raise LegalCorpusCheckpointConflict(
                "checkpoint_before is stale for provider/requested_scope "
                f"(head sequence {int(head[0])})"
            )

    def _acquire_or_resume_lease(
        self, cursor: Any, run: LegalSyncRunRecord
    ) -> LegalSyncLease:
        cursor.execute(
            _SELECT_LATEST_LEASE_SQL,
            (
                run.provider.provider_id,
                run.requested_scope_hash,
            ),
        )
        latest = cursor.fetchone()
        if (
            latest is not None
            and str(latest[6]) in {"acquired", "renewed"}
            and bool(latest[9])
        ):
            if (
                str(latest[4]) != run.sync_run_id
                or str(latest[5]) != run.lease_owner_id
            ):
                raise LegalCorpusLeaseConflict(
                    "provider/requested_scope is held by another fenced worker"
                )
            return _lease_from_row(
                latest,
                provider_id=run.provider.provider_id,
                requested_scope=run.requested_scope,
                requested_scope_hash=run.requested_scope_hash,
            )

        prior_event_id = str(latest[0]) if latest is not None else None
        event_sequence = int(latest[1]) + 1 if latest is not None else 1
        fencing_token = int(latest[3]) + 1 if latest is not None else 1
        lease_id = _deterministic_id(
            "legal-sync-lease",
            {
                "syncRunId": run.sync_run_id,
                "fencingToken": fencing_token,
            },
        )
        expires_at = run.requested_at + timedelta(
            seconds=run.lease_ttl_seconds
        )
        lease = LegalSyncLease(
            sync_run_id=run.sync_run_id,
            provider_id=run.provider.provider_id,
            requested_scope=run.requested_scope,
            requested_scope_hash=run.requested_scope_hash,
            lease_id=lease_id,
            fencing_token=fencing_token,
            event_sequence=event_sequence,
            owner_id=run.lease_owner_id,
            acquired_at=run.requested_at,
            expires_at=expires_at,
        )
        self._append_lease_event(
            cursor,
            lease=lease,
            event_type="acquired",
            occurred_at=run.requested_at,
            expires_at=expires_at,
            prior_event_id=prior_event_id,
        )
        return lease

    def get_completed_publication(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        expected_resource_id: str,
        expected_version_id: str,
    ) -> PublishedCorpusVersion:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._advisory_lock(cursor, sync_run_id)
                cursor.execute(
                    _SELECT_RUN_SCOPE_SQL, (sync_run_id, provider_id)
                )
                run_scope = cursor.fetchone()
                if run_scope is None:
                    raise LegalCorpusImmutableConflict(
                        "completed run is not owned by the expected provider"
                    )
                self._advisory_lock(
                    cursor,
                    _scope_lock_key(provider_id, str(run_scope[1])),
                )
                latest = self._latest_event(cursor, sync_run_id)
                if latest is None or latest[1] != "completed":
                    raise LegalCorpusRepositoryError(
                        "sync run has no completed publication"
                    )
                cursor.execute(_SELECT_RUN_SQL, (sync_run_id,))
                run = cursor.fetchone()
                if run is None or str(run[0]) != provider_id:
                    raise LegalCorpusImmutableConflict(
                        "completed run is not owned by the expected provider"
                    )
                cursor.execute(
                    _SELECT_FETCH_FOR_RUN_SQL,
                    (sync_run_id, provider_id),
                )
                fetch = cursor.fetchone()
                if fetch is None:
                    raise LegalCorpusImmutableConflict(
                        "completed run has no source fetch"
                    )
                return self._load_completed_result(
                    cursor,
                    sync_run_id=sync_run_id,
                    provider_id=provider_id,
                    expected_fetch_id=str(fetch[0]),
                    expected_resource_id=expected_resource_id,
                    expected_version_id=expected_version_id,
                    checkpoint_after=latest[2],
                    run_replayed=True,
                )

    def get_completed_page(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        page_hash: str,
        expected_records: Sequence[tuple[str, str]],
    ) -> PublishedCorpusPage:
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._advisory_lock(cursor, sync_run_id)
                latest = self._latest_event(cursor, sync_run_id)
                if latest is None or latest[1] != "completed":
                    raise LegalCorpusRepositoryError(
                        "sync run has no completed page"
                    )
                cursor.execute(_SELECT_RUN_SQL, (sync_run_id,))
                run = cursor.fetchone()
                if (
                    run is None
                    or str(run[0]) != provider_id
                    or str(run[6]) != page_hash
                    or int(run[7]) != len(expected_records)
                ):
                    raise LegalCorpusImmutableConflict(
                        "completed page differs from immutable run envelope"
                    )
                cursor.execute(
                    _SELECT_FETCH_FOR_RUN_SQL,
                    (sync_run_id, provider_id),
                )
                fetch = cursor.fetchone()
                cursor.execute(
                    _SELECT_CHECKPOINT_FOR_RUN_SQL,
                    (sync_run_id, provider_id),
                )
                checkpoint = cursor.fetchone()
                if fetch is None or checkpoint is None:
                    raise LegalCorpusImmutableConflict(
                        "completed page lacks fetch or checkpoint evidence"
                    )
                if (
                    str(checkpoint[3]) != page_hash
                    or int(checkpoint[4]) != len(expected_records)
                ):
                    raise LegalCorpusImmutableConflict(
                        "completed checkpoint page attestation differs"
                    )
                records = tuple(
                    self._load_completed_result(
                        cursor,
                        sync_run_id=sync_run_id,
                        provider_id=provider_id,
                        expected_fetch_id=str(fetch[0]),
                        expected_resource_id=resource_id,
                        expected_version_id=version_id,
                        checkpoint_after=checkpoint[2],
                        run_replayed=True,
                    )
                    for resource_id, version_id in expected_records
                )
                return PublishedCorpusPage(
                    sync_run_id=sync_run_id,
                    provider_id=provider_id,
                    requested_scope_hash=str(run[4]),
                    source_fetch_id=str(fetch[0]),
                    page_hash=page_hash,
                    checkpoint_before=checkpoint[1],
                    checkpoint_after=checkpoint[2],
                    checkpoint_sequence=int(checkpoint[0]),
                    records=records,
                    run_replayed=True,
                )

    def renew_lease(
        self,
        lease: LegalSyncLease,
        *,
        occurred_at: datetime,
        lease_ttl_seconds: int,
    ) -> LegalSyncLease:
        if lease_ttl_seconds < 30 or lease_ttl_seconds > 3600:
            raise LegalCorpusRepositoryError(
                "lease_ttl_seconds must be between 30 and 3600"
            )
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._advisory_lock(cursor, lease.sync_run_id)
                self._advisory_lock(
                    cursor,
                    _scope_lock_key(
                        lease.provider_id,
                        lease.requested_scope_hash,
                    ),
                )
                active = self._assert_active_lease(cursor, lease)
                expires_at = max(
                    active.expires_at,
                    occurred_at
                    + timedelta(seconds=lease_ttl_seconds),
                )
                renewed = LegalSyncLease(
                    sync_run_id=lease.sync_run_id,
                    provider_id=lease.provider_id,
                    requested_scope=lease.requested_scope,
                    requested_scope_hash=lease.requested_scope_hash,
                    lease_id=lease.lease_id,
                    fencing_token=lease.fencing_token,
                    event_sequence=active.event_sequence + 1,
                    owner_id=lease.owner_id,
                    acquired_at=lease.acquired_at,
                    expires_at=expires_at,
                )
                self._append_lease_event(
                    cursor,
                    lease=renewed,
                    event_type="renewed",
                    occurred_at=occurred_at,
                    expires_at=expires_at,
                    prior_event_id=self._latest_lease_event_id(
                        cursor, active
                    ),
                )
                return renewed

    def publish(self, publication: CorpusPublication) -> PublishedCorpusVersion:
        if len(publication.records) != 1:
            raise LegalCorpusRepositoryError(
                "publish() accepts one record; use publish_page() for a page"
            )
        return self.publish_page(publication).records[0]

    def publish_page(
        self, publication: CorpusPublication
    ) -> PublishedCorpusPage:
        fetch = publication.fetch
        verifications = _validated_publication_verification(publication)
        _validate_page_publication_envelope(publication)
        calculated_page_hash = _publication_page_hash(
            publication, verifications
        )
        if not hmac.compare_digest(
            publication.page_hash, calculated_page_hash
        ):
            raise LegalCorpusImmutableConflict(
                "page_hash does not attest the normalized record page"
            )
        expected_records = tuple(
            (
                item.resource.legal_resource_id,
                item.version.legal_resource_version_id,
            )
            for item in publication.records
        )

        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._advisory_lock(cursor, publication.sync_run_id)
                cursor.execute(
                    _SELECT_RUN_SCOPE_SQL,
                    (publication.sync_run_id, publication.provider_id),
                )
                run_scope = cursor.fetchone()
                if run_scope is None:
                    raise LegalCorpusRepositoryError("sync run is absent")
                if (
                    str(run_scope[0]) != publication.lease.requested_scope
                    or str(run_scope[1])
                    != publication.lease.requested_scope_hash
                    or not _same_checkpoint(
                        run_scope[2], publication.checkpoint_before
                    )
                    or str(run_scope[3]) != publication.page_hash
                    or int(run_scope[4]) != len(publication.records)
                ):
                    raise LegalCorpusImmutableConflict(
                        "page publication differs from immutable run envelope"
                    )
                self._advisory_lock(
                    cursor,
                    _scope_lock_key(
                        publication.provider_id,
                        publication.lease.requested_scope_hash,
                    ),
                )
                latest = self._latest_event(cursor, publication.sync_run_id)
                if latest is None:
                    raise LegalCorpusRepositoryError("sync run is absent")
                if latest[1] == "completed":
                    return self._load_completed_page(
                        cursor,
                        sync_run_id=publication.sync_run_id,
                        provider_id=publication.provider_id,
                        requested_scope_hash=(
                            publication.lease.requested_scope_hash
                        ),
                        page_hash=publication.page_hash,
                        expected_records=expected_records,
                        run_replayed=True,
                    )
                if latest[1] != "running":
                    raise LegalCorpusRunTerminalError(
                        f"cannot publish a {latest[1]} sync run"
                    )

                active_lease = self._assert_active_lease(
                    cursor, publication.lease
                )
                checkpoint_head = self._assert_publication_checkpoint(
                    cursor, publication
                )
                cursor.execute(
                    _SELECT_PROVIDER_SQL, (publication.provider_id,)
                )
                provider = cursor.fetchone()
                if (
                    provider is None
                    or any(
                        str(provider[2])
                        != str(verification["sourceAuthority"])
                        for verification in verifications
                    )
                ):
                    raise LegalCorpusImmutableConflict(
                        "source verification authority does not match provider"
                    )
                self._insert_fetch(cursor, fetch)
                published_by_identity: dict[
                    tuple[str, str], PublishedCorpusVersion
                ] = {}
                records_written = 0
                for item in sorted(
                    publication.records,
                    key=lambda candidate: (
                        candidate.resource.legal_resource_id,
                        candidate.version.legal_resource_version_id,
                    ),
                ):
                    result, written = self._publish_record(
                        cursor,
                        publication=publication,
                        item=item,
                    )
                    published_by_identity[
                        (
                            item.resource.legal_resource_id,
                            item.version.legal_resource_version_id,
                        )
                    ] = result
                    records_written += written

                checkpoint_sequence = self._append_checkpoint(
                    cursor,
                    publication=publication,
                    head=checkpoint_head,
                )
                self._append_event(
                    cursor,
                    sync_run_id=publication.sync_run_id,
                    provider_id=publication.provider_id,
                    sequence_number=int(latest[0]) + 1,
                    from_status="running",
                    to_status="completed",
                    checkpoint_after=publication.checkpoint_after,
                    records_seen=len(publication.records),
                    records_written=records_written,
                    occurred_at=publication.completed_at,
                    metadata={
                        "sourceFetchId": fetch.source_fetch_id,
                        "pageHash": publication.page_hash,
                        "pageRecordCount": len(publication.records),
                        "checkpointSequence": checkpoint_sequence,
                        "legalResourceVersionIds": [
                            item.version.legal_resource_version_id
                            for item in publication.records
                        ],
                    },
                )
                released_lease = LegalSyncLease(
                    sync_run_id=active_lease.sync_run_id,
                    provider_id=active_lease.provider_id,
                    requested_scope=active_lease.requested_scope,
                    requested_scope_hash=(
                        active_lease.requested_scope_hash
                    ),
                    lease_id=active_lease.lease_id,
                    fencing_token=active_lease.fencing_token,
                    event_sequence=active_lease.event_sequence + 1,
                    owner_id=active_lease.owner_id,
                    acquired_at=active_lease.acquired_at,
                    expires_at=publication.completed_at,
                )
                self._append_lease_event(
                    cursor,
                    lease=released_lease,
                    event_type="released",
                    occurred_at=publication.completed_at,
                    expires_at=publication.completed_at,
                    prior_event_id=self._latest_lease_event_id(
                        cursor, active_lease
                    ),
                )
                records = tuple(
                    published_by_identity[identity]
                    for identity in expected_records
                )
                return PublishedCorpusPage(
                    sync_run_id=publication.sync_run_id,
                    provider_id=publication.provider_id,
                    requested_scope_hash=(
                        publication.lease.requested_scope_hash
                    ),
                    source_fetch_id=fetch.source_fetch_id,
                    page_hash=publication.page_hash,
                    checkpoint_before=publication.checkpoint_before,
                    checkpoint_after=publication.checkpoint_after,
                    checkpoint_sequence=checkpoint_sequence,
                    records=records,
                )

    def _assert_active_lease(
        self, cursor: Any, lease: LegalSyncLease
    ) -> LegalSyncLease:
        cursor.execute(
            _SELECT_LATEST_LEASE_SQL,
            (lease.provider_id, lease.requested_scope_hash),
        )
        latest = cursor.fetchone()
        if (
            latest is None
            or str(latest[2]) != lease.lease_id
            or int(latest[3]) != lease.fencing_token
            or str(latest[4]) != lease.sync_run_id
            or str(latest[5]) != lease.owner_id
            or str(latest[6]) not in {"acquired", "renewed"}
            or not bool(latest[9])
        ):
            raise LegalCorpusLeaseConflict(
                "sync lease is expired, released, or fenced by another worker"
            )
        return _lease_from_row(
            latest,
            provider_id=lease.provider_id,
            requested_scope=lease.requested_scope,
            requested_scope_hash=lease.requested_scope_hash,
        )

    @staticmethod
    def _latest_lease_event_id(
        cursor: Any, lease: LegalSyncLease
    ) -> str:
        cursor.execute(
            _SELECT_LATEST_LEASE_SQL,
            (lease.provider_id, lease.requested_scope_hash),
        )
        latest = cursor.fetchone()
        if latest is None:
            raise LegalCorpusLeaseConflict("sync lease event is absent")
        return str(latest[0])

    @staticmethod
    def _assert_publication_checkpoint(
        cursor: Any, publication: CorpusPublication
    ) -> Any:
        cursor.execute(
            _SELECT_LATEST_CHECKPOINT_SQL,
            (
                publication.provider_id,
                publication.lease.requested_scope_hash,
            ),
        )
        head = cursor.fetchone()
        if head is not None and not _same_checkpoint(
            head[2], publication.checkpoint_before
        ):
            raise LegalCorpusCheckpointConflict(
                "checkpoint head changed after lease acquisition"
            )
        return head

    def _publish_record(
        self,
        cursor: Any,
        *,
        publication: CorpusPublication,
        item: CorpusRecordPublication,
    ) -> tuple[PublishedCorpusVersion, int]:
        resource = item.resource
        version = item.version
        self._advisory_lock(cursor, resource.legal_resource_id)
        self._insert_resource(cursor, resource)
        cursor.execute(_LOCK_RESOURCE_SQL, (resource.legal_resource_id,))
        if cursor.fetchone() is None:
            raise LegalCorpusRepositoryError(
                "resource lock target disappeared"
            )
        crosswalk_inserted = self._insert_resource_crosswalk(
            cursor, item.crosswalk
        )
        cursor.execute(
            _SELECT_VERSION_BY_HASH_SQL,
            (resource.legal_resource_id, version.normalized_sha256),
        )
        existing = cursor.fetchone()
        reused = existing is not None
        if existing is not None:
            if str(existing[0]) != version.legal_resource_version_id:
                raise LegalCorpusImmutableConflict(
                    "normalized content is bound to another version identity"
                )
            version_sequence = int(existing[1])
            supersedes_version_id = existing[2]
            if existing[5] is None or existing[6] is None:
                raise LegalCorpusImmutableConflict(
                    "existing legal version has no effective-time lineage"
                )
            effective_predecessor_version_id = (
                str(existing[4]) if existing[4] is not None else None
            )
            effective_lineage_type = str(existing[5])
            effective_continuity_status = str(existing[6])
            self._compare_provision_set(
                cursor,
                version.legal_resource_version_id,
                item.provisions,
            )
        else:
            cursor.execute(
                _SELECT_EFFECTIVE_WINDOWS_SQL,
                (resource.legal_resource_id,),
            )
            windows = tuple(
                EffectiveVersionWindow(
                    legal_resource_version_id=str(row[0]),
                    official_version_identifier=str(row[1]),
                    version_sequence=int(row[2]),
                    effective_from=row[3],
                    effective_to=row[4],
                    valid_time_start=row[5],
                    valid_time_end=row[6],
                )
                for row in cursor.fetchall()
            )
            try:
                lineage = validate_effective_time_lineage(
                    windows,
                    EffectiveVersionCandidate(
                        legal_resource_version_id=(
                            version.legal_resource_version_id
                        ),
                        official_version_identifier=(
                            version.official_version_identifier
                        ),
                        effective_from=version.effective_from,
                        effective_to=version.effective_to,
                        valid_time_start=version.valid_time_start,
                        valid_time_end=version.valid_time_end,
                        declared_predecessor_official_version_identifier=(
                            version.declared_predecessor_official_version_identifier
                        ),
                        continuity_policy=(
                            version.effective_continuity_policy
                        ),
                        gap_reason=version.effective_gap_reason,
                    ),
                )
            except EffectiveTimeConflict as exc:
                raise LegalCorpusEffectiveTimeConflict(
                    exc.code, str(exc)
                ) from exc
            cursor.execute(
                _SELECT_LATEST_VERSION_SQL,
                (resource.legal_resource_id,),
            )
            previous = cursor.fetchone()
            supersedes_version_id = previous[0] if previous else None
            version_sequence = int(previous[1]) + 1 if previous else 1
            self._insert_version(
                cursor,
                version,
                version_sequence=version_sequence,
                supersedes_version_id=supersedes_version_id,
            )
            self._insert_version_lineage(
                cursor,
                version=version,
                decision=lineage,
                validated_at=publication.completed_at,
            )
            effective_predecessor_version_id = (
                lineage.predecessor_version_id
            )
            effective_lineage_type = lineage.lineage_type
            effective_continuity_status = lineage.continuity_status
            for provision in item.provisions:
                self._insert_provision(cursor, provision)
        return (
            PublishedCorpusVersion(
                sync_run_id=publication.sync_run_id,
                source_fetch_id=publication.fetch.source_fetch_id,
                legal_resource_id=resource.legal_resource_id,
                legal_resource_version_id=version.legal_resource_version_id,
                version_sequence=version_sequence,
                supersedes_version_id=(
                    str(supersedes_version_id)
                    if supersedes_version_id is not None
                    else None
                ),
                normalized_sha256=version.normalized_sha256,
                checkpoint_after=publication.checkpoint_after,
                reused_existing_version=reused,
                effective_predecessor_version_id=(
                    effective_predecessor_version_id
                ),
                effective_lineage_type=effective_lineage_type,
                effective_continuity_status=(
                    effective_continuity_status
                ),
            ),
            (
                (1 if crosswalk_inserted else 0)
                if reused
                else 2
                + len(item.provisions)
                + (1 if crosswalk_inserted else 0)
            ),
        )

    def _append_checkpoint(
        self,
        cursor: Any,
        *,
        publication: CorpusPublication,
        head: Any,
    ) -> int:
        checkpoint_sequence = int(head[0]) + 1 if head is not None else 1
        event_id = _deterministic_id(
            "legal-sync-checkpoint",
            {
                "syncRunId": publication.sync_run_id,
                "pageHash": publication.page_hash,
            },
        )
        cursor.execute(
            _INSERT_CHECKPOINT_SQL,
            (
                event_id,
                publication.provider_id,
                publication.lease.requested_scope,
                publication.lease.requested_scope_hash,
                checkpoint_sequence,
                publication.sync_run_id,
                publication.lease.lease_id,
                publication.lease.fencing_token,
                publication.checkpoint_before,
                publication.checkpoint_after,
                publication.page_hash,
                len(publication.records),
                publication.completed_at,
                _json(
                    {
                        "sourceFetchId": publication.fetch.source_fetch_id,
                        "coverageIdentity": publication.fetch.metadata.get(
                            "coverageIdentity"
                        ),
                        "legalResourceVersionIds": [
                            item.version.legal_resource_version_id
                            for item in publication.records
                        ],
                    }
                ),
            ),
        )
        if cursor.fetchone() is None:
            cursor.execute(_SELECT_CHECKPOINT_EVENT_SQL, (event_id,))
            existing = cursor.fetchone()
            expected = (
                publication.provider_id,
                publication.lease.requested_scope_hash,
                checkpoint_sequence,
                publication.sync_run_id,
                publication.lease.lease_id,
                publication.lease.fencing_token,
                publication.checkpoint_before,
                publication.checkpoint_after,
                publication.page_hash,
                len(publication.records),
            )
            if existing is None or tuple(existing) != expected:
                raise LegalCorpusImmutableConflict(
                    "checkpoint event differs from immutable page commit"
                )
        return checkpoint_sequence

    def _load_completed_page(
        self,
        cursor: Any,
        *,
        sync_run_id: str,
        provider_id: str,
        requested_scope_hash: str,
        page_hash: str,
        expected_records: Sequence[tuple[str, str]],
        run_replayed: bool,
    ) -> PublishedCorpusPage:
        cursor.execute(
            _SELECT_FETCH_FOR_RUN_SQL, (sync_run_id, provider_id)
        )
        fetch = cursor.fetchone()
        cursor.execute(
            _SELECT_CHECKPOINT_FOR_RUN_SQL, (sync_run_id, provider_id)
        )
        checkpoint = cursor.fetchone()
        if (
            fetch is None
            or checkpoint is None
            or str(checkpoint[3]) != page_hash
            or int(checkpoint[4]) != len(expected_records)
        ):
            raise LegalCorpusImmutableConflict(
                "completed page evidence is absent or differs"
            )
        records = tuple(
            self._load_completed_result(
                cursor,
                sync_run_id=sync_run_id,
                provider_id=provider_id,
                expected_fetch_id=str(fetch[0]),
                expected_resource_id=resource_id,
                expected_version_id=version_id,
                checkpoint_after=checkpoint[2],
                run_replayed=run_replayed,
            )
            for resource_id, version_id in expected_records
        )
        return PublishedCorpusPage(
            sync_run_id=sync_run_id,
            provider_id=provider_id,
            requested_scope_hash=requested_scope_hash,
            source_fetch_id=str(fetch[0]),
            page_hash=page_hash,
            checkpoint_before=checkpoint[1],
            checkpoint_after=checkpoint[2],
            checkpoint_sequence=int(checkpoint[0]),
            records=records,
            run_replayed=run_replayed,
        )

    def mark_failed(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        error_code: str,
        occurred_at: datetime,
    ) -> None:
        safe_code = _safe_error_code(error_code)
        with self._transaction() as connection:
            with connection.cursor() as cursor:
                self._advisory_lock(cursor, sync_run_id)
                latest = self._latest_event(cursor, sync_run_id)
                if latest is None or latest[1] in {
                    "completed",
                    "failed",
                    "cancelled",
                }:
                    return
                cursor.execute(
                    _SELECT_RUN_SCOPE_SQL, (sync_run_id, provider_id)
                )
                run_scope = cursor.fetchone()
                if run_scope is None:
                    raise LegalCorpusImmutableConflict(
                        "failed run is not owned by the expected provider"
                    )
                self._advisory_lock(
                    cursor,
                    _scope_lock_key(provider_id, str(run_scope[1])),
                )
                self._append_event(
                    cursor,
                    sync_run_id=sync_run_id,
                    provider_id=provider_id,
                    sequence_number=int(latest[0]) + 1,
                    from_status=str(latest[1]),
                    to_status="failed",
                    error_code=safe_code,
                    occurred_at=occurred_at,
                )
                cursor.execute(
                    _SELECT_LATEST_LEASE_SQL,
                    (provider_id, str(run_scope[1])),
                )
                lease_event = cursor.fetchone()
                if (
                    lease_event is not None
                    and str(lease_event[4]) == sync_run_id
                    and str(lease_event[6]) in {"acquired", "renewed"}
                ):
                    active = _lease_from_row(
                        lease_event,
                        provider_id=provider_id,
                        requested_scope=str(run_scope[0]),
                        requested_scope_hash=str(run_scope[1]),
                    )
                    released = LegalSyncLease(
                        sync_run_id=active.sync_run_id,
                        provider_id=active.provider_id,
                        requested_scope=active.requested_scope,
                        requested_scope_hash=active.requested_scope_hash,
                        lease_id=active.lease_id,
                        fencing_token=active.fencing_token,
                        event_sequence=active.event_sequence + 1,
                        owner_id=active.owner_id,
                        acquired_at=active.acquired_at,
                        expires_at=occurred_at,
                    )
                    self._append_lease_event(
                        cursor,
                        lease=released,
                        event_type="released",
                        occurred_at=occurred_at,
                        expires_at=occurred_at,
                        prior_event_id=str(lease_event[0]),
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
            (identity,),
        )

    @staticmethod
    def _latest_event(cursor: Any, sync_run_id: str) -> Any:
        cursor.execute(_SELECT_LATEST_EVENT_SQL, (sync_run_id,))
        return cursor.fetchone()

    def _insert_provider(
        self, cursor: Any, provider: LegalSourceProviderRecord
    ) -> None:
        cursor.execute(
            _SELECT_PREVIOUS_PROVIDER_SQL,
            (provider.provider_key, provider.provider_revision),
        )
        previous = cursor.fetchone()
        supersedes_provider_id = (
            str(previous[0]) if previous is not None else None
        )
        expected = (
            provider.provider_key,
            provider.provider_revision,
            provider.source_authority,
            provider.authority_tier,
            provider.provider_kind,
            provider.canonical_base_url,
            provider.terms_version,
            provider.adapter_version,
            "active",
            supersedes_provider_id,
        )
        cursor.execute(_SELECT_PROVIDER_SQL, (provider.provider_id,))
        existing = cursor.fetchone()
        if existing is not None:
            if tuple(existing) != expected:
                raise LegalCorpusImmutableConflict(
                    "provider replay differs from immutable revision lineage"
                )
            return

        cursor.execute(
            _SELECT_CURRENT_PROVIDER_REVISION_SQL,
            (provider.provider_key,),
        )
        current = cursor.fetchone()
        if (
            current is not None
            and int(current[1]) >= provider.provider_revision
        ):
            raise LegalCorpusImmutableConflict(
                "provider revision must be appended after the current revision"
            )
        cursor.execute(
            _INSERT_PROVIDER_SQL,
            (
                provider.provider_id,
                provider.provider_key,
                provider.provider_revision,
                provider.source_authority,
                provider.authority_tier,
                provider.provider_kind,
                provider.canonical_base_url,
                provider.terms_version,
                provider.adapter_version,
                supersedes_provider_id,
                provider.configured_by,
                provider.configured_at,
                _json(provider.metadata),
            ),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(_SELECT_PROVIDER_SQL, (provider.provider_id,))
        existing = cursor.fetchone()
        if existing is None or tuple(existing) != expected:
            raise LegalCorpusImmutableConflict(
                "provider identity is bound to different configuration"
            )

    def _append_lease_event(
        self,
        cursor: Any,
        *,
        lease: LegalSyncLease,
        event_type: str,
        occurred_at: datetime,
        expires_at: datetime,
        prior_event_id: str | None,
    ) -> None:
        event_id = _deterministic_id(
            "legal-sync-lease-event",
            {
                "leaseId": lease.lease_id,
                "eventSequence": lease.event_sequence,
                "eventType": event_type,
            },
        )
        cursor.execute(
            _INSERT_LEASE_EVENT_SQL,
            (
                event_id,
                lease.provider_id,
                lease.requested_scope,
                lease.requested_scope_hash,
                lease.event_sequence,
                lease.lease_id,
                lease.fencing_token,
                lease.sync_run_id,
                lease.owner_id,
                event_type,
                occurred_at,
                expires_at,
                prior_event_id,
                _json({}),
            ),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(_SELECT_LEASE_EVENT_SQL, (event_id,))
        existing = cursor.fetchone()
        expected = (
            lease.provider_id,
            lease.requested_scope_hash,
            lease.event_sequence,
            lease.lease_id,
            lease.fencing_token,
            lease.sync_run_id,
            lease.owner_id,
            event_type,
            occurred_at,
            expires_at,
            prior_event_id,
        )
        if existing is None or tuple(existing) != expected:
            raise LegalCorpusImmutableConflict(
                "lease event differs from immutable fencing transition"
            )

    def _append_event(
        self,
        cursor: Any,
        *,
        sync_run_id: str,
        provider_id: str,
        sequence_number: int,
        from_status: str | None,
        to_status: str,
        occurred_at: datetime,
        checkpoint_after: str | None = None,
        records_seen: int | None = None,
        records_written: int | None = None,
        error_code: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        event_id = (
            f"legal-sync-event:{sync_run_id}:{sequence_number}:{to_status}"
        )
        cursor.execute(
            _INSERT_EVENT_SQL,
            (
                event_id,
                sync_run_id,
                provider_id,
                sequence_number,
                from_status,
                to_status,
                checkpoint_after,
                records_seen,
                records_written,
                error_code,
                occurred_at,
                _json(metadata or {}),
            ),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(
            _SELECT_EVENT_SEQUENCE_SQL, (sync_run_id, sequence_number)
        )
        existing = cursor.fetchone()
        if existing is None or str(existing[0]) != to_status:
            raise LegalCorpusImmutableConflict(
                "sync event sequence is bound to a different transition"
            )

    @staticmethod
    def _insert_fetch(cursor: Any, fetch: SourceFetchRecord) -> None:
        cursor.execute(
            _INSERT_FETCH_SQL,
            (
                fetch.source_fetch_id,
                fetch.provider_id,
                fetch.sync_run_id,
                fetch.transport,
                fetch.endpoint_key,
                fetch.sanitized_source_locator,
                fetch.request_fingerprint,
                fetch.response_status,
                fetch.raw_object_key,
                fetch.raw_object_version,
                fetch.raw_sha256,
                fetch.raw_size_bytes,
                fetch.response_content_type,
                fetch.provider_etag,
                fetch.provider_last_modified,
                fetch.fetched_at,
                _json(fetch.metadata),
            ),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(_SELECT_FETCH_SQL, (fetch.source_fetch_id,))
        existing = cursor.fetchone()
        expected = (
            fetch.provider_id,
            fetch.sync_run_id,
            fetch.request_fingerprint,
            "succeeded",
            fetch.raw_object_key,
            fetch.raw_object_version,
            fetch.raw_sha256,
            fetch.raw_size_bytes,
        )
        if existing is None or tuple(existing) != expected:
            raise LegalCorpusImmutableConflict(
                "source fetch identity is bound to different immutable bytes"
            )

    @staticmethod
    def _insert_resource(cursor: Any, resource: LegalResourceRecord) -> None:
        cursor.execute(
            _INSERT_RESOURCE_SQL,
            (
                resource.legal_resource_id,
                resource.provider_id,
                resource.resource_type,
                resource.official_identifier,
                resource.jurisdiction_code,
                resource.issuing_authority,
                resource.identity_hash,
                resource.first_observed_at,
                _json(resource.metadata),
            ),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(_SELECT_RESOURCE_SQL, (resource.legal_resource_id,))
        existing = cursor.fetchone()
        expected_stable_identity = (
            resource.resource_type,
            resource.official_identifier,
            resource.jurisdiction_code,
            resource.issuing_authority,
            resource.identity_hash,
            resource.cross_revision_key,
        )
        if (
            existing is None
            or tuple(existing[1:]) != expected_stable_identity
        ):
            raise LegalCorpusImmutableConflict(
                "legal resource identity is bound to different authority"
            )

    @staticmethod
    def _insert_resource_crosswalk(
        cursor: Any,
        crosswalk: LegalResourceCrosswalkRecord,
    ) -> bool:
        cursor.execute(
            _INSERT_RESOURCE_CROSSWALK_SQL,
            (
                crosswalk.legal_resource_crosswalk_id,
                crosswalk.legal_resource_id,
                crosswalk.provider_id,
                crosswalk.first_observed_source_fetch_id,
                crosswalk.provider_resource_type,
                crosswalk.provider_official_identifier,
                crosswalk.provider_jurisdiction_code,
                crosswalk.cross_revision_key,
                crosswalk.observed_at,
                _json(crosswalk.metadata),
            ),
        )
        if cursor.fetchone() is not None:
            return True
        cursor.execute(
            _SELECT_RESOURCE_CROSSWALK_SQL,
            (crosswalk.legal_resource_crosswalk_id,),
        )
        existing = cursor.fetchone()
        expected = (
            crosswalk.legal_resource_id,
            crosswalk.provider_id,
            crosswalk.provider_resource_type,
            crosswalk.provider_official_identifier,
            crosswalk.provider_jurisdiction_code,
            crosswalk.cross_revision_key,
        )
        if existing is None or tuple(existing) != expected:
            raise LegalCorpusImmutableConflict(
                "provider resource alias maps to a different canonical resource"
            )
        return False

    @staticmethod
    def _insert_version(
        cursor: Any,
        version: LegalResourceVersionRecord,
        *,
        version_sequence: int,
        supersedes_version_id: str | None,
    ) -> None:
        cursor.execute(
            _INSERT_VERSION_SQL,
            (
                version.legal_resource_version_id,
                version.legal_resource_id,
                version.provider_id,
                version.source_fetch_id,
                version.official_version_identifier,
                version_sequence,
                version.canonical_title,
                version.alternative_titles_text,
                version.publication_date,
                version.promulgation_date,
                version.effective_from,
                version.effective_to,
                version.valid_time_start,
                version.valid_time_end,
                version.observed_at,
                version.fetched_at,
                version.verified_at,
                version.verification_status,
                version.normalized_sha256,
                version.parser_schema_version,
                supersedes_version_id,
                _json(version.metadata),
            ),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(
            _SELECT_VERSION_IDENTITY_SQL,
            (version.legal_resource_version_id,),
        )
        existing = cursor.fetchone()
        expected = (
            version.legal_resource_id,
            version.provider_id,
            version.official_version_identifier,
            version_sequence,
            version.normalized_sha256,
            supersedes_version_id,
        )
        if existing is None or tuple(existing) != expected:
            raise LegalCorpusImmutableConflict(
                "legal version identity is bound to different normalized content"
            )

    @staticmethod
    def _insert_version_lineage(
        cursor: Any,
        *,
        version: LegalResourceVersionRecord,
        decision: EffectiveLineageDecision,
        validated_at: datetime,
    ) -> None:
        lineage_id = _deterministic_id(
            "legal-resource-version-lineage",
            {
                "legalResourceVersionId": (
                    version.legal_resource_version_id
                ),
            },
        )
        metadata = {
            "declaredPredecessorOfficialVersionIdentifier": (
                version.declared_predecessor_official_version_identifier
            ),
        }
        if version.effective_gap_reason:
            metadata["gapReason"] = version.effective_gap_reason
        cursor.execute(
            _INSERT_VERSION_LINEAGE_SQL,
            (
                lineage_id,
                version.legal_resource_id,
                version.legal_resource_version_id,
                decision.predecessor_version_id,
                decision.lineage_type,
                decision.continuity_status,
                version.effective_continuity_policy,
                decision.predecessor_effective_to,
                decision.successor_effective_from,
                decision.derived_predecessor_effective_to,
                decision.gap_days,
                validated_at,
                _json(metadata),
            ),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(_SELECT_VERSION_LINEAGE_SQL, (lineage_id,))
        existing = cursor.fetchone()
        expected = (
            version.legal_resource_id,
            version.legal_resource_version_id,
            decision.predecessor_version_id,
            decision.lineage_type,
            decision.continuity_status,
            version.effective_continuity_policy,
            decision.predecessor_effective_to,
            decision.successor_effective_from,
            decision.derived_predecessor_effective_to,
            decision.gap_days,
        )
        if existing is None or tuple(existing) != expected:
            raise LegalCorpusImmutableConflict(
                "effective-time lineage identity is bound to different content"
            )

    @staticmethod
    def _insert_provision(cursor: Any, provision: LegalProvisionRecord) -> None:
        cursor.execute(
            _INSERT_PROVISION_SQL,
            (
                provision.legal_provision_id,
                provision.legal_resource_version_id,
                provision.legal_resource_id,
                provision.parent_provision_id,
                provision.provision_type,
                provision.provision_identifier,
                provision.display_path,
                list(provision.ordinal_path),
                provision.heading_text,
                provision.body_text,
                provision.provision_sha256,
                provision.source_locator,
                _json(provision.metadata),
            ),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(_SELECT_PROVISION_SQL, (provision.legal_provision_id,))
        existing = cursor.fetchone()
        expected = (
            provision.legal_resource_version_id,
            provision.legal_resource_id,
            provision.parent_provision_id,
            provision.provision_type,
            provision.provision_identifier,
            provision.display_path,
            list(provision.ordinal_path),
            provision.provision_sha256,
        )
        if existing is None or tuple(existing) != expected:
            raise LegalCorpusImmutableConflict(
                "legal provision identity is bound to different normalized text"
            )

    @staticmethod
    def _compare_provision_set(
        cursor: Any,
        version_id: str,
        provisions: Sequence[LegalProvisionRecord],
    ) -> None:
        cursor.execute(_SELECT_VERSION_PROVISIONS_SQL, (version_id,))
        existing = tuple(
            sorted((str(row[0]), str(row[1])) for row in cursor.fetchall())
        )
        expected = tuple(
            sorted(
                (
                    provision.provision_identifier,
                    provision.provision_sha256,
                )
                for provision in provisions
            )
        )
        if existing != expected:
            raise LegalCorpusImmutableConflict(
                "existing normalized version has a different provision set"
            )

    @staticmethod
    def _load_completed_result(
        cursor: Any,
        *,
        sync_run_id: str,
        provider_id: str,
        expected_fetch_id: str,
        expected_resource_id: str,
        expected_version_id: str,
        checkpoint_after: str | None,
        run_replayed: bool,
    ) -> PublishedCorpusVersion:
        cursor.execute(
            _SELECT_COMPLETED_RESULT_SQL,
            (
                expected_fetch_id,
                expected_resource_id,
                expected_version_id,
                sync_run_id,
                provider_id,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise LegalCorpusImmutableConflict(
                "completed run does not resolve to its deterministic publication"
            )
        return PublishedCorpusVersion(
            sync_run_id=sync_run_id,
            source_fetch_id=str(row[4]),
            legal_resource_id=expected_resource_id,
            legal_resource_version_id=str(row[0]),
            version_sequence=int(row[1]),
            supersedes_version_id=(
                str(row[2]) if row[2] is not None else None
            ),
            normalized_sha256=str(row[3]),
            checkpoint_after=checkpoint_after,
            reused_existing_version=str(row[5]) != expected_fetch_id,
            run_replayed=run_replayed,
            effective_predecessor_version_id=(
                str(row[6]) if row[6] is not None else None
            ),
            effective_lineage_type=str(row[7]),
            effective_continuity_status=str(row[8]),
        )


class InMemoryLegalCorpusSyncRepository:
    """Thread-safe behavioral twin of the fenced PostgreSQL publisher.

    It stages a complete page on copied state and swaps that state only after
    every record, checkpoint, run event, and lease release succeeds.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self.providers: dict[str, LegalSourceProviderRecord] = {}
        self.runs: dict[str, LegalSyncRunRecord] = {}
        self.events: dict[str, list[str]] = {}
        self.lease_events: dict[
            tuple[str, str], list[dict[str, Any]]
        ] = {}
        self.checkpoint_events: dict[
            tuple[str, str], list[dict[str, Any]]
        ] = {}
        self.fetches: dict[str, SourceFetchRecord] = {}
        self.resources: dict[str, LegalResourceRecord] = {}
        self.crosswalks: dict[str, LegalResourceCrosswalkRecord] = {}
        self.versions: dict[
            str, tuple[LegalResourceVersionRecord, int, str | None, str | None,
                       str, str]
        ] = {}
        self.resource_versions: dict[str, list[str]] = {}
        self.provisions: dict[str, LegalProvisionRecord] = {}
        self.version_provisions: dict[str, tuple[str, ...]] = {}
        self.pages: dict[str, PublishedCorpusPage] = {}
        self.failed_codes: list[str] = []

    def begin_sync(self, run: LegalSyncRunRecord) -> SyncRunState:
        _validate_global_provider(run.provider)
        _validate_sync_run_envelope(run)
        with self._lock:
            providers = deepcopy(self.providers)
            runs = deepcopy(self.runs)
            events = deepcopy(self.events)
            leases = deepcopy(self.lease_events)
            self._stage_provider(providers, run.provider)
            existing = runs.get(run.sync_run_id)
            if existing is not None and not _same_run_envelope(existing, run):
                raise LegalCorpusImmutableConflict(
                    "sync run identity is bound to different input"
                )
            if existing is None:
                runs[run.sync_run_id] = run
            statuses = events.get(run.sync_run_id, [])
            if statuses and statuses[-1] == "completed":
                return SyncRunState(run.sync_run_id, "completed", None)
            if statuses and statuses[-1] == "cancelled":
                raise LegalCorpusRunTerminalError(
                    "sync run is terminal (cancelled); use a new idempotency key"
                )
            self._assert_checkpoint_before(
                run.provider.provider_id,
                run.requested_scope_hash,
                run.checkpoint_before,
            )
            scope_key = (
                run.provider.provider_id,
                run.requested_scope_hash,
            )
            scope_events = leases.setdefault(scope_key, [])
            latest = scope_events[-1] if scope_events else None
            if (
                latest is not None
                and latest["eventType"] in {"acquired", "renewed"}
                and latest["lease"].expires_at > self._now()
            ):
                lease = latest["lease"]
                if (
                    lease.sync_run_id != run.sync_run_id
                    or lease.owner_id != run.lease_owner_id
                ):
                    raise LegalCorpusLeaseConflict(
                        "provider/requested_scope is held by another "
                        "fenced worker"
                    )
            else:
                prior = latest["lease"] if latest is not None else None
                token = prior.fencing_token + 1 if prior else 1
                sequence = prior.event_sequence + 1 if prior else 1
                lease = LegalSyncLease(
                    sync_run_id=run.sync_run_id,
                    provider_id=run.provider.provider_id,
                    requested_scope=run.requested_scope,
                    requested_scope_hash=run.requested_scope_hash,
                    lease_id=_deterministic_id(
                        "legal-sync-lease",
                        {
                            "syncRunId": run.sync_run_id,
                            "fencingToken": token,
                        },
                    ),
                    fencing_token=token,
                    event_sequence=sequence,
                    owner_id=run.lease_owner_id,
                    acquired_at=run.requested_at,
                    expires_at=run.requested_at
                    + timedelta(seconds=run.lease_ttl_seconds),
                )
                scope_events.append(
                    {
                        "eventType": "acquired",
                        "lease": lease,
                        "occurredAt": run.requested_at,
                    }
                )
            if not statuses:
                statuses = ["pending", "running"]
            elif statuses[-1] == "pending":
                statuses.append("running")
            elif statuses[-1] == "failed":
                statuses.append("running")
            elif statuses[-1] != "running":
                raise LegalCorpusRunTerminalError(
                    f"sync run is terminal ({statuses[-1]})"
                )
            events[run.sync_run_id] = statuses
            self.providers = providers
            self.runs = runs
            self.events = events
            self.lease_events = leases
            return SyncRunState(run.sync_run_id, "running", lease)

    def get_completed_publication(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        expected_resource_id: str,
        expected_version_id: str,
    ) -> PublishedCorpusVersion:
        page = self.get_completed_page(
            sync_run_id=sync_run_id,
            provider_id=provider_id,
            page_hash=self.runs[sync_run_id].page_hash,
            expected_records=(
                (expected_resource_id, expected_version_id),
            ),
        )
        return page.records[0]

    def get_completed_page(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        page_hash: str,
        expected_records: Sequence[tuple[str, str]],
    ) -> PublishedCorpusPage:
        with self._lock:
            return self._completed_page(
                sync_run_id,
                provider_id,
                page_hash,
                expected_records,
                replayed=True,
            )

    def renew_lease(
        self,
        lease: LegalSyncLease,
        *,
        occurred_at: datetime,
        lease_ttl_seconds: int,
    ) -> LegalSyncLease:
        if not 30 <= lease_ttl_seconds <= 3600:
            raise LegalCorpusRepositoryError(
                "lease_ttl_seconds must be between 30 and 3600"
            )
        with self._lock:
            active = self._active_lease(lease)
            leases = deepcopy(self.lease_events)
            renewed = LegalSyncLease(
                sync_run_id=active.sync_run_id,
                provider_id=active.provider_id,
                requested_scope=active.requested_scope,
                requested_scope_hash=active.requested_scope_hash,
                lease_id=active.lease_id,
                fencing_token=active.fencing_token,
                event_sequence=active.event_sequence + 1,
                owner_id=active.owner_id,
                acquired_at=active.acquired_at,
                expires_at=max(
                    active.expires_at,
                    occurred_at + timedelta(seconds=lease_ttl_seconds),
                ),
            )
            leases[
                (lease.provider_id, lease.requested_scope_hash)
            ].append(
                {
                    "eventType": "renewed",
                    "lease": renewed,
                    "occurredAt": occurred_at,
                }
            )
            self.lease_events = leases
            return renewed

    def publish(self, publication: CorpusPublication) -> PublishedCorpusVersion:
        if len(publication.records) != 1:
            raise LegalCorpusRepositoryError(
                "publish() accepts one record; use publish_page() for a page"
            )
        return self.publish_page(publication).records[0]

    def publish_page(
        self, publication: CorpusPublication
    ) -> PublishedCorpusPage:
        verifications = _validated_publication_verification(publication)
        _validate_page_publication_envelope(publication)
        if not hmac.compare_digest(
            publication.page_hash,
            _publication_page_hash(publication, verifications),
        ):
            raise LegalCorpusImmutableConflict(
                "page_hash does not attest the normalized record page"
            )
        expected_records = tuple(
            (
                item.resource.legal_resource_id,
                item.version.legal_resource_version_id,
            )
            for item in publication.records
        )
        with self._lock:
            return self._publish_page_locked(publication, expected_records)

    def _publish_page_locked(
        self,
        publication: CorpusPublication,
        expected_records: Sequence[tuple[str, str]],
    ) -> PublishedCorpusPage:
        run = self.runs.get(publication.sync_run_id)
        if run is None or run.provider.provider_id != publication.provider_id:
            raise LegalCorpusRepositoryError("sync run is absent")
        if (
            run.requested_scope != publication.lease.requested_scope
            or run.requested_scope_hash
            != publication.lease.requested_scope_hash
            or not _same_checkpoint(
                run.checkpoint_before, publication.checkpoint_before
            )
            or run.page_hash != publication.page_hash
            or run.page_record_count != len(publication.records)
        ):
            raise LegalCorpusImmutableConflict(
                "page publication differs from immutable run envelope"
            )
        statuses = self.events.get(publication.sync_run_id, [])
        if statuses and statuses[-1] == "completed":
            return self._completed_page(
                publication.sync_run_id,
                publication.provider_id,
                publication.page_hash,
                expected_records,
                replayed=True,
            )
        if not statuses or statuses[-1] != "running":
            raise LegalCorpusRunTerminalError(
                "only a running sync run may publish"
            )
        active = self._active_lease(publication.lease)
        self._assert_checkpoint_before(
            publication.provider_id,
            publication.lease.requested_scope_hash,
            publication.checkpoint_before,
        )
        provider = self.providers.get(publication.provider_id)
        if provider is None or any(
            provider.source_authority != str(item["sourceAuthority"])
            for item in _validated_publication_verification(publication)
        ):
            raise LegalCorpusImmutableConflict(
                "source verification authority does not match provider"
            )
        return self._stage_and_commit_page(
            publication, expected_records, active
        )

    def _stage_and_commit_page(
        self,
        publication: CorpusPublication,
        expected_records: Sequence[tuple[str, str]],
        active: LegalSyncLease,
    ) -> PublishedCorpusPage:
        fetches = deepcopy(self.fetches)
        resources = deepcopy(self.resources)
        crosswalks = deepcopy(self.crosswalks)
        versions = deepcopy(self.versions)
        resource_versions = deepcopy(self.resource_versions)
        provisions = deepcopy(self.provisions)
        version_provisions = deepcopy(self.version_provisions)
        checkpoints = deepcopy(self.checkpoint_events)
        leases = deepcopy(self.lease_events)
        events = deepcopy(self.events)
        pages = deepcopy(self.pages)
        existing_fetch = fetches.get(publication.fetch.source_fetch_id)
        if existing_fetch is not None and existing_fetch != publication.fetch:
            raise LegalCorpusImmutableConflict(
                "source fetch identity is bound to different evidence"
            )
        fetches[publication.fetch.source_fetch_id] = publication.fetch
        published: dict[tuple[str, str], PublishedCorpusVersion] = {}
        for index, item in enumerate(
            sorted(
                publication.records,
                key=lambda candidate: (
                    candidate.resource.legal_resource_id,
                    candidate.version.legal_resource_version_id,
                ),
            )
        ):
            self._before_stage_record(index, item)
            result = self._stage_record(
                publication,
                item,
                resources,
                crosswalks,
                versions,
                resource_versions,
                provisions,
                version_provisions,
            )
            published[
                (
                    item.resource.legal_resource_id,
                    item.version.legal_resource_version_id,
                )
            ] = result
        scope_key = (
            publication.provider_id,
            publication.lease.requested_scope_hash,
        )
        scope_checkpoints = checkpoints.setdefault(scope_key, [])
        checkpoint_sequence = len(scope_checkpoints) + 1
        scope_checkpoints.append(
            {
                "checkpointSequence": checkpoint_sequence,
                "syncRunId": publication.sync_run_id,
                "checkpointBefore": publication.checkpoint_before,
                "checkpointAfter": publication.checkpoint_after,
                "pageHash": publication.page_hash,
                "pageRecordCount": len(publication.records),
                "leaseId": active.lease_id,
                "fencingToken": active.fencing_token,
            }
        )
        page = PublishedCorpusPage(
            sync_run_id=publication.sync_run_id,
            provider_id=publication.provider_id,
            requested_scope_hash=publication.lease.requested_scope_hash,
            source_fetch_id=publication.fetch.source_fetch_id,
            page_hash=publication.page_hash,
            checkpoint_before=publication.checkpoint_before,
            checkpoint_after=publication.checkpoint_after,
            checkpoint_sequence=checkpoint_sequence,
            records=tuple(published[identity] for identity in expected_records),
        )
        pages[publication.sync_run_id] = page
        events[publication.sync_run_id].append("completed")
        released = LegalSyncLease(
            sync_run_id=active.sync_run_id,
            provider_id=active.provider_id,
            requested_scope=active.requested_scope,
            requested_scope_hash=active.requested_scope_hash,
            lease_id=active.lease_id,
            fencing_token=active.fencing_token,
            event_sequence=active.event_sequence + 1,
            owner_id=active.owner_id,
            acquired_at=active.acquired_at,
            expires_at=publication.completed_at,
        )
        leases[scope_key].append(
            {
                "eventType": "released",
                "lease": released,
                "occurredAt": publication.completed_at,
            }
        )
        self.fetches = fetches
        self.resources = resources
        self.crosswalks = crosswalks
        self.versions = versions
        self.resource_versions = resource_versions
        self.provisions = provisions
        self.version_provisions = version_provisions
        self.checkpoint_events = checkpoints
        self.lease_events = leases
        self.events = events
        self.pages = pages
        return page

    def _stage_record(
        self,
        publication: CorpusPublication,
        item: CorpusRecordPublication,
        resources: dict[str, LegalResourceRecord],
        crosswalks: dict[str, LegalResourceCrosswalkRecord],
        versions: dict[
            str, tuple[LegalResourceVersionRecord, int, str | None, str | None,
                       str, str]
        ],
        resource_versions: dict[str, list[str]],
        provisions: dict[str, LegalProvisionRecord],
        version_provisions: dict[str, tuple[str, ...]],
    ) -> PublishedCorpusVersion:
        resource = item.resource
        version = item.version
        existing_resource = resources.get(resource.legal_resource_id)
        if (
            existing_resource is not None
            and _resource_identity_tuple(existing_resource)
            != _resource_identity_tuple(resource)
        ):
            raise LegalCorpusImmutableConflict(
                "legal resource identity is bound to different authority"
            )
        resources.setdefault(resource.legal_resource_id, resource)
        existing_crosswalk = crosswalks.get(
            item.crosswalk.legal_resource_crosswalk_id
        )
        if (
            existing_crosswalk is not None
            and _crosswalk_identity_tuple(existing_crosswalk)
            != _crosswalk_identity_tuple(item.crosswalk)
        ):
            raise LegalCorpusImmutableConflict(
                "resource crosswalk identity is bound to another alias"
            )
        crosswalks.setdefault(
            item.crosswalk.legal_resource_crosswalk_id, item.crosswalk
        )
        version_ids = resource_versions.setdefault(
            resource.legal_resource_id, []
        )
        same_hash = next(
            (
                version_id
                for version_id in version_ids
                if versions[version_id][0].normalized_sha256
                == version.normalized_sha256
            ),
            None,
        )
        if same_hash is not None:
            if same_hash != version.legal_resource_version_id:
                raise LegalCorpusImmutableConflict(
                    "normalized content is bound to another version identity"
                )
            stored = versions[same_hash]
            sequence, supersedes = stored[1], stored[2]
            effective_predecessor = stored[3]
            lineage_type, continuity = stored[4], stored[5]
            self._assert_same_provisions(
                same_hash, item.provisions, provisions, version_provisions
            )
            reused = True
        else:
            if version.legal_resource_version_id in versions:
                raise LegalCorpusImmutableConflict(
                    "legal version identity is bound to different content"
                )
            lineage = self._effective_lineage(
                version, version_ids, versions
            )
            supersedes = version_ids[-1] if version_ids else None
            sequence = len(version_ids) + 1
            effective_predecessor = lineage.predecessor_version_id
            lineage_type = lineage.lineage_type
            continuity = lineage.continuity_status
            versions[version.legal_resource_version_id] = (
                version,
                sequence,
                supersedes,
                effective_predecessor,
                lineage_type,
                continuity,
            )
            version_ids.append(version.legal_resource_version_id)
            provision_ids: list[str] = []
            for provision in item.provisions:
                existing = provisions.get(provision.legal_provision_id)
                if existing is not None and existing != provision:
                    raise LegalCorpusImmutableConflict(
                        "legal provision identity is bound to different text"
                    )
                provisions[provision.legal_provision_id] = provision
                provision_ids.append(provision.legal_provision_id)
            version_provisions[version.legal_resource_version_id] = tuple(
                provision_ids
            )
            reused = False
        return PublishedCorpusVersion(
            sync_run_id=publication.sync_run_id,
            source_fetch_id=publication.fetch.source_fetch_id,
            legal_resource_id=resource.legal_resource_id,
            legal_resource_version_id=version.legal_resource_version_id,
            version_sequence=sequence,
            supersedes_version_id=supersedes,
            normalized_sha256=version.normalized_sha256,
            checkpoint_after=publication.checkpoint_after,
            reused_existing_version=reused,
            effective_predecessor_version_id=effective_predecessor,
            effective_lineage_type=lineage_type,
            effective_continuity_status=continuity,
        )

    def _effective_lineage(
        self,
        version: LegalResourceVersionRecord,
        version_ids: Sequence[str],
        versions: Mapping[
            str, tuple[LegalResourceVersionRecord, int, str | None, str | None,
                       str, str]
        ],
    ) -> EffectiveLineageDecision:
        candidates = tuple(
            EffectiveVersionWindow(
                legal_resource_version_id=version_id,
                official_version_identifier=versions[version_id][
                    0
                ].official_version_identifier,
                version_sequence=versions[version_id][1],
                effective_from=versions[version_id][0].effective_from,
                effective_to=versions[version_id][0].effective_to,
                valid_time_start=versions[version_id][0].valid_time_start,
                valid_time_end=versions[version_id][0].valid_time_end,
            )
            for version_id in version_ids
        )
        try:
            return validate_effective_time_lineage(
                candidates,
                EffectiveVersionCandidate(
                    legal_resource_version_id=(
                        version.legal_resource_version_id
                    ),
                    official_version_identifier=(
                        version.official_version_identifier
                    ),
                    effective_from=version.effective_from,
                    effective_to=version.effective_to,
                    valid_time_start=version.valid_time_start,
                    valid_time_end=version.valid_time_end,
                    declared_predecessor_official_version_identifier=(
                        version.declared_predecessor_official_version_identifier
                    ),
                    continuity_policy=version.effective_continuity_policy,
                    gap_reason=version.effective_gap_reason,
                ),
            )
        except EffectiveTimeConflict as exc:
            raise LegalCorpusEffectiveTimeConflict(
                exc.code, str(exc)
            ) from exc

    def mark_failed(
        self,
        *,
        sync_run_id: str,
        provider_id: str,
        error_code: str,
        occurred_at: datetime,
    ) -> None:
        with self._lock:
            run = self.runs.get(sync_run_id)
            statuses = self.events.get(sync_run_id)
            if (
                run is None
                or run.provider.provider_id != provider_id
                or not statuses
                or statuses[-1] in {"completed", "failed", "cancelled"}
            ):
                return
            statuses.append("failed")
            self.failed_codes.append(_safe_error_code(error_code))
            scope_key = (provider_id, run.requested_scope_hash)
            leases = self.lease_events.get(scope_key, [])
            if (
                leases
                and leases[-1]["eventType"] in {"acquired", "renewed"}
                and leases[-1]["lease"].sync_run_id == sync_run_id
            ):
                active = leases[-1]["lease"]
                leases.append(
                    {
                        "eventType": "released",
                        "lease": LegalSyncLease(
                            sync_run_id=active.sync_run_id,
                            provider_id=active.provider_id,
                            requested_scope=active.requested_scope,
                            requested_scope_hash=active.requested_scope_hash,
                            lease_id=active.lease_id,
                            fencing_token=active.fencing_token,
                            event_sequence=active.event_sequence + 1,
                            owner_id=active.owner_id,
                            acquired_at=active.acquired_at,
                            expires_at=occurred_at,
                        ),
                        "occurredAt": occurred_at,
                    }
                )

    def _completed_page(
        self,
        sync_run_id: str,
        provider_id: str,
        page_hash: str,
        expected_records: Sequence[tuple[str, str]],
        *,
        replayed: bool,
    ) -> PublishedCorpusPage:
        run = self.runs.get(sync_run_id)
        page = self.pages.get(sync_run_id)
        records_by_identity = (
            {
                (
                    record.legal_resource_id,
                    record.legal_resource_version_id,
                ): record
                for record in page.records
            }
            if page is not None
            else {}
        )
        expected = tuple(expected_records)
        if (
            run is None
            or run.provider.provider_id != provider_id
            or page is None
            or page.page_hash != page_hash
            or len(records_by_identity) != len(expected)
            or set(records_by_identity) != set(expected)
        ):
            raise LegalCorpusImmutableConflict(
                "completed page differs from immutable publication"
            )
        ordered_page = PublishedCorpusPage(
            sync_run_id=page.sync_run_id,
            provider_id=page.provider_id,
            requested_scope_hash=page.requested_scope_hash,
            source_fetch_id=page.source_fetch_id,
            page_hash=page.page_hash,
            checkpoint_before=page.checkpoint_before,
            checkpoint_after=page.checkpoint_after,
            checkpoint_sequence=page.checkpoint_sequence,
            records=tuple(
                records_by_identity[identity] for identity in expected
            ),
            run_replayed=page.run_replayed,
        )
        return _replayed_page(ordered_page, replayed)

    def _active_lease(
        self, lease: LegalSyncLease
    ) -> LegalSyncLease:
        events = self.lease_events.get(
            (lease.provider_id, lease.requested_scope_hash), []
        )
        latest = events[-1] if events else None
        if (
            latest is None
            or latest["eventType"] not in {"acquired", "renewed"}
            or latest["lease"].lease_id != lease.lease_id
            or latest["lease"].fencing_token != lease.fencing_token
            or latest["lease"].sync_run_id != lease.sync_run_id
            or latest["lease"].owner_id != lease.owner_id
            or latest["lease"].expires_at <= self._now()
        ):
            raise LegalCorpusLeaseConflict(
                "sync lease is expired, released, or fenced by another worker"
            )
        return latest["lease"]

    def _assert_checkpoint_before(
        self,
        provider_id: str,
        scope_hash: str,
        checkpoint_before: str | None,
    ) -> None:
        events = self.checkpoint_events.get(
            (provider_id, scope_hash), []
        )
        head = events[-1]["checkpointAfter"] if events else None
        if events and not _same_checkpoint(head, checkpoint_before):
            raise LegalCorpusCheckpointConflict(
                "checkpoint_before is stale for provider/requested_scope"
            )

    @staticmethod
    def _stage_provider(
        providers: dict[str, LegalSourceProviderRecord],
        provider: LegalSourceProviderRecord,
    ) -> None:
        existing = providers.get(provider.provider_id)
        if existing is not None:
            if _provider_identity_tuple(existing) != _provider_identity_tuple(
                provider
            ):
                raise LegalCorpusImmutableConflict(
                    "provider replay differs from immutable revision lineage"
                )
            return
        current = max(
            (
                item.provider_revision
                for item in providers.values()
                if item.provider_key == provider.provider_key
            ),
            default=0,
        )
        if current >= provider.provider_revision:
            raise LegalCorpusImmutableConflict(
                "provider revision must be appended after the current revision"
            )
        providers[provider.provider_id] = provider

    @staticmethod
    def _assert_same_provisions(
        version_id: str,
        expected: Sequence[LegalProvisionRecord],
        provisions: Mapping[str, LegalProvisionRecord],
        version_provisions: Mapping[str, tuple[str, ...]],
    ) -> None:
        actual_set = tuple(
            sorted(
                (
                    provisions[item_id].provision_identifier,
                    provisions[item_id].provision_sha256,
                )
                for item_id in version_provisions[version_id]
            )
        )
        expected_set = tuple(
            sorted(
                (
                    item.provision_identifier,
                    item.provision_sha256,
                )
                for item in expected
            )
        )
        if actual_set != expected_set:
            raise LegalCorpusImmutableConflict(
                "existing normalized version has a different provision set"
            )

    def _before_stage_record(
        self, index: int, item: CorpusRecordPublication
    ) -> None:
        """Test seam invoked against copied state before each page record."""

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise LegalCorpusRepositoryError(
                "in-memory legal corpus clock must be timezone-aware"
            )
        return value


def _replayed_page(
    page: PublishedCorpusPage, replayed: bool
) -> PublishedCorpusPage:
    records = tuple(
        PublishedCorpusVersion(
            sync_run_id=item.sync_run_id,
            source_fetch_id=item.source_fetch_id,
            legal_resource_id=item.legal_resource_id,
            legal_resource_version_id=item.legal_resource_version_id,
            version_sequence=item.version_sequence,
            supersedes_version_id=item.supersedes_version_id,
            normalized_sha256=item.normalized_sha256,
            checkpoint_after=item.checkpoint_after,
            reused_existing_version=item.reused_existing_version,
            run_replayed=replayed,
            effective_predecessor_version_id=(
                item.effective_predecessor_version_id
            ),
            effective_lineage_type=item.effective_lineage_type,
            effective_continuity_status=item.effective_continuity_status,
        )
        for item in page.records
    )
    return PublishedCorpusPage(
        sync_run_id=page.sync_run_id,
        provider_id=page.provider_id,
        requested_scope_hash=page.requested_scope_hash,
        source_fetch_id=page.source_fetch_id,
        page_hash=page.page_hash,
        checkpoint_before=page.checkpoint_before,
        checkpoint_after=page.checkpoint_after,
        checkpoint_sequence=page.checkpoint_sequence,
        records=records,
        run_replayed=replayed,
    )


def _same_run_envelope(
    left: LegalSyncRunRecord, right: LegalSyncRunRecord
) -> bool:
    return (
        left.provider.provider_id == right.provider.provider_id
        and hmac.compare_digest(left.input_hash, right.input_hash)
        and left.idempotency_key == right.idempotency_key
        and left.requested_scope == right.requested_scope
        and hmac.compare_digest(
            left.requested_scope_hash, right.requested_scope_hash
        )
        and _same_checkpoint(
            left.checkpoint_before, right.checkpoint_before
        )
        and hmac.compare_digest(left.page_hash, right.page_hash)
        and left.page_record_count == right.page_record_count
        and left.coverage_contract_id == right.coverage_contract_id
    )


def _provider_identity_tuple(
    provider: LegalSourceProviderRecord,
) -> tuple[Any, ...]:
    return (
        provider.provider_key,
        provider.provider_revision,
        provider.source_authority,
        provider.authority_tier,
        provider.provider_kind,
        provider.canonical_base_url,
        provider.terms_version,
        provider.adapter_version,
    )


def _resource_identity_tuple(
    resource: LegalResourceRecord,
) -> tuple[Any, ...]:
    return (
        resource.cross_revision_key,
        resource.resource_type,
        resource.official_identifier,
        resource.jurisdiction_code,
        resource.issuing_authority,
        resource.identity_hash,
    )


def _crosswalk_identity_tuple(
    item: LegalResourceCrosswalkRecord,
) -> tuple[Any, ...]:
    return (
        item.legal_resource_id,
        item.provider_id,
        item.provider_resource_type,
        item.provider_official_identifier,
        item.provider_jurisdiction_code,
        item.cross_revision_key,
    )


def _validated_publication_verification(
    publication: CorpusPublication,
) -> tuple[Mapping[str, Any], ...]:
    """Fail closed when a caller bypasses the application-service contract."""

    fetch = publication.fetch
    if (
        fetch.response_status is None
        or not 200 <= int(fetch.response_status) <= 299
    ):
        raise LegalCorpusRepositoryError(
            "verified corpus publication requires a 2xx source response"
        )
    if fetch.transport == "remote_mcp":
        raise LegalCorpusRepositoryError(
            "remote MCP transport cannot publish the verified corpus"
        )
    raw_verifications = fetch.metadata.get("sourceVerifications")
    if raw_verifications is None:
        legacy = fetch.metadata.get("sourceVerification")
        raw_verifications = [legacy] if legacy is not None else None
    if not isinstance(raw_verifications, list) or not all(
        isinstance(item, Mapping) for item in raw_verifications
    ):
        raise LegalCorpusRepositoryError(
            "source verification attestations are required"
        )
    verification_by_identifier = {
        str(item.get("officialIdentifier") or ""): dict(item)
        for item in raw_verifications
    }
    if len(verification_by_identifier) != len(publication.records):
        raise LegalCorpusImmutableConflict(
            "page verification attestations are missing or duplicated"
        )
    allowed_bases = {
        "direct_api": {"official_api_response"},
        "bulk_download": {"official_bulk_artifact"},
        "self_hosted_mcp": {"self_hosted_official_bridge"},
        "manual_verified_import": {"verified_manual_review"},
    }
    verified: list[Mapping[str, Any]] = []
    for item in publication.records:
        version = item.version
        resource = item.resource
        if version.verification_status not in {
            "source_verified",
            "schema_verified",
        }:
            raise LegalCorpusRepositoryError(
                "verified corpus publication requires verified versions"
            )
        verification = verification_by_identifier.get(
            item.crosswalk.provider_official_identifier
        )
        if verification is None:
            raise LegalCorpusImmutableConflict(
                "record has no matching source verification attestation"
            )
        expected = {
            "schemaVersion": "realytics-legal-source-verification-v1",
            "responseSha256": fetch.raw_sha256,
            "officialIdentifier": (
                item.crosswalk.provider_official_identifier
            ),
            "parserSchemaVersion": version.parser_schema_version,
            "schemaValidationStatus": "passed",
            "authorityValidationStatus": "matched",
            "transport": fetch.transport,
        }
        if any(
            str(verification.get(key) or "") != str(value)
            for key, value in expected.items()
        ):
            raise LegalCorpusImmutableConflict(
                "source verification attestation does not match publication"
            )
        for key in (
            "sourceAuthority",
            "verificationBasis",
            "verifierId",
            "verifierVersion",
            "contractSha256",
        ):
            if not str(verification.get(key) or "").strip():
                raise LegalCorpusRepositoryError(
                    f"source verification attestation is missing {key}"
                )
        if str(verification["verificationBasis"]) not in allowed_bases.get(
            fetch.transport, set()
        ):
            raise LegalCorpusRepositoryError(
                "verification basis is not valid for source transport"
            )
        supplied_contract_hash = str(verification["contractSha256"])
        contract_payload = {
            key: value
            for key, value in verification.items()
            if key != "contractSha256"
        }
        calculated_contract_hash = hashlib.sha256(
            _json(contract_payload).encode("utf-8")
        ).hexdigest()
        if not hmac.compare_digest(
            supplied_contract_hash, calculated_contract_hash
        ):
            raise LegalCorpusImmutableConflict(
                "source verification contract hash does not match attestation"
            )
        verified.append(verification)
    return tuple(verified)


def _validate_page_publication_envelope(
    publication: CorpusPublication,
) -> None:
    if not publication.records:
        raise LegalCorpusRepositoryError(
            "corpus page requires at least one normalized record"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", publication.page_hash):
        raise LegalCorpusRepositoryError("page_hash must be SHA-256 hex")
    fetch = publication.fetch
    if (
        fetch.sync_run_id != publication.sync_run_id
        or fetch.provider_id != publication.provider_id
        or publication.lease.sync_run_id != publication.sync_run_id
        or publication.lease.provider_id != publication.provider_id
    ):
        raise LegalCorpusRepositoryError(
            "page identifiers do not share one provider and sync run"
        )
    identities: set[tuple[str, str]] = set()
    for item in publication.records:
        resource = item.resource
        crosswalk = item.crosswalk
        version = item.version
        identity = (
            resource.legal_resource_id,
            version.legal_resource_version_id,
        )
        if identity in identities:
            raise LegalCorpusRepositoryError(
                "corpus page contains duplicate normalized records"
            )
        identities.add(identity)
        if (
            resource.provider_id != publication.provider_id
            or crosswalk.provider_id != publication.provider_id
            or crosswalk.first_observed_source_fetch_id
            != fetch.source_fetch_id
            or crosswalk.legal_resource_id
            != resource.legal_resource_id
            or crosswalk.cross_revision_key
            != resource.cross_revision_key
            or crosswalk.provider_resource_type
            != resource.resource_type
            or crosswalk.provider_jurisdiction_code
            != resource.jurisdiction_code
            or resource.cross_revision_key
            != f"official-resource:v1:{resource.identity_hash}"
            or resource.legal_resource_id
            != _deterministic_id(
                "legal-resource",
                {"crossRevisionKey": resource.cross_revision_key},
            )
            or crosswalk.legal_resource_crosswalk_id
            != _deterministic_id(
                "legal-resource-crosswalk",
                {
                    "providerId": crosswalk.provider_id,
                    "providerResourceType": (
                        crosswalk.provider_resource_type
                    ),
                    "providerOfficialIdentifier": (
                        crosswalk.provider_official_identifier
                    ),
                    "providerJurisdictionCode": (
                        crosswalk.provider_jurisdiction_code
                    ),
                },
            )
            or version.provider_id != publication.provider_id
            or version.source_fetch_id != fetch.source_fetch_id
            or version.legal_resource_id != resource.legal_resource_id
            or any(
                provision.legal_resource_id
                != resource.legal_resource_id
                or provision.legal_resource_version_id
                != version.legal_resource_version_id
                for provision in item.provisions
            )
        ):
            raise LegalCorpusRepositoryError(
                "record identifiers escape the page provider/fetch/resource"
            )


def _publication_page_hash(
    publication: CorpusPublication,
    verifications: Sequence[Mapping[str, Any]],
) -> str:
    verification_by_identifier = {
        str(item["officialIdentifier"]): item for item in verifications
    }
    payload = {
        "records": sorted(
            (
                {
                    "legalResourceId": item.resource.legal_resource_id,
                    "legalResourceVersionId": (
                        item.version.legal_resource_version_id
                    ),
                    "normalizedSha256": item.version.normalized_sha256,
                    "sourceVerificationSha256": verification_by_identifier[
                        item.crosswalk.provider_official_identifier
                    ]["contractSha256"],
                    "lineageContractSha256": hashlib.sha256(
                        _json(
                            {
                                "declaredPredecessorOfficialVersionIdentifier": (
                                    item.version.declared_predecessor_official_version_identifier
                                ),
                                "effectiveContinuityPolicy": (
                                    item.version.effective_continuity_policy
                                ),
                                "effectiveGapReason": (
                                    item.version.effective_gap_reason
                                ),
                            }
                        ).encode("utf-8")
                    ).hexdigest(),
                }
                for item in publication.records
            ),
            key=lambda item: (
                item["legalResourceId"],
                item["legalResourceVersionId"],
            ),
        )
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _validate_sync_run_envelope(run: LegalSyncRunRecord) -> None:
    raw_identity = run.metadata.get("coverageIdentity")
    if run.coverage_contract_id is None:
        if raw_identity is not None:
            raise LegalCorpusRepositoryError(
                "coverage identity requires a coverage contract binding"
            )
        expected_scope_hash = _scope_hash(
            run.provider.provider_id, run.requested_scope
        )
    else:
        if not isinstance(raw_identity, Mapping):
            raise LegalCorpusRepositoryError(
                "coverage contract binding requires typed coverage identity"
            )
        try:
            identity = OfficialCorpusCoverageIdentity(
                provider_key=str(raw_identity.get("providerKey") or ""),
                provider_revision=int(
                    raw_identity.get("providerRevision") or 0
                ),
                registry_version=str(
                    raw_identity.get("registryVersion") or ""
                ),
                resource_kind=str(
                    raw_identity.get("resourceKind") or ""
                ),
                target=str(raw_identity.get("target") or ""),
                query_sha256=str(
                    raw_identity.get("querySha256") or ""
                ),
                source_adapter_version=str(
                    raw_identity.get("sourceAdapterVersion") or ""
                ),
                boundary_contract_version=str(
                    raw_identity.get("boundaryContractVersion") or ""
                ),
                coverage_contract_version=str(
                    raw_identity.get("coverageContractVersion") or ""
                ),
            )
        except (TypeError, ValueError) as exc:
            raise LegalCorpusRepositoryError(
                "coverage identity is malformed"
            ) from exc
        identity.assert_provider(
            provider_key=run.provider.provider_key,
            provider_revision=run.provider.provider_revision,
            provider_id=run.provider.provider_id,
        )
        if not hmac.compare_digest(
            identity.coverage_contract_id,
            run.coverage_contract_id,
        ):
            raise LegalCorpusRepositoryError(
                "coverage contract id differs from typed identity"
            )
        expected_scope_hash = identity.scope_hash(
            requested_scope=run.requested_scope
        )
    if not hmac.compare_digest(
        run.requested_scope_hash, expected_scope_hash
    ):
        raise LegalCorpusRepositoryError(
            "requested_scope_hash does not match provider/scope"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", run.page_hash):
        raise LegalCorpusRepositoryError("page_hash must be SHA-256 hex")
    if run.page_record_count < 1:
        raise LegalCorpusRepositoryError(
            "page_record_count must be positive"
        )
    if run.lease_ttl_seconds < 30 or run.lease_ttl_seconds > 3600:
        raise LegalCorpusRepositoryError(
            "lease_ttl_seconds must be between 30 and 3600"
        )
    if not run.lease_owner_id.strip():
        raise LegalCorpusRepositoryError("lease_owner_id is required")


def _lease_from_row(
    row: Sequence[Any],
    *,
    provider_id: str,
    requested_scope: str,
    requested_scope_hash: str,
) -> LegalSyncLease:
    return LegalSyncLease(
        sync_run_id=str(row[4]),
        provider_id=provider_id,
        requested_scope=requested_scope,
        requested_scope_hash=requested_scope_hash,
        lease_id=str(row[2]),
        fencing_token=int(row[3]),
        event_sequence=int(row[1]),
        owner_id=str(row[5]),
        acquired_at=row[7],
        expires_at=row[8],
    )


def _same_checkpoint(left: Any, right: Any) -> bool:
    return (None if left is None else str(left)) == (
        None if right is None else str(right)
    )


def _scope_hash(provider_id: str, requested_scope: str) -> str:
    return hashlib.sha256(
        _json(
            {
                "providerId": provider_id,
                "requestedScope": requested_scope,
            }
        ).encode("utf-8")
    ).hexdigest()


def _scope_lock_key(provider_id: str, requested_scope_hash: str) -> str:
    return f"legal-sync-scope:{provider_id}:{requested_scope_hash}"


def _deterministic_id(prefix: str, value: Any) -> str:
    digest = hashlib.sha256(_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def _validate_global_provider(
    provider: LegalSourceProviderRecord,
) -> None:
    if provider.authority_tier not in {
        "official_primary",
        "official_secondary",
    }:
        raise LegalCorpusRepositoryError(
            "global legal corpus accepts only official source authorities"
        )
    normalized_authority = re.sub(
        r"[^a-z0-9]", "", provider.source_authority.lower()
    )
    if normalized_authority in {
        "tenantdocument",
        "tenantsupplied",
        "callersupplied",
        "unknown",
    }:
        raise LegalCorpusRepositoryError(
            "tenant/caller material belongs in the tenant RLS overlay"
        )


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _safe_error_code(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_]", "_", str(value).upper())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return (normalized or "LEGAL_SYNC_FAILED")[:80]


__all__ = [
    "CorpusRecordPublication",
    "CorpusPublication",
    "InMemoryLegalCorpusSyncRepository",
    "LegalCorpusCheckpointConflict",
    "LegalCorpusEffectiveTimeConflict",
    "LegalCorpusImmutableConflict",
    "LegalCorpusLeaseConflict",
    "LegalCorpusRepositoryError",
    "LegalCorpusRunTerminalError",
    "LegalCorpusSyncRepository",
    "LegalProvisionRecord",
    "LegalResourceCrosswalkRecord",
    "LegalResourceRecord",
    "LegalResourceVersionRecord",
    "LegalSourceProviderRecord",
    "LegalSyncRunRecord",
    "LegalSyncLease",
    "PostgresLegalCorpusSyncRepository",
    "PublishedCorpusPage",
    "PublishedCorpusVersion",
    "SourceFetchRecord",
    "SyncRunState",
]
