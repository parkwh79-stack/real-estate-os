from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from core.oauth_redirects import safe_relative_path, web_session_url

from core.authz import (
    authorize_request,
    identity_assurance_from_verified_claims,
    identity_context_from_request,
    permissions_for_role,
    require_step_up_authentication,
)
from core.onboarding_policy import (
    OnboardingPolicyVersionError,
    current_onboarding_policy_versions,
    require_current_onboarding_policy_versions,
)
from core.credential_session import credential_session_binding_from_request
from core.idp import idp_status, principal_from_request
from core.runtime_profile import (
    legacy_oauth_sessions_enabled,
    local_auth_bootstrap_enabled,
)
from core.secret_vault import (
    credential_access_status,
    ingest_user_credential,
    revoke_credential,
    revoke_session_credentials,
)
from db.repositories.identity_invitation_repository import (
    IdentityInvitationRepositoryUnavailable,
    InvitationLifecycleError,
    PostgresIdentityInvitationRepository,
)
from db.repositories.identity_membership_admin_repository import (
    IdentityMembershipAdministrationRepositoryUnavailable,
    MembershipRoleChangeError,
    PostgresIdentityMembershipAdministrationRepository,
)
from db.repositories.onboarding_repository import (
    OnboardingLifecycleError,
    OnboardingRepositoryUnavailable,
    PostgresOnboardingRepository,
)
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
from schemas.identity_access import (
    AcceptTenantInvitationInput,
    ChangeTenantMembershipRoleInput,
    IssueTenantInvitationInput,
    RevokeTenantInvitationInput,
    TenantInvitationView,
    TenantMembershipView,
)
from schemas.onboarding import (
    CreateTenantAccessRequestInput,
    DecideTenantAccessRequestInput,
    OnboardingProfileView,
    ResubmitTenantAccessRequestInput,
    SaveOnboardingProfileInput,
    TenantAccessRequestView,
)
from use_cases.user_ai_profile import build_user_ai_context, safe_user_ai_context_summary


router = APIRouter(prefix="/auth", tags=["auth"])

STEP_UP_INVITATION_ROLES = frozenset(
    {
        "admin",
        "legal_counsel_approver",
        "external_counsel",
        "compliance_officer",
    }
)


def identity_invitation_repository() -> PostgresIdentityInvitationRepository:
    """Composition point kept injectable for invitation contract tests."""

    return PostgresIdentityInvitationRepository()


def identity_membership_administration_repository(
) -> PostgresIdentityMembershipAdministrationRepository:
    """Injectable composition point for normalized membership administration."""

    return PostgresIdentityMembershipAdministrationRepository()


def onboarding_repository() -> PostgresOnboardingRepository:
    """Injectable composition point for normalized onboarding tests."""

    return PostgresOnboardingRepository()


def _verified_onboarding_principal(request: Request):
    """Require only a signed human principal, not an existing membership."""

    principal = principal_from_request(request)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "authentication_required"},
        )
    if principal.principal_type != "human":
        raise HTTPException(
            status_code=403,
            detail={"error": "verified_human_principal_required"},
        )
    if not principal.issuer or not principal.subject:
        raise HTTPException(
            status_code=403,
            detail={"error": "verified_principal_key_required"},
        )
    return principal


def _onboarding_request_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    return TenantAccessRequestView.model_validate(item).model_dump(mode="json")


def _raise_onboarding_lifecycle(exc: OnboardingLifecycleError) -> None:
    status_code = {
        "organization_not_found": 404,
        "tenant_access_request_not_found": 404,
        "organization_query_too_short": 400,
        "tenant_access_request_status_invalid": 400,
        "existing_organization_resubmission_shape_invalid": 400,
        "tenant_admin_authority_required": 403,
        "tenant_admin_selector_required": 409,
        "platform_operator_authority_required": 403,
        "onboarding_profile_version_conflict": 409,
        "onboarding_profile_required": 409,
        "onboarding_profile_snapshot_missing": 409,
        "onboarding_profile_snapshot_integrity_error": 409,
        "onboarding_policy_reacceptance_required": 409,
        "organization_name_conflict": 409,
        "organization_business_registration_conflict": 409,
        "tenant_membership_already_exists": 409,
        "tenant_access_request_pending": 409,
        "tenant_access_request_not_rejected": 409,
        "tenant_access_request_not_pending": 409,
        "tenant_access_request_version_conflict": 409,
        "tenant_access_request_self_approval_forbidden": 409,
        "new_organization_initial_admin_required": 409,
        "existing_organization_admin_assignment_forbidden": 409,
        "requester_principal_not_human": 409,
        "requester_principal_not_active": 409,
        "onboarding_approval_conflict": 409,
    }.get(exc.code, 400)
    raise HTTPException(
        status_code=status_code,
        detail={"error": exc.code},
    ) from exc


