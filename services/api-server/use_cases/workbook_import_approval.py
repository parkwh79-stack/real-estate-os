from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from db.repositories.project_repository import ProjectRepository
from schemas.feasibility_study import FeasibilityStudyV2
from storage import upsert_risk_register_record


def approve_feasibility_workbook_import_use_case(
    project_repository: ProjectRepository,
    project_id: str,
    input_data: Dict[str, Any],
    *,
    approved_by: str,
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
    now = datetime.now(timezone.utc).isoformat()
    import_id = (
        input_data.get("importId")
        or input_data.get("import_id")
        or (canonical.get("raw_import") or {}).get("importId")
        or f"feasibility-import-{uuid4()}"
    )
    approval = {
        "importId": import_id,
        "schemaVersion": "realytics-feasibility-workbook-approval-v1",
        "approvedBy": approved_by,
        "approvedAt": now,
        "comment": input_data.get("comment") or input_data.get("reviewComment") or "",
        "sourceDocument": input_data.get("sourceDocument") or (canonical.get("raw_import") or {}).get("sourceDocument") or {},
        "validation": input_data.get("validation") or {},
    }
    canonical.setdefault("raw_import", {})
    canonical["raw_import"]["approval"] = approval
    canonical["raw_import"]["canonicalStatus"] = "approved"

    existing_project = project_repository.get(project_id) or {}
    history = list(((existing_project.get("_meta") or {}).get("approvedFeasibilityWorkbookImports") or []))
    history.append(approval)
    feasibility_input = _feasibility_input_from_study(canonical)

    update_payload = {
        "feasibilityStudyV2": canonical,
        "canonicalFeasibilityStudyV2": canonical,
        "feasibility_input": feasibility_input,
        "_meta": {
            "approvedFeasibilityWorkbookImports": history,
            "latestFeasibilityWorkbookImportId": import_id,
        },
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

    This is deliberately different from approval: the imported values are
    marked ``candidate`` and remain subject to reviewer/assumption approval,
    but Feasibility-Cashflow can now calculate from the real workbook instead
    of treating the project as empty.
    """
    draft = input_data.get("feasibilityStudyV2Draft") or input_data.get("draft")
    if not isinstance(draft, dict):
        raise ValueError("feasibilityStudyV2Draft is required")

    canonical = FeasibilityStudyV2.model_validate(draft).model_dump(mode="json")
    now = datetime.now(timezone.utc).isoformat()
    source_document = input_data.get("sourceDocument") or (canonical.get("raw_import") or {}).get("sourceDocument") or {}
    import_id = input_data.get("importId") or f"feasibility-candidate-{uuid4()}"
    canonical.setdefault("raw_import", {})
    canonical["raw_import"]["candidateImport"] = {
        "importId": import_id,
        "recordedBy": recorded_by,
        "recordedAt": now,
        "status": "candidate",
        "sourceDocument": source_document,
        "validation": input_data.get("validation") or {},
    }
    existing = project_repository.get(project_id) or {}
    history = list(((existing.get("_meta") or {}).get("feasibilityWorkbookCandidates") or []))
    history.append({
        "importId": import_id,
        "recordedBy": recorded_by,
        "recordedAt": now,
        "sourceDocument": source_document,
        "validationStatus": (input_data.get("validation") or {}).get("status"),
    })
    update_payload = {
        "feasibilityStudyV2Draft": canonical,
        "feasibilityStudyV2": canonical,
        "canonicalFeasibilityStudyV2": canonical,
        "product_mix": canonical.get("product_mix") or [],
        "schedule": canonical.get("schedule") or {},
        "pf_structure": canonical.get("financing") or [],
        "feasibility_input": {
            **(existing.get("feasibility_input") or {}),
            **_feasibility_input_from_study(canonical),
        },
        "_meta": {
            "feasibilityWorkbookCandidates": history,
            "latestFeasibilityWorkbookCandidateId": import_id,
            "feasibilityInputSource": "workbook_validation_candidate",
            "feasibilityInputApprovalStatus": "candidate",
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
        "feasibilityInput": update_payload["feasibility_input"],
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
        if item.get("priority") in {"senior", "middle", "mezzanine", "junior", "bridge", "other"}
    )
    if pf_amount:
        result["pf_amount"] = pf_amount
    return {key: value for key, value in result.items() if value is not None}
