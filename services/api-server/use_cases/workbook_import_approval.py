from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from db.repositories.project_repository import ProjectRepository
from schemas.feasibility_study import FeasibilityStudyV2
from storage import upsert_risk_register_record
from use_cases.parser_v2_records import (
    ParserV2RecordStore,
    approved_input_snapshot_wire_payload,
)
from use_cases.workbook_parser_v2_bridge import (
    bridge_approved_workbook_to_parser_v2,
)


def approve_feasibility_workbook_import_use_case(
    project_repository: ProjectRepository,
    project_id: str,
    input_data: Dict[str, Any],
    *,
    approved_by: str,
    known_source_documents: List[Dict[str, Any]],
    parser_v2_record_store: ParserV2RecordStore,
    tenant_id: str,
) -> Dict[str, Any]:
    """Persist reviewer-approved FeasibilityStudyV2 as canonical project data."""
    draft = (
        input_data.get("feasibilityStudyV2Draft")
        or input_data.get("feasibility_study_v2_draft")
        or input_data.get("draft")
        or input_data.get("feasibilityStudyV2")
    )
    if not isinstance(draft, dict):
        raise ValueError("feasibilityStudyV2Draft is required")

    canonical = FeasibilityStudyV2.model_validate(draft).model_dump(mode="json")
    _bind_draft_to_project(canonical, project_id)
    validation, verified_source_document = _approval_validation(
        input_data,
        canonical,
        known_source_documents,
    )
    existing_project = project_repository.get(project_id) or {}
    now = datetime.now(timezone.utc).isoformat()
    import_id = (
        input_data.get("importId")
        or input_data.get("import_id")
        or (canonical.get("raw_import") or {}).get("importId")
        or f"feasibility-import-{uuid4()}"
    )
    candidate_context = _require_prior_workbook_candidate(
        existing_project=existing_project,
        canonical=canonical,
        verified_source_document=verified_source_document,
        approved_by=approved_by,
        requested_candidate_id=(
            input_data.get("candidateImportId")
            or input_data.get("candidate_import_id")
            or import_id
        ),
    )
    approval = {
        "importId": import_id,
        "candidateImportId": candidate_context["importId"],
        "schemaVersion": "realytics-feasibility-workbook-approval-v1",
        "preparedBy": candidate_context["recordedBy"],
        "approvedBy": approved_by,
        "approvedAt": now,
        "comment": input_data.get("comment") or input_data.get("reviewComment") or "",
        "sourceDocument": verified_source_document,
        "validation": validation,
    }
    canonical.setdefault("raw_import", {})
    canonical["raw_import"]["candidateImport"] = {
        "importId": candidate_context["importId"],
        "recordedBy": candidate_context["recordedBy"],
        "recordedAt": candidate_context.get("recordedAt"),
        "status": "reviewed",
        "draftHash": candidate_context["draftHash"],
        "sourceDocumentHashes": list(
            candidate_context.get("sourceDocumentHashes") or []
        ),
    }
    canonical["raw_import"]["approval"] = approval
    canonical["raw_import"]["canonicalStatus"] = "approved"

    parser_v2_bridge = bridge_approved_workbook_to_parser_v2(
        record_store=parser_v2_record_store,
        tenant_id=tenant_id,
        project_id=project_id,
        import_id=import_id,
        canonical_study=canonical,
        source_document=verified_source_document,
        approved_by=approved_by,
    )
    snapshot = parser_v2_bridge.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError(
            "approved workbook did not produce a governed Parser V2 input snapshot"
        )
    snapshot_id = str(snapshot.get("snapshotId") or "").strip()
    snapshot_hash = str(snapshot.get("immutablePayloadHash") or "").strip()
    if not snapshot_id or len(snapshot_hash) != 64:
        raise ValueError("Parser V2 workbook snapshot identity is incomplete")
    canonical["input_references"] = {
        **dict(canonical.get("input_references") or {}),
        "approved_input_snapshot_id": snapshot_id,
        "approved_input_snapshot_hash": snapshot_hash,
    }
    canonical["raw_import"]["parserV2Bridge"] = {
        "schemaVersion": parser_v2_bridge.get("schemaVersion"),
        "status": parser_v2_bridge.get("status"),
        "purpose": parser_v2_bridge.get("purpose"),
        "parsingRunId": parser_v2_bridge.get("parsingRunId"),
        "approvedInputSnapshotId": snapshot_id,
        "approvedInputSnapshotHash": snapshot_hash,
        "approvedFields": list(parser_v2_bridge.get("approvedFields") or []),
        "missingFields": list(parser_v2_bridge.get("missingFields") or []),
    }
    approval["approvedInputSnapshotId"] = snapshot_id
    approval["approvedInputSnapshotHash"] = snapshot_hash
    canonical = FeasibilityStudyV2.model_validate(canonical).model_dump(mode="json")

    history = list(((existing_project.get("_meta") or {}).get("approvedFeasibilityWorkbookImports") or []))
    history.append(approval)
    feasibility_input = _feasibility_input_from_study(canonical)

    meta_update = {
        "approvedFeasibilityWorkbookImports": history,
        "latestFeasibilityWorkbookImportId": import_id,
    }
    latest_candidate_id = (existing_project.get("_meta") or {}).get("latestFeasibilityWorkbookCandidateId")
    if latest_candidate_id:
        meta_update.update({
            "feasibilityWorkbookCandidateStatus": "superseded_by_approval",
            "supersededFeasibilityWorkbookCandidateId": latest_candidate_id,
        })
    update_payload = {
        "feasibilityStudyV2": canonical,
        "canonicalFeasibilityStudyV2": canonical,
        "feasibility_input": feasibility_input,
        "_meta": meta_update,
    }
    updated = project_repository.update(project_id, update_payload)
    if not updated:
        raise LookupError("Project not found")

    return {
        "schemaVersion": "realytics-feasibility-workbook-approval-result-v1",
        "projectId": project_id,
        "importId": import_id,
        "approval": approval,
        "canonicalFeasibilityStudyV2": canonical,
        "feasibilityInput": feasibility_input,
        "parserV2Bridge": parser_v2_bridge,
        "approvedInputSnapshot": approved_input_snapshot_wire_payload(snapshot),
        "project": updated,
    }


