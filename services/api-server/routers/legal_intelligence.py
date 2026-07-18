"""Read-only HTTP surface for the Legal Intelligence Core."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request

from core.authz import authorize_request
from legal_intelligence.models import LegalResourceKind
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
):
    """Search verified local snapshots first, then approved official sources."""

    authorize_request(request, "project:read")
    return search_legal_resources(
        q,
        resource_kind=resource_type,
        as_of=as_of,
        limit=limit,
        public_query_acknowledged=public_query_acknowledged,
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