def _raise_onboarding_unavailable(
    exc: OnboardingRepositoryUnavailable,
    *,
    review: bool = False,
) -> None:
    raise HTTPException(
        status_code=503,
        detail={
            "error": (
                "onboarding_review_authority_unavailable"
                if review
                else "onboarding_authority_unavailable"
            )
        },
    ) from exc


def _normalized_membership_actor(
    context: Dict[str, Any],
) -> tuple[str, str, str]:
    """Return tenant and verified principal key, never OAuth profile metadata."""

    authority = context.get("membershipAuthority")
    user = context.get("user") or {}
    metadata = user.get("metadata") or {}
    tenant_id = str(user.get("tenantId") or "").strip()
    issuer = str(metadata.get("issuer") or "").strip().rstrip("/")
    subject = str(metadata.get("subject") or "").strip()
    if (
        not isinstance(authority, dict)
        or authority.get("source") != "normalized_postgres"
    ):
        raise HTTPException(
            status_code=503,
            detail={"error": "membership_authority_not_normalized"},
        )
    if not tenant_id or not issuer or not subject:
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_authority_context_incomplete"},
        )
    return tenant_id, issuer, subject


def _require_local_auth_bootstrap() -> None:
    if local_auth_bootstrap_enabled():
        return
    raise HTTPException(
        status_code=403,
        detail={
            "error": "local_auth_disabled",
            "message": (
                "Local tenant, user, and session bootstrap is disabled. "
                "Use a verified bearer token or an approved identity-provider flow."
            ),
        },
    )


@router.post("/tenants")
def create_or_update_tenant(payload: Dict[str, Any]):
    _require_local_auth_bootstrap()
    tenant = upsert_tenant(payload)
    return {
        "schemaVersion": "realytics-tenant-v1",
        "tenant": tenant,
    }


@router.post("/users")
def create_or_update_user(payload: Dict[str, Any]):
    _require_local_auth_bootstrap()
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
    _require_local_auth_bootstrap()
    return {
        "schemaVersion": "realytics-user-list-v1",
        "users": list_users(tenant_id=tenant_id, limit=limit),
    }


@router.post("/session")
def create_session(payload: Dict[str, Any]):
    _require_local_auth_bootstrap()
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


@router.get("/onboarding/status")
def get_onboarding_status(request: Request):
    principal = _verified_onboarding_principal(request)
    try:
        status = onboarding_repository().get_status(
            issuer=principal.issuer,
            subject=principal.subject,
        )
        terms_version, privacy_version = current_onboarding_policy_versions()
    except OnboardingLifecycleError as exc:
        _raise_onboarding_lifecycle(exc)
    except OnboardingRepositoryUnavailable as exc:
        _raise_onboarding_unavailable(exc)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "onboarding_policy_not_configured"},
        ) from exc
    profile = status.get("profile")
    requests = [
        _onboarding_request_payload(item)
        for item in status.get("accessRequests") or []
    ]
    memberships = status.get("memberships") or []
    active_member = any(
        item.get("status") == "active"
        and item.get("organizationStatus") == "active"
        for item in memberships
    )
    return {
        "schemaVersion": "realytics-onboarding-status-v1",
        "accessState": "member" if active_member else "onboarding_required",
        "profile": (
            OnboardingProfileView.model_validate(profile).model_dump(mode="json")
            if profile is not None
            else None
        ),
        # ``requests`` is the canonical web contract. ``accessRequests`` is
        # retained during the UI rollout and carries the identical projection.
        "requests": requests,
        "accessRequests": requests,
        "memberships": memberships,
        "platformOperator": bool(status.get("platformOperator")),
        "nextAction": status.get("nextAction"),
        "policyVersions": {
            "termsVersion": terms_version,
            "privacyVersion": privacy_version,
        },
    }


