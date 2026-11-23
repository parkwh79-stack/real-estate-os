"""Governed PostgreSQL administration for tenant membership roles.

Authorization is performed by the FastAPI boundary before this repository is
called.  This layer supplies the independent database invariants: tenant-bound
lookups, optimistic concurrency, self-change protection, last-admin
protection, and an append-only audit event in the same transaction.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from db.postgres_runtime_pool import api_tenant_transaction
from db.postgres_runtime_privileges import validate_database_identifier
from schemas.identity_access import ChangeTenantMembershipRoleInput


TenantTransactionFactory = Callable[[str], AbstractContextManager[Any]]


class IdentityMembershipAdministrationRepositoryUnavailable(RuntimeError):
    """The normalized tenant membership authority could not be reached."""


class MembershipRoleChangeError(ValueError):
    """Stable, non-secret-bearing membership role-change failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class PostgresIdentityMembershipAdministrationRepository:
    """List and change roles under the tenant-scoped API runtime principal.

    The read-only identity pool remains isolated from administrative writes.
    ``api_tenant_transaction`` establishes the transaction-local tenant RLS
    context before any repository SQL executes.
    """

    def __init__(
        self,
        *,
        transaction_factory: TenantTransactionFactory = api_tenant_transaction,
        schema_name: str | None = None,
        now: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if transaction_factory is None:
            raise TypeError("transaction_factory is required")
        self._transaction = transaction_factory
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get("REALYTICS_DATABASE_SCHEMA")
            or "realytics",
            label="database schema",
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._event_id_factory = event_id_factory or (
            lambda: f"identity-event-{uuid4()}"
        )

    def list_for_tenant(
        self,
        *,
        tenant_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant_id = _required(tenant_id, "tenant_id")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        try:
            with self._transaction(tenant_id) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT
                            membership.tenant_id,
                            membership.issuer,
                            membership.subject,
                            principal.principal_type,
                            principal.status AS principal_status,
                            principal.email,
                            principal.display_name,
                            membership.role,
                            membership.status,
                            membership.membership_version,
                            membership.updated_at
                        FROM "{self._schema}".tenant_memberships AS membership
                        JOIN "{self._schema}".principals AS principal
                          ON principal.issuer = membership.issuer
                         AND principal.subject = membership.subject
                        WHERE membership.tenant_id = %s
                        ORDER BY
                            CASE WHEN membership.status = 'active' THEN 0 ELSE 1 END,
                            principal.display_name NULLS LAST,
                            principal.email NULLS LAST,
                            membership.issuer,
                            membership.subject
                        LIMIT %s
                        """,
                        (tenant_id, limit),
                    )
                    return [
                        _membership_view(row) for row in cursor.fetchall()
                    ]
        except Exception as exc:
            raise IdentityMembershipAdministrationRepositoryUnavailable(
                "tenant membership administration authority is unavailable"
            ) from exc

    def change_role(
        self,
        *,
        tenant_id: str,
        actor_issuer: str,
        actor_subject: str,
        payload: ChangeTenantMembershipRoleInput,
    ) -> dict[str, Any]:
        tenant_id = _required(tenant_id, "tenant_id")
        actor_issuer = _required(actor_issuer, "actor_issuer").rstrip("/")
        actor_subject = _required(actor_subject, "actor_subject")
        target_issuer = payload.targetIssuer
        target_subject = payload.targetSubject
        requested_role = payload.role.value
        now = self._now()
        event_id = self._event_id_factory()

        try:
            with self._transaction(tenant_id) as connection:
                with connection.cursor() as cursor:
                    # Serialize role changes per tenant so two concurrent admin
                    # demotions cannot both pass the last-admin check.
                    cursor.execute(
                        """
                        SELECT pg_catalog.pg_advisory_xact_lock(
                            pg_catalog.hashtextextended(%s, 0)
                        )
                        """,
                        (f"tenant-membership-role\x1f{tenant_id}",),
                    )
                    cursor.execute(
                        f"""
                        SELECT
                            membership.tenant_id,
                            membership.issuer,
                            membership.subject,
                            principal.principal_type,
                            principal.status AS principal_status,
                            principal.email,
                            principal.display_name,
                            membership.role,
                            membership.status,
                            membership.membership_version,
                            membership.updated_at
                        FROM "{self._schema}".tenant_memberships AS membership
                        JOIN "{self._schema}".principals AS principal
                          ON principal.issuer = membership.issuer
                         AND principal.subject = membership.subject
                        WHERE membership.tenant_id = %s
                          AND membership.issuer = %s
                          AND membership.subject = %s
                        FOR UPDATE OF membership
                        """,
                        (tenant_id, target_issuer, target_subject),
                    )
                    target_row = cursor.fetchone()
                    if target_row is None:
                        raise MembershipRoleChangeError(
                            "tenant_membership_not_found"
                        )
                    current = _membership_view(target_row)
                    if current["status"] != "active":
                        raise MembershipRoleChangeError(
                            "tenant_membership_not_active"
                        )
                    if current["principalStatus"] != "active":
                        raise MembershipRoleChangeError(
                            "target_principal_not_active"
                        )
                    if current["principalType"] != "human":
                        raise MembershipRoleChangeError(
                            "target_principal_not_human"
                        )
                    if (
                        current["membershipVersion"]
                        != payload.expectedMembershipVersion
                    ):
                        raise MembershipRoleChangeError(
                            "membership_version_conflict"
                        )
                    if (
                        target_issuer == actor_issuer
                        and target_subject == actor_subject
                    ):
                        raise MembershipRoleChangeError(
                            "membership_self_change_forbidden"
                        )
                    if current["role"] == requested_role:
                        raise MembershipRoleChangeError(
                            "membership_role_unchanged"
                        )

                    if current["role"] == "admin" and requested_role != "admin":
                        cursor.execute(
                            f"""
                            SELECT count(*)
                            FROM "{self._schema}".tenant_memberships
                            WHERE tenant_id = %s
                              AND role = 'admin'
                              AND status = 'active'
                            """,
                            (tenant_id,),
                        )
                        count_row = cursor.fetchone()
                        active_admin_count = int(count_row[0]) if count_row else 0
                        if active_admin_count <= 1:
                            raise MembershipRoleChangeError(
                                "last_tenant_admin_role_change_forbidden"
                            )

                    cursor.execute(
                        f"""
                        UPDATE "{self._schema}".tenant_memberships
                           SET role = %s,
                               membership_version = membership_version + 1,
                               updated_at = %s
                         WHERE tenant_id = %s
                           AND issuer = %s
                           AND subject = %s
                           AND status = 'active'
                           AND membership_version = %s
                     RETURNING membership_version, updated_at
                        """,
                        (
                            requested_role,
                            now,
                            tenant_id,
                            target_issuer,
                            target_subject,
                            payload.expectedMembershipVersion,
                        ),
                    )
                    updated = cursor.fetchone()
                    if updated is None:
                        raise MembershipRoleChangeError(
                            "membership_version_conflict"
                        )
                    next_version = int(updated[0])
                    updated_at = updated[1]
                    cursor.execute(
                        f"""
                        INSERT INTO "{self._schema}".tenant_identity_access_events (
                            tenant_id, event_id, event_type,
                            actor_issuer, actor_subject,
                            target_issuer, target_subject,
                            event_payload, occurred_at
                        ) VALUES (
                            %s, %s, 'membership_role_changed',
                            %s, %s, %s, %s, %s::jsonb, %s
                        )
                        """,
                        (
                            tenant_id,
                            event_id,
                            actor_issuer,
                            actor_subject,
                            target_issuer,
                            target_subject,
                            json.dumps(
                                {
                                    "previousRole": current["role"],
                                    "role": requested_role,
                                    "previousMembershipVersion": current[
                                        "membershipVersion"
                                    ],
                                    "membershipVersion": next_version,
                                    "reason": payload.reason,
                                },
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            now,
                        ),
                    )
                    return {
                        **current,
                        "role": requested_role,
                        "membershipVersion": next_version,
                        "updatedAt": updated_at,
                    }
        except MembershipRoleChangeError:
            raise
        except Exception as exc:
            raise IdentityMembershipAdministrationRepositoryUnavailable(
                "tenant membership administration authority is unavailable"
            ) from exc


def _required(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _membership_view(row: Any) -> dict[str, Any]:
    return {
        "tenantId": str(row[0]),
        "issuer": str(row[1]),
        "subject": str(row[2]),
        "principalType": str(row[3]),
        "principalStatus": str(row[4]),
        "email": str(row[5]) if row[5] is not None else None,
        "displayName": str(row[6]) if row[6] is not None else None,
        "role": str(row[7]),
        "status": str(row[8]),
        "membershipVersion": int(row[9]),
        "updatedAt": row[10],
    }


__all__ = [
    "IdentityMembershipAdministrationRepositoryUnavailable",
    "MembershipRoleChangeError",
    "PostgresIdentityMembershipAdministrationRepository",
]
