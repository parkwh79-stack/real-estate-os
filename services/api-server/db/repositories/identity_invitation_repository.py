"""PostgreSQL authority for single-use tenant invitations."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from db.postgres_runtime_pool import identity_transaction
from db.postgres_runtime_privileges import validate_database_identifier
from schemas.identity_access import IssueTenantInvitationInput


TransactionFactory = Callable[[], AbstractContextManager[Any]]


class IdentityInvitationRepositoryUnavailable(RuntimeError):
    """The invitation authority could not be reached safely."""


class InvitationLifecycleError(ValueError):
    """A stable, non-secret-bearing invitation failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: dict[str, Any]
    token: str


class PostgresIdentityInvitationRepository:
    def __init__(
        self,
        *,
        transaction_factory: TransactionFactory = identity_transaction,
        schema_name: str | None = None,
        now: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._transaction = transaction_factory
        self._schema = validate_database_identifier(
            schema_name
            or os.environ.get("REALYTICS_DATABASE_SCHEMA")
            or "realytics",
            label="database schema",
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._token_factory = token_factory or (
            lambda: secrets.token_urlsafe(48)
        )

    def issue(
        self,
        *,
        tenant_id: str,
        payload: IssueTenantInvitationInput,
        inviter_issuer: str,
        inviter_subject: str,
    ) -> IssuedInvitation:
        tenant_id = _required(tenant_id, "tenant_id")
        inviter_issuer = _required(inviter_issuer, "inviter_issuer").rstrip("/")
        inviter_subject = _required(inviter_subject, "inviter_subject")
        now = self._now()
        expires_at = now + _hours(payload.expiresInHours)
        invitation_id = f"inv-{uuid4()}"
        event_id = f"identity-event-{uuid4()}"
        token = self._token_factory()
        if len(token) < 32:
            raise RuntimeError("invitation token factory returned a weak token")
        token_hash = _hash_token(token)
        invitation_params = (
            tenant_id,
            invitation_id,
            payload.role.value,
            payload.invitedEmail,
            payload.invitedIssuer,
            payload.invitedSubject,
            token_hash,
            inviter_issuer,
            inviter_subject,
            now,
            expires_at,
        )
        try:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    _set_tenant(cursor, tenant_id)
                    cursor.execute(
                        f"""
                        INSERT INTO "{self._schema}".tenant_invitations (
                            tenant_id, invitation_id, role, status,
                            invited_email, invited_issuer, invited_subject,
                            token_hash, issued_by_issuer, issued_by_subject,
                            issued_at, expires_at
                        ) VALUES (
                            %s, %s, %s, 'pending', %s, %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        """,
                        invitation_params,
                    )
                    cursor.execute(
                        f"""
                        INSERT INTO "{self._schema}".identity_invitation_token_index (
                            token_hash, tenant_scope_key, invitation_id,
                            status, expires_at
                        ) VALUES (%s, %s, %s, 'pending', %s)
                        """,
                        (token_hash, tenant_id, invitation_id, expires_at),
                    )
                    _append_event(
                        cursor,
                        schema=self._schema,
                        tenant_id=tenant_id,
                        event_id=event_id,
                        event_type="invitation_issued",
                        invitation_id=invitation_id,
                        actor_issuer=inviter_issuer,
                        actor_subject=inviter_subject,
                        occurred_at=now,
                        payload={"role": payload.role.value},
                    )
        except InvitationLifecycleError:
            raise
        except Exception as exc:
            raise IdentityInvitationRepositoryUnavailable(
                "identity invitation authority is unavailable"
            ) from exc

        return IssuedInvitation(
            invitation={
                "invitationId": invitation_id,
                "tenantId": tenant_id,
                "role": payload.role.value,
                "status": "pending",
                "invitedEmail": payload.invitedEmail,
                "invitedIssuer": payload.invitedIssuer,
                "invitedSubject": payload.invitedSubject,
                "issuedByIssuer": inviter_issuer,
                "issuedBySubject": inviter_subject,
                "issuedAt": now,
                "expiresAt": expires_at,
                "acceptedByIssuer": None,
                "acceptedBySubject": None,
                "acceptedAt": None,
                "revokedAt": None,
                "revocationReason": None,
            },
            token=token,
        )

    def list_for_tenant(
        self,
        *,
        tenant_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant_id = _required(tenant_id, "tenant_id")
        try:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    _set_tenant(cursor, tenant_id)
                    cursor.execute(
                        f"""
                        SELECT invitation_id, tenant_id, role, status,
                               invited_email, invited_issuer, invited_subject,
                               issued_by_issuer, issued_by_subject, issued_at,
                               expires_at, accepted_by_issuer,
                               accepted_by_subject, accepted_at, revoked_at,
                               revocation_reason
                          FROM "{self._schema}".tenant_invitations
                         WHERE tenant_id = %s
                         ORDER BY issued_at DESC, invitation_id DESC
                         LIMIT %s
                        """,
                        (tenant_id, limit),
                    )
                    return [_invitation_view(row) for row in cursor.fetchall()]
        except Exception as exc:
            raise IdentityInvitationRepositoryUnavailable(
                "identity invitation authority is unavailable"
            ) from exc

    def revoke(
        self,
        *,
        tenant_id: str,
        invitation_id: str,
        actor_issuer: str,
        actor_subject: str,
        reason: str,
    ) -> dict[str, Any]:
        tenant_id = _required(tenant_id, "tenant_id")
        invitation_id = _required(invitation_id, "invitation_id")
        reason = _required(reason, "reason")
        now = self._now()
        try:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    _set_tenant(cursor, tenant_id)
                    cursor.execute(
                        f"""
                        UPDATE "{self._schema}".tenant_invitations
                           SET status = 'revoked', revoked_at = %s,
                               revocation_reason = %s, updated_at = %s
                         WHERE tenant_id = %s AND invitation_id = %s
                           AND status = 'pending' AND expires_at > %s
                     RETURNING token_hash
                        """,
                        (now, reason, now, tenant_id, invitation_id, now),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise InvitationLifecycleError(
                            "invitation_not_pending_or_not_found"
                        )
                    cursor.execute(
                        f"""
                        UPDATE "{self._schema}".identity_invitation_token_index
                           SET status = 'revoked', updated_at = %s
                         WHERE token_hash = %s AND status = 'pending'
                        """,
                        (now, row[0]),
                    )
                    _append_event(
                        cursor,
                        schema=self._schema,
                        tenant_id=tenant_id,
                        event_id=f"identity-event-{uuid4()}",
                        event_type="invitation_revoked",
                        invitation_id=invitation_id,
                        actor_issuer=actor_issuer,
                        actor_subject=actor_subject,
                        occurred_at=now,
                        payload={"reason": reason},
                    )
            return {"invitationId": invitation_id, "status": "revoked"}
        except InvitationLifecycleError:
            raise
        except Exception as exc:
            raise IdentityInvitationRepositoryUnavailable(
                "identity invitation authority is unavailable"
            ) from exc

    def accept(
        self,
        *,
        token: str,
        principal_issuer: str,
        principal_subject: str,
        principal_email: str | None,
        principal_display_name: str | None,
    ) -> dict[str, Any]:
        token_hash = _hash_token(_required(token, "token"))
        principal_issuer = _required(
            principal_issuer, "principal_issuer"
        ).rstrip("/")
        principal_subject = _required(principal_subject, "principal_subject")
        principal_email = _optional(principal_email)
        now = self._now()
        expired = False
        try:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT tenant_scope_key, invitation_id, status,
                               expires_at
                          FROM "{self._schema}".identity_invitation_token_index
                         WHERE token_hash = %s
                         FOR UPDATE
                        """,
                        (token_hash,),
                    )
                    index_row = cursor.fetchone()
                    if index_row is None:
                        raise InvitationLifecycleError("invitation_invalid")
                    tenant_id = str(index_row[0])
                    invitation_id = str(index_row[1])
                    if str(index_row[2]) != "pending":
                        raise InvitationLifecycleError("invitation_not_pending")
                    _set_tenant(cursor, tenant_id)
                    cursor.execute(
                        f"""
                        SELECT invitation_id, tenant_id, role, status,
                               invited_email, invited_issuer, invited_subject,
                               issued_by_issuer, issued_by_subject, issued_at,
                               expires_at, accepted_by_issuer,
                               accepted_by_subject, accepted_at, revoked_at,
                               revocation_reason
                          FROM "{self._schema}".tenant_invitations
                         WHERE tenant_id = %s AND invitation_id = %s
                           AND token_hash = %s
                         FOR UPDATE
                        """,
                        (tenant_id, invitation_id, token_hash),
                    )
                    invitation_row = cursor.fetchone()
                    if invitation_row is None:
                        raise InvitationLifecycleError(
                            "invitation_authority_integrity_error"
                        )
                    invitation = _invitation_view(invitation_row)
                    if invitation["status"] != "pending":
                        raise InvitationLifecycleError("invitation_not_pending")
                    if invitation["expiresAt"] <= now:
                        cursor.execute(
                            f"""
                            UPDATE "{self._schema}".tenant_invitations
                               SET status = 'expired', updated_at = %s
                             WHERE tenant_id = %s AND invitation_id = %s
                            """,
                            (now, tenant_id, invitation_id),
                        )
                        cursor.execute(
                            f"""
                            UPDATE "{self._schema}".identity_invitation_token_index
                               SET status = 'expired', updated_at = %s
                             WHERE token_hash = %s
                            """,
                            (now, token_hash),
                        )
                        _append_event(
                            cursor,
                            schema=self._schema,
                            tenant_id=tenant_id,
                            event_id=f"identity-event-{uuid4()}",
                            event_type="invitation_expired",
                            invitation_id=invitation_id,
                            occurred_at=now,
                        )
                        expired = True
                    else:
                        _verify_binding(
                            invitation,
                            issuer=principal_issuer,
                            subject=principal_subject,
                            email=principal_email,
                        )
                        cursor.execute(
                            f"""
                            SELECT status
                              FROM "{self._schema}".tenant_memberships
                             WHERE tenant_id = %s AND issuer = %s
                               AND subject = %s
                             FOR UPDATE
                            """,
                            (tenant_id, principal_issuer, principal_subject),
                        )
                        if cursor.fetchone() is not None:
                            raise InvitationLifecycleError(
                                "tenant_membership_already_exists"
                            )
                        cursor.execute(
                            f"""
                            INSERT INTO "{self._schema}".principals (
                                issuer, subject, principal_type, status,
                                email, display_name, verification_method,
                                first_verified_at, last_verified_at
                            ) VALUES (
                                %s, %s, 'human', 'active', %s, %s,
                                'supabase_verified_invitation', %s, %s
                            )
                            ON CONFLICT (issuer, subject) DO UPDATE
                               SET email = COALESCE(EXCLUDED.email,
                                                    principals.email),
                                   display_name = COALESCE(
                                       EXCLUDED.display_name,
                                       principals.display_name
                                   ),
                                   last_verified_at = EXCLUDED.last_verified_at,
                                   updated_at = EXCLUDED.last_verified_at
                         RETURNING status
                            """,
                            (
                                principal_issuer,
                                principal_subject,
                                principal_email,
                                _optional(principal_display_name),
                                now,
                                now,
                            ),
                        )
                        principal_status = cursor.fetchone()
                        if (
                            principal_status is None
                            or str(principal_status[0]) != "active"
                        ):
                            raise InvitationLifecycleError(
                                "principal_inactive"
                            )
                        cursor.execute(
                            f"""
                            INSERT INTO "{self._schema}".tenant_memberships (
                                tenant_id, issuer, subject, role, status,
                                membership_version, membership_source
                            ) VALUES (
                                %s, %s, %s, %s, 'active', 1, %s
                            )
                            """,
                            (
                                tenant_id,
                                principal_issuer,
                                principal_subject,
                                invitation["role"],
                                f"invitation:{invitation_id}",
                            ),
                        )
                        cursor.execute(
                            f"""
                            UPDATE "{self._schema}".tenant_invitations
                               SET status = 'accepted',
                                   accepted_by_issuer = %s,
                                   accepted_by_subject = %s,
                                   accepted_at = %s, updated_at = %s
                             WHERE tenant_id = %s AND invitation_id = %s
                               AND status = 'pending'
                            """,
                            (
                                principal_issuer,
                                principal_subject,
                                now,
                                now,
                                tenant_id,
                                invitation_id,
                            ),
                        )
                        cursor.execute(
                            f"""
                            UPDATE "{self._schema}".identity_invitation_token_index
                               SET status = 'accepted', updated_at = %s
                             WHERE token_hash = %s AND status = 'pending'
                            """,
                            (now, token_hash),
                        )
                        _append_event(
                            cursor,
                            schema=self._schema,
                            tenant_id=tenant_id,
                            event_id=f"identity-event-{uuid4()}",
                            event_type="invitation_accepted",
                            invitation_id=invitation_id,
                            target_issuer=principal_issuer,
                            target_subject=principal_subject,
                            occurred_at=now,
                            payload={"role": invitation["role"]},
                        )
        except InvitationLifecycleError:
            raise
        except Exception as exc:
            raise IdentityInvitationRepositoryUnavailable(
                "identity invitation authority is unavailable"
            ) from exc
        if expired:
            raise InvitationLifecycleError("invitation_expired")
        return {
            "invitationId": invitation_id,
            "tenantId": tenant_id,
            "role": invitation["role"],
            "status": "accepted",
            "membershipStatus": "active",
        }


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _required(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def _optional(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _hours(value: int):
    from datetime import timedelta

    return timedelta(hours=value)


def _set_tenant(cursor: Any, tenant_id: str) -> None:
    cursor.execute(
        "SELECT set_config(%s, %s, true)",
        ("realytics.tenant_id", tenant_id),
    )


def _append_event(
    cursor: Any,
    *,
    schema: str,
    tenant_id: str,
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    invitation_id: str | None = None,
    actor_issuer: str | None = None,
    actor_subject: str | None = None,
    target_issuer: str | None = None,
    target_subject: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    cursor.execute(
        f"""
        INSERT INTO "{schema}".tenant_identity_access_events (
            tenant_id, event_id, event_type, invitation_id,
            actor_issuer, actor_subject, target_issuer, target_subject,
            event_payload, occurred_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
        """,
        (
            tenant_id,
            event_id,
            event_type,
            invitation_id,
            actor_issuer,
            actor_subject,
            target_issuer,
            target_subject,
            json.dumps(payload or {}, sort_keys=True, separators=(",", ":")),
            occurred_at,
        ),
    )


def _invitation_view(row: Any) -> dict[str, Any]:
    return {
        "invitationId": str(row[0]),
        "tenantId": str(row[1]),
        "role": str(row[2]),
        "status": str(row[3]),
        "invitedEmail": str(row[4]) if row[4] is not None else None,
        "invitedIssuer": str(row[5]) if row[5] is not None else None,
        "invitedSubject": str(row[6]) if row[6] is not None else None,
        "issuedByIssuer": str(row[7]),
        "issuedBySubject": str(row[8]),
        "issuedAt": row[9],
        "expiresAt": row[10],
        "acceptedByIssuer": str(row[11]) if row[11] is not None else None,
        "acceptedBySubject": str(row[12]) if row[12] is not None else None,
        "acceptedAt": row[13],
        "revokedAt": row[14],
        "revocationReason": str(row[15]) if row[15] is not None else None,
    }


def _verify_binding(
    invitation: dict[str, Any],
    *,
    issuer: str,
    subject: str,
    email: str | None,
) -> None:
    invited_issuer = invitation.get("invitedIssuer")
    invited_subject = invitation.get("invitedSubject")
    if invited_issuer and (
        str(invited_issuer).rstrip("/") != issuer
        or str(invited_subject) != subject
    ):
        raise InvitationLifecycleError("invitation_identity_mismatch")
    invited_email = invitation.get("invitedEmail")
    if invited_email and (
        not email or str(invited_email).casefold() != email.casefold()
    ):
        raise InvitationLifecycleError("invitation_email_mismatch")


__all__ = [
    "IdentityInvitationRepositoryUnavailable",
    "InvitationLifecycleError",
    "IssuedInvitation",
    "PostgresIdentityInvitationRepository",
]
