from __future__ import annotations

"""Transactional PostgreSQL repository for the normalized Parser V2 schema.

This module intentionally does not use ``db.postgres.upsert_typed_jsonb_record``.
Parser V2 rows are append-only, while the generic helper emits
``ON CONFLICT .. DO UPDATE``.  Every statement below names a fixed table and
uses INSERT-only idempotency: an identical immutable hash is a successful
replay; a different hash is a conflict.

Migrations are a deployment concern.  No repository method calls
``ensure_schema`` or commits an injected connection.
"""

import copy
import hashlib
import hmac
import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Sequence

from db import postgres


PROJECT_PROFILE_RECORD_TYPE = "parser_v2_project_profile"
PARSING_RUN_RECORD_TYPE = "parser_v2_parsing_run"
DOCUMENT_VERSION_RECORD_TYPE = "parser_v2_document_version"
PARSED_FACT_CANDIDATE_RECORD_TYPE = "parser_v2_parsed_fact_candidate"
SOURCE_CONFLICT_RECORD_TYPE = "parser_v2_source_conflict"
REVIEW_DECISION_RECORD_TYPE = "parser_v2_review_decision"
CANONICAL_SELECTION_RECORD_TYPE = "parser_v2_canonical_selection"
APPROVED_INPUT_SNAPSHOT_RECORD_TYPE = "parser_v2_approved_input_snapshot"
CANDIDATE_SET_SEAL_RECORD_TYPE = "parser_v2_candidate_set_seal"

_GENERATED_TIMESTAMPS = frozenset(
    {
        "createdAt",
        "updatedAt",
        "requestedAt",
        "ingestedAt",
        "detectedAt",
        "decidedAt",
        "selectedAt",
        "approvedAt",
        "resolvedAt",
        "startedAt",
        "completedAt",
    }
)
_MUTABLE_OUTBOX_PAYLOAD_FIELDS = frozenset(
    {
        "status",
        "attemptCount",
        "availableAt",
        "leaseOwner",
        "leaseExpiresAt",
        "lastError",
        "updatedAt",
        "publishedAt",
    }
)
_HEX = frozenset("0123456789abcdef")


class ParserV2RepositoryError(RuntimeError):
    """Base error raised by the normalized Parser V2 repository."""


class ParserV2TenantScopeError(ParserV2RepositoryError):
    """The requested project is absent from the transaction tenant scope."""


class ParserV2ImmutableConflict(ParserV2RepositoryError):
    """An immutable identity already exists with different content."""


class ParserV2UnsupportedRecord(ParserV2RepositoryError):
    """A record type/event kind has no fixed normalized-table dispatch."""


Connect = Callable[[], Any]


_SET_TENANT_SQL = "SELECT set_config('realytics.tenant_id', %s, true)"
_LOCK_TENANT_PROJECT_SQL = (
    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"
)
_LOCK_PROJECT_SCOPE_SQL = (
    "SELECT project_id FROM projects "
    "WHERE project_id = %s AND tenant_id = %s"
)


_INSERT_PROFILE_SQL = """
INSERT INTO parser_project_profiles(
    tenant_id, project_id, profile_id, profile_version,
    configuration_status, profile_hash, plan_hash, configured_by,
    configured_at, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_PROFILE_IDENTITY_SQL = """
SELECT profile_hash, plan_hash, payload_json
FROM parser_project_profiles
WHERE tenant_id = %s AND project_id = %s
  AND profile_id = %s AND profile_version = %s
