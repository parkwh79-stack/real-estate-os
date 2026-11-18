import os
import re
import time
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path
from uuid import uuid4


def _load_local_env() -> None:
    """Load services/api-server/.env into os.environ (dependency-free).

    Dev convenience so local secrets (e.g. VWORLD_API_KEY) are picked up without
    exporting them by hand. Existing environment variables always win, and the
    file is gitignored. No-op if absent.
    """
    if os.environ.get("REALYTICS_LOAD_LOCAL_ENV", "true").strip().lower() in {"false", "0", "off"}:
        return
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_env()

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from routers import agent_platform, audit, auth, glossary, kakao, google, legal_citator, legal_corpus_operations, legal_governance, legal_intelligence, legal_provider_operations, valuemap, projects, project_assumptions, ops, market
from routers import cash_flow_comparison
from core.authz import enforce_project_tenant_scope, identity_context_from_request
from core.idp import verified_principal_from_request_state
from core.runtime_profile import (
    cors_origins,
    is_hardened_environment,
    raw_identity_headers_enabled,
    server_issued_sessions_enabled,
    validate_security_configuration,
)
from core.runtime_health import liveness_payload, readiness_payload
from db.postgres_runtime_pool import (
    close_runtime_pools,
    open_runtime_pools,
)
from db.repositories.upload_archive_repository import UploadRejectedError
from db.repositories.activity_log_repository import PostgresActivityLogRepository
from db.repositories.request_project_repository import project_request_scope, current_project_scope
from db.repositories.postgres_project_repository import ProjectVersionConflict
from core.financial_input_validation import FinancialInputError
from db.repositories.report_repository import ReportPublicationConflict
from storage import get_user_session, record_user_activity_log, storage_backend

