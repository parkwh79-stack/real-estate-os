from __future__ import annotations

"""Govern approved workbook facts through the Parser V2 approval ledger.

This bridge intentionally covers only four project-level facts.  It does not
silently promote every value in a workbook and it never treats workbook
approval as a substitute for Parser V2 review, selection, and snapshot gates.
"""

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence

from parsing_platform.contracts import ApprovedInputSnapshot, SourceLocator
from parsing_platform.fact_basis import FinancingFactBasis, RevenueCostFactBasis
from parsing_platform.field_registry import validate_candidate_against_registry
from schemas.feasibility_study import FeasibilityStudyV2
from use_cases.parser_v2_records import (
    ImmutableRecordConflict,
    ParserV2ApprovalError,
    ParserV2RecordStore,
    approved_input_snapshot_wire_payload,
)
from use_cases.approved_workbook_authority import (
    GOVERNED_WORKBOOK_CORE_FIELDS,
)


WORKBOOK_BRIDGE_VERSION = "workbook-parser-v2-bridge-1.0.0"
WORKBOOK_BRIDGE_ONTOLOGY_VERSION = "realytics-real-estate-domain-standard-v1"
WORKBOOK_BRIDGE_PURPOSE = "feasibility_workbook_governed_core"
WORKBOOK_BRIDGE_ACTOR = "system:workbook-parser-v2-bridge"
WORKBOOK_SNAPSHOT_GATE_ACTOR = "system:workbook-parser-v2-snapshot-gate"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

class WorkbookParserV2BridgeError(ParserV2ApprovalError):
    """The approved workbook cannot safely enter the governed fact ledger."""