@router.put("/onboarding/profile")
def save_onboarding_profile(
    request: Request,
    payload: SaveOnboardingProfileInput,
):
    principal = _verified_onboarding_principal(request)
    try:
        require_current_onboarding_policy_versions(
            terms_version=payload.termsVersion,
            privacy_version=payload.privacyVersion,
        )
        profile = onboarding_repository().save_profile(
            issuer=principal.issuer,
            subject=principal.subject,
            contact_email=principal.email,
            payload=payload,
        )
    except OnboardingPolicyVersionError as exc:
        terms_version, privacy_version = current_onboarding_policy_versions()
        raise HTTPException(
            status_code=409,
            detail={
                "error": exc.code,
                "currentPolicyVersions": {
                    "termsVersion": terms_version,
                    "privacyVersion": privacy_version,
                },
            },
        ) from exc
    except OnboardingLifecycleError as exc:
        _raise_onboarding_lifecycle(exc)
    except OnboardingRepositoryUnavailable as exc:
        _raise_onboarding_unavailable(exc)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "onboarding_policy_not_configured"},
        ) from exc
    return {
        "schemaVersion": "realytics-onboarding-profile-v1",
        "profile": OnboardingProfileView.model_validate(profile).model_dump(
            mode="json"
        ),
    }


@router.get("/onboarding/organizations")
def search_onboarding_organizations(
    request: Request,
    query: str = Query(min_length=3, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
):
    principal = _verified_onboarding_principal(request)
    try:
        organizations = onboarding_repository().search_organizations(
            issuer=principal.issuer,
            subject=principal.subject,
            query=query,
            limit=limit,
        )
    except OnboardingLifecycleError as exc:
        _raise_onboarding_lifecycle(exc)
    except OnboardingRepositoryUnavailable as exc:
        _raise_onboarding_unavailable(exc)
    return {
        "schemaVersion": "realytics-onboarding-organization-list-v1",
        "organizations": organizations,
    }


@router.post("/onboarding/requests", status_code=201)
def submit_onboarding_access_request(
    request: Request,
    payload: CreateTenantAccessRequestInput,
):
    principal = _verified_onboarding_principal(request)
    try:
        created = onboarding_repository().create_request(
            issuer=principal.issuer,
            subject=principal.subject,
            payload=payload,
        )
    except OnboardingLifecycleError as exc:
        _raise_onboarding_lifecycle(exc)
    except OnboardingRepositoryUnavailable as exc:
        _raise_onboarding_unavailable(exc)
    return {
        "schemaVersion": "realytics-tenant-access-request-v1",
        "request": _onboarding_request_payload(created),
    }


@router.post("/onboarding/requests/{request_id}/resubmit")
def resubmit_onboarding_access_request(
    request_id: str,
    request: Request,
    payload: ResubmitTenantAccessRequestInput,
):
    principal = _verified_onboarding_principal(request)
    try:
        updated = onboarding_repository().resubmit_request(
            issuer=principal.issuer,
            subject=principal.subject,
            request_id=request_id,
            payload=payload,
        )
    except OnboardingLifecycleError as exc:
        _raise_onboarding_lifecycle(exc)
    except OnboardingRepositoryUnavailable as exc:
        _raise_onboarding_unavailable(exc)
    return {
        "schemaVersion": "realytics-tenant-access-request-v1",
        "request": _onboarding_request_payload(updated),
    }


@router.get("/access-requests")
def list_onboarding_access_requests(
    request: Request,
    status: str = Query(default="pending"),
    tenant_id: str | None = Query(default=None, alias="tenantId"),
    limit: int = Query(default=100, ge=1, le=500),
):
    principal = _verified_onboarding_principal(request)
    selected_tenant = str(
        tenant_id
        or request.headers.get("x-realytics-tenant-id")
        or request.headers.get("x-tenant-id")
        or ""
    ).strip() or None
    try:
        review_scope, requests = onboarding_repository().list_reviewable(
            actor_issuer=principal.issuer,
            actor_subject=principal.subject,
            tenant_id=selected_tenant,
            status=status,
            limit=limit,
        )
    except OnboardingLifecycleError as exc:
        _raise_onboarding_lifecycle(exc)
    except OnboardingRepositoryUnavailable as exc:
        _raise_onboarding_unavailable(exc, review=True)
    return {
        "schemaVersion": "realytics-tenant-access-request-review-list-v1",
        "reviewScope": review_scope,
        "requests": [
            _onboarding_request_payload(item) for item in requests
        ],
    }


@router.post("/access-requests/{request_id}/decision")
def decide_onboarding_access_request(
    request_id: str,
    request: Request,
    payload: DecideTenantAccessRequestInput,
):
    principal = _verified_onboarding_principal(request)
    require_step_up_authentication(
        {
            "authenticated": True,
            "user": {"metadata": {"principalType": "human"}},
            "identityAssurance": identity_assurance_from_verified_claims(
                principal.claims
            ),
        },
        action="tenant_access_request_decision",
    )
    try:
        decided = onboarding_repository().decide_request(
            actor_issuer=principal.issuer,
            actor_subject=principal.subject,
            request_id=request_id,
            payload=payload,
        )
    except OnboardingLifecycleError as exc:
        _raise_onboarding_lifecycle(exc)
    except OnboardingRepositoryUnavailable as exc:
        _raise_onboarding_unavailable(exc, review=True)
    return {
        "schemaVersion": "realytics-tenant-access-request-decision-v1",
        "request": _onboarding_request_payload(decided),
    }


@router.get("/memberships")
def list_tenant_memberships(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
):
    context = authorize_request(request, "user:manage")
    tenant_id, _actor_issuer, _actor_subject = _normalized_membership_actor(
        context
    )
    try:
        memberships = identity_membership_administration_repository().list_for_tenant(
            tenant_id=tenant_id,
            limit=limit,
        )
    except IdentityMembershipAdministrationRepositoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "identity_membership_administration_authority_unavailable"
            },
        ) from exc
    return {
        "schemaVersion": "realytics-tenant-membership-list-v1",
        "memberships": [
            TenantMembershipView.model_validate(item).model_dump(mode="json")
            for item in memberships
        ],
    }


