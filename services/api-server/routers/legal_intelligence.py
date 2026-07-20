"""Read-only HTTP surface for the Legal Intelligence Core."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from core.authz import authorize_request
from legal_intelligence.models import LegalResourceKind
from db.repositories.legal_query_run_repository import LegalQueryAuditContext
from use_cases.legal_intelligence import (
    get_legal_resource,
    search_legal_resources,
)


router = APIRouter(prefix="/legal", tags=["legal-intelligence"])


@router.get("/search")
def search_legal_intelligence(
    request: Request,
    q: str = Query(min_length=1, max_length=500),
    resource_type: LegalResourceKind | None = Query(default=None),
    as_of: date | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
    public_query_acknowledged: bool = Query(
        default=False,
        description=(
            "Acknowledge that q contains public-only information and may be "
            "sent to approved external legal providers."
        ),
    ),
    project_id: str | None = Query(
        default=None,
        alias="projectId",
        min_length=1,
        max_length=255,
        description=(
            "Project scope for append-only normalized query run/event audit."
        ),
    ),
):
    """Search verified local snapshots first, then approved official sources."""

    authorization = authorize_request(request, "project:read")
    audit_context = None
    if project_id is not None:
        user = authorization.get("user") or {}
        tenant_id = str(user.get("tenantId") or "").strip()
        actor_id = str(user.get("userId") or "").strip()
        if (
            not authorization.get("authenticated")
            or not tenant_id
            or not actor_id
            or actor_id.casefold() in {"anonymous", "unknown"}
        ):
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "authenticated_project_scope_required",
                    "message": (
                        "projectId legal searches require a named tenant "
                        "principal so the normalized query audit can be written."
                    ),
                },
            )
        idempotency_key = str(
            request.headers.get("x-idempotency-key")
            or f"legal-search:{uuid4()}"
        ).strip()
        try:
            audit_context = LegalQueryAuditContext(
                tenant_id=tenant_id,
                project_id=project_id,
                requested_by=actor_id,
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_legal_query_audit_context",
                    "message": str(exc),
                },
            ) from exc
    return search_legal_resources(
        q,
        resource_kind=resource_type,
        as_of=as_of,
        limit=limit,
        public_query_acknowledged=public_query_acknowledged,
        audit_context=audit_context,
    )


@router.get("/resources/{resource_id}")
def read_legal_resource(
    resource_id: str,
    request: Request,
    as_of: date | None = Query(default=None),
    provision_limit: int = Query(default=500, ge=1, le=2_000),
    provision_offset: int = Query(default=0, ge=0),
):
    """Read one effective-dated resource with exact provision locators."""

    authorize_request(request, "project:read")
    try:
        resource = get_legal_resource(
            resource_id,
            as_of=as_of,
            provision_limit=provision_limit,
            provision_offset=provision_offset,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "legal_corpus_unavailable",
                "message": (
                    "The verified legal corpus is not currently available. "
                    "No unverified fallback was returned."
                ),
            },
        ) from exc
    if resource is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "legal_resource_not_found",
                "resourceId": resource_id,
            },
        )
    return {
        "schemaVersion": "realytics-legal-intelligence-v1",
        "resource": resource,
        "disclaimer": (
            "이 자료는 공식 원문 확인을 돕는 검색 결과이며 최종 법률의견이 아닙니다."
        ),
    }