class WorkbookParserV2BridgeBlocked(WorkbookParserV2BridgeError):
    """Preflight found a blocking basis or field-registry issue.

    ``coverage`` is safe to return to an API caller.  No Parser V2 record has
    been written when this exception is raised.
    """

    def __init__(
        self,
        message: str,
        *,
        coverage: Sequence[Mapping[str, Any]],
        validation_issues: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        super().__init__(message)
        self.coverage = [dict(item) for item in coverage]
        self.validation_issues = [dict(item) for item in validation_issues]


@dataclass(frozen=True)
class _FieldSpec:
    field_key: str
    canonical_path: str
    unit: str
    mapped_cell_keys: tuple[str, ...]
    target_modules: tuple[str, ...]
    monetary_kind: str | None = None


@dataclass(frozen=True)
class _PreparedFact:
    spec: _FieldSpec
    value: float
    locator: Dict[str, Any]
    metadata: Dict[str, Any]
    origin_type: str
    coverage_index: int


_FIELD_SPECS = (
    _FieldSpec(
        "program.gross_floor_area_sqm",
        "program.gross_floor_area_sqm.value",
        "SQM",
        (
            "grossFloorArea",
            "grossFloorAreaSqm",
            "program.gross_floor_area_sqm.value",
            "program.gross_floor_area_sqm",
        ),
        ("feasibility_cashflow",),
    ),
    _FieldSpec(
        "revenue.total_sales_revenue",
        "metrics.total_revenue",
        "KRW",
        (
            "totalRevenue",
            "metrics.total_revenue",
            "revenue.total_sales_revenue",
        ),
        ("feasibility_cashflow", "pf_credit"),
        "revenue_cost",
    ),
    _FieldSpec(
        "cost.total_project_cost",
        "metrics.total_cost",
        "KRW",
        ("totalCost", "totalProjectCost", "metrics.total_cost"),
        ("feasibility_cashflow", "pf_credit"),
        "revenue_cost",
    ),
    _FieldSpec(
        "financing.pf_amount",
        "financing[].amount",
        "KRW",
        ("pfAmount", "financing.pf_amount"),
        ("feasibility_cashflow", "pf_credit"),
        "financing",
    ),
)
if tuple(spec.field_key for spec in _FIELD_SPECS) != (
    GOVERNED_WORKBOOK_CORE_FIELDS
):
    raise RuntimeError(
        "workbook bridge field specs must match the governed core-field contract"
    )


def bridge_approved_workbook_to_parser_v2(
    *,
    record_store: ParserV2RecordStore,
    tenant_id: str,
    project_id: str,
    import_id: str,
    canonical_study: Mapping[str, Any],
    source_document: Mapping[str, Any],
    approved_by: str,
) -> Dict[str, Any]:
    """Create an idempotent candidate-to-snapshot chain for approved facts."""

    tenant = _required(tenant_id, "tenant_id")
    project = _required(project_id, "project_id")
    workbook_import_id = _required(import_id, "import_id")
    reviewer = _required(approved_by, "approved_by")
    if reviewer in {WORKBOOK_BRIDGE_ACTOR, WORKBOOK_SNAPSHOT_GATE_ACTOR}:
        raise WorkbookParserV2BridgeError(
            "approved_by must identify the human workbook reviewer"
        )

    study = FeasibilityStudyV2.model_validate(canonical_study).model_dump(
        mode="json"
    )
    if study.get("project_id") and str(study["project_id"]) != project:
        raise WorkbookParserV2BridgeError(
            "canonical workbook project_id does not match project_id"
        )
    raw_import = dict(study.get("raw_import") or {})
    if str(raw_import.get("canonicalStatus") or "").casefold() != "approved":
        raise WorkbookParserV2BridgeError(
            "workbook bridge requires canonicalStatus=approved"
        )
    _assert_human_approval_binding(
        raw_import=raw_import,
        import_id=workbook_import_id,
        reviewer=reviewer,
    )

    source = _verified_workbook_source(source_document)
    source_hash = source["sourceHash"]
    _assert_canonical_source_binding(raw_import, source_hash)
    id_material = {
        "tenantId": tenant,
        "projectId": project,
        "importId": workbook_import_id,
        "sourceHash": source_hash,
    }
    document_id = _semantic_id("workbook-document", {"sourceHash": source_hash})
    document_version_id = _semantic_id(
        "workbook-document-version",
        {
            "archiveId": source["archiveId"],
            "objectVersion": source["objectVersion"],
            "sourceHash": source_hash,
        },
    )
    run_id = _semantic_id("workbook-parse", id_material)

    coverage, prepared = _prepare_facts(
        study=study,
        raw_import=raw_import,
        source=source,
        document_id=document_id,
        document_version_id=document_version_id,
        import_id=workbook_import_id,
    )
    _raise_for_blocking_preflight(coverage, prepared)
    if not prepared:
        return _bridge_result(
            status="no_approvable_facts",
            tenant_id=tenant,
            project_id=project,
            import_id=workbook_import_id,
            run_id=run_id,
            coverage=coverage,
            snapshot=None,
        )

    input_hash = _stable_hash(
        [
            {
                "fieldKey": item.spec.field_key,
                "value": item.value,
                "unit": item.spec.unit,
                "locator": item.locator,
                "metadata": item.metadata,
                "originType": item.origin_type,
            }
            for item in prepared
        ]
    )
    candidate_ids = {
        item.spec.field_key: _semantic_id(
            "workbook-candidate",
            {"runId": run_id, "fieldKey": item.spec.field_key},
        )
        for item in prepared
    }
    decision_ids = {
        field_key: _semantic_id(
            "workbook-review",
            {
                "candidateId": candidate_id,
                "reviewerId": reviewer,
                "importId": workbook_import_id,
            },
        )
        for field_key, candidate_id in candidate_ids.items()
    }
    selection_ids = {
        field_key: _semantic_id(
            "workbook-selection",
            {
                "candidateId": candidate_ids[field_key],
                "reviewDecisionId": decision_ids[field_key],
            },
        )
        for field_key in candidate_ids
    }
    snapshot_id = _semantic_id(
        "workbook-snapshot",
        {
            **id_material,
            "reviewerId": reviewer,
            "selectionIds": [
                selection_ids[item.spec.field_key] for item in prepared
            ],
            "purpose": WORKBOOK_BRIDGE_PURPOSE,
        },
    )

    existing_snapshot = _find_by_id(
        record_store.list_approved_input_snapshots(tenant, project),
        "snapshotId",
        snapshot_id,
    )
    if existing_snapshot is not None:
        ApprovedInputSnapshot.model_validate(
            approved_input_snapshot_wire_payload(existing_snapshot)
        )
        if (
            str(existing_snapshot.get("approvalStatus") or "").casefold()
            != "approved"
            or str(existing_snapshot.get("validityStatus") or "").casefold()
            != "active"
        ):
            raise WorkbookParserV2BridgeError(
                "existing workbook snapshot is superseded or invalidated; "
                "only approvalStatus=approved and validityStatus=active may be replayed"
            )
        for item in prepared:
            index = item.coverage_index
            coverage[index] = {
                **coverage[index],
                "status": "approved",
                "candidateId": candidate_ids[item.spec.field_key],
                "selectionId": selection_ids[item.spec.field_key],
            }
        return _bridge_result(
            status="already_bridged",
            tenant_id=tenant,
            project_id=project,
            import_id=workbook_import_id,
            run_id=run_id,
            coverage=coverage,
            snapshot=existing_snapshot,
        )

    record_store.create_document_version(
        tenant_id=tenant,
        project_id=project,
        document_id=document_id,
        document_version_id=document_version_id,
        actor_id=WORKBOOK_BRIDGE_ACTOR,
        archive_id=source["archiveId"],
        object_version=source["objectVersion"],
        source_hash=source_hash,
        media_type=source["mediaType"],
        original_file_name=source["originalFileName"],
        size_bytes=source["sizeBytes"],
        source_authority="submitted_document",
    )
    run = record_store.create_parsing_run(
        tenant_id=tenant,
        project_id=project,
        run_id=run_id,
        document_version_id=document_version_id,
        actor_id=WORKBOOK_BRIDGE_ACTOR,
        parser_version=WORKBOOK_BRIDGE_VERSION,
        ontology_version=WORKBOOK_BRIDGE_ONTOLOGY_VERSION,
        prompt_version="approved-workbook-bridge-no-llm-v1",
        model_version="deterministic-workbook-import",
        profile_id="approved_feasibility_workbook",
        profile_version="1.0.0",
        plan_hash=_stable_hash(
            [
                {
                    "fieldKey": item.field_key,
                    "canonicalPath": item.canonical_path,
                    "unit": item.unit,
                }
                for item in _FIELD_SPECS
            ]
        ),
        input_hash=input_hash,
        requested_modules=sorted(
            {module for item in prepared for module in item.spec.target_modules}
        ),
        idempotency_key=_semantic_id(
            "workbook-bridge-idempotency",
            {**id_material, "inputHash": input_hash},
        ),
    )
    if run.get("executionStatus") == "pending":
        record_store.append_parsing_run_event(
            tenant_id=tenant,
            project_id=project,
            run_id=run_id,
            actor_id=WORKBOOK_BRIDGE_ACTOR,
            execution_status="running",
        )
        run = record_store.get_parsing_run(tenant, project, run_id)
    if run.get("executionStatus") not in {"running", "completed"}:
        raise WorkbookParserV2BridgeError(
            "workbook bridge parsing run is not resumable"
        )

    candidates: Dict[str, Dict[str, Any]] = {}
    for item in prepared:
        field_key = item.spec.field_key
        candidate_id = candidate_ids[field_key]
        existing_candidate = _find_by_id(
            record_store.list_candidates(tenant, project, run_id=run_id),
            "candidateId",
            candidate_id,
        )
        if existing_candidate is not None:
            _assert_candidate_replay(existing_candidate, item)
            candidates[field_key] = existing_candidate
            continue
        candidates[field_key] = record_store.create_candidate(
            tenant_id=tenant,
            project_id=project,
            run_id=run_id,
            document_version_id=document_version_id,
            candidate_id=candidate_id,
            actor_id=WORKBOOK_BRIDGE_ACTOR,
            canonical_field=field_key,
            value=item.value,
            normalized_value=item.value,
            raw_value=item.value,
            unit=item.spec.unit,
            origin_type=item.origin_type,
            presence_status="present",
            confidence=1.0,
            evidence=[item.locator],
            target_modules=item.spec.target_modules,
            authority="submitted_document",
            metadata=item.metadata,
        )
        if candidates[field_key].get("validationStatus") == "blocked":
            # This is defense in depth for a registry version changing between
            # preflight and persistence.
            raise WorkbookParserV2BridgeBlocked(
                "field registry blocked a workbook candidate",
                coverage=coverage,
                validation_issues=candidates[field_key].get(
                    "validationFindings"
                )
                or [],
            )

    run = record_store.get_parsing_run(tenant, project, run_id)
    if run.get("executionStatus") == "running":
        record_store.append_parsing_run_event(
            tenant_id=tenant,
            project_id=project,
            run_id=run_id,
            actor_id=WORKBOOK_BRIDGE_ACTOR,
            execution_status="completed",
        )
    elif run.get("executionStatus") != "completed":
        raise WorkbookParserV2BridgeError(
            "workbook bridge parsing run did not complete successfully"
        )

    record_store.seal_candidate_set(
        tenant_id=tenant,
        project_id=project,
        run_id=run_id,
        actor_id=WORKBOOK_BRIDGE_ACTOR,
        expected_candidate_ids=sorted(candidate_ids.values()),
        expected_conflict_ids=[],
    )

    decisions: Dict[str, Dict[str, Any]] = {}
    selections: Dict[str, Dict[str, Any]] = {}
    for item in prepared:
        field_key = item.spec.field_key
        candidate_id = candidate_ids[field_key]
        decision_id = decision_ids[field_key]
        existing_decisions = record_store.list_review_decisions(
            tenant,
            project,
            candidate_id=candidate_id,
        )
        decision_reason = (
            f"Human-approved feasibility workbook import "
            f"{workbook_import_id}: {field_key}"
        )
        existing_decision = _find_by_id(
            existing_decisions,
            "reviewDecisionId",
            decision_id,
        )
        if existing_decision is not None:
            _assert_review_decision_replay(
                existing_decision,
                candidate_id=candidate_id,
                reviewer=reviewer,
                reason=decision_reason,
            )
            decisions[field_key] = existing_decision
        else:
            decisions[field_key] = record_store.append_review_decision(
                tenant_id=tenant,
                project_id=project,
                candidate_id=candidate_id,
                actor_id=reviewer,
                action="approve",
                reason=decision_reason,
                decision_id=decision_id,
                expected_decision_sequence=len(existing_decisions),
            )

        selection_id = selection_ids[field_key]
        existing_selection = _find_by_id(
            record_store.list_canonical_selections(tenant, project),
            "selectionId",
            selection_id,
        )
        if existing_selection is not None:
            selections[field_key] = existing_selection
            continue
        field_history = [
            selection
            for selection in record_store.list_canonical_selections(
                tenant, project
            )
            if selection.get("fieldKey") == field_key
            and selection.get("entityId") is None
        ]
        previous_selection = (
            max(
                field_history,
                key=lambda value: int(value.get("selectionVersion") or 0),
            )
            if field_history
            else None
        )
        selections[field_key] = record_store.create_canonical_selection(
            tenant_id=tenant,
            project_id=project,
            selection_id=selection_id,
            actor_id=reviewer,
            candidate_id=candidate_id,
            review_decision_id=decisions[field_key]["reviewDecisionId"],
            selection_version=(
                int(previous_selection.get("selectionVersion") or 0) + 1
                if previous_selection
                else 1
            ),
            supersedes_selection_id=(
                str(previous_selection["selectionId"])
                if previous_selection
                else None
            ),
        )

    snapshot_history = [
        snapshot
        for snapshot in record_store.list_approved_input_snapshots(
            tenant, project
        )
        if snapshot.get("purpose") == WORKBOOK_BRIDGE_PURPOSE
    ]
    previous_snapshot = (
        max(
            snapshot_history,
            key=lambda value: int(value.get("snapshotVersion") or 0),
        )
        if snapshot_history
        else None
    )
    snapshot = record_store.create_approved_input_snapshot(
        tenant_id=tenant,
        project_id=project,
        snapshot_id=snapshot_id,
        actor_id=WORKBOOK_SNAPSHOT_GATE_ACTOR,
        selection_ids=[
            selections[item.spec.field_key]["selectionId"] for item in prepared
        ],
        purpose=WORKBOOK_BRIDGE_PURPOSE,
        supersedes_snapshot_id=(
            str(previous_snapshot["snapshotId"]) if previous_snapshot else None
        ),
    )
    ApprovedInputSnapshot.model_validate(
        approved_input_snapshot_wire_payload(snapshot)
    )

    for item in prepared:
        index = item.coverage_index
        coverage[index] = {
            **coverage[index],
            "status": "approved",
            "candidateId": candidates[item.spec.field_key]["candidateId"],
            "reviewDecisionId": decisions[item.spec.field_key][
                "reviewDecisionId"
            ],
            "selectionId": selections[item.spec.field_key]["selectionId"],
        }
    return _bridge_result(
        status="approved",
        tenant_id=tenant,
        project_id=project,
        import_id=workbook_import_id,
        run_id=run_id,
        coverage=coverage,
        snapshot=snapshot,
    )


def _prepare_facts(
    *,
    study: Mapping[str, Any],
    raw_import: Mapping[str, Any],
    source: Mapping[str, Any],
    document_id: str,
    document_version_id: str,
    import_id: str,
) -> tuple[list[Dict[str, Any]], list[_PreparedFact]]:
    coverage: list[Dict[str, Any]] = []
    prepared: list[_PreparedFact] = []
    basis_map = _basis_map(raw_import)
    mapped_cells = raw_import.get("mappedCells")
    mapped_cells = mapped_cells if isinstance(mapped_cells, Mapping) else {}

    for spec in _FIELD_SPECS:
        value = _field_value(study, spec)
        coverage_item: Dict[str, Any] = {
            "fieldKey": spec.field_key,
            "canonicalPath": spec.canonical_path,
            "unit": spec.unit,
        }
        coverage_index = len(coverage)
        if value is None:
            coverage_item["status"] = "missing_value"
            coverage.append(coverage_item)
            continue

        basis: Dict[str, Any] | None = None
        if spec.monetary_kind is not None:
            raw_basis = basis_map.get(spec.field_key)
            basis, basis_error = _validated_basis(
                field_key=spec.field_key,
                monetary_kind=spec.monetary_kind,
                raw_basis=raw_basis,
            )
            if basis_error:
                coverage_item.update(
                    {"status": "blocked_missing_or_invalid_basis", "reason": basis_error}
                )

        locator = _field_locator(
            study=study,
            spec=spec,
            mapped_cells=mapped_cells,
            raw_import=raw_import,
            source=source,
            document_id=document_id,
            document_version_id=document_version_id,
            import_id=import_id,
        )
        if locator is None:
            if "status" not in coverage_item:
                coverage_item.update(
                    {
                        "status": "missing_locator",
                        "reason": "exact workbook sheet/cell locator is required",
                    }
                )
            coverage.append(coverage_item)
            continue
        coverage_item["sourceLocator"] = locator
        lineage_status, lineage_reason = _workbook_lineage_blocker(
            study=study,
            raw_import=raw_import,
            spec=spec,
            locator=locator,
        )
        if lineage_status and "status" not in coverage_item:
            coverage_item.update(
                {
                    "status": lineage_status,
                    "reason": lineage_reason,
                }
            )
        if "status" in coverage_item:
            coverage.append(coverage_item)
            continue

        metadata: Dict[str, Any] = {
            "bridgeVersion": WORKBOOK_BRIDGE_VERSION,
            "workbookImportId": import_id,
            "canonicalPath": spec.canonical_path,
        }
        candidate_import = raw_import.get("candidateImport")
        if isinstance(candidate_import, Mapping):
            prepared_by = str(
                candidate_import.get("recordedBy") or ""
            ).strip()
            candidate_import_id = str(
                candidate_import.get("importId") or ""
            ).strip()
            if prepared_by:
                metadata["workbookPreparedBy"] = prepared_by
            if candidate_import_id:
                metadata["workbookCandidateImportId"] = candidate_import_id
        if basis is not None:
            metadata["basis"] = basis
        coverage_item["status"] = "ready"
        coverage.append(coverage_item)
        prepared.append(
            _PreparedFact(
                spec=spec,
                value=float(value),
                locator=locator,
                metadata=metadata,
                origin_type="extracted",
                coverage_index=coverage_index,
            )
        )
    return coverage, prepared


def _raise_for_blocking_preflight(
    coverage: Sequence[Mapping[str, Any]],
    prepared: Sequence[_PreparedFact],
) -> None:
    value_blockers = [
        dict(item)
        for item in coverage
        if item.get("status") == "missing_value"
    ]
    if value_blockers:
        raise WorkbookParserV2BridgeBlocked(
            "all four governed workbook core facts require approved values",
            coverage=coverage,
            validation_issues=value_blockers,
        )

    locator_blockers = [
        dict(item)
        for item in coverage
        if item.get("status") == "missing_locator"
    ]
    if locator_blockers:
        raise WorkbookParserV2BridgeBlocked(
            "a present core fact requires an exact workbook sheet/cell locator",
            coverage=coverage,
            validation_issues=locator_blockers,
        )

    basis_blockers = [
        dict(item)
        for item in coverage
        if item.get("status") == "blocked_missing_or_invalid_basis"
    ]
    if basis_blockers:
        raise WorkbookParserV2BridgeBlocked(
            "monetary fact basis is missing or invalid",
            coverage=coverage,
            validation_issues=basis_blockers,
        )

    formula_blockers = [
        dict(item)
        for item in coverage
        if item.get("status") == "blocked_formula_recalculation_required"
    ]
    if formula_blockers:
        raise WorkbookParserV2BridgeBlocked(
            "workbook formula values require deterministic recalculation lineage",
            coverage=coverage,
            validation_issues=formula_blockers,
        )

    derived_blockers = [
        dict(item)
        for item in coverage
        if item.get("status") == "blocked_derived_lineage_required"
    ]
    if derived_blockers:
        raise WorkbookParserV2BridgeBlocked(
            "workbook aggregate or calculated values require derived-fact lineage",
            coverage=coverage,
            validation_issues=derived_blockers,
        )

    registry_issues: list[Dict[str, Any]] = []
    for item in prepared:
        kwargs: Dict[str, Any] = {
            "ontology_version": WORKBOOK_BRIDGE_ONTOLOGY_VERSION,
            "field_key": item.spec.field_key,
            "normalized_value": item.value,
            "unit": item.spec.unit,
            "entity_id": None,
            "evidence": [item.locator],
            "presence_status": "present",
            "origin_type": item.origin_type,
        }
        if "metadata" in inspect.signature(
            validate_candidate_against_registry
        ).parameters:
            kwargs["metadata"] = item.metadata
        for issue in validate_candidate_against_registry(**kwargs):
            if not issue.blocking:
                continue
            registry_issues.append(
                {
                    "fieldKey": item.spec.field_key,
                    "code": issue.code,
                    "message": issue.message,
                    "blocking": True,
                }
            )
    if registry_issues:
        raise WorkbookParserV2BridgeBlocked(
            "field registry blocked one or more workbook facts",
            coverage=coverage,
            validation_issues=registry_issues,
        )


def _verified_workbook_source(value: Mapping[str, Any]) -> Dict[str, Any]:
    source = dict(value or {})
    if str(source.get("documentType") or "") != "feasibility_workbook":
        raise WorkbookParserV2BridgeError(
            "verified source document must be a feasibility_workbook"
        )
    if source.get("archiveStored") is not True:
        raise WorkbookParserV2BridgeError(
            "verified source document must be present in immutable object storage"
        )
    if str(source.get("archiveIntegrityStatus") or "") != "verified":
        raise WorkbookParserV2BridgeError(
            "verified source document requires archiveIntegrityStatus=verified"
        )
    validation = source.get("validation")
    validation = validation if isinstance(validation, Mapping) else {}
    validation_status = str(
        source.get("validationStatus") or validation.get("status") or ""
    ).casefold()
    if validation_status not in {"passed", "valid"}:
        raise WorkbookParserV2BridgeError(
            "verified source document requires passing workbook validation"
        )

    hash_values = {
        str(source.get(key) or "").strip().lower()
        for key in ("archiveId", "originalFileHash", "sha256")
        if str(source.get(key) or "").strip()
    }
    if len(hash_values) != 1:
        raise WorkbookParserV2BridgeError(
            "source document SHA-256 identities are missing or inconsistent"
        )
    source_hash = next(iter(hash_values))
    if _SHA256.fullmatch(source_hash) is None:
        raise WorkbookParserV2BridgeError(
            "source document requires a valid SHA-256 digest"
        )
    archive_id = str(source.get("archiveId") or "").strip().lower()
    if archive_id != source_hash:
        raise WorkbookParserV2BridgeError(
            "archiveId must equal the verified source SHA-256"
        )
    return {
        "archiveId": archive_id,
        "sourceHash": source_hash,
        "objectVersion": str(
            source.get("objectVersion")
            or source.get("archiveObjectKey")
            or f"sha256:{source_hash}"
        ),
        "mediaType": str(
            source.get("contentType")
            or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        "originalFileName": str(
            source.get("originalFileName") or f"{source_hash}.xlsx"
        ),
        "sizeBytes": int(source.get("fileSizeBytes") or 0),
    }


def _assert_canonical_source_binding(
    raw_import: Mapping[str, Any],
    source_hash: str,
) -> None:
    bound_sources: list[Mapping[str, Any]] = []
    raw_source = raw_import.get("sourceDocument")
    if isinstance(raw_source, Mapping):
        bound_sources.append(raw_source)
    approval = raw_import.get("approval")
    if isinstance(approval, Mapping) and isinstance(
        approval.get("sourceDocument"), Mapping
    ):
        bound_sources.append(approval["sourceDocument"])

    for bound_source in bound_sources:
        hashes = {
            str(bound_source.get(key) or "").strip().lower()
            for key in ("archiveId", "originalFileHash", "sha256")
            if str(bound_source.get(key) or "").strip()
        }
        if hashes and hashes != {source_hash}:
            raise WorkbookParserV2BridgeError(
                "canonical workbook is bound to another source document"
            )

    candidate_import = raw_import.get("candidateImport")
    if isinstance(candidate_import, Mapping):
        candidate_hashes = {
            str(value).strip().lower()
            for value in candidate_import.get("sourceDocumentHashes") or []
            if str(value).strip()
        }
        if candidate_hashes and source_hash not in candidate_hashes:
            raise WorkbookParserV2BridgeError(
                "workbook candidate lineage does not include the approved source"
            )


def _assert_human_approval_binding(
    *,
    raw_import: Mapping[str, Any],
    import_id: str,
    reviewer: str,
) -> None:
    candidate = raw_import.get("candidateImport")
    approval = raw_import.get("approval")
    if not isinstance(candidate, Mapping) or not isinstance(
        approval, Mapping
    ):
        raise WorkbookParserV2BridgeError(
            "workbook bridge requires persisted candidate and approval lineage"
        )

    candidate_id = str(candidate.get("importId") or "").strip()
    maker = str(candidate.get("recordedBy") or "").strip()
    candidate_status = str(candidate.get("status") or "").strip().casefold()
    draft_hash = str(candidate.get("draftHash") or "").strip().lower()
    if (
        not candidate_id
        or not maker
        or candidate_status != "reviewed"
        or _SHA256.fullmatch(draft_hash) is None
    ):
        raise WorkbookParserV2BridgeError(
            "workbook candidate lineage is incomplete or not reviewed"
        )
    if maker == reviewer:
        raise WorkbookParserV2BridgeError(
            "workbook maker-checker requires different preparer and reviewer"
        )

    expected = {
        "importId": import_id,
        "candidateImportId": candidate_id,
        "preparedBy": maker,
        "approvedBy": reviewer,
    }
    mismatched = [
        key
        for key, value in expected.items()
        if str(approval.get(key) or "").strip() != value
    ]
    if mismatched:
        raise WorkbookParserV2BridgeError(
            "workbook approval lineage differs for: " + ", ".join(mismatched)
        )


def _basis_map(raw_import: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("governedFactBasis", "governed_fact_basis"):
        value = raw_import.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _validated_basis(
    *,
    field_key: str,
    monetary_kind: str,
    raw_basis: Any,
) -> tuple[Dict[str, Any] | None, str | None]:
    if not isinstance(raw_basis, Mapping):
        return None, f"{field_key} requires raw_import.governedFactBasis"
    model_type = (
        FinancingFactBasis
        if monetary_kind == "financing"
        else RevenueCostFactBasis
    )
    try:
        model = model_type.model_validate(dict(raw_basis))
    except ValueError as exc:
        return None, f"{field_key} basis violates its governed contract: {exc}"
    return model.model_dump(by_alias=True, mode="json", exclude_none=True), None


def _field_value(study: Mapping[str, Any], spec: _FieldSpec) -> Any:
    if spec.field_key == "program.gross_floor_area_sqm":
        return (
            ((study.get("program") or {}).get("gross_floor_area_sqm") or {}).get(
                "value"
            )
        )
    if spec.field_key == "revenue.total_sales_revenue":
        return (study.get("metrics") or {}).get("total_revenue")
    if spec.field_key == "cost.total_project_cost":
        return (study.get("metrics") or {}).get("total_cost")
    if spec.field_key == "financing.pf_amount":
        debt = [
            item
            for item in study.get("financing") or []
            if item.get("priority") in {"senior", "mezzanine", "junior", "other"}
            and item.get("amount") is not None
        ]
        return sum(float(item["amount"]) for item in debt) if debt else None
    raise AssertionError(f"unsupported governed field: {spec.field_key}")


def _field_locator(
    *,
    study: Mapping[str, Any],
    spec: _FieldSpec,
    mapped_cells: Mapping[str, Any],
    raw_import: Mapping[str, Any],
    source: Mapping[str, Any],
    document_id: str,
    document_version_id: str,
    import_id: str,
) -> Dict[str, Any] | None:
    reference: Any = next(
        (
            mapped_cells[key]
            for key in spec.mapped_cell_keys
            if key in mapped_cells and mapped_cells[key]
        ),
        None,
    )
    if reference is None:
        reference = _value_provenance(study, spec.field_key)
    anchor = _workbook_anchor(reference)
    if anchor is None:
        return None
    locator: Dict[str, Any] = {
        "sourceName": source["originalFileName"],
        "sourceType": "xlsx",
        "documentId": document_id,
        "documentVersionId": document_version_id,
        "fileSha256": source["sourceHash"],
        "sheetName": anchor["sheetName"],
        "parserMethod": "approved_workbook_parser_v2_bridge",
        "metadata": {
            "workbookImportId": import_id,
            "canonicalField": spec.field_key,
        },
    }
    if isinstance(reference, Mapping):
        source_value_kind = str(reference.get("kind") or "").strip().casefold()
        if source_value_kind:
            locator["metadata"]["sourceValueKind"] = source_value_kind
    formula_sha256 = _workbook_formula_sha256(raw_import, anchor)
    if formula_sha256 is not None:
        locator["metadata"]["workbookFormulaSha256"] = formula_sha256
    if anchor["isRange"]:
        locator["cellRange"] = anchor["cell"]
    else:
        locator["cellReference"] = anchor["cell"]
    locator["evidenceId"] = _semantic_id(
        "workbook-evidence",
        {
            "documentVersionId": document_version_id,
            "fieldKey": spec.field_key,
            "sheetName": anchor["sheetName"],
            "cell": anchor["cell"],
        },
    )
    return SourceLocator.model_validate(locator).model_dump(
        by_alias=True,
        mode="json",
        exclude_none=True,
    )


def _workbook_formula_sha256(
    raw_import: Mapping[str, Any],
    anchor: Mapping[str, Any],
) -> str | None:
    named_values = raw_import.get("namedRangeValues")
    if not isinstance(named_values, Mapping):
        return None
    expected_sheet = _normalized_sheet_name(anchor.get("sheetName"))
    expected_cell = _normalized_cell_reference(anchor.get("cell"))
    for record in named_values.values():
        if not isinstance(record, Mapping):
            continue
        formula = record.get("formula")
        if not isinstance(formula, str) or not formula.strip().startswith("="):
            continue
        destinations = record.get("destinations")
        if not isinstance(destinations, Sequence) or isinstance(
            destinations, (str, bytes, bytearray)
        ):
            continue
        for destination in destinations:
            if not isinstance(destination, Mapping):
                continue
            if (
                _normalized_sheet_name(destination.get("sheetName"))
                == expected_sheet
                and _normalized_cell_reference(
                    destination.get("range")
                    or destination.get("cell")
                    or destination.get("cellReference")
                )
                == expected_cell
            ):
                return hashlib.sha256(
                    formula.strip().encode("utf-8")
                ).hexdigest()
    return None


def _workbook_lineage_blocker(
    *,
    study: Mapping[str, Any],
    raw_import: Mapping[str, Any],
    spec: _FieldSpec,
    locator: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    metadata = locator.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    if metadata.get("workbookFormulaSha256"):
        return (
            "blocked_formula_recalculation_required",
            "a cached Excel formula is not an authoritative extracted fact; "
            "a deterministic CalculationRun output is required",
        )

    source_kind = str(metadata.get("sourceValueKind") or "").casefold()
    profile_kind = _sample_profile_value_kind(raw_import, spec.field_key)
    if "calculated" in {source_kind, profile_kind}:
        return (
            "blocked_derived_lineage_required",
            "the workbook profile classifies this scalar as calculated, but "
            "formula, input fact, and CalculationRun lineage are absent",
        )

    if locator.get("cellRange"):
        return (
            "blocked_derived_lineage_required",
            "a governed scalar cannot be approved from a multi-cell range "
            "without an explicit aggregation CalculationRun",
        )

    if spec.field_key == "financing.pf_amount":
        debt_count = sum(
            1
            for item in study.get("financing") or []
            if item.get("priority")
            in {"senior", "mezzanine", "junior", "other"}
            and item.get("amount") is not None
        )
        if debt_count != 1:
            return (
                "blocked_derived_lineage_required",
                "financing.pf_amount aggregates multiple debt tranches and "
                "requires explicit inputFactIds and CalculationRun lineage",
            )
    return None, None


def _sample_profile_value_kind(
    raw_import: Mapping[str, Any],
    field_key: str,
) -> str:
    profile = raw_import.get("sampleProfile")
    if not isinstance(profile, Mapping):
        return ""
    locators = profile.get("fieldLocators")
    if not isinstance(locators, Mapping):
        return ""
    aliases = {
        "program.gross_floor_area_sqm": (
            "program.gross_floor_area_sqm",
        ),
        "revenue.total_sales_revenue": (
            "revenue.total_sales_revenue",
            "revenue.total",
        ),
        "cost.total_project_cost": (
            "cost.total_project_cost",
            "cost.total",
        ),
    }.get(field_key, (field_key,))
    for alias in aliases:
        locator = locators.get(alias)
        if isinstance(locator, Mapping):
            return str(locator.get("kind") or "").strip().casefold()
    return ""


def _normalized_sheet_name(value: Any) -> str:
    return str(value or "").strip().strip("'").replace("''", "'").casefold()


def _normalized_cell_reference(value: Any) -> str:
    return str(value or "").strip().replace("$", "").upper()


def _value_provenance(
    study: Mapping[str, Any],
    field_key: str,
) -> Mapping[str, Any] | None:
    if field_key == "program.gross_floor_area_sqm":
        value = (study.get("program") or {}).get("gross_floor_area_sqm") or {}
        return value.get("provenance")
    if field_key == "revenue.total_sales_revenue":
        items = study.get("revenue_items") or []
        keys = {"total_revenue", "total_sales_revenue", "sales_revenue"}
    elif field_key == "cost.total_project_cost":
        items = study.get("cost_items") or []
        keys = {"total_cost", "total_project_cost"}
    else:
        return None
    matches = [
        item.get("provenance")
        for item in items
        if item.get("key") in keys and isinstance(item.get("provenance"), Mapping)
    ]
    return matches[0] if len(matches) == 1 else None


def _workbook_anchor(value: Any) -> Dict[str, Any] | None:
    if isinstance(value, Mapping):
        sheet = value.get("sheetName") or value.get("sheet_name")
        cell = (
            value.get("cellReference")
            or value.get("cell_reference")
            or value.get("cellRange")
            or value.get("cell_range")
            or value.get("cell")
        )
        if sheet and cell:
            rendered = str(cell).strip()
            return {
                "sheetName": str(sheet).strip().strip("'"),
                "cell": rendered,
                "isRange": ":" in rendered or "," in rendered,
            }
        return None
    text = str(value or "").strip()
    if "!" not in text:
        return None
    sheet, cell = text.rsplit("!", 1)
    sheet = sheet.strip().strip("'")
    cell = cell.strip()
    if not sheet or not cell:
        return None
    return {
        "sheetName": sheet,
        "cell": cell,
        "isRange": ":" in cell or "," in cell,
    }


def _assert_candidate_replay(
    candidate: Mapping[str, Any],
    prepared: _PreparedFact,
) -> None:
    expected = {
        "canonicalField": prepared.spec.field_key,
        "normalizedValue": prepared.value,
        "unit": prepared.spec.unit,
        "originType": prepared.origin_type,
    }
    mismatched = [
        key for key, value in expected.items() if candidate.get(key) != value
    ]
    if mismatched:
        raise ImmutableRecordConflict(
            "workbook bridge candidate replay differs for: "
            + ", ".join(mismatched)
        )
    persisted_metadata = candidate.get("metadata")
    if not isinstance(persisted_metadata, Mapping) or any(
        persisted_metadata.get(key) != value
        for key, value in prepared.metadata.items()
    ):
        raise ImmutableRecordConflict(
            "workbook bridge candidate replay differs for: metadata"
        )


def _assert_review_decision_replay(
    decision: Mapping[str, Any],
    *,
    candidate_id: str,
    reviewer: str,
    reason: str,
) -> None:
    expected = {
        "candidateId": candidate_id,
        "reviewerId": reviewer,
        "action": "approve",
        "reason": reason,
        "resultingReviewStatus": "approved",
        "resultingApprovalStatus": "approved",
    }
    if any(decision.get(key) != value for key, value in expected.items()):
        raise ImmutableRecordConflict(
            "workbook review decision replay changed immutable content"
        )


def _bridge_result(
    *,
    status: str,
    tenant_id: str,
    project_id: str,
    import_id: str,
    run_id: str,
    coverage: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    items = [dict(item) for item in coverage]
    return {
        "schemaVersion": "realytics-workbook-parser-v2-bridge-result-v1",
        "status": status,
        "tenantId": tenant_id,
        "projectId": project_id,
        "importId": import_id,
        "parsingRunId": run_id,
        "purpose": WORKBOOK_BRIDGE_PURPOSE,
        "coverage": items,
        "approvedFields": [
            item["fieldKey"] for item in items if item.get("status") == "approved"
        ],
        "missingFields": [
            {
                "fieldKey": item["fieldKey"],
                "status": item.get("status"),
                "reason": item.get("reason"),
            }
            for item in items
            if item.get("status") != "approved"
        ],
        "snapshot": dict(snapshot) if snapshot is not None else None,
    }


def _find_by_id(
    values: Sequence[Mapping[str, Any]],
    key: str,
    expected: str,
) -> Dict[str, Any] | None:
    return next(
        (dict(item) for item in values if str(item.get(key) or "") == expected),
        None,
    )


def _required(value: Any, label: str) -> str:
    rendered = str(value or "").strip()
    if not rendered:
        raise WorkbookParserV2BridgeError(f"{label} is required")
    return rendered


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _semantic_id(prefix: str, value: Any) -> str:
    return f"{prefix}-{_stable_hash(value)[:24]}"


__all__ = [
    "WORKBOOK_BRIDGE_ACTOR",
    "WORKBOOK_BRIDGE_PURPOSE",
    "WORKBOOK_BRIDGE_VERSION",
    "WORKBOOK_SNAPSHOT_GATE_ACTOR",
    "WorkbookParserV2BridgeBlocked",
    "WorkbookParserV2BridgeError",
    "bridge_approved_workbook_to_parser_v2",
]
