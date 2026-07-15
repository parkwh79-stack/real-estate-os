from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from core.authz import authorize_request, identity_context_from_request
from routers import projects
from schemas.assumption_proposal import AssumptionProposalApprovalRequest
from storage import (
    list_assumption_proposal_records,
    list_project_version_records,
    upsert_assumption_proposal_record,
    upsert_project_version_record,
)
from use_cases.assumption_proposals import approve_assumption_proposals


router = APIRouter(prefix="/projects", tags=["project assumptions"])


@router.get("/{project_id}/orchestrations/{run_id}/assumption-proposals")
def list_assumption_proposals(project_id: str, run_id: str, request: Request) -> Dict[str, Any]:
    authorize_request(request, "project:read")
    run = projects.orchestration_repository().get(project_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    artifact = run.get("artifacts", {}).get("assumptionProposals") or {}
    records = list_assumption_proposal_records(project_id=project_id, run_id=run_id)
    if not records:
        records = artifact.get("proposals") or []
    return {
        "schemaVersion": "realytics-assumption-proposal-list-v1",
        "projectId": project_id,
        "runId": run_id,
        "status": artifact.get("status") or "not_required",
        "records": records,
    }


@router.get("/{project_id}/versions")
def list_project_versions(project_id: str, request: Request) -> Dict[str, Any]:
    authorize_request(request, "project:read")
    if not projects.project_repository().get(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "schemaVersion": "realytics-project-version-list-v1",
        "projectId": project_id,
        "records": list_project_version_records(project_id),
    }


@router.post("/{project_id}/orchestrations/{run_id}/assumption-proposals/approve")
def approve_run_assumption_proposals(
    project_id: str,
    run_id: str,
    request: Request,
    input_data: AssumptionProposalApprovalRequest,
) -> Dict[str, Any]:
    authz = authorize_request(request, "assumption:override")
    project = projects.project_repository().get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    repository = projects.orchestration_repository()
    run = repository.get(project_id, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Orchestration run not found")
    artifact = run.get("artifacts", {}).get("assumptionProposals") or {}
    proposals = artifact.get("proposals") or []
    actor = str(identity_context_from_request(request).get("userId") or "reviewer")
    try:
        result = approve_assumption_proposals(
            project,
            proposals,
            input_data.proposal_ids,
            approved_by=actor,
            comment=input_data.comment,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    updated = projects.project_repository().update(project_id, result["project"])
    if not updated:
        raise HTTPException(status_code=404, detail="Project not found")
    artifact["proposals"] = result["proposals"]
    artifact["status"] = "approved"
    artifact["approvedProjectVersion"] = result["projectVersion"]
    run["approvalStatus"] = "recalculation_required"
    run.setdefault("artifacts", {})["assumptionProposals"] = artifact
    repository.save()
    for proposal in result["proposals"]:
        upsert_assumption_proposal_record(proposal)
    version_record = {
        **result["projectVersion"],
        "versionId": f"{project_id}:v{result['projectVersion']['version']}",
        "projectId": project_id,
        "tenantId": (result["project"].get("_meta") or {}).get("tenantId")
        or (authz.get("user") or {}).get("tenantId"),
        "projectState": result["project"],
    }
    upsert_project_version_record(version_record)
    projects.record_sensitive_project_event(
        request,
        event_type="assumption_proposals_approved",
        project_id=project_id,
        run_id=run_id,
        metadata={
            "proposalIds": input_data.proposal_ids,
            "projectVersion": result["projectVersion"]["version"],
            "authorization": authz.get("authorization"),
        },
    )
    return {
        "schemaVersion": "realytics-assumption-proposal-approval-v1",
        "projectId": project_id,
        "runId": run_id,
        "projectVersion": version_record,
        "records": result["proposals"],
        "requiresRecalculation": True,
    }
