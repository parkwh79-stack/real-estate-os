"""Read-only PostgreSQL authority for verified principals and memberships."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from db.postgres_runtime_pool import identity_transaction
from db.postgres_runtime_privileges import validate_database_identifier


TransactionFactory = Callable[[], AbstractContextManager[Any]]


class IdentityMembershipRepositoryUnavailable(RuntimeError):
    """The normalized identity authority could not be queried safely."""


@dataclass(frozen=True)
class IdentityPrincipalRecord:
    issuer: str
    subject: str
    principal_type: str
    status: str
    email: str | None
    display_name: str | None
    legacy_user_id: str | None


@dataclass(frozen=True)
class TenantMembershipRecord:
    tenant_id: str
    role: str
    status: str
    membership_version: int


@dataclass(frozen=True)
class PrincipalMembershipDirectory:
    principal: IdentityPrincipalRecord | None
    memberships: tuple[TenantMembershipRecord, ...]


class IdentityMembershipRepository(Protocol):
    def load_directory(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> PrincipalMembershipDirectory:
        """Load one verified principal and every tenant-membership state."""


class PostgresIdentityMembershipRepository:
    """Query migrated identity tables without schema creation or mutation.

    ``issuer`` and ``subject`` are always parameters.  Only the validated schema
    identifier is interpolated, keeping the lookup safe while allowing an
    isolated REALYTICS schema in Supabase.
    """

    def __init__(
        self,
        *,
        transaction_factory: TransactionFactory = identity_transaction,
        schema_name: str | None = None,
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

    def load_directory(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> PrincipalMembershipDirectory:
        # Migration 0012 stores ``btrim(issuer)`` after removing every trailing
        # slash. Apply the same canonicalization before the composite-PK lookup.
        normalized_issuer = str(issuer or "").strip().rstrip("/")
        normalized_subject = str(subject or "").strip()
        if not normalized_issuer or not normalized_subject:
            raise ValueError("issuer and subject are required")

        sql = f"""
            SELECT
                p.issuer,
                p.subject,
                p.principal_type,
                p.status,
                p.email,
                p.display_name,
                p.legacy_user_id,
                m.tenant_id,
                m.role,
                m.status,
                m.membership_version
            FROM "{self._schema}".principals AS p
            LEFT JOIN "{self._schema}".tenant_memberships AS m
              ON m.issuer = p.issuer
             AND m.subject = p.subject
            WHERE p.issuer = %s
              AND p.subject = %s
            ORDER BY m.tenant_id
        """
        try:
            with self._transaction() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(sql, (normalized_issuer, normalized_subject))
                    rows = cursor.fetchall()
        except Exception as exc:
            raise IdentityMembershipRepositoryUnavailable(
                "normalized identity membership authority is unavailable"
            ) from exc

        if not rows:
            return PrincipalMembershipDirectory(
                principal=None,
                memberships=(),
            )

        first = rows[0]
        principal = IdentityPrincipalRecord(
            issuer=str(first[0]),
            subject=str(first[1]),
            principal_type=str(first[2]),
            status=str(first[3]),
            email=str(first[4]) if first[4] is not None else None,
            display_name=(
                str(first[5]) if first[5] is not None else None
            ),
            legacy_user_id=(
                str(first[6]) if first[6] is not None else None
            ),
        )
        memberships = tuple(
            TenantMembershipRecord(
                tenant_id=str(row[7]),
                role=str(row[8]),
                status=str(row[9]),
                membership_version=int(row[10]),
            )
            for row in rows
            if row[7] is not None
        )
        return PrincipalMembershipDirectory(
            principal=principal,
            memberships=memberships,
        )


__all__ = [
    "IdentityMembershipRepository",
    "IdentityMembershipRepositoryUnavailable",
    "IdentityPrincipalRecord",
    "PostgresIdentityMembershipRepository",
    "PrincipalMembershipDirectory",
    "TenantMembershipRecord",
]
