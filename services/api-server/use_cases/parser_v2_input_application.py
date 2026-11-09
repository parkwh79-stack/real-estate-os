"""Explicit application of three approved identity/site inputs, never raw candidates.

The snapshot and its effective validity are read under the same Parser V2
project advisory lock as approval writes. The project patch, immutable source
bindings and application receipt commit together through the existing CAS repo.
No financial, parcel-entity, legal-opinion or whole-Pod approval is implied.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Mapping

from parsing_platform.field_registry import FIELD_REGISTRY_VERSION, validate_candidate_against_registry
from use_cases.parser_v2_records import ParserV2ApprovalError, ParserV2RecordStore, approved_input_snapshot_wire_payload

SCHEMA = "realytics-parser-input-application-v1"
# Existing project/land runtime keys; no financial or entity-scoped projection.
TARGETS = {"project.name": ("project", "name"), "land.address": ("land", "address"), "land.area_sqm": ("land", "area_sqm")}


class ParserInputApplicationUnavailable(RuntimeError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _approved_items(store: ParserV2RecordStore, tenant_id: str, project_id: str, snapshot_id: str) -> tuple[dict, list[dict]]:
    record = next((row for row in store.list_approved_input_snapshots(tenant_id, project_id, active_only=True)
                   if row.get("snapshotId") == snapshot_id), None)
    if record is None:
        raise ParserV2ApprovalError("approved snapshot is absent or invalidated")
    snapshot = approved_input_snapshot_wire_payload(record)
    if snapshot.get("approvalStatus") != "approved" or snapshot.get("fieldRegistryVersion") != FIELD_REGISTRY_VERSION:
        raise ParserV2ApprovalError("snapshot approval or field registry is no longer current")
    selections = {row["selectionId"]: row for row in store.list_canonical_selections(tenant_id, project_id, active_only=True)}
    candidates = {row["candidateId"]: row for row in store.list_candidates(tenant_id, project_id)}
    runs = {row["parsingRunId"]: row for row in store.list_parsing_runs(tenant_id, project_id)}
    versions = {row["documentVersionId"]: row for row in store.list_document_versions(tenant_id, project_id)}
    reviews = store.list_review_decisions(tenant_id, project_id)
    conflicts = store.list_source_conflicts(tenant_id, project_id)
    items = []
    for item in snapshot.get("inputItems") or []:
        if item.get("fieldKey") not in TARGETS or item.get("entityId") is not None:
            continue
        selection = selections.get(item["selectionId"])
        if not selection or selection.get("candidateId") != item["candidateId"] or selection.get("reviewDecisionId") != item["reviewDecisionId"]:
            raise ParserV2ApprovalError("snapshot canonical selection is no longer active")
        candidate = candidates.get(item["candidateId"])
        if candidate is None:
            raise ParserV2ApprovalError("approved candidate is no longer available")
        metadata = candidate.get("metadata") or {}
        if metadata.get("referenceOnly") or metadata.get("noFuturePfProjection") or "FACT_BASIS_REQUIRED" in (candidate.get("validationCodes") or []):
            raise ParserV2ApprovalError("reference-only candidates cannot be applied as project inputs")
        decisions = [row for row in reviews if row.get("candidateId") == item["candidateId"]]
        if not decisions or decisions[-1].get("reviewDecisionId") != item["reviewDecisionId"] or decisions[-1].get("action") != "approve":
            raise ParserV2ApprovalError("snapshot does not reference the latest approved review")
        run = runs.get(item["parsingRunId"], {})
        if run.get("executionStatus") not in {"completed", "completed_with_warnings"}:
            raise ParserV2ApprovalError("snapshot parsing run is not successfully completed")
        version = versions.get(item["documentVersionId"], {})
        for key in ("sourceHash", "sha256"):
            if item.get(key) != candidate.get(key) or item.get(key) != version.get(key):
                raise ParserV2ApprovalError("snapshot source version/hash changed")
        if item.get("normalizedValue") != candidate.get("normalizedValue") or item.get("unit") != candidate.get("unit"):
            raise ParserV2ApprovalError("snapshot value/unit no longer matches its candidate")
        issues = validate_candidate_against_registry(
            ontology_version=str(candidate.get("ontologyVersion") or ""), field_key=item["fieldKey"],
            normalized_value=item.get("normalizedValue"), unit=item.get("unit"), entity_id=None,
            evidence=candidate.get("evidence") or [], presence_status=str(candidate.get("presenceStatus") or ""),
            origin_type=str(candidate.get("originType") or ""), metadata=metadata,
        )
        if any(issue.blocking for issue in issues):
            raise ParserV2ApprovalError("approved input fails current governed field validation")
        if any(row.get("conflictStatus") == "open" and item["candidateId"] in row.get("candidateIds", [])
               for row in conflicts):
            raise ParserV2ApprovalError("approved input has an unresolved source conflict")
        items.append(deepcopy(item))
    if not items:
        raise ParserV2ApprovalError("snapshot has no supported project-level identity/site input")
    return snapshot, items


def build_parser_input_application_preview(*, store: ParserV2RecordStore, tenant_id: str, project_id: str,
                                         snapshot_id: str, project: Mapping[str, Any]) -> dict:
    snapshot, inputs = _approved_items(store, tenant_id, project_id, snapshot_id)
    version = (project.get("_meta") or {}).get("stateVersion")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ParserInputApplicationUnavailable("versioned project authority is required")
    items = []
    for item in inputs:
        group, key = TARGETS[item["fieldKey"]]
        current = (project.get(group) or {}).get(key)
        items.append({"fieldKey": item["fieldKey"], "targetPath": ".".join((group, key)),
                      "currentValue": deepcopy(current), "approvedValue": item.get("normalizedValue"),
                      "unit": item.get("unit"), "selectionId": item["selectionId"], "candidateId": item["candidateId"],
                      "documentVersionId": item["documentVersionId"], "sourceHash": item["sourceHash"],
                      "sourceLocator": item["sourceLocator"], "changed": current != item.get("normalizedValue")})
    result = {"schemaVersion": SCHEMA, "projectId": project_id, "snapshotId": snapshot_id,
              "snapshotHash": _digest(snapshot), "expectedStateVersion": version, "items": items,
              "lastApplication": deepcopy(project.get("parserInputApplication")),
              "limitations": ["선택한 사업 기본 입력만 반영합니다. 필지별 면적·법적 권리·인허가·전체 Pod 승인이나 재무 승인을 뜻하지 않습니다."]}
    result["previewHash"] = _digest(result)
    return result


def apply_parser_approved_inputs(*, tenant_id: str, project_id: str, snapshot_id: str, actor_id: str,
                                application_id: str, field_keys: list[str], expected_snapshot_hash: str,
                                expected_state_version: int, expected_preview_hash: str,
                                transaction_pair: Callable | None = None) -> dict:
    if not field_keys or len(set(field_keys)) != len(field_keys) or set(field_keys) - TARGETS.keys():
        raise ValueError("select supported input fields without duplicates")
    with (transaction_pair or _runtime_transaction_pair)(tenant_id, project_id) as (store, repository):
        project = repository.get(project_id)
        if not project:
            raise LookupError("Project not found")
        preview = build_parser_input_application_preview(store=store, tenant_id=tenant_id, project_id=project_id,
                                                        snapshot_id=snapshot_id, project=project)
        if preview["snapshotHash"] != expected_snapshot_hash:
            raise ParserV2ApprovalError("approved snapshot hash changed")
        by_key = {item["fieldKey"]: item for item in preview["items"]}
        if set(field_keys) - by_key.keys():
            raise ParserV2ApprovalError("snapshot does not contain every selected input")
        request_hash = _digest({"applicationId": application_id, "snapshotId": snapshot_id,
                                "snapshotHash": expected_snapshot_hash, "fieldKeys": sorted(field_keys),
                                "expectedStateVersion": expected_state_version, "expectedPreviewHash": expected_preview_hash,
                                "actorId": actor_id})
        previous = project.get("parserInputApplication") or {}
        if previous.get("applicationId") == application_id:
            if previous.get("requestHash") != request_hash:
                raise ParserV2ApprovalError("application identity reused with different content")
            if any(by_key[key]["currentValue"] != by_key[key]["approvedValue"] for key in field_keys):
                raise ParserV2ApprovalError("previously applied inputs have since changed")
            return {"applicationId": application_id, "status": "already_applied", "appliedFieldKeys": sorted(field_keys)}
        if preview["expectedStateVersion"] != expected_state_version or preview["previewHash"] != expected_preview_hash:
            raise ParserV2ApprovalError("project inputs changed after preview")
        # Build the complete next envelope, preserving unselected field bindings
        # while replacing each selected binding as a whole immutable source row.
        # The project repository replaces this top-level envelope, not its leaves.
        bindings = project.get("parserApprovedInputBindings") or {}
        if not isinstance(bindings, Mapping):
            raise ParserV2ApprovalError("existing approved input bindings are malformed")
        patch: dict[str, Any] = {"parserApprovedInputBindings": deepcopy(dict(bindings))}
        for field_key in field_keys:
            item = by_key[field_key]
            group, key = TARGETS[field_key]
            patch.setdefault(group, {})[key] = deepcopy(item["approvedValue"])
            patch["parserApprovedInputBindings"][field_key] = {
                **deepcopy(item), "snapshotId": snapshot_id, "snapshotHash": expected_snapshot_hash,
                "applicationId": application_id, "appliedBy": actor_id,
            }
        patch["parserInputApplication"] = {"applicationId": application_id, "requestHash": request_hash,
            "snapshotId": snapshot_id, "snapshotHash": expected_snapshot_hash, "fieldKeys": sorted(field_keys),
            "appliedBy": actor_id, "appliedAt": datetime.now(timezone.utc).isoformat()}
        updated = repository.update(project_id, patch, expected_state_version=expected_state_version)
        if updated is None:
            raise LookupError("Project was deleted before approved inputs could be applied")
        return {"applicationId": application_id, "status": "applied", "appliedFieldKeys": sorted(field_keys)}


@contextmanager
def _runtime_transaction_pair(tenant_id: str, project_id: str):
    from db.repositories.request_project_repository import current_project_scope
    from db.repositories.parser_v2_repository import ParserV2PostgresRepository
    from db.repositories.postgres_project_repository import PostgresProjectRepository
    from db.postgres_runtime_pool import api_tenant_transaction
    scope = current_project_scope()
    if scope is None or scope.tenant_id != tenant_id:
        raise ParserInputApplicationUnavailable("normalized verified project authority is required")
    with (scope.transaction_factory or api_tenant_transaction)(tenant_id) as connection:
        session = ParserV2PostgresRepository().bind(connection, tenant_id, project_id)

        def records(_project_id: str, *, record_type: str, limit: int = 1000):
            return session.list_records(record_type, limit=limit)

        def readonly(*_args, **_kwargs):
            raise RuntimeError("input application may not mutate Parser V2 records")

        @contextmanager
        def same_connection(_tenant_id: str):
            if _tenant_id != tenant_id:
                raise ValueError("transaction tenant mismatch")
            yield connection

        store = ParserV2RecordStore(upsert_fn=readonly, list_fn=records)
        repository = PostgresProjectRepository(tenant_id=tenant_id, transaction_factory=same_connection)
        yield store, repository
