"""Deployment-only bootstrap for a tenant's first normalized administrator.

This repository deliberately has no runtime composition root.  It is used by
an operator CLI with the migration DSN after Supabase Auth has already issued
the target identity.  Authorization identity is always the signed-token
``issuer`` plus ``subject`` pair; email is copied only as optional profile data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from db.postgres_runtime_privileges import (
    RUNTIME_SCHEMA_ENV,
    assert_current_schema,
    validate_database_identifier,
)


ConnectionFactory = Callable[[], Any]
Clock = Callable[[], datetime]

BOOTSTRAP_EVENT_TYPE = "initial_tenant_admin_bootstrapped"
BOOTSTRAP_VERSION = 1
DEFAULT_OPERATOR_ISSUER = "urn:realytics:platform-operator"
_TENANT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class FirstTenantAdminBootstrapError(RuntimeError):
    """The requested operator bootstrap could not be completed safely."""


class FirstTenantAdminBootstrapConflict(FirstTenantAdminBootstrapError):
    """Existing authoritative state conflicts with the requested bootstrap."""


@dataclass(frozen=True)
class FirstTenantAdminBootstrapPlan:
    tenant_id: str
    tenant_name: str
    issuer: str
    subject: str
    provider: str
    operator_issuer: str
    operator_subject: str
    display_name: str | None = None

    @property
    def event_id(self) -> str:
        identity = "\x00".join(
            (
                "realytics-first-tenant-admin-v1",
                self.tenant_id,
                self.issuer,
                self.subject,
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"first-tenant-admin:{digest}"

    @property
    def membership_source(self) -> str:
        return f"platform-operator-bootstrap:{self.event_id}"

    @property
    def event_payload(self) -> dict[str, Any]:
        return {
            "bootstrapVersion": BOOTSTRAP_VERSION,
            "membershipRole": "admin",
            "membershipSource": self.membership_source,
            "membershipStatus": "active",
            "provider": self.provider,
            "tenantName": self.tenant_name,
        }


@dataclass(frozen=True)
class FirstTenantAdminBootstrapResult:
    action: str
    tenant_id: str
    issuer: str
    subject: str
    role: str
    membership_status: str
    event_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "eventId": self.event_id,
            "issuer": self.issuer,
            "membershipStatus": self.membership_status,
            "role": self.role,
            "subject": self.subject,
            "tenantId": self.tenant_id,
        }


def build_first_tenant_admin_plan(
    *,
    tenant_id: str,
    tenant_name: str,
    issuer: str,
    subject: str,
    provider: str,
    operator_issuer: str,
    operator_subject: str,
    display_name: str | None = None,
) -> FirstTenantAdminBootstrapPlan:
    normalized_tenant_id = str(tenant_id or "").strip()
    if not _TENANT_ID.fullmatch(normalized_tenant_id):
        raise ValueError(
            "tenant_id must be a lowercase slug of 1-63 letters, digits, or hyphens"
        )

    normalized_tenant_name = str(tenant_name or "").strip()
    if not normalized_tenant_name or len(normalized_tenant_name) > 200:
        raise ValueError("tenant_name must contain 1-200 characters")

    normalized_issuer = str(issuer or "").strip().rstrip("/")
    parsed_issuer = urlsplit(normalized_issuer)
    if (
        parsed_issuer.scheme != "https"
        or not parsed_issuer.hostname
        or parsed_issuer.username is not None
        or parsed_issuer.password is not None
        or parsed_issuer.query
        or parsed_issuer.fragment
        or parsed_issuer.path != "/auth/v1"
    ):
        raise ValueError(
            "issuer must be the exact HTTPS Supabase Auth issuer ending in /auth/v1"
        )

    try:
        normalized_subject = str(UUID(str(subject or "").strip()))
    except (ValueError, AttributeError) as exc:
        raise ValueError("subject must be the exact Supabase Auth user UUID") from exc

    normalized_provider = str(provider or "").strip().casefold()
    if not _PROVIDER.fullmatch(normalized_provider):
        raise ValueError("provider must be a lowercase Auth provider identifier")

    normalized_operator_issuer = str(operator_issuer or "").strip()
    normalized_operator_subject = str(operator_subject or "").strip()
    if not normalized_operator_issuer or len(normalized_operator_issuer) > 500:
        raise ValueError("operator_issuer must contain 1-500 characters")
    if not normalized_operator_subject or len(normalized_operator_subject) > 500:
        raise ValueError("operator_subject must contain 1-500 characters")

    normalized_display_name = str(display_name or "").strip() or None
    if normalized_display_name and len(normalized_display_name) > 200:
        raise ValueError("display_name must not exceed 200 characters")

    return FirstTenantAdminBootstrapPlan(
        tenant_id=normalized_tenant_id,
        tenant_name=normalized_tenant_name,
        issuer=normalized_issuer,
        subject=normalized_subject,
        provider=normalized_provider,
        operator_issuer=normalized_operator_issuer,
        operator_subject=normalized_operator_subject,
        display_name=normalized_display_name,
    )


class PostgresFirstTenantAdminBootstrapRepository:
    """Atomically register one tenant's first administrator and audit event."""

    def __init__(
        self,
        *,
        connection_factory: ConnectionFactory,
        schema_name: str | None = None,
        now: Clock | None = None,
    ) -> None:
        if connection_factory is None:
            raise TypeError("connection_factory is required")
        self._connect = connection_factory
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get(RUNTIME_SCHEMA_ENV)
            or "realytics",
            label="database schema",
        )
        self._now = now or (lambda: datetime.now(timezone.utc))

    def inspect(
        self,
        plan: FirstTenantAdminBootstrapPlan,
    ) -> FirstTenantAdminBootstrapResult:
        return self._execute(plan, apply=False)

    def bootstrap(
        self,
        plan: FirstTenantAdminBootstrapPlan,
    ) -> FirstTenantAdminBootstrapResult:
        return self._execute(plan, apply=True)

    def _execute(
        self,
        plan: FirstTenantAdminBootstrapPlan,
        *,
        apply: bool,
    ) -> FirstTenantAdminBootstrapResult:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                assert_current_schema(cursor, schema_name=self._schema)
                cursor.execute(
                    f'SET LOCAL search_path TO "{self._schema}", '
                    "pg_catalog, pg_temp"
                )
                cursor.execute(
                    "SELECT pg_catalog.set_config(%s, %s, true)",
                    ("realytics.tenant_id", plan.tenant_id),
                )
                cursor.execute(
                    "SELECT pg_catalog.pg_advisory_xact_lock("
                    "pg_catalog.hashtextextended(%s, 0))",
                    (
                        "first-tenant-admin-bootstrap:"
                        f"{self._schema}:{plan.tenant_id}",
                    ),
                )
                auth_email = self._require_auth_identity(cursor, plan)
                if apply:
                    # This is a rare deployment operation.  Blocking concurrent
                    # membership writes for the short bootstrap transaction is
                    # preferable to racing an invitation acceptance and
                    # accidentally creating a second "first" member.
                    cursor.execute(
                        f'LOCK TABLE "{self._schema}".tenant_memberships '
                        "IN SHARE ROW EXCLUSIVE MODE"
                    )

                tenant = self._tenant(cursor, plan)
                principal = self._principal(cursor, plan)
                membership = self._membership(cursor, plan)
                membership_count = self._tenant_membership_count(cursor, plan)
                event = self._event(cursor, plan)

                self._validate_existing_state(
                    plan,
                    tenant=tenant,
                    principal=principal,
                    membership=membership,
                    membership_count=membership_count,
                    event=event,
                )
                if membership is not None and event is not None:
                    connection.rollback()
                    return self._result(plan, action="current")

                if not apply:
                    connection.rollback()
                    return self._result(plan, action="would_bootstrap")

                observed_at = self._now()
                if observed_at.tzinfo is None:
                    raise FirstTenantAdminBootstrapError(
                        "bootstrap clock must return a timezone-aware timestamp"
                    )
                if tenant is None:
                    self._insert_tenant(cursor, plan, observed_at)
                if principal is None:
                    self._insert_principal(
                        cursor,
                        plan,
                        observed_at=observed_at,
                        auth_email=auth_email,
                    )
                self._insert_membership(cursor, plan, observed_at)
                self._insert_event(cursor, plan, observed_at)
            connection.commit()
            return self._result(plan, action="bootstrapped")
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _require_auth_identity(
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
    ) -> str | None:
        cursor.execute(
            """
            SELECT
                auth_user.id::TEXT,
                auth_user.email,
                EXISTS (
                    SELECT 1
                    FROM auth.identities AS auth_identity
                    WHERE auth_identity.user_id = auth_user.id
                      AND auth_identity.provider = %s
                ) AS provider_present
            FROM auth.users AS auth_user
            WHERE auth_user.id = %s::UUID
            FOR KEY SHARE OF auth_user
            """,
            (plan.provider, plan.subject),
        )
        row = cursor.fetchone()
        if row is None or str(row[0]) != plan.subject:
            raise FirstTenantAdminBootstrapError(
                "the exact subject does not exist in Supabase auth.users"
            )
        if not bool(row[2]):
            raise FirstTenantAdminBootstrapError(
                "the exact Supabase user is not linked to the requested provider"
            )
        return str(row[1]).strip() if row[1] is not None else None

    def _tenant(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
    ) -> tuple[Any, ...] | None:
        cursor.execute(
            f"""
            SELECT name, status
            FROM "{self._schema}".tenants
            WHERE tenant_id = %s
            FOR UPDATE
            """,
            (plan.tenant_id,),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    def _principal(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
    ) -> tuple[Any, ...] | None:
        cursor.execute(
            f"""
            SELECT principal_type, status, verification_method
            FROM "{self._schema}".principals
            WHERE issuer = %s AND subject = %s
            FOR UPDATE
            """,
            (plan.issuer, plan.subject),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    def _membership(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
    ) -> tuple[Any, ...] | None:
        cursor.execute(
            f"""
            SELECT role, status, membership_version, membership_source
            FROM "{self._schema}".tenant_memberships
            WHERE tenant_id = %s AND issuer = %s AND subject = %s
            FOR UPDATE
            """,
            (plan.tenant_id, plan.issuer, plan.subject),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    def _tenant_membership_count(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
    ) -> int:
        cursor.execute(
            f"""
            SELECT COUNT(*)::BIGINT
            FROM "{self._schema}".tenant_memberships
            WHERE tenant_id = %s
            """,
            (plan.tenant_id,),
        )
        row = cursor.fetchone()
        return int(row[0]) if row is not None else 0

    def _event(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
    ) -> tuple[Any, ...] | None:
        cursor.execute(
            f"""
            SELECT
                event_type, invitation_id, actor_issuer, actor_subject,
                target_issuer, target_subject, event_payload
            FROM "{self._schema}".tenant_identity_access_events
            WHERE tenant_id = %s AND event_id = %s
            FOR UPDATE
            """,
            (plan.tenant_id, plan.event_id),
        )
        row = cursor.fetchone()
        return tuple(row) if row is not None else None

    @staticmethod
    def _validate_existing_state(
        plan: FirstTenantAdminBootstrapPlan,
        *,
        tenant: tuple[Any, ...] | None,
        principal: tuple[Any, ...] | None,
        membership: tuple[Any, ...] | None,
        membership_count: int,
        event: tuple[Any, ...] | None,
    ) -> None:
        if tenant is not None and (
            str(tenant[0]) != plan.tenant_name
            or str(tenant[1]) != "active"
        ):
            raise FirstTenantAdminBootstrapConflict(
                "tenant identifier already exists with another name or status"
            )
        if principal is not None and (
            str(principal[0]) != "human"
            or str(principal[1]) != "active"
            or not str(principal[2] or "").strip()
        ):
            raise FirstTenantAdminBootstrapConflict(
                "issuer and subject already identify a non-active human principal"
            )

        expected_membership = (
            "admin",
            "active",
            1,
            plan.membership_source,
        )
        if membership is not None and tuple(membership) != expected_membership:
            raise FirstTenantAdminBootstrapConflict(
                "target identity already has a conflicting tenant membership"
            )

        if event is not None:
            raw_payload = event[6]
            if isinstance(raw_payload, (str, bytes, bytearray)):
                try:
                    raw_payload = json.loads(raw_payload)
                except (TypeError, ValueError) as exc:
                    raise FirstTenantAdminBootstrapConflict(
                        "bootstrap audit event payload is not valid JSON"
                    ) from exc
            expected_event = (
                BOOTSTRAP_EVENT_TYPE,
                None,
                plan.operator_issuer,
                plan.operator_subject,
                plan.issuer,
                plan.subject,
                plan.event_payload,
            )
            actual_event = (*event[:6], raw_payload)
            if actual_event != expected_event:
                raise FirstTenantAdminBootstrapConflict(
                    "bootstrap event identifier already has conflicting lineage"
                )

        if (membership is None) != (event is None):
            raise FirstTenantAdminBootstrapConflict(
                "partial membership/audit bootstrap state requires operator review"
            )
        if membership is not None:
            if tenant is None or principal is None:
                raise FirstTenantAdminBootstrapConflict(
                    "bootstrap membership is missing tenant or principal lineage"
                )
            return
        if membership_count != 0:
            raise FirstTenantAdminBootstrapConflict(
                "tenant already has memberships; first-admin bootstrap is closed"
            )

    def _insert_tenant(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
        observed_at: datetime,
    ) -> None:
        metadata = {
            "authority": "platform_operator_bootstrap",
            "bootstrapEventId": plan.event_id,
            "bootstrapVersion": BOOTSTRAP_VERSION,
        }
        cursor.execute(
            f"""
            INSERT INTO "{self._schema}".tenants (
                tenant_id, name, status, created_at, updated_at, metadata_json
            ) VALUES (%s, %s, 'active', %s, %s, %s::JSONB)
            """,
            (
                plan.tenant_id,
                plan.tenant_name,
                observed_at,
                observed_at,
                _json(metadata),
            ),
        )

    def _insert_principal(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
        *,
        observed_at: datetime,
        auth_email: str | None,
    ) -> None:
        cursor.execute(
            f"""
            INSERT INTO "{self._schema}".principals (
                issuer, subject, principal_type, status, email, display_name,
                legacy_user_id, verification_method, first_verified_at,
                last_verified_at, created_at, updated_at
            ) VALUES (
                %s, %s, 'human', 'active', %s, %s, NULL, %s,
                %s, %s, %s, %s
            )
            """,
            (
                plan.issuer,
                plan.subject,
                auth_email,
                plan.display_name,
                f"platform_operator_approved_supabase_{plan.provider}",
                observed_at,
                observed_at,
                observed_at,
                observed_at,
            ),
        )

    def _insert_membership(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
        observed_at: datetime,
    ) -> None:
        cursor.execute(
            f"""
            INSERT INTO "{self._schema}".tenant_memberships (
                tenant_id, issuer, subject, role, status,
                membership_version, membership_source, created_at, updated_at
            ) VALUES (%s, %s, %s, 'admin', 'active', 1, %s, %s, %s)
            """,
            (
                plan.tenant_id,
                plan.issuer,
                plan.subject,
                plan.membership_source,
                observed_at,
                observed_at,
            ),
        )

    def _insert_event(
        self,
        cursor: Any,
        plan: FirstTenantAdminBootstrapPlan,
        observed_at: datetime,
    ) -> None:
        cursor.execute(
            f"""
            INSERT INTO "{self._schema}".tenant_identity_access_events (
                tenant_id, event_id, event_type, invitation_id,
                actor_issuer, actor_subject, target_issuer, target_subject,
                event_payload, occurred_at
            ) VALUES (%s, %s, %s, NULL, %s, %s, %s, %s, %s::JSONB, %s)
            """,
            (
                plan.tenant_id,
                plan.event_id,
                BOOTSTRAP_EVENT_TYPE,
                plan.operator_issuer,
                plan.operator_subject,
                plan.issuer,
                plan.subject,
                _json(plan.event_payload),
                observed_at,
            ),
        )

    @staticmethod
    def _result(
        plan: FirstTenantAdminBootstrapPlan,
        *,
        action: str,
    ) -> FirstTenantAdminBootstrapResult:
        return FirstTenantAdminBootstrapResult(
            action=action,
            tenant_id=plan.tenant_id,
            issuer=plan.issuer,
            subject=plan.subject,
            role="admin",
            membership_status="active",
            event_id=plan.event_id,
        )


def _json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "BOOTSTRAP_EVENT_TYPE",
    "DEFAULT_OPERATOR_ISSUER",
    "FirstTenantAdminBootstrapConflict",
    "FirstTenantAdminBootstrapError",
    "FirstTenantAdminBootstrapPlan",
    "FirstTenantAdminBootstrapResult",
    "PostgresFirstTenantAdminBootstrapRepository",
    "build_first_tenant_admin_plan",
]