def persist_feasibility_workbook_candidate_use_case(
    project_repository: ProjectRepository,
    project_id: str,
    input_data: Dict[str, Any],
    *,
    recorded_by: str,
) -> Dict[str, Any]:
    """Persist a validated workbook draft for deterministic Pod consumption.

    This is deliberately different from approval: imported values are stored
    only as a reviewable draft.  They must not populate canonical project
    inputs or feed report-grade calculations before explicit approval.
    """
    draft = input_data.get("feasibilityStudyV2Draft") or input_data.get("draft")
    if not isinstance(draft, dict):
        raise ValueError("feasibilityStudyV2Draft is required")

    canonical = FeasibilityStudyV2.model_validate(draft).model_dump(mode="json")
    _bind_draft_to_project(canonical, project_id)
    now = datetime.now(timezone.utc).isoformat()
    source_document = input_data.get("sourceDocument") or (canonical.get("raw_import") or {}).get("sourceDocument") or {}
    import_id = input_data.get("importId") or f"feasibility-candidate-{uuid4()}"
    draft_hash = feasibility_study_draft_hash(canonical)
    source_ids = sorted(_source_identifiers(source_document))
    canonical.setdefault("raw_import", {})
    canonical["raw_import"]["candidateImport"] = {
        "importId": import_id,
        "recordedBy": recorded_by,
        "recordedAt": now,
        "status": "candidate",
        "draftHash": draft_hash,
        "sourceDocumentHashes": source_ids,
        "sourceDocument": source_document,
        "validation": input_data.get("validation") or {},
    }
    existing = project_repository.get(project_id) or {}
    history = list(((existing.get("_meta") or {}).get("feasibilityWorkbookCandidates") or []))
    history.append({
        "importId": import_id,
        "recordedBy": recorded_by,
        "recordedAt": now,
        "status": "candidate",
        "draftHash": draft_hash,
        "sourceDocumentHashes": source_ids,
        "sourceDocument": source_document,
        "validationStatus": (input_data.get("validation") or {}).get("status"),
    })
    preview = _feasibility_input_from_study(canonical)
    update_payload = {
        "feasibilityStudyV2Draft": canonical,
        "_meta": {
            "feasibilityWorkbookCandidates": history,
            "latestFeasibilityWorkbookCandidateId": import_id,
            "feasibilityWorkbookCandidateStatus": "candidate",
            "upload": {
                "validationStatus": "candidate",
                "parserVersion": "feasibility-workbook-importer-v2",
            },
        },
    }
    updated = project_repository.update(project_id, update_payload)
    if not updated:
        raise LookupError("Project not found")
    return {
        "schemaVersion": "realytics-feasibility-workbook-candidate-v1",
        "projectId": project_id,
        "importId": import_id,
        "status": "candidate",
        "authoritative": False,
        "feasibilityInputPreview": preview,
        "project": updated,
    }


