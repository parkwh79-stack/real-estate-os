"""Normalized PostgreSQL authority for verified-principal onboarding.

Self-service profile and access-request work uses the dedicated identity pool
because it is available before a tenant membership exists.  Review decisions
use the API pool and re-check normalized administrator/operator authority in
the same transaction that creates the principal, tenant, membership and audit
events.  No JWT role, provider metadata or browser-supplied tenant claim is an
authorization input.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.onboarding_policy import current_onboarding_policy_versions
from db.postgres_runtime_pool import (
    api_principal_transaction,
    identity_principal_transaction,
)
from db.postgres_runtime_privileges import validate_database_identifier
from schemas.onboarding import (
    CreateTenantAccessRequestInput,
    DecideTenantAccessRequestInput,
    OnboardingDecision,
    OnboardingRequestType,
    ResubmitTenantAccessRequestInput,
    SaveOnboardingProfileInput,
)


PrincipalTransactionFactory = Callable[
    [str, str], AbstractContextManager[Any]
]


class OnboardingRepositoryUnavailable(RuntimeError):
    """The normalized onboarding authority could not be reached safely."""


class OnboardingLifecycleError(ValueError):
    """Stable, non-secret-bearing onboarding workflow failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PostgresOnboardingRepository:
    """Verified-principal onboarding with CAS and append-only audit."""

    def __init__(
        self,
        *,
        self_transaction_factory: PrincipalTransactionFactory = (
            identity_principal_transaction
        ),
        review_transaction_factory: PrincipalTransactionFactory = (
            api_principal_transaction
        ),
        schema_name: str | None = None,
        now: Callable[[], datetime] | None = None,
        request_id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        tenant_id_factory: Callable[[], str] | None = None,
        policy_versions_provider: Callable[[], tuple[str, str]] = (
            current_onboarding_policy_versions
        ),
    ) -> None:
        if self_transaction_factory is None or review_transaction_factory is None:
            raise TypeError("both onboarding transaction factories are required")
        self._self_transaction = self_transaction_factory
        self._review_transaction = review_transaction_factory
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get("REALYTICS_DATABASE_SCHEMA")
            or "realytics",
            label="database schema",
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._request_id = request_id_factory or (
            lambda: f"access-request-{uuid4()}"
        )
        self._event_id = event_id_factory or (
            lambda: f"onboarding-event-{uuid4()}"
        )
        self._tenant_id = tenant_id_factory or (
            lambda: f"tenant-{uuid4()}"
        )
        self._policy_versions = policy_versions_provider

    def get_status(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> dict[str, Any]:
        issuer, subject = _principal_key(issuer, subject)
        try:
            with self._self_transaction(issuer, subject) as connection:
                with connection.cursor() as cursor:
                    profile = self._load_profile(cursor, issuer, subject)
                    cursor.execute(
                        self._request_select_sql(
                            "WHERE request.requester_issuer = %s "
                            "AND request.requester_subject = %s "
                            "ORDER BY request.submitted_at DESC, "
                            "request.request_id DESC"
                        ),
                        (issuer, subject),
                    )
                    requests = [
                        _request_view(row) for row in cursor.fetchall()
                    ]
                    cursor.execute(
                        f"""
                        SELECT membership.tenant_id,
                               tenant.name,
                               membership.role,
                               membership.status,
                               membership.membership_version,
                               tenant.status,
                               membership.updated_at
                          FROM "{self._schema}".tenant_memberships AS membership
                          JOIN "{self._schema}".tenants AS tenant
                            ON tenant.tenant_id = membership.tenant_id
                         WHERE membership.issuer = %s
                           AND membership.subject = %s
                         ORDER BY
                               CASE WHEN membership.status = 'active'
                                    THEN 0 ELSE 1 END,
                               tenant.name,
                               membership.tenant_id
                        """,
                        (issuer, subject),
                    )
                    memberships = [
                        {
                            "organizationId": str(row[0]),
                            "organizationName": str(row[1]),
                            "role": str(row[2]),
                            "status": str(row[3]),
                            "membershipVersion": int(row[4]),
                            "organizationStatus": str(row[5] or "inactive"),
                            "updatedAt": row[6],
                        }
                        for row in cursor.fetchall()
                    ]
                    cursor.execute(
                        f"""
                        SELECT status
                          FROM "{self._schema}".platform_onboarding_operators
                         WHERE issuer = %s AND subject = %s
                        """,
                        (issuer, subject),
                    )
                    operator_row = cursor.fetchone()
            next_action = _next_action(
                profile=profile,
                requests=requests,
                memberships=memberships,
            )
            return {
                "profile": profile,
                "accessRequests": requests,
                "memberships": memberships,
                "platformOperator": bool(
                    operator_row and str(operator_row[0]) == "active"
                ),
                "nextAction": next_action,
            }
        except OnboardingLifecycleError:
            raise
        except Exception as exc:
            raise OnboardingRepositoryUnavailable(
                "onboarding status authority is unavailable"
            ) from exc

    def save_profile(
        self,
        *,
        issuer: str,
        subject: str,
        contact_email: str | None,
        payload: SaveOnboardingProfileInput,
    ) -> dict[str, Any]:
        issuer, subject = _principal_key(issuer, subject)
        contact_email = _optional(contact_email)
        now = self._now()
        try:
            with self._self_transaction(issuer, subject) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_catalog.pg_advisory_xact_lock("
                        "pg_catalog.hashtextextended(%s, 0))",
                        (f"onboarding-profile\x1f{issuer}\x1f{subject}",),
                    )
                    cursor.execute(
                        f"""
                        SELECT profile_version
                          FROM "{self._schema}".onboarding_profiles
                         WHERE issuer = %s AND subject = %s
                         FOR UPDATE
                        """,
                        (issuer, subject),
                    )
                    current = cursor.fetchone()
                    if current is None:
                        if payload.expectedProfileVersion != 0:
                            raise OnboardingLifecycleError(
                                "onboarding_profile_version_conflict"
                            )
                        next_version = 1
                        cursor.execute(
                            f"""
                            INSERT INTO "{self._schema}".onboarding_profiles (
                                issuer, subject, contact_email, display_name,
                                phone, job_title, terms_version,
                                privacy_version, terms_accepted_at,
                                privacy_accepted_at, profile_version,
                                created_at, updated_at
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s
                            )
                            """,
                            (
                                issuer,
                                subject,
                                contact_email,
                                payload.displayName,
                                payload.phone,
                                payload.jobTitle,
                                payload.termsVersion,
                                payload.privacyVersion,
                                now,
                                now,
                                next_version,
                                now,
                                now,
                            ),
                        )
                    else:
                        current_version = int(current[0])
                        if current_version != payload.expectedProfileVersion:
                            raise OnboardingLifecycleError(
                                "onboarding_profile_version_conflict"
                            )
                        next_version = current_version + 1
                        cursor.execute(
                            f"""
                            UPDATE "{self._schema}".onboarding_profiles
                               SET contact_email = %s,
                                   display_name = %s,
                                   phone = %s,
                                   job_title = %s,
                                   terms_version = %s,
                                   privacy_version = %s,
                                   terms_accepted_at = %s,
                                   privacy_accepted_at = %s,
                                   profile_version = %s,
                                   updated_at = %s
                             WHERE issuer = %s AND subject = %s
                               AND profile_version = %s
                            """,
                            (
                                contact_email,
                                payload.displayName,
                                payload.phone,
                                payload.jobTitle,
                                payload.termsVersion,
                                payload.privacyVersion,
                                now,
                                now,
                                next_version,
                                now,
                                issuer,
                                subject,
                                current_version,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise OnboardingLifecycleError(
                                "onboarding_profile_version_conflict"
                            )
                    self._append_principal_event(
                        cursor,
                        issuer=issuer,
                        subject=subject,
                        event_type="onboarding_profile_saved",
                        occurred_at=now,
                        payload={
                            "profileVersion": next_version,
                            "termsVersion": payload.termsVersion,
                            "privacyVersion": payload.privacyVersion,
                        },
                    )
                    saved = self._load_profile(cursor, issuer, subject)
                    if saved is None:
                        raise RuntimeError("saved onboarding profile is missing")
                    return saved
        except OnboardingLifecycleError:
            raise
        except Exception as exc:
            raise OnboardingRepositoryUnavailable(
                "onboarding profile authority is unavailable"
            ) from exc

    def search_organizations(
        self,
        *,
        issuer: str,
        subject: str,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        issuer, subject = _principal_key(issuer, subject)
        query = _required(query, "query")
        if len(query) < 3:
            raise OnboardingLifecycleError("organization_query_too_short")
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        try:
            with self._self_transaction(issuer, subject) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT tenant_id, name
                          FROM "{self._schema}".tenants
                         WHERE COALESCE(status, 'active') = 'active'
                           AND tenant_id !~ '^personal-[0-9a-f]{{64}}$'
                           AND name ILIKE %s
                         ORDER BY name, tenant_id
                         LIMIT %s
                        """,
                        (f"%{query}%", limit),
                    )
                    return [
                        {
                            "organizationId": str(row[0]),
                            "name": str(row[1]),
                        }
                        for row in cursor.fetchall()
                    ]
        except OnboardingLifecycleError:
            raise
        except Exception as exc:
            raise OnboardingRepositoryUnavailable(
                "organization directory is unavailable"
            ) from exc

    def create_request(
        self,
        *,
        issuer: str,
        subject: str,
        payload: CreateTenantAccessRequestInput,
    ) -> dict[str, Any]:
        issuer, subject = _principal_key(issuer, subject)
        now = self._now()
        request_id = self._request_id()
        request_type = payload.requestType.value
        target_tenant_id = payload.organizationId
        organization_name = payload.organizationName
        if request_type == "existing_organization" and re.fullmatch(
            r"personal-[0-9a-f]{64}", str(target_tenant_id or "")
        ):
            raise OnboardingLifecycleError("organization_not_found")
        try:
            with self._self_transaction(issuer, subject) as connection:
                with connection.cursor() as cursor:
                    profile = self._load_profile(
                        cursor, issuer, subject, lock=True
                    )
                    if profile is None:
                        raise OnboardingLifecycleError(
                            "onboarding_profile_required"
                        )
                    self._require_current_policy_snapshot(
                        terms_version=profile["termsVersion"],
                        privacy_version=profile["privacyVersion"],
                    )
                    snapshot_hash = _profile_snapshot_hash(
                        issuer=issuer,
                        subject=subject,
                        profile=profile,
                    )
                    cursor.execute(
                        "SELECT pg_catalog.pg_advisory_xact_lock("
                        "pg_catalog.hashtextextended(%s, 0))",
                        (
                            "\x1f".join(
                                (
                                    "tenant-access-request",
                                    issuer,
                                    subject,
                                    str(target_tenant_id or organization_name),
                                )
                            ),
                        ),
                    )
                    if request_type == "existing_organization":
                        cursor.execute(
                            f"""
                            SELECT status
                              FROM "{self._schema}".tenants
                             WHERE tenant_id = %s
                            """,
                            (target_tenant_id,),
                        )
                        tenant = cursor.fetchone()
                        if tenant is None or str(tenant[0] or "active") != "active":
                            raise OnboardingLifecycleError(
                                "organization_not_found"
                            )
                        cursor.execute(
                            f"""
                            SELECT 1
                              FROM "{self._schema}".tenant_memberships
                             WHERE tenant_id = %s
                               AND issuer = %s AND subject = %s
                            """,
                            (target_tenant_id, issuer, subject),
                        )
                        if cursor.fetchone() is not None:
                            raise OnboardingLifecycleError(
                                "tenant_membership_already_exists"
                            )
                    else:
                        cursor.execute(
                            f"""
                            SELECT 1
                              FROM "{self._schema}".tenants
                             WHERE lower(btrim(name)) = lower(btrim(%s))
                            """,
                            (organization_name,),
                        )
                        if cursor.fetchone() is not None:
                            raise OnboardingLifecycleError(
                                "organization_name_conflict"
                            )
                        cursor.execute(
                            f"""
                            SELECT 1
                              FROM "{self._schema}".tenant_access_requests
                             WHERE request_type = 'new_organization'
                               AND business_registration_number = %s
                               AND status IN ('pending', 'approved')
                            """,
                            (payload.businessRegistrationNumber,),
                        )
                        if cursor.fetchone() is not None:
                            raise OnboardingLifecycleError(
                                "organization_business_registration_conflict"
                            )
                    cursor.execute(
                        f"""
                        SELECT 1
                          FROM "{self._schema}".tenant_access_requests
                         WHERE requester_issuer = %s
                           AND requester_subject = %s
                           AND status = 'pending'
                        """,
                        (issuer, subject),
                    )
                    if cursor.fetchone() is not None:
                        raise OnboardingLifecycleError(
                            "tenant_access_request_pending"
                        )
                    cursor.execute(
                        f"""
                        INSERT INTO "{self._schema}".tenant_access_requests (
                            request_id, request_type,
                            requester_issuer, requester_subject,
                            requester_email_snapshot,
                            requester_display_name_snapshot,
                            profile_version_snapshot,
                            terms_version_snapshot,
                            privacy_version_snapshot,
                            profile_snapshot_hash,
                            target_tenant_id, organization_name,
                            business_registration_number, requested_role,
                            request_message, status, request_version,
                            submitted_at, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            'pending', 1, %s, %s, %s
                        )
                        """,
                        (
                            request_id,
                            request_type,
                            issuer,
                            subject,
                            profile["contactEmail"],
                            profile["displayName"],
                            profile["profileVersion"],
                            profile["termsVersion"],
                            profile["privacyVersion"],
                            snapshot_hash,
                            target_tenant_id,
                            organization_name,
                            payload.businessRegistrationNumber,
                            payload.requestedRole.value,
                            payload.message,
                            now,
                            now,
                            now,
                        ),
                    )
                    self._append_request_event(
                        cursor,
                        request_id=request_id,
                        event_type="request_submitted",
                        request_type=request_type,
                        requester_issuer=issuer,
                        requester_subject=subject,
                        target_tenant_id=target_tenant_id,
                        actor_issuer=issuer,
                        actor_subject=subject,
                        occurred_at=now,
                        payload={
                            "requestVersion": 1,
                            "requestedRole": payload.requestedRole.value,
                            "profileVersion": profile["profileVersion"],
                            "profileSnapshotHash": snapshot_hash,
                            "termsVersion": profile["termsVersion"],
                            "privacyVersion": profile["privacyVersion"],
                        },
                    )
                    return self._load_request(cursor, request_id)
        except OnboardingLifecycleError:
            raise
        except Exception as exc:
            if _sqlstate(exc) == "23505":
                if _constraint_name(exc) == (
                    "uq_tenant_access_request_business_active"
                ):
                    raise OnboardingLifecycleError(
                        "organization_business_registration_conflict"
                    ) from exc
                raise OnboardingLifecycleError(
                    "tenant_access_request_pending"
                ) from exc
            raise OnboardingRepositoryUnavailable(
                "tenant access request authority is unavailable"
            ) from exc

    def resubmit_request(
        self,
        *,
        issuer: str,
        subject: str,
        request_id: str,
        payload: ResubmitTenantAccessRequestInput,
    ) -> dict[str, Any]:
        issuer, subject = _principal_key(issuer, subject)
        request_id = _required(request_id, "request_id")
        now = self._now()
        try:
            with self._self_transaction(issuer, subject) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._request_select_sql(
                            "WHERE request.request_id = %s "
                            "AND request.requester_issuer = %s "
                            "AND request.requester_subject = %s "
                            "FOR UPDATE OF request"
                        ),
                        (request_id, issuer, subject),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise OnboardingLifecycleError(
                            "tenant_access_request_not_found"
                        )
                    current = _request_view(row)
                    if current["status"] != "rejected":
                        raise OnboardingLifecycleError(
                            "tenant_access_request_not_rejected"
                        )
                    if current["requestVersion"] != payload.expectedRequestVersion:
                        raise OnboardingLifecycleError(
                            "tenant_access_request_version_conflict"
                        )
                    profile = self._load_profile(
                        cursor, issuer, subject, lock=True
                    )
                    if profile is None:
                        raise OnboardingLifecycleError(
                            "onboarding_profile_required"
                        )
                    self._require_current_policy_snapshot(
                        terms_version=profile["termsVersion"],
                        privacy_version=profile["privacyVersion"],
                    )
                    snapshot_hash = _profile_snapshot_hash(
                        issuer=issuer,
                        subject=subject,
                        profile=profile,
                    )
                    if (
                        current["requestType"] == "existing_organization"
                        and (
                            payload.organizationName is not None
                            or payload.businessRegistrationNumber is not None
                        )
                    ):
                        raise OnboardingLifecycleError(
                            "existing_organization_resubmission_shape_invalid"
                        )
                    organization_name = (
                        payload.organizationName
                        if payload.organizationName is not None
                        else current["organizationName"]
                    )
                    business_number = (
                        payload.businessRegistrationNumber
                        if payload.businessRegistrationNumber is not None
                        else current["businessRegistrationNumber"]
                    )
                    if current["requestType"] == "new_organization":
                        cursor.execute(
                            f"""
                            SELECT 1
                              FROM "{self._schema}".tenants
                             WHERE lower(btrim(name)) = lower(btrim(%s))
                            """,
                            (organization_name,),
                        )
                        if cursor.fetchone() is not None:
                            raise OnboardingLifecycleError(
                                "organization_name_conflict"
                            )
                        cursor.execute(
                            f"""
                            SELECT 1
                              FROM "{self._schema}".tenant_access_requests
                             WHERE request_id <> %s
                               AND request_type = 'new_organization'
                               AND business_registration_number = %s
                               AND status IN ('pending', 'approved')
                            """,
                            (request_id, business_number),
                        )
                        if cursor.fetchone() is not None:
                            raise OnboardingLifecycleError(
                                "organization_business_registration_conflict"
                            )
                    requested_role = (
                        payload.requestedRole.value
                        if payload.requestedRole is not None
                        else current["requestedRole"]
                    )
                    message = (
                        payload.message
                        if payload.message is not None
                        else current["message"]
                    )
                    next_version = current["requestVersion"] + 1
                    cursor.execute(
                        f"""
                        UPDATE "{self._schema}".tenant_access_requests
                           SET requester_email_snapshot = %s,
                               requester_display_name_snapshot = %s,
                               profile_version_snapshot = %s,
                               terms_version_snapshot = %s,
                               privacy_version_snapshot = %s,
                               profile_snapshot_hash = %s,
                               organization_name = %s,
                               business_registration_number = %s,
                               requested_role = %s,
                               request_message = %s,
                               status = 'pending',
                               request_version = %s,
                               submitted_at = %s,
                               decision_by_issuer = NULL,
                               decision_by_subject = NULL,
                               decision_at = NULL,
                               decision_reason = NULL,
                               approved_tenant_id = NULL,
                               updated_at = %s
                         WHERE request_id = %s
                           AND requester_issuer = %s
                           AND requester_subject = %s
                           AND status = 'rejected'
                           AND request_version = %s
                        """,
                        (
                            profile["contactEmail"],
                            profile["displayName"],
                            profile["profileVersion"],
                            profile["termsVersion"],
                            profile["privacyVersion"],
                            snapshot_hash,
                            organization_name,
                            business_number,
                            requested_role,
                            message,
                            next_version,
                            now,
                            now,
                            request_id,
                            issuer,
                            subject,
                            current["requestVersion"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise OnboardingLifecycleError(
                            "tenant_access_request_version_conflict"
                        )
                    self._append_request_event(
                        cursor,
                        request_id=request_id,
                        event_type="request_resubmitted",
                        request_type=current["requestType"],
                        requester_issuer=issuer,
                        requester_subject=subject,
                        target_tenant_id=current["organizationId"],
                        actor_issuer=issuer,
                        actor_subject=subject,
                        occurred_at=now,
                        payload={
                            "previousRequestVersion": current["requestVersion"],
                            "requestVersion": next_version,
                            "requestedRole": requested_role,
                            "profileVersion": profile["profileVersion"],
                            "profileSnapshotHash": snapshot_hash,
                            "termsVersion": profile["termsVersion"],
                            "privacyVersion": profile["privacyVersion"],
                        },
                    )
                    return self._load_request(cursor, request_id)
        except OnboardingLifecycleError:
            raise
        except Exception as exc:
            if _sqlstate(exc) == "23505":
                if _constraint_name(exc) == (
                    "uq_tenant_access_request_business_active"
                ):
                    raise OnboardingLifecycleError(
                        "organization_business_registration_conflict"
                    ) from exc
                raise OnboardingLifecycleError(
                    "tenant_access_request_pending"
                ) from exc
            raise OnboardingRepositoryUnavailable(
                "tenant access request authority is unavailable"
            ) from exc

    def list_reviewable(
        self,
        *,
        actor_issuer: str,
        actor_subject: str,
        tenant_id: str | None,
        status: str = "pending",
        limit: int = 100,
    ) -> tuple[str, list[dict[str, Any]]]:
        actor_issuer, actor_subject = _principal_key(
            actor_issuer, actor_subject
        )
        status = str(status or "pending").strip().casefold()
        if status not in {"all", "pending", "approved", "rejected"}:
            raise OnboardingLifecycleError(
                "tenant_access_request_status_invalid"
            )
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        tenant_id = _optional(tenant_id)
        try:
            with self._review_transaction(
                actor_issuer, actor_subject
            ) as connection:
                with connection.cursor() as cursor:
                    scope: str
                    if tenant_id:
                        self._require_tenant_admin(
                            cursor,
                            tenant_id=tenant_id,
                            actor_issuer=actor_issuer,
                            actor_subject=actor_subject,
                        )
                        scope = "tenant"
                    else:
                        cursor.execute(
                            f"""
                            SELECT 1
                              FROM "{self._schema}".platform_onboarding_operators
                             WHERE issuer = %s AND subject = %s
                               AND status = 'active'
                            """,
                            (actor_issuer, actor_subject),
                        )
                        if cursor.fetchone() is not None:
                            scope = "platform"
                        else:
                            cursor.execute(
                                f"""
                                SELECT tenant_id
                                  FROM "{self._schema}".tenant_memberships
                                 WHERE issuer = %s AND subject = %s
                                   AND role = 'admin' AND status = 'active'
                                 ORDER BY tenant_id
                                 LIMIT 2
                                """,
                                (actor_issuer, actor_subject),
                            )
                            admin_tenants = [
                                str(row[0]) for row in cursor.fetchall()
                            ]
                            if len(admin_tenants) > 1:
                                raise OnboardingLifecycleError(
                                    "tenant_admin_selector_required"
                                )
                            if not admin_tenants:
                                raise OnboardingLifecycleError(
                                    "platform_operator_authority_required"
                                )
                            tenant_id = admin_tenants[0]
                            scope = "tenant"
                    if scope == "tenant":
                        predicate = (
                            "request.request_type = 'existing_organization' "
                            "AND request.target_tenant_id = %s"
                        )
                        params_list: list[Any] = [tenant_id]
                    else:
                        predicate = "request.request_type = 'new_organization'"
                        params_list = []
                    if status != "all":
                        predicate += " AND request.status = %s"
                        params_list.append(status)
                    params_list.append(limit)
                    cursor.execute(
                        self._request_select_sql(
                            f"WHERE {predicate} "
                            "ORDER BY request.submitted_at, request.request_id "
                            "LIMIT %s"
                        ),
                        tuple(params_list),
                    )
                    return scope, [
                        _request_view(row) for row in cursor.fetchall()
                    ]
        except OnboardingLifecycleError:
            raise
        except Exception as exc:
            raise OnboardingRepositoryUnavailable(
                "tenant access review authority is unavailable"
            ) from exc

    def decide_request(
        self,
        *,
        actor_issuer: str,
        actor_subject: str,
        request_id: str,
        payload: DecideTenantAccessRequestInput,
    ) -> dict[str, Any]:
        actor_issuer, actor_subject = _principal_key(
            actor_issuer, actor_subject
        )
        request_id = _required(request_id, "request_id")
        now = self._now()
        try:
            with self._review_transaction(
                actor_issuer, actor_subject
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_catalog.pg_advisory_xact_lock("
                        "pg_catalog.hashtextextended(%s, 0))",
                        (f"tenant-access-decision\x1f{request_id}",),
                    )
                    cursor.execute(
                        self._request_select_sql(
                            "WHERE request.request_id = %s "
                            "FOR UPDATE OF request"
                        ),
                        (request_id,),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise OnboardingLifecycleError(
                            "tenant_access_request_not_found"
                        )
                    current = _request_view(row)
                    if (
                        current["requesterIssuer"] == actor_issuer
                        and current["requesterSubject"] == actor_subject
                    ):
                        raise OnboardingLifecycleError(
                            "tenant_access_request_self_approval_forbidden"
                        )
                    if current["status"] != "pending":
                        raise OnboardingLifecycleError(
                            "tenant_access_request_not_pending"
                        )
                    if current["requestVersion"] != payload.expectedRequestVersion:
                        raise OnboardingLifecycleError(
                            "tenant_access_request_version_conflict"
                        )
                    if current["requestType"] == "existing_organization":
                        approved_tenant_id = _required(
                            current["organizationId"], "organization_id"
                        )
                        self._require_tenant_admin(
                            cursor,
                            tenant_id=approved_tenant_id,
                            actor_issuer=actor_issuer,
                            actor_subject=actor_subject,
                        )
                    else:
                        approved_tenant_id = None
                        self._require_platform_operator(
                            cursor,
                            actor_issuer=actor_issuer,
                            actor_subject=actor_subject,
                        )

                    next_version = current["requestVersion"] + 1
                    if payload.decision == OnboardingDecision.REJECT:
                        self._update_decision(
                            cursor,
                            request_id=request_id,
                            current_version=current["requestVersion"],
                            next_version=next_version,
                            status="rejected",
                            actor_issuer=actor_issuer,
                            actor_subject=actor_subject,
                            reason=payload.reason,
                            approved_tenant_id=None,
                            now=now,
                        )
                        self._append_request_event(
                            cursor,
                            request_id=request_id,
                            event_type="request_rejected",
                            request_type=current["requestType"],
                            requester_issuer=current["requesterIssuer"],
                            requester_subject=current["requesterSubject"],
                            target_tenant_id=current["organizationId"],
                            actor_issuer=actor_issuer,
                            actor_subject=actor_subject,
                            occurred_at=now,
                            payload={
                                "previousRequestVersion": current[
                                    "requestVersion"
                                ],
                                "requestVersion": next_version,
                                "reason": payload.reason,
                            },
                        )
                        return self._load_request(cursor, request_id)

                    assigned_role = (
                        payload.assignedRole.value
                        if payload.assignedRole is not None
                        else current["requestedRole"]
                    )
                    if current["requestType"] == "new_organization":
                        if assigned_role != "admin":
                            raise OnboardingLifecycleError(
                                "new_organization_initial_admin_required"
                            )
                        assigned_role = "admin"
                    elif assigned_role == "admin":
                        raise OnboardingLifecycleError(
                            "existing_organization_admin_assignment_forbidden"
                        )
                    self._require_current_policy_snapshot(
                        terms_version=current["termsVersionSnapshot"],
                        privacy_version=current["privacyVersionSnapshot"],
                    )
                    expected_snapshot_hash = _profile_snapshot_hash(
                        issuer=current["requesterIssuer"],
                        subject=current["requesterSubject"],
                        profile={
                            "contactEmail": current["requesterEmail"],
                            "displayName": current["requesterDisplayName"],
                            "profileVersion": current[
                                "profileVersionSnapshot"
                            ],
                            "termsVersion": current["termsVersionSnapshot"],
                            "privacyVersion": current[
                                "privacyVersionSnapshot"
                            ],
                        },
                    )
                    if not hmac.compare_digest(
                        expected_snapshot_hash,
                        current["profileSnapshotHash"],
                    ):
                        raise OnboardingLifecycleError(
                            "onboarding_profile_snapshot_integrity_error"
                        )
                    # Review never broad-reads the applicant's self-scoped
                    # profile. Submission sealed these display/contact fields
                    # into the CAS-governed request row under the applicant's
                    # verified principal context.
                    profile = {
                        "contactEmail": current["requesterEmail"],
                        "displayName": current["requesterDisplayName"],
                    }
                    if not profile["displayName"]:
                        raise OnboardingLifecycleError(
                            "onboarding_profile_snapshot_missing"
                        )
                    if current["requestType"] == "new_organization":
                        approved_tenant_id = self._create_tenant(
                            cursor,
                            request=current,
                            now=now,
                        )
                        tenant_created = True
                    else:
                        self._require_active_tenant(cursor, approved_tenant_id)
                        tenant_created = False

                    principal_created = self._ensure_principal(
                        cursor,
                        request=current,
                        profile=profile,
                        now=now,
                    )
                    cursor.execute(
                        f"""
                        SELECT status
                          FROM "{self._schema}".tenant_memberships
                         WHERE tenant_id = %s
                           AND issuer = %s AND subject = %s
                         FOR UPDATE
                        """,
                        (
                            approved_tenant_id,
                            current["requesterIssuer"],
                            current["requesterSubject"],
                        ),
                    )
                    if cursor.fetchone() is not None:
                        raise OnboardingLifecycleError(
                            "tenant_membership_already_exists"
                        )
                    cursor.execute(
                        f"""
                        INSERT INTO "{self._schema}".tenant_memberships (
                            tenant_id, issuer, subject, role, status,
                            membership_version, membership_source,
                            created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, 'active', 1, %s, %s, %s
                        )
                        """,
                        (
                            approved_tenant_id,
                            current["requesterIssuer"],
                            current["requesterSubject"],
                            assigned_role,
                            f"access_request:{request_id}",
                            now,
                            now,
                        ),
                    )
                    self._update_decision(
                        cursor,
                        request_id=request_id,
                        current_version=current["requestVersion"],
                        next_version=next_version,
                        status="approved",
                        actor_issuer=actor_issuer,
                        actor_subject=actor_subject,
                        reason=payload.reason,
                        approved_tenant_id=approved_tenant_id,
                        now=now,
                    )
                    if tenant_created:
                        self._append_request_event(
                            cursor,
                            request_id=request_id,
                            event_type="tenant_created",
                            request_type=current["requestType"],
                            requester_issuer=current["requesterIssuer"],
                            requester_subject=current["requesterSubject"],
                            target_tenant_id=approved_tenant_id,
                            actor_issuer=actor_issuer,
                            actor_subject=actor_subject,
                            occurred_at=now,
                            payload={"organizationId": approved_tenant_id},
                        )
                    if principal_created:
                        self._append_request_event(
                            cursor,
                            request_id=request_id,
                            event_type="principal_registered",
                            request_type=current["requestType"],
                            requester_issuer=current["requesterIssuer"],
                            requester_subject=current["requesterSubject"],
                            target_tenant_id=approved_tenant_id,
                            actor_issuer=actor_issuer,
                            actor_subject=actor_subject,
                            occurred_at=now,
                            payload={"principalType": "human"},
                        )
                    self._append_request_event(
                        cursor,
                        request_id=request_id,
                        event_type="membership_created",
                        request_type=current["requestType"],
                        requester_issuer=current["requesterIssuer"],
                        requester_subject=current["requesterSubject"],
                        target_tenant_id=approved_tenant_id,
                        actor_issuer=actor_issuer,
                        actor_subject=actor_subject,
                        occurred_at=now,
                        payload={
                            "organizationId": approved_tenant_id,
                            "role": assigned_role,
                            "membershipVersion": 1,
                        },
                    )
                    self._append_request_event(
                        cursor,
                        request_id=request_id,
                        event_type="request_approved",
                        request_type=current["requestType"],
                        requester_issuer=current["requesterIssuer"],
                        requester_subject=current["requesterSubject"],
                        target_tenant_id=approved_tenant_id,
                        actor_issuer=actor_issuer,
                        actor_subject=actor_subject,
                        occurred_at=now,
                        payload={
                            "previousRequestVersion": current[
                                "requestVersion"
                            ],
                            "requestVersion": next_version,
                            "organizationId": approved_tenant_id,
                            "assignedRole": assigned_role,
                            "reason": payload.reason,
                        },
                    )
                    return self._load_request(cursor, request_id)
        except OnboardingLifecycleError:
            raise
        except Exception as exc:
            if _sqlstate(exc) == "23505":
                raise OnboardingLifecycleError(
                    "onboarding_approval_conflict"
                ) from exc
            raise OnboardingRepositoryUnavailable(
                "tenant access decision authority is unavailable"
            ) from exc

    def _load_profile(
        self,
        cursor: Any,
        issuer: str,
        subject: str,
        *,
        lock: bool = False,
    ) -> dict[str, Any] | None:
        cursor.execute(
            f"""
            SELECT issuer, subject, contact_email, display_name,
                   phone, job_title, terms_version, privacy_version,
                   terms_accepted_at, privacy_accepted_at,
                   profile_version, updated_at
              FROM "{self._schema}".onboarding_profiles
             WHERE issuer = %s AND subject = %s
             {"FOR SHARE" if lock else ""}
            """,
            (issuer, subject),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "issuer": str(row[0]),
            "subject": str(row[1]),
            "contactEmail": _optional(row[2]),
            "displayName": str(row[3]),
            "phone": _optional(row[4]),
            "jobTitle": _optional(row[5]),
            "termsVersion": str(row[6]),
            "privacyVersion": str(row[7]),
            "termsAcceptedAt": row[8],
            "privacyAcceptedAt": row[9],
            "profileVersion": int(row[10]),
            "updatedAt": row[11],
        }

    def _load_request(self, cursor: Any, request_id: str) -> dict[str, Any]:
        cursor.execute(
            self._request_select_sql("WHERE request.request_id = %s"),
            (request_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("saved tenant access request is missing")
        return _request_view(row)

    def _request_select_sql(self, suffix: str) -> str:
        return f"""
            SELECT request.request_id,
                   request.request_type,
                   request.requester_issuer,
                   request.requester_subject,
                   request.requester_display_name_snapshot,
                   request.requester_email_snapshot,
                   request.profile_version_snapshot,
                   request.terms_version_snapshot,
                   request.privacy_version_snapshot,
                   request.profile_snapshot_hash,
                   request.target_tenant_id,
                   COALESCE(
                       request.organization_name,
                       target_tenant.name,
                       approved_tenant.name
                   ),
                   request.business_registration_number,
                   request.requested_role,
                   request.request_message,
                   request.status,
                   request.request_version,
                   request.submitted_at,
                   request.decision_at,
                   request.decision_reason,
                   request.approved_tenant_id,
                   request.created_at,
                   request.updated_at
              FROM "{self._schema}".tenant_access_requests AS request
              LEFT JOIN "{self._schema}".tenants AS target_tenant
                ON target_tenant.tenant_id = request.target_tenant_id
              LEFT JOIN "{self._schema}".tenants AS approved_tenant
                ON approved_tenant.tenant_id = request.approved_tenant_id
              {suffix}
        """

    def _require_tenant_admin(
        self,
        cursor: Any,
        *,
        tenant_id: str,
        actor_issuer: str,
        actor_subject: str,
    ) -> None:
        cursor.execute(
            f"""
            SELECT 1
              FROM "{self._schema}".tenant_memberships
             WHERE tenant_id = %s
               AND issuer = %s AND subject = %s
               AND role = 'admin' AND status = 'active'
            """,
            (tenant_id, actor_issuer, actor_subject),
        )
        if cursor.fetchone() is None:
            raise OnboardingLifecycleError(
                "tenant_admin_authority_required"
            )

    def _require_platform_operator(
        self,
        cursor: Any,
        *,
        actor_issuer: str,
        actor_subject: str,
    ) -> None:
        cursor.execute(
            f"""
            SELECT 1
              FROM "{self._schema}".platform_onboarding_operators
             WHERE issuer = %s AND subject = %s AND status = 'active'
            """,
            (actor_issuer, actor_subject),
        )
        if cursor.fetchone() is None:
            raise OnboardingLifecycleError(
                "platform_operator_authority_required"
            )

    def _require_active_tenant(self, cursor: Any, tenant_id: str) -> None:
        cursor.execute(
            f"""
            SELECT status
              FROM "{self._schema}".tenants
             WHERE tenant_id = %s
            """,
            (tenant_id,),
        )
        row = cursor.fetchone()
        if row is None or str(row[0] or "active") != "active":
            raise OnboardingLifecycleError("organization_not_found")

    def _require_current_policy_snapshot(
        self,
        *,
        terms_version: str,
        privacy_version: str,
    ) -> None:
        current_terms, current_privacy = self._policy_versions()
        if (
            terms_version != current_terms
            or privacy_version != current_privacy
        ):
            raise OnboardingLifecycleError(
                "onboarding_policy_reacceptance_required"
            )

    def _create_tenant(
        self,
        cursor: Any,
        *,
        request: dict[str, Any],
        now: datetime,
    ) -> str:
        organization_name = _required(
            request["organizationName"], "organization_name"
        )
        cursor.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended(%s, 0))",
            (f"tenant-name\x1f{organization_name.casefold()}",),
        )
        cursor.execute(
            f"""
            SELECT tenant_id
              FROM "{self._schema}".tenants
             WHERE lower(btrim(name)) = lower(btrim(%s))
            """,
            (organization_name,),
        )
        if cursor.fetchone() is not None:
            raise OnboardingLifecycleError("organization_name_conflict")
        tenant_id = _required(self._tenant_id(), "generated tenant_id")
        cursor.execute(
            f"""
            INSERT INTO "{self._schema}".tenants (
                tenant_id, name, status, created_at, updated_at, metadata_json
            ) VALUES (%s, %s, 'active', %s, %s, %s::jsonb)
            """,
            (
                tenant_id,
                organization_name,
                now,
                now,
                json.dumps(
                    {
                        "authority": "tenant_access_request",
                        "requestId": request["requestId"],
                        "businessRegistrationNumber": request[
                            "businessRegistrationNumber"
                        ],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        return tenant_id

    def _ensure_principal(
        self,
        cursor: Any,
        *,
        request: dict[str, Any],
        profile: dict[str, Any],
        now: datetime,
    ) -> bool:
        cursor.execute(
            f"""
            SELECT principal_type, status
              FROM "{self._schema}".principals
             WHERE issuer = %s AND subject = %s
             FOR UPDATE
            """,
            (request["requesterIssuer"], request["requesterSubject"]),
        )
        existing = cursor.fetchone()
        if existing is not None:
            if str(existing[0]) != "human":
                raise OnboardingLifecycleError(
                    "requester_principal_not_human"
                )
            if str(existing[1]) != "active":
                raise OnboardingLifecycleError(
                    "requester_principal_not_active"
                )
            cursor.execute(
                f"""
                UPDATE "{self._schema}".principals
                   SET email = %s, display_name = %s, updated_at = %s
                 WHERE issuer = %s AND subject = %s
                """,
                (
                    profile["contactEmail"],
                    profile["displayName"],
                    now,
                    request["requesterIssuer"],
                    request["requesterSubject"],
                ),
            )
            return False
        cursor.execute(
            f"""
            INSERT INTO "{self._schema}".principals (
                issuer, subject, principal_type, status,
                email, display_name, verification_method, first_verified_at,
                last_verified_at, created_at, updated_at
            ) VALUES (
                %s, %s, 'human', 'active', %s, %s,
                'approved_onboarding_request', %s, %s, %s, %s
            )
            """,
            (
                request["requesterIssuer"],
                request["requesterSubject"],
                profile["contactEmail"],
                profile["displayName"],
                request["submittedAt"],
                request["submittedAt"],
                now,
                now,
            ),
        )
        return True

    def _update_decision(
        self,
        cursor: Any,
        *,
        request_id: str,
        current_version: int,
        next_version: int,
        status: str,
        actor_issuer: str,
        actor_subject: str,
        reason: str,
        approved_tenant_id: str | None,
        now: datetime,
    ) -> None:
        cursor.execute(
            f"""
            UPDATE "{self._schema}".tenant_access_requests
               SET status = %s,
                   request_version = %s,
                   decision_by_issuer = %s,
                   decision_by_subject = %s,
                   decision_at = %s,
                   decision_reason = %s,
                   approved_tenant_id = %s,
                   updated_at = %s
             WHERE request_id = %s
               AND status = 'pending'
               AND request_version = %s
            """,
            (
                status,
                next_version,
                actor_issuer,
                actor_subject,
                now,
                reason,
                approved_tenant_id,
                now,
                request_id,
                current_version,
            ),
        )
        if cursor.rowcount != 1:
            raise OnboardingLifecycleError(
                "tenant_access_request_version_conflict"
            )

    def _append_principal_event(
        self,
        cursor: Any,
        *,
        issuer: str,
        subject: str,
        event_type: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> None:
        cursor.execute(
            f"""
            INSERT INTO "{self._schema}".onboarding_principal_events (
                event_id, issuer, subject, event_type,
                event_payload, occurred_at
            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (
                self._event_id(),
                issuer,
                subject,
                event_type,
                _json(payload),
                occurred_at,
            ),
        )

    def _append_request_event(
        self,
        cursor: Any,
        *,
        request_id: str,
        event_type: str,
        request_type: str,
        requester_issuer: str,
        requester_subject: str,
        target_tenant_id: str | None,
        actor_issuer: str,
        actor_subject: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> None:
        cursor.execute(
            f"""
            INSERT INTO "{self._schema}".tenant_access_request_events (
                event_id, request_id, event_type, request_type,
                requester_issuer, requester_subject, target_tenant_id,
                actor_issuer, actor_subject, event_payload, occurred_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
            )
            """,
            (
                self._event_id(),
                request_id,
                event_type,
                request_type,
                requester_issuer,
                requester_subject,
                target_tenant_id,
                actor_issuer,
                actor_subject,
                _json(payload),
                occurred_at,
            ),
        )


def _request_view(row: Any) -> dict[str, Any]:
    return {
        "requestId": str(row[0]),
        "requestType": str(row[1]),
        "requesterIssuer": str(row[2]),
        "requesterSubject": str(row[3]),
        "requesterDisplayName": _optional(row[4]),
        "requesterEmail": _optional(row[5]),
        "profileVersionSnapshot": int(row[6]),
        "termsVersionSnapshot": str(row[7]),
        "privacyVersionSnapshot": str(row[8]),
        "profileSnapshotHash": str(row[9]),
        "organizationId": _optional(row[10]),
        "organizationName": _optional(row[11]),
        "businessRegistrationNumber": _optional(row[12]),
        "requestedRole": str(row[13]),
        "message": _optional(row[14]),
        "status": str(row[15]),
        "requestVersion": int(row[16]),
        "submittedAt": row[17],
        # Decision-maker principal keys remain only in PostgreSQL audit
        # lineage and are never selected into applicant/reviewer projections.
        "decisionAt": row[18],
        "decisionReason": _optional(row[19]),
        "approvedOrganizationId": _optional(row[20]),
        "createdAt": row[21],
        "updatedAt": row[22],
    }


def _next_action(
    *,
    profile: dict[str, Any] | None,
    requests: list[dict[str, Any]],
    memberships: list[dict[str, Any]],
) -> str:
    if any(
        item["status"] == "active"
        and item["organizationStatus"] == "active"
        for item in memberships
    ):
        return "workspace"
    if profile is None:
        return "complete_profile"
    if any(item["status"] == "pending" for item in requests):
        return "await_review"
    if any(item["status"] == "rejected" for item in requests):
        return "resubmit_request"
    return "select_organization"


def _principal_key(issuer: str, subject: str) -> tuple[str, str]:
    return (
        _required(issuer, "issuer").rstrip("/"),
        _required(subject, "subject"),
    )


def _required(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _optional(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _profile_snapshot_hash(
    *,
    issuer: str,
    subject: str,
    profile: dict[str, Any],
) -> str:
    sealed = {
        "contactEmail": _optional(profile.get("contactEmail")),
        "displayName": _required(
            profile.get("displayName"), "profile display_name"
        ),
        "issuer": _required(issuer, "issuer").rstrip("/"),
        "privacyVersion": _required(
            profile.get("privacyVersion"), "privacy_version"
        ),
        "profileVersion": int(profile.get("profileVersion") or 0),
        "subject": _required(subject, "subject"),
        "termsVersion": _required(
            profile.get("termsVersion"), "terms_version"
        ),
    }
    if sealed["profileVersion"] <= 0:
        raise ValueError("profile_version must be positive")
    return hashlib.sha256(_json(sealed).encode("utf-8")).hexdigest()


def _sqlstate(exc: Exception) -> str | None:
    state = getattr(exc, "sqlstate", None)
    if state:
        return str(state)
    cause = getattr(exc, "__cause__", None)
    return str(getattr(cause, "sqlstate", "") or "") or None


def _constraint_name(exc: Exception) -> str | None:
    diagnostic = getattr(exc, "diag", None)
    name = getattr(diagnostic, "constraint_name", None)
    if name:
        return str(name)
    cause = getattr(exc, "__cause__", None)
    cause_diagnostic = getattr(cause, "diag", None)
    cause_name = getattr(cause_diagnostic, "constraint_name", None)
    return str(cause_name) if cause_name else None


__all__ = [
    "OnboardingLifecycleError",
    "OnboardingRepositoryUnavailable",
    "PostgresOnboardingRepository",
]
