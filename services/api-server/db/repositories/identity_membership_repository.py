"""Read-only PostgreSQL authority for verified principals and memberships."""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from threading import Event, Lock
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
    tenant_status: str | None
    role: str
    status: str
    membership_version: int


@dataclass(frozen=True)
class PrincipalMembershipDirectory:
    principal: IdentityPrincipalRecord | None
    memberships: tuple[TenantMembershipRecord, ...]


@dataclass
class _DirectoryLookupFlight:
    """One process-local lookup shared only while the query is in flight."""

    completed: Event = field(default_factory=Event)
    result: PrincipalMembershipDirectory | None = None
    error: Exception | None = None
    followers: int = 0


_directory_lookup_flights: dict[tuple[Any, ...], _DirectoryLookupFlight] = {}
_directory_lookup_flights_lock = Lock()


class IdentityMembershipRepository(Protocol):
    def load_directory(
        self,
        *,
        issuer: str,
        subject: str,
    ) -> PrincipalMembershipDirectory:
        """Load one verified principal and every tenant-membership state."""


def _normalized_identity_key(*, issuer: str, subject: str) -> tuple[str, str]:
    normalized_issuer = str(issuer or "").strip().rstrip("/")
    normalized_subject = str(subject or "").strip()
    if not normalized_issuer or not normalized_subject:
        raise ValueError("issuer and subject are required")
    return normalized_issuer, normalized_subject


def _directory_lookup_key(
    repository: IdentityMembershipRepository,
    *,
    issuer: str,
    subject: str,
) -> tuple[Any, ...]:
    normalized_issuer, normalized_subject = _normalized_identity_key(
        issuer=issuer,
        subject=subject,
    )
    schema = getattr(repository, "_schema", None)
    transaction_factory = getattr(repository, "_transaction", None)
    authority_identity = (
        id(transaction_factory)
        if transaction_factory is not None
        else id(repository)
    )
    return (
        type(repository).__module__,
        type(repository).__qualname__,
        str(schema or ""),
        authority_identity,
        normalized_issuer,
        normalized_subject,
    )


def _raise_flight_error(error: Exception) -> None:
    try:
        cloned = type(error)(*error.args)
    except Exception as clone_error:
        raise IdentityMembershipRepositoryUnavailable(
            "identity membership lookup failed"
        ) from clone_error
    raise cloned from error


def load_directory_singleflight(
    repository: IdentityMembershipRepository,
    *,
    issuer: str,
    subject: str,
) -> PrincipalMembershipDirectory:
    """Coalesce an identical directory read only while its query is running.

    This deliberately is not a membership cache.  The flight is removed as
    soon as the leader publishes its result (or failure), so a later request
    must query PostgreSQL again and observes membership suspension/revocation.
    Waiting callers retain their own reference to the completed flight.
    """

    normalized_issuer, normalized_subject = _normalized_identity_key(
        issuer=issuer,
        subject=subject,
    )
    key = _directory_lookup_key(
        repository,
        issuer=normalized_issuer,
        subject=normalized_subject,
    )
    with _directory_lookup_flights_lock:
        flight = _directory_lookup_flights.get(key)
        if flight is None:
            flight = _DirectoryLookupFlight()
            _directory_lookup_flights[key] = flight
            is_leader = True
        else:
            is_leader = False
            flight.followers += 1

    if is_leader:
        try:
            flight.result = repository.load_directory(
                issuer=normalized_issuer,
                subject=normalized_subject,
            )
        except Exception as exc:
            # Publish provider failures so followers cannot remain blocked.
            # Every caller receives a separate exception object and remains
            # fail-closed.
            flight.error = exc
        finally:
            with _directory_lookup_flights_lock:
                if _directory_lookup_flights.get(key) is flight:
                    del _directory_lookup_flights[key]
                flight.completed.set()
    else:
        flight.completed.wait()

    if flight.error is not None:
        _raise_flight_error(flight.error)
    if flight.result is None:
        raise IdentityMembershipRepositoryUnavailable(
            "identity membership lookup completed without a result"
        )
    return flight.result


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
        normalized_issuer, normalized_subject = _normalized_identity_key(
            issuer=issuer,
            subject=subject,
        )

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
                t.status AS tenant_status,
                m.role,
                m.status,
                m.membership_version
            FROM "{self._schema}".principals AS p
            LEFT JOIN "{self._schema}".tenant_memberships AS m
              ON m.issuer = p.issuer
             AND m.subject = p.subject
            LEFT JOIN "{self._schema}".tenants AS t
              ON t.tenant_id = m.tenant_id
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
                tenant_status=(
                    str(row[8]) if row[8] is not None else None
                ),
                role=str(row[9]),
                status=str(row[10]),
                membership_version=int(row[11]),
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
    "load_directory_singleflight",
    "PostgresIdentityMembershipRepository",
    "PrincipalMembershipDirectory",
    "TenantMembershipRecord",
]
