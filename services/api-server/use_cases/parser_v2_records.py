from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence

from parsing_platform.contracts import SourceLocator
from parsing_platform.field_registry import (
    FIELD_REGISTRY_VERSION,
    validate_candidate_against_registry,
)
from storage import (
    list_calculation_run_records,
    list_deal_state_records,
    upsert_deal_state_record,
)


PARSER_V2_SCHEMA_VERSION = "realytics-parser-v2-record-v1"
MAX_CANDIDATE_VALUE_BYTES = 256 * 1024
MAX_CANDIDATE_METADATA_BYTES = 64 * 1024
MAX_CANDIDATE_EVIDENCE_ITEMS = 100
MAX_CANDIDATE_EVIDENCE_BYTES = 512 * 1024
MAX_SANITIZED_ORCHESTRATION_BYTES = 5 * 1024 * 1024

PARSING_RUN_RECORD_TYPE = "parser_v2_parsing_run"
DOCUMENT_VERSION_RECORD_TYPE = "parser_v2_document_version"
PARSED_FACT_CANDIDATE_RECORD_TYPE = "parser_v2_parsed_fact_candidate"
SOURCE_CONFLICT_RECORD_TYPE = "parser_v2_source_conflict"
REVIEW_DECISION_RECORD_TYPE = "parser_v2_review_decision"
CANONICAL_SELECTION_RECORD_TYPE = "parser_v2_canonical_selection"
APPROVED_INPUT_SNAPSHOT_RECORD_TYPE = "parser_v2_approved_input_snapshot"
CANDIDATE_SET_SEAL_RECORD_TYPE = "parser_v2_candidate_set_seal"

_APPROVED_ACTION = "approve"
_REVIEW_RESULTS = {
    "approve": ("approved", "approved"),
    "reject": ("rejected", "rejected"),
    "request_revision": ("needs_revision", "pending"),
}
_RUN_TRANSITIONS = {
    "pending": {"running", "failed", "cancelled"},
    "running": {"completed", "completed_with_warnings", "failed", "cancelled"},
    "completed": set(),
    "completed_with_warnings": set(),
    "failed": set(),
    "cancelled": set(),
}
_TERMINAL_RUN_STATUSES = {
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
}

UpsertRecord = Callable[[str, Dict[str, Any]], Dict[str, Any]]
PersistRecordBatch = Callable[
    [str, List[Dict[str, Any]]], List[Dict[str, Any]]
]
ListRecords = Callable[..., List[Dict[str, Any]]]
ListCalculationRuns = Callable[..., List[Dict[str, Any]]]
Clock = Callable[[], datetime | str]
_UNSET = object()
_BACKEND_HISTORY_READ_LIMIT = 1000


class ParserV2RecordError(ValueError):
    """Base error for an invalid Parser V2 persistence operation."""


class ImmutableRecordConflict(ParserV2RecordError):
    """Raised when a stable Parser V2 ID is reused with different content."""


class ParserV2ReferenceError(ParserV2RecordError):
    """Raised when a Parser V2 record references an absent or incompatible row."""


class ParserV2ApprovalError(ParserV2RecordError):
    """Raised when an unapproved or invalidated value enters an approval artifact."""


