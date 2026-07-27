"""Versioned, evidence-first read model for the document review workspace.

The browser must not assemble a decision-critical review surface from unrelated
requests.  This module projects already-authorized source-document and Parser
V2 records into one deterministic payload.  It does not promote candidates,
resolve conflicts, or create approval authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from schemas.document_review_workspace import DocumentReviewWorkspaceViewModel


DOCUMENT_REVIEW_SCHEMA_VERSION = "realytics-document-review-workspace-v1"
DOCUMENT_REVIEW_VIEW_MODEL_VERSION = "document-review-workspace-2026-07-27"


def build_document_review_workspace_read_model(
    *,
    project_id: str,
    source_documents: Sequence[Mapping[str, Any]],
    run_packages: Sequence[Mapping[str, Any]],
    glossary_candidates: Sequence[Mapping[str, Any]],
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Return one deterministic review payload without inventing authority."""

    packages = sorted(
        (copy.deepcopy(dict(item)) for item in run_packages),
        key=_run_package_sort_key,
    )
    documents = sorted(
        (copy.deepcopy(dict(item)) for item in source_documents),
        key=_source_document_sort_key,
    )
    glossary = sorted(
        (copy.deepcopy(dict(item)) for item in glossary_candidates),
        key=lambda item: (
            str(item.get("surface") or ""),
            str(item.get("candidateId") or ""),
        ),
    )
    runs = [copy.deepcopy(item.get("parsingRun") or {}) for item in packages]
    review_queue = _build_review_queue(packages)
    snapshot_diffs = _build_snapshot_diffs(packages)
    as_of_date = _latest_timestamp(documents, packages, glossary)

    stable_payload = {
        "schemaVersion": DOCUMENT_REVIEW_SCHEMA_VERSION,
        "viewModelVersion": DOCUMENT_REVIEW_VIEW_MODEL_VERSION,
        "projectId": project_id,
        "asOfDate": as_of_date,
        "sourceDocuments": documents,
        "runs": runs,
        "packages": packages,
        "glossaryCandidates": glossary,
        "reviewQueue": review_queue,
        "snapshotDiffs": snapshot_diffs,
    }
    snapshot_hash = _stable_hash(stable_payload)
    observed = (observed_at or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    ).isoformat()
    payload = {
        **stable_payload,
        "snapshotId": f"document-review:{project_id}:{snapshot_hash[:20]}",
        "snapshotHash": snapshot_hash,
        "observedAt": observed,
        "freshness": {
            "status": "observed" if as_of_date else "unknown",
            "latestSourceAt": as_of_date,
            "observedAt": observed,
            "warningCodes": [] if as_of_date else ["SOURCE_AS_OF_DATE_UNKNOWN"],
        },
        "issueSummary": _issue_summary(review_queue),
        "warnings": [],
        "authority": {
            "candidateOnly": True,
            "authoritativeProjectionMutated": False,
            "approvalAuthority": "approved_input_snapshot_only",
            "assembly": "server_versioned_read_model",
        },
    }
    return DocumentReviewWorkspaceViewModel.model_validate(payload).model_dump(
        mode="json", by_alias=True, exclude_none=False
    )


