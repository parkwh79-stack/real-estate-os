from __future__ import annotations

import os
from typing import Any, Dict
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from core.authz import identity_context_from_request, permissions_for_role
from core.idp import idp_status
from core.secret_vault import ingest_user_credential, revoke_credential, revoke_session_credentials
from integrations.kakao import (
    build_kakao_identity_records,
    exchange_kakao_authorization_code,
    fetch_kakao_user_info,
    verify_kakao_oauth_state,
)
from storage import (
    create_user_session,
    delete_user_ai_profile,
    get_user_ai_profile,
    get_user,
    get_user_session,
    list_users,
    record_user_activity_log,
    revoke_user_session,
    upsert_user_ai_profile,
    upsert_tenant,
    upsert_user,
)
from use_cases.user_ai_profile import build_user_ai_context, safe_user_ai_context_summary


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/tenants")
def create_or_update_tenant(payload: Dict[str, Any]):
    tenant = upsert_tenant(payload)
    return {
        "schemaVersion": "realytics-tenant-v1",
        "tenant": tenant,
    }


@router.post("/users")
def create_or_update_user(payload: Dict[str, Any]):
    try:
        user = upsert_user(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "schemaVersion": "realytics-user-v1",
        "user": user,
    }


@router.get("/users")
def get_users(
    tenant_id: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    return {
        "schemaVersion": "realytics-user-list-v1",
        "users": list_users(tenant_id=tenant_id, limit=limit),
    }


@router.post("/session")
def create_session(payload: Dict[str, Any]):
    try:
        session = create_user_session(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    user = get_user(session["userId"])
    record_user_activity_log(
        {
            "eventType": "login",
            "userId": session["userId"],
            "metadata": {
                "tenantId": session["tenantId"],
                "sessionId": session["sessionId"],
                "provider": session["provider"],
                "role": user.get("role") if user else None,
                "loginStatus": "success",
                "host": payload.get("host") or "api",
            },
        }
    )
    return {
        "schemaVersion": "realytics-session-v1",
        "session": session,
        "user": user,
    }


@router.get("/me")
def get_identity_context(request: Request):
    context = identity_context_from_request(request)
    user = context.get("user")
    session = context.get("session")

    if not context.get("authenticated"):
        return {
            "schemaVersion": "realytics-identity-context-v1",
            "authenticated": False,
            "user": user,
            "session": session,
            "permissions": [],
            "authSource": context.get("authSource", "local_session_or_header"),
            "identityProvider": context.get("identityProvider"),
            "limitations": [
                "No persisted user record was found for the supplied session or user header.",
            ],
        }

    return {
        "schemaVersion": "realytics-identity-context-v1",
        "authenticated": user.get("status") == "active",
        "user": user,
        "session": session,
        "permissions": context.get("permissions") or permissions_for_role(user.get("role")),
        "authSource": context.get("authSource", "local_session_or_header"),
        "identityProvider": context.get("identityProvider"),
        "limitations": [
            "Header/session based identity is an MVP control. Bearer-token identity is available when REALYTICS_IDP_MODE is configured.",
        ],
    }


@router.get("/ai-profile")
def get_ai_profile(request: Request):
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")
    user = context["user"]
    profile = get_user_ai_profile(user["userId"], include_secret=False)
    ai_context = build_user_ai_context(
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
        host="web",
    )
    return {
        "schemaVersion": "realytics-user-ai-profile-v1",
        "profile": profile,
        "effectiveAi": safe_user_ai_context_summary(ai_context),
        "fallbackPolicy": {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "description": "개인 API Key가 없으면 시스템에 설정된 무료/기본 Gemini 프로필을 사용합니다. 시스템 키도 없으면 deterministic fallback으로 전환합니다.",
        },
        "secretPolicy": "API Key는 전용 보안 입력 API로만 받고 응답·감사로그·프로필 DB에는 평문으로 저장하지 않습니다. 세션 키는 로그아웃 시 폐기됩니다.",
    }


@router.post("/ai-credentials")
def ingest_ai_credential(request: Request, payload: Dict[str, Any]):
    """Accept a BYOK secret once and persist only its non-secret reference."""
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")
    session = context.get("session") or {}
    session_id = session.get("sessionId")
    user = context["user"]
    provider = str(payload.get("provider") or "gemini").strip().lower()
    api_key = payload.get("apiKey") or payload.get("api_key")
    try:
        credential = ingest_user_credential(
            user_id=user["userId"],
            tenant_id=user.get("tenantId"),
            session_id=session_id,
            provider=provider,
            secret=str(api_key or ""),
            expires_on_logout=bool(payload.get("expiresOnLogout", True)),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    previous = get_user_ai_profile(user["userId"], include_secret=True) or {}
    previous_ref = previous.get("credentialRef")
    profile = upsert_user_ai_profile(
        {
            "userId": user["userId"],
            "tenantId": user.get("tenantId"),
            "provider": provider,
            "model": payload.get("model"),
            "credentialRef": credential["credentialRef"],
            "credentialBackend": credential["backend"],
            "apiKeyMasked": credential["masked"],
            "allowSystemFallback": bool(payload.get("allowSystemFallback", True)),
            "metadata": {
                "expiresOnLogout": credential["expiresOnLogout"],
                "ingestPath": "/auth/ai-credentials",
            },
        }
    )
    if previous_ref and previous_ref != credential["credentialRef"]:
        revoke_credential(previous_ref)
    record_user_activity_log(
        {
            "eventType": "ai_credential_ingested",
            "userId": user["userId"],
            "metadata": {
                "tenantId": user.get("tenantId"),
                "provider": provider,
                "credentialBackend": credential["backend"],
                "expiresOnLogout": credential["expiresOnLogout"],
                "secretPersistedInProfile": False,
            },
        }
    )
    return {
        "schemaVersion": "realytics-user-ai-credential-v1",
        "profile": profile,
        "credential": {
            "configured": True,
            "backend": credential["backend"],
            "expiresOnLogout": credential["expiresOnLogout"],
            "masked": credential["masked"],
        },
    }


@router.post("/ai-profile")
def save_ai_profile(request: Request, payload: Dict[str, Any]):
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")
    forbidden_secret_fields = {"apiKey", "api_key", "credentialRef", "credential_ref"}
    supplied_secret_fields = sorted(forbidden_secret_fields.intersection(payload))
    if supplied_secret_fields:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "secret_ingest_endpoint_required",
                "message": "API Key는 /auth/ai-credentials 전용 엔드포인트로 입력해야 합니다.",
                "fields": supplied_secret_fields,
            },
        )
    user = context["user"]
    profile = upsert_user_ai_profile(
        {
            **payload,
            "userId": user["userId"],
            "tenantId": user.get("tenantId"),
        }
    )
    record_user_activity_log(
        {
            "eventType": "ai_profile_updated",
            "userId": user["userId"],
            "metadata": {
                "tenantId": user.get("tenantId"),
                "provider": profile.get("provider"),
                "model": profile.get("model"),
                "apiKeyConfigured": profile.get("apiKeyConfigured"),
                "allowSystemFallback": profile.get("allowSystemFallback"),
            },
        }
    )
    ai_context = build_user_ai_context(user_id=user["userId"], tenant_id=user.get("tenantId"), host="web")
    return {
        "schemaVersion": "realytics-user-ai-profile-v1",
        "profile": profile,
        "effectiveAi": safe_user_ai_context_summary(ai_context),
    }


@router.delete("/ai-profile")
def remove_ai_profile(request: Request):
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")
    user = context["user"]
    current_profile = get_user_ai_profile(user["userId"], include_secret=True) or {}
    credential_revoked = revoke_credential(current_profile.get("credentialRef"))
    deleted = delete_user_ai_profile(user["userId"])
    record_user_activity_log(
        {
            "eventType": "ai_profile_deleted",
            "userId": user["userId"],
            "metadata": {
                "tenantId": user.get("tenantId"),
                "deleted": deleted,
                "credentialRevoked": credential_revoked,
            },
        }
    )
    ai_context = build_user_ai_context(user_id=user["userId"], tenant_id=user.get("tenantId"), host="web")
    return {
        "schemaVersion": "realytics-user-ai-profile-v1",
        "deleted": deleted,
        "profile": None,
        "effectiveAi": safe_user_ai_context_summary(ai_context),
    }


@router.delete("/ai-credentials/current")
def revoke_current_ai_credential(request: Request):
    """Revoke BYOK while preserving the user's provider/model preferences.

    This path also supports OIDC bearer identities. REALYTICS cannot revoke
    the upstream identity-provider session, but it can and must remove its own
    credential reference before the browser completes logout.
    """
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")
    user = context["user"]
    current_profile = get_user_ai_profile(user["userId"], include_secret=True) or {}
    credential_revoked = revoke_credential(current_profile.get("credentialRef"))
    if current_profile:
        upsert_user_ai_profile(
            {
                "userId": user["userId"],
                "tenantId": user.get("tenantId"),
                "clearApiKey": True,
            }
        )
    record_user_activity_log(
        {
            "eventType": "ai_credential_revoked",
            "userId": user["userId"],
            "metadata": {
                "tenantId": user.get("tenantId"),
                "credentialRevoked": credential_revoked,
                "reason": "explicit_logout_cleanup",
            },
        }
    )
    return {
        "schemaVersion": "realytics-user-ai-credential-revocation-v1",
        "credentialRevoked": credential_revoked,
    }


@router.delete("/session/current")
def logout_current_session(request: Request):
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")
    session = context.get("session") or {}
    session_id = str(session.get("sessionId") or "")
    if not session_id:
        raise HTTPException(
            status_code=400,
            detail="Bearer-token sessions must be revoked by the configured identity provider.",
        )
    user = context["user"]
    current_profile = get_user_ai_profile(user["userId"], include_secret=True) or {}
    profile_credential_revoked = revoke_credential(current_profile.get("credentialRef"))
    if current_profile:
        upsert_user_ai_profile(
            {
                "userId": user["userId"],
                "tenantId": user.get("tenantId"),
                "clearApiKey": True,
            }
        )
    revoked_credentials = revoke_session_credentials(session_id)
    session_revoked = revoke_user_session(session_id)
    record_user_activity_log(
        {
            "eventType": "logout",
            "userId": user["userId"],
            "metadata": {
                "tenantId": user.get("tenantId"),
                "sessionId": session_id,
                "sessionRevoked": session_revoked,
                "sessionCredentialsRevoked": revoked_credentials,
                "profileCredentialRevoked": profile_credential_revoked,
            },
        }
    )
    return {
        "schemaVersion": "realytics-session-logout-v1",
        "sessionRevoked": session_revoked,
        "sessionCredentialsRevoked": revoked_credentials,
        "profileCredentialRevoked": profile_credential_revoked,
    }


@router.get("/idp/status")
def get_idp_status():
    return idp_status()


@router.get("/kakao/callback")
def kakao_oidc_callback(
    request: Request,
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
):
    if error:
        record_user_activity_log(
            {
                "eventType": "kakao_login_callback_failed",
                "userId": "anonymous",
                "metadata": {
                    "provider": "kakao",
                    "error": error,
                    "errorDescription": error_description,
                    "host": "kakao_oauth_callback",
                },
            }
        )
        raise HTTPException(status_code=401, detail={"error": error, "errorDescription": error_description})
    if not code:
        raise HTTPException(status_code=400, detail={"error": "missing_kakao_authorization_code"})

    state_payload = verify_kakao_oauth_state(state)
    tokens = exchange_kakao_authorization_code(code, redirect_uri=os.environ.get("KAKAO_REDIRECT_URI"))
    user_info = fetch_kakao_user_info(str(tokens.get("access_token") or "")) if tokens.get("access_token") else {}
    identity = build_kakao_identity_records(tokens=tokens, user_info=user_info, state_payload=state_payload)

    tenant_id = identity["user"]["tenantId"]
    upsert_tenant(
        {
            "tenantId": tenant_id,
            "name": os.environ.get("KAKAO_DEFAULT_TENANT_NAME") or "Kakao Pilot Workspace",
            "status": "active",
        }
    )
    user = upsert_user(identity["user"])
    session = create_user_session(identity["session"])
    record_user_activity_log(
        {
            "eventType": "kakao_login_callback",
            "userId": session["userId"],
            "metadata": {
                "tenantId": session["tenantId"],
                "sessionId": session["sessionId"],
                "provider": "kakao",
                "role": user.get("role") if user else None,
                "loginStatus": "success",
                "host": "kakao_oauth_callback",
                "stateSignatureVerified": bool(state_payload.get("signatureVerified")),
                "requestPath": str(request.url.path),
            },
        }
    )

    web_base_url = os.environ.get("REALYTICS_WEB_BASE_URL")
    if web_base_url:
        redirect_path = _safe_relative_redirect_path(identity.get("redirectPath"))
        session_url = (
            f"{web_base_url.rstrip('/')}/auth/kakao/session"
            f"?session_id={quote(session['sessionId'])}&redirect={quote(redirect_path)}"
        )
        return RedirectResponse(session_url, status_code=303)

    return {
        "schemaVersion": "realytics-kakao-callback-session-v1",
        "session": session,
        "user": user,
        "redirectPath": identity.get("redirectPath"),
        "identityProvider": {
            "provider": "kakao",
            "idTokenSignatureVerification": "not_yet_verified_jwks",
            "stateSignatureVerified": bool(state_payload.get("signatureVerified")),
        },
    }


def _safe_relative_redirect_path(value: Any) -> str:
    path = str(value or "/dashboard")
    if not path.startswith("/") or path.startswith("//") or "://" in path:
        return "/dashboard"
    return path


@router.get("/stats")
def get_system_stats():
    """Return real system statistics from the database for the login screen."""
    import sqlite3
    from storage import sqlite_file_path
    
    conn = sqlite3.connect(sqlite_file_path())
    cursor = conn.cursor()
    
    users_count = 0
    logs_count = 0
    projects_count = 0
    
    try:
        cursor.execute("SELECT count(*) FROM users")
        users_count = cursor.fetchone()[0]
    except Exception:
        pass
        
    try:
        cursor.execute("SELECT count(*) FROM user_activity_logs")
        logs_count = cursor.fetchone()[0]
    except Exception:
        pass
        
    try:
        cursor.execute("SELECT count(*) FROM projects")
        projects_count = cursor.fetchone()[0]
    except Exception:
        pass
        
    conn.close()
    
    return {
        "schemaVersion": "realytics-system-stats-v1",
        "usersCount": users_count,
        "dataUsageCount": logs_count,
        "projectsCount": projects_count,
        "projectScaleTrillionWon": round(projects_count * 0.15, 1) if projects_count > 0 else 0
    }
