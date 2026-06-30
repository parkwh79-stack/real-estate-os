from __future__ import annotations

import os
from typing import Any, Dict
from uuid import uuid4
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from integrations.google import (
    build_google_authorize_url,
    build_google_identity_records,
    exchange_google_authorization_code,
    fetch_google_user_info,
    google_integration_status,
    google_login_configured,
)
from storage import (
    create_user_session,
    record_user_activity_log,
    upsert_tenant,
    upsert_user,
)

router = APIRouter(prefix="/integrations/google", tags=["google"])


def _safe_relative_redirect_path(value: Any) -> str:
    path = str(value or "/start")
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        return "/start"
    if path in {"/", "/dashboard"}:
        return "/start"
    return path


@router.get("/status")
def get_google_status():
    return google_integration_status()


@router.post("/login/authorize-url")
def create_google_authorize_url(payload: Dict[str, Any] | None = None):
    """Google OAuth authorize URL.

    When GOOGLE_CLIENT_ID is configured, returns the real Google consent URL.
    Otherwise falls back to a clearly-labelled pilot mock callback so the
    pilot UI keeps working without fabricating a real Google identity silently.
    """
    payload = payload or {}
    redirect_path = _safe_relative_redirect_path(payload.get("redirectPath") or payload.get("redirect_path"))
    res = build_google_authorize_url(
        state=quote(redirect_path),
        redirect_uri=payload.get("redirectUri") or payload.get("redirect_uri"),
        prompt=payload.get("prompt") or "consent",
    )
    if not res.get("ready") or not res.get("authorizeUrl"):
        base_url = os.environ.get("REALYTICS_API_BASE_URL", "http://127.0.0.1:8000")
        res["ready"] = True
        res["mode"] = "pilot_mock"
        res["authorizeUrl"] = f"{base_url}/integrations/google/callback?redirect_path={quote(redirect_path)}"
    else:
        res["mode"] = "oauth"
    return res


@router.get("/callback")
def google_oauth_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    redirect_path: str = Query("/start"),
):
    """Google OAuth callback.

    Real flow (credentials configured + authorization `code` present): exchange
    the code for tokens, fetch userinfo, and create the REALYTICS user/session.
    Pilot fallback (no credentials): create a clearly-labelled mock session so
    demos work — this never claims to be a verified Google account.
    """
    if error:
        record_user_activity_log(
            {
                "eventType": "google_login_callback_failed",
                "userId": "anonymous",
                "metadata": {
                    "provider": "google",
                    "error": error,
                    "errorDescription": error_description,
                    "host": "google_oauth_callback",
                },
            }
        )
        raise HTTPException(status_code=401, detail={"error": error, "errorDescription": error_description})

    if google_login_configured() and code:
        resolved_redirect_path = _safe_relative_redirect_path(unquote(state) if state else redirect_path)
        tokens = exchange_google_authorization_code(code)
        user_info = (
            fetch_google_user_info(str(tokens.get("access_token") or ""))
            if tokens.get("access_token")
            else {}
        )
        identity = build_google_identity_records(
            tokens=tokens,
            user_info=user_info,
            redirect_path=resolved_redirect_path,
        )
        tenant_id = identity["user"]["tenantId"]
        upsert_tenant(
            {
                "tenantId": tenant_id,
                "name": os.environ.get("GOOGLE_DEFAULT_TENANT_NAME") or "Google Workspace",
                "status": "active",
            }
        )
        user = upsert_user(identity["user"])
        session = create_user_session(identity["session"])
        record_user_activity_log(
            {
                "eventType": "google_login_callback",
                "userId": session["userId"],
                "metadata": {
                    "tenantId": session["tenantId"],
                    "sessionId": session["sessionId"],
                    "provider": "google",
                    "role": user.get("role") if user else None,
                    "loginStatus": "success",
                    "host": "google_oauth_callback",
                    "loginMode": "oauth",
                },
            }
        )
        return _redirect_to_web_session(session, identity["redirectPath"])

    # Pilot mock fallback (no Google credentials configured).
    return _mock_google_callback(_safe_relative_redirect_path(redirect_path))


def _mock_google_callback(redirect_path: str):
    tenant_id = "google-workspace"
    upsert_tenant(
        {
            "tenantId": tenant_id,
            "name": "Google Pilot Workspace",
            "status": "active",
        }
    )
    user_id = f"google_user_{uuid4().hex[:8]}"
    user = upsert_user(
        {
            "userId": user_id,
            "tenantId": tenant_id,
            "email": f"{user_id}@gmail.com",
            "displayName": "구글 회원 (파일럿)",
            "role": "reviewer",
            "status": "active",
            "metadata": {"provider": "google", "loginMode": "pilot_mock"},
        }
    )
    session = create_user_session(
        {
            "userId": user_id,
            "tenantId": tenant_id,
            "provider": "google",
            "host": "google_oauth_callback",
            "metadata": {"loginMode": "pilot_mock"},
        }
    )
    record_user_activity_log(
        {
            "eventType": "google_login_callback",
            "userId": session["userId"],
            "metadata": {
                "tenantId": session["tenantId"],
                "sessionId": session["sessionId"],
                "provider": "google",
                "role": user.get("role"),
                "loginStatus": "success",
                "loginMode": "pilot_mock",
            },
        }
    )
    return _redirect_to_web_session(session, redirect_path)


def _redirect_to_web_session(session: Dict[str, Any], redirect_path: str):
    web_base_url = os.environ.get("REALYTICS_WEB_BASE_URL", "http://localhost:3000")
    session_url = (
        f"{web_base_url.rstrip('/')}/auth/kakao/session"  # shared frontend session receiver
        f"?session_id={quote(session['sessionId'])}&redirect={quote(redirect_path)}"
    )
    return RedirectResponse(session_url, status_code=303)
