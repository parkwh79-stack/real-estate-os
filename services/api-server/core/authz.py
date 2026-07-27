from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException, Request

from core.idp import (
    IdpPrincipal,
    canonicalize_issuer,
    enforce_tenant_header_match,
    principal_from_request,
    sanitized_idp_config,
    tenant_isolation_mode,
)
from core.runtime_profile import (
    identity_membership_authority,
    raw_identity_headers_enabled,
    security_default,
    server_issued_sessions_enabled,
)
from db.repositories.identity_membership_repository import (
    IdentityMembershipRepository,
    IdentityMembershipRepositoryUnavailable,
    PostgresIdentityMembershipRepository,
)
from storage import get_user, get_user_session, upsert_user


ROLE_PERMISSIONS = {
    "admin": {
        "project:read",
        "project:write",
        "project:analyze",
        "project:review",
        "legal:review",
        "legal:operate",
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
        "project:review",
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
    "legal_reviewer": {
        "project:read",
        "project:analyze",
        "project:review",
        "legal:review",
        "evidence:read",
        "report:read",
        "risk:review",
        "audit:read",
    },
    "legal_counsel_approver": {
        "project:read",
        "project:analyze",
        "project:review",
        "legal:review",
        "evidence:read",
        "report:read",
        "risk:review",
        "audit:read",
    },
    "external_counsel": {
        "project:read",
        "project:review",
        "legal:review",
        "evidence:read",
        "report:read",
        "audit:read:self",
    },
    "compliance_officer": {
        "project:read",
        "project:review",
        "legal:review",
        "legal:operate",
        "evidence:read",
        "report:read",
        "risk:review",
        "audit:read",
    },
}


def permissions_for_role(role: str | None) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role or "viewer", ROLE_PERMISSIONS["viewer"]))


def is_explicit_human_principal(context: Dict[str, Any]) -> bool:
    """Return true only for a server-classified, authenticated human actor.

    A legal role is an authorization attribute, not proof that the bearer is
    a person.  Final review, governance, delegation, and replay approvals must
    reject signed client-credential/service principals even if an IdP
    mistakenly assigns them a human-facing role.
    """

    if context.get("authenticated") is not True:
        return False
    user = context.get("user")
    if not isinstance(user, dict):
        return False
    metadata = user.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return (
        str(metadata.get("principalType") or "").strip().casefold()
        == "human"
    )


def identity_context_from_request(request: Request) -> Dict[str, Any]:
    principal = principal_from_request(request)
    if principal:
        if identity_membership_authority() == "postgres":
            principal, authority_metadata = _resolve_postgres_membership(
                request,
                principal,
                repository=identity_membership_repository(),
            )
        else:
            enforce_tenant_header_match(request, principal)
            authority_metadata = {
                "source": "verified_bearer_claims",
                "mode": "claims_compatibility",
            }
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
                    "issuer": principal.issuer,
                    "subject": principal.subject,
                    "membershipAuthority": authority_metadata,
                },
            }
        )
        # The legacy user store is a read-model projection. Never read tenant,
        # role, or status back from it to authorize a verified bearer.
        user = {
            **user,
            "userId": principal.user_id,
            "tenantId": principal.tenant_id,
            "role": principal.role,
            "status": "active",
        }
        permissions = permissions_for_role(principal.role)
        assurance = _identity_assurance(principal.claims)
        return {
            "authenticated": True,
            "user": user,
            "session": None,
            "role": principal.role,
            "permissions": permissions,
            "rbacMode": rbac_mode(),
            "identityProvider": sanitized_idp_config(),
            "identityAssurance": assurance,
            "membershipAuthority": authority_metadata,
            "authSource": "bearer_token",
        }

    session_id = request.headers.get("x-realytics-session-id")
    if session_id and not server_issued_sessions_enabled():
        raise HTTPException(
            status_code=401,
            detail={
                "error": "legacy_session_disabled",
                "message": (
                    "Opaque REALYTICS session ids are disabled. "
                    "Use a verified identity-provider bearer token."
                ),
            },
        )
    session = get_user_session(session_id) if session_id else None
    if session and session.get("status") != "active":
        session = None
    raw_user_id = (
        request.headers.get("x-realytics-user-id")
        or request.headers.get("x-user-id")
        or request.headers.get("x-codex-user-id")
        or request.query_params.get("user_id")
    )
    if not session and raw_user_id and not raw_identity_headers_enabled():
        raise HTTPException(
            status_code=401,
            detail={
                "error": "raw_identity_header_disabled",
                "message": (
                    "User-id headers are not an authentication mechanism. "
                    "Use a verified bearer token or an approved server-issued session."
                ),
            },
        )
    user_id = (
        session.get("userId")
        if session
        else raw_user_id
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


def require_step_up_authentication(
    context: Dict[str, Any],
    *,
    action: str,
) -> Dict[str, Any]:
    """Require a recent, verified human MFA proof for a high-risk action.

    The assurance is derived only from the already verified bearer claims.
    Client timestamps, UI state, membership role, and OAuth provider profile do
    not satisfy this gate.
    """

    if not is_explicit_human_principal(context):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "step_up_required",
                "action": action,
                "reason": "verified_human_principal_required",
            },
        )
    assurance = context.get("identityAssurance")
    if not isinstance(assurance, dict) or assurance.get("mfaVerified") is not True:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "step_up_required",
                "action": action,
                "reason": "aal2_required",
            },
        )
    authenticated_at = assurance.get("authenticatedAt")
    if not isinstance(authenticated_at, str):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "step_up_required",
                "action": action,
                "reason": "authentication_time_missing",
            },
        )
    try:
        parsed = datetime.fromisoformat(authenticated_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("authentication time must be timezone-aware")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "step_up_required",
                "action": action,
                "reason": "authentication_time_invalid",
            },
        ) from exc
    configured_window = str(
        os.environ.get("REALYTICS_STEP_UP_MAX_AGE_SECONDS") or ""
    ).strip()
    if not configured_window:
        if security_default(local="configured", hardened="missing") == "missing":
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "step_up_policy_not_configured",
                    "action": action,
                },
            )
        max_age_seconds = 900
    else:
        try:
            max_age_seconds = int(configured_window)
        except ValueError as exc:
            raise HTTPException(
                status_code=503,
                detail={"error": "step_up_policy_invalid", "action": action},
            ) from exc
        if max_age_seconds <= 0:
            raise HTTPException(
                status_code=503,
                detail={"error": "step_up_policy_invalid", "action": action},
            )
    age_seconds = (datetime.now(timezone.utc) - parsed).total_seconds()
    if age_seconds < -60 or age_seconds > max_age_seconds:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "step_up_required",
                "action": action,
                "reason": "authentication_too_old",
                "maxAgeSeconds": max_age_seconds,
            },
        )
    return context


