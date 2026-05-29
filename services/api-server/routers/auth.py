from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException, Query

from core.authz import permissions_for_role
from storage import (
    create_user_session,
    get_user,
    get_user_session,
    list_users,
    record_user_activity_log,
    upsert_tenant,
    upsert_user,
)


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
def get_identity_context(
    x_realytics_session_id: str | None = Header(None),
    x_realytics_user_id: str | None = Header(None),
    x_user_id: str | None = Header(None),
):
    session = get_user_session(x_realytics_session_id) if x_realytics_session_id else None
    user_id = session.get("userId") if session else (x_realytics_user_id or x_user_id)
    user = get_user(user_id) if user_id else None

    if not user:
        return {
            "schemaVersion": "realytics-identity-context-v1",
            "authenticated": False,
            "user": {
                "userId": user_id or "anonymous",
                "tenantId": None,
                "role": "anonymous",
                "status": "unknown",
            },
            "session": session,
            "permissions": [],
            "limitations": [
                "No persisted user record was found for the supplied session or user header.",
            ],
        }

    return {
        "schemaVersion": "realytics-identity-context-v1",
        "authenticated": user.get("status") == "active",
        "user": user,
        "session": session,
        "permissions": permissions_for_role(user.get("role")),
        "limitations": [
            "Header/session based identity is an MVP control and not a substitute for a production identity provider.",
        ],
    }