def _build_review_queue(
    packages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for package in packages:
        run = _mapping(package.get("parsingRun"))
        run_id = str(run.get("parsingRunId") or "")
        conflicts = [
            _mapping(item) for item in _sequence(package.get("conflicts"))
        ]
        reviews_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for raw_review in _sequence(package.get("reviewDecisions")):
            review = _mapping(raw_review)
            candidate_id = str(review.get("candidateId") or "")
            if candidate_id:
                reviews_by_candidate.setdefault(candidate_id, []).append(review)
        open_conflicts_by_candidate: dict[str, list[str]] = {}
        for conflict in conflicts:
            if str(conflict.get("conflictStatus") or "open") != "open":
                continue
            conflict_id = str(conflict.get("conflictId") or "")
            for candidate_id in _sequence(conflict.get("candidateIds")):
                normalized_id = str(candidate_id or "")
                if normalized_id:
                    open_conflicts_by_candidate.setdefault(normalized_id, []).append(
                        conflict_id
                    )

        for raw_candidate in _sequence(package.get("candidates")):
            candidate = _mapping(raw_candidate)
            candidate_id = str(candidate.get("candidateId") or "")
            if not candidate_id:
                continue
            issue_codes: list[str] = []
            validation_status = str(candidate.get("validationStatus") or "unknown")
            validation_codes = sorted(
                {
                    str(value)
                    for value in _sequence(candidate.get("validationCodes"))
                    if str(value or "")
                }
            )
            if validation_status not in {"passed", "valid"}:
                issue_codes.append("VALIDATION_NOT_PASSED")
            issue_codes.extend(
                f"VALIDATION:{code}" for code in validation_codes
            )

            locators = _candidate_locators(candidate)
            if not locators:
                issue_codes.append("MISSING_EVIDENCE_LOCATOR")
            elif not any(_is_precise_evidence_locator(locator) for locator in locators):
                issue_codes.append("WEAK_EVIDENCE_LOCATOR")

            conflict_ids = sorted(
                value
                for value in open_conflicts_by_candidate.get(candidate_id, [])
                if value
            )
            if conflict_ids:
                issue_codes.append("OPEN_SOURCE_CONFLICT")

            decisions = sorted(
                reviews_by_candidate.get(candidate_id, []),
                key=lambda item: int(item.get("decisionSequence") or 0),
            )
            latest_decision = decisions[-1] if decisions else {}
            review_status = str(
                latest_decision.get("resultingReviewStatus")
                or candidate.get("reviewStatus")
                or "pending"
            )
            if review_status in {"pending", "in_review"}:
                issue_codes.append("REVIEW_PENDING")
            elif review_status == "needs_revision":
                issue_codes.append("REVIEW_NEEDS_REVISION")

            authority = str(candidate.get("authority") or "unknown")
            if authority in {"unknown", "model_inference", "draft_document"}:
                issue_codes.append("WEAK_SOURCE_AUTHORITY")
            presence_status = str(candidate.get("presenceStatus") or "present")
            if presence_status in {"missing", "not_disclosed", "pending"}:
                issue_codes.append("MISSING_VALUE")

            issue_codes = list(dict.fromkeys(issue_codes))
            if not issue_codes:
                continue
            queue.append(
                {
                    "queueItemId": f"{run_id}:{candidate_id}",
                    "candidateId": candidate_id,
                    "parsingRunId": str(
                        candidate.get("parsingRunId") or run_id
                    ),
                    "documentVersionId": str(
                        candidate.get("documentVersionId")
                        or run.get("documentVersionId")
                        or ""
                    ),
                    "fieldKey": str(
                        candidate.get("fieldKey")
                        or candidate.get("canonicalField")
                        or "unmapped_field"
                    ),
                    "entityId": candidate.get("entityId"),
                    "validationOutcome": _validation_outcome(
                        validation_status, validation_codes
                    ),
                    "issueCodes": issue_codes,
                    "openConflictIds": conflict_ids,
                    "evidenceLocatorCount": len(locators),
                    "severity": _issue_severity(issue_codes),
                }
            )
    return sorted(
        queue,
        key=lambda item: (
            _severity_rank(str(item.get("severity") or "unknown")),
            str(item.get("fieldKey") or ""),
            str(item.get("candidateId") or ""),
        ),
    )


def _build_snapshot_diffs(
    packages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    snapshots_by_id: dict[str, dict[str, Any]] = {}
    for package in packages:
        for raw_snapshot in _sequence(package.get("approvedInputSnapshots")):
            snapshot = copy.deepcopy(_mapping(raw_snapshot))
            snapshot_id = str(snapshot.get("snapshotId") or "")
            if snapshot_id:
                snapshots_by_id[snapshot_id] = snapshot
    ordered = sorted(snapshots_by_id.values(), key=_snapshot_sort_key)
    previous: dict[str, Any] | None = None
    diffs: list[dict[str, Any]] = []
    for snapshot in ordered:
        predecessor_id = str(snapshot.get("supersedesSnapshotId") or "")
        predecessor = snapshots_by_id.get(predecessor_id) if predecessor_id else previous
        if predecessor is not None:
            diffs.append(_snapshot_diff(predecessor, snapshot))
        previous = snapshot
    return diffs


def _snapshot_diff(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> dict[str, Any]:
    before_items = _snapshot_item_map(before)
    after_items = _snapshot_item_map(after)
    comparable = before_items is not None and after_items is not None
    changes: list[dict[str, Any]] = []
    if comparable:
        before_items = before_items or {}
        after_items = after_items or {}
        for semantic_key in sorted(set(before_items) | set(after_items)):
            old = before_items.get(semantic_key)
            new = after_items.get(semantic_key)
            if old is None:
                changes.append(_snapshot_change("added", None, new))
            elif new is None:
                changes.append(_snapshot_change("removed", old, None))
            elif _material_snapshot_value(old) != _material_snapshot_value(new):
                changes.append(_snapshot_change("changed", old, new))
    return {
        "fromSnapshotId": before.get("snapshotId"),
        "fromSnapshotHash": before.get("snapshotHash") or _stable_hash(before),
        "toSnapshotId": after.get("snapshotId"),
        "toSnapshotHash": after.get("snapshotHash") or _stable_hash(after),
        "toSnapshotVersion": after.get("snapshotVersion"),
        "approvedAt": after.get("approvedAt"),
        "comparable": comparable,
        "issueCodes": [] if comparable else ["SNAPSHOT_INPUT_ITEMS_UNAVAILABLE"],
        "changeCount": len(changes),
        "changes": changes,
    }


def _snapshot_item_map(
    snapshot: Mapping[str, Any],
) -> dict[str, dict[str, Any]] | None:
    input_items = snapshot.get("inputItems")
    raw_items = _sequence(input_items)
    if input_items is None:
        raw_items = _sequence(snapshot.get("selections"))
    if not raw_items:
        return None
    result: dict[str, dict[str, Any]] = {}
    for raw_item in raw_items:
        item = copy.deepcopy(_mapping(raw_item))
        field_key = str(item.get("fieldKey") or item.get("canonicalField") or "")
        if not field_key:
            continue
        entity_id = str(item.get("entityId") or "")
        result[f"{field_key}\u001f{entity_id}"] = item
    return result or None


def _snapshot_change(
    change_type: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> dict[str, Any]:
    reference = after or before or {}
    return {
        "changeType": change_type,
        "fieldKey": reference.get("fieldKey")
        or reference.get("canonicalField"),
        "entityId": reference.get("entityId"),
        "before": _snapshot_value(before),
        "after": _snapshot_value(after),
    }


def _snapshot_value(item: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "normalizedValue": copy.deepcopy(item.get("normalizedValue")),
        "unit": item.get("unit"),
        "candidateId": item.get("candidateId"),
        "selectionId": item.get("selectionId"),
        "documentVersionId": item.get("documentVersionId"),
        "sourceHash": item.get("sourceHash") or item.get("sha256"),
        "sourceLocator": copy.deepcopy(item.get("sourceLocator")),
        "authority": item.get("authority"),
        "effectiveDate": item.get("effectiveDate"),
        "asOfDate": item.get("asOfDate"),
    }


def _material_snapshot_value(item: Mapping[str, Any]) -> str:
    return _canonical_json(_snapshot_value(item))


def _candidate_locators(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    evidence = [
        _mapping(item)
        for item in _sequence(candidate.get("evidence"))
        if _mapping(item)
    ]
    if evidence:
        return evidence
    locator = _mapping(candidate.get("sourceLocator"))
    return [locator] if locator else []


def _is_precise_evidence_locator(locator: Mapping[str, Any]) -> bool:
    has_document_binding = any(
        str(locator.get(key) or "").strip()
        for key in (
            "documentVersionId",
            "documentId",
            "fileSha256",
            "artifactKey",
            "apiEndpoint",
            "sourceUrl",
        )
    )
    has_source_coordinate = any(
        value not in (None, "", [])
        for value in (
            locator.get("pageNumber"),
            locator.get("page"),
            locator.get("cellReference"),
            locator.get("cellRange"),
            locator.get("cell"),
            locator.get("tableId"),
            locator.get("paragraphIndex"),
            locator.get("jsonPath"),
            locator.get("apiRowId"),
        )
    )
    return has_document_binding and has_source_coordinate


def _validation_outcome(status: str, codes: Sequence[str]) -> str:
    if status in {"passed", "valid"} and not codes:
        return "passed"
    if status in {"failed", "invalid", "blocked"}:
        return "failed"
    if codes or status in {"warning", "partial", "needs_revision"}:
        return "warning"
    return "unknown"


def _issue_severity(issue_codes: Sequence[str]) -> str:
    if "OPEN_SOURCE_CONFLICT" in issue_codes or "MISSING_EVIDENCE_LOCATOR" in issue_codes:
        return "blocking"
    if "VALIDATION_NOT_PASSED" in issue_codes or "REVIEW_NEEDS_REVISION" in issue_codes or any(
        value.startswith("VALIDATION:") for value in issue_codes
    ):
        return "high"
    return "medium"


def _severity_rank(value: str) -> int:
    return {"blocking": 0, "high": 1, "medium": 2, "low": 3}.get(value, 4)


def _issue_summary(queue: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_code: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for item in queue:
        severity = str(item.get("severity") or "unknown")
        by_severity[severity] = by_severity.get(severity, 0) + 1
        for code in _sequence(item.get("issueCodes")):
            normalized = str(code or "")
            if normalized:
                by_code[normalized] = by_code.get(normalized, 0) + 1
    return {
        "count": len(queue),
        "byCode": dict(sorted(by_code.items())),
        "bySeverity": dict(sorted(by_severity.items())),
    }


def _latest_timestamp(*collections: Iterable[Any]) -> str | None:
    timestamp_keys = {
        "uploadedAt",
        "createdAt",
        "updatedAt",
        "ingestedAt",
        "requestedAt",
        "startedAt",
        "completedAt",
        "detectedAt",
        "resolvedAt",
        "decidedAt",
        "selectedAt",
        "approvedAt",
    }
    timestamps: list[tuple[datetime, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key in timestamp_keys and isinstance(nested, str):
                    parsed = _parse_timestamp(nested)
                    if parsed is not None:
                        timestamps.append((parsed, nested))
                else:
                    visit(nested)
        elif isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            for nested in value:
                visit(nested)

    for collection in collections:
        visit(collection)
    if not timestamps:
        return None
    latest = max(timestamps, key=lambda item: item[0])[0]
    return latest.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _run_package_sort_key(item: Mapping[str, Any]) -> tuple[str, str]:
    run = _mapping(item.get("parsingRun"))
    return (
        str(run.get("requestedAt") or run.get("createdAt") or ""),
        str(run.get("parsingRunId") or ""),
    )


def _source_document_sort_key(item: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("uploadedAt") or item.get("createdAt") or ""),
        str(
            item.get("archiveId")
            or item.get("originalFileHash")
            or item.get("sha256")
            or ""
        ),
    )


def _snapshot_sort_key(item: Mapping[str, Any]) -> tuple[int, str, str]:
    return (
        int(item.get("snapshotVersion") or 0),
        str(item.get("approvedAt") or item.get("createdAt") or ""),
        str(item.get("snapshotId") or ""),
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return list(value)
    return []


__all__ = [
    "DOCUMENT_REVIEW_SCHEMA_VERSION",
    "DOCUMENT_REVIEW_VIEW_MODEL_VERSION",
    "build_document_review_workspace_read_model",
]