@router.patch("/memberships/role")
def change_tenant_membership_role(
    request: Request,
    payload: ChangeTenantMembershipRoleInput,
):
    context = authorize_request(request, "user:manage")
    require_step_up_authentication(
        context,
        action="tenant_membership_role_change",
    )
    tenant_id, actor_issuer, actor_subject = _normalized_membership_actor(
        context
    )
    try:
        membership = identity_membership_administration_repository().change_role(
            tenant_id=tenant_id,
            actor_issuer=actor_issuer,
            actor_subject=actor_subject,
            payload=payload,
        )
    except MembershipRoleChangeError as exc:
        status_code = {
            "tenant_membership_not_found": 404,
            "tenant_membership_not_active": 409,
            "target_principal_not_active": 409,
            "target_principal_not_human": 409,
            "membership_version_conflict": 409,
            "membership_self_change_forbidden": 409,
            "membership_role_unchanged": 409,
            "last_tenant_admin_role_change_forbidden": 409,
        }.get(exc.code, 400)
        raise HTTPException(
            status_code=status_code,
            detail={"error": exc.code},
        ) from exc
    except IdentityMembershipAdministrationRepositoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "identity_membership_administration_authority_unavailable"
            },
        ) from exc
    return {
        "schemaVersion": "realytics-tenant-membership-role-change-v1",
        "membership": TenantMembershipView.model_validate(membership).model_dump(
            mode="json"
        ),
    }


@router.post("/invitations", status_code=201)
def issue_tenant_invitation(
    request: Request,
    payload: IssueTenantInvitationInput,
):
    context = authorize_request(request, "user:manage")
    user = context.get("user") or {}
    metadata = user.get("metadata") or {}
    tenant_id = str(user.get("tenantId") or "").strip()
    inviter_issuer = str(metadata.get("issuer") or "").strip()
    inviter_subject = str(metadata.get("subject") or "").strip()
    if not tenant_id or not inviter_issuer or not inviter_subject:
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_authority_context_incomplete"},
        )
    if (
        payload.role.value in STEP_UP_INVITATION_ROLES
        or (not payload.invitedEmail and not payload.invitedIssuer)
    ):
        require_step_up_authentication(
            context,
            action="tenant_invitation_issue_sensitive",
        )
    try:
        issued = identity_invitation_repository().issue(
            tenant_id=tenant_id,
            payload=payload,
            inviter_issuer=inviter_issuer,
            inviter_subject=inviter_subject,
        )
    except IdentityInvitationRepositoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_invitation_authority_unavailable"},
        ) from exc
    return {
        "schemaVersion": "realytics-tenant-invitation-issue-v1",
        "invitation": TenantInvitationView.model_validate(
            issued.invitation
        ).model_dump(mode="json"),
        "token": issued.token,
        "tokenPolicy": "shown_once_never_persisted_in_plaintext",
    }


