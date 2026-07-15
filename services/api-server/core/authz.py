from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import HTTPException, Request

from core.idp import (
    enforce_tenant_header_match,
    principal_from_request,
    sanitized_idp_config,
    tenant_isolation_mode,
)
from core.runtime_profile import security_default
from storage import get_user, get_user_session, upsert_user


ROLE_PERMISSIONS = {
    "admin": {
        "project:read",
        "project:write",
        "project:analyze",
        "evidence:read",
        "report:read",
        "report:export",
        "risk:review",
        "assumption:override",
        "audit:read",
        "user:manage",
        "agent:operate",
        "brain:manage",
        "tool:read",
        "tool:execute",
        "learning:read",
        "learning:review",
        "learning:promote",
        "coding:request",
        "coding:operate",
    },
    "reviewer": {
        "project:read",
        "project:analyze",
        "evidence:read",
        "report:read",
        "report:export",
        "risk:review",
        "assumption:override",
        "audit:read",
        "agent:operate",
        "tool:read",
        "learning:read",
        "learning:review",
    },
    "analyst": {
        "project:read",
        "project:write",
        "project:analyze",
        "evidence:read",
        "report:read",
        "report:export",
        "audit:read:self",
        "agent:operate",
        "tool:read",
        "coding:request",
    },
    "viewer": {
        "project:read",
        "report:read",
        "tool:read",
    },
}


def permissions_for_role(role: str | None) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role or "viewer", ROLE_PERMISSIONS["viewer"]))


def identity_context_from_request(request: Request) -> Dict[str, Any]:
    principal = principal_from_request(request)
    if principal:
        enforce_tenant_header_match(request, principal)
        user = upsert_user(
            {
                "userId": principal.user_id,
                "tenantId": principal.tenant_id,
                "email": principal.email,
                "displayName": principal.display_name,
                "role": principal.role,
                "status": "active",
                "metadata": {
                    "identityProvider": principal.provider,
                    "tokenId": principal.token_id,
                    "authSource": "bearer_token",
                    "principalType": principal.principal_type,
                },
            }
        )
        permissions = permissions_for_role(user.get("role"))
        assurance = _identity_assurance(principal.claims)
        return {
            "authenticated": True,
            "user": user,
            "session": None,
            "role": user.get("role"),
            "permissions": permissions,
            "rbacMode": rbac_mode(),
            "identityProvider": sanitized_idp_config(),
            "identityAssurance": assurance,
            "authSource": "bearer_token",
        }

    session_id = request.headers.get("x-realytics-session-id")
    session = get_user_session(session_id) if session_id else None
    if session and session.get("status") != "active":
        session = None
    user_id = (
        session.get("userId")
        if session
        else (
            request.headers.get("x-realytics-user-id")
            or request.headers.get("x-user-id")
            or request.headers.get("x-codex-user-id")
            or request.query_params.get("user_id")
        )
    )
    user = get_user(user_id) if user_id else None
    role = user.get("role") if user else "anonymous"
    permissions = permissions_for_role(role) if user else []
    return {
        "authenticated": bool(user and user.get("status") == "active"),
        "user": user
        or {
            "userId": user_id or "anonymous",
            "tenantId": None,
            "role": "anonymous",
            "status": "unknown",
        },
        "session": session,
        "role": role,
        "permissions": permissions,
        "rbacMode": rbac_mode(),
    }


def authorize_request(request: Request, permission: str) -> Dict[str, Any]:
    context = identity_context_from_request(request)
    permissions = set(context.get("permissions", []))
    role = context.get("role")
    is_known_user = context.get("authenticated") is True

    if permission in permissions or "user:manage" in permissions:
        context["authorization"] = {"allowed": True, "permission": permission, "reason": "permission_granted"}
        return context

    if not is_known_user and rbac_mode() != "strict":
        context["authorization"] = {
            "allowed": True,
            "permission": permission,
            "reason": "legacy_identity_permissive_mode",
            "warning": "Persisted user/session not found; set REALYTICS_RBAC_MODE=strict to block this request.",
        }
        return context

    raise HTTPException(
        status_code=403,
        detail={
            "error": "forbidden",
            "requiredPermission": permission,
            "role": role,
            "rbacMode": rbac_mode(),
        },
    )


def enforce_project_tenant_scope(request: Request, project: Dict[str, Any]) -> Dict[str, Any]:
    """Fail closed on every project-scoped request in strict tenant mode."""
    context = identity_context_from_request(request)
    if tenant_isolation_mode() != "strict":
        return context
    if not context.get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required", "scope": "project"},
        )
    metadata = project.get("_meta") if isinstance(project.get("_meta"), dict) else {}
    project_tenant = metadata.get("tenantId") or metadata.get("tenant_id")
    actor_tenant = (context.get("user") or {}).get("tenantId")
    if not project_tenant:
        raise HTTPException(
            status_code=403,
            detail={"error": "project_tenant_unassigned", "scope": "project"},
        )
    if str(project_tenant) != str(actor_tenant or ""):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "tenant_scope_mismatch",
                "projectTenantId": str(project_tenant),
                "actorTenantId": str(actor_tenant or ""),
            },
        )
    return context


def rbac_mode() -> str:
    return (
        os.environ.get("REALYTICS_RBAC_MODE")
        or security_default(local="permissive", hardened="strict")
    ).lower()


def _identity_assurance(claims: Dict[str, Any]) -> Dict[str, Any]:
    raw_amr = claims.get("amr")
    if isinstance(raw_amr, str):
        amr = [raw_amr]
    elif isinstance(raw_amr, list):
        amr = [str(item) for item in raw_amr]
    else:
        amr = []
    acr = str(claims.get("acr") or "") or None
    mfa_markers = {"mfa", "otp", "hwk", "swk", "fido", "webauthn"}
    return {
        "amr": amr,
        "acr": acr,
        "mfaVerified": bool(mfa_markers.intersection(item.casefold() for item in amr)),
        "source": "verified_bearer_claims",
    }
