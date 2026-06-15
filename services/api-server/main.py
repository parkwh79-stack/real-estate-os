import re
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from routers import audit, auth, kakao, valuemap, projects
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
app.include_router(audit.router)
app.include_router(auth.router)
app.include_router(kakao.router)


@app.get("/")
def read_root():
    return {"message": "REALYTICS x PLATFORM9 API Server"}


@app.get("/health")
def health_check():
    return {"status": "ok"}
