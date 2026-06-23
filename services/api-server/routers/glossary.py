from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request

from core.authz import authorize_request
from storage import record_user_activity_log
from use_cases.glossary import (
    canonical_field_map_use_case,
    capture_glossary_candidates_use_case,
    get_glossary_term_use_case,
    list_glossary_candidates_use_case,
    list_glossary_terms_use_case,
    review_glossary_candidate_use_case,
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


@router.get("/candidates")
def list_glossary_candidates(
    request: Request,
    q: str | None = Query(None),
    status: str | None = Query(None),
    project_id: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    authorize_request(request, "project:read")
    return list_glossary_candidates_use_case(
        query=q,
        status=status,
        project_id=project_id,
        category=category,
        limit=limit,
    )


@router.post("/candidates/capture")
def capture_glossary_candidates(request: Request, payload: Dict[str, Any]):
    context = authorize_request(request, "project:write")
    actor = (context.get("user") or {}).get("userId") or "anonymous"
    result = capture_glossary_candidates_use_case(
        payload.get("annotations") or [],
        project_id=payload.get("projectId") or payload.get("project_id"),
        run_id=payload.get("runId") or payload.get("run_id"),
    )
    record_user_activity_log(
        {
            "eventType": "glossary_candidate_captured",
            "userId": actor,
            "projectId": result.get("projectId"),
            "runId": result.get("runId"),
            "method": request.method,
            "path": request.url.path,
            "metadata": {"capturedCount": result.get("capturedCount")},
        }
    )
    return result


@router.post("/candidates/{candidate_id}/review")
def review_glossary_candidate(request: Request, candidate_id: str, payload: Dict[str, Any]):
    context = authorize_request(request, "project:write")
    actor = (context.get("user") or {}).get("userId") or "anonymous"
    try:
        result = review_glossary_candidate_use_case(candidate_id, payload, actor=actor)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_user_activity_log(
        {
            "eventType": "glossary_candidate_reviewed",
            "userId": actor,
            "projectId": (result.get("candidate") or {}).get("projectId"),
            "runId": (result.get("candidate") or {}).get("runId"),
            "method": request.method,
            "path": request.url.path,
            "metadata": {
                "candidateId": candidate_id,
                "status": (result.get("candidate") or {}).get("status"),
                "termId": (result.get("term") or {}).get("termId"),
                "canonicalField": (result.get("term") or {}).get("canonicalField")
                    or (result.get("candidate") or {}).get("approvedCanonicalField"),
            },
        }
    )
    return result