def approve_risk_report_workbook_import_use_case(
    project_repository: ProjectRepository,
    project_id: str,
    input_data: Dict[str, Any],
    *,
    approved_by: str,
) -> Dict[str, Any]:
    """Persist reviewer-approved risk-report workbook import into Risk Register/report sections."""
    draft = (
        input_data.get("riskReportImportDraft")
        or input_data.get("risk_report_import_draft")
        or input_data.get("draft")
    )
    if not isinstance(draft, dict):
        raise ValueError("riskReportImportDraft is required")
    validation = draft.get("validation") or {}
    if validation.get("importReady") is False:
        raise ValueError("riskReportImportDraft is not import-ready")

    now = datetime.now(timezone.utc).isoformat()
    import_id = draft.get("importId") or f"risk-report-import-{uuid4()}"
    approval = {
        "importId": import_id,
        "schemaVersion": "realytics-risk-report-workbook-approval-v1",
        "approvedBy": approved_by,
        "approvedAt": now,
        "comment": input_data.get("comment") or input_data.get("reviewComment") or "",
        "sourceDocument": draft.get("sourceDocument") or {},
        "validation": validation,
    }

    risk_register = dict(draft.get("riskRegisterDraft") or {})
    risk_register["projectId"] = project_id
    risk_register["runId"] = import_id
    risk_register["registerId"] = risk_register.get("registerId") or f"{import_id}:risk-register"
    risk_register["importApproval"] = approval
    persisted_risk_register = upsert_risk_register_record(project_id, import_id, risk_register)

    report_sections = {
        **(draft.get("reportSectionsDraft") or {}),
        "importApproval": approval,
        "metrics": draft.get("metrics") or {},
        "cashFlowSummary": draft.get("cashFlowSummary") or [],
        "sourceMap": draft.get("sourceMap") or [],
    }
    existing_project = project_repository.get(project_id) or {}
    import_history = list(existing_project.get("riskReportWorkbookImports") or [])
    import_history.append(approval)
    update_payload = {
        "riskReportSections": report_sections,
        "riskReportWorkbookImports": import_history,
        "_meta": {
            "latestRiskReportWorkbookImportId": import_id,
            "latestRiskReportWorkbookApprovedAt": now,
        },
    }
    updated = project_repository.update(project_id, update_payload)
    if not updated:
        raise LookupError("Project not found")

    return {
        "schemaVersion": "realytics-risk-report-workbook-approval-result-v1",
        "projectId": project_id,
        "importId": import_id,
        "approval": approval,
        "riskRegister": persisted_risk_register,
        "reportSections": report_sections,
        "project": updated,
    }


def _feasibility_input_from_study(study: Dict[str, Any]) -> Dict[str, Any]:
    metrics = study.get("metrics") or {}
    result = {
        "schema_source": study.get("version"),
        "total_revenue": metrics.get("total_revenue"),
        "total_cost": metrics.get("total_cost"),
        "development_profit": metrics.get("development_profit"),
        "profit_margin": metrics.get("profit_margin"),
        "max_minus": metrics.get("max_minus"),
        "ltv": metrics.get("ltv"),
        "ltc": metrics.get("ltc"),
        "exit_sale_rate": metrics.get("exit_sale_rate"),
    }
    financing = study.get("financing") or []
    pf_amount = sum(
        float(item.get("amount") or 0)
        for item in financing
        if item.get("priority") in {"senior", "mezzanine", "junior", "other"}
    )
    if pf_amount:
        result["pf_amount"] = pf_amount
    return {key: value for key, value in result.items() if value is not None}


