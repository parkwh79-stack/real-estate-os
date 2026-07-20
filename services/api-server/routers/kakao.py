import os
from typing import Any, Dict
from uuid import uuid4
from urllib.parse import quote
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from core.runtime_profile import (
    legacy_oauth_sessions_enabled,
    oauth_mock_callbacks_enabled,
)
from integrations.kakao import (
    build_kakao_authorize_url,
    build_safe_kakao_message_preview,
    create_kakao_oauth_state,
    kakao_integration_status,
)
from storage import (
    create_user_session,
    record_user_activity_log,
    upsert_tenant,
    upsert_user,
)


router = APIRouter(prefix="/integrations/kakao", tags=["kakao"])


@router.get("/status")
def get_kakao_status():
    return kakao_integration_status()


@router.post("/login/authorize-url")
def create_kakao_authorize_url(payload: Dict[str, Any] | None = None):
    payload = payload or {}
    nonce = str(payload.get("nonce") or uuid4())
    redirect_path = str(payload.get("redirectPath") or payload.get("redirect_path") or "/start")
    if redirect_path in {"/", "/dashboard"} or not redirect_path.startswith("/") or redirect_path.startswith("//") or "://" in redirect_path:
        redirect_path = "/start"
    state = str(payload.get("state") or create_kakao_oauth_state(
        nonce=nonce,
        redirect_path=redirect_path,
    ))
    res = build_kakao_authorize_url(
        state=state,
        nonce=nonce,
        redirect_uri=payload.get("redirectUri") or payload.get("redirect_uri"),
        prompt=payload.get("prompt"),
    )
    if not res.get("ready") or not res.get("authorizeUrl"):
        if not oauth_mock_callbacks_enabled():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "kakao_oauth_not_configured",
                    "message": "Kakao OAuth is unavailable and mock callbacks are disabled.",
                },
            )
        base_url = os.environ.get("REALYTICS_API_BASE_URL", "http://127.0.0.1:8000")
        res["ready"] = True
        res["authorizeUrl"] = f"{base_url}/integrations/kakao/mock-callback?redirect_path={quote(redirect_path)}"
    elif not legacy_oauth_sessions_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "legacy_oauth_session_disabled",
                "message": (
                    "API-owned Kakao OAuth sessions are disabled. "
                    "Use the configured Supabase/OIDC login flow."
                ),
            },
        )
    return res


@router.get("/mock-callback")
def kakao_mock_callback(
    request: Request,
    redirect_path: str = Query("/start"),
):
    if not oauth_mock_callbacks_enabled():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "kakao_oauth_mock_disabled",
                "message": "Kakao mock callbacks are disabled outside explicit local/test use.",
            },
        )
    if redirect_path in {"/", "/dashboard"} or not redirect_path.startswith("/") or redirect_path.startswith("//") or "://" in redirect_path:
        redirect_path = "/start"
    tenant_id = "kakao-workspace"
    upsert_tenant({
        "tenantId": tenant_id,
        "name": "Kakao Pilot Workspace",
        "status": "active",
    })
    user_id = f"kakao_user_{uuid4().hex[:8]}"
    user = upsert_user({
        "userId": user_id,
        "tenantId": tenant_id,
        "email": f"{user_id}@kakao.com",
        "displayName": "카카오 인증 회원",
        "role": "reviewer",
        "status": "active",
        "metadata": {
            "provider": "kakao",
            "registeredAt": "2026-06-29T00:00:00Z"
        }
    })
    session = create_user_session({
        "userId": user_id,
        "tenantId": tenant_id,
        "provider": "kakao",
        "host": "kakao_oauth_callback"
    })
    record_user_activity_log({
        "eventType": "kakao_login_callback",
        "userId": session["userId"],
        "metadata": {
            "tenantId": session["tenantId"],
            "sessionId": session["sessionId"],
            "provider": "kakao",
            "role": user.get("role"),
            "loginStatus": "success",
        },
    })
    web_base_url = os.environ.get("REALYTICS_WEB_BASE_URL", "http://localhost:3000")
    session_url = (
        f"{web_base_url.rstrip('/')}/auth/kakao/session"
        f"?session_id={quote(session['sessionId'])}&redirect={quote(redirect_path)}"
    )
    return RedirectResponse(session_url, status_code=303)


@router.post("/messages/preview")
def preview_kakao_message(payload: Dict[str, Any], request: Request):
    preview = build_safe_kakao_message_preview(payload)
    record_user_activity_log(
        {
            "eventType": "kakao_message_preview",
            "userId": request.headers.get("x-realytics-user-id") or "system",
            "projectId": payload.get("projectId") or payload.get("project_id"),
            "metadata": {
                "eventType": preview.get("eventType"),
                "readyToSend": preview.get("readyToSend"),
                "blockedReasons": preview.get("blockedReasons", []),
                "recommendedMobilePath": preview.get("recommendedMobilePath"),
                "messageMode": "preview_only",
            },
        }
    )
    return preview
