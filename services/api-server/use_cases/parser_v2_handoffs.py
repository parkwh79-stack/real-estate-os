from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from parsing_platform.contracts import SourceLocator
from parsing_platform.domain_standards import RealEstateDomain, get_domain_standard
from schemas.orchestration import AgentHandoffEnvelope
from use_cases.parser_v2_records import ParserV2ApprovalError, ParserV2RecordStore


APPROVED_SNAPSHOT_HANDOFF_SCHEMA_VERSION = (
    "realytics-parser-v2-approved-snapshot-handoff-v1"
)


def build_approved_snapshot_handoff(
    *,
    record_store: ParserV2RecordStore,
    tenant_id: str,
    project_id: str,
    snapshot_id: str,
    target_domain: RealEstateDomain | str,
    actor_id: str,
    run_id: str | None = None,
    created_at: datetime | None = None,
) -> AgentHandoffEnvelope:
    """Build a domain-scoped handoff from one active approved snapshot.

    Raw candidates and parsing-run outputs are intentionally not accepted by
    this boundary.  Each emitted input retains its approved selection,
    candidate, document version, source hash, and exact source locator.
    """

    snapshot = _active_snapshot(
        record_store=record_store,
        tenant_id=tenant_id,
        project_id=project_id,
        snapshot_id=snapshot_id,
    )
    if snapshot.get("approvalStatus") != "approved":
        raise ParserV2ApprovalError("handoff requires approvalStatus=approved")
    if snapshot.get("validityStatus") != "active":
        raise ParserV2ApprovalError("handoff requires an active approved snapshot")

    standard = get_domain_standard(target_domain)
    selected_inputs = [
        dict(item)
        for item in snapshot.get("inputItems") or []
        if isinstance(item, Mapping)
        and _matches_prefix(str(item.get("fieldKey") or ""), standard.critical_field_prefixes)
    ]
    validated_inputs = [
        _validate_approved_input(item, project_id=project_id)
        for item in selected_inputs
    ]
    evidence_ids = sorted(
        {
            str(item["sourceLocator"]["evidenceId"])
            for item in validated_inputs
            if item["sourceLocator"].get("evidenceId")
        }
    )
    selection_ids = sorted(str(item["selectionId"]) for item in validated_inputs)
    selected_document_version_ids = {
        str(item["documentVersionId"]) for item in validated_inputs
    }
    selected_source_documents = [
        dict(item)
        for item in snapshot.get("sourceDocuments") or []
        if isinstance(item, Mapping)
        and str(item.get("documentVersionId")) in selected_document_version_ids
    ]
    selected_run_ids = sorted(
        {str(item["parsingRunId"]) for item in validated_inputs}
    )
    selected_version_contexts = _unique_mappings(
        item.get("versionContext")
        for item in validated_inputs
        if isinstance(item.get("versionContext"), Mapping)
    )
    completeness_policy_approved = standard.handoff_policy_status == "approved"
    blocked = not validated_inputs or not completeness_policy_approved
    blocking_issues: list[str] = []
    limitations = []
    if not validated_inputs:
        blocking_issues.append("NO_APPROVED_INPUTS_FOR_TARGET_DOMAIN")
        limitations.append(
            "The approved snapshot contains no field in the target domain's governed field scope."
        )
    if not completeness_policy_approved:
        blocking_issues.append("DOMAIN_HANDOFF_COMPLETENESS_POLICY_NOT_APPROVED")
        limitations.append(
            "The target domain has no approved minimum-input/completeness policy; inputs may be reviewed but the Pod must not execute authoritatively."
        )

    handoff_id = _semantic_id(
        {
            "schemaVersion": APPROVED_SNAPSHOT_HANDOFF_SCHEMA_VERSION,
            "tenantId": tenant_id,
            "projectId": project_id,
            "snapshotId": snapshot_id,
            "targetDomain": standard.domain.value,
            "targetPod": standard.owner_pod,
            "selectionIds": selection_ids,
        }
    )
    timestamp = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload = {
        "schemaVersion": APPROVED_SNAPSHOT_HANDOFF_SCHEMA_VERSION,
        "tenantId": tenant_id,
        "projectId": project_id,
        "approvedSnapshotId": snapshot_id,
        "snapshotPurpose": snapshot.get("purpose"),
        "approvalStatus": snapshot.get("approvalStatus"),
        "approvedAt": snapshot.get("approvedAt"),
        "approvedBy": snapshot.get("approvedBy"),
        "targetDomain": standard.domain.value,
        "targetPod": standard.owner_pod,
        "handoffPolicyStatus": standard.handoff_policy_status,
        "requiredApprovedFieldGroups": [
            list(group) for group in standard.required_approved_field_groups
        ],
        "approvedInputs": validated_inputs,
        "selectionIds": selection_ids,
        "candidateIds": sorted(str(item["candidateId"]) for item in validated_inputs),
        "sourceParsingRunIds": selected_run_ids,
        "versionContexts": selected_version_contexts,
        "sourceDocuments": selected_source_documents,
        "authoritativeInputBoundary": "approved_snapshot_only",
    }
    return AgentHandoffEnvelope(
        handoffId=handoff_id,
        runId=run_id,
        handoffType="approved_input_snapshot",
        fromAgent="RealEstateDocumentParserApprovalGate",
        toAgent=standard.owner_pod,
        status="blocked" if blocked else "created",
        payloadSchema=APPROVED_SNAPSHOT_HANDOFF_SCHEMA_VERSION,
        payload=payload,
        evidenceIds=evidence_ids,
        blockingIssues=blocking_issues,
        assumptions=[],
        dataLimitations=limitations,
        requiresHumanReview=blocked,
        reviewStatus="pending" if blocked else "not_required",
        createdAt=timestamp.isoformat(),
        progressImpact={
            "targetDomain": standard.domain.value,
            "targetPod": standard.owner_pod,
            "approvedInputCount": len(validated_inputs),
            "canExecute": not blocked,
            "requestedBy": actor_id,
        },
    )


