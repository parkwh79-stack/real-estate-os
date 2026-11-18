from __future__ import annotations

"""Repository-wide, candidate-only Parser V2 source reconciliation.

This use case deliberately stops at conflict detection.  It never chooses a
candidate, ranks source authority, or writes a canonical selection.  Candidate
and document supersession are evaluated from their immutable lineage before
facts are compared.
"""

import copy
import hashlib
import json
from collections import defaultdict
from typing import Any, Mapping, Sequence

from parsing_platform.canonical_fields import canonical_field_key
from use_cases.parser_v2_records import (
    ImmutableRecordConflict,
    ParserV2RecordStore,
)


RECONCILIATION_REPORT_SCHEMA_VERSION = (
    "realytics-parser-v2-cross-document-reconciliation-v1"
)
_CONFLICT_ID_PREFIX = "parser-v2-cross-source-conflict"
_INACTIVE_VALIDITY_STATUSES = frozenset(
    {"inactive", "invalidated", "superseded"}
)
_UNSUCCESSFUL_RUN_STATUSES = frozenset({"failed", "cancelled"})
_SUCCESSFUL_RUN_STATUSES = frozenset(
    {"completed", "completed_with_warnings"}
)


class ParserV2CrossDocumentReconciliationError(ValueError):
    """Raised when stored Parser V2 lineage cannot be reconciled safely."""


class ParserV2CrossDocumentReconciliationPreconditionError(
    ParserV2CrossDocumentReconciliationError
):
    """Raised before writes when reconciliation must run inside the seal boundary."""

    code = "PARSER_V2_CROSS_DOCUMENT_RECONCILIATION_REQUIRES_PRE_SEAL"

    def __init__(
        self,
        *,
        conflict_ids: Sequence[str],
        sealed_run_ids: Sequence[str],
    ) -> None:
        self.conflict_ids = tuple(sorted(str(item) for item in conflict_ids))
        self.sealed_run_ids = tuple(sorted(str(item) for item in sealed_run_ids))
        super().__init__(
            "cross-document reconciliation would add a new conflict to an "
            "already sealed parsing run; run reconciliation before sealing or "
            "inside the atomic conflict-and-seal transaction"
        )