def high_risk_step_up_required() -> bool:
    raw = str(os.environ.get("REALYTICS_REQUIRE_HIGH_RISK_STEP_UP") or "").strip()
    if raw:
        return raw.casefold() in {"1", "true", "yes", "on"}
    return security_default(local="false", hardened="true") == "true"


def enforce_high_risk_step_up(
    context: Dict[str, Any],
    *,
    action: str,
) -> Dict[str, Any]:
    if high_risk_step_up_required():
        return require_step_up_authentication(context, action=action)
    return context


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


def identity_membership_repository() -> IdentityMembershipRepository:
    """Composition point kept injectable for deterministic authorization tests."""

    return PostgresIdentityMembershipRepository()


def _resolve_postgres_membership(
    request: Request,
    principal: IdpPrincipal,
    *,
    repository: IdentityMembershipRepository,
) -> tuple[IdpPrincipal, Dict[str, Any]]:
    requested_tenant = _requested_tenant_selector(request)
    try:
        directory = repository.load_directory(
            issuer=principal.issuer,
            subject=principal.subject,
        )
    except IdentityMembershipRepositoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_membership_authority_unavailable"},
        ) from exc

    directory_principal = directory.principal
    if directory_principal is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "principal_not_registered",
                "issuer": principal.issuer,
            },
        )
    if (
        canonicalize_issuer(directory_principal.issuer)
        != principal.issuer
        or directory_principal.subject.strip() != principal.subject
    ):
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_authority_integrity_error"},
        )
    principal_status = directory_principal.status.strip().casefold()
    if principal_status != "active":
        raise HTTPException(
            status_code=403,
            detail={
                "error": (
                    "principal_disabled"
                    if principal_status == "disabled"
                    else "principal_inactive"
                ),
                "principalStatus": principal_status,
            },
        )

    memberships_by_tenant = {
        membership.tenant_id: membership
        for membership in directory.memberships
    }
    active_memberships = [
        membership
        for membership in directory.memberships
        if membership.status.strip().casefold() == "active"
        and _tenant_status(membership.tenant_status) == "active"
    ]
    if requested_tenant:
        selected = memberships_by_tenant.get(requested_tenant)
        if selected is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "tenant_membership_not_found",
                    "requestedTenantId": requested_tenant,
                },
            )
        membership_status = selected.status.strip().casefold()
        if membership_status != "active":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "tenant_membership_inactive",
                    "requestedTenantId": requested_tenant,
                    "membershipStatus": membership_status,
                },
            )
        tenant_status = _tenant_status(selected.tenant_status)
        if tenant_status != "active":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "tenant_inactive",
                    "requestedTenantId": requested_tenant,
                    "tenantStatus": tenant_status,
                },
            )
    elif len(active_memberships) == 1:
        selected = active_memberships[0]
    elif not active_memberships:
        membership_active = [
            membership
            for membership in directory.memberships
            if membership.status.strip().casefold() == "active"
        ]
        if len(membership_active) == 1:
            selected = membership_active[0]
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "tenant_inactive",
                    "requestedTenantId": selected.tenant_id,
                    "tenantStatus": _tenant_status(selected.tenant_status),
                },
            )
        raise HTTPException(
            status_code=403,
            detail={"error": "no_active_tenant_membership"},
        )
    else:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "tenant_selection_required",
                "activeMembershipCount": len(active_memberships),
            },
        )

    role = selected.role.strip().casefold()
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "invalid_membership_role",
                "tenantId": selected.tenant_id,
            },
        )
    principal_type = directory_principal.principal_type.strip().casefold()
    if principal_type not in {"human", "service", "unknown"}:
        principal_type = "unknown"
    projection_user_id = (
        directory_principal.legacy_user_id
        or f"{directory_principal.issuer}::{directory_principal.subject}"
    )
    resolved = replace(
        principal,
        user_id=projection_user_id,
        tenant_id=selected.tenant_id,
        email=directory_principal.email or principal.email,
        display_name=(
            directory_principal.display_name or principal.display_name
        ),
        role=role,
        principal_type=principal_type,
    )
    return resolved, {
        "source": "normalized_postgres",
        "principalStatus": principal_status,
        "tenantStatus": _tenant_status(selected.tenant_status),
        "membershipStatus": selected.status.strip().casefold(),
        "membershipVersion": selected.membership_version,
    }


