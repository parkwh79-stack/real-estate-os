"""Tenant-aware durable PF review job API.

The router is built with project-domain callbacks so async execution does not
reach into the monolithic projects router through a circular import.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import APIRouter, HTTPException, Request

from core.authz import authorize_request
from use_cases.async_jobs import (
    cancel_pf_review_job,
    get_job_store,
    recover_interrupted_jobs,
    submit_pf_review_job,
)


ProjectLookup = Callable[[str], Dict[str, Any] | None]
ProjectSourceBuilder = Callable[[str, Dict[str, Any]], Dict[str, Any]]
RepositoryFactory = Callable[[], Any]


def build_project_jobs_router(
    *,
    project_lookup: ProjectLookup,
    source_builder: ProjectSourceBuilder,
    orchestration_repository_factory: RepositoryFactory,
) -> APIRouter:
    router = APIRouter(prefix="/projects", tags=["project-jobs"])

    def require_project(project_id: str) -> Dict[str, Any]:
        project = project_lookup(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @router.post("/{project_id}/orchestrations/pf-review/async")
    def start_pf_review_job(
        project_id: str,
        request: Request,
        input_data: Dict[str, Any] | None = None,
    ):
        require_project(project_id)
        identity = authorize_request(request, "project:analyze")
        input_data = input_data or {}
        source_data = source_builder(project_id, input_data)
        user = identity.get("user") or {}
        options = dict(input_data.get("options") or {})
        options.setdefault("tenantId", user.get("tenantId") or "default")
        options.setdefault("requestedBy", user.get("userId") or "unknown")
        options.setdefault("actorRoles", [user.get("role")] if user.get("role") else [])
        job = submit_pf_review_job(
            orchestration_repository_factory(),
            project_id,
            source_data,
            options,
            background=True,
        )
        return {"job": job}

    @router.get("/{project_id}/jobs")
    def list_jobs(project_id: str, request: Request, limit: int = 50):
        require_project(project_id)
        authorize_request(request, "project:read")
        return {"jobs": get_job_store().list(project_id=project_id, limit=limit)}

    @router.get("/{project_id}/jobs/{job_id}")
    def get_job(project_id: str, job_id: str, request: Request):
        require_project(project_id)
        authorize_request(request, "project:read")
        job = get_job_store().get(job_id)
        if job is None or job.get("projectId") != project_id:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @router.delete("/{project_id}/jobs/{job_id}")
    def cancel_job(project_id: str, job_id: str, request: Request):
        require_project(project_id)
        identity = authorize_request(request, "project:analyze")
        job = get_job_store().get(job_id)
        if job is None or job.get("projectId") != project_id:
            raise HTTPException(status_code=404, detail="Job not found")
        user = identity.get("user") or {}
        return {
            "job": cancel_pf_review_job(
                job_id,
                reason=f"Cancelled by {user.get('userId') or 'unknown'}",
            )
        }

    def resubmit(old_job: Dict[str, Any]) -> Dict[str, Any] | None:
        project_id = str(old_job.get("projectId") or "")
        if not project_id or project_lookup(project_id) is None:
            return None
        payload = (old_job.get("params") or {}).get("inputData") or {}
        source_data = source_builder(project_id, payload)
        return submit_pf_review_job(
            orchestration_repository_factory(),
            project_id,
            source_data,
            payload.get("options", {}),
            background=True,
            resume_payload=payload,
            recovered_from=old_job.get("jobId"),
        )

    @router.post("/{project_id}/jobs/recover")
    def recover_jobs(project_id: str, request: Request):
        require_project(project_id)
        authorize_request(request, "project:analyze")
        return recover_interrupted_jobs(resubmit, project_id=project_id)

    return router