@router.get("/invitations")
def list_tenant_invitations(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
):
    context = authorize_request(request, "user:manage")
    tenant_id = str((context.get("user") or {}).get("tenantId") or "").strip()
    if not tenant_id:
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_authority_context_incomplete"},
        )
    try:
        invitations = identity_invitation_repository().list_for_tenant(
            tenant_id=tenant_id,
            limit=limit,
        )
    except IdentityInvitationRepositoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_invitation_authority_unavailable"},
        ) from exc
    return {
        "schemaVersion": "realytics-tenant-invitation-list-v1",
        "invitations": [
            TenantInvitationView.model_validate(item).model_dump(mode="json")
            for item in invitations
        ],
    }


@router.post("/invitations/{invitation_id}/revoke")
def revoke_tenant_invitation(
    invitation_id: str,
    request: Request,
    payload: RevokeTenantInvitationInput,
):
    context = authorize_request(request, "user:manage")
    user = context.get("user") or {}
    metadata = user.get("metadata") or {}
    tenant_id = str(user.get("tenantId") or "").strip()
    try:
        result = identity_invitation_repository().revoke(
            tenant_id=tenant_id,
            invitation_id=invitation_id,
            actor_issuer=str(metadata.get("issuer") or ""),
            actor_subject=str(metadata.get("subject") or ""),
            reason=payload.reason,
        )
    except InvitationLifecycleError as exc:
        raise HTTPException(status_code=409, detail={"error": exc.code}) from exc
    except IdentityInvitationRepositoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_invitation_authority_unavailable"},
        ) from exc
    return {
        "schemaVersion": "realytics-tenant-invitation-revoke-v1",
        "invitation": result,
    }


@router.post("/invitations/accept")
def accept_tenant_invitation(
    request: Request,
    payload: AcceptTenantInvitationInput,
):
    # Invitation acceptance is still memberless, but it is not allowed to
    # bypass the same server-owned profile/consent gate as an access request.
    principal = _verified_onboarding_principal(request)
    try:
        terms_version, privacy_version = current_onboarding_policy_versions()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "onboarding_policy_not_configured"},
        ) from exc
    try:
        accepted = identity_invitation_repository().accept(
            token=payload.token,
            principal_issuer=principal.issuer,
            principal_subject=principal.subject,
            principal_email=principal.email,
            principal_display_name=principal.display_name,
            required_terms_version=terms_version,
            required_privacy_version=privacy_version,
        )
    except InvitationLifecycleError as exc:
        status_code = {
            "invitation_expired": 410,
            "invitation_identity_mismatch": 403,
            "invitation_email_mismatch": 403,
            "principal_inactive": 403,
            "invitation_not_pending": 409,
            "tenant_membership_already_exists": 409,
            "onboarding_profile_required": 409,
            "terms_version_mismatch": 409,
            "privacy_version_mismatch": 409,
        }.get(exc.code, 400)
        detail: dict[str, Any] = {"error": exc.code}
        if exc.code in {"terms_version_mismatch", "privacy_version_mismatch"}:
            detail["currentPolicyVersions"] = {
                "termsVersion": terms_version,
                "privacyVersion": privacy_version,
            }
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        ) from exc
    except IdentityInvitationRepositoryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "identity_invitation_authority_unavailable"},
        ) from exc
    return {
        "schemaVersion": "realytics-tenant-invitation-accept-v1",
        "invitation": accepted,
    }