validate_security_configuration()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Own bounded PostgreSQL pools for exactly one API process lifespan."""

    registry = open_runtime_pools()
    application.state.postgres_runtime_pools = registry
    try:
        yield
    finally:
        try:
            close_runtime_pools()
        finally:
            application.state.postgres_runtime_pools = None


app = FastAPI(
    title="REALYTICS x PLATFORM9 API",
    description="Backend API server for Real Estate OS alpha workflows",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(FinancialInputError)
async def financial_input_error_handler(_request: Request, exc: FinancialInputError):
    return JSONResponse(
        status_code=422,
        content={"detail": {"error": "invalid_financial_input", "message": str(exc)}},
        headers={"Cache-Control": "no-store"},
    )


@app.exception_handler(ReportPublicationConflict)
async def report_integrity_error_handler(_request: Request, exc: ReportPublicationConflict):
    return JSONResponse(status_code=409, content={"detail": {"error": "report_integrity_conflict", "message": str(exc)}},
        headers={"Cache-Control": "no-store"})


@app.exception_handler(UploadRejectedError)
async def upload_rejected_handler(
    _request: Request,
    exc: UploadRejectedError,
):
    status_code = {
        "malware_detected": 422,
        "malware_scan_failed": 503,
        "malware_scan_required": 503,
        "quarantine_hash_mismatch": 409,
        "active_archive_hash_mismatch": 409,
    }.get(exc.code, 422)
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": {
                "error": exc.code,
                "archiveId": exc.archive_id,
                "malwareScanStatus": exc.scan_status,
                "storageLifecycleStatus": "quarantine",
                "activeArchiveCreated": False,
            }
        },
        headers={"Cache-Control": "no-store"},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_PATH_RE = re.compile(r"^/projects/(?P<project_id>[^/]+)")
RUN_PATH_RE = re.compile(r"/orchestrations/(?P<run_id>[^/]+)")
NON_PROJECT_ROUTE_SEGMENTS = frozenset({"recent-work", "intake", "monitoring"})


def _activity_project_id(path: str) -> str | None:
    match = PROJECT_PATH_RE.match(path)
    if not match:
        return None
    project_id = str(match.group("project_id") or "").strip()
    if not project_id or project_id in NON_PROJECT_ROUTE_SEGMENTS:
        return None
    return project_id


def normalized_activity_repository(
    *,
    tenant_id: str,
    user_id: str,
) -> PostgresActivityLogRepository:
    return PostgresActivityLogRepository(tenant_id=tenant_id, user_id=user_id)


@app.middleware("http")
async def user_activity_middleware(request: Request, call_next):
    if storage_backend() in {"postgres", "postgresql"}:
        with project_request_scope(lambda: projects.project_repository(request)):
            try:
                return await _user_activity_middleware(request, call_next)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            except ProjectVersionConflict:
                return JSONResponse(status_code=409, content={"detail": {"error": "project_version_conflict", "message": "Project changed; reload before retrying."}})
    return await _user_activity_middleware(request, call_next)


async def _user_activity_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    started = time.perf_counter()
    response = None
    try:
        project_id = _activity_project_id(request.url.path)
        if project_id:
            project = None
            if storage_backend() == "postgres":
                project = current_project_scope().get(project_id)
            else:
                project = projects.PROJECTS_DB.get(project_id)
            if isinstance(project, dict):
                try:
                    enforce_project_tenant_scope(request, project)
                except Exception as exc:
                    from fastapi import HTTPException
                    from fastapi.responses import JSONResponse

                    if isinstance(exc, HTTPException):
                        response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
                        return response
                    raise
            elif storage_backend() == "postgres":
                from fastapi.responses import JSONResponse
                response = JSONResponse(status_code=404, content={"detail": "Project not found"})
                return response
        response = await call_next(request)
        return response
    finally:
        path = request.url.path
        if path not in {"/health", "/health/live", "/health/ready"}:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            project_id = _activity_project_id(path)
            run_match = RUN_PATH_RE.search(path)
            user_id = None
            if request.headers.get("authorization"):
                principal = verified_principal_from_request_state(request)
                user_id = principal.user_id if principal else None
            session_id = request.headers.get("x-realytics-session-id")
            if (
                not user_id
                and session_id
                and server_issued_sessions_enabled()
            ):
                session = get_user_session(session_id)
                user_id = session.get("userId") if session else None
            if not user_id and raw_identity_headers_enabled():
                user_id = (
                    request.headers.get("x-realytics-user-id")
                    or request.headers.get("x-user-id")
                    or request.headers.get("x-codex-user-id")
                    or request.query_params.get("user_id")
                )
            user_id = user_id or "anonymous"
            event = {
                "eventId": str(uuid4()),
                "eventType": "api_request",
                "userId": user_id,
                "projectId": project_id,
                "runId": run_match.group("run_id") if run_match else None,
                "method": request.method,
                "path": path,
                "statusCode": response.status_code if response else 500,
                "durationMs": duration_ms,
                "clientHost": request.client.host if request.client else None,
                "userAgent": request.headers.get("user-agent"),
                "requestId": request_id,
                "metadata": {
                    "queryKeys": sorted(request.query_params.keys()),
                },
            }
            # PostgreSQL activity uses the membership-resolved identity, not
            # the raw OAuth ``sub``.  This keeps the append and recent feed
            # lookup on the same stable user key.  Compatibility logging has
            # its own failure boundary so a transient normalized write cannot
            # suppress the existing operational trace.
            if storage_backend() in {"postgres", "postgresql"} and project_id:
                try:
                    context = identity_context_from_request(request)
                    verified_user = context.get("user") or {}
                    tenant_id = str(
                        verified_user.get("tenantId") or ""
                    ).strip()
                    resolved_user_id = str(
                        verified_user.get("userId") or ""
                    ).strip()
                    if not tenant_id or not resolved_user_id:
                        raise ValueError(
                            "Verified tenant and user are required for activity append"
                        )
                    normalized_activity_repository(
                        tenant_id=tenant_id,
                        user_id=resolved_user_id,
                    ).append_api_request(event)
                except Exception:
                    # Activity logging must never break the product workflow.
                    pass

            if storage_backend() not in {"postgres", "postgresql"}:
                try:
                    # Local compatibility retains its projection. PostgreSQL
                    # uses the normalized append above, never legacy app_state.
                    record_user_activity_log(event)
                except Exception:
                    # Activity logging must never break the product workflow.
                    pass

app.include_router(valuemap.router)
app.include_router(projects.router)
app.include_router(projects.monitoring_router)
app.include_router(projects.jobs_router)
app.include_router(projects.source_documents_router)
app.include_router(projects.parser_v2_router)
app.include_router(project_assumptions.router)
app.include_router(cash_flow_comparison.router)
app.include_router(audit.router)
app.include_router(auth.router)
app.include_router(glossary.router)
app.include_router(legal_intelligence.router)
app.include_router(legal_citator.router)
app.include_router(legal_corpus_operations.router)
app.include_router(legal_governance.router)
app.include_router(legal_provider_operations.router)
app.include_router(kakao.router)
app.include_router(google.router)
app.include_router(ops.router)
app.include_router(market.router)
app.include_router(agent_platform.router)



_API_LINKS = {
    "swaggerDocs": "/docs",
    "redoc": "/redoc",
    "openapi": "/openapi.json",
    "health": "/health",
    "infraStatus": "/ops/infra-status",
}
_ENDPOINT_GROUPS = {
    "projects / PF review / 여신문서 / RAG": "/projects",
    "auth / identity": "/auth",
    "glossary / 용어표준화": "/glossary",
    "ops / infra · credit policy": "/ops",
    "valuemap / land data": "/valuemap",
}


@app.get("/")
def read_root(request: Request):
    """Human-friendly landing in a browser; JSON for API clients."""
    accept = request.headers.get("accept", "")
    configured_web_app = os.environ.get("REALYTICS_WEB_BASE_URL", "").strip()
    web_app = configured_web_app if configured_web_app.startswith("https://") else None
    if not is_hardened_environment():
        web_app = web_app or "http://localhost:3000"
    payload = {
        "message": "REALYTICS x PLATFORM9 API Server",
        "version": app.version,
        "webApp": web_app,
        "docs": _API_LINKS,
        "endpointGroups": _ENDPOINT_GROUPS,
        "auth": "Send X-REALYTICS-SESSION-ID (pilot) or Authorization: Bearer <token> (OIDC/IdP).",
    }
    if "text/html" not in accept:
        return payload

    from fastapi.responses import HTMLResponse

    doc_links = "".join(f'<li><a href="{url}">{name}</a> <code>{url}</code></li>' for name, url in _API_LINKS.items())
    group_links = "".join(f'<li><a href="{url}">{name}</a> <code>{url}</code></li>' for name, url in _ENDPOINT_GROUPS.items())
    web_link = f'<a href="{escape(web_app)}">REALYTICS 웹앱</a>' if web_app else "웹앱 주소 미설정"
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"/>
<title>REALYTICS x PLATFORM9 API</title>
<style>
 body{{font-family:Arial,'Malgun Gothic',sans-serif;max-width:820px;margin:40px auto;padding:0 16px;color:#0f172a;line-height:1.6}}
 h1{{margin-bottom:0}} .v{{color:#64748b;font-size:13px}}
 .card{{border:1px solid #e2e8f0;border-radius:10px;padding:16px 20px;margin:16px 0;background:#f8fafc}}
 code{{background:#eef2ff;padding:1px 6px;border-radius:4px;font-size:12px;color:#3730a3}}
 a{{color:#0e7490;text-decoration:none}} a:hover{{text-decoration:underline}}
 ul{{margin:6px 0}} pre{{background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;overflow:auto;font-size:12px}}
 .tag{{display:inline-block;background:#dcfce7;color:#166534;border-radius:6px;padding:2px 8px;font-size:12px;font-weight:bold}}
</style></head><body>
<h1>REALYTICS <span style="color:#6366f1">x</span> PLATFORM9 API</h1>
<p class="v">Real Estate OS · v{app.version} · <span class="tag">API 서버</span></p>
<div class="card">
 <b>API 서버</b> · {web_link}
</div>
<div class="card"><b>문서 · 도구</b><ul>{doc_links}</ul></div>
<div class="card"><b>엔드포인트 그룹</b><ul>{group_links}</ul></div>
<div class="card"><b>API 연동 방법</b>
 <p>현재 서버 주소를 API 기준 주소로 사용합니다. 운영 요청에는 <code>Authorization: Bearer &lt;token&gt;</code>이 필요합니다.</p>
 <pre>POST /projects/{{PROJECT_ID}}/orchestrations/pf-review
GET /projects/{{PROJECT_ID}}/orchestrations/{{RUN_ID}}/credit-documents
GET /projects/{{PROJECT_ID}}/similar-cases/rag</pre>
 <p>전체 스키마는 <a href="/docs">Swagger UI(/docs)</a> 에서 바로 호출·테스트할 수 있습니다.</p>
</div>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/health")
def health_check():
    """Backward-compatible process liveness probe."""

    return {"status": "ok"}


@app.get("/health/live")
def process_liveness():
    return liveness_payload()


@app.get("/health/ready")
def deployment_readiness(request: Request):
    status_code, payload = readiness_payload(request.app)
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={"Cache-Control": "no-store"},
    )
