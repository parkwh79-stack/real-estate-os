import re
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from routers import audit, auth, glossary, kakao, valuemap, projects, ops
from core.idp import principal_from_request
from storage import get_user_session, record_user_activity_log

app = FastAPI(
    title="REALYTICS x PLATFORM9 API",
    description="Backend API server for Real Estate OS alpha workflows",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_PATH_RE = re.compile(r"^/projects/(?P<project_id>[^/]+)")
RUN_PATH_RE = re.compile(r"/orchestrations/(?P<run_id>[^/]+)")


@app.middleware("http")
async def user_activity_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    started = time.perf_counter()
    response = None
    try:
        response = await call_next(request)
        return response
    finally:
        path = request.url.path
        if path not in {"/health"}:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            project_match = PROJECT_PATH_RE.match(path)
            run_match = RUN_PATH_RE.search(path)
            user_id = (
                request.headers.get("x-realytics-user-id")
                or request.headers.get("x-user-id")
                or request.headers.get("x-codex-user-id")
                or request.query_params.get("user_id")
            )
            session_id = request.headers.get("x-realytics-session-id")
            if not user_id and session_id:
                session = get_user_session(session_id)
                user_id = session.get("userId") if session else None
            if not user_id and request.headers.get("authorization"):
                try:
                    principal = principal_from_request(request)
                    user_id = principal.user_id if principal else None
                except Exception:
                    user_id = None
            user_id = user_id or "anonymous"
            try:
                record_user_activity_log(
                    {
                        "eventType": "api_request",
                        "userId": user_id,
                        "projectId": project_match.group("project_id") if project_match else None,
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
                )
            except Exception:
                # Activity logging must never break the product workflow.
                pass

app.include_router(valuemap.router)
app.include_router(projects.router)
app.include_router(projects.monitoring_router)
app.include_router(audit.router)
app.include_router(auth.router)
app.include_router(glossary.router)
app.include_router(kakao.router)
app.include_router(ops.router)


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
    payload = {
        "message": "REALYTICS x PLATFORM9 API Server",
        "version": app.version,
        "webApp": "http://localhost:3000",
        "docs": _API_LINKS,
        "endpointGroups": _ENDPOINT_GROUPS,
        "auth": "Send X-REALYTICS-SESSION-ID (pilot) or Authorization: Bearer <token> (OIDC/IdP).",
    }
    if "text/html" not in accept:
        return payload

    from fastapi.responses import HTMLResponse

    doc_links = "".join(f'<li><a href="{url}">{name}</a> <code>{url}</code></li>' for name, url in _API_LINKS.items())
    group_links = "".join(f'<li><a href="{url}">{name}</a> <code>{url}</code></li>' for name, url in _ENDPOINT_GROUPS.items())
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
 <b>여기는 API 서버입니다.</b> 실제 심사 화면(웹앱)은 <a href="http://localhost:3000">http://localhost:3000</a> 입니다.
</div>
<div class="card"><b>문서 · 도구</b><ul>{doc_links}</ul></div>
<div class="card"><b>엔드포인트 그룹</b><ul>{group_links}</ul></div>
<div class="card"><b>API 연동 방법</b>
 <p>Base URL: <code>http://127.0.0.1:8000</code> · 인증 헤더: <code>X-REALYTICS-SESSION-ID</code>(파일럿) 또는 <code>Authorization: Bearer &lt;token&gt;</code>(OIDC).</p>
 <pre># PF 심사 실행
curl -X POST http://127.0.0.1:8000/projects/{{PROJECT_ID}}/orchestrations/pf-review \\
  -H "Content-Type: application/json" -d '{{}}'

# 여신 문서(거래조건서·여신심사보고서) 조회
curl http://127.0.0.1:8000/projects/{{PROJECT_ID}}/orchestrations/{{RUN_ID}}/credit-documents

# 유사사례 RAG
curl "http://127.0.0.1:8000/projects/{{PROJECT_ID}}/similar-cases/rag?q=대구+미분양"</pre>
 <p>전체 스키마는 <a href="/docs">Swagger UI(/docs)</a> 에서 바로 호출·테스트할 수 있습니다.</p>
</div>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/health")
def health_check():
    return {"status": "ok"}
