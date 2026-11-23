"""Idempotent, verified-principal bootstrap of an isolated analysis workspace.

Uses the existing API principal transaction and narrow column grants. It never
approves organization requests, changes an existing role or revives a revoked
identity. The identity-reader pool remains read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from db.postgres_runtime_pool import api_principal_transaction
from db.postgres_runtime_privileges import validate_database_identifier
from schemas.identity_access import TenantRole


class DefaultWorkspaceUnavailable(RuntimeError):
    """The bootstrap transaction could not complete safely."""


class DefaultWorkspaceError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def default_workspace_id(issuer: str, subject: str) -> str:
    # Length framing avoids ambiguous identities even if a key contains a separator.
    encoded = json.dumps([issuer, subject], ensure_ascii=False, separators=(",", ":"))
    return "personal-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class PostgresDefaultWorkspaceRepository:
    def __init__(
        self, *,
        transaction_factory: Callable[[str, str], AbstractContextManager[Any]] = api_principal_transaction,
        schema_name: str | None = None,
        now: Callable[[], datetime] | None = None,
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._transaction = transaction_factory
        self._schema = validate_database_identifier(
            schema_name or os.environ.get("REALYTICS_DATABASE_SCHEMA") or "realytics",
            label="database schema",
        )
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._event_id = event_id_factory or (lambda: f"identity-event-{uuid4()}")

    def ensure_for_principal(
        self, *, issuer: str, subject: str,
        email: str | None, display_name: str | None,
    ) -> dict[str, Any]:
        issuer, subject = str(issuer or "").strip().rstrip("/"), str(subject or "").strip()
        if not issuer or not subject:
            raise DefaultWorkspaceError("verified_principal_key_required")
        tenant_id = default_workspace_id(issuer, subject)
        now = self._now()
        try:
            with self._transaction(issuer, subject) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(%s, 0))",
                        (f"default-workspace\x1f{issuer}\x1f{subject}",),
                    )
                    cursor.execute(
                        f'SELECT principal_type, status FROM "{self._schema}".principals '
                        'WHERE issuer = %s AND subject = %s FOR UPDATE',
                        (issuer, subject),
                    )
                    principal = cursor.fetchone()
                    if principal is not None:
                        if str(principal[0]) != "human":
                            raise DefaultWorkspaceError("verified_human_principal_required")
                        if str(principal[1]) != "active":
                            raise DefaultWorkspaceError("principal_disabled")
                    cursor.execute(
                        f'SELECT membership.tenant_id, membership.role, membership.status, tenant.status '
                        f'FROM "{self._schema}".tenant_memberships AS membership '
                        f'JOIN "{self._schema}".tenants AS tenant ON tenant.tenant_id = membership.tenant_id '
                        'WHERE membership.issuer = %s AND membership.subject = %s '
                        'ORDER BY membership.tenant_id FOR UPDATE OF membership',
                        (issuer, subject),
                    )
                    memberships = cursor.fetchall()
                    active = [row for row in memberships if row[2] == "active" and row[3] == "active"]
                    for row in active:
                        if str(row[1]) not in {role.value for role in TenantRole}:
                            raise DefaultWorkspaceError("invalid_membership_role")
                    if len(active) > 1:
                        raise DefaultWorkspaceError("tenant_selection_required")
                    if active:
                        if principal is None:
                            raise DefaultWorkspaceError("identity_authority_integrity_error")
                        return {"tenantId": str(active[0][0]), "role": str(active[0][1]), "created": False}
                    if memberships:
                        # An old membership state is not permission to resurrect access
                        # or bypass a suspended/revoked account via a fresh workspace.
                        raise DefaultWorkspaceError("no_active_tenant_membership")
                    cursor.execute(
                        f'SELECT tenant_id, status FROM "{self._schema}".tenants WHERE tenant_id = %s',
                        (tenant_id,),
                    )
                    existing = cursor.fetchone()
                    if existing is not None:
                        if str(existing[1]) != "active":
                            raise DefaultWorkspaceError("tenant_inactive")
                        raise DefaultWorkspaceError("default_workspace_conflict")
                    if principal is None:
                        cursor.execute(
                            f'INSERT INTO "{self._schema}".principals '
                            '(issuer, subject, principal_type, status, email, display_name, verification_method, '
                            'first_verified_at, last_verified_at, created_at, updated_at) '
                            "VALUES (%s, %s, 'human', 'active', %s, %s, 'verified_signup', %s, %s, %s, %s)",
                            (issuer, subject, email, display_name, now, now, now, now),
                        )
                    cursor.execute(
                        f'INSERT INTO "{self._schema}".tenants '
                        '(tenant_id, name, status, created_at, updated_at, metadata_json) '
                        "VALUES (%s, %s, 'active', %s, %s, %s::jsonb)",
                        (tenant_id, "내 작업공간", now, now,
                         json.dumps({"workspaceType": "personal", "source": "verified_signup"}, separators=(",", ":"))),
                    )
                    cursor.execute(
                        f'INSERT INTO "{self._schema}".tenant_memberships '
                        '(tenant_id, issuer, subject, role, status, membership_version, membership_source, created_at, updated_at) '
                        "VALUES (%s, %s, %s, 'analyst', 'active', 1, 'verified_signup_default_workspace', %s, %s)",
                        (tenant_id, issuer, subject, now, now),
                    )
                    # The generated tenant is server-owned. Scope only the append,
                    # inside this transaction; SET LOCAL cannot leak to another checkout.
                    cursor.execute("SELECT set_config('realytics.tenant_id', %s, true)", (tenant_id,))
                    cursor.execute(
                        f'INSERT INTO "{self._schema}".tenant_identity_access_events '
                        '(tenant_id, event_id, event_type, actor_issuer, actor_subject, target_issuer, target_subject, event_payload, occurred_at) '
                        "VALUES (%s, %s, 'default_workspace_created', %s, %s, %s, %s, %s::jsonb, %s)",
                        (tenant_id, self._event_id(), issuer, subject, issuer, subject,
                         json.dumps({"role": "analyst", "membershipVersion": 1, "source": "verified_signup"}, separators=(",", ":")), now),
                    )
            return {"tenantId": tenant_id, "role": "analyst", "created": True}
        except DefaultWorkspaceError:
            raise
        except Exception as exc:
            raise DefaultWorkspaceUnavailable("default workspace authority is unavailable") from exc