def _require_prior_workbook_candidate(
    *,
    existing_project: Dict[str, Any],
    canonical: Dict[str, Any],
    verified_source_document: Dict[str, Any],
    approved_by: str,
    requested_candidate_id: Any,
) -> Dict[str, Any]:
    """Require an immutable maker record before a reviewer may approve.

    The UI already persists the validated workbook draft through the candidate
    endpoint. Making that step mandatory gives the approval path a real
    human maker/checker boundary instead of treating an automated snapshot
    service account as a second human approver.
    """

    metadata = existing_project.get("_meta")
    metadata = metadata if isinstance(metadata, dict) else {}
    history = metadata.get("feasibilityWorkbookCandidates")
    history = history if isinstance(history, list) else []
    expected_hash = feasibility_study_draft_hash(canonical)
    expected_source_ids = _source_identifiers(verified_source_document)
    requested_id = str(requested_candidate_id or "").strip()

    matching = [
        dict(item)
        for item in history
        if isinstance(item, dict)
        and str(item.get("status") or "candidate").casefold() == "candidate"
        and str(item.get("draftHash") or "") == expected_hash
        and (
            not requested_id
            or str(item.get("importId") or "") == requested_id
        )
        and bool(
            expected_source_ids.intersection(
                {
                    str(value).strip()
                    for value in item.get("sourceDocumentHashes") or []
                    if str(value or "").strip()
                }
                or _source_identifiers(item.get("sourceDocument"))
            )
        )
    ]
    if not matching:
        raise ValueError(
            "workbook approval requires a persisted candidate with the same "
            "draft hash and archived source"
        )
    candidate = matching[-1]
    prepared_by = str(candidate.get("recordedBy") or "").strip()
    reviewer = str(approved_by or "").strip()
    if not prepared_by:
        raise ValueError("persisted workbook candidate is missing recordedBy")
    if prepared_by == reviewer:
        raise ValueError(
            "maker-checker separation requires another reviewer to approve "
            "the persisted workbook candidate"
        )
    return candidate