def reconcile_parser_v2_project_sources(
    *,
    record_store: ParserV2RecordStore,
    tenant_id: str,
    project_id: str,
    actor_id: str,
) -> dict[str, Any]:
    """Detect active cross-document/cross-run fact conflicts for one project.

    Facts are grouped only by the exact canonical ``(fieldKey, entityId)`` key.
    Within a group, semantic equality is defined by ``normalizedValue``, the
    normalized unit token, and the complete explicit ``metadata.basis`` value.
    Effective/as-of dates and authority remain review context; they do not rank
    or silently select a source.
    """

    tenant = _required(tenant_id, "tenant_id")
    project = _required(project_id, "project_id")
    actor = _required(actor_id, "actor_id")

    document_versions = record_store.list_document_versions(tenant, project)
    parsing_runs = record_store.list_parsing_runs(tenant, project)
    candidates = record_store.list_candidates(tenant, project)

    documents_by_id = _unique_by_id(
        document_versions,
        id_key="documentVersionId",
        label="document version",
    )
    runs_by_id = _unique_by_id(
        parsing_runs,
        id_key="parsingRunId",
        label="parsing run",
    )
    candidates_by_id = _unique_by_id(
        candidates, id_key="candidateId", label="candidate"
    )

    declared_superseded_document_ids = {
        str(item["supersedesDocumentVersionId"])
        for item in document_versions
        if str(item.get("supersedesDocumentVersionId") or "").strip()
    }
    declared_superseded_candidate_ids = {
        str(item["supersedesCandidateId"])
        for item in candidates
        if str(item.get("supersedesCandidateId") or "").strip()
    }
    if not declared_superseded_document_ids.issubset(documents_by_id):
        raise ParserV2CrossDocumentReconciliationError(
            "document supersession lineage references a missing document version"
        )
    if not declared_superseded_candidate_ids.issubset(candidates_by_id):
        raise ParserV2CrossDocumentReconciliationError(
            "candidate supersession lineage references a missing candidate"
        )

    pre_supersession_eligible_candidates: list[Mapping[str, Any]] = []
    base_ineligibility_reasons: dict[str, list[str]] = {}
    for candidate in sorted(
        candidates, key=lambda item: str(item.get("candidateId") or "")
    ):
        candidate_id = str(candidate.get("candidateId") or "")
        document_version_id = str(candidate.get("documentVersionId") or "")
        parsing_run_id = str(candidate.get("parsingRunId") or "")
        document = documents_by_id.get(document_version_id)
        run = runs_by_id.get(parsing_run_id)
        reasons: list[str] = []

        if document is None:
            raise ParserV2CrossDocumentReconciliationError(
                f"candidate {candidate_id} references a missing document version"
            )
        if run is None:
            raise ParserV2CrossDocumentReconciliationError(
                f"candidate {candidate_id} references a missing parsing run"
            )
        if str(run.get("documentVersionId") or "") != document_version_id:
            raise ParserV2CrossDocumentReconciliationError(
                f"candidate {candidate_id} run/document lineage is inconsistent"
            )
        if (
            str(candidate.get("documentId") or "")
            and str(candidate.get("documentId") or "")
            != str(document.get("documentId") or "")
        ):
            raise ParserV2CrossDocumentReconciliationError(
                f"candidate {candidate_id} document identity is inconsistent"
            )
        if (
            str(candidate.get("sourceHash") or "")
            != str(document.get("sha256") or "")
            or str(run.get("sourceHash") or "")
            != str(document.get("sha256") or "")
        ):
            raise ParserV2CrossDocumentReconciliationError(
                f"candidate {candidate_id} source hash lineage is inconsistent"
            )

        if _inactive_validity(candidate):
            reasons.append("candidate_validity_inactive")
        if (
            str(candidate.get("presenceStatus") or "").strip().casefold()
            != "present"
        ):
            reasons.append("presence_not_present")
        if (
            str(candidate.get("validationStatus") or "").strip().casefold()
            != "passed"
        ):
            reasons.append("candidate_validation_not_passed")

        if _inactive_validity(document):
            reasons.append("document_version_validity_inactive")

        run_status = str(
            run.get("executionStatus") or ""
        ).strip().casefold()
        if run_status in _UNSUCCESSFUL_RUN_STATUSES:
            reasons.append("parsing_run_unsuccessful")
        elif run_status not in _SUCCESSFUL_RUN_STATUSES:
            reasons.append("parsing_run_not_completed")

        if reasons:
            base_ineligibility_reasons[candidate_id] = reasons
            continue
        pre_supersession_eligible_candidates.append(candidate)

    # Registering a replacement document is not enough to suppress its
    # predecessor. The replacement must have at least one usable candidate
    # produced by a successful run under the same validity/presence/validation
    # gates used by reconciliation. This preserves the last usable source when
    # a replacement parse is pending, failed, empty, or validation-blocked.
    eligible_replacement_document_ids = {
        str(item.get("documentVersionId") or "")
        for item in pre_supersession_eligible_candidates
    }
    effective_superseded_document_ids = (
        _effective_superseded_document_ids(
            documents_by_id=documents_by_id,
            eligible_replacement_document_ids=(
                eligible_replacement_document_ids
            ),
        )
    )
    base_eligible_candidates: list[Mapping[str, Any]] = []
    for candidate in pre_supersession_eligible_candidates:
        candidate_id = str(candidate.get("candidateId") or "")
        document_version_id = str(candidate.get("documentVersionId") or "")
        if document_version_id in effective_superseded_document_ids:
            base_ineligibility_reasons[candidate_id] = [
                "document_version_superseded"
            ]
            continue
        base_eligible_candidates.append(candidate)

    # A replacement can suppress its predecessor only after the replacement
    # itself satisfies the same active-document, successful-run, present, and
    # validation gates.  This prevents a failed retry or stale document from
    # hiding the last usable candidate.
    effective_superseded_candidate_ids = {
        str(item["supersedesCandidateId"])
        for item in base_eligible_candidates
        if str(item.get("supersedesCandidateId") or "").strip()
    }
    active_candidates: list[Mapping[str, Any]] = []
    ignored_candidates: list[dict[str, Any]] = []
    for candidate in sorted(
        candidates, key=lambda item: str(item.get("candidateId") or "")
    ):
        candidate_id = str(candidate.get("candidateId") or "")
        reasons = list(base_ineligibility_reasons.get(candidate_id) or [])
        if candidate_id in effective_superseded_candidate_ids:
            reasons.append("candidate_superseded")
        if reasons:
            ignored_candidates.append(
                {
                    "candidateId": candidate_id,
                    "documentVersionId": (
                        str(candidate.get("documentVersionId") or "") or None
                    ),
                    "parsingRunId": (
                        str(candidate.get("parsingRunId") or "") or None
                    ),
                    "reasons": reasons,
                }
            )
            continue
        active_candidates.append(candidate)

    grouped: dict[tuple[str, str | None], list[Mapping[str, Any]]] = defaultdict(
        list
    )
    for candidate in active_candidates:
        try:
            field_key = canonical_field_key(
                str(candidate.get("canonicalField") or candidate.get("fieldKey") or "")
            )
        except ValueError as exc:
            raise ParserV2CrossDocumentReconciliationError(
                f"candidate {candidate.get('candidateId')} has an invalid canonical field"
            ) from exc
        entity_id = candidate.get("entityId")
        grouped[(field_key, None if entity_id is None else str(entity_id))].append(
            candidate
        )

    existing_conflicts = {
        str(item.get("conflictId") or ""): item
        for item in record_store.list_source_conflicts(tenant, project)
        if str(item.get("conflictId") or "")
    }
    report_groups: list[dict[str, Any]] = []
    conflict_ids: list[str] = []
    planned_groups: list[dict[str, Any]] = []
    new_conflict_specs: list[dict[str, Any]] = []

    for (field_key, entity_id), group_candidates in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1] or ""),
    ):
        ordered_candidates = sorted(
            group_candidates,
            key=lambda item: str(item.get("candidateId") or ""),
        )
        variants = _semantic_variants(
            ordered_candidates,
            documents_by_id=documents_by_id,
            runs_by_id=runs_by_id,
        )
        candidate_ids = [
            str(item.get("candidateId") or "") for item in ordered_candidates
        ]
        document_version_ids = sorted(
            {str(item.get("documentVersionId") or "") for item in ordered_candidates}
        )
        parsing_run_ids = sorted(
            {str(item.get("parsingRunId") or "") for item in ordered_candidates}
        )
        cross_document = len(document_version_ids) > 1
        cross_run = len(parsing_run_ids) > 1

        status = "single_source"
        conflict_id: str | None = None
        conflict_code: str | None = None
        if len(ordered_candidates) > 1 and len(variants) == 1:
            status = "semantic_duplicate"
        elif len(variants) > 1 and not (cross_document or cross_run):
            status = "same_run_conflict_out_of_scope"
        elif len(variants) > 1:
            status = "conflict"
            conflict_code = (
                "PARSER_V2_CROSS_DOCUMENT_FACT_CONFLICT"
                if cross_document
                else "PARSER_V2_CROSS_RUN_FACT_CONFLICT"
            )
            conflict_id = _conflict_id(
                tenant_id=tenant,
                project_id=project,
                field_key=field_key,
                entity_id=entity_id,
                conflict_code=conflict_code,
                candidate_ids=candidate_ids,
            )
            existing_conflict = existing_conflicts.get(conflict_id)
            if existing_conflict is not None:
                _validate_existing_conflict(
                    existing_conflict,
                    field_key=field_key,
                    entity_id=entity_id,
                    candidate_ids=candidate_ids,
                    conflict_code=conflict_code,
                )
            else:
                new_conflict_specs.append(
                    {
                        "conflictId": conflict_id,
                        "fieldKey": field_key,
                        "entityId": entity_id,
                        "candidateIds": candidate_ids,
                        "conflictCode": conflict_code,
                        "parsingRunIds": parsing_run_ids,
                    }
                )
            conflict_ids.append(conflict_id)

        group_report: dict[str, Any] = {
            "fieldKey": field_key,
            "entityId": entity_id,
            "status": status,
            "candidateIds": candidate_ids,
            "documentVersionIds": document_version_ids,
            "parsingRunIds": parsing_run_ids,
            "crossDocument": cross_document,
            "crossRun": cross_run,
            "semanticVariants": variants,
        }
        if conflict_id is not None:
            group_report["conflictId"] = conflict_id
        planned_groups.append(group_report)

    # The per-run seal hashes the conflict set. Adding a conflict after that
    # point would make review impossible, while conflict resolution itself
    # requires a review decision. Preflight every group and reject the entire
    # reconciliation before the first write if any new conflict touches a
    # sealed run. Existing identical conflicts remain an idempotent read.
    sealed_run_ids = {
        str(item.get("parsingRunId") or "")
        for item in record_store.list_candidate_set_seals(tenant, project)
        if str(item.get("parsingRunId") or "").strip()
    }
    blocked_run_ids = sorted(
        {
            run_id
            for spec in new_conflict_specs
            for run_id in spec["parsingRunIds"]
            if run_id in sealed_run_ids
        }
    )
    if blocked_run_ids:
        raise ParserV2CrossDocumentReconciliationPreconditionError(
            conflict_ids=[
                str(spec["conflictId"]) for spec in new_conflict_specs
            ],
            sealed_run_ids=blocked_run_ids,
        )

    for spec in new_conflict_specs:
        conflict_id = str(spec["conflictId"])
        try:
            conflict = record_store.create_source_conflict(
                tenant_id=tenant,
                project_id=project,
                conflict_id=conflict_id,
                actor_id=actor,
                canonical_field=str(spec["fieldKey"]),
                candidate_ids=list(spec["candidateIds"]),
                conflict_code=str(spec["conflictCode"]),
                severity="high",
                notes=(
                    "Repository-wide Parser V2 reconciliation detected "
                    "distinct normalized value, unit, or basis variants "
                    "for the same canonical field and entity. Authority "
                    "and temporal context require human review; no source "
                    "was selected."
                ),
            )
        except ImmutableRecordConflict:
            # Another worker may have persisted the same deterministic
            # conflict after our preflight read. Re-read and validate it.
            conflict = record_store.get_source_conflict(
                tenant, project, conflict_id
            )
        _validate_existing_conflict(
            conflict,
            field_key=str(spec["fieldKey"]),
            entity_id=spec["entityId"],
            candidate_ids=list(spec["candidateIds"]),
            conflict_code=str(spec["conflictCode"]),
        )
        existing_conflicts[conflict_id] = conflict

    for group_report in planned_groups:
        conflict_id = group_report.get("conflictId")
        if conflict_id is not None:
            conflict = existing_conflicts[str(conflict_id)]
            group_report["conflictStatus"] = str(
                conflict.get("conflictStatus") or "open"
            )
        report_groups.append(group_report)

    active_document_ids = sorted(
        document_version_id
        for document_version_id, document in documents_by_id.items()
        if document_version_id not in effective_superseded_document_ids
        and not _inactive_validity(document)
    )
    report: dict[str, Any] = {
        "schemaVersion": RECONCILIATION_REPORT_SCHEMA_VERSION,
        "tenantId": tenant,
        "projectId": project,
        "scope": {
            "candidateActivityRule": (
                "present candidates whose candidate and document version are "
                "not superseded/invalidated, whose validation passed, and whose "
                "parsing run completed successfully"
            ),
            "groupKey": ["canonicalField", "entityId"],
            "semanticComparison": [
                "normalizedValue",
                "normalizedUnit",
                "metadata.basis",
            ],
        },
        "activeDocumentVersionIds": active_document_ids,
        "declaredSupersededDocumentVersionIds": sorted(
            declared_superseded_document_ids
        ),
        "effectiveSupersededDocumentVersionIds": sorted(
            effective_superseded_document_ids
        ),
        "supersededDocumentVersionIds": sorted(
            effective_superseded_document_ids
        ),
        "activeCandidateIds": sorted(
            str(item.get("candidateId") or "") for item in active_candidates
        ),
        "declaredSupersededCandidateIds": sorted(
            declared_superseded_candidate_ids
        ),
        "effectiveSupersededCandidateIds": sorted(
            effective_superseded_candidate_ids
        ),
        "ignoredCandidates": ignored_candidates,
        "groups": report_groups,
        "conflictIds": sorted(conflict_ids),
        "automaticSelectionPerformed": False,
        "authorityRankingPerformed": False,
    }
    report["reportHash"] = _stable_hash(report)
    return report