"""
_LIST_PROFILES_SQL = """
SELECT payload_json
FROM parser_project_profiles
WHERE tenant_id = %s AND project_id = %s
ORDER BY configured_at, profile_id, profile_version
LIMIT %s
"""


_INSERT_DOCUMENT_SQL = """
INSERT INTO parser_document_versions(
    document_version_id, tenant_id, project_id, document_id, archive_id,
    object_version, sha256, file_name, media_type, size_bytes, version_number,
    source_authority, schema_version, supersedes_document_version_id,
    ingested_by, ingested_at, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_DOCUMENT_IDENTITY_SQL = """
SELECT payload_json->>'immutablePayloadHash', payload_json
FROM parser_document_versions
WHERE document_version_id = %s AND tenant_id = %s AND project_id = %s
"""


_INSERT_RUN_SQL = """
INSERT INTO parser_runs(
    parsing_run_id, tenant_id, project_id, document_version_id,
    execution_status, approval_status, parser_version, ontology_version,
    prompt_version, model_version, profile_id, profile_version, plan_hash,
    requested_modules, idempotency_key, input_hash, parent_parsing_run_id,
    attempt_number, requested_at, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_RUN_IDENTITY_SQL = """
SELECT payload_json->>'immutablePayloadHash', payload_json
FROM parser_runs
WHERE parsing_run_id = %s AND tenant_id = %s AND project_id = %s
"""


_INSERT_RUN_EVENT_SQL = """
INSERT INTO parser_run_events(
    run_event_id, tenant_id, project_id, parsing_run_id, sequence_number,
    from_execution_status, execution_status, approval_status, output_hash,
    started_at, completed_at, occurred_at, actor_id, event_hash, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_RUN_EVENT_IDENTITY_SQL = """
SELECT event_hash, payload_json
FROM parser_run_events
WHERE run_event_id = %s AND tenant_id = %s AND project_id = %s
"""


_INSERT_SEAL_SQL = """
INSERT INTO parser_candidate_set_seals(
    seal_id, tenant_id, project_id, parsing_run_id, candidate_set_hash,
    candidate_count, conflict_count, sealed_by, sealed_at, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_SEAL_IDENTITY_SQL = """
SELECT payload_json->>'immutablePayloadHash', payload_json
FROM parser_candidate_set_seals
WHERE seal_id = %s AND tenant_id = %s AND project_id = %s
"""


_INSERT_CANDIDATE_SQL = """
INSERT INTO parsed_fact_candidates(
    candidate_id, tenant_id, project_id, parsing_run_id, document_version_id,
    field_key, entity_id, raw_value, normalized_value, unit, origin_type,
    presence_status, review_status, approval_status, conflict_status,
    confidence, source_authority, effective_date, as_of_date, parser_version,
    ontology_version, prompt_version, model_version, field_registry_version,
    validation_status, validation_codes, validation_finding_count, formula_id,
    formula_version, calculation_run_id, calculation_engine_version,
    calculation_output_path, calculation_output_hash, input_snapshot_hash,
    supersedes_candidate_id, candidate_hash, created_at, payload_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_CANDIDATE_IDENTITY_SQL = """
SELECT candidate_hash, payload_json
FROM parsed_fact_candidates
WHERE candidate_id = %s AND tenant_id = %s AND project_id = %s
"""
_SELECT_RUN_SEAL_SQL = """
SELECT seal_id
FROM parser_candidate_set_seals
WHERE tenant_id = %s AND project_id = %s AND parsing_run_id = %s
LIMIT 1
"""
_SELECT_RUN_CANDIDATE_SET_SQL = """
SELECT candidate_id, candidate_hash
FROM parsed_fact_candidates
WHERE tenant_id = %s AND project_id = %s AND parsing_run_id = %s
ORDER BY candidate_id
"""
_SELECT_RUN_CONFLICT_SET_SQL = """
SELECT conflict_id, conflict_hash
FROM parser_source_conflicts
WHERE tenant_id = %s AND project_id = %s
  AND (
      parsing_run_id = %s
      OR COALESCE(payload_json->'sourceParsingRunIds', '[]'::jsonb) ? %s
  )
ORDER BY conflict_id
"""
_INSERT_CANDIDATE_INPUT_SQL = """
INSERT INTO parser_candidate_inputs(
    tenant_id, project_id, candidate_id, input_candidate_id, input_order, created_at
) VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""
_INSERT_VALIDATION_FINDING_SQL = """
INSERT INTO parser_validation_findings(
    finding_id, tenant_id, project_id, parsing_run_id, candidate_id,
    field_registry_version, code, severity, message, blocking, created_at,
    finding_hash, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
"""


_INSERT_REVIEW_SQL = """
INSERT INTO parser_review_decisions(
    review_decision_id, tenant_id, project_id, candidate_id, decision_sequence,
    action, resulting_review_status, resulting_approval_status, reviewer_id,
    decided_at, reason, decision_hash, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_REVIEW_IDENTITY_SQL = """
SELECT decision_hash, payload_json
FROM parser_review_decisions
WHERE review_decision_id = %s AND tenant_id = %s AND project_id = %s
"""
_INSERT_REVIEW_CONFLICT_SQL = """
INSERT INTO parser_review_decision_conflicts(
    tenant_id, project_id, review_decision_id, conflict_id, created_at
) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


_INSERT_CONFLICT_SQL = """
INSERT INTO parser_source_conflicts(
    conflict_id, tenant_id, project_id, parsing_run_id, field_key, entity_id,
    conflict_type, conflict_status, candidate_count, conflict_hash, detected_at,
    resolution_review_decision_id, resolved_at, supersedes_conflict_id,
    payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_CONFLICT_IDENTITY_SQL = """
SELECT conflict_hash, payload_json
FROM parser_source_conflicts
WHERE conflict_id = %s AND tenant_id = %s AND project_id = %s
"""
_INSERT_CONFLICT_CANDIDATE_SQL = """
INSERT INTO parser_conflict_candidates(
    tenant_id, project_id, conflict_id, candidate_id, ordinal, created_at
) VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""
_INSERT_CONFLICT_EVENT_SQL = """
INSERT INTO parser_source_conflict_events(
    conflict_event_id, tenant_id, project_id, conflict_id, sequence_number,
    conflict_status, review_decision_id, actor_id, resolved_at, event_hash,
    payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_CONFLICT_EVENT_IDENTITY_SQL = """
SELECT event_hash, payload_json
FROM parser_source_conflict_events
WHERE conflict_event_id = %s AND tenant_id = %s AND project_id = %s
"""


_INSERT_SELECTION_SQL = """
INSERT INTO parser_canonical_selections(
    selection_id, tenant_id, project_id, field_key, entity_id, candidate_id,
    review_decision_id, approval_status, conflict_status, normalized_value, unit,
    field_registry_version, selection_version, selection_hash, selected_by,
    selected_at, effective_date, as_of_date, supersedes_selection_id, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_SELECTION_IDENTITY_SQL = """
SELECT selection_hash, payload_json
FROM parser_canonical_selections
WHERE selection_id = %s AND tenant_id = %s AND project_id = %s
"""


_INSERT_SNAPSHOT_SQL = """
INSERT INTO parser_approved_snapshots(
    snapshot_id, tenant_id, project_id, purpose, snapshot_version,
    approval_status, input_schema_version, ontology_version,
    field_registry_version, snapshot_hash, selection_count, source_run_count,
    approved_by, approved_at, supersedes_snapshot_id, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_SNAPSHOT_IDENTITY_SQL = """
SELECT snapshot_hash, payload_json
FROM parser_approved_snapshots
WHERE snapshot_id = %s AND tenant_id = %s AND project_id = %s
"""
_INSERT_SNAPSHOT_SELECTION_SQL = """
INSERT INTO parser_snapshot_selections(
    tenant_id, project_id, snapshot_id, selection_id, ordinal, created_at
) VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""
_INSERT_SNAPSHOT_RUN_SQL = """
INSERT INTO parser_snapshot_source_runs(
    tenant_id, project_id, snapshot_id, parsing_run_id, created_at
) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""
_INSERT_SNAPSHOT_OUTBOX_SQL = """
INSERT INTO runtime_outbox_events(
    event_id, tenant_id, project_id, aggregate_type, aggregate_id, event_type,
    idempotency_key, status, attempt_count, max_attempts, available_at,
    created_at, updated_at, payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', 0, 8, %s, %s, %s, %s::jsonb)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING payload_json
"""
_SELECT_SNAPSHOT_OUTBOX_IDENTITY_SQL = """
SELECT event_id, tenant_id, project_id, aggregate_type, aggregate_id,
       event_type, payload_json
FROM runtime_outbox_events
WHERE idempotency_key = %s
"""


_SELECT_LATEST_SELECTION_STATE_SQL = """
SELECT selection_id, selection_version,
       NOT EXISTS (
           SELECT 1 FROM parser_validity_events validity
           WHERE validity.tenant_id = selection.tenant_id
             AND validity.project_id = selection.project_id
             AND validity.aggregate_type = 'canonical_selection'
             AND validity.aggregate_id = selection.selection_id
       ) AS is_active
FROM parser_canonical_selections selection
WHERE tenant_id = %s AND project_id = %s AND field_key = %s
  AND entity_id IS NOT DISTINCT FROM %s
ORDER BY selection_version DESC, selected_at DESC
LIMIT 1
"""
_SELECT_LATEST_SNAPSHOT_STATE_SQL = """
SELECT snapshot_id, snapshot_version,
       NOT EXISTS (
           SELECT 1 FROM parser_validity_events validity
           WHERE validity.tenant_id = snapshot.tenant_id
             AND validity.project_id = snapshot.project_id
             AND validity.aggregate_type = 'approved_snapshot'
             AND validity.aggregate_id = snapshot.snapshot_id
       ) AS is_active
FROM parser_approved_snapshots snapshot
WHERE tenant_id = %s AND project_id = %s AND purpose = %s
ORDER BY snapshot_version DESC, approved_at DESC
LIMIT 1
"""
_SELECT_REPLACEMENT_SELECTION_SQL = """
SELECT supersedes_selection_id
FROM parser_canonical_selections
WHERE tenant_id = %s AND project_id = %s AND selection_id = %s
"""
_SELECT_REPLACEMENT_SNAPSHOT_SQL = """
SELECT supersedes_snapshot_id
FROM parser_approved_snapshots
WHERE tenant_id = %s AND project_id = %s AND snapshot_id = %s
"""
_SELECT_REPLACEMENT_DOCUMENT_SQL = """
SELECT supersedes_document_version_id, payload_json
FROM parser_document_versions
WHERE tenant_id = %s AND project_id = %s AND document_version_id = %s
"""
_SELECT_VALIDITY_TARGET_SELECTION_SQL = """
SELECT payload_json,
       NOT EXISTS (
           SELECT 1 FROM parser_validity_events validity
           WHERE validity.tenant_id = selection.tenant_id
             AND validity.project_id = selection.project_id
             AND validity.aggregate_type = 'canonical_selection'
             AND validity.aggregate_id = selection.selection_id
             AND validity.validity_event_id <> %s
       ) AS is_active
FROM parser_canonical_selections selection
WHERE tenant_id = %s AND project_id = %s AND selection_id = %s
"""
_SELECT_VALIDITY_TARGET_SNAPSHOT_SQL = """
SELECT payload_json,
       NOT EXISTS (
           SELECT 1 FROM parser_validity_events validity
           WHERE validity.tenant_id = snapshot.tenant_id
             AND validity.project_id = snapshot.project_id
             AND validity.aggregate_type = 'approved_snapshot'
             AND validity.aggregate_id = snapshot.snapshot_id
             AND validity.validity_event_id <> %s
       ) AS is_active
FROM parser_approved_snapshots snapshot
WHERE tenant_id = %s AND project_id = %s AND snapshot_id = %s
"""
_SELECT_CANDIDATE_FOR_SELECTION_SQL = """
SELECT field_key, entity_id, presence_status, validation_status, origin_type
FROM parsed_fact_candidates
WHERE tenant_id = %s AND project_id = %s AND candidate_id = %s
  AND NOT EXISTS (
      SELECT 1 FROM parser_validity_events validity
      WHERE validity.tenant_id = parsed_fact_candidates.tenant_id
        AND validity.project_id = parsed_fact_candidates.project_id
        AND validity.aggregate_type = 'parsed_fact_candidate'
        AND validity.aggregate_id = parsed_fact_candidates.candidate_id
  )
"""
_SELECT_LATEST_CANDIDATE_REVIEW_SQL = """
SELECT review_decision_id, action, resulting_review_status,
       resulting_approval_status
FROM parser_review_decisions
WHERE tenant_id = %s AND project_id = %s AND candidate_id = %s
ORDER BY decision_sequence DESC, decided_at DESC
LIMIT 1
"""
_SELECT_OPEN_CANDIDATE_CONFLICT_SQL = """
SELECT conflict.conflict_id
FROM parser_conflict_candidates membership
JOIN parser_source_conflicts conflict
  ON conflict.conflict_id = membership.conflict_id
 AND conflict.tenant_id = membership.tenant_id
 AND conflict.project_id = membership.project_id
WHERE membership.tenant_id = %s AND membership.project_id = %s
  AND membership.candidate_id = %s
  AND NOT EXISTS (
      SELECT 1 FROM parser_source_conflict_events resolution
      WHERE resolution.tenant_id = conflict.tenant_id
        AND resolution.project_id = conflict.project_id
        AND resolution.conflict_id = conflict.conflict_id
        AND resolution.conflict_status IN ('resolved', 'waived')
  )
LIMIT 1
"""
_SELECT_UNSELECTED_DERIVED_INPUT_SQL = """
SELECT inputs.input_candidate_id
FROM parser_candidate_inputs inputs
WHERE inputs.tenant_id = %s AND inputs.project_id = %s
  AND inputs.candidate_id = %s
  AND NOT EXISTS (
      SELECT 1 FROM parser_canonical_selections selection
      WHERE selection.tenant_id = inputs.tenant_id
        AND selection.project_id = inputs.project_id
        AND selection.candidate_id = inputs.input_candidate_id
        AND NOT EXISTS (
            SELECT 1 FROM parser_validity_events validity
            WHERE validity.tenant_id = selection.tenant_id
              AND validity.project_id = selection.project_id
              AND validity.aggregate_type = 'canonical_selection'
              AND validity.aggregate_id = selection.selection_id
        )
  )
LIMIT 1
"""
_SELECT_SNAPSHOT_SELECTION_STATE_SQL = """
SELECT selection.selection_id, selection.field_key, selection.entity_id,
       selection.candidate_id, selection.review_decision_id,
       selection.selection_version, selection.approval_status,
       selection.payload_json,
       NOT EXISTS (
           SELECT 1 FROM parser_validity_events validity
           WHERE validity.tenant_id = selection.tenant_id
             AND validity.project_id = selection.project_id
             AND validity.aggregate_type = 'canonical_selection'
             AND validity.aggregate_id = selection.selection_id
       ) AS is_active,
       NOT EXISTS (
           SELECT 1 FROM parser_canonical_selections newer
           WHERE newer.tenant_id = selection.tenant_id
             AND newer.project_id = selection.project_id
             AND newer.field_key = selection.field_key
             AND newer.entity_id IS NOT DISTINCT FROM selection.entity_id
             AND newer.selection_version > selection.selection_version
       ) AS is_latest
FROM parser_canonical_selections selection
WHERE selection.tenant_id = %s AND selection.project_id = %s
  AND selection.selection_id = %s
"""
_SELECT_ACTIVE_SNAPSHOTS_FOR_SELECTION_SQL = """
SELECT snapshot.snapshot_id, snapshot.payload_json
FROM parser_snapshot_selections membership
JOIN parser_approved_snapshots snapshot
  ON snapshot.snapshot_id = membership.snapshot_id
 AND snapshot.tenant_id = membership.tenant_id
 AND snapshot.project_id = membership.project_id
WHERE membership.tenant_id = %s AND membership.project_id = %s
  AND membership.selection_id = %s
  AND NOT EXISTS (
      SELECT 1 FROM parser_validity_events validity
      WHERE validity.tenant_id = snapshot.tenant_id
        AND validity.project_id = snapshot.project_id
        AND validity.aggregate_type = 'approved_snapshot'
        AND validity.aggregate_id = snapshot.snapshot_id
  )
ORDER BY snapshot.approved_at, snapshot.snapshot_id
"""


_INSERT_VALIDITY_EVENT_SQL = """
INSERT INTO parser_validity_events(
    validity_event_id, tenant_id, project_id, aggregate_type, aggregate_id,
    event_type, replacement_id, actor_id, reason, occurred_at, event_hash,
    payload_json
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT DO NOTHING
RETURNING payload_json
"""
_SELECT_VALIDITY_EVENT_IDENTITY_SQL = """
SELECT event_hash, payload_json
FROM parser_validity_events
WHERE validity_event_id = %s AND tenant_id = %s AND project_id = %s
"""


_LIST_DOCUMENTS_SQL = """SELECT payload_json FROM parser_document_versions
WHERE tenant_id = %s AND project_id = %s ORDER BY ingested_at, document_version_id LIMIT %s"""
_LIST_RUNS_SQL = """SELECT payload_json FROM parser_runs
WHERE tenant_id = %s AND project_id = %s ORDER BY requested_at, parsing_run_id LIMIT %s"""
_LIST_RUN_EVENTS_SQL = """SELECT payload_json FROM parser_run_events
WHERE tenant_id = %s AND project_id = %s ORDER BY occurred_at, sequence_number LIMIT %s"""
_LIST_SEALS_SQL = """SELECT payload_json FROM parser_candidate_set_seals
WHERE tenant_id = %s AND project_id = %s ORDER BY sealed_at, seal_id LIMIT %s"""
_LIST_CANDIDATES_SQL = """SELECT payload_json FROM parsed_fact_candidates
WHERE tenant_id = %s AND project_id = %s ORDER BY created_at, candidate_id LIMIT %s"""
_LIST_REVIEWS_SQL = """SELECT payload_json FROM parser_review_decisions
WHERE tenant_id = %s AND project_id = %s ORDER BY decided_at, review_decision_id LIMIT %s"""
_LIST_CONFLICTS_SQL = """SELECT payload_json FROM parser_source_conflicts
WHERE tenant_id = %s AND project_id = %s ORDER BY detected_at, conflict_id LIMIT %s"""
_LIST_CONFLICT_EVENTS_SQL = """SELECT payload_json FROM parser_source_conflict_events
WHERE tenant_id = %s AND project_id = %s ORDER BY resolved_at, sequence_number LIMIT %s"""
_LIST_SELECTIONS_SQL = """SELECT payload_json FROM parser_canonical_selections
WHERE tenant_id = %s AND project_id = %s ORDER BY selected_at, selection_id LIMIT %s"""
_LIST_SELECTION_EVENTS_SQL = """SELECT payload_json FROM parser_validity_events
WHERE tenant_id = %s AND project_id = %s AND aggregate_type = 'canonical_selection'
ORDER BY occurred_at, validity_event_id LIMIT %s"""
_LIST_SNAPSHOTS_SQL = """SELECT payload_json FROM parser_approved_snapshots
WHERE tenant_id = %s AND project_id = %s ORDER BY approved_at, snapshot_id LIMIT %s"""
_LIST_SNAPSHOT_EVENTS_SQL = """SELECT payload_json FROM parser_validity_events
WHERE tenant_id = %s AND project_id = %s AND aggregate_type = 'approved_snapshot'
ORDER BY occurred_at, validity_event_id LIMIT %s"""


_LIST_QUERIES: Dict[str, tuple[str, ...]] = {
    DOCUMENT_VERSION_RECORD_TYPE: (_LIST_DOCUMENTS_SQL,),
    PARSING_RUN_RECORD_TYPE: (_LIST_RUNS_SQL, _LIST_RUN_EVENTS_SQL),
    CANDIDATE_SET_SEAL_RECORD_TYPE: (_LIST_SEALS_SQL,),
    PARSED_FACT_CANDIDATE_RECORD_TYPE: (_LIST_CANDIDATES_SQL,),
    REVIEW_DECISION_RECORD_TYPE: (_LIST_REVIEWS_SQL,),
    SOURCE_CONFLICT_RECORD_TYPE: (_LIST_CONFLICTS_SQL, _LIST_CONFLICT_EVENTS_SQL),
    CANONICAL_SELECTION_RECORD_TYPE: (_LIST_SELECTIONS_SQL, _LIST_SELECTION_EVENTS_SQL),
    APPROVED_INPUT_SNAPSHOT_RECORD_TYPE: (_LIST_SNAPSHOTS_SQL, _LIST_SNAPSHOT_EVENTS_SQL),
}


@dataclass(frozen=True)
class ParserV2AppendBatch:
    """One atomic batch; profiles are inserted before dependent records."""

    profiles: tuple[Mapping[str, Any], ...] = ()
    records: tuple[Mapping[str, Any], ...] = ()


class ParserV2PostgresRepository:
    """Connection owner and transaction boundary for normalized Parser V2 rows."""

    def __init__(self, connect_fn: Connect = postgres.connect) -> None:
        self._connect = connect_fn

    def bind(self, conn: Any, tenant_id: str, project_id: str) -> "ParserV2PostgresSession":
        """Bind an existing transaction without committing it.

        This is the seam used when a Parser V2 snapshot and an outbox event must
        share a larger caller-owned transaction.
        """

        tenant = _required(tenant_id, "tenant_id")
        project = _required(project_id, "project_id")
        with conn.cursor() as cursor:
            cursor.execute(_SET_TENANT_SQL, (tenant,))
            cursor.execute(
                _LOCK_TENANT_PROJECT_SQL,
                (f"parser_v2:{tenant}:{project}",),
            )
            cursor.execute(_LOCK_PROJECT_SCOPE_SQL, (project, tenant))
            if cursor.fetchone() is None:
                raise ParserV2TenantScopeError(
                    "Parser V2 project is absent from the requested tenant scope"
                )
        return ParserV2PostgresSession(conn, tenant, project)

    @contextmanager
    def transaction(
        self, tenant_id: str, project_id: str
    ) -> Iterator["ParserV2PostgresSession"]:
        conn = self._connect()
        try:
            session = self.bind(conn, tenant_id, project_id)
            yield session
            session.validate_batch()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def append_batch(
        self,
        tenant_id: str,
        project_id: str,
        batch: ParserV2AppendBatch,
    ) -> Dict[str, list[Dict[str, Any]]]:
        with self.transaction(tenant_id, project_id) as session:
            profiles = [session.append_profile(value) for value in batch.profiles]
            records = [session.append_record(value) for value in batch.records]
        return {"profiles": profiles, "records": records}

    def append_record(
        self, tenant_id: str, project_id: str, record: Mapping[str, Any]
    ) -> Dict[str, Any]:
        with self.transaction(tenant_id, project_id) as session:
            return session.append_record(record)

    def append_profile(
        self, tenant_id: str, project_id: str, profile: Mapping[str, Any]
    ) -> Dict[str, Any]:
        with self.transaction(tenant_id, project_id) as session:
            return session.append_profile(profile)

    def list_records(
        self,
        tenant_id: str,
        project_id: str,
        record_type: str,
        *,
        limit: int = 1000,
    ) -> list[Dict[str, Any]]:
        with self.transaction(tenant_id, project_id) as session:
            return session.list_records(record_type, limit=limit)

    def get_approved_snapshot(
        self,
        tenant_id: str,
        project_id: str,
        snapshot_id: str,
    ) -> Dict[str, Any] | None:
        """Return one created snapshot from the requested tenant/project scope."""

        with self.transaction(tenant_id, project_id) as session:
            return session.get_approved_snapshot(snapshot_id)


class ParserV2PostgresSession:
    """A tenant/project-scoped view over one caller-owned PostgreSQL transaction."""

    def __init__(self, conn: Any, tenant_id: str, project_id: str) -> None:
        self._conn = conn
        self.tenant_id = tenant_id
        self.project_id = project_id
        self._required_invalidations: set[tuple[str, str, str]] = set()
        self._seen_invalidations: set[tuple[str, str, str]] = set()
        self._selection_invalidations: list[Dict[str, Any]] = []

    def validate_batch(self) -> None:
        """Fail before commit when a supersession lacks its matching validity row."""

        self._cascade_active_snapshots()
        missing = self._required_invalidations.difference(self._seen_invalidations)
        if missing:
            formatted = ", ".join(
                f"{aggregate}:{old_id}->{new_id}"
                for aggregate, old_id, new_id in sorted(missing)
            )
            raise ParserV2ImmutableConflict(
                "superseding Parser V2 rows require matching invalidation events "
                f"in the same transaction: {formatted}"
            )

    def _cascade_active_snapshots(self) -> None:
        """Invalidate snapshots that became stale after the caller's pre-read."""

        for source in self._selection_invalidations:
            selection_id = str(source["selectionId"])
            with self._conn.cursor() as cursor:
                cursor.execute(
                    _SELECT_ACTIVE_SNAPSHOTS_FOR_SELECTION_SQL,
                    (self.tenant_id, self.project_id, selection_id),
                )
                active_snapshots = [
                    (str(row[0]), _payload(row[1])) for row in cursor.fetchall()
                ]
            for snapshot_id, snapshot in active_snapshots:
                event_seed = {
                    "snapshotId": snapshot_id,
                    "selectionInvalidationId": source["invalidationId"],
                }
                event_id = f"snapshot-cascade:{_stable_hash(event_seed)}"
                record = {
                    "schemaVersion": "realytics-parser-v2-record-v1",
                    "recordType": APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
                    "recordId": f"{APPROVED_INPUT_SNAPSHOT_RECORD_TYPE}:{event_id}",
                    "eventKind": "invalidated",
                    "tenantId": self.tenant_id,
                    "projectId": self.project_id,
                    "actorId": source["actorId"],
                    "invalidationId": event_id,
                    "snapshotId": snapshot_id,
                    "selectionIds": list(snapshot.get("selectionIds") or []),
                    "reason": (
                        "contains canonical selection invalidated in the same "
                        f"transaction: {selection_id}"
                    ),
                    "approvalStatus": "superseded",
                    "validityStatus": "invalidated",
                    "sourceType": "parser_v2_transactional_cascade",
                    "createdAt": source["createdAt"],
                }
                record["immutablePayloadHash"] = _canonical_record_hash(record)
                self._append_validity_event(record, record["immutablePayloadHash"])

    def append_profile(self, value: Mapping[str, Any]) -> Dict[str, Any]:
        record = _scoped_copy(value, self.tenant_id, self.project_id)
        if record.get("recordType") != PROJECT_PROFILE_RECORD_TYPE:
            raise ParserV2UnsupportedRecord("expected a Parser V2 project profile record")
        profile = _mapping(record.get("profile"), "profile")
        plan = _mapping(record.get("executionPlan"), "executionPlan")
        profile_id = _required(profile.get("profileId"), "profile.profileId")
        profile_version = _required(profile.get("version"), "profile.version")
        profile_hash = _sha256(record.get("profileHash"), "profileHash")
        plan_hash = _sha256(record.get("executionPlanHash"), "executionPlanHash")
        if not hmac.compare_digest(profile_hash, _stable_hash(profile)):
            raise ParserV2ImmutableConflict("profileHash does not match profile payload")
        if not hmac.compare_digest(plan_hash, _stable_hash(plan)):
            raise ParserV2ImmutableConflict(
                "executionPlanHash does not match executionPlan payload"
            )
        payload = _json(record)
        with self._conn.cursor() as cursor:
            return _insert_and_compare(
                cursor,
                insert_sql=_INSERT_PROFILE_SQL,
                insert_params=(
                    self.tenant_id,
                    self.project_id,
                    profile_id,
                    profile_version,
                    record.get("configurationStatus") or "active",
                    profile_hash,
                    plan_hash,
                    _required(record.get("actorId"), "actorId"),
                    _required(record.get("createdAt"), "createdAt"),
                    payload,
                ),
                select_sql=_SELECT_PROFILE_IDENTITY_SQL,
                select_params=(
                    self.tenant_id,
                    self.project_id,
                    profile_id,
                    profile_version,
                ),
                expected_hashes=(profile_hash, plan_hash),
                identity=f"profile {profile_id}@{profile_version}",
            )

    def append_record(self, value: Mapping[str, Any]) -> Dict[str, Any]:
        record = _scoped_copy(value, self.tenant_id, self.project_id)
        record_type = str(record.get("recordType") or "")
        event_kind = str(record.get("eventKind") or "created")
        if record_type == PROJECT_PROFILE_RECORD_TYPE:
            return self.append_profile(record)
        record_hash = _record_hash(record)
        dispatch = {
            (DOCUMENT_VERSION_RECORD_TYPE, "created"): self._append_document,
            (PARSING_RUN_RECORD_TYPE, "created"): self._append_run,
            (PARSING_RUN_RECORD_TYPE, "execution_transition"): self._append_run_event,
            (CANDIDATE_SET_SEAL_RECORD_TYPE, "created"): self._append_seal,
            (PARSED_FACT_CANDIDATE_RECORD_TYPE, "created"): self._append_candidate,
            (REVIEW_DECISION_RECORD_TYPE, "created"): self._append_review,
            (SOURCE_CONFLICT_RECORD_TYPE, "created"): self._append_conflict,
            (SOURCE_CONFLICT_RECORD_TYPE, "conflict_resolution"): self._append_conflict_event,
            (CANONICAL_SELECTION_RECORD_TYPE, "created"): self._append_selection,
            (CANONICAL_SELECTION_RECORD_TYPE, "invalidated"): self._append_validity_event,
            (APPROVED_INPUT_SNAPSHOT_RECORD_TYPE, "created"): self._append_snapshot,
            (APPROVED_INPUT_SNAPSHOT_RECORD_TYPE, "invalidated"): self._append_validity_event,
        }.get((record_type, event_kind))
        if dispatch is None:
            raise ParserV2UnsupportedRecord(
                f"unsupported Parser V2 record dispatch: {record_type}/{event_kind}"
            )
        return dispatch(record, record_hash)

    def list_profiles(self, *, limit: int = 1000) -> list[Dict[str, Any]]:
        bounded = _limit(limit)
        with self._conn.cursor() as cursor:
            cursor.execute(
                _LIST_PROFILES_SQL,
                (self.tenant_id, self.project_id, bounded),
            )
            return [_payload(row[0]) for row in cursor.fetchall()]

    def list_records(
        self, record_type: str, *, limit: int = 1000
    ) -> list[Dict[str, Any]]:
        queries = _LIST_QUERIES.get(record_type)
        if queries is None:
            raise ParserV2UnsupportedRecord(
                f"unsupported Parser V2 record list: {record_type}"
            )
        bounded = _limit(limit)
        rows: list[Dict[str, Any]] = []
        with self._conn.cursor() as cursor:
            for query in queries:
                cursor.execute(query, (self.tenant_id, self.project_id, bounded))
                rows.extend(_payload(row[0]) for row in cursor.fetchall())
        rows.sort(key=_record_sort_key)
        return rows[:bounded]

    def get_approved_snapshot(self, snapshot_id: str) -> Dict[str, Any] | None:
        """Look up a created snapshot by identity without a bounded list scan."""

        identity = _required(snapshot_id, "snapshot_id")
        with self._conn.cursor() as cursor:
            cursor.execute(
                _SELECT_SNAPSHOT_IDENTITY_SQL,
                (identity, self.tenant_id, self.project_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        snapshot = _payload(row[1])
        if snapshot.get("eventKind", "created") != "created":
            return None
        return snapshot

    def _append_document(
        self, record: Dict[str, Any], record_hash: str
    ) -> Dict[str, Any]:
        identity = _required(record.get("documentVersionId"), "documentVersionId")
        return self._insert_record(
            _INSERT_DOCUMENT_SQL,
            (
                identity,
                self.tenant_id,
                self.project_id,
                _required(record.get("documentId"), "documentId"),
                _required(record.get("archiveId"), "archiveId"),
                _required(record.get("objectVersion"), "objectVersion"),
                _sha256(record.get("sha256") or record.get("sourceHash"), "sha256"),
                _required(record.get("fileName") or record.get("originalFileName"), "fileName"),
                _required(record.get("mediaType"), "mediaType"),
                int(record.get("sizeBytes") or 0),
                int(record.get("versionNumber") or 0),
                record.get("sourceAuthority") or "unknown",
                _required(record.get("schemaVersion"), "schemaVersion"),
                record.get("supersedesDocumentVersionId"),
                _required(record.get("ingestedBy") or record.get("actorId"), "ingestedBy"),
                _required(record.get("ingestedAt") or record.get("createdAt"), "ingestedAt"),
                _json(record),
            ),
            _SELECT_DOCUMENT_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"document version {identity}",
        )

    def _append_run(self, record: Dict[str, Any], record_hash: str) -> Dict[str, Any]:
        identity = _required(record.get("parsingRunId"), "parsingRunId")
        return self._insert_record(
            _INSERT_RUN_SQL,
            (
                identity, self.tenant_id, self.project_id,
                _required(record.get("documentVersionId"), "documentVersionId"),
                record.get("executionStatus") or "pending",
                record.get("approvalStatus") or "not_requested",
                _required(record.get("parserVersion"), "parserVersion"),
                _required(record.get("ontologyVersion"), "ontologyVersion"),
                _required(record.get("promptVersion"), "promptVersion"),
                _required(record.get("modelVersion"), "modelVersion"),
                _required(record.get("profileId"), "profileId"),
                _required(record.get("profileVersion"), "profileVersion"),
                _sha256(record.get("planHash"), "planHash"),
                list(record.get("requestedModules") or []),
                _required(record.get("idempotencyKey"), "idempotencyKey"),
                _sha256(record.get("inputHash"), "inputHash"),
                record.get("parentParsingRunId"),
                int(record.get("attemptNumber") or 1),
                _required(record.get("requestedAt") or record.get("createdAt"), "requestedAt"),
                _json(record),
            ),
            _SELECT_RUN_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"parsing run {identity}",
        )

    def _append_run_event(
        self, record: Dict[str, Any], record_hash: str
    ) -> Dict[str, Any]:
        identity = _required(record.get("transitionEventId"), "transitionEventId")
        return self._insert_record(
            _INSERT_RUN_EVENT_SQL,
            (
                identity, self.tenant_id, self.project_id,
                _required(record.get("parsingRunId"), "parsingRunId"),
                int(record.get("sequenceNumber") or 0),
                _required(record.get("fromExecutionStatus"), "fromExecutionStatus"),
                _required(record.get("executionStatus"), "executionStatus"),
                record.get("approvalStatus") or "not_requested",
                record.get("orchestrationArtifactHash"), record.get("startedAt"),
                record.get("completedAt"),
                _required(record.get("createdAt"), "createdAt"),
                _required(record.get("actorId"), "actorId"), record_hash,
                _json(record),
            ),
            _SELECT_RUN_EVENT_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"run event {identity}",
        )

    def _append_seal(self, record: Dict[str, Any], record_hash: str) -> Dict[str, Any]:
        identity = _required(record.get("sealId"), "sealId")
        run_id = _required(record.get("parsingRunId"), "parsingRunId")
        with self._conn.cursor() as cursor:
            cursor.execute(
                _SELECT_RUN_CANDIDATE_SET_SQL,
                (self.tenant_id, self.project_id, run_id),
            )
            candidate_descriptors = [
                {"candidateId": str(row[0]), "immutablePayloadHash": str(row[1])}
                for row in cursor.fetchall()
            ]
            cursor.execute(
                _SELECT_RUN_CONFLICT_SET_SQL,
                (self.tenant_id, self.project_id, run_id, run_id),
            )
            conflict_descriptors = [
                {"conflictId": str(row[0]), "immutablePayloadHash": str(row[1])}
                for row in cursor.fetchall()
            ]
        calculated_hash = _stable_hash(
            {
                "parsingRunId": run_id,
                "candidates": candidate_descriptors,
                "conflicts": conflict_descriptors,
            }
        )
        expected_candidate_ids = [item["candidateId"] for item in candidate_descriptors]
        expected_conflict_ids = [item["conflictId"] for item in conflict_descriptors]
        if (
            not expected_candidate_ids
            or list(record.get("candidateIds") or []) != expected_candidate_ids
            or list(record.get("conflictIds") or []) != expected_conflict_ids
            or int(record.get("candidateCount") or 0) != len(expected_candidate_ids)
            or int(record.get("conflictCount") or 0) != len(expected_conflict_ids)
            or not hmac.compare_digest(
                _sha256(record.get("candidateSetHash"), "candidateSetHash"),
                calculated_hash,
            )
        ):
            raise ParserV2ImmutableConflict(
                "candidate-set seal does not match the current database candidate/conflict set"
            )
        return self._insert_record(
            _INSERT_SEAL_SQL,
            (
                identity, self.tenant_id, self.project_id,
                run_id,
                _sha256(record.get("candidateSetHash"), "candidateSetHash"),
                int(record.get("candidateCount") or 0),
                int(record.get("conflictCount") or 0),
                _required(record.get("sealedBy") or record.get("actorId"), "sealedBy"),
                _required(record.get("sealedAt") or record.get("createdAt"), "sealedAt"),
                _json(record),
            ),
            _SELECT_SEAL_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"candidate set seal {identity}",
        )

    def _append_candidate(
        self, record: Dict[str, Any], record_hash: str
    ) -> Dict[str, Any]:
        identity = _required(record.get("candidateId"), "candidateId")
        run_id = _required(record.get("parsingRunId"), "parsingRunId")
        with self._conn.cursor() as cursor:
            cursor.execute(
                _SELECT_CANDIDATE_IDENTITY_SQL,
                (identity, self.tenant_id, self.project_id),
            )
            existing_candidate = cursor.fetchone()
            if existing_candidate is None:
                cursor.execute(
                    _SELECT_RUN_SEAL_SQL,
                    (self.tenant_id, self.project_id, run_id),
                )
                if cursor.fetchone() is not None:
                    raise ParserV2ImmutableConflict(
                        "cannot append a candidate after the parsing run is sealed"
                    )
        findings = [dict(item) for item in record.get("validationFindings") or []]
        persisted = self._insert_record(
            _INSERT_CANDIDATE_SQL,
            (
                identity, self.tenant_id, self.project_id,
                run_id,
                _required(record.get("documentVersionId"), "documentVersionId"),
                _required(record.get("fieldKey") or record.get("canonicalField"), "fieldKey"),
                record.get("entityId"), _json(record.get("rawValue")),
                _json(record.get("normalizedValue")), record.get("unit"),
                _required(record.get("originType"), "originType"),
                record.get("presenceStatus") or "present",
                record.get("reviewStatus") or "pending",
                record.get("approvalStatus") or "not_requested",
                record.get("conflictStatus") or "none", record.get("confidence"),
                record.get("authority") or "unknown", record.get("effectiveDate"),
                record.get("asOfDate"), _required(record.get("parserVersion"), "parserVersion"),
                _required(record.get("ontologyVersion"), "ontologyVersion"),
                _required(record.get("promptVersion"), "promptVersion"),
                _required(record.get("modelVersion"), "modelVersion"),
                _required(record.get("fieldRegistryVersion"), "fieldRegistryVersion"),
                record.get("validationStatus") or "passed",
                list(record.get("validationCodes") or []), len(findings),
                record.get("formulaId"), record.get("formulaVersion"),
                record.get("calculationRunId"), record.get("calculationEngineVersion"),
                record.get("calculationOutputPath"), record.get("calculationOutputHash"),
                record.get("inputSnapshotHash"), record.get("supersedesCandidateId"),
                record_hash, _required(record.get("createdAt"), "createdAt"), _json(record),
            ),
            _SELECT_CANDIDATE_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"candidate {identity}",
        )
        persisted_findings = [
            dict(item) for item in persisted.get("validationFindings") or []
        ]
        persisted_created_at = _required(
            persisted.get("createdAt"), "createdAt"
        )
        with self._conn.cursor() as cursor:
            for ordinal, input_id in enumerate(persisted.get("inputFactIds") or []):
                cursor.execute(
                    _INSERT_CANDIDATE_INPUT_SQL,
                    (
                        self.tenant_id,
                        self.project_id,
                        identity,
                        str(input_id),
                        ordinal,
                        persisted_created_at,
                    ),
                )
            for ordinal, finding in enumerate(persisted_findings):
                normalized = _normalize_finding(persisted, finding, ordinal)
                cursor.execute(
                    _INSERT_VALIDATION_FINDING_SQL,
                    (
                        normalized["findingId"], self.tenant_id, self.project_id,
                        persisted["parsingRunId"], identity,
                        persisted["fieldRegistryVersion"],
                        normalized["code"], normalized["severity"], normalized["message"],
                        normalized["blocking"], normalized["createdAt"],
                        normalized["findingHash"], _json(normalized),
                    ),
                )
        return persisted

    def _append_review(self, record: Dict[str, Any], record_hash: str) -> Dict[str, Any]:
        identity = _required(record.get("reviewDecisionId"), "reviewDecisionId")
        persisted = self._insert_record(
            _INSERT_REVIEW_SQL,
            (
                identity, self.tenant_id, self.project_id,
                _required(record.get("candidateId"), "candidateId"),
                int(record.get("decisionSequence") or 1),
                _required(record.get("action"), "action"),
                _required(record.get("resultingReviewStatus"), "resultingReviewStatus"),
                _required(record.get("resultingApprovalStatus"), "resultingApprovalStatus"),
                _required(record.get("reviewerId") or record.get("actorId"), "reviewerId"),
                _required(record.get("decidedAt") or record.get("createdAt"), "decidedAt"),
                _required(record.get("reason"), "reason"), record_hash, _json(record),
            ),
            _SELECT_REVIEW_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"review decision {identity}",
        )
        with self._conn.cursor() as cursor:
            for conflict_id in record.get("conflictIds") or []:
                cursor.execute(
                    _INSERT_REVIEW_CONFLICT_SQL,
                    (self.tenant_id, self.project_id, identity, str(conflict_id), record["createdAt"]),
                )
        return persisted

    def _append_conflict(
        self, record: Dict[str, Any], record_hash: str
    ) -> Dict[str, Any]:
        identity = _required(record.get("conflictId"), "conflictId")
        candidate_ids = [str(item) for item in record.get("candidateIds") or []]
        persisted = self._insert_record(
            _INSERT_CONFLICT_SQL,
            (
                identity, self.tenant_id, self.project_id, record.get("parsingRunId"),
                _required(record.get("fieldKey") or record.get("canonicalField"), "fieldKey"),
                record.get("entityId"),
                _required(record.get("conflictType") or record.get("conflictCode"), "conflictType"),
                record.get("conflictStatus") or "open", len(candidate_ids), record_hash,
                _required(record.get("detectedAt") or record.get("createdAt"), "detectedAt"),
                record.get("resolutionReviewDecisionId"), record.get("resolvedAt"),
                record.get("supersedesConflictId"), _json(record),
            ),
            _SELECT_CONFLICT_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"source conflict {identity}",
        )
        with self._conn.cursor() as cursor:
            for ordinal, candidate_id in enumerate(candidate_ids):
                cursor.execute(
                    _INSERT_CONFLICT_CANDIDATE_SQL,
                    (self.tenant_id, self.project_id, identity, candidate_id, ordinal, record["createdAt"]),
                )
        return persisted

    def _append_conflict_event(
        self, record: Dict[str, Any], record_hash: str
    ) -> Dict[str, Any]:
        identity = _required(record.get("resolutionEventId"), "resolutionEventId")
        return self._insert_record(
            _INSERT_CONFLICT_EVENT_SQL,
            (
                identity, self.tenant_id, self.project_id,
                _required(record.get("conflictId"), "conflictId"),
                int(record.get("sequenceNumber") or 1),
                _required(record.get("conflictStatus"), "conflictStatus"),
                _required(record.get("resolutionReviewDecisionId"), "resolutionReviewDecisionId"),
                _required(record.get("actorId"), "actorId"),
                _required(record.get("resolvedAt") or record.get("createdAt"), "resolvedAt"),
                record_hash, _json(record),
            ),
            _SELECT_CONFLICT_EVENT_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"source conflict event {identity}",
        )

    def _append_selection(
        self, record: Dict[str, Any], record_hash: str
    ) -> Dict[str, Any]:
        identity = _required(record.get("selectionId"), "selectionId")
        field_key = _required(
            record.get("fieldKey") or record.get("canonicalField"), "fieldKey"
        )
        candidate_id = _required(record.get("candidateId"), "candidateId")
        review_decision_id = _required(
            record.get("reviewDecisionId"), "reviewDecisionId"
        )
        with self._conn.cursor() as cursor:
            cursor.execute(
                _SELECT_CANDIDATE_FOR_SELECTION_SQL,
                (self.tenant_id, self.project_id, candidate_id),
            )
            candidate = cursor.fetchone()
            cursor.execute(
                _SELECT_LATEST_CANDIDATE_REVIEW_SQL,
                (self.tenant_id, self.project_id, candidate_id),
            )
            latest_review = cursor.fetchone()
            cursor.execute(
                _SELECT_OPEN_CANDIDATE_CONFLICT_SQL,
                (self.tenant_id, self.project_id, candidate_id),
            )
            open_conflict = cursor.fetchone()
            if candidate is not None and str(candidate[4]) == "derived":
                cursor.execute(
                    _SELECT_UNSELECTED_DERIVED_INPUT_SQL,
                    (self.tenant_id, self.project_id, candidate_id),
                )
                missing_input_selection = cursor.fetchone()
            else:
                missing_input_selection = None
        if candidate is None:
            raise ParserV2ImmutableConflict(
                "canonical selection candidate is absent from the transaction scope"
            )
        if (
            str(candidate[0]) != field_key
            or candidate[1] != record.get("entityId")
            or str(candidate[2]) != "present"
            or str(candidate[3]) == "blocked"
        ):
            raise ParserV2ImmutableConflict(
                "canonical selection candidate is stale, absent, or blocked"
            )
        if latest_review is None or (
            str(latest_review[0]) != review_decision_id
            or str(latest_review[1]) != "approve"
            or str(latest_review[2]) != "approved"
            or str(latest_review[3]) != "approved"
        ):
            raise ParserV2ImmutableConflict(
                "canonical selection requires the candidate's latest approving review"
            )
        if open_conflict is not None:
            raise ParserV2ImmutableConflict(
                "canonical selection candidate still belongs to an open conflict"
            )
        if missing_input_selection is not None:
            raise ParserV2ImmutableConflict(
                "derived candidate inputs are not all active canonical selections"
            )
        selection_version = int(record.get("selectionVersion") or 1)
        supersedes = record.get("supersedesSelectionId")
        with self._conn.cursor() as cursor:
            cursor.execute(
                _SELECT_LATEST_SELECTION_STATE_SQL,
                (self.tenant_id, self.project_id, field_key, record.get("entityId")),
            )
            latest = cursor.fetchone()
        if latest is None:
            if supersedes is not None or selection_version != 1:
                raise ParserV2ImmutableConflict(
                    "the first field/entity selection must be version 1 without supersedesSelectionId"
                )
        elif str(latest[0]) != identity:
            latest_id, latest_version, is_active = str(latest[0]), int(latest[1]), bool(latest[2])
            if str(supersedes or "") != latest_id or selection_version != latest_version + 1:
                raise ParserV2ImmutableConflict(
                    "selection supersedes/version no longer matches the latest database state"
                )
            if is_active:
                self._required_invalidations.add(
                    ("canonical_selection", latest_id, identity)
                )
        return self._insert_record(
            _INSERT_SELECTION_SQL,
            (
                identity, self.tenant_id, self.project_id,
                field_key,
                record.get("entityId"), candidate_id,
                review_decision_id, record.get("approvalStatus") or "pending",
                record.get("conflictStatus") or "none", _json(record.get("normalizedValue")),
                record.get("unit"), _required(record.get("fieldRegistryVersion"), "fieldRegistryVersion"),
                selection_version, record_hash,
                _required(record.get("selectedBy") or record.get("actorId"), "selectedBy"),
                _required(record.get("selectedAt") or record.get("createdAt"), "selectedAt"),
                record.get("effectiveDate"), record.get("asOfDate"),
                record.get("supersedesSelectionId"), _json(record),
            ),
            _SELECT_SELECTION_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"canonical selection {identity}",
        )

    def _append_snapshot(
        self, record: Dict[str, Any], record_hash: str
    ) -> Dict[str, Any]:
        identity = _required(record.get("snapshotId"), "snapshotId")
        selection_ids = [str(item) for item in record.get("selectionIds") or []]
        run_ids = [str(item) for item in record.get("sourceParsingRunIds") or []]
        self._validate_snapshot_selections(record, selection_ids)
        purpose = _required(record.get("purpose"), "purpose")
        snapshot_version = int(record.get("snapshotVersion") or 1)
        supersedes = record.get("supersedesSnapshotId")
        with self._conn.cursor() as cursor:
            cursor.execute(
                _SELECT_LATEST_SNAPSHOT_STATE_SQL,
                (self.tenant_id, self.project_id, purpose),
            )
            latest = cursor.fetchone()
        if latest is None:
            if supersedes is not None or snapshot_version != 1:
                raise ParserV2ImmutableConflict(
                    "the first purpose snapshot must be version 1 without supersedesSnapshotId"
                )
        elif str(latest[0]) != identity:
            latest_id, latest_version, is_active = str(latest[0]), int(latest[1]), bool(latest[2])
            if str(supersedes or "") != latest_id or snapshot_version != latest_version + 1:
                raise ParserV2ImmutableConflict(
                    "snapshot supersedes/version no longer matches the latest database state"
                )
            if is_active:
                self._required_invalidations.add(
                    ("approved_snapshot", latest_id, identity)
                )
        persisted = self._insert_record(
            _INSERT_SNAPSHOT_SQL,
            (
                identity, self.tenant_id, self.project_id,
                purpose, snapshot_version,
                record.get("approvalStatus") or "approved",
                _required(record.get("inputSchemaVersion"), "inputSchemaVersion"),
                _required(record.get("ontologyVersion"), "ontologyVersion"),
                _required(record.get("fieldRegistryVersion"), "fieldRegistryVersion"),
                record_hash, len(selection_ids), len(run_ids),
                _required(record.get("approvedBy") or record.get("actorId"), "approvedBy"),
                _required(record.get("approvedAt") or record.get("createdAt"), "approvedAt"),
                record.get("supersedesSnapshotId"), _json(record),
            ),
            _SELECT_SNAPSHOT_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"approved snapshot {identity}",
        )
        with self._conn.cursor() as cursor:
            for ordinal, selection_id in enumerate(selection_ids):
                cursor.execute(
                    _INSERT_SNAPSHOT_SELECTION_SQL,
                    (self.tenant_id, self.project_id, identity, selection_id, ordinal, record["createdAt"]),
                )
            for run_id in run_ids:
                cursor.execute(
                    _INSERT_SNAPSHOT_RUN_SQL,
                    (self.tenant_id, self.project_id, identity, run_id, record["createdAt"]),
                )
            # `_insert_record` returns the authoritative stored payload on an
            # idempotent replay. Generated timestamps are excluded from the
            # immutable hash, so the outbox must be derived from that stored
            # payload rather than a racer's later timestamp envelope.
            persisted_selection_ids = [
                str(item) for item in persisted.get("selectionIds") or []
            ]
            self._append_snapshot_outbox(
                cursor,
                persisted,
                identity,
                persisted_selection_ids,
            )
        return persisted

    def _validate_snapshot_selections(
        self, record: Mapping[str, Any], selection_ids: Sequence[str]
    ) -> None:
        if not selection_ids or len(set(selection_ids)) != len(selection_ids):
            raise ParserV2ImmutableConflict(
                "approved snapshot requires unique active selectionIds"
            )
        declared_items = record.get("selections")
        if not isinstance(declared_items, list):
            raise ParserV2ImmutableConflict(
                "approved snapshot must carry its materialized selections"
            )
        declared_by_id = {
            str(item.get("selectionId") or ""): item
            for item in declared_items
            if isinstance(item, Mapping)
        }
        if set(declared_by_id) != set(selection_ids) or "" in declared_by_id:
            raise ParserV2ImmutableConflict(
                "approved snapshot selections do not match selectionIds"
            )
        with self._conn.cursor() as cursor:
            for selection_id in selection_ids:
                cursor.execute(
                    _SELECT_SNAPSHOT_SELECTION_STATE_SQL,
                    (self.tenant_id, self.project_id, selection_id),
                )
                state = cursor.fetchone()
                if state is None or not bool(state[8]) or not bool(state[9]):
                    raise ParserV2ImmutableConflict(
                        "approved snapshot selection is absent, invalidated, or no longer current"
                    )
                stored_payload = _payload(state[7])
                declared = declared_by_id[selection_id]
                comparisons = (
                    (declared.get("fieldKey"), state[1]),
                    (declared.get("entityId"), state[2]),
                    (declared.get("candidateId"), state[3]),
                    (declared.get("reviewDecisionId"), state[4]),
                    (declared.get("selectionVersion"), state[5]),
                    (declared.get("approvalStatus"), state[6]),
                    (declared.get("normalizedValue"), stored_payload.get("normalizedValue")),
                )
                if any(left != right for left, right in comparisons):
                    raise ParserV2ImmutableConflict(
                        "approved snapshot selection payload differs from database authority"
                    )
                cursor.execute(
                    _SELECT_LATEST_CANDIDATE_REVIEW_SQL,
                    (self.tenant_id, self.project_id, state[3]),
                )
                latest_review = cursor.fetchone()
                if latest_review is None or (
                    str(latest_review[0]) != str(state[4])
                    or str(latest_review[1]) != "approve"
                    or str(latest_review[2]) != "approved"
                    or str(latest_review[3]) != "approved"
                ):
                    raise ParserV2ImmutableConflict(
                        "approved snapshot selection no longer has the latest approving review"
                    )

    def _append_snapshot_outbox(
        self,
        cursor: Any,
        record: Mapping[str, Any],
        snapshot_id: str,
        selection_ids: Sequence[str],
    ) -> Dict[str, Any]:
        event_type = "parser.approved_snapshot.created"
        aggregate_type = "parser_approved_snapshot"
        idempotency_key = (
            f"{event_type}:{self.tenant_id}:{self.project_id}:{snapshot_id}"
        )
        event_id = f"parser-v2-outbox:{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"
        occurred_at = _required(
            record.get("approvedAt") or record.get("createdAt"), "approvedAt"
        )
        payload = {
            "schemaVersion": "realytics-runtime-outbox-event-v1",
            "eventId": event_id,
            "tenantId": self.tenant_id,
            "projectId": self.project_id,
            "aggregateType": aggregate_type,
            "aggregateId": snapshot_id,
            "eventType": event_type,
            "idempotencyKey": idempotency_key,
            "status": "pending",
            "payload": {
                "tenantId": self.tenant_id,
                "projectId": self.project_id,
                "snapshotId": snapshot_id,
                "selectionIds": list(selection_ids),
            },
            "attemptCount": 0,
            "maxAttempts": 8,
            "availableAt": occurred_at,
            "leaseOwner": None,
            "leaseExpiresAt": None,
            "lastError": None,
            "createdAt": occurred_at,
            "updatedAt": occurred_at,
            "publishedAt": None,
        }
        cursor.execute(
            _INSERT_SNAPSHOT_OUTBOX_SQL,
            (
                event_id, self.tenant_id, self.project_id, aggregate_type,
                snapshot_id, event_type, idempotency_key, occurred_at,
                occurred_at, occurred_at, _json(payload),
            ),
        )
        inserted = cursor.fetchone()
        if inserted is not None:
            return _payload(inserted[0])
        cursor.execute(_SELECT_SNAPSHOT_OUTBOX_IDENTITY_SQL, (idempotency_key,))
        existing = cursor.fetchone()
        expected_identity = (
            event_id, self.tenant_id, self.project_id, aggregate_type,
            snapshot_id, event_type,
        )
        if existing is None or tuple(str(item or "") for item in existing[:-1]) != expected_identity:
            raise ParserV2ImmutableConflict(
                "approved snapshot outbox idempotency key is bound to another aggregate"
            )
        existing_payload = _payload(existing[-1])
        if not hmac.compare_digest(
            _stable_hash(_immutable_outbox_payload(existing_payload)),
            _stable_hash(_immutable_outbox_payload(payload)),
        ):
            raise ParserV2ImmutableConflict(
                "approved snapshot outbox payload differs from the immutable replay"
            )
        return existing_payload

    def _append_validity_event(
        self, record: Dict[str, Any], record_hash: str
    ) -> Dict[str, Any]:
        identity = _required(record.get("invalidationId"), "invalidationId")
        replacement_selection_id = None
        replacement_snapshot_id = None
        replacement_document_id = None
        if record.get("recordType") == CANONICAL_SELECTION_RECORD_TYPE:
            aggregate_type = "canonical_selection"
            aggregate_id = _required(record.get("selectionId"), "selectionId")
            replacement_selection_id = record.get("replacementSelectionId")
            replacement_document_id = record.get("replacementDocumentVersionId")
            replacement_id = replacement_selection_id or replacement_document_id
        else:
            aggregate_type = "approved_snapshot"
            aggregate_id = _required(record.get("snapshotId"), "snapshotId")
            replacement_snapshot_id = record.get("replacementSnapshotId")
            replacement_document_id = record.get("replacementDocumentVersionId")
            replacement_id = replacement_snapshot_id or replacement_document_id
        with self._conn.cursor() as cursor:
            if aggregate_type == "canonical_selection":
                cursor.execute(
                    _SELECT_VALIDITY_TARGET_SELECTION_SQL,
                    (identity, self.tenant_id, self.project_id, aggregate_id),
                )
            else:
                cursor.execute(
                    _SELECT_VALIDITY_TARGET_SNAPSHOT_SQL,
                    (identity, self.tenant_id, self.project_id, aggregate_id),
                )
            target = cursor.fetchone()
        if target is None or not bool(target[1]):
            raise ParserV2ImmutableConflict(
                "validity-event target is absent or already invalidated"
            )
        target_payload = _payload(target[0])
        if replacement_id:
            with self._conn.cursor() as cursor:
                if aggregate_type == "canonical_selection" and replacement_selection_id:
                    cursor.execute(
                        _SELECT_REPLACEMENT_SELECTION_SQL,
                        (self.tenant_id, self.project_id, replacement_selection_id),
                    )
                    replacement = cursor.fetchone()
                    expected_parent = replacement[0] if replacement else None
                elif aggregate_type == "approved_snapshot" and replacement_snapshot_id:
                    cursor.execute(
                        _SELECT_REPLACEMENT_SNAPSHOT_SQL,
                        (self.tenant_id, self.project_id, replacement_snapshot_id),
                    )
                    replacement = cursor.fetchone()
                    expected_parent = replacement[0] if replacement else None
                else:
                    superseded_document_id = _required(
                        record.get("supersededDocumentVersionId"),
                        "supersededDocumentVersionId",
                    )
                    cursor.execute(
                        _SELECT_REPLACEMENT_DOCUMENT_SQL,
                        (self.tenant_id, self.project_id, replacement_document_id),
                    )
                    replacement = cursor.fetchone()
                    replacement_parent = replacement[0] if replacement else None
                    if aggregate_type == "canonical_selection":
                        target_references_old = (
                            target_payload.get("documentVersionId")
                            == superseded_document_id
                        )
                    else:
                        target_references_old = superseded_document_id in {
                            str(item)
                            for item in target_payload.get("documentVersionIds") or []
                        }
                    expected_parent = (
                        aggregate_id
                        if str(replacement_parent or "") == superseded_document_id
                        and target_references_old
                        else None
                    )
            if str(expected_parent or "") != aggregate_id:
                raise ParserV2ImmutableConflict(
                    "validity-event replacement does not supersede the target aggregate"
                )
        event_type = "superseded" if replacement_id else "invalidated"
        persisted = self._insert_record(
            _INSERT_VALIDITY_EVENT_SQL,
            (
                identity, self.tenant_id, self.project_id, aggregate_type,
                aggregate_id, event_type, replacement_id,
                _required(record.get("actorId"), "actorId"),
                _required(record.get("reason"), "reason"),
                _required(record.get("createdAt"), "createdAt"),
                record_hash, _json(record),
            ),
            _SELECT_VALIDITY_EVENT_IDENTITY_SQL,
            (identity, self.tenant_id, self.project_id),
            record_hash,
            f"validity event {identity}",
        )
        if replacement_id:
            self._seen_invalidations.add(
                (aggregate_type, aggregate_id, str(replacement_id))
            )
        if aggregate_type == "canonical_selection":
            self._selection_invalidations.append(copy.deepcopy(record))
        return persisted

    def _insert_record(
        self,
        insert_sql: str,
        insert_params: Sequence[Any],
        select_sql: str,
        select_params: Sequence[Any],
        record_hash: str,
        identity: str,
    ) -> Dict[str, Any]:
        with self._conn.cursor() as cursor:
            return _insert_and_compare(
                cursor,
                insert_sql=insert_sql,
                insert_params=insert_params,
                select_sql=select_sql,
                select_params=select_params,
                expected_hashes=(record_hash,),
                identity=identity,
            )


def _insert_and_compare(
    cursor: Any,
    *,
    insert_sql: str,
    insert_params: Sequence[Any],
    select_sql: str,
    select_params: Sequence[Any],
    expected_hashes: Sequence[str],
    identity: str,
) -> Dict[str, Any]:
    cursor.execute(insert_sql, tuple(insert_params))
    inserted = cursor.fetchone()
    if inserted is not None:
        return _payload(inserted[0])
    cursor.execute(select_sql, tuple(select_params))
    existing = cursor.fetchone()
    if existing is None:
        raise ParserV2ImmutableConflict(
            f"{identity} conflicts with another immutable uniqueness constraint"
        )
    stored_hashes = tuple(str(item or "") for item in existing[:-1])
    if len(stored_hashes) != len(expected_hashes) or any(
        not hmac.compare_digest(stored, expected)
        for stored, expected in zip(stored_hashes, expected_hashes)
    ):
        raise ParserV2ImmutableConflict(
            f"{identity} already exists with a different immutable hash"
        )
    return _payload(existing[-1])


def _record_hash(record: Mapping[str, Any]) -> str:
    supplied = _sha256(record.get("immutablePayloadHash"), "immutablePayloadHash")
    calculated = _canonical_record_hash(record)
    if not hmac.compare_digest(supplied, calculated):
        raise ParserV2ImmutableConflict(
            "immutablePayloadHash does not match the canonical record payload"
        )
    return supplied


def _canonical_record_hash(record: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in record.items()
        if key not in _GENERATED_TIMESTAMPS.union({"audit", "immutablePayloadHash"})
    }
    return _stable_hash(payload)


def _normalize_finding(
    candidate: Mapping[str, Any], finding: Mapping[str, Any], ordinal: int
) -> Dict[str, Any]:
    base = {
        "tenantId": candidate["tenantId"],
        "projectId": candidate["projectId"],
        "parsingRunId": candidate["parsingRunId"],
        "candidateId": candidate["candidateId"],
        "fieldRegistryVersion": candidate["fieldRegistryVersion"],
        "code": _required(finding.get("code"), "validationFindings.code"),
        "severity": finding.get("severity")
        or ("error" if finding.get("blocking", True) else "warning"),
        "message": _required(finding.get("message"), "validationFindings.message"),
        "blocking": bool(finding.get("blocking", True)),
        "createdAt": finding.get("createdAt") or candidate["createdAt"],
        "metadata": copy.deepcopy(dict(finding.get("metadata") or {})),
    }
    identity_seed = {"candidateId": candidate["candidateId"], "ordinal": ordinal, **base}
    finding_id = str(finding.get("findingId") or f"parser-finding:{_stable_hash(identity_seed)}")
    normalized = {"schemaVersion": "realytics-validation-finding-v2", "findingId": finding_id, **base}
    normalized["findingHash"] = _stable_hash(normalized)
    return normalized


def _scoped_copy(
    value: Mapping[str, Any], tenant_id: str, project_id: str
) -> Dict[str, Any]:
    record = copy.deepcopy(dict(value))
    if str(record.get("tenantId") or "") != tenant_id:
        raise ParserV2TenantScopeError("record tenantId does not match transaction tenant")
    if str(record.get("projectId") or "") != project_id:
        raise ParserV2TenantScopeError("record projectId does not match transaction project")
    return record


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, int, str]:
    timestamp = next(
        (
            str(record.get(key))
            for key in (
                "createdAt", "requestedAt", "ingestedAt", "detectedAt",
                "decidedAt", "selectedAt", "approvedAt", "resolvedAt",
            )
            if record.get(key)
        ),
        "",
    )
    return (
        timestamp,
        int(record.get("sequenceNumber") or record.get("decisionSequence") or 0),
        str(record.get("recordId") or ""),
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _immutable_outbox_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return fields that identify the immutable business event.

    Dispatchers mutate lifecycle fields in ``payload_json`` while claiming,
    retrying, and publishing an event. Those operational transitions must not
    make an identical approved-snapshot replay look like a different event.
    """

    return {
        key: copy.deepcopy(value)
        for key, value in payload.items()
        if key not in _MUTABLE_OUTBOX_PAYLOAD_FIELDS
    }


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _payload(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    if isinstance(value, (str, bytes, bytearray)):
        parsed = json.loads(value)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise ParserV2RepositoryError("PostgreSQL payload_json did not contain an object")


def _mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ParserV2RepositoryError(f"{name} must be an object")
    return copy.deepcopy(dict(value))


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ParserV2RepositoryError(f"{name} is required")
    return text


def _sha256(value: Any, name: str) -> str:
    digest = _required(value, name).lower()
    if len(digest) != 64 or any(character not in _HEX for character in digest):
        raise ParserV2RepositoryError(f"{name} must be a SHA-256 hex digest")
    return digest


def _limit(value: int) -> int:
    return max(1, min(int(value), 10_000))


__all__ = [
    "ParserV2AppendBatch",
    "ParserV2ImmutableConflict",
    "ParserV2PostgresRepository",
    "ParserV2PostgresSession",
    "ParserV2RepositoryError",
    "ParserV2TenantScopeError",
    "ParserV2UnsupportedRecord",
]