class ParserV2RecordStore:
    """Candidate-only persistence boundary for the Parser V2 pipeline.

    The store deliberately writes only first-class deal-state records.  It never
    loads or merges legacy project JSON.  Every creation is immutable and every
    invalidation is appended as a separate event sharing the target record type.
    """

    def __init__(
        self,
        *,
        upsert_fn: UpsertRecord = upsert_deal_state_record,
        persist_batch_fn: PersistRecordBatch | None = None,
        list_fn: ListRecords = list_deal_state_records,
        list_calculation_runs_fn: ListCalculationRuns = list_calculation_run_records,
        clock: Clock | None = None,
    ) -> None:
        self._upsert = upsert_fn
        # When supplied, this callback must persist the complete list in one
        # all-or-nothing transaction and preserve each immutable payload hash.
        # The legacy single-record callback remains the default so existing
        # JSON/SQLite/test backends keep their current behaviour.
        self._persist_batch = persist_batch_fn
        self._list_records = list_fn
        self._list_calculation_runs = list_calculation_runs_fn
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- Parsing runs and immutable document versions ---------------------

    def create_parsing_run(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        document_version_id: str,
        actor_id: str,
        parser_version: str,
        ontology_version: str,
        prompt_version: str,
        model_version: str,
        profile_id: str,
        profile_version: str,
        plan_hash: str,
        input_hash: str,
        execution_status: str = "pending",
        requested_modules: Sequence[str] | None = None,
        idempotency_key: str | None = None,
        parent_parsing_run_id: str | None = None,
        attempt_number: int = 1,
    ) -> Dict[str, Any]:
        if execution_status != "pending":
            raise ParserV2RecordError(
                "a new parsing run must start with execution_status=pending"
            )
        document = self.get_document_version(
            tenant_id, project_id, document_version_id
        )
        if parent_parsing_run_id:
            self.get_parsing_run(tenant_id, project_id, parent_parsing_run_id)
        if parent_parsing_run_id == run_id:
            raise ParserV2RecordError("a parsing run cannot be its own parent")
        if attempt_number < 1:
            raise ParserV2RecordError("attempt_number must be at least 1")
        versions = _versions(
            parser_version, ontology_version, prompt_version, model_version
        )
        normalized_profile_id = _required(profile_id, "profile_id")
        normalized_profile_version = _required(profile_version, "profile_version")
        normalized_plan_hash = _sha256(plan_hash)
        normalized_input_hash = _sha256(input_hash)
        normalized_modules = sorted(
            {str(item) for item in requested_modules or []}
        )
        effective_idempotency_key = idempotency_key or _semantic_id(
            "parse",
            {
                "tenantId": tenant_id,
                "projectId": project_id,
                "documentVersionId": document_version_id,
                **versions,
                "profileId": normalized_profile_id,
                "profileVersion": normalized_profile_version,
                "planHash": normalized_plan_hash,
                "inputHash": normalized_input_hash,
                "requestedModules": normalized_modules,
                "attemptNumber": attempt_number,
            },
        )
        existing_idempotent = next(
            (
                item
                for item in self.list_parsing_runs(tenant_id, project_id)
                if item.get("idempotencyKey") == effective_idempotency_key
            ),
            None,
        )
        if existing_idempotent is not None:
            expected = {
                "documentVersionId": document_version_id,
                "parserVersion": versions["parserVersion"],
                "ontologyVersion": versions["ontologyVersion"],
                "promptVersion": versions["promptVersion"],
                "modelVersion": versions["modelVersion"],
                "profileId": normalized_profile_id,
                "profileVersion": normalized_profile_version,
                "planHash": normalized_plan_hash,
                "inputHash": normalized_input_hash,
                "requestedModules": normalized_modules,
                "attemptNumber": attempt_number,
            }
            if all(
                existing_idempotent.get(key) == value
                for key, value in expected.items()
            ):
                return existing_idempotent
            raise ImmutableRecordConflict(
                "parsing idempotency key is already bound to different inputs"
            )
        record = self._base_record(
            record_type=PARSING_RUN_RECORD_TYPE,
            stable_id=run_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="parsing_run_created",
            versions=versions,
        )
        record.update(
            {
                "parsingRunId": _required(run_id, "run_id"),
                "documentVersionId": document_version_id,
                "key": run_id,
                "executionStatus": _required(execution_status, "execution_status"),
                "approvalStatus": "not_requested",
                "requestedModules": normalized_modules,
                "profileId": normalized_profile_id,
                "profileVersion": normalized_profile_version,
                "planHash": normalized_plan_hash,
                "inputHash": normalized_input_hash,
                "idempotencyKey": effective_idempotency_key,
                "parentParsingRunId": parent_parsing_run_id,
                "attemptNumber": attempt_number,
                "requestedAt": record["createdAt"],
                "sourceHash": document["sha256"],
                "sha256": document["sha256"],
            }
        )
        return self._persist_immutable(record)

    def append_parsing_run_event(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        actor_id: str,
        execution_status: str,
        error_codes: Sequence[str] | None = None,
        orchestration_artifact: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Append a legal execution transition without overwriting the run."""

        current = self.get_parsing_run(tenant_id, project_id, run_id)
        target = _required(execution_status, "execution_status")
        current_status = str(current.get("executionStatus") or "pending")
        events = list(current.get("transitionEvents") or [])
        unique_errors = list(dict.fromkeys(str(item) for item in error_codes or []))
        artifact_payload = copy.deepcopy(dict(orchestration_artifact or {}))
        if artifact_payload:
            _assert_bounded_json(
                artifact_payload,
                label="sanitized orchestration artifact",
                max_bytes=MAX_SANITIZED_ORCHESTRATION_BYTES,
            )
        artifact_hash = _immutable_hash(artifact_payload) if artifact_payload else None
        if current_status == target and events:
            latest = events[-1]
            if latest.get("toExecutionStatus") == target and list(
                latest.get("errorCodes") or []
            ) == unique_errors and latest.get("orchestrationArtifactHash") == artifact_hash:
                return latest
            raise ImmutableRecordConflict(
                "an existing parsing run transition cannot be changed"
            )
        if target not in _RUN_TRANSITIONS.get(current_status, set()):
            raise ParserV2RecordError(
                f"illegal parsing run transition: {current_status} -> {target}"
            )
        if target in {"completed", "cancelled"} and unique_errors:
            raise ParserV2RecordError(
                f"execution_status={target} cannot carry error_codes"
            )
        sequence_number = len(events) + 1
        event_id = _semantic_id(
            "run_transition",
            {
                "parsingRunId": run_id,
                "sequenceNumber": sequence_number,
                "fromExecutionStatus": current_status,
                "toExecutionStatus": target,
                "errorCodes": unique_errors,
                "orchestrationArtifact": artifact_payload or None,
                "orchestrationArtifactHash": artifact_hash,
            },
        )
        record = self._base_record(
            record_type=PARSING_RUN_RECORD_TYPE,
            stable_id=event_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="parsing_run_execution_transitioned",
            versions=_version_context(current),
        )
        started_at = current.get("startedAt")
        if target == "running" and not started_at:
            started_at = record["createdAt"]
        if target in _TERMINAL_RUN_STATUSES and not started_at:
            started_at = record["createdAt"]
        completed_at = record["createdAt"] if target in _TERMINAL_RUN_STATUSES else None
        record.update(
            {
                "eventKind": "execution_transition",
                "transitionEventId": event_id,
                "parsingRunId": run_id,
                "key": run_id,
                "sequenceNumber": sequence_number,
                "fromExecutionStatus": current_status,
                "toExecutionStatus": target,
                "executionStatus": target,
                "approvalStatus": "not_requested",
                "startedAt": started_at,
                "completedAt": completed_at,
                "errorCodes": unique_errors,
                "orchestrationArtifact": artifact_payload or None,
                "orchestrationArtifactHash": artifact_hash,
                "documentVersionId": current["documentVersionId"],
                "sourceHash": current["sourceHash"],
                "sha256": current["sha256"],
                "sourceType": "parser_v2_execution_event",
            }
        )
        return self._persist_immutable(record)

    def create_document_version(
        self,
        *,
        tenant_id: str,
        project_id: str,
        document_id: str,
        document_version_id: str,
        actor_id: str,
        archive_id: str,
        object_version: str,
        source_hash: str,
        media_type: str,
        original_file_name: str,
        size_bytes: int = 0,
        version_number: int = 1,
        source_authority: str = "unknown",
        predecessor_version_id: str | None = None,
    ) -> Dict[str, Any]:
        if int(size_bytes) < 0:
            raise ParserV2RecordError("size_bytes must not be negative")
        if int(version_number) < 1:
            raise ParserV2RecordError("version_number must be at least 1")
        existing_versions = [
            item
            for item in self.list_document_versions(tenant_id, project_id)
            if item.get("documentId") == document_id
        ]
        existing_same_id = next(
            (
                item
                for item in existing_versions
                if item.get("documentVersionId") == document_version_id
            ),
            None,
        )
        if existing_same_id is not None:
            expected = {
                "archiveId": archive_id,
                "objectVersion": object_version,
                "sha256": _sha256(source_hash),
                "versionNumber": int(version_number),
                "supersedesDocumentVersionId": predecessor_version_id,
            }
            if all(existing_same_id.get(key) == value for key, value in expected.items()):
                return existing_same_id
            raise ImmutableRecordConflict(
                "immutable document version ID reused with different content"
            )
        duplicate_object = next(
            (
                item
                for item in self.list_document_versions(tenant_id, project_id)
                if item.get("archiveId") == archive_id
                and item.get("objectVersion") == object_version
            ),
            None,
        )
        if duplicate_object is not None:
            raise ImmutableRecordConflict(
                "archive/object version is already registered as another document version"
            )
        if predecessor_version_id:
            predecessor = self.get_document_version(
                tenant_id, project_id, predecessor_version_id
            )
            if predecessor.get("documentId") != document_id:
                raise ParserV2ReferenceError(
                    "predecessor document version belongs to another document"
                )
            if int(version_number) != int(predecessor.get("versionNumber") or 0) + 1:
                raise ParserV2RecordError(
                    "version_number must increment the predecessor version by one"
                )
            if existing_versions:
                latest = max(
                    existing_versions,
                    key=lambda item: int(item.get("versionNumber") or 0),
                )
                if latest.get("documentVersionId") != predecessor_version_id:
                    raise ParserV2RecordError(
                        "predecessor_version_id must reference the latest document version"
                    )
        elif existing_versions:
            raise ParserV2RecordError(
                "a later document version requires predecessor_version_id"
            )
        elif int(version_number) != 1:
            raise ParserV2RecordError("the first document version must be version 1")
        record = self._base_record(
            record_type=DOCUMENT_VERSION_RECORD_TYPE,
            stable_id=document_version_id,
            tenant_id=tenant_id,
                project_id=project_id,
                actor_id=actor_id,
                action="document_version_registered",
            versions=None,
        )
        record.update(
            {
                "documentVersionId": _required(
                    document_version_id, "document_version_id"
                ),
                "documentId": _required(document_id, "document_id"),
                "key": document_version_id,
                "archiveId": _required(archive_id, "archive_id"),
                "objectVersion": _required(object_version, "object_version"),
                "sourceHash": _sha256(source_hash),
                "sha256": _sha256(source_hash),
                "mediaType": _required(media_type, "media_type"),
                "fileName": _required(original_file_name, "original_file_name"),
                "originalFileName": original_file_name,
                "sizeBytes": int(size_bytes),
                "versionNumber": int(version_number),
                "sourceAuthority": _required(source_authority, "source_authority"),
                "supersedesDocumentVersionId": predecessor_version_id,
                "ingestedAt": record["createdAt"],
                "ingestedBy": actor_id,
                "sourceType": "immutable_object_archive",
            }
        )
        return self._persist_immutable(record)

    # -- Candidates, conflicts, and append-only review decisions ----------

    def create_candidate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        document_version_id: str,
        candidate_id: str,
        actor_id: str,
        canonical_field: str,
        value: Any,
        source_locator: Mapping[str, Any] | None = None,
        evidence: Sequence[Mapping[str, Any]] | None = None,
        unit: str | None = None,
        origin_type: str = "extracted",
        presence_status: str = "present",
        confidence: float | None = None,
        raw_value: Any = None,
        normalized_value: Any = _UNSET,
        target_modules: Sequence[str] | None = None,
        entity_id: str | None = None,
        authority: str = "unknown",
        validation_codes: Sequence[str] | None = None,
        formula_id: str | None = None,
        formula_version: str | None = None,
        input_fact_ids: Sequence[str] | None = None,
        calculation_run_id: str | None = None,
        calculation_engine_version: str | None = None,
        calculation_output_path: str | None = None,
        calculation_output_hash: str | None = None,
        input_snapshot_hash: str | None = None,
        supersedes_candidate_id: str | None = None,
        effective_date: Any = None,
        as_of_date: Any = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        canonical_field = _canonical_field(canonical_field)
        normalized = value if normalized_value is _UNSET else normalized_value
        _assert_bounded_json(
            value if raw_value is None else raw_value,
            label="candidate raw value",
            max_bytes=MAX_CANDIDATE_VALUE_BYTES,
        )
        _assert_bounded_json(
            normalized,
            label="candidate normalized value",
            max_bytes=MAX_CANDIDATE_VALUE_BYTES,
        )
        _assert_bounded_json(
            dict(metadata or {}),
            label="candidate metadata",
            max_bytes=MAX_CANDIDATE_METADATA_BYTES,
        )
        run = self.get_parsing_run(tenant_id, project_id, run_id)
        if self._find_candidate_set_seal(tenant_id, project_id, run_id) is not None:
            raise ParserV2ApprovalError(
                "candidate set is sealed; create a new parsing run for additional candidates"
            )
        document = self.get_document_version(
            tenant_id, project_id, document_version_id
        )
        raw_evidence = list(evidence or [])
        if len(raw_evidence) > MAX_CANDIDATE_EVIDENCE_ITEMS:
            raise ParserV2RecordError("candidate evidence count exceeds the controlled limit")
        evidence_items = [
            _validated_source_locator(
                item,
                document_version_id=document_version_id,
                source_hash=str(document["sha256"]),
            )
            for item in raw_evidence
            if dict(item)
        ]
        if not evidence_items and source_locator:
            evidence_items = [
                _validated_source_locator(
                    source_locator,
                    document_version_id=document_version_id,
                    source_hash=str(document["sha256"]),
                )
            ]
        if run.get("orchestrationArtifact"):
            validate_source_locator_attestations(
                evidence_items,
                run.get("orchestrationArtifact") or {},
            )
        _assert_bounded_json(
            evidence_items,
            label="candidate evidence",
            max_bytes=MAX_CANDIDATE_EVIDENCE_BYTES,
        )
        if run.get("documentVersionId") != document_version_id:
            raise ParserV2ReferenceError(
                "candidate run_id does not match its document version run"
            )
        superseded_candidate: Dict[str, Any] | None = None
        if supersedes_candidate_id:
            superseded_candidate = self.get_candidate(
                tenant_id, project_id, supersedes_candidate_id
            )
            if (
                superseded_candidate.get("canonicalField") != canonical_field
                or superseded_candidate.get("entityId") != entity_id
            ):
                raise ParserV2ReferenceError(
                    "superseded candidate belongs to another canonical field/entity"
                )
            later_candidates = [
                item
                for item in self.list_candidates(tenant_id, project_id)
                if item.get("supersedesCandidateId") == supersedes_candidate_id
                and item.get("candidateId") != candidate_id
            ]
            if later_candidates:
                raise ParserV2ApprovalError(
                    "superseded candidate already has a replacement; use the latest candidate"
                )
        if supersedes_candidate_id == candidate_id:
            raise ParserV2RecordError("a candidate cannot supersede itself")
        if presence_status == "present" and normalized is None:
            raise ParserV2RecordError("present candidate must carry a value")
        if presence_status != "present" and normalized is not None:
            raise ParserV2RecordError("non-present candidate value must be null")
        if presence_status == "present" and not evidence_items:
            raise ParserV2RecordError("present candidate requires source evidence")
        if origin_type == "extracted" and presence_status == "present" and not any(
            item.get("documentVersionId") == document_version_id
            for item in evidence_items
        ):
            raise ParserV2RecordError(
                "an extracted candidate requires evidence bound to its document version"
            )
        if confidence is not None and not 0 <= float(confidence) <= 1:
            raise ParserV2RecordError("confidence must be between 0 and 1")
        calculation_fields = {
            "formula_id": formula_id,
            "formula_version": formula_version,
            "input_fact_ids": list(input_fact_ids or []),
            "calculation_run_id": calculation_run_id,
            "calculation_engine_version": calculation_engine_version,
            "calculation_output_path": calculation_output_path,
            "calculation_output_hash": calculation_output_hash,
            "input_snapshot_hash": input_snapshot_hash,
        }
        if origin_type == "derived" and not all(calculation_fields.values()):
            raise ParserV2RecordError(
                "derived candidate requires formula_id, formula_version, "
                "input_fact_ids, and calculation_run_id"
            )
        if origin_type == "derived":
            unique_input_ids = list(dict.fromkeys(str(item) for item in input_fact_ids or []))
            if len(unique_input_ids) != len(list(input_fact_ids or [])):
                raise ParserV2RecordError("derived input_fact_ids must be unique")
            if candidate_id in unique_input_ids:
                raise ParserV2RecordError("a derived candidate cannot use itself as an input")
            input_candidates = [
                self.get_candidate(tenant_id, project_id, input_id)
                for input_id in unique_input_ids
            ]
            if any(item.get("presenceStatus") != "present" for item in input_candidates):
                raise ParserV2ApprovalError(
                    "derived candidate inputs must be present facts"
                )
            calculation_runs = self._list_calculation_runs(project_id, limit=500)
            calculation_run = next(
                (
                    item
                    for item in calculation_runs
                    if str(item.get("calculationRunId")) == str(calculation_run_id)
                ),
                None,
            )
            if calculation_run is None:
                raise ParserV2ReferenceError(
                    f"calculation run not found: {calculation_run_id}"
                )
            if str(calculation_run.get("status") or "").lower() not in {
                "completed",
                "completed_with_warnings",
                "approved",
            }:
                raise ParserV2ApprovalError(
                    "derived candidate requires a successfully completed calculation run"
                )
            _validate_calculation_run_binding(
                calculation_run=calculation_run,
                tenant_id=tenant_id,
                input_fact_ids=unique_input_ids,
                formula_id=str(formula_id),
                formula_version=str(formula_version),
                calculation_engine_version=str(calculation_engine_version),
                calculation_output_path=str(calculation_output_path),
                calculation_output_hash=str(calculation_output_hash),
                input_snapshot_hash=str(input_snapshot_hash),
                normalized_value=normalized,
            )
            has_calculation_locator = any(
                str(item.get("artifactKey") or "") == str(calculation_run_id)
                and str(item.get("jsonPath") or "")
                == str(calculation_output_path)
                and str(item.get("documentVersionId") or "")
                == str(document_version_id)
                and str(item.get("fileSha256") or "") == str(document["sha256"])
                and str((item.get("metadata") or {}).get("calculationRunId") or "")
                == str(calculation_run_id)
                and str((item.get("metadata") or {}).get("outputHash") or "")
                == str(calculation_output_hash)
                for item in evidence_items
            )
            if not has_calculation_locator:
                evidence_items.append(
                    _validated_source_locator(
                        {
                            "artifactKey": str(calculation_run_id),
                            "jsonPath": str(calculation_output_path),
                            "documentVersionId": str(document_version_id),
                            "fileSha256": str(document["sha256"]),
                            "metadata": {
                                "calculationRunId": str(calculation_run_id),
                                "outputHash": str(calculation_output_hash),
                                "calculationEngineVersion": str(
                                    calculation_engine_version
                                ),
                                "inputSnapshotHash": str(input_snapshot_hash),
                            },
                        },
                        document_version_id=document_version_id,
                        source_hash=str(document["sha256"]),
                    )
                )
            if len(evidence_items) > MAX_CANDIDATE_EVIDENCE_ITEMS:
                raise ParserV2RecordError(
                    "candidate evidence count exceeds the controlled limit"
                )
            _assert_bounded_json(
                evidence_items,
                label="candidate evidence",
                max_bytes=MAX_CANDIDATE_EVIDENCE_BYTES,
            )
        if origin_type != "derived" and any(calculation_fields.values()):
            raise ParserV2RecordError(
                "formula metadata is allowed only for a derived candidate"
            )
        ontology_issues = validate_candidate_against_registry(
            ontology_version=str(run["ontologyVersion"]),
            field_key=canonical_field,
            normalized_value=normalized,
            unit=unit,
            entity_id=entity_id,
            evidence=evidence_items,
            presence_status=presence_status,
            origin_type=origin_type,
        )
        effective_validation_codes = list(
            dict.fromkeys(
                [*(validation_codes or []), *(item.code for item in ontology_issues)]
            )
        )
        ontology_validation_status = (
            "blocked" if any(item.blocking for item in ontology_issues) else "passed"
        )
        record = self._base_record(
            record_type=PARSED_FACT_CANDIDATE_RECORD_TYPE,
            stable_id=candidate_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="parsed_fact_candidate_created",
            versions=_version_context(run),
        )
        record.update(
            {
                "candidateId": _required(candidate_id, "candidate_id"),
                "parsingRunId": run_id,
                "documentId": document["documentId"],
                "documentVersionId": document_version_id,
                "archiveId": document["archiveId"],
                "objectVersion": document["objectVersion"],
                "sourceHash": document["sourceHash"],
                "sha256": document["sha256"],
                "key": _canonical_field(canonical_field),
                "fieldKey": _canonical_field(canonical_field),
                "canonicalField": _canonical_field(canonical_field),
                "value": copy.deepcopy(normalized),
                "rawValue": copy.deepcopy(value if raw_value is None else raw_value),
                "normalizedValue": copy.deepcopy(normalized),
                "unit": unit,
                "entityId": entity_id,
                "originType": _required(origin_type, "origin_type"),
                "presenceStatus": _required(presence_status, "presence_status"),
                "reviewStatus": "pending",
                "conflictStatus": "none",
                "approvalStatus": "not_requested",
                "confidence": confidence,
                "authority": _required(authority, "authority"),
                "sourceType": "parser_v2_candidate",
                "sourceLocator": _authoritative_source_locator(
                    evidence_items,
                    origin_type=origin_type,
                    calculation_run_id=calculation_run_id,
                    calculation_output_path=calculation_output_path,
                    calculation_output_hash=calculation_output_hash,
                ),
                "evidence": evidence_items,
                "validationCodes": effective_validation_codes,
                "validationStatus": ontology_validation_status,
                "validationFindings": [item.to_dict() for item in ontology_issues],
                "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
                "formulaId": formula_id,
                "formulaVersion": formula_version,
                "inputFactIds": list(input_fact_ids or []),
                "calculationRunId": calculation_run_id,
                "calculationEngineVersion": calculation_engine_version,
                "calculationOutputPath": calculation_output_path,
                "calculationOutputHash": calculation_output_hash,
                "inputSnapshotHash": input_snapshot_hash,
                "supersedesCandidateId": supersedes_candidate_id,
                "effectiveDate": _date_wire_value(effective_date),
                "asOfDate": _date_wire_value(as_of_date),
                "metadata": copy.deepcopy(dict(metadata or {})),
                "targetModules": sorted(
                    {str(item) for item in target_modules or []}
                ),
            }
        )
        if supersedes_candidate_id:
            affected_ids = self._candidate_dependency_closure(
                tenant_id, project_id, {str(supersedes_candidate_id)}
            )
            if self._persist_batch is not None:
                invalidations = self._build_active_selection_invalidation_batch_for_candidates(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    candidate_ids=affected_ids,
                    actor_id=actor_id,
                    reason=f"candidate superseded by {candidate_id}",
                )
                return self._persist_immutable_batch([record, *invalidations])[0]
        persisted = self._persist_immutable(record)
        if supersedes_candidate_id:
            self._invalidate_active_selections_for_candidates(
                tenant_id=tenant_id,
                project_id=project_id,
                candidate_ids=affected_ids,
                actor_id=actor_id,
                reason=f"candidate superseded by {candidate_id}",
            )
        return persisted

    def create_source_conflict(
        self,
        *,
        tenant_id: str,
        project_id: str,
        conflict_id: str,
        actor_id: str,
        canonical_field: str,
        candidate_ids: Sequence[str],
        conflict_code: str,
        severity: str = "high",
        notes: str | None = None,
        detection_run_id: str | None = None,
    ) -> Dict[str, Any]:
        canonical_field = _canonical_field(canonical_field)
        unique_ids = list(dict.fromkeys(str(item) for item in candidate_ids))
        if len(unique_ids) < 2:
            raise ParserV2RecordError("source conflict requires at least two candidates")
        candidates = [
            self.get_candidate(tenant_id, project_id, candidate_id)
            for candidate_id in unique_ids
        ]
        if any(item.get("canonicalField") != canonical_field for item in candidates):
            raise ParserV2ReferenceError(
                "all conflicting candidates must target the same canonical field"
            )
        entity_ids = {item.get("entityId") for item in candidates}
        if len(entity_ids) != 1:
            raise ParserV2ReferenceError(
                "all conflicting candidates must target the same entity"
            )
        source_run_ids = sorted(
            {str(item["parsingRunId"]) for item in candidates}
        )
        runs = [
            self.get_parsing_run(tenant_id, project_id, run_id)
            for run_id in source_run_ids
        ]
        if detection_run_id is not None and detection_run_id not in source_run_ids:
            raise ParserV2ReferenceError(
                "detection_run_id must be one of the conflict source runs"
            )
        effective_detection_run_id = detection_run_id or (
            source_run_ids[0] if len(source_run_ids) == 1 else None
        )
        run = runs[0]
        record = self._base_record(
            record_type=SOURCE_CONFLICT_RECORD_TYPE,
            stable_id=conflict_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="source_conflict_created",
            versions=_version_context(run),
        )
        record.update(
            {
                "conflictId": _required(conflict_id, "conflict_id"),
                "key": _required(canonical_field, "canonical_field"),
                "fieldKey": canonical_field,
                "canonicalField": canonical_field,
                "parsingRunId": effective_detection_run_id,
                "detectionParsingRunId": effective_detection_run_id,
                "sourceParsingRunIds": source_run_ids,
                "documentVersionIds": sorted(
                    {str(item["documentVersionId"]) for item in candidates}
                ),
                "entityId": candidates[0].get("entityId"),
                "candidateIds": unique_ids,
                "candidateVersionContexts": [
                    {
                        "candidateId": item["candidateId"],
                        "parsingRunId": item["parsingRunId"],
                        "documentVersionId": item["documentVersionId"],
                        "sourceHash": item["sourceHash"],
                        **_version_context(item),
                    }
                    for item in candidates
                ],
                "conflictCode": _required(conflict_code, "conflict_code"),
                "conflictType": conflict_code,
                "conflictStatus": "open",
                "severity": severity,
                "notes": notes,
                "detectedAt": record["createdAt"],
                "sourceType": "parser_v2_reconciliation",
            }
        )
        affected_ids = self._candidate_dependency_closure(
            tenant_id, project_id, set(unique_ids)
        )
        if self._persist_batch is not None:
            invalidations = self._build_active_selection_invalidation_batch_for_candidates(
                tenant_id=tenant_id,
                project_id=project_id,
                candidate_ids=affected_ids,
                actor_id=actor_id,
                reason=f"open source conflict discovered: {conflict_id}",
            )
            return self._persist_immutable_batch([record, *invalidations])[0]
        persisted = self._persist_immutable(record)
        self._invalidate_active_selections_for_candidates(
            tenant_id=tenant_id,
            project_id=project_id,
            candidate_ids=affected_ids,
            actor_id=actor_id,
            reason=f"open source conflict discovered: {conflict_id}",
        )
        return persisted

    def append_source_conflict_resolution(
        self,
        *,
        tenant_id: str,
        project_id: str,
        conflict_id: str,
        actor_id: str,
        review_decision_id: str,
        expected_decision_sequence: int | None = None,
        resolution_status: str = "resolved",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        """Close a conflict with an explicit, separately auditable event."""

        if resolution_status not in {"resolved", "waived"}:
            raise ParserV2RecordError(
                "resolution_status must be resolved or waived"
            )
        conflict = self.get_source_conflict(tenant_id, project_id, conflict_id)
        decision = self.get_review_decision(
            tenant_id, project_id, review_decision_id
        )
        candidate_decisions = self.list_review_decisions(
            tenant_id,
            project_id,
            candidate_id=str(decision.get("candidateId") or ""),
        )
        latest_decision = candidate_decisions[-1] if candidate_decisions else None
        if latest_decision is None or latest_decision.get("reviewDecisionId") != review_decision_id:
            raise ImmutableRecordConflict(
                "conflict resolution requires the candidate's latest review decision"
            )
        if expected_decision_sequence is not None and (
            len(candidate_decisions) != expected_decision_sequence
            or int(decision.get("decisionSequence") or 0)
            != expected_decision_sequence
        ):
            raise ImmutableRecordConflict(
                "conflict resolution review sequence changed; reload before resolving"
            )
        if conflict_id not in decision.get("conflictIds", []):
            raise ParserV2ReferenceError(
                "conflict resolution decision must explicitly reference the conflict"
            )
        if decision.get("action") != _APPROVED_ACTION:
            raise ParserV2ApprovalError(
                "conflict resolution requires an approved winning-candidate decision"
            )
        selected_candidate_id = str(decision.get("candidateId") or "")
        if selected_candidate_id not in conflict.get("candidateIds", []):
            raise ParserV2ReferenceError(
                "conflict resolution winner must be a member of the conflict"
            )
        existing_events = list(conflict.get("resolutionEvents") or [])
        if conflict.get("conflictStatus") in {"resolved", "waived"}:
            if (
                existing_events
                and existing_events[-1].get("resolutionReviewDecisionId")
                == review_decision_id
                and existing_events[-1].get("conflictStatus") == resolution_status
            ):
                return existing_events[-1]
            raise ImmutableRecordConflict("a closed source conflict cannot be changed")
        event_id = _semantic_id(
            "conflict_resolution",
            {
                "conflictId": conflict_id,
                "reviewDecisionId": review_decision_id,
                "resolutionStatus": resolution_status,
                "selectedCandidateId": selected_candidate_id,
            },
        )
        record = self._base_record(
            record_type=SOURCE_CONFLICT_RECORD_TYPE,
            stable_id=event_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="source_conflict_resolved",
            versions=_version_context(conflict),
        )
        record.update(
            {
                "eventKind": "conflict_resolution",
                "resolutionEventId": event_id,
                "conflictId": conflict_id,
                "key": conflict["fieldKey"],
                "fieldKey": conflict["fieldKey"],
                "canonicalField": conflict["canonicalField"],
                "parsingRunId": conflict["parsingRunId"],
                "detectionParsingRunId": conflict.get("detectionParsingRunId"),
                "sourceParsingRunIds": list(
                    conflict.get("sourceParsingRunIds") or []
                ),
                "documentVersionIds": list(
                    conflict.get("documentVersionIds") or []
                ),
                "candidateIds": list(conflict["candidateIds"]),
                "conflictStatus": resolution_status,
                "resolutionReviewDecisionId": review_decision_id,
                "selectedCandidateId": selected_candidate_id,
                "rejectedCandidateIds": [
                    candidate_id
                    for candidate_id in conflict["candidateIds"]
                    if candidate_id != selected_candidate_id
                ],
                "resolvedAt": record["createdAt"],
                "notes": notes,
                "sourceType": "human_conflict_resolution",
            }
        )
        return self._persist_immutable(record)

    def seal_candidate_set(
        self,
        *,
        tenant_id: str,
        project_id: str,
        run_id: str,
        actor_id: str,
        expected_candidate_ids: Sequence[str],
        expected_conflict_ids: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        """Freeze the complete review package for one successful parsing run.

        The backend-neutral store cannot make this a multi-row database
        transaction.  The explicit seal still prevents a late candidate from
        entering an already reviewed package and provides the hash that the
        typed PostgreSQL repository must later protect with a transaction/lock.
        """

        run = self.get_parsing_run(tenant_id, project_id, run_id)
        if run.get("executionStatus") not in {"completed", "completed_with_warnings"}:
            raise ParserV2ApprovalError(
                "candidate set can be sealed only after a successful parsing run"
            )
        expected_candidates = sorted(
            {str(item) for item in expected_candidate_ids if str(item).strip()}
        )
        if not expected_candidates:
            raise ParserV2ApprovalError(
                "candidate set seal requires at least one expected candidate"
            )
        if len(expected_candidates) != len(list(expected_candidate_ids)):
            raise ParserV2RecordError(
                "expected_candidate_ids must be non-empty and unique"
            )
        expected_conflicts = sorted(
            {str(item) for item in expected_conflict_ids or [] if str(item).strip()}
        )
        if len(expected_conflicts) != len(list(expected_conflict_ids or [])):
            raise ParserV2RecordError(
                "expected_conflict_ids must be non-empty and unique"
            )
        state = self._candidate_set_state(tenant_id, project_id, run_id)
        if state["candidateIds"] != expected_candidates:
            raise ImmutableRecordConflict(
                "candidate set changed; reload the run package before sealing"
            )
        if state["conflictIds"] != expected_conflicts:
            raise ImmutableRecordConflict(
                "source conflict set changed; reload the run package before sealing"
            )
        existing = self._find_candidate_set_seal(tenant_id, project_id, run_id)
        if existing is not None:
            if hmac.compare_digest(
                str(existing.get("candidateSetHash") or ""),
                str(state["candidateSetHash"]),
            ):
                return existing
            raise ImmutableRecordConflict(
                "sealed candidate set no longer matches the current run package; use a new parsing run"
            )
        record = self._base_record(
            record_type=CANDIDATE_SET_SEAL_RECORD_TYPE,
            stable_id=run_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="parser_v2_candidate_set_sealed",
            versions=_version_context(run),
        )
        record.update(
            {
                "sealId": f"candidate-set-seal:{run_id}",
                "parsingRunId": run_id,
                "documentVersionId": run["documentVersionId"],
                "candidateIds": state["candidateIds"],
                "candidateCount": len(state["candidateIds"]),
                "conflictIds": state["conflictIds"],
                "conflictCount": len(state["conflictIds"]),
                "candidateSetHash": state["candidateSetHash"],
                "sealedAt": record["createdAt"],
                "sealedBy": actor_id,
                "sourceType": "parser_v2_review_package_seal",
            }
        )
        return self._persist_immutable(record)

    def append_review_decision(
        self,
        *,
        tenant_id: str,
        project_id: str,
        candidate_id: str,
        actor_id: str,
        action: str,
        reason: str,
        decision_id: str | None = None,
        conflict_ids: Sequence[str] | None = None,
        expected_decision_sequence: int | None = None,
    ) -> Dict[str, Any]:
        if action not in _REVIEW_RESULTS:
            raise ParserV2RecordError(
                f"action must be one of {sorted(_REVIEW_RESULTS)}"
            )
        candidate = self.get_candidate(tenant_id, project_id, candidate_id)
        candidate_run = self.get_parsing_run(
            tenant_id, project_id, str(candidate["parsingRunId"])
        )
        if candidate_run.get("executionStatus") not in {
            "completed",
            "completed_with_warnings",
        }:
            raise ParserV2ApprovalError(
                "review decision requires a successfully completed parsing run"
            )
        if str(candidate.get("actorId") or "") == str(actor_id):
            raise ParserV2ApprovalError(
                "maker-checker separation prohibits reviewing a candidate created by the same actor"
            )
        seal = self._find_candidate_set_seal(
            tenant_id, project_id, str(candidate["parsingRunId"])
        )
        if seal is None:
            raise ParserV2ApprovalError(
                "review decision requires a sealed candidate and conflict set"
            )
        current_state = self._candidate_set_state(
            tenant_id, project_id, str(candidate["parsingRunId"])
        )
        if not hmac.compare_digest(
            str(seal.get("candidateSetHash") or ""),
            str(current_state["candidateSetHash"]),
        ):
            raise ParserV2ApprovalError(
                "candidate or conflict set changed after sealing; use a new parsing run"
            )
        unique_conflict_ids = list(dict.fromkeys(str(item) for item in conflict_ids or []))
        for conflict_id in unique_conflict_ids:
            conflict = self.get_source_conflict(
                tenant_id, project_id, conflict_id
            )
            if candidate_id not in conflict.get("candidateIds", []):
                raise ParserV2ReferenceError(
                    "review decision conflict does not contain the candidate"
                )
        existing_decisions = self.list_review_decisions(
            tenant_id, project_id, candidate_id=candidate_id
        )
        sequence_precondition = (
            expected_decision_sequence
            if expected_decision_sequence is not None
            else len(existing_decisions)
        )
        stable_decision_id = decision_id or _semantic_id(
            "decision",
            {
                "tenantId": tenant_id,
                "projectId": project_id,
                "candidateId": candidate_id,
                "actorId": actor_id,
                "action": action,
                "reason": reason,
                "conflictIds": unique_conflict_ids,
                "expectedDecisionSequence": sequence_precondition,
            },
        )
        existing_same_id = next(
            (
                item
                for item in existing_decisions
                if item.get("reviewDecisionId") == stable_decision_id
            ),
            None,
        )
        if (
            existing_same_id is None
            and expected_decision_sequence is not None
            and len(existing_decisions) != expected_decision_sequence
        ):
            raise ImmutableRecordConflict(
                "review decision sequence changed; reload before appending a decision"
            )
        decision_sequence = int(
            (existing_same_id or {}).get("decisionSequence")
            or (len(existing_decisions) + 1)
        )
        record = self._base_record(
            record_type=REVIEW_DECISION_RECORD_TYPE,
            stable_id=stable_decision_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="review_decision_appended",
            versions=_version_context(candidate),
        )
        record.update(
            {
                "reviewDecisionId": stable_decision_id,
                "candidateId": candidate_id,
                "conflictIds": unique_conflict_ids,
                "parsingRunId": candidate["parsingRunId"],
                "documentId": candidate["documentId"],
                "documentVersionId": candidate["documentVersionId"],
                "sourceHash": candidate["sourceHash"],
                "key": candidate["canonicalField"],
                "fieldKey": candidate["canonicalField"],
                "canonicalField": candidate["canonicalField"],
                "action": action,
                "resultingReviewStatus": _REVIEW_RESULTS[action][0],
                "resultingApprovalStatus": _REVIEW_RESULTS[action][1],
                "reason": _required(reason, "reason"),
                "reviewStatus": _REVIEW_RESULTS[action][0],
                "approvalStatus": _REVIEW_RESULTS[action][1],
                "reviewerId": actor_id,
                "decidedAt": record["createdAt"],
                "decisionSequence": decision_sequence,
                "sourceType": "human_review",
            }
        )
        if self._persist_batch is not None and existing_same_id is None:
            invalidations = self._build_active_selection_invalidation_batch_for_candidates(
                tenant_id=tenant_id,
                project_id=project_id,
                candidate_ids={candidate_id},
                actor_id=actor_id,
                reason=f"candidate received newer review decision {stable_decision_id}",
            )
            return self._persist_immutable_batch([record, *invalidations])[0]

        persisted = self._persist_immutable(record)
        if existing_same_id is None:
            for selection in self.list_canonical_selections(
                tenant_id, project_id, active_only=True
            ):
                if selection.get("candidateId") != candidate_id:
                    continue
                self._append_selection_invalidation_event(
                    selection=selection,
                    actor_id=actor_id,
                    reason=(
                        f"candidate received newer review decision {stable_decision_id}"
                    ),
                )
        return persisted

    # -- Approved projections ---------------------------------------------

    def create_canonical_selection(
        self,
        *,
        tenant_id: str,
        project_id: str,
        selection_id: str,
        actor_id: str,
        candidate_id: str,
        review_decision_id: str,
        selection_version: int = 1,
        supersedes_selection_id: str | None = None,
    ) -> Dict[str, Any]:
        existing_selection = next(
            (
                item
                for item in self.list_canonical_selections(tenant_id, project_id)
                if item.get("selectionId") == selection_id
            ),
            None,
        )
        if existing_selection is not None:
            if (
                existing_selection.get("candidateId") == candidate_id
                and existing_selection.get("reviewDecisionId") == review_decision_id
                and int(existing_selection.get("selectionVersion") or 0)
                == int(selection_version)
                and existing_selection.get("supersedesSelectionId")
                == supersedes_selection_id
            ):
                return existing_selection
            raise ImmutableRecordConflict(
                "immutable canonical selection ID reused with different content"
            )
        candidate = self.get_candidate(tenant_id, project_id, candidate_id)
        decision = self.get_review_decision(
            tenant_id, project_id, review_decision_id
        )
        if decision.get("candidateId") != candidate_id:
            raise ParserV2ReferenceError(
                "canonical selection decision belongs to another candidate"
            )
        decisions = self.list_review_decisions(
            tenant_id, project_id, candidate_id=candidate_id
        )
        if not decisions or decisions[-1].get("reviewDecisionId") != review_decision_id:
            raise ParserV2ApprovalError(
                "canonical selection must reference the latest review decision"
            )
        if decision.get("action") != _APPROVED_ACTION:
            raise ParserV2ApprovalError(
                "canonical selection requires an approved review decision"
            )
        if candidate.get("presenceStatus") != "present":
            raise ParserV2ApprovalError("only a present candidate can be selected")
        if candidate.get("validationStatus") == "blocked":
            codes = ", ".join(candidate.get("validationCodes") or [])
            raise ParserV2ApprovalError(
                "candidate has unresolved blocking validation findings"
                + (f": {codes}" if codes else "")
            )
        if candidate.get("conflictStatus") == "open":
            raise ParserV2ApprovalError("a candidate with an open conflict cannot be selected")
        open_conflict_ids = [
            str(item["conflictId"])
            for item in self.list_source_conflicts(tenant_id, project_id)
            if item.get("conflictStatus") == "open"
            and candidate_id in item.get("candidateIds", [])
        ]
        if open_conflict_ids:
            raise ParserV2ApprovalError(
                "canonical selection is blocked by open source conflicts: "
                + ", ".join(open_conflict_ids)
            )
        losing_conflict_ids = [
            str(item["conflictId"])
            for item in self.list_source_conflicts(tenant_id, project_id)
            if item.get("conflictStatus") in {"resolved", "waived"}
            and candidate_id in item.get("candidateIds", [])
            and item.get("selectedCandidateId") != candidate_id
        ]
        if losing_conflict_ids:
            raise ParserV2ApprovalError(
                "candidate is not the approved winner of source conflicts: "
                + ", ".join(losing_conflict_ids)
            )
        if candidate.get("originType") == "derived":
            active_input_candidate_ids = {
                str(item.get("candidateId"))
                for item in self.list_canonical_selections(
                    tenant_id, project_id, active_only=True
                )
                if item.get("approvalStatus") == "approved"
            }
            unapproved_inputs = [
                str(input_id)
                for input_id in candidate.get("inputFactIds") or []
                if str(input_id) not in active_input_candidate_ids
            ]
            if unapproved_inputs:
                raise ParserV2ApprovalError(
                    "derived candidate depends on inputs without active approved selections: "
                    + ", ".join(unapproved_inputs)
                )
        previous: Dict[str, Any] | None = None
        historical_for_field = [
            item
            for item in self.list_canonical_selections(tenant_id, project_id)
            if item.get("fieldKey") == candidate.get("fieldKey")
            and item.get("entityId") == candidate.get("entityId")
        ]
        latest_historical = (
            max(
                historical_for_field,
                key=lambda item: int(item.get("selectionVersion") or 0),
            )
            if historical_for_field
            else None
        )
        if supersedes_selection_id:
            previous = next(
                (
                    item
                    for item in historical_for_field
                    if item.get("selectionId") == supersedes_selection_id
                ),
                None,
            )
            if previous is None:
                raise ParserV2ReferenceError(
                    f"superseded selection not found: {supersedes_selection_id}"
                )
            if (
                previous.get("fieldKey") != candidate.get("fieldKey")
                or previous.get("entityId") != candidate.get("entityId")
            ):
                raise ParserV2ReferenceError(
                    "superseded selection belongs to another canonical field/entity"
                )
            if latest_historical is not None and latest_historical.get(
                "selectionId"
            ) != previous.get("selectionId"):
                raise ParserV2ApprovalError(
                    "supersedesSelectionId must reference the latest historical selection"
                )
            expected_selection_version = int(previous.get("selectionVersion") or 0) + 1
            if int(selection_version) != expected_selection_version:
                raise ParserV2RecordError(
                    "selection_version must increment the superseded selection version"
                )
        else:
            competing_active = [
                item
                for item in self.list_canonical_selections(
                    tenant_id, project_id, active_only=True
                )
                if item.get("fieldKey") == candidate.get("fieldKey")
                and item.get("entityId") == candidate.get("entityId")
            ]
            if competing_active:
                raise ParserV2ApprovalError(
                    "an active canonical selection already exists; supersedesSelectionId is required"
                )
            if latest_historical is not None:
                raise ParserV2ApprovalError(
                    "a historical canonical selection exists; supersedesSelectionId is required"
                )
            if int(selection_version) != 1:
                raise ParserV2RecordError(
                    "a new canonical field/entity selection must start at version 1"
                )
        if supersedes_selection_id == selection_id:
            raise ParserV2RecordError("a selection cannot supersede itself")
        record = self._base_record(
            record_type=CANONICAL_SELECTION_RECORD_TYPE,
            stable_id=selection_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="canonical_selection_created",
            versions=_version_context(candidate),
        )
        record.update(
            {
                "selectionId": _required(selection_id, "selection_id"),
                "candidateId": candidate_id,
                "reviewDecisionId": review_decision_id,
                "parsingRunId": candidate["parsingRunId"],
                "documentId": candidate["documentId"],
                "documentVersionId": candidate["documentVersionId"],
                "archiveId": candidate["archiveId"],
                "objectVersion": candidate["objectVersion"],
                "sourceHash": candidate["sourceHash"],
                "sha256": candidate["sha256"],
                "key": candidate["canonicalField"],
                "fieldKey": candidate["canonicalField"],
                "canonicalField": candidate["canonicalField"],
                "value": copy.deepcopy(candidate.get("value")),
                "normalizedValue": copy.deepcopy(candidate.get("normalizedValue")),
                "unit": candidate.get("unit"),
                "entityId": candidate.get("entityId"),
                "authority": candidate.get("authority"),
                "originType": candidate.get("originType"),
                "formulaId": candidate.get("formulaId"),
                "formulaVersion": candidate.get("formulaVersion"),
                "inputFactIds": list(candidate.get("inputFactIds") or []),
                "calculationRunId": candidate.get("calculationRunId"),
                "calculationEngineVersion": candidate.get(
                    "calculationEngineVersion"
                ),
                "calculationOutputPath": candidate.get("calculationOutputPath"),
                "calculationOutputHash": candidate.get("calculationOutputHash"),
                "inputSnapshotHash": candidate.get("inputSnapshotHash"),
                "effectiveDate": candidate.get("effectiveDate"),
                "asOfDate": candidate.get("asOfDate"),
                "sourceLocator": copy.deepcopy(candidate.get("sourceLocator") or {}),
                "fieldRegistryVersion": candidate.get("fieldRegistryVersion"),
                "approvalStatus": "approved",
                "conflictStatus": candidate.get("conflictStatus") or "none",
                "validityStatus": "active",
                "selectionVersion": max(1, int(selection_version)),
                "selectedBy": actor_id,
                "selectedAt": record["createdAt"],
                "supersedesSelectionId": supersedes_selection_id,
                "eventKind": "created",
                "sourceType": "approved_parser_v2_candidate",
            }
        )
        if self._persist_batch is not None:
            records = [record]
            if previous is not None:
                records.extend(
                    self._build_selection_invalidation_batch(
                        selection=previous,
                        actor_id=actor_id,
                        reason=f"superseded by canonical selection {selection_id}",
                        replacement_selection_id=selection_id,
                    )
                )
            return self._persist_immutable_batch(records)[0]

        persisted = self._persist_immutable(record)
        if previous is not None:
            self._append_selection_invalidation_event(
                selection=previous,
                actor_id=actor_id,
                reason=f"superseded by canonical selection {selection_id}",
                replacement_selection_id=selection_id,
            )
        return persisted

    def create_approved_input_snapshot(
        self,
        *,
        tenant_id: str,
        project_id: str,
        snapshot_id: str,
        actor_id: str,
        selection_ids: Sequence[str],
        purpose: str,
        supersedes_snapshot_id: str | None = None,
    ) -> Dict[str, Any]:
        unique_ids = list(dict.fromkeys(str(item) for item in selection_ids))
        existing_snapshot = next(
            (
                item
                for item in self.list_approved_input_snapshots(tenant_id, project_id)
                if item.get("snapshotId") == snapshot_id
            ),
            None,
        )
        if existing_snapshot is not None:
            if (
                list(existing_snapshot.get("selectionIds") or []) == unique_ids
                and existing_snapshot.get("purpose") == purpose
                and existing_snapshot.get("supersedesSnapshotId")
                == supersedes_snapshot_id
            ):
                return existing_snapshot
            raise ImmutableRecordConflict(
                "immutable approved snapshot ID reused with different content"
            )
        if not unique_ids:
            raise ParserV2ApprovalError(
                "approved input snapshot requires at least one selection"
            )
        selections = [
            self._get_active_selection(tenant_id, project_id, selection_id)
            for selection_id in unique_ids
        ]
        selected_candidate_ids = {
            str(item.get("candidateId") or "") for item in selections
        }
        missing_dependency_ids = sorted(
            {
                str(input_candidate_id)
                for item in selections
                for input_candidate_id in item.get("inputFactIds") or []
                if str(input_candidate_id) not in selected_candidate_ids
            }
        )
        if missing_dependency_ids:
            raise ParserV2ApprovalError(
                "approved snapshot must include the active selections for every derived input: "
                + ", ".join(missing_dependency_ids)
            )
        fields = [
            (str(item["fieldKey"]), item.get("entityId")) for item in selections
        ]
        if len(fields) != len(set(fields)):
            raise ParserV2ApprovalError(
                "approved input snapshot cannot contain multiple selections for one field"
            )
        snapshot_reviewer_ids: set[str] = set()
        for selection in selections:
            decision = self.get_review_decision(
                tenant_id, project_id, str(selection["reviewDecisionId"])
            )
            snapshot_reviewer_ids.add(str(decision.get("reviewerId") or ""))
            if decision.get("action") != _APPROVED_ACTION:
                raise ParserV2ApprovalError(
                    "snapshot selection no longer has an approved review decision"
                )
            latest = self.list_review_decisions(
                tenant_id,
                project_id,
                candidate_id=str(selection["candidateId"]),
            )
            if not latest or latest[-1].get("reviewDecisionId") != decision.get(
                "reviewDecisionId"
            ):
                raise ParserV2ApprovalError(
                    "snapshot selection is superseded by a newer review decision"
                )
        snapshot_reviewer_ids.discard("")
        if str(actor_id) in snapshot_reviewer_ids:
            raise ParserV2ApprovalError(
                "four-eyes control requires the snapshot approver to differ from every candidate reviewer"
            )
        previous_snapshot: Dict[str, Any] | None = None
        if supersedes_snapshot_id:
            previous_snapshot = next(
                (
                    item
                    for item in self.list_approved_input_snapshots(
                        tenant_id, project_id
                    )
                    if item.get("snapshotId") == supersedes_snapshot_id
                ),
                None,
            )
            if previous_snapshot is None:
                raise ParserV2ReferenceError(
                    f"superseded snapshot not found: {supersedes_snapshot_id}"
                )
            if previous_snapshot.get("purpose") != purpose:
                raise ParserV2ReferenceError(
                    "superseded snapshot belongs to another purpose"
                )
            historical_for_purpose = [
                item
                for item in self.list_approved_input_snapshots(tenant_id, project_id)
                if item.get("purpose") == purpose
            ]
            latest_for_purpose = max(
                historical_for_purpose,
                key=lambda item: int(item.get("snapshotVersion") or 0),
            )
            if latest_for_purpose.get("snapshotId") != previous_snapshot.get(
                "snapshotId"
            ):
                raise ParserV2ApprovalError(
                    "supersedesSnapshotId must reference the latest historical snapshot"
                )
            snapshot_version = int(previous_snapshot.get("snapshotVersion") or 1) + 1
        else:
            historical_for_purpose = [
                item
                for item in self.list_approved_input_snapshots(tenant_id, project_id)
                if item.get("purpose") == purpose
            ]
            competing_snapshots = [
                item
                for item in historical_for_purpose
                if item.get("validityStatus") == "active"
            ]
            if competing_snapshots:
                raise ParserV2ApprovalError(
                    "an active approved snapshot already exists for this purpose; "
                    "supersedesSnapshotId is required"
                )
            if historical_for_purpose:
                raise ParserV2ApprovalError(
                    "a historical approved snapshot exists for this purpose; "
                    "supersedesSnapshotId is required"
                )
            snapshot_version = 1
        if supersedes_snapshot_id == snapshot_id:
            raise ParserV2RecordError("a snapshot cannot supersede itself")
        version_contexts = [_version_context(item) for item in selections]
        ontology_versions = {item["ontologyVersion"] for item in version_contexts}
        if len(ontology_versions) != 1:
            raise ParserV2ApprovalError(
                "approved snapshot cannot mix ontology versions without a governed migration"
            )
        record = self._base_record(
            record_type=APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
            stable_id=snapshot_id,
            tenant_id=tenant_id,
            project_id=project_id,
            actor_id=actor_id,
            action="approved_input_snapshot_created",
            versions=version_contexts[0],
        )
        source_documents = _unique_dicts(
            [
                {
                    "documentId": item["documentId"],
                    "documentVersionId": item["documentVersionId"],
                    "archiveId": item["archiveId"],
                    "objectVersion": item["objectVersion"],
                    "sourceHash": item["sourceHash"],
                    "sha256": item["sha256"],
                }
                for item in selections
            ]
        )
        input_items = [
            {
                "fieldKey": item["fieldKey"],
                "canonicalField": item["canonicalField"],
                "entityId": item.get("entityId"),
                "normalizedValue": copy.deepcopy(item.get("normalizedValue")),
                "unit": item.get("unit"),
                "authority": item.get("authority"),
                "candidateId": item["candidateId"],
                "selectionId": item["selectionId"],
                "reviewDecisionId": item["reviewDecisionId"],
                "parsingRunId": item["parsingRunId"],
                "documentVersionId": item["documentVersionId"],
                "sourceHash": item["sourceHash"],
                "sha256": item["sha256"],
                "sourceLocator": copy.deepcopy(item.get("sourceLocator") or {}),
                "originType": item.get("originType"),
                "formulaId": item.get("formulaId"),
                "formulaVersion": item.get("formulaVersion"),
                "inputFactIds": list(item.get("inputFactIds") or []),
                "calculationRunId": item.get("calculationRunId"),
                "calculationEngineVersion": item.get("calculationEngineVersion"),
                "calculationOutputPath": item.get("calculationOutputPath"),
                "calculationOutputHash": item.get("calculationOutputHash"),
                "inputSnapshotHash": item.get("inputSnapshotHash"),
                "effectiveDate": item.get("effectiveDate"),
                "asOfDate": item.get("asOfDate"),
                "fieldRegistryVersion": item.get("fieldRegistryVersion"),
                "versionContext": _version_context(item),
            }
            for item in selections
        ]
        record.update(
            {
                "snapshotId": _required(snapshot_id, "snapshot_id"),
                "snapshotVersion": snapshot_version,
                "key": snapshot_id,
                "purpose": _required(purpose, "purpose"),
                "inputSchemaVersion": "realytics-parser-approved-inputs-v1",
                "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
                "selectionIds": unique_ids,
                "selections": [_selection_wire_payload(item) for item in selections],
                "documentVersionIds": sorted(
                    {str(item["documentVersionId"]) for item in selections}
                ),
                "sourceHashes": sorted(
                    {str(item["sourceHash"]) for item in selections}
                ),
                "sourceDocuments": source_documents,
                "inputItems": input_items,
                "inputs": {
                    str(item["fieldKey"]): copy.deepcopy(item.get("normalizedValue"))
                    for item in selections
                    if item.get("entityId") is None
                },
                "inputsByEntity": _inputs_by_entity(selections),
                "versionContexts": _unique_dicts(version_contexts),
                "sourceParsingRunIds": sorted(
                    {str(item["parsingRunId"]) for item in selections}
                ),
                "approvedBy": actor_id,
                "approvedAt": record["createdAt"],
                "supersedesSnapshotId": supersedes_snapshot_id,
                "approvalStatus": "approved",
                "validityStatus": "active",
                "eventKind": "created",
                "sourceType": "approved_parser_v2_snapshot",
            }
        )
        if self._persist_batch is not None:
            records = [record]
            if (
                previous_snapshot is not None
                and previous_snapshot.get("validityStatus") == "active"
            ):
                records.append(
                    self._build_snapshot_invalidation_record(
                        snapshot=previous_snapshot,
                        actor_id=actor_id,
                        reason=f"superseded by approved snapshot {snapshot_id}",
                        replacement_snapshot_id=snapshot_id,
                    )
                )
            return self._persist_immutable_batch(records)[0]

        persisted = self._persist_immutable(record)
        if (
            previous_snapshot is not None
            and previous_snapshot.get("validityStatus") == "active"
        ):
            self._append_snapshot_invalidation_event(
                snapshot=previous_snapshot,
                actor_id=actor_id,
                reason=f"superseded by approved snapshot {snapshot_id}",
                replacement_snapshot_id=snapshot_id,
            )
        return persisted

    def _append_selection_invalidation_event(
        self,
        *,
        selection: Mapping[str, Any],
        actor_id: str,
        reason: str,
        replacement_selection_id: str | None = None,
        replacement_document_version_id: str | None = None,
    ) -> Dict[str, Any]:
        if self._persist_batch is not None:
            records = self._build_selection_invalidation_batch(
                selection=selection,
                actor_id=actor_id,
                reason=reason,
                replacement_selection_id=replacement_selection_id,
                replacement_document_version_id=replacement_document_version_id,
            )
            return self._persist_immutable_batch(records)[0]

        record = self._build_selection_invalidation_record(
            selection=selection,
            actor_id=actor_id,
            reason=reason,
            replacement_selection_id=replacement_selection_id,
            replacement_document_version_id=replacement_document_version_id,
        )
        persisted = self._persist_immutable(record)
        for snapshot in self.list_approved_input_snapshots(
            str(selection["tenantId"]),
            str(selection["projectId"]),
            active_only=True,
        ):
            if selection["selectionId"] in snapshot.get("selectionIds", []):
                self._append_snapshot_invalidation_event(
                    snapshot=snapshot,
                    actor_id=actor_id,
                    reason=(
                        f"contains invalidated canonical selection {selection['selectionId']}"
                    ),
                )
        return persisted

    def _build_selection_invalidation_batch(
        self,
        *,
        selection: Mapping[str, Any],
        actor_id: str,
        reason: str,
        replacement_selection_id: str | None = None,
        replacement_document_version_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        records = [
            self._build_selection_invalidation_record(
                selection=selection,
                actor_id=actor_id,
                reason=reason,
                replacement_selection_id=replacement_selection_id,
                replacement_document_version_id=replacement_document_version_id,
            )
        ]
        for snapshot in self.list_approved_input_snapshots(
            str(selection["tenantId"]),
            str(selection["projectId"]),
            active_only=True,
        ):
            if selection["selectionId"] not in snapshot.get("selectionIds", []):
                continue
            records.append(
                self._build_snapshot_invalidation_record(
                    snapshot=snapshot,
                    actor_id=actor_id,
                    reason=(
                        f"contains invalidated canonical selection {selection['selectionId']}"
                    ),
                )
            )
        return records

    def _build_selection_invalidation_record(
        self,
        *,
        selection: Mapping[str, Any],
        actor_id: str,
        reason: str,
        replacement_selection_id: str | None = None,
        replacement_document_version_id: str | None = None,
    ) -> Dict[str, Any]:
        event_id = _semantic_id(
            "selection_invalidation",
            {
                "selectionId": selection["selectionId"],
                "replacementSelectionId": replacement_selection_id,
                "replacementDocumentVersionId": replacement_document_version_id,
                "reason": reason,
            },
        )
        record = self._base_record(
            record_type=CANONICAL_SELECTION_RECORD_TYPE,
            stable_id=event_id,
            tenant_id=str(selection["tenantId"]),
            project_id=str(selection["projectId"]),
            actor_id=actor_id,
            action="canonical_selection_invalidated",
            versions=_version_context(selection),
        )
        record.update(
            {
                "eventKind": "invalidated",
                "invalidationId": event_id,
                "selectionId": selection["selectionId"],
                "invalidatesRecordId": selection["recordId"],
                "key": selection["fieldKey"],
                "fieldKey": selection["fieldKey"],
                "canonicalField": selection["fieldKey"],
                "candidateId": selection["candidateId"],
                "replacementSelectionId": replacement_selection_id,
                "replacementDocumentVersionId": replacement_document_version_id,
                "reason": _required(reason, "reason"),
                "approvalStatus": "superseded",
                "validityStatus": "invalidated",
                "sourceType": "parser_v2_approval_invalidation",
            }
        )
        return record

    def _append_snapshot_invalidation_event(
        self,
        *,
        snapshot: Mapping[str, Any],
        actor_id: str,
        reason: str,
        replacement_snapshot_id: str | None = None,
        replacement_document_version_id: str | None = None,
    ) -> Dict[str, Any]:
        record = self._build_snapshot_invalidation_record(
            snapshot=snapshot,
            actor_id=actor_id,
            reason=reason,
            replacement_snapshot_id=replacement_snapshot_id,
            replacement_document_version_id=replacement_document_version_id,
        )
        if self._persist_batch is not None:
            return self._persist_immutable_batch([record])[0]
        return self._persist_immutable(record)

    def _build_snapshot_invalidation_record(
        self,
        *,
        snapshot: Mapping[str, Any],
        actor_id: str,
        reason: str,
        replacement_snapshot_id: str | None = None,
        replacement_document_version_id: str | None = None,
    ) -> Dict[str, Any]:
        event_id = _semantic_id(
            "snapshot_invalidation",
            {
                "snapshotId": snapshot["snapshotId"],
                "replacementSnapshotId": replacement_snapshot_id,
                "replacementDocumentVersionId": replacement_document_version_id,
                "reason": reason,
            },
        )
        record = self._base_record(
            record_type=APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
            stable_id=event_id,
            tenant_id=str(snapshot["tenantId"]),
            project_id=str(snapshot["projectId"]),
            actor_id=actor_id,
            action="approved_input_snapshot_invalidated",
            versions=_version_context(snapshot),
        )
        record.update(
            {
                "eventKind": "invalidated",
                "invalidationId": event_id,
                "snapshotId": snapshot["snapshotId"],
                "invalidatesRecordId": snapshot["recordId"],
                "key": snapshot["snapshotId"],
                "selectionIds": list(snapshot.get("selectionIds") or []),
                "replacementSnapshotId": replacement_snapshot_id,
                "replacementDocumentVersionId": replacement_document_version_id,
                "reason": _required(reason, "reason"),
                "approvalStatus": "superseded",
                "validityStatus": "invalidated",
                "sourceType": "parser_v2_approval_invalidation",
            }
        )
        return record

    def _candidate_dependency_closure(
        self,
        tenant_id: str,
        project_id: str,
        seed_candidate_ids: set[str],
    ) -> set[str]:
        affected = {str(item) for item in seed_candidate_ids}
        candidates = self.list_candidates(tenant_id, project_id)
        changed = True
        while changed:
            changed = False
            for candidate in candidates:
                candidate_id = str(candidate.get("candidateId") or "")
                if not candidate_id or candidate_id in affected:
                    continue
                inputs = {str(item) for item in candidate.get("inputFactIds") or []}
                if inputs.intersection(affected):
                    affected.add(candidate_id)
                    changed = True
        return affected

    def _invalidate_active_selections_for_candidates(
        self,
        *,
        tenant_id: str,
        project_id: str,
        candidate_ids: set[str],
        actor_id: str,
        reason: str,
    ) -> list[Dict[str, Any]]:
        invalidations: list[Dict[str, Any]] = []
        for selection in self.list_canonical_selections(
            tenant_id, project_id, active_only=True
        ):
            if str(selection.get("candidateId") or "") not in candidate_ids:
                continue
            invalidations.append(
                self._append_selection_invalidation_event(
                    selection=selection,
                    actor_id=actor_id,
                    reason=reason,
                )
            )
        return invalidations

    def _build_active_selection_invalidation_batch_for_candidates(
        self,
        *,
        tenant_id: str,
        project_id: str,
        candidate_ids: set[str],
        actor_id: str,
        reason: str,
    ) -> list[Dict[str, Any]]:
        selections = sorted(
            (
                selection
                for selection in self.list_canonical_selections(
                    tenant_id, project_id, active_only=True
                )
                if str(selection.get("candidateId") or "") in candidate_ids
            ),
            key=lambda item: (
                str(item.get("selectionId") or ""),
                str(item.get("recordId") or ""),
            ),
        )
        records = [
            self._build_selection_invalidation_record(
                selection=selection,
                actor_id=actor_id,
                reason=reason,
            )
            for selection in selections
        ]
        invalidated_selection_ids = {
            str(selection["selectionId"]) for selection in selections
        }
        active_snapshots = sorted(
            self.list_approved_input_snapshots(
                tenant_id, project_id, active_only=True
            ),
            key=lambda item: (
                str(item.get("snapshotId") or ""),
                str(item.get("recordId") or ""),
            ),
        )
        for snapshot in active_snapshots:
            contained_selection_ids = sorted(
                invalidated_selection_ids.intersection(
                    str(item) for item in snapshot.get("selectionIds", [])
                )
            )
            if not contained_selection_ids:
                continue
            selection_label = (
                "selection " + contained_selection_ids[0]
                if len(contained_selection_ids) == 1
                else "selections " + ", ".join(contained_selection_ids)
            )
            records.append(
                self._build_snapshot_invalidation_record(
                    snapshot=snapshot,
                    actor_id=actor_id,
                    reason=f"contains invalidated canonical {selection_label}",
                )
            )
        return records

    # -- Append-only approval invalidation --------------------------------

    def invalidate_approvals_for_document_change(
        self,
        *,
        tenant_id: str,
        project_id: str,
        superseded_document_version_id: str,
        replacement_document_version_id: str,
        actor_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        superseded = self.get_document_version(
            tenant_id, project_id, superseded_document_version_id
        )
        replacement = self.get_document_version(
            tenant_id, project_id, replacement_document_version_id
        )
        if superseded_document_version_id == replacement_document_version_id:
            raise ParserV2RecordError("replacement document version must be new")
        if superseded.get("documentId") != replacement.get("documentId"):
            raise ParserV2ReferenceError(
                "replacement version belongs to another document"
            )
        active_selections = self.list_canonical_selections(
            tenant_id, project_id, active_only=True
        )
        source_candidate_ids = {
            str(item["candidateId"])
            for item in self.list_candidates(tenant_id, project_id)
            if item.get("documentVersionId") == superseded_document_version_id
        }
        affected_candidate_ids = self._candidate_dependency_closure(
            tenant_id, project_id, source_candidate_ids
        )
        affected_selections = [
            item
            for item in active_selections
            if item.get("documentVersionId") == superseded_document_version_id
            or str(item.get("candidateId") or "") in affected_candidate_ids
        ]
        selection_events: List[Dict[str, Any]] = []
        for selection in affected_selections:
            event_id = _semantic_id(
                "selection_invalidation",
                {
                    "selectionId": selection["selectionId"],
                    "supersededDocumentVersionId": superseded_document_version_id,
                    "replacementDocumentVersionId": replacement_document_version_id,
                },
            )
            record = self._base_record(
                record_type=CANONICAL_SELECTION_RECORD_TYPE,
                stable_id=event_id,
                tenant_id=tenant_id,
                project_id=project_id,
                actor_id=actor_id,
                action="canonical_selection_invalidated",
                versions=_version_context(selection),
            )
            record.update(
                {
                    "eventKind": "invalidated",
                    "invalidationId": event_id,
                    "selectionId": selection["selectionId"],
                    "invalidatesRecordId": selection["recordId"],
                    "key": selection["canonicalField"],
                    "fieldKey": selection["canonicalField"],
                    "canonicalField": selection["canonicalField"],
                    "candidateId": selection["candidateId"],
                    "supersededDocumentVersionId": superseded_document_version_id,
                    "replacementDocumentVersionId": replacement_document_version_id,
                    "supersededSourceHash": superseded["sourceHash"],
                    "replacementSourceHash": replacement["sourceHash"],
                    "supersededSha256": superseded["sha256"],
                    "replacementSha256": replacement["sha256"],
                    "reason": _required(reason, "reason"),
                    "approvalStatus": "superseded",
                    "validityStatus": "invalidated",
                    "sourceType": "parser_v2_approval_invalidation",
                }
            )
            selection_events.append(
                record if self._persist_batch is not None else self._persist_immutable(record)
            )

        affected_selection_ids = {
            str(item["selectionId"]) for item in affected_selections
        }
        active_snapshots = self.list_approved_input_snapshots(
            tenant_id, project_id, active_only=True
        )
        affected_snapshots = [
            item
            for item in active_snapshots
            if superseded_document_version_id in item.get("documentVersionIds", [])
            or bool(affected_selection_ids.intersection(item.get("selectionIds", [])))
        ]
        snapshot_events: List[Dict[str, Any]] = []
        for snapshot in affected_snapshots:
            event_id = _semantic_id(
                "snapshot_invalidation",
                {
                    "snapshotId": snapshot["snapshotId"],
                    "supersededDocumentVersionId": superseded_document_version_id,
                    "replacementDocumentVersionId": replacement_document_version_id,
                },
            )
            record = self._base_record(
                record_type=APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
                stable_id=event_id,
                tenant_id=tenant_id,
                project_id=project_id,
                actor_id=actor_id,
                action="approved_input_snapshot_invalidated",
                versions=_version_context(snapshot),
            )
            record.update(
                {
                    "eventKind": "invalidated",
                    "invalidationId": event_id,
                    "snapshotId": snapshot["snapshotId"],
                    "invalidatesRecordId": snapshot["recordId"],
                    "key": snapshot["snapshotId"],
                    "selectionIds": list(snapshot.get("selectionIds") or []),
                    "supersededDocumentVersionId": superseded_document_version_id,
                    "replacementDocumentVersionId": replacement_document_version_id,
                    "supersededSourceHash": superseded["sourceHash"],
                    "replacementSourceHash": replacement["sourceHash"],
                    "supersededSha256": superseded["sha256"],
                    "replacementSha256": replacement["sha256"],
                    "reason": _required(reason, "reason"),
                    "approvalStatus": "superseded",
                    "validityStatus": "invalidated",
                    "sourceType": "parser_v2_approval_invalidation",
                }
            )
            snapshot_events.append(
                record if self._persist_batch is not None else self._persist_immutable(record)
            )

        if self._persist_batch is not None and (selection_events or snapshot_events):
            selection_count = len(selection_events)
            persisted_events = self._persist_immutable_batch(
                [*selection_events, *snapshot_events]
            )
            selection_events = persisted_events[:selection_count]
            snapshot_events = persisted_events[selection_count:]

        return {
            "schemaVersion": "realytics-parser-v2-invalidation-result-v1",
            "tenantId": tenant_id,
            "projectId": project_id,
            "supersededDocumentVersionId": superseded_document_version_id,
            "replacementDocumentVersionId": replacement_document_version_id,
            "selectionInvalidationCount": len(selection_events),
            "snapshotInvalidationCount": len(snapshot_events),
            "selectionInvalidations": selection_events,
            "snapshotInvalidations": snapshot_events,
        }

    # -- Typed reads -------------------------------------------------------

    def get_parsing_run(
        self, tenant_id: str, project_id: str, run_id: str
    ) -> Dict[str, Any]:
        for record in self.list_parsing_runs(tenant_id, project_id):
            if record.get("parsingRunId") == run_id:
                return record
        raise ParserV2ReferenceError(
            f"{PARSING_RUN_RECORD_TYPE} not found for parsingRunId={run_id}"
        )

    def get_document_version(
        self, tenant_id: str, project_id: str, document_version_id: str
    ) -> Dict[str, Any]:
        return self._get_created(
            tenant_id,
            project_id,
            DOCUMENT_VERSION_RECORD_TYPE,
            "documentVersionId",
            document_version_id,
        )

    def get_candidate(
        self, tenant_id: str, project_id: str, candidate_id: str
    ) -> Dict[str, Any]:
        return self._get_created(
            tenant_id,
            project_id,
            PARSED_FACT_CANDIDATE_RECORD_TYPE,
            "candidateId",
            candidate_id,
        )

    def get_source_conflict(
        self, tenant_id: str, project_id: str, conflict_id: str
    ) -> Dict[str, Any]:
        for record in self.list_source_conflicts(tenant_id, project_id):
            if record.get("conflictId") == conflict_id:
                return record
        raise ParserV2ReferenceError(
            f"{SOURCE_CONFLICT_RECORD_TYPE} not found for conflictId={conflict_id}"
        )

    def get_review_decision(
        self, tenant_id: str, project_id: str, decision_id: str
    ) -> Dict[str, Any]:
        return self._get_created(
            tenant_id,
            project_id,
            REVIEW_DECISION_RECORD_TYPE,
            "reviewDecisionId",
            decision_id,
        )

    def list_parsing_runs(self, tenant_id: str, project_id: str) -> List[Dict[str, Any]]:
        return _materialize_parsing_runs(
            self._records(tenant_id, project_id, PARSING_RUN_RECORD_TYPE)
        )

    def list_document_versions(
        self, tenant_id: str, project_id: str, *, document_id: str | None = None
    ) -> List[Dict[str, Any]]:
        records = self._created_records(
            tenant_id, project_id, DOCUMENT_VERSION_RECORD_TYPE
        )
        return [
            item
            for item in records
            if document_id is None or item.get("documentId") == document_id
        ]

    def list_candidates(
        self,
        tenant_id: str,
        project_id: str,
        *,
        run_id: str | None = None,
        document_version_id: str | None = None,
        canonical_field: str | None = None,
    ) -> List[Dict[str, Any]]:
        records = self._created_records(
            tenant_id, project_id, PARSED_FACT_CANDIDATE_RECORD_TYPE
        )
        return [
            item
            for item in records
            if (run_id is None or item.get("parsingRunId") == run_id)
            and (
                document_version_id is None
                or item.get("documentVersionId") == document_version_id
            )
            and (
                canonical_field is None
                or item.get("canonicalField") == canonical_field
            )
        ]

    def list_candidate_set_seals(
        self, tenant_id: str, project_id: str
    ) -> List[Dict[str, Any]]:
        return self._created_records(
            tenant_id, project_id, CANDIDATE_SET_SEAL_RECORD_TYPE
        )

    def resolve_evidence_locator(
        self, tenant_id: str, project_id: str, evidence_id: str
    ) -> Dict[str, Any]:
        normalized_id = _required(evidence_id, "evidence_id")
        references: list[Dict[str, Any]] = []
        for candidate in self.list_candidates(tenant_id, project_id):
            for locator in candidate.get("evidence") or []:
                if not isinstance(locator, Mapping):
                    continue
                if str(locator.get("evidenceId") or "") != normalized_id:
                    continue
                references.append(
                    {
                        "candidateId": candidate["candidateId"],
                        "parsingRunId": candidate["parsingRunId"],
                        "documentVersionId": candidate["documentVersionId"],
                        "fieldKey": candidate["fieldKey"],
                        "sourceLocator": copy.deepcopy(dict(locator)),
                    }
                )
        if not references:
            raise ParserV2ReferenceError(
                f"Parser V2 evidence locator not found: {normalized_id}"
            )
        locator_hashes = {
            _stable_json_hash(item["sourceLocator"]) for item in references
        }
        if len(locator_hashes) != 1:
            raise ImmutableRecordConflict(
                "one evidenceId resolves to conflicting source locators"
            )
        return {
            "schemaVersion": "realytics-parser-v2-evidence-resolution-v1",
            "tenantId": tenant_id,
            "projectId": project_id,
            "evidenceId": normalized_id,
            "sourceLocator": references[0]["sourceLocator"],
            "references": references,
            "storageMode": "candidate_embedded_controlled_foundation",
            "firstClassEvidenceGraphRecord": False,
        }

    def list_source_conflicts(
        self, tenant_id: str, project_id: str
    ) -> List[Dict[str, Any]]:
        return _materialize_source_conflicts(
            self._records(tenant_id, project_id, SOURCE_CONFLICT_RECORD_TYPE)
        )

    def list_review_decisions(
        self,
        tenant_id: str,
        project_id: str,
        *,
        candidate_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        records = self._created_records(
            tenant_id, project_id, REVIEW_DECISION_RECORD_TYPE
        )
        filtered = [
            item
            for item in records
            if candidate_id is None or item.get("candidateId") == candidate_id
        ]
        return sorted(
            filtered, key=lambda item: int(item.get("decisionSequence") or 0)
        )

    def list_canonical_selections(
        self,
        tenant_id: str,
        project_id: str,
        *,
        active_only: bool = False,
        include_events: bool = False,
    ) -> List[Dict[str, Any]]:
        records = self._records(
            tenant_id, project_id, CANONICAL_SELECTION_RECORD_TYPE
        )
        materialized = _materialize_validity(records, "selectionId")
        if active_only:
            materialized = [
                item for item in materialized if item.get("validityStatus") == "active"
            ]
        if include_events:
            return materialized + [
                copy.deepcopy(item)
                for item in records
                if item.get("eventKind") == "invalidated"
            ]
        return materialized

    def list_approved_input_snapshots(
        self,
        tenant_id: str,
        project_id: str,
        *,
        active_only: bool = False,
        include_events: bool = False,
    ) -> List[Dict[str, Any]]:
        records = self._records(
            tenant_id, project_id, APPROVED_INPUT_SNAPSHOT_RECORD_TYPE
        )
        materialized = _materialize_validity(records, "snapshotId")
        if active_only:
            materialized = [
                item for item in materialized if item.get("validityStatus") == "active"
            ]
        if include_events:
            return materialized + [
                copy.deepcopy(item)
                for item in records
                if item.get("eventKind") == "invalidated"
            ]
        return materialized

    # -- Internal persistence guardrails ----------------------------------

    def _find_candidate_set_seal(
        self, tenant_id: str, project_id: str, run_id: str
    ) -> Dict[str, Any] | None:
        return next(
            (
                item
                for item in self.list_candidate_set_seals(tenant_id, project_id)
                if item.get("parsingRunId") == run_id
            ),
            None,
        )

    def _candidate_set_state(
        self, tenant_id: str, project_id: str, run_id: str
    ) -> Dict[str, Any]:
        candidates = sorted(
            self.list_candidates(tenant_id, project_id, run_id=run_id),
            key=lambda item: str(item.get("candidateId") or ""),
        )
        conflicts = sorted(
            (
                item
                for item in self.list_source_conflicts(tenant_id, project_id)
                if run_id
                in {
                    str(value)
                    for value in (
                        item.get("sourceParsingRunIds")
                        or [item.get("parsingRunId")]
                    )
                    if value
                }
            ),
            key=lambda item: str(item.get("conflictId") or ""),
        )
        candidate_descriptors = [
            {
                "candidateId": str(item.get("candidateId") or ""),
                "immutablePayloadHash": _sha256(item.get("immutablePayloadHash")),
            }
            for item in candidates
        ]
        conflict_descriptors = [
            {
                "conflictId": str(item.get("conflictId") or ""),
                "immutablePayloadHash": _sha256(item.get("immutablePayloadHash")),
            }
            for item in conflicts
        ]
        return {
            "candidateIds": [item["candidateId"] for item in candidate_descriptors],
            "conflictIds": [item["conflictId"] for item in conflict_descriptors],
            "candidateSetHash": _stable_json_hash(
                {
                    "parsingRunId": run_id,
                    "candidates": candidate_descriptors,
                    "conflicts": conflict_descriptors,
                }
            ),
        }

    def _get_active_selection(
        self, tenant_id: str, project_id: str, selection_id: str
    ) -> Dict[str, Any]:
        for item in self.list_canonical_selections(
            tenant_id, project_id, active_only=True
        ):
            if item.get("selectionId") == selection_id:
                return item
        raise ParserV2ApprovalError(
            f"canonical selection is absent or invalidated: {selection_id}"
        )

    def _get_active_snapshot(
        self, tenant_id: str, project_id: str, snapshot_id: str
    ) -> Dict[str, Any]:
        for item in self.list_approved_input_snapshots(
            tenant_id, project_id, active_only=True
        ):
            if item.get("snapshotId") == snapshot_id:
                return item
        raise ParserV2ApprovalError(
            f"approved input snapshot is absent or invalidated: {snapshot_id}"
        )

    def _get_created(
        self,
        tenant_id: str,
        project_id: str,
        record_type: str,
        identity_field: str,
        identity: str,
    ) -> Dict[str, Any]:
        for record in self._created_records(tenant_id, project_id, record_type):
            if str(record.get(identity_field)) == str(identity):
                return record
        raise ParserV2ReferenceError(
            f"{record_type} not found for {identity_field}={identity}"
        )

    def _created_records(
        self, tenant_id: str, project_id: str, record_type: str
    ) -> List[Dict[str, Any]]:
        return [
            item
            for item in self._records(tenant_id, project_id, record_type)
            if item.get("eventKind", "created") == "created"
        ]

    def _records(
        self, tenant_id: str, project_id: str, record_type: str
    ) -> List[Dict[str, Any]]:
        records = self._list_records(
            project_id,
            record_type=record_type,
            limit=_BACKEND_HISTORY_READ_LIMIT,
        )
        if len(records) >= _BACKEND_HISTORY_READ_LIMIT:
            raise ParserV2RecordError(
                "Parser V2 history reached the backend read limit; use the typed "
                "paginated repository before continuing approval operations"
            )
        return [
            copy.deepcopy(item)
            for item in records
            if item.get("recordType") == record_type
            and item.get("tenantId") == tenant_id
            and item.get("projectId") == project_id
        ]

    def _base_record(
        self,
        *,
        record_type: str,
        stable_id: str,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        action: str,
        versions: Mapping[str, str] | None,
    ) -> Dict[str, Any]:
        timestamp = self._timestamp()
        record = {
            "schemaVersion": PARSER_V2_SCHEMA_VERSION,
            "recordId": f"{record_type}:{_required(stable_id, 'stable_id')}",
            "recordType": record_type,
            "tenantId": _required(tenant_id, "tenant_id"),
            "projectId": _required(project_id, "project_id"),
            "actorId": _required(actor_id, "actor_id"),
            "eventKind": "created",
            "createdAt": timestamp,
            "audit": {
                "actorId": actor_id,
                "tenantId": tenant_id,
                "projectId": project_id,
                "action": action,
                "timestamp": timestamp,
            },
        }
        if versions:
            record.update(dict(versions))
        return record

    def _timestamp(self) -> str:
        value = self._clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        return str(value)

    def _persist_immutable(self, record: Dict[str, Any]) -> Dict[str, Any]:
        prepared = copy.deepcopy(record)
        prepared["immutablePayloadHash"] = _immutable_hash(prepared)
        existing = self._find_by_record_id(
            str(prepared["tenantId"]),
            str(prepared["projectId"]),
            str(prepared["recordType"]),
            str(prepared["recordId"]),
        )
        if existing is not None:
            if existing.get("immutablePayloadHash") == prepared["immutablePayloadHash"]:
                return existing
            raise ImmutableRecordConflict(
                f"immutable Parser V2 ID reused with different content: {prepared['recordId']}"
            )
        return self._upsert(str(prepared["projectId"]), prepared)

    def _persist_immutable_batch(
        self, records: Sequence[Mapping[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Persist an immutable record group through the optional atomic seam.

        Validation and idempotent-existing checks remain in the record store;
        the injected callback owns cross-process isolation and the all-or-none
        commit. Callers only use this path when ``self._persist_batch`` exists.
        """

        if self._persist_batch is None:
            raise ParserV2RecordError(
                "atomic Parser V2 batch persistence is not configured"
            )
        if not records:
            return []

        prepared_records: List[Dict[str, Any]] = []
        prepared_by_id: Dict[str, Dict[str, Any]] = {}
        tenant_ids: set[str] = set()
        project_ids: set[str] = set()
        for source in records:
            prepared = copy.deepcopy(dict(source))
            prepared["immutablePayloadHash"] = _immutable_hash(prepared)
            record_id = str(prepared.get("recordId") or "")
            project_id = str(prepared.get("projectId") or "")
            if not record_id or not project_id:
                raise ParserV2RecordError(
                    "atomic Parser V2 records require recordId and projectId"
                )
            duplicate = prepared_by_id.get(record_id)
            if duplicate is not None and duplicate.get(
                "immutablePayloadHash"
            ) != prepared.get("immutablePayloadHash"):
                raise ImmutableRecordConflict(
                    f"immutable Parser V2 ID reused within one batch: {record_id}"
                )
            prepared_by_id[record_id] = prepared
            prepared_records.append(prepared)
            tenant_ids.add(str(prepared.get("tenantId") or ""))
            project_ids.add(project_id)
        if len(tenant_ids) != 1 or "" in tenant_ids:
            raise ParserV2RecordError(
                "one atomic Parser V2 batch cannot span multiple tenants"
            )
        if len(project_ids) != 1:
            raise ParserV2RecordError(
                "one atomic Parser V2 batch cannot span multiple projects"
            )

        resolved_by_id: Dict[str, Dict[str, Any]] = {}
        pending_by_id: Dict[str, Dict[str, Any]] = {}
        pending: List[Dict[str, Any]] = []
        for prepared in prepared_records:
            record_id = str(prepared["recordId"])
            if record_id in resolved_by_id or record_id in pending_by_id:
                continue
            existing = self._find_by_record_id(
                str(prepared["tenantId"]),
                str(prepared["projectId"]),
                str(prepared["recordType"]),
                record_id,
            )
            if existing is not None:
                if existing.get("immutablePayloadHash") != prepared.get(
                    "immutablePayloadHash"
                ):
                    raise ImmutableRecordConflict(
                        "immutable Parser V2 ID reused with different content: "
                        f"{record_id}"
                    )
                resolved_by_id[record_id] = existing
                continue
            pending_by_id[record_id] = prepared
            pending.append(prepared)

        if pending:
            persisted_rows = self._persist_batch(next(iter(project_ids)), pending)
            if len(persisted_rows) != len(pending):
                raise ParserV2RecordError(
                    "atomic Parser V2 batch callback returned an incomplete result"
                )
            returned_ids: set[str] = set()
            for persisted in persisted_rows:
                saved = copy.deepcopy(dict(persisted))
                record_id = str(saved.get("recordId") or "")
                expected = pending_by_id.get(record_id)
                if expected is None or record_id in returned_ids:
                    raise ParserV2RecordError(
                        "atomic Parser V2 batch callback returned an unexpected record"
                    )
                if saved.get("immutablePayloadHash") != expected.get(
                    "immutablePayloadHash"
                ):
                    raise ImmutableRecordConflict(
                        "atomic Parser V2 batch callback changed immutable content: "
                        f"{record_id}"
                    )
                returned_ids.add(record_id)
                resolved_by_id[record_id] = saved
            if returned_ids != set(pending_by_id):
                raise ParserV2RecordError(
                    "atomic Parser V2 batch callback omitted a requested record"
                )

        return [
            copy.deepcopy(resolved_by_id[str(item["recordId"])])
            for item in prepared_records
        ]

    def _find_by_record_id(
        self,
        tenant_id: str,
        project_id: str,
        record_type: str,
        record_id: str,
    ) -> Dict[str, Any] | None:
        for item in self._records(tenant_id, project_id, record_type):
            if item.get("recordId") == record_id:
                return item
        return None


def _versions(
    parser_version: str,
    ontology_version: str,
    prompt_version: str,
    model_version: str,
) -> Dict[str, str]:
    return {
        "parserVersion": _required(parser_version, "parser_version"),
        "ontologyVersion": _required(ontology_version, "ontology_version"),
        "promptVersion": _required(prompt_version, "prompt_version"),
        "modelVersion": _required(model_version, "model_version"),
    }


def _version_context(record: Mapping[str, Any]) -> Dict[str, str]:
    return _versions(
        str(record.get("parserVersion") or ""),
        str(record.get("ontologyVersion") or ""),
        str(record.get("promptVersion") or ""),
        str(record.get("modelVersion") or ""),
    )


def _sha256(value: Any) -> str:
    digest = _required(value, "source_hash").lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ParserV2RecordError("source_hash must be a 64-character sha256 hex digest")
    return digest


def _selection_wire_payload(selection: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "selectionId": selection["selectionId"],
        "tenantId": selection["tenantId"],
        "projectId": selection["projectId"],
        "fieldKey": selection["fieldKey"],
        "entityId": selection.get("entityId"),
        "candidateId": selection["candidateId"],
        "reviewDecisionId": selection["reviewDecisionId"],
        "approvalStatus": selection["approvalStatus"],
        "conflictStatus": selection["conflictStatus"],
        "normalizedValue": copy.deepcopy(selection.get("normalizedValue")),
        "unit": selection.get("unit"),
        "selectionVersion": selection["selectionVersion"],
        "selectedBy": selection["selectedBy"],
        "selectedAt": selection["selectedAt"],
        "effectiveDate": selection.get("effectiveDate"),
        "asOfDate": selection.get("asOfDate"),
        "supersedesSelectionId": selection.get("supersedesSelectionId"),
        "metadata": {
            "parsingRunId": selection["parsingRunId"],
            "documentVersionId": selection["documentVersionId"],
            "sha256": selection["sha256"],
            "sourceLocator": copy.deepcopy(selection.get("sourceLocator") or {}),
        },
    }


def approved_input_snapshot_wire_payload(
    snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    """Project a persistence envelope into the strict public snapshot contract."""

    public_keys = (
        "snapshotId",
        "tenantId",
        "projectId",
        "approvalStatus",
        "validityStatus",
        "snapshotVersion",
        "purpose",
        "inputSchemaVersion",
        "fieldRegistryVersion",
        "selectionIds",
        "selections",
        "ontologyVersion",
        "documentVersionIds",
        "sourceHashes",
        "sourceDocuments",
        "inputItems",
        "inputs",
        "inputsByEntity",
        "versionContexts",
        "sourceParsingRunIds",
        "approvedBy",
        "approvedAt",
        "supersedesSnapshotId",
        "metadata",
    )
    return {
        key: copy.deepcopy(snapshot[key])
        for key in public_keys
        if key in snapshot
    }


def validate_source_locator_attestations(
    evidence: Sequence[Mapping[str, Any]],
    orchestration_artifact: Mapping[str, Any],
) -> None:
    """Reject locators not emitted by the immutable sanitized run artifact."""

    attested = _collect_attested_source_locators(orchestration_artifact)
    matched_count = 0
    for raw_locator in evidence:
        locator = SourceLocator.model_validate(raw_locator).model_dump(
            by_alias=True, mode="json", exclude_none=True
        )
        metadata = locator.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        is_calculation_locator = bool(
            locator.get("artifactKey")
            and locator.get("jsonPath")
            and metadata.get("calculationRunId")
            and metadata.get("outputHash")
        )
        if is_calculation_locator:
            continue
        evidence_id = str(locator.get("evidenceId") or "").strip()
        if not evidence_id:
            raise ParserV2ReferenceError(
                "candidate evidence requires an evidenceId attested by structural extraction"
            )
        expected = attested.get(evidence_id)
        if expected is None:
            raise ParserV2ReferenceError(
                "candidate evidenceId is absent from the parsing run structural artifact"
            )
        if locator != expected:
            raise ParserV2ReferenceError(
                "candidate evidence locator does not match its attested structural artifact"
            )
        matched_count += 1
    if evidence and matched_count == 0:
        raise ParserV2ReferenceError(
            "candidate evidence requires a source locator attested by structural extraction"
        )


def _collect_attested_source_locators(value: Any) -> Dict[str, Dict[str, Any]]:
    locators: Dict[str, Dict[str, Any]] = {}

    def walk(item: Any) -> None:
        if isinstance(item, list):
            for child in item:
                walk(child)
            return
        if not isinstance(item, Mapping):
            return
        evidence_id = str(item.get("evidenceId") or "").strip()
        if evidence_id:
            try:
                payload = SourceLocator.model_validate(item).model_dump(
                    by_alias=True, mode="json", exclude_none=True
                )
            except ValueError:
                payload = None
            if payload is not None:
                existing = locators.get(evidence_id)
                if existing is not None and existing != payload:
                    raise ParserV2ReferenceError(
                        "structural artifact contains a conflicting evidenceId"
                    )
                locators[evidence_id] = payload
        for child in item.values():
            walk(child)

    walk(value)
    return locators


def _authoritative_source_locator(
    evidence: Sequence[Mapping[str, Any]],
    *,
    origin_type: str,
    calculation_run_id: str | None,
    calculation_output_path: str | None,
    calculation_output_hash: str | None,
) -> Dict[str, Any]:
    if not evidence:
        return {}
    if origin_type == "derived":
        for item in evidence:
            metadata = item.get("metadata")
            metadata = metadata if isinstance(metadata, Mapping) else {}
            if (
                str(item.get("artifactKey") or "") == str(calculation_run_id or "")
                and str(item.get("jsonPath") or "")
                == str(calculation_output_path or "")
                and str(metadata.get("calculationRunId") or "")
                == str(calculation_run_id or "")
                and str(metadata.get("outputHash") or "")
                == str(calculation_output_hash or "")
            ):
                return copy.deepcopy(dict(item))
        raise ParserV2ApprovalError(
            "derived candidate evidence is missing its exact calculation output locator"
        )
    return copy.deepcopy(dict(evidence[0]))


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ParserV2RecordError(f"{name} is required")
    return text


def _canonical_field(value: Any) -> str:
    field_key = _required(value, "canonical_field")
    if re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", field_key) is None:
        raise ParserV2RecordError(
            "canonical_field must be a lower snake_case dotted path"
        )
    return field_key


def _assert_bounded_json(value: Any, *, label: str, max_bytes: int) -> None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ParserV2RecordError(f"{label} must be a safe JSON value") from exc
    if len(encoded) > max_bytes:
        raise ParserV2RecordError(f"{label} exceeds the controlled storage limit")


def _sanitize_source_locator(value: Mapping[str, Any]) -> Dict[str, Any]:
    locator = copy.deepcopy(dict(value))
    raw_excerpt = locator.pop("rawTextExcerpt", None)
    if raw_excerpt is None:
        raw_excerpt = locator.pop("raw_text_excerpt", None)
    if raw_excerpt is not None:
        computed_hash = hashlib.sha256(str(raw_excerpt).encode("utf-8")).hexdigest()
        supplied_hash = locator.get("rawTextSha256") or locator.get("raw_text_sha256")
        if supplied_hash is not None and not hmac.compare_digest(
            str(supplied_hash).lower(), computed_hash
        ):
            raise ParserV2RecordError(
                "candidate evidence raw text hash does not match its excerpt"
            )
        locator.pop("raw_text_sha256", None)
        locator["rawTextSha256"] = computed_hash
        metadata = dict(locator.get("metadata") or {})
        metadata["rawTextPersisted"] = False
        locator["metadata"] = metadata
    return locator


def _validated_source_locator(
    value: Mapping[str, Any],
    *,
    document_version_id: str,
    source_hash: str,
) -> Dict[str, Any]:
    sanitized = _sanitize_source_locator(value)
    try:
        locator = SourceLocator.model_validate(sanitized)
    except ValueError as exc:
        raise ParserV2RecordError(
            "candidate evidence must be a valid explicit SourceLocator"
        ) from exc
    if (
        locator.document_version_id is not None
        and locator.document_version_id != document_version_id
    ):
        raise ParserV2ReferenceError(
            "candidate evidence belongs to another document version"
        )
    if locator.file_sha256 is not None and locator.file_sha256 != source_hash:
        raise ParserV2ReferenceError(
            "candidate evidence source hash does not match its document version"
        )
    payload = locator.model_dump(by_alias=True, mode="json", exclude_none=True)
    if not payload.get("evidenceId"):
        material = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload["evidenceId"] = (
            "parser-evidence:" + hashlib.sha256(material).hexdigest()[:32]
        )
    return payload


def _inputs_by_entity(
    selections: Sequence[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for item in selections:
        entity_id = item.get("entityId")
        if entity_id is None:
            continue
        grouped.setdefault(str(entity_id), {})[str(item["fieldKey"])] = copy.deepcopy(
            item.get("normalizedValue")
        )
    return grouped


def _date_wire_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return str(value.isoformat())
    text = str(value).strip()
    if not text:
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ParserV2RecordError("date values must use YYYY-MM-DD") from exc
    return text


def _stable_json_hash(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ParserV2RecordError(
            "calculation lineage contains a non-canonical JSON value"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


def _resolve_json_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ParserV2ReferenceError(
            "calculation_output_path must be an RFC 6901 JSON Pointer"
        )
    current = document
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise ParserV2ReferenceError(
                    f"calculation output path was not found: {pointer}"
                )
            current = current[token]
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            if not token.isdigit():
                raise ParserV2ReferenceError(
                    f"calculation output array path is invalid: {pointer}"
                )
            index = int(token)
            if index >= len(current):
                raise ParserV2ReferenceError(
                    f"calculation output array index is out of range: {pointer}"
                )
            current = current[index]
            continue
        raise ParserV2ReferenceError(
            f"calculation output path cannot traverse a scalar: {pointer}"
        )
    return current


def _validate_calculation_run_binding(
    *,
    calculation_run: Mapping[str, Any],
    tenant_id: str,
    input_fact_ids: Sequence[str],
    formula_id: str,
    formula_version: str,
    calculation_engine_version: str,
    calculation_output_path: str,
    calculation_output_hash: str,
    input_snapshot_hash: str,
    normalized_value: Any,
) -> None:
    run_tenant = calculation_run.get("tenantId") or calculation_run.get("tenant_id")
    if run_tenant and str(run_tenant) != tenant_id:
        raise ParserV2ReferenceError(
            "derived candidate calculation run belongs to another tenant"
        )
    run_status = str(
        calculation_run.get("executionStatus")
        or calculation_run.get("status")
        or ""
    ).casefold()
    if run_status not in {
        "approved",
        "completed",
        "completed_with_warnings",
        "succeeded",
    }:
        raise ParserV2ApprovalError(
            "derived candidate calculation run has not completed successfully"
        )
    engine_key = str(
        calculation_run.get("engineKey")
        or calculation_run.get("engine_key")
        or ""
    )
    engine_version = str(
        calculation_run.get("engineVersion")
        or calculation_run.get("engine_version")
        or ""
    )
    if not engine_key or not engine_version:
        raise ParserV2ReferenceError(
            "derived candidate calculation run lacks engine identity/version"
        )
    if calculation_engine_version != engine_version:
        raise ParserV2ReferenceError(
            "calculationEngineVersion does not match the persisted calculation run"
        )
    run_formula_id = str(calculation_run.get("formulaId") or engine_key)
    run_formula_version = str(calculation_run.get("formulaVersion") or engine_version)
    if formula_id != run_formula_id or formula_version != run_formula_version:
        raise ParserV2ReferenceError(
            "derived candidate formula identity/version does not match the calculation run"
        )
    input_snapshot = calculation_run.get("inputSnapshot")
    if input_snapshot is None:
        input_snapshot = calculation_run.get("input_snapshot")
    if input_snapshot is None:
        raise ParserV2ReferenceError(
            "derived candidate calculation run lacks its input snapshot"
        )
    actual_input_hash = _stable_json_hash(input_snapshot)
    if not hmac.compare_digest(_sha256(input_snapshot_hash), actual_input_hash):
        raise ParserV2ReferenceError(
            "inputSnapshotHash does not match the persisted calculation run"
        )
    recorded_inputs = (
        calculation_run.get("inputFactIds")
        or calculation_run.get("inputCandidateIds")
        or calculation_run.get("input_fact_ids")
    )
    if recorded_inputs is None and isinstance(input_snapshot, Mapping):
        recorded_inputs = (
            input_snapshot.get("inputFactIds")
            or input_snapshot.get("inputCandidateIds")
            or input_snapshot.get("input_fact_ids")
        )
    if not isinstance(recorded_inputs, list):
        raise ParserV2ReferenceError(
            "derived candidate calculation run lacks input-fact lineage"
        )
    if {str(item) for item in recorded_inputs} != {str(item) for item in input_fact_ids}:
        raise ParserV2ReferenceError(
            "derived candidate inputFactIds do not match the calculation run"
        )
    result_snapshot = calculation_run.get("resultSnapshot")
    if result_snapshot is None:
        result_snapshot = calculation_run.get("result_snapshot")
    if result_snapshot is None:
        raise ParserV2ReferenceError(
            "derived candidate calculation run lacks its result snapshot"
        )
    output_value = _resolve_json_pointer(result_snapshot, calculation_output_path)
    actual_output_hash = _stable_json_hash(output_value)
    if not hmac.compare_digest(_sha256(calculation_output_hash), actual_output_hash):
        raise ParserV2ReferenceError(
            "calculationOutputHash does not match the selected calculation output"
        )
    if normalized_value != output_value:
        raise ParserV2ApprovalError(
            "derived candidate normalizedValue does not match the selected calculation output"
        )


def _immutable_hash(record: Mapping[str, Any]) -> str:
    generated_timestamps = {
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
    payload = {
        key: value
        for key, value in record.items()
        if key
        not in generated_timestamps.union({"audit", "immutablePayloadHash"})
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _semantic_id(prefix: str, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _unique_dicts(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for item in items:
        copied = copy.deepcopy(dict(item))
        key = json.dumps(copied, ensure_ascii=False, sort_keys=True, default=str)
        unique[key] = copied
    return [unique[key] for key in sorted(unique)]


def _materialize_validity(
    records: Sequence[Mapping[str, Any]], identity_field: str
) -> List[Dict[str, Any]]:
    invalidations: Dict[str, List[Dict[str, Any]]] = {}
    for item in records:
        if item.get("eventKind") != "invalidated":
            continue
        identity = str(item.get(identity_field) or "")
        invalidations.setdefault(identity, []).append(copy.deepcopy(dict(item)))
    materialized: List[Dict[str, Any]] = []
    for item in records:
        if item.get("eventKind", "created") != "created":
            continue
        copied = copy.deepcopy(dict(item))
        events = invalidations.get(str(copied.get(identity_field) or ""), [])
        if events:
            copied["validityStatus"] = "invalidated"
            copied["approvalStatus"] = "superseded"
            copied["invalidationEvents"] = events
        materialized.append(copied)
    return materialized


def _materialize_parsing_runs(
    records: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    events_by_run: Dict[str, List[Dict[str, Any]]] = {}
    for item in records:
        if item.get("eventKind") != "execution_transition":
            continue
        events_by_run.setdefault(str(item.get("parsingRunId") or ""), []).append(
            copy.deepcopy(dict(item))
        )
    materialized: List[Dict[str, Any]] = []
    for item in records:
        if item.get("eventKind", "created") != "created":
            continue
        copied = copy.deepcopy(dict(item))
        events = sorted(
            events_by_run.get(str(copied.get("parsingRunId") or ""), []),
            key=lambda event: int(event.get("sequenceNumber") or 0),
        )
        for event in events:
            copied.update(
                {
                    "executionStatus": event["executionStatus"],
                    "approvalStatus": event["approvalStatus"],
                    "startedAt": event.get("startedAt"),
                    "completedAt": event.get("completedAt"),
                    "errorCodes": list(event.get("errorCodes") or []),
                    "orchestrationArtifact": copy.deepcopy(
                        event.get("orchestrationArtifact")
                    ),
                    "orchestrationArtifactHash": event.get(
                        "orchestrationArtifactHash"
                    ),
                    "currentTransitionEventId": event.get("transitionEventId"),
                }
            )
        copied["transitionEvents"] = events
        materialized.append(copied)
    return materialized


def _materialize_source_conflicts(
    records: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    events_by_conflict: Dict[str, List[Dict[str, Any]]] = {}
    for item in records:
        if item.get("eventKind") != "conflict_resolution":
            continue
        events_by_conflict.setdefault(str(item.get("conflictId") or ""), []).append(
            copy.deepcopy(dict(item))
        )
    materialized: List[Dict[str, Any]] = []
    for item in records:
        if item.get("eventKind", "created") != "created":
            continue
        copied = copy.deepcopy(dict(item))
        events = events_by_conflict.get(str(copied.get("conflictId") or ""), [])
        if events:
            latest = events[-1]
            copied.update(
                {
                    "conflictStatus": latest["conflictStatus"],
                    "resolutionReviewDecisionId": latest[
                        "resolutionReviewDecisionId"
                    ],
                    "resolvedAt": latest["resolvedAt"],
                    "selectedCandidateId": latest.get("selectedCandidateId"),
                    "rejectedCandidateIds": list(
                        latest.get("rejectedCandidateIds") or []
                    ),
                    "resolutionEvents": events,
                }
            )
        else:
            copied["resolutionEvents"] = []
        materialized.append(copied)
    return materialized
