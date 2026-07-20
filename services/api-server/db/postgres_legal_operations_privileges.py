"""Least-privilege role manifest for Legal Provider operations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


LEGAL_OPERATIONS_ROLE_ENV = "REALYTICS_LEGAL_OPERATIONS_DATABASE_ROLE"
LEGAL_OPERATIONS_SCHEMA_ENV = "REALYTICS_DATABASE_SCHEMA"

LEGAL_OPERATIONS_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "legal_provider_quarantine": ("SELECT", "INSERT"),
    "legal_provider_dead_letters": ("SELECT", "INSERT"),
    "legal_provider_metrics": ("SELECT", "INSERT"),
    "legal_provider_replay_objects": ("SELECT", "INSERT"),
    "legal_provider_replay_approval_events": ("SELECT", "INSERT"),
    "legal_provider_replay_requests": ("SELECT", "INSERT"),
    "legal_provider_replay_events": ("SELECT", "INSERT"),
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


class LegalOperationsPrivilegeError(RuntimeError):
    """The operations principal is missing or over-privileged."""


def _identifier(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise LegalOperationsPrivilegeError(
            f"{label} must be a simple PostgreSQL identifier"
        )
    return normalized


def render_legal_operations_role_sql(
    *,
    role_name: str,
    schema_name: str,
) -> tuple[str, ...]:
    role = _identifier(role_name, label="legal operations role")
    schema = _identifier(schema_name, label="database schema")
    statements = [
        (
            "DO $role$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f'THEN CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB '
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS; "
            "END IF; END $role$;"
        ),
        (
            f'ALTER ROLE "{role}" NOSUPERUSER NOCREATEDB NOCREATEROLE '
            "NOINHERIT NOREPLICATION NOBYPASSRLS"
        ),
        f'REVOKE CREATE ON SCHEMA "{schema}" FROM "{role}"',
        f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"',
        (
            f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{schema}" '
            f'FROM "{role}"'
        ),
        (
            f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "{schema}" '
            f'FROM "{role}"'
        ),
        (
            f'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "{schema}" '
            f'FROM "{role}"'
        ),
    ]
    for table, privileges in LEGAL_OPERATIONS_PRIVILEGES.items():
        grants = ", ".join(privileges)
        statements.append(
            f'GRANT {grants} ON TABLE "{schema}"."{table}" TO "{role}"'
        )
    return tuple(statements)


def provision_legal_operations_role(
    connection: Any,
    *,
    role_name: str,
    schema_name: str,
    migration_lock_key: int,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (migration_lock_key,),
        )
        for statement in render_legal_operations_role_sql(
            role_name=role_name,
            schema_name=schema_name,
        ):
            cursor.execute(statement)


def audit_legal_operations_role(
    cursor: Any,
    *,
    role_name: str,
    schema_name: str,
) -> tuple[list[str], list[str]]:
    role = _identifier(role_name, label="legal operations role")
    schema = _identifier(schema_name, label="database schema")
    expected = {
        (table, privilege)
        for table, privileges in LEGAL_OPERATIONS_PRIVILEGES.items()
        for privilege in privileges
    }
    missing: list[str] = []
    forbidden: list[str] = []
    cursor.execute(
        """
        SELECT
            rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit,
            rolreplication, rolbypassrls
        FROM pg_roles
        WHERE rolname = %s
        """,
        (role,),
    )
    flags = cursor.fetchone()
    if flags is None:
        return [f"role:{role}"], []
    if any(bool(value) for value in flags):
        forbidden.append(f"role:{role}:elevated_attribute")
    cursor.execute(
        """
        SELECT parent.rolname
        FROM pg_auth_members AS membership
        JOIN pg_roles AS child ON child.oid = membership.member
        JOIN pg_roles AS parent ON parent.oid = membership.roleid
        WHERE child.rolname = %s
        """,
        (role,),
    )
    for row in cursor.fetchall():
        forbidden.append(f"role:{role}:member_of:{row[0]}")
    cursor.execute(
        """
        SELECT
            has_schema_privilege(%s, %s, 'USAGE'),
            has_schema_privilege(%s, %s, 'CREATE')
        """,
        (role, schema, role, schema),
    )
    schema_flags = cursor.fetchone()
    if schema_flags is None or not bool(schema_flags[0]):
        missing.append(f"{schema}.USAGE")
    if schema_flags is not None and bool(schema_flags[1]):
        forbidden.append(f"{schema}.CREATE")
    cursor.execute(
        """
        SELECT table_name, privilege_type
        FROM information_schema.role_table_grants
        WHERE grantee = %s AND table_schema = %s
        """,
        (role, schema),
    )
    actual = {
        (str(row[0]), str(row[1]).upper())
        for row in cursor.fetchall()
    }
    for table, privilege in sorted(expected - actual):
        missing.append(f"{schema}.{table}.{privilege}")
    for table, privilege in sorted(actual - expected):
        forbidden.append(f"{schema}.{table}.{privilege}")
    return missing, forbidden


__all__ = [
    "LEGAL_OPERATIONS_PRIVILEGES",
    "LEGAL_OPERATIONS_ROLE_ENV",
    "LEGAL_OPERATIONS_SCHEMA_ENV",
    "LegalOperationsPrivilegeError",
    "audit_legal_operations_role",
    "provision_legal_operations_role",
    "render_legal_operations_role_sql",
]