def _semantic_variants(
    candidates: Sequence[Mapping[str, Any]],
    *,
    documents_by_id: Mapping[str, Mapping[str, Any]],
    runs_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_signature: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        metadata = candidate.get("metadata")
        basis = (
            copy.deepcopy(metadata.get("basis"))
            if isinstance(metadata, Mapping) and "basis" in metadata
            else None
        )
        normalized_unit = _normalized_unit(candidate.get("unit"))
        semantic_value = {
            "normalizedValue": copy.deepcopy(candidate.get("normalizedValue")),
            "normalizedUnit": normalized_unit,
            "basis": basis,
        }
        canonical_signature = _canonical_json(semantic_value)
        variant = by_signature.setdefault(
            canonical_signature,
            {
                "signatureHash": hashlib.sha256(
                    canonical_signature.encode("utf-8")
                ).hexdigest(),
                **semantic_value,
                "candidateContexts": [],
            },
        )
        document_version_id = str(candidate.get("documentVersionId") or "")
        parsing_run_id = str(candidate.get("parsingRunId") or "")
        document = documents_by_id[document_version_id]
        run = runs_by_id[parsing_run_id]
        variant["candidateContexts"].append(
            {
                "candidateId": str(candidate.get("candidateId") or ""),
                "documentId": str(document.get("documentId") or ""),
                "documentVersionId": document_version_id,
                "parsingRunId": parsing_run_id,
                "executionStatus": str(run.get("executionStatus") or ""),
                "sourceHash": str(candidate.get("sourceHash") or ""),
                "unit": candidate.get("unit"),
                "basis": copy.deepcopy(basis),
                "effectiveDate": candidate.get("effectiveDate"),
                "asOfDate": candidate.get("asOfDate"),
                "authority": candidate.get("authority"),
                "documentSourceAuthority": document.get("sourceAuthority"),
                "parserVersion": candidate.get("parserVersion"),
                "ontologyVersion": candidate.get("ontologyVersion"),
                "promptVersion": candidate.get("promptVersion"),
                "modelVersion": candidate.get("modelVersion"),
            }
        )

    variants = list(by_signature.values())
    for variant in variants:
        variant["candidateContexts"] = sorted(
            variant["candidateContexts"],
            key=lambda item: str(item.get("candidateId") or ""),
        )
    return sorted(variants, key=lambda item: str(item["signatureHash"]))


def _validate_existing_conflict(
    conflict: Mapping[str, Any],
    *,
    field_key: str,
    entity_id: str | None,
    candidate_ids: Sequence[str],
    conflict_code: str,
) -> None:
    matches = (
        str(conflict.get("canonicalField") or conflict.get("fieldKey") or "")
        == field_key
        and conflict.get("entityId") == entity_id
        and sorted(str(item) for item in conflict.get("candidateIds") or [])
        == sorted(str(item) for item in candidate_ids)
        and str(conflict.get("conflictCode") or conflict.get("conflictType") or "")
        == conflict_code
    )
    if not matches:
        raise ParserV2CrossDocumentReconciliationError(
            "deterministic cross-source conflict ID is bound to different content"
        )


def _conflict_id(
    *,
    tenant_id: str,
    project_id: str,
    field_key: str,
    entity_id: str | None,
    conflict_code: str,
    candidate_ids: Sequence[str],
) -> str:
    material = {
        "schemaVersion": RECONCILIATION_REPORT_SCHEMA_VERSION,
        "tenantId": tenant_id,
        "projectId": project_id,
        "fieldKey": field_key,
        "entityId": entity_id,
        "conflictCode": conflict_code,
        "candidateIds": sorted(str(item) for item in candidate_ids),
    }
    return f"{_CONFLICT_ID_PREFIX}:{_stable_hash(material)[:32]}"


def _unique_by_id(
    records: Sequence[Mapping[str, Any]],
    *,
    id_key: str,
    label: str,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for record in records:
        record_id = str(record.get(id_key) or "").strip()
        if not record_id:
            raise ParserV2CrossDocumentReconciliationError(
                f"{label} is missing {id_key}"
            )
        if record_id in result:
            raise ParserV2CrossDocumentReconciliationError(
                f"duplicate {label} ID: {record_id}"
            )
        result[record_id] = record
    return result


def _effective_superseded_document_ids(
    *,
    documents_by_id: Mapping[str, Mapping[str, Any]],
    eligible_replacement_document_ids: set[str],
) -> set[str]:
    """Return the complete ancestor closure of usable replacements.

    A usable v3 replaces both v2 and v1 in a ``v3 -> v2 -> v1`` chain even
    when v2's own parse failed. Conversely, a declared replacement with no
    usable candidate is not a root and therefore cannot hide its predecessor.
    Every declared chain is validated before activity is calculated so corrupt
    cycles and cross-document links fail closed instead of producing a
    traversal-order-dependent result.
    """

    predecessor_by_id: dict[str, str] = {}
    for document_version_id, document in documents_by_id.items():
        predecessor_id = str(
            document.get("supersedesDocumentVersionId") or ""
        ).strip()
        if not predecessor_id:
            continue
        predecessor = documents_by_id.get(predecessor_id)
        if predecessor is None:
            raise ParserV2CrossDocumentReconciliationError(
                "document supersession lineage references a missing "
                "document version"
            )
        document_id = str(document.get("documentId") or "").strip()
        predecessor_document_id = str(
            predecessor.get("documentId") or ""
        ).strip()
        if (
            not document_id
            or not predecessor_document_id
            or document_id != predecessor_document_id
        ):
            raise ParserV2CrossDocumentReconciliationError(
                "document supersession lineage crosses document identities"
            )
        predecessor_by_id[document_version_id] = predecessor_id

    # Validate all declared chains, not only currently eligible roots. A cycle
    # is corrupt immutable lineage and must never become conditionally visible
    # merely because one of its parses is not yet successful.
    for start_id in sorted(documents_by_id):
        visited: set[str] = set()
        current_id = start_id
        while current_id in predecessor_by_id:
            if current_id in visited:
                raise ParserV2CrossDocumentReconciliationError(
                    "document supersession lineage contains a cycle"
                )
            visited.add(current_id)
            current_id = predecessor_by_id[current_id]
        if current_id in visited:
            raise ParserV2CrossDocumentReconciliationError(
                "document supersession lineage contains a cycle"
            )

    children_by_predecessor: dict[str, list[str]] = defaultdict(list)
    version_numbers_by_document: dict[str, set[int]] = defaultdict(set)
    for document_version_id, document in documents_by_id.items():
        version_number = document.get("versionNumber")
        if (
            isinstance(version_number, bool)
            or not isinstance(version_number, int)
            or version_number < 1
        ):
            raise ParserV2CrossDocumentReconciliationError(
                "document supersession lineage has an invalid version number"
            )
        document_id = str(document.get("documentId") or "").strip()
        if not document_id:
            raise ParserV2CrossDocumentReconciliationError(
                "document supersession lineage is missing document identity"
            )
        if version_number in version_numbers_by_document[document_id]:
            raise ParserV2CrossDocumentReconciliationError(
                "document supersession lineage has duplicate version numbers"
            )
        version_numbers_by_document[document_id].add(version_number)

        predecessor_id = predecessor_by_id.get(document_version_id)
        if predecessor_id is None:
            if version_number != 1:
                raise ParserV2CrossDocumentReconciliationError(
                    "document supersession lineage root must be version 1"
                )
            continue
        predecessor_version_number = documents_by_id[predecessor_id].get(
            "versionNumber"
        )
        if (
            isinstance(predecessor_version_number, bool)
            or not isinstance(predecessor_version_number, int)
            or version_number != predecessor_version_number + 1
        ):
            raise ParserV2CrossDocumentReconciliationError(
                "document supersession lineage version numbers must "
                "increase by one"
            )
        children_by_predecessor[predecessor_id].append(document_version_id)

    if any(
        len(children) > 1
        for children in children_by_predecessor.values()
    ):
        raise ParserV2CrossDocumentReconciliationError(
            "document supersession lineage cannot branch"
        )

    effective: set[str] = set()
    for replacement_id in sorted(eligible_replacement_document_ids):
        current_id = replacement_id
        while current_id in predecessor_by_id:
            predecessor_id = predecessor_by_id[current_id]
            effective.add(predecessor_id)
            current_id = predecessor_id
    return effective


def _inactive_validity(record: Mapping[str, Any]) -> bool:
    return (
        str(record.get("validityStatus") or "").strip().casefold()
        in _INACTIVE_VALIDITY_STATUSES
    )


def _normalized_unit(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    return normalized or None


def _required(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ParserV2CrossDocumentReconciliationError(f"{label} is required")
    return normalized


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ParserV2CrossDocumentReconciliationError(
            "candidate reconciliation values must be canonical JSON"
        ) from exc


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