def _tenant_status(status: str | None) -> str:
    normalized = str(status or "").strip().casefold()
    return normalized or "unknown"


def _requested_tenant_selector(request: Request) -> str | None:
    selectors = [
        request.headers.get("x-realytics-tenant-id"),
        request.headers.get("x-tenant-id"),
        request.query_params.get("tenant_id"),
        request.query_params.get("tenantId"),
    ]
    normalized = {
        str(value).strip()
        for value in selectors
        if value is not None and str(value).strip()
    }
    if len(normalized) > 1:
        raise HTTPException(
            status_code=400,
            detail={"error": "tenant_selector_conflict"},
        )
    return next(iter(normalized), None)


def _identity_assurance(claims: Dict[str, Any]) -> Dict[str, Any]:
    raw_amr = claims.get("amr")
    amr_events: list[tuple[str, float | None]] = []
    if isinstance(raw_amr, str):
        amr = [raw_amr]
        amr_events.append((raw_amr, None))
    elif isinstance(raw_amr, list):
        amr = []
        for item in raw_amr:
            if isinstance(item, dict):
                method = item.get("method") or item.get("amr")
                if method:
                    normalized_method = str(method)
                    amr.append(normalized_method)
                    raw_timestamp = item.get("timestamp")
                    timestamp = (
                        float(raw_timestamp)
                        if isinstance(raw_timestamp, (int, float))
                        and not isinstance(raw_timestamp, bool)
                        and raw_timestamp > 0
                        else None
                    )
                    amr_events.append((normalized_method, timestamp))
            elif item:
                normalized_method = str(item)
                amr.append(normalized_method)
                amr_events.append((normalized_method, None))
    else:
        amr = []
    acr = str(claims.get("acr") or "") or None
    aal = str(claims.get("aal") or "").casefold() or None
    # Supabase records each authentication method with its own Unix timestamp.
    # For a high-risk action, use the actual strong-factor event when it is
    # present. Falling back to the generic ``auth_time`` remains conservative:
    # it can make a valid session look older, but can never extend MFA recency
    # merely because an access token was refreshed.
    strong_methods = {"mfa", "totp", "phone", "hwk", "swk", "fido", "webauthn"}
    strong_timestamps = [
        timestamp
        for method, timestamp in amr_events
        if method.casefold() in strong_methods and timestamp is not None
    ]
    raw_auth_time = claims.get("auth_time")
    authentication_timestamp: float | None = (
        max(strong_timestamps) if strong_timestamps else None
    )
    if (
        authentication_timestamp is None
        and isinstance(raw_auth_time, (int, float))
        and not isinstance(raw_auth_time, bool)
        and raw_auth_time > 0
    ):
        authentication_timestamp = float(raw_auth_time)
    authenticated_at = None
    if authentication_timestamp is not None:
        authenticated_at = datetime.fromtimestamp(
            authentication_timestamp,
            tz=timezone.utc,
        ).isoformat()
    return {
        "amr": amr,
        "acr": acr,
        "aal": aal,
        "authenticatedAt": authenticated_at,
        # A method label alone is not assurance. Only the signed Supabase AAL
        # claim can authorize the final approval/publication gate.
        "mfaVerified": aal == "aal2",
        "source": "verified_bearer_claims",
    }
