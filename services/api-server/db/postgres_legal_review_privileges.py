"""Least-privilege role manifest for human legal governance writes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


LEGAL_REVIEW_ROLE_ENV = "REALYTICS_LEGAL_REVIEW_DATABASE_ROLE"
LEGAL_REVIEW_SCHEMA_ENV = "REALYTICS_DATABASE_SCHEMA"

LEGAL_REVIEW_WRITE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "legal_review_decisions": ("SELECT", "INSERT"),
    "legal_approval_delegations": ("SELECT", "INSERT"),
    "legal_required_claim_sets": ("SELECT", "INSERT"),
    "legal_required_claims": ("SELECT", "INSERT"),
    "project_legal_matters": ("SELECT", "INSERT"),
    "legal_waivers": ("SELECT", "INSERT"),
    "counsel_opinions": ("SELECT", "INSERT"),
    "legal_analysis_versions": ("SELECT", "INSERT"),
    "project_legal_annotations": ("SELECT", "INSERT"),
    "tenant_legal_term_aliases": ("SELECT", "INSERT"),
    "tenant_legal_term_alias_review_events": ("SELECT", "INSERT"),
    "project_legal_citation_review_versions": ("SELECT", "INSERT"),
    "project_legal_citation_review_events": ("SELECT", "INSERT"),
    "project_legal_relation_review_versions": ("SELECT", "INSERT"),
    "project_legal_relation_review_events": ("SELECT", "INSERT"),
}

LEGAL_REVIEW_READ_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "projects": ("SELECT",),
    "legal_source_providers": ("SELECT",),
    "legal_source_fetches": ("SELECT",),
    "legal_resources": ("SELECT",),
    "legal_resource_versions": ("SELECT",),
    "legal_resource_version_lineages": ("SELECT",),
    "legal_provisions": ("SELECT",),
    "legal_citations": ("SELECT",),
    "legal_relation_assertions": ("SELECT",),
    "legal_findings": ("SELECT",),
    "legal_query_runs": ("SELECT",),
    "legal_evidence_snapshots": ("SELECT",),
    "legal_evidence_snapshot_items": ("SELECT",),
    "project_legal_review_versions": ("SELECT",),
    "glossary_terms": ("SELECT",),
}

LEGAL_REVIEW_EXECUTE_FUNCTIONS = (
    "legal_try_timestamptz(TEXT)",
)
LEGAL_REVIEW_EXTENSION_SCHEMA = "extensions"
LEGAL_REVIEW_EXTENSION_EXECUTE_FUNCTIONS = (
    "digest(bytea, text)",
)

FORBIDDEN_LEGAL_REVIEW_PRIVILEGES = (
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


class LegalReviewPrivilegeError(RuntimeError):
    """The legal-review principal is missing or over-privileged."""


def validate_identifier(value: str, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise LegalReviewPrivilegeError(
            f"{label} must be a simple PostgreSQL identifier"
        )
    return normalized


def render_legal_review_role_sql(
    *,
    role_name: str,
    schema_name: str,
) -> tuple[str, ...]:
    role = validate_identifier(role_name, label="legal review role")
    schema = validate_identifier(schema_name, label="database schema")
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
            "NOINHERIT NOREPLICATION NOBYPASSRLS;"
        ),
        f'REVOKE CREATE ON SCHEMA "{schema}" FROM "{role}";',
        f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}";',
        (
            f'REVOKE CREATE ON SCHEMA "{LEGAL_REVIEW_EXTENSION_SCHEMA}" '
            f'FROM "{role}";'
        ),
        (
            f'GRANT USAGE ON SCHEMA "{LEGAL_REVIEW_EXTENSION_SCHEMA}" '
            f'TO "{role}";'
        ),
        (
            f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "{schema}" '
            f'FROM "{role}";'
        ),
        (
            f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "{schema}" '
            f'FROM "{role}";'
        ),
        (
            f'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA "{schema}" '
            f'FROM "{role}";'
        ),
    ]
    privileges = {
        **LEGAL_REVIEW_READ_PRIVILEGES,
        **LEGAL_REVIEW_WRITE_PRIVILEGES,
    }
    for table, grants in privileges.items():
        qualified = f'"{schema}"."{table}"'
        statements.append(
            f'GRANT {", ".join(grants)} ON TABLE {qualified} TO "{role}";'
        )
    for signature in LEGAL_REVIEW_EXECUTE_FUNCTIONS:
        statements.append(
            "GRANT EXECUTE ON FUNCTION "
            f'"{schema}".{signature} TO "{role}";'
        )
    for signature in LEGAL_REVIEW_EXTENSION_EXECUTE_FUNCTIONS:
        statements.append(
            "GRANT EXECUTE ON FUNCTION "
            f'"{LEGAL_REVIEW_EXTENSION_SCHEMA}".{signature} TO "{role}";'
        )
    return tuple(statements)


def provision_legal_review_role(
    connection: Any,
    *,
    role_name: str,
    schema_name: str,
    migration_lock_key: int,
) -> None:
    statements = render_legal_review_role_sql(
        role_name=role_name,
        schema_name=schema_name,
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (migration_lock_key,),
        )
        for statement in statements:
            cursor.execute(statement)


def audit_legal_review_role(
    cursor: Any,
    *,
    role_name: str,
    schema_name: str,
    require_login: bool = False,
) -> tuple[list[str], list[str]]:
    """Audit the provisioned NOLOGIN role or the approved DSN login."""

    role = validate_identifier(role_name, label="legal review role")
    schema = validate_identifier(schema_name, label="database schema")
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
    if require_login and not flags[0]:
        missing.append(f"role:{role}:LOGIN")
    if any(bool(value) for value in flags[1:]) or (flags[0] and not require_login):
        forbidden.append(f"role:{role}:elevated_attribute")

    cursor.execute(
        """
        SELECT parent.rolname
        FROM pg_auth_members AS membership
        JOIN pg_roles AS child ON child.oid = membership.member
        JOIN pg_roles AS parent ON parent.oid = membership.roleid
        WHERE child.rolname = %s
        ORDER BY parent.rolname
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
        SELECT
            has_schema_privilege(%s, %s, 'USAGE'),
            has_schema_privilege(%s, %s, 'CREATE')
        """,
        (
            role,
            LEGAL_REVIEW_EXTENSION_SCHEMA,
            role,
            LEGAL_REVIEW_EXTENSION_SCHEMA,
        ),
    )
    extension_schema_flags = cursor.fetchone()
    if (
        extension_schema_flags is None
        or not bool(extension_schema_flags[0])
    ):
        missing.append(f"{LEGAL_REVIEW_EXTENSION_SCHEMA}.USAGE")
    if extension_schema_flags is not None and bool(
        extension_schema_flags[1]
    ):
        forbidden.append(f"{LEGAL_REVIEW_EXTENSION_SCHEMA}.CREATE")

    for signature in LEGAL_REVIEW_EXTENSION_EXECUTE_FUNCTIONS:
        qualified = f'"{LEGAL_REVIEW_EXTENSION_SCHEMA}".{signature}'
        cursor.execute(
            "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
            (role, qualified),
        )
        function_flag = cursor.fetchone()
        if function_flag is None or not bool(function_flag[0]):
            missing.append(f"{qualified}.EXECUTE")

    expected = {
        (table, privilege)
        for table, privileges in {
            **LEGAL_REVIEW_READ_PRIVILEGES,
            **LEGAL_REVIEW_WRITE_PRIVILEGES,
        }.items()
        for privilege in privileges
    }
    cursor.execute(
        """
        SELECT table_name, privilege_type
        FROM information_schema.role_table_grants
        WHERE grantee = %s
          AND table_schema = %s
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
    "FORBIDDEN_LEGAL_REVIEW_PRIVILEGES",
    "LEGAL_REVIEW_EXTENSION_EXECUTE_FUNCTIONS",
    "LEGAL_REVIEW_EXTENSION_SCHEMA",
    "LEGAL_REVIEW_READ_PRIVILEGES",
    "LEGAL_REVIEW_EXECUTE_FUNCTIONS",
    "LEGAL_REVIEW_ROLE_ENV",
    "LEGAL_REVIEW_SCHEMA_ENV",
    "LEGAL_REVIEW_WRITE_PRIVILEGES",
    "LegalReviewPrivilegeError",
    "audit_legal_review_role",
    "provision_legal_review_role",
    "render_legal_review_role_sql",
    "validate_identifier",
]