def _active_snapshot(
    *,
    record_store: ParserV2RecordStore,
    tenant_id: str,
    project_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    for item in record_store.list_approved_input_snapshots(
        tenant_id, project_id, active_only=True
    ):
        if item.get("snapshotId") == snapshot_id:
            if item.get("tenantId") != tenant_id or item.get("projectId") != project_id:
                break
            return dict(item)
    raise ParserV2ApprovalError(
        f"approved input snapshot is absent or invalidated: {snapshot_id}"
    )


def _validate_approved_input(
    value: Mapping[str, Any], *, project_id: str
) -> dict[str, Any]:
    required = (
        "fieldKey",
        "candidateId",
        "selectionId",
        "reviewDecisionId",
        "documentVersionId",
        "sourceHash",
        "parsingRunId",
    )
    missing = [key for key in required if not value.get(key)]
    if missing:
        raise ParserV2ApprovalError(
            "approved snapshot input is missing lineage: " + ", ".join(missing)
        )
    source_hash = str(value["sourceHash"])
    if len(source_hash) != 64 or any(char not in "0123456789abcdef" for char in source_hash):
        raise ParserV2ApprovalError("approved snapshot input has an invalid sourceHash")
    try:
        locator = SourceLocator.model_validate(value.get("sourceLocator") or {})
    except ValueError as exc:
        raise ParserV2ApprovalError(
            "approved snapshot input requires an explicit source locator"
        ) from exc
    if locator.document_version_id != value["documentVersionId"]:
        raise ParserV2ApprovalError(
            "approved snapshot locator belongs to another document version"
        )
    payload = dict(value)
    payload["sourceLocator"] = locator.model_dump(by_alias=True, mode="json", exclude_none=True)
    payload["projectId"] = project_id
    return payload


def _matches_prefix(field_key: str, prefixes: tuple[str, ...]) -> bool:
    return any(field_key.startswith(prefix) for prefix in prefixes)


def _semantic_id(payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"parser-v2-handoff:{digest}"


def _unique_mappings(values: Any) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for value in values:
        copied = dict(value)
        key = json.dumps(copied, ensure_ascii=False, sort_keys=True, default=str)
        unique[key] = copied
    return [unique[key] for key in sorted(unique)]
