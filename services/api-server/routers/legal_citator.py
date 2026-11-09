"""Human review API for project-relevant Citator candidates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.authz import (
    authorize_request,
    enforce_project_tenant_scope,
    is_explicit_human_principal,
)
from db.legal_review_database import legal_review_database_configured
from db.postgres import postgres_configured
from db.repositories.legal_citator_repository import (
    PostgresLegalCitatorRepository,
)
from legal_intelligence.citator_models import (
    CitatorRecordKind,
    CitatorReviewInput,
)
from storage import get_project_record
from use_cases.legal_citator_review import LegalCitatorReviewService


router = APIRouter(
    prefix="/projects/{project_id}/legal/citator",
    tags=["legal-citator"],
)


@router.get("/review-queue")
def list_citator_review_queue(
    project_id: str,
    request: Request,
    record_kind: CitatorRecordKind | None = Query(
        default=None,
        alias="recordKind",
    ),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    service, _reviewer, _role = _service(
        project_id,
        request,
        require_human=False,
    )
    return service.list_queue(record_kind=record_kind, limit=limit)


@router.post("/citations/{citation_id}/reviews", status_code=201)
def review_citator_citation(
    project_id: str,
    citation_id: str,
    request: Request,
    payload: CitatorReviewInput,
) -> dict[str, Any]:
    service, reviewer, role = _service(
        project_id,
        request,
        require_human=True,
    )
    return _review(
        lambda: service.review_citation(
            citation_id,
            payload,
            reviewer_id=reviewer,
            reviewer_role=role,
        )
    )


@router.post("/relations/{assertion_id}/reviews", status_code=201)
def review_citator_relation(
    project_id: str,
    assertion_id: str,
    request: Request,
    payload: CitatorReviewInput,
) -> dict[str, Any]:
    service, reviewer, role = _service(
        project_id,
        request,
        require_human=True,
    )
    return _review(
        lambda: service.review_relation(
            assertion_id,
            payload,
            reviewer_id=reviewer,
            reviewer_role=role,
        )
    )


def _service(
    project_id: str,
    request: Request,
    *,
    require_human: bool = True,
) -> tuple[LegalCitatorReviewService, str, str]:
    project = get_project_record(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    authorization = authorize_request(request, "legal:review")
    if not authorization.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required"},
        )
    enforce_project_tenant_scope(request, project)
    user = authorization.get("user") or {}
    metadata = (
        project.get("_meta")
        if isinstance(project.get("_meta"), dict)
        else {}
    )
    tenant_id = str(
        user.get("tenantId")
        or metadata.get("tenantId")
        or metadata.get("tenant_id")
        or ""
    ).strip()
    reviewer_id = str(user.get("userId") or "").strip()
    reviewer_role = str(
        authorization.get("role")
        or user.get("role")
        or ""
    ).strip().casefold()
    if not tenant_id or not reviewer_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "legal_reviewer_identity_required"},
        )
    if require_human and (
        not is_explicit_human_principal(authorization)
        or reviewer_id.casefold() in {"anonymous", "unknown", "system"}
    ):
        raise HTTPException(
            status_code=403,
            detail={"error": "human_legal_reviewer_required"},
        )
    if not postgres_configured() or not legal_review_database_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "normalized_legal_store_required",
                "message": (
                    "Citator review requires both normalized runtime PostgreSQL "
                    "and the dedicated tenant-overlay legal-review DSN; no "
                    "global Corpus mutation, runtime, legal-sync, JSON, or "
                    "machine-finalization fallback is permitted."
                ),
            },
        )
    repository = PostgresLegalCitatorRepository(
        tenant_id=tenant_id,
        project_id=project_id,
    )
    return LegalCitatorReviewService(repository), reviewer_id, reviewer_role


def _review(writer: Any) -> dict[str, Any]:
    try:
        return writer()
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "legal_counsel_approver_required"},
        ) from exc
    except LookupError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "citator_candidate_not_found"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "citator_review_contract_rejected",
                "message": str(exc),
            },
        ) from exc


__all__ = ["router"]
