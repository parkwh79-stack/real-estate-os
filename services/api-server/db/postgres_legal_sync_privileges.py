from __future__ import annotations

"""Least-privilege PostgreSQL role for official legal corpus maintenance."""

import re
from collections.abc import Mapping
from typing import Any


LEGAL_SYNC_ROLE_ENV = "REALYTICS_LEGAL_SYNC_DATABASE_ROLE"
LEGAL_SYNC_SCHEMA_ENV = "REALYTICS_DATABASE_SCHEMA"
LEGAL_SYNC_POSTGIS_SCHEMA_ENV = "REALYTICS_POSTGIS_SCHEMA"

LEGAL_SYNC_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "legal_source_providers": ("SELECT", "INSERT"),
    "legal_sync_runs": ("SELECT", "INSERT"),
    "legal_sync_run_events": ("SELECT", "INSERT"),
    "legal_sync_checkpoint_events": ("SELECT", "INSERT"),
    "legal_sync_scope_lease_events": ("SELECT", "INSERT"),
    "legal_corpus_scope_completion_attestations": (
        "SELECT",
        "INSERT",
    ),
    "legal_source_fetches": ("SELECT", "INSERT"),
    "legal_resources": ("SELECT", "INSERT"),
    "legal_resource_identity_crosswalks": ("SELECT", "INSERT"),
    "legal_resource_versions": ("SELECT", "INSERT"),
    "legal_resource_version_lineages": ("SELECT", "INSERT"),
    "legal_provisions": ("SELECT", "INSERT"),
    "legal_citations": ("SELECT", "INSERT"),
    "legal_relation_assertions": ("SELECT", "INSERT"),
    "legal_term_definitions": ("SELECT", "INSERT"),
    "legal_term_provision_links": ("SELECT", "INSERT"),
    "legal_semantic_artifacts": ("SELECT", "INSERT"),
    "legal_raw_object_reconciliation_runs": ("SELECT", "INSERT"),
    "legal_raw_object_reconciliation_items": ("SELECT", "INSERT"),
}

FORBIDDEN_LEGAL_SYNC_PRIVILEGES = (
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


class LegalSyncPrivilegeError(RuntimeError):
    """The corpus-maintenance role is absent or over-privileged."""


def validate_identifier(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise LegalSyncPrivilegeError(
            f"{label} must be a simple PostgreSQL identifier"
        )
    return normalized


def render_legal_sync_role_sql(
    *,
    role_name: str,
    schema_name: str,
    postgis_schema_name: str = "extensions",
) -> tuple[str, ...]:
    role = validate_identifier(role_name, label="legal sync role")
    schema = validate_identifier(schema_name, label="database schema")
    postgis_schema = validate_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    statements = [
        (
            "DO $role$ BEGIN "
            f'IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = \'{role}\') '
            f'THEN CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB '
            "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS; "
            "END IF; END $role$;"
        ),
        (
            f'ALTER ROLE "{role}" NOSUPERUSER NOCREATEDB NOCREATEROLE '
            "NOINHERIT NOREPLICATION NOBYPASSRLS;"
        ),
        f'REVOKE CREATE ON SCHEMA "{schema}" FROM "{role}";',
        f'REVOKE CREATE ON SCHEMA "{postgis_schema}" FROM "{role}";',
        f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}";',
        f'GRANT USAGE ON SCHEMA "{postgis_schema}" TO "{role}";',
    ]
    for table, privileges in LEGAL_SYNC_TABLE_PRIVILEGES.items():
        qualified = f'"{schema}"."{table}"'
        statements.append(
            f'REVOKE ALL PRIVILEGES ON TABLE {qualified} FROM "{role}";'
        )
        statements.append(
            f'GRANT {", ".join(privileges)} ON TABLE {qualified} TO "{role}";'
        )
    return tuple(statements)


def provision_legal_sync_role(
    connection: Any,
    *,
    role_name: str,
    schema_name: str,
    postgis_schema_name: str = "extensions",
    migration_lock_key: int,
) -> None:
    statements = render_legal_sync_role_sql(
        role_name=role_name,
        schema_name=schema_name,
        postgis_schema_name=postgis_schema_name,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (migration_lock_key,),
        )
        for statement in statements:
            cursor.execute(statement)


def audit_legal_sync_role(
    cursor: Any,
    *,
    schema_name: str,
    postgis_schema_name: str = "extensions",
) -> tuple[list[str], list[str]]:
    """Audit the connected sync principal's effective privileges."""

    schema = validate_identifier(schema_name, label="database schema")
    postgis_schema = validate_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    missing: list[str] = []
    forbidden: list[str] = []

    cursor.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, "
        "rolbypassrls, rolinherit "
        "FROM pg_roles WHERE rolname = current_user"
    )
    role_flags = cursor.fetchone()
    if role_flags is None:
        forbidden.append("role.missing")
    elif any(bool(value) for value in role_flags):
        forbidden.append("role.elevated_attributes")

    for scoped_schema in dict.fromkeys((schema, postgis_schema)):
        cursor.execute(
            "SELECT has_schema_privilege(current_user, %s, 'USAGE'), "
            "has_schema_privilege(current_user, %s, 'CREATE')",
            (scoped_schema, scoped_schema),
        )
        row = cursor.fetchone()
        if not row or not bool(row[0]):
            missing.append(f"schema:{scoped_schema}.USAGE")
        if row and bool(row[1]):
            forbidden.append(f"schema:{scoped_schema}.CREATE")

    for table, required in LEGAL_SYNC_TABLE_PRIVILEGES.items():
        qualified = f"{schema}.{table}"
        for privilege in required:
            cursor.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (qualified, privilege),
            )
            row = cursor.fetchone()
            if not row or not bool(row[0]):
                missing.append(f"{qualified}.{privilege}")
        for privilege in FORBIDDEN_LEGAL_SYNC_PRIVILEGES:
            cursor.execute(
                "SELECT has_table_privilege(current_user, %s, %s)",
                (qualified, privilege),
            )
            row = cursor.fetchone()
            if row and bool(row[0]):
                forbidden.append(f"{qualified}.{privilege}")
    return missing, forbidden


__all__ = [
    "FORBIDDEN_LEGAL_SYNC_PRIVILEGES",
    "LEGAL_SYNC_POSTGIS_SCHEMA_ENV",
    "LEGAL_SYNC_ROLE_ENV",
    "LEGAL_SYNC_SCHEMA_ENV",
    "LEGAL_SYNC_TABLE_PRIVILEGES",
    "LegalSyncPrivilegeError",
    "audit_legal_sync_role",
    "provision_legal_sync_role",
    "render_legal_sync_role_sql",
    "validate_identifier",
]
