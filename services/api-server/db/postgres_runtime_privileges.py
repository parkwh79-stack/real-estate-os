from __future__ import annotations

"""Least-privilege PostgreSQL grants for normalized Parser V2.

The role name is deployment-specific, so these grants deliberately do not live
in a numbered schema migration.  The deployment runner applies this manifest
after migrations and before the API readiness probe.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any


RUNTIME_ROLE_ENV = "REALYTICS_RUNTIME_DATABASE_ROLE"
RUNTIME_SCHEMA_ENV = "REALYTICS_DATABASE_SCHEMA"
DEFAULT_RUNTIME_SCHEMA = "public"

# Parser tables are owned by the migration role and are append-only.  The
# runtime role receives only the operations exercised by the normalized
# repository.  Empty tuples are intentional: the table is RLS-protected and
# reserved for a future governed workflow, but the current runtime does not use
# it yet.
PARSER_V2_TABLE_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "parser_document_versions": ("SELECT", "INSERT"),
    "parser_project_profiles": ("SELECT", "INSERT"),
    "parser_runs": ("SELECT", "INSERT"),
    "parser_run_events": ("SELECT", "INSERT"),
    "parser_candidate_set_seals": ("SELECT", "INSERT"),
    "parsed_fact_candidates": ("SELECT", "INSERT"),
    "parser_candidate_inputs": ("SELECT", "INSERT"),
    "parser_validation_findings": ("INSERT",),
    "parser_validation_finding_events": (),
    "parser_review_decisions": ("SELECT", "INSERT"),
    "parser_source_conflicts": ("SELECT", "INSERT"),
    "parser_source_conflict_events": ("SELECT", "INSERT"),
    "parser_conflict_candidates": ("SELECT", "INSERT"),
    "parser_review_decision_conflicts": ("INSERT",),
    "parser_canonical_selections": ("SELECT", "INSERT"),
    "parser_approved_snapshots": ("SELECT", "INSERT"),
    "parser_snapshot_selections": ("SELECT", "INSERT"),
    "parser_snapshot_source_runs": ("INSERT",),
    "parser_validity_events": ("SELECT", "INSERT"),
}

# These objects are shared with existing application paths.  Provisioning is
# additive here so Parser V2 hardening cannot accidentally revoke permissions
# required by project CRUD or calculation persistence.  The broader application
# role manifest remains a separate deployment responsibility.
PARSER_V2_SUPPORT_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "projects": ("SELECT",),
    "calculation_runs": ("SELECT",),
}

# These objects have a complete, known runtime contract and can be managed
# exactly.  The outbox dispatcher needs INSERT for idempotent enqueue, SELECT for
# claim/read, and UPDATE for lease/retry/published transitions.
PARSER_V2_MANAGED_SUPPORT_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "schema_migrations": ("SELECT",),
    "runtime_outbox_events": ("SELECT", "INSERT", "UPDATE"),
}

FORBIDDEN_APPEND_ONLY_PRIVILEGES = (
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
FORBIDDEN_MANAGED_SUPPORT_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "schema_migrations": (
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ),
    "runtime_outbox_events": ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"),
}

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


class RuntimePrivilegeProvisioningError(RuntimeError):
    """The requested runtime role cannot be safely provisioned."""


def validate_database_identifier(value: str, *, label: str) -> str:
    identifier = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(identifier):
        raise RuntimePrivilegeProvisioningError(
            f"{label} must be a simple PostgreSQL identifier (1-63 characters)"
        )
    return identifier


def assert_current_schema(cursor: Any, *, schema_name: str) -> str:
    """Fail before DDL/grants when the DSN search path targets another schema."""

    expected = validate_database_identifier(
        schema_name,
        label="database schema",
    )
    cursor.execute("SELECT current_schema()")
    row = cursor.fetchone()
    actual = str(row[0] or "") if row else ""
    if actual != expected:
        raise RuntimePrivilegeProvisioningError(
            "PostgreSQL DSN search_path targets schema "
            f"{actual!r}; expected {expected!r}"
        )
    return actual


def required_table_privileges() -> dict[str, tuple[str, ...]]:
    """Return every positive privilege required by normalized Parser V2."""

    return {
        **PARSER_V2_TABLE_PRIVILEGES,
        **PARSER_V2_SUPPORT_PRIVILEGES,
        **PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
    }


def forbidden_table_privileges() -> dict[str, tuple[str, ...]]:
    """Return effective privileges that the runtime role must not possess."""

    return {
        **{
            table: FORBIDDEN_APPEND_ONLY_PRIVILEGES
            for table in PARSER_V2_TABLE_PRIVILEGES
        },
        **dict(FORBIDDEN_MANAGED_SUPPORT_PRIVILEGES),
    }


def audit_runtime_privileges(
    cursor: Any,
    *,
    schema_name: str,
) -> tuple[list[str], list[str]]:
    """Return missing required and unexpectedly granted effective privileges."""

    schema = validate_database_identifier(schema_name, label="database schema")
    missing: list[str] = []
    forbidden: list[str] = []

    cursor.execute(
        "SELECT has_schema_privilege(current_user, %s, 'USAGE')",
        (schema,),
    )
    schema_usage = cursor.fetchone()
    if not schema_usage or not bool(schema_usage[0]):
        missing.append(f"schema:{schema}.USAGE")

    cursor.execute(
        "SELECT has_schema_privilege(current_user, %s, 'CREATE')",
        (schema,),
    )
    schema_create = cursor.fetchone()
    if schema_create and bool(schema_create[0]):
        forbidden.append(f"schema:{schema}.CREATE")

    for table, privileges in required_table_privileges().items():
        for privilege in privileges:
            if not _has_table_privilege(
                cursor,
                schema=schema,
                table=table,
                privilege=privilege,
            ):
                missing.append(f"{table}.{privilege}")

    for table, privileges in forbidden_table_privileges().items():
        for privilege in privileges:
            if _has_table_privilege(
                cursor,
                schema=schema,
                table=table,
                privilege=privilege,
            ):
                forbidden.append(f"{table}.{privilege}")

    return sorted(missing), sorted(forbidden)


def provision_parser_v2_runtime_role(
    conn: Any,
    *,
    role_name: str,
    schema_name: str = DEFAULT_RUNTIME_SCHEMA,
    migration_lock_key: int,
) -> None:
    """Apply the Parser V2 role manifest inside the caller's transaction.

    The caller owns commit/rollback.  Parser tables and the two managed support
    tables are reset to the exact allowlist.  Shared project/calculation tables
    receive additive SELECT only, preserving the rest of the application's
    independently managed grants.
    """

    role = validate_database_identifier(role_name, label="runtime role")
    schema = validate_database_identifier(schema_name, label="database schema")
    scoped_tables = tuple(
        dict.fromkeys(
            (
                *PARSER_V2_TABLE_PRIVILEGES,
                *PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
                *PARSER_V2_SUPPORT_PRIVILEGES,
            )
        )
    )

    with conn.cursor() as cursor:
        assert_current_schema(cursor, schema_name=schema)
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (migration_lock_key,))
        cursor.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolbypassrls "
            "FROM pg_roles WHERE rolname = %s",
            (role,),
        )
        state = cursor.fetchone()
        if state is None:
            raise RuntimePrivilegeProvisioningError(
                f"PostgreSQL runtime role does not exist: {role}"
            )
        if (
            bool(state[0])
            or bool(state[1])
            or bool(state[2])
            or bool(state[3])
            or bool(state[4])
        ):
            raise RuntimePrivilegeProvisioningError(
                "PostgreSQL runtime role must be NOSUPERUSER, NOCREATEDB, "
                "NOCREATEROLE, NOINHERIT, and NOBYPASSRLS before grants are applied"
            )

        cursor.execute(
            """
            SELECT c.relname
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            JOIN pg_roles AS owner_role ON owner_role.oid = c.relowner
            WHERE n.nspname = %s AND owner_role.rolname = %s
              AND c.relname = ANY(%s)
            ORDER BY c.relname
            """,
            (schema, role, list(scoped_tables)),
        )
        owned = [str(row[0]) for row in cursor.fetchall()]
        if owned:
            raise RuntimePrivilegeProvisioningError(
                "runtime role must not own Parser V2 runtime objects: "
                + ", ".join(owned)
            )

        role_identifier = _quoted_identifier(role)
        schema_identifier = _quoted_identifier(schema)
        cursor.execute(
            f"REVOKE CREATE ON SCHEMA {schema_identifier} FROM PUBLIC"
        )
        cursor.execute(
            "REVOKE ALL PRIVILEGES ON SCHEMA "
            f"{schema_identifier} FROM {role_identifier}"
        )
        cursor.execute(
            f"GRANT USAGE ON SCHEMA {schema_identifier} TO {role_identifier}"
        )
        cursor.execute(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA "
            f"{schema_identifier} REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC"
        )

        exact_privileges = {
            **PARSER_V2_TABLE_PRIVILEGES,
            **PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
        }
        for table, privileges in exact_privileges.items():
            relation = f"{schema_identifier}.{_quoted_identifier(table)}"
            cursor.execute(
                f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM PUBLIC"
            )
            cursor.execute(
                "REVOKE ALL PRIVILEGES ON TABLE "
                f"{relation} FROM {role_identifier}"
            )
            if privileges:
                _grant(cursor, relation, role_identifier, privileges)

        for table, privileges in PARSER_V2_SUPPORT_PRIVILEGES.items():
            relation = f"{schema_identifier}.{_quoted_identifier(table)}"
            _grant(cursor, relation, role_identifier, privileges)


def _grant(
    cursor: Any,
    relation: str,
    role_identifier: str,
    privileges: Sequence[str],
) -> None:
    privilege_sql = ", ".join(privileges)
    cursor.execute(
        f"GRANT {privilege_sql} ON TABLE {relation} TO {role_identifier}"
    )


def _quoted_identifier(value: str) -> str:
    """Quote a previously validated PostgreSQL identifier.

    Keeping SQL composition dependency-free lets the privilege manifest be
    unit-tested without the optional psycopg driver.  Runtime connection setup
    still requires psycopg before this provisioner can reach the database.
    """

    return '"' + value.replace('"', '""') + '"'


def _has_table_privilege(
    cursor: Any,
    *,
    schema: str,
    table: str,
    privilege: str,
) -> bool:
    cursor.execute(
        """
        SELECT CASE
            WHEN relation_oid IS NULL THEN FALSE
            ELSE has_table_privilege(current_user, relation_oid, %s)
        END
        FROM (
            SELECT to_regclass(
                format('%%I.%%I', %s::text, %s::text)
            ) AS relation_oid
        ) AS runtime_relation
        """,
        (privilege, schema, table),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


__all__ = [
    "DEFAULT_RUNTIME_SCHEMA",
    "FORBIDDEN_APPEND_ONLY_PRIVILEGES",
    "PARSER_V2_MANAGED_SUPPORT_PRIVILEGES",
    "PARSER_V2_SUPPORT_PRIVILEGES",
    "PARSER_V2_TABLE_PRIVILEGES",
    "RUNTIME_ROLE_ENV",
    "RUNTIME_SCHEMA_ENV",
    "RuntimePrivilegeProvisioningError",
    "assert_current_schema",
    "audit_runtime_privileges",
    "forbidden_table_privileges",
    "provision_parser_v2_runtime_role",
    "required_table_privileges",
    "validate_database_identifier",
]