def _approval_validation(
    input_data: Dict[str, Any],
    canonical: Dict[str, Any],
    known_source_documents: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Require a passing, conflict-free workbook validation before promotion.

    A caller cannot mask an importer warning by sending ``status=passed``:
    both the submitted validation and the status embedded in ``raw_import``
    are checked.  Manual feasibility assumptions use the separate assumption
    approval flow and therefore do not need to bypass this workbook boundary.
    """
    raw_import = canonical.get("raw_import") or {}
    requested_source = input_data.get("sourceDocument")
    raw_source = raw_import.get("sourceDocument")
    source_document = _source_with_identifier(requested_source, raw_source)
    source_ids = _source_identifiers(source_document)
    if not source_ids:
        raise ValueError("archived source document hash is required before approval")
    persisted_source = next(
        (
            document
            for document in known_source_documents
            if isinstance(document, dict) and source_ids.intersection(_source_identifiers(document))
        ),
        None,
    )
    if persisted_source is None:
        raise ValueError("approval source document is not archived for this project")
    if str(persisted_source.get("documentType") or "").strip() != "feasibility_workbook":
        raise ValueError("approval source document is not a feasibility workbook")
    persisted_draft_hash = str(persisted_source.get("feasibilityStudyV2DraftHash") or "").strip()
    if not persisted_draft_hash:
        raise ValueError("server-issued workbook draft hash is required before approval")
    if persisted_draft_hash != feasibility_study_draft_hash(canonical):
        raise ValueError("workbook draft differs from the server-extracted source draft")

    validation = persisted_source.get("validation")
    if not isinstance(validation, dict):
        raise ValueError("server-issued workbook validation is required before approval")
    submitted_validation = input_data.get("validation")
    if isinstance(submitted_validation, dict):
        submitted_hash = str(submitted_validation.get("sourceDocumentHash") or "").strip()
        if submitted_hash and submitted_hash not in _source_identifiers(persisted_source):
            raise ValueError("submitted validation does not match the archived source document")
    raw_status = str(raw_import.get("validationStatus") or "").strip().lower()
    persisted_status = str(validation.get("status") or "").strip().lower()
    statuses = [status for status in (persisted_status, raw_status) if status]
    if not statuses:
        raise ValueError("passing workbook validation is required before approval")
    if any(status not in {"passed", "valid"} for status in statuses):
        raise ValueError(f"workbook validation is not approval-ready: {', '.join(statuses)}")

    if "formulaIntegrity" not in validation:
        raise ValueError("formula integrity validation is required before approval")
    formula_integrity = validation.get("formulaIntegrity") or {}
    if formula_integrity.get("hasBlockingFormulaErrors") is not False:
        raise ValueError("formula integrity must explicitly pass before approval")
    if formula_integrity.get("hasExternalWorkbookReferences") is True:
        raise ValueError("workbook contains unresolved external workbook references")

    for required_contract in ("reconciliationChecks", "requiredSheetChecks", "requiredKeyCellChecks"):
        if required_contract not in validation:
            raise ValueError(f"{required_contract} validation is required before approval")
    reconciliation_checks = validation.get("reconciliationChecks") or []
    required_sheet_checks = validation.get("requiredSheetChecks") or []
    required_key_cell_checks = validation.get("requiredKeyCellChecks") or []
    profile = str(validation.get("profile") or "")
    if not required_sheet_checks:
        raise ValueError("workbook validation did not execute required sheet checks")
    if profile != "realytics-formula-cash-flow-export" and (
        not reconciliation_checks or not required_key_cell_checks
    ):
        raise ValueError("legacy workbook validation did not execute required numeric and reconciliation checks")
    if any(
        isinstance(check, dict)
        and (check.get("passed") is False or check.get("severity") == "blocking")
        for check in reconciliation_checks
    ):
        raise ValueError("workbook reconciliation has blocking failures")

    if any(isinstance(check, dict) and check.get("present") is False for check in required_sheet_checks):
        raise ValueError("workbook is missing required sheets")
    if any(isinstance(check, dict) and check.get("passed") is False for check in required_key_cell_checks):
        raise ValueError("workbook has blank required key cells")

    validated_hash = str(validation.get("sourceDocumentHash") or "").strip()
    persisted_ids = _source_identifiers(persisted_source)
    if not validated_hash:
        raise ValueError("validated source document hash is required before approval")
    if validated_hash not in persisted_ids:
        raise ValueError("workbook validation hash does not match the approval source document")

    conflicts = validation.get("sourceConflicts") or raw_import.get("sourceConflicts") or []
    unresolved = [
        conflict for conflict in conflicts
        if isinstance(conflict, dict)
        and str(conflict.get("status") or "open").lower() not in {"resolved", "approved", "dismissed"}
    ]
    if unresolved:
        raise ValueError("workbook has unresolved source conflicts")
    return validation, dict(persisted_source)


def _bind_draft_to_project(canonical: Dict[str, Any], project_id: str) -> None:
    draft_project_id = str(canonical.get("project_id") or "").strip()
    if draft_project_id and draft_project_id != str(project_id):
        raise ValueError("workbook draft project_id does not match the route project_id")
    canonical["project_id"] = str(project_id)


def _source_identifiers(source_document: Any) -> set[str]:
    if not isinstance(source_document, dict):
        return set()
    return {
        str(value).strip()
        for value in (
            source_document.get("archiveId"),
            source_document.get("originalFileHash"),
            source_document.get("sha256"),
        )
        if str(value or "").strip()
    }


def _source_with_identifier(*candidates: Any) -> Dict[str, Any]:
    for candidate in candidates:
        if isinstance(candidate, dict) and _source_identifiers(candidate):
            return candidate
    return {}


def feasibility_study_draft_hash(draft: Dict[str, Any]) -> str:
    """Return a stable digest for the server-extracted, pre-approval draft."""
    canonical = FeasibilityStudyV2.model_validate(draft).model_dump(mode="json")
    raw_import = dict(canonical.get("raw_import") or {})
    for mutable_key in ("approval", "canonicalStatus", "candidateImport"):
        raw_import.pop(mutable_key, None)
    canonical["raw_import"] = raw_import
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