@router.get("/ai-profile")
def get_ai_profile(request: Request):
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")
    user = context["user"]
    credential_session = credential_session_binding_from_request(request, context)
    profile = _cleanup_stale_ai_credential_profile(
        user=user,
        credential_session=credential_session,
    )
    ai_context = build_user_ai_context(
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
        session_id=(credential_session or {}).get("sessionId"),
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


def _cleanup_stale_ai_credential_profile(
    *,
    user: Dict[str, Any],
    credential_session: Dict[str, str] | None,
) -> Dict[str, Any] | None:
    current_profile = get_user_ai_profile(user["userId"], include_secret=True)
    if not current_profile or not current_profile.get("credentialRef"):
        return get_user_ai_profile(user["userId"], include_secret=False)
    if credential_session is None:
        return get_user_ai_profile(user["userId"], include_secret=False)
    access_status = credential_access_status(
        current_profile.get("credentialRef"),
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
        session_id=credential_session["sessionId"],
    )
    if access_status not in {"missing", "expired"}:
        return get_user_ai_profile(user["userId"], include_secret=False)
    profile = upsert_user_ai_profile(
        {
            "userId": user["userId"],
            "tenantId": user.get("tenantId"),
            "clearApiKey": True,
        }
    )
    record_user_activity_log(
        {
            "eventType": "ai_credential_stale_reference_cleared",
            "userId": user["userId"],
            "metadata": {
                "tenantId": user.get("tenantId"),
                "reason": access_status,
                "secretPersistedInProfile": False,
            },
        }
    )
    return profile


@router.post("/ai-credentials")
def ingest_ai_credential(request: Request, payload: Dict[str, Any]):
    """Accept a BYOK secret once and persist only its non-secret reference."""
    context = identity_context_from_request(request)
    if not context.get("authenticated"):
        raise HTTPException(status_code=401, detail="Login required")
    user = context["user"]
    credential_session = credential_session_binding_from_request(request, context)
    if credential_session is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "credential_session_binding_required",
                "message": (
                    "A verified session_id is required for an ephemeral AI credential."
                ),
            },
        )
    provider = str(payload.get("provider") or "gemini").strip().lower()
    api_key = payload.get("apiKey") or payload.get("api_key")
    previous = get_user_ai_profile(user["userId"], include_secret=True) or {}
    previous_ref = previous.get("credentialRef")
    try:
        credential = ingest_user_credential(
            user_id=user["userId"],
            tenant_id=user.get("tenantId"),
            session_id=credential_session["sessionId"],
            provider=provider,
            secret=str(api_key or ""),
            expires_on_logout=bool(payload.get("expiresOnLogout", True)),
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
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
                    "expiresAt": credential["expiresAt"],
                    "ingestPath": "/auth/ai-credentials",
                },
            },
        )
    except Exception:
        # Secret creation succeeded but profile persistence failed. Remove the
        # orphaned secret before propagating the storage failure.
        revoke_credential(
            credential["credentialRef"],
            user_id=user["userId"],
            tenant_id=user.get("tenantId"),
            session_id=credential_session["sessionId"],
        )
        raise
    if previous_ref and previous_ref != credential["credentialRef"]:
        # The reference came from this authenticated user's server-owned
        # profile. Unconditional cleanup also removes pre-envelope AWS secrets
        # that are intentionally no longer resolvable through the hardened
        # user/tenant/session boundary.
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
            "expiresAt": credential["expiresAt"],
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
    credential_session = credential_session_binding_from_request(request, context)
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
    ai_context = build_user_ai_context(
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
        session_id=(credential_session or {}).get("sessionId"),
        host="web",
    )
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
    ai_context = build_user_ai_context(
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
        host="web",
    )
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
    credential_session = credential_session_binding_from_request(request, context)
    if credential_session is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "credential_session_binding_required"},
        )
    current_profile = get_user_ai_profile(user["userId"], include_secret=True) or {}
    current_ref = current_profile.get("credentialRef")
    access_status = credential_access_status(
        current_ref,
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
        session_id=credential_session["sessionId"],
    )
    if access_status == "forbidden":
        raise HTTPException(
            status_code=409,
            detail={"error": "credential_session_mismatch"},
        )
    credential_revoked = revoke_credential(
        current_ref,
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
        session_id=credential_session["sessionId"],
    )
    if current_profile and access_status in {"active", "missing", "expired"}:
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
    profile_credential_revoked = revoke_credential(
        current_profile.get("credentialRef"),
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
        session_id=session_id,
    )
    if current_profile:
        upsert_user_ai_profile(
            {
                "userId": user["userId"],
                "tenantId": user.get("tenantId"),
                "clearApiKey": True,
            }
        )
    revoked_credentials = revoke_session_credentials(
        session_id,
        user_id=user["userId"],
        tenant_id=user.get("tenantId"),
    )
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
    if not legacy_oauth_sessions_enabled():
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
        session_url = web_session_url(web_base_url, session["sessionId"], redirect_path)
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
    return safe_relative_path(value, default="/dashboard")


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
