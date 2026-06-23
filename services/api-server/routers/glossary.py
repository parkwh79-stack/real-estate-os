from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from core.authz import authorize_request
from storage import record_user_activity_log
from use_cases.glossary import (
    canonical_field_map_use_case,
    get_glossary_term_use_case,
    list_glossary_terms_use_case,
    resolve_glossary_term_use_case,
    upsert_glossary_term_use_case,
)


router = APIRouter(prefix="/glossary", tags=["glossary"])


@router.get("/terms")
def list_glossary_terms(
    request: Request,
    q: str | None = Query(None),
    category: str | None = Query(None),
    canonical_field: str | None = Query(None),
    review_status: str | None = Query(None),
):
    authorize_request(request, "project:read")
    return list_glossary_terms_use_case(
        query=q,
        category=category,
        canonical_field=canonical_field,
        review_status=review_status,
    )


@router.post("/terms")
def upsert_glossary_term(request: Request, payload: Dict[str, Any]):
    context = authorize_request(request, "project:write")
    actor = (context.get("user") or {}).get("userId") or "anonymous"
    try:
        term = upsert_glossary_term_use_case(payload, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_user_activity_log(
        {
            "eventType": "glossary_term_upserted",
            "userId": actor,
            "method": request.method,
            "path": request.url.path,
            "metadata": {
                "termId": term.get("termId"),
                "canonicalField": term.get("canonicalField"),
                "reviewStatus": term.get("reviewStatus"),
            },
        }
    )
    return {"schemaVersion": "realytics-glossary-term-upsert-v1", "term": term}


@router.get("/terms/{term_id}")
def get_glossary_term(request: Request, term_id: str):
    authorize_request(request, "project:read")
    term = get_glossary_term_use_case(term_id)
    if not term:
        raise HTTPException(status_code=404, detail="glossary term not found")
    return {"schemaVersion": "realytics-glossary-term-get-v1", "term": term}


@router.get("/resolve")
def resolve_glossary_term(
    request: Request,
    term: str = Query(..., min_length=1),
    include_pending: bool = Query(False),
):
    authorize_request(request, "project:read")
    return resolve_glossary_term_use_case(term, include_pending=include_pending)


@router.get("/canonical-field-map")
def get_canonical_field_map(request: Request):
    authorize_request(request, "project:read")
    return canonical_field_map_use_case()
