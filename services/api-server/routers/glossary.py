from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.authz import authorize_request
from storage import record_user_activity_log, storage_backend
from db.repositories.glossary_repository import GlossaryAuthorityUnavailable, PostgresGlossaryRepository
from db.repositories.request_project_repository import current_project_scope
from use_cases.glossary import (
    canonical_field_map_use_case,
    capture_glossary_candidates_use_case,
    get_glossary_term_use_case,
    import_term_standard_seed_use_case,
    list_glossary_candidates_use_case,
    list_glossary_terms_use_case,
    normalize_glossary_surfaces_use_case,
    review_glossary_candidate_use_case,
    resolve_glossary_term_use_case,
    upsert_glossary_term_use_case,
)


def normalized_glossary_repository() -> PostgresGlossaryRepository | None:
    if storage_backend() not in {"postgres", "postgresql"}:
        return None
    scope = current_project_scope()
    if scope is None:
        raise GlossaryAuthorityUnavailable("request_scope")
    return PostgresGlossaryRepository(tenant_id=scope.tenant_id)


def glossary_authority(request: Request):
    if storage_backend() in {"postgres", "postgresql"} and request.method != "GET":
        authorize_request(request, "project:write")
        raise HTTPException(status_code=503, detail={
            "error": "glossary_write_authority_unavailable",
            "message": "운영 용어집의 편집·승인은 전용 검토 저장소 연결이 필요합니다.",
        })
    try:
        yield
    except GlossaryAuthorityUnavailable as exc:
        raise HTTPException(status_code=503, detail={
            "error": "glossary_read_authority_unavailable",
            "authority": exc.authority,
            "message": "운영 용어집의 저장소 연결과 조회 권한을 확인해 주세요.",
        }) from exc


router = APIRouter(prefix="/glossary", tags=["glossary"], dependencies=[Depends(glossary_authority)])


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
        repository=normalized_glossary_repository(),
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
    term = get_glossary_term_use_case(term_id, repository=normalized_glossary_repository())
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
    return resolve_glossary_term_use_case(term, include_pending=include_pending, repository=normalized_glossary_repository())


@router.post("/normalize")
def normalize_glossary_terms(request: Request, payload: Dict[str, Any]):
    context = authorize_request(request, "project:write")
    actor = (context.get("user") or {}).get("userId") or "anonymous"
    items = payload.get("items") or payload.get("terms") or []
    if payload.get("term") or payload.get("surface") or payload.get("rawTerm"):
        items = [payload]
    result = normalize_glossary_surfaces_use_case(
        items,
        project_id=payload.get("projectId") or payload.get("project_id"),
        run_id=payload.get("runId") or payload.get("run_id"),
        min_suggestion_score=float(payload.get("minSuggestionScore") or 0.72),
    )
    record_user_activity_log(
        {
            "eventType": "glossary_terms_normalized",
            "userId": actor,
            "projectId": result.get("projectId"),
            "runId": result.get("runId"),
            "method": request.method,
            "path": request.url.path,
            "metadata": {
                "inputCount": result.get("inputCount"),
                "matchedCount": result.get("matchedCount"),
                "candidateCount": result.get("candidateCount"),
            },
        }
    )
    return result


@router.get("/canonical-field-map")
def get_canonical_field_map(request: Request):
    authorize_request(request, "project:read")
    return canonical_field_map_use_case(repository=normalized_glossary_repository())


@router.post("/standard-pack/import")
def import_term_standard_pack(request: Request, payload: Dict[str, Any]):
    context = authorize_request(request, "project:write")
    actor = (context.get("user") or {}).get("userId") or "anonymous"
    seed_payload = payload.get("seed") or payload
    result = import_term_standard_seed_use_case(
        seed_payload,
        actor=actor,
        replace_existing=bool(payload.get("replaceExisting") or payload.get("replace_existing")),
    )
    record_user_activity_log(
        {
            "eventType": "glossary_standard_pack_imported",
            "userId": actor,
            "method": request.method,
            "path": request.url.path,
            "metadata": {
                "termCount": result.get("termCount"),
                "importedCount": result.get("importedCount"),
                "skippedCount": result.get("skippedCount"),
                "replaceExisting": result.get("replaceExisting"),
            },
        }
    )
    return result


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
    repository = normalized_glossary_repository()
    if repository is not None and project_id and current_project_scope().get(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return list_glossary_candidates_use_case(
        query=q,
        status=status,
        project_id=project_id,
        category=category,
        limit=limit,
        repository=repository,
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
