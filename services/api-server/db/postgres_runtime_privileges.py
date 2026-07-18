from __future__ import annotations

"""Least-privilege PostgreSQL grants for normalized runtime domains.

The role name is deployment-specific, so these grants deliberately do not live
in a numbered schema migration.  The deployment runner applies this manifest
after migrations and before the API readiness probe.
"""

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any


RUNTIME_ROLE_ENV = "REALYTICS_RUNTIME_DATABASE_ROLE"
RUNTIME_SCHEMA_ENV = "REALYTICS_DATABASE_SCHEMA"
POSTGIS_SCHEMA_ENV = "REALYTICS_POSTGIS_SCHEMA"
REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV = (
    "REALYTICS_REVOKE_PUBLIC_DATABASE_TEMPORARY"
)
DEFAULT_RUNTIME_SCHEMA = "public"
DEFAULT_POSTGIS_SCHEMA = "extensions"

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

# The API consumes the verified public/legal corpus but never mutates it.
# Synchronization and corpus ingestion run under a separately governed worker
# role.  Keeping the synchronization ledger visible lets the API disclose
# coverage/freshness without inheriting ingestion authority.
LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "legal_source_providers": ("SELECT",),
    "legal_sync_runs": ("SELECT",),
    "legal_sync_run_events": ("SELECT",),
    "legal_sync_scope_lease_events": ("SELECT",),
    "legal_sync_checkpoint_events": ("SELECT",),
    "legal_corpus_scope_completion_attestations": ("SELECT",),
    "legal_source_fetches": ("SELECT",),
    "legal_resources": ("SELECT",),
    "legal_resource_identity_crosswalks": ("SELECT",),
    "legal_resource_versions": ("SELECT",),
    "legal_resource_version_lineages": ("SELECT",),
    "legal_provisions": ("SELECT",),
    "legal_citations": ("SELECT",),
    "legal_relation_assertions": ("SELECT",),
    "legal_term_definitions": ("SELECT",),
    "legal_term_provision_links": ("SELECT",),
    "legal_semantic_artifacts": ("SELECT",),
}

# Tenant data is an append-only audit/evidence overlay.  RLS supplies tenant
# isolation and the runtime role may only read or append; review history is
# never rewritten or deleted.
LEGAL_INTELLIGENCE_TENANT_PRIVILEGES: Mapping[str, tuple[str, ...]] = {
    "legal_query_runs": ("SELECT", "INSERT"),
    "legal_query_run_events": ("SELECT", "INSERT"),
    "project_legal_resource_links": ("SELECT", "INSERT"),
    "legal_findings": ("SELECT", "INSERT"),
    "legal_evidence_snapshots": ("SELECT", "INSERT"),
    "legal_evidence_snapshot_items": ("SELECT", "INSERT"),
    "legal_review_decisions": ("SELECT", "INSERT"),
}

# The canonical glossary remains owned by its governed curation workflow.  The
# legal runtime can resolve term IDs but cannot promote or rewrite definitions.
LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES: Mapping[
    str, tuple[str, ...]
] = {
    "glossary_terms": ("SELECT",),
}

FORBIDDEN_APPEND_ONLY_PRIVILEGES = (
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
FORBIDDEN_READ_ONLY_PRIVILEGES = (
    "INSERT",
    *FORBIDDEN_APPEND_ONLY_PRIVILEGES,
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
    """Return every positive privilege required by normalized runtime domains."""

    return {
        **PARSER_V2_TABLE_PRIVILEGES,
        **PARSER_V2_SUPPORT_PRIVILEGES,
        **PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
        **LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES,
        **LEGAL_INTELLIGENCE_TENANT_PRIVILEGES,
        **LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES,
    }


def forbidden_table_privileges() -> dict[str, tuple[str, ...]]:
    """Return effective privileges that the runtime role must not possess."""

    return {
        **{
            table: FORBIDDEN_APPEND_ONLY_PRIVILEGES
            for table in PARSER_V2_TABLE_PRIVILEGES
        },
        **{
            table: FORBIDDEN_READ_ONLY_PRIVILEGES
            for table in LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES
        },
        **{
            table: FORBIDDEN_APPEND_ONLY_PRIVILEGES
            for table in LEGAL_INTELLIGENCE_TENANT_PRIVILEGES
        },
        **{
            table: FORBIDDEN_READ_ONLY_PRIVILEGES
            for table in LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES
        },
        **dict(FORBIDDEN_MANAGED_SUPPORT_PRIVILEGES),
    }


def audit_runtime_privileges(
    cursor: Any,
    *,
    schema_name: str,
    postgis_schema_name: str = DEFAULT_POSTGIS_SCHEMA,
    require_no_database_temporary: bool = False,
) -> tuple[list[str], list[str]]:
    """Return missing required and unexpectedly granted effective privileges."""

    schema = validate_database_identifier(schema_name, label="database schema")
    postgis_schema = validate_database_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    missing: list[str] = []
    forbidden: list[str] = []

    if require_no_database_temporary:
        cursor.execute(
            "SELECT has_database_privilege("
            "current_user, current_database(), 'TEMPORARY'"
            ")"
        )
        database_temporary = cursor.fetchone()
        if database_temporary and bool(database_temporary[0]):
            forbidden.append("database.TEMPORARY")

    for scoped_schema in dict.fromkeys((schema, postgis_schema)):
        cursor.execute(
            "SELECT has_schema_privilege(current_user, %s, 'USAGE')",
            (scoped_schema,),
        )
        schema_usage = cursor.fetchone()
        if not schema_usage or not bool(schema_usage[0]):
            missing.append(f"schema:{scoped_schema}.USAGE")

        cursor.execute(
            "SELECT has_schema_privilege(current_user, %s, 'CREATE')",
            (scoped_schema,),
        )
        schema_create = cursor.fetchone()
        if schema_create and bool(schema_create[0]):
            forbidden.append(f"schema:{scoped_schema}.CREATE")

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
    postgis_schema_name: str = DEFAULT_POSTGIS_SCHEMA,
    migration_lock_key: int,
    revoke_public_database_temporary: bool = False,
) -> None:
    """Apply the normalized runtime role manifest inside the transaction.

    The caller owns commit/rollback.  Parser tables, managed support tables,
    Legal Intelligence corpus/overlay tables, and its glossary dependency are
    reset to their exact allowlists.  Shared project/calculation tables receive
    additive SELECT only, preserving the rest of the application's independently
    managed grants.  The public function name is kept for deployment
    compatibility.
    """

    role = validate_database_identifier(role_name, label="runtime role")
    schema = validate_database_identifier(schema_name, label="database schema")
    postgis_schema = validate_database_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    scoped_tables = tuple(
        dict.fromkeys(
            (
                *PARSER_V2_TABLE_PRIVILEGES,
                *PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
                *PARSER_V2_SUPPORT_PRIVILEGES,
                *LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES,
                *LEGAL_INTELLIGENCE_TENANT_PRIVILEGES,
                *LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES,
            )
        )
    )

    with conn.cursor() as cursor:
        assert_current_schema(cursor, schema_name=schema)
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (migration_lock_key,))
        cursor.execute(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, "
            "rolbypassrls, rolreplication "
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
            or bool(state[5])
        ):
            raise RuntimePrivilegeProvisioningError(
                "PostgreSQL runtime role must be NOSUPERUSER, NOCREATEDB, "
                "NOCREATEROLE, NOINHERIT, NOBYPASSRLS, and NOREPLICATION "
                "before grants are applied"
            )

        cursor.execute(
            """
            SELECT parent_role.rolname
            FROM pg_auth_members AS membership
            JOIN pg_roles AS member_role
              ON member_role.oid = membership.member
            JOIN pg_roles AS parent_role
              ON parent_role.oid = membership.roleid
            WHERE member_role.rolname = %s
            ORDER BY parent_role.rolname
            """,
            (role,),
        )
        memberships = [str(row[0]) for row in cursor.fetchall()]
        if memberships:
            raise RuntimePrivilegeProvisioningError(
                "PostgreSQL runtime role must not be a member of other roles: "
                + ", ".join(memberships)
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
                "runtime role must not own normalized runtime objects: "
                + ", ".join(owned)
            )

        cursor.execute("SELECT current_database()")
        database_row = cursor.fetchone()
        database_name = str(database_row[0] or "") if database_row else ""
        if not database_name:
            raise RuntimePrivilegeProvisioningError(
                "PostgreSQL current database could not be determined"
            )
        for statement in render_parser_v2_runtime_role_sql(
            role_name=role,
            schema_name=schema,
            postgis_schema_name=postgis_schema,
            database_name=database_name,
            revoke_public_database_temporary=(
                revoke_public_database_temporary
            ),
        ):
            cursor.execute(statement)


def render_parser_v2_runtime_role_sql(
    *,
    role_name: str,
    schema_name: str = DEFAULT_RUNTIME_SCHEMA,
    postgis_schema_name: str = DEFAULT_POSTGIS_SCHEMA,
    database_name: str,
    revoke_public_database_temporary: bool = False,
) -> tuple[str, ...]:
    """Render the validated runtime-role manifest as executable SQL.

    The deployment provisioner and an external SQL execution surface (for
    example, Supabase's Management API) can apply this exact manifest after
    performing the same role-attribute and object-ownership preflight checks.
    The database-scoped role setting avoids changing the role in unrelated
    databases.
    """

    role = validate_database_identifier(role_name, label="runtime role")
    schema = validate_database_identifier(schema_name, label="database schema")
    postgis_schema = validate_database_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    database = str(database_name or "")
    if not database:
        raise RuntimePrivilegeProvisioningError(
            "database name must not be empty"
        )

    role_identifier = _quoted_identifier(role)
    schema_identifier = _quoted_identifier(schema)
    postgis_schema_identifier = _quoted_identifier(postgis_schema)
    database_identifier = _quoted_identifier(database)
    statements: list[str] = []
    if revoke_public_database_temporary:
        # PostgreSQL grants database TEMPORARY to PUBLIC by default. A revoke
        # from only the runtime role is therefore ineffective; strict mode must
        # revoke PUBLIC and may affect other roles in a shared Supabase DB.
        statements.extend(
            (
                "REVOKE TEMPORARY ON DATABASE "
                f"{database_identifier} FROM PUBLIC",
                "REVOKE TEMPORARY ON DATABASE "
                f"{database_identifier} FROM {role_identifier}",
            )
        )

    for scoped_schema_identifier in dict.fromkeys(
        (schema_identifier, postgis_schema_identifier)
    ):
        statements.extend(
            (
                f"REVOKE CREATE ON SCHEMA {scoped_schema_identifier} FROM PUBLIC",
                "REVOKE ALL PRIVILEGES ON SCHEMA "
                f"{scoped_schema_identifier} FROM {role_identifier}",
                "GRANT USAGE ON SCHEMA "
                f"{scoped_schema_identifier} TO {role_identifier}",
            )
        )

    # Keep the application schema first so current_schema() remains stable.
    # pg_catalog is searched implicitly before explicit entries; naming
    # pg_temp last prevents PostgreSQL from implicitly searching it first.
    search_path = tuple(dict.fromkeys((schema, postgis_schema, "pg_temp")))
    search_path_sql = ", ".join(_quoted_identifier(item) for item in search_path)
    statements.append(
        f"ALTER ROLE {role_identifier} IN DATABASE {database_identifier} "
        f"SET search_path TO {search_path_sql}"
    )
    statements.append(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA "
        f"{schema_identifier} REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC"
    )

    exact_privileges = {
        **PARSER_V2_TABLE_PRIVILEGES,
        **PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
        **LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES,
        **LEGAL_INTELLIGENCE_TENANT_PRIVILEGES,
        **LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES,
    }
    for table, privileges in exact_privileges.items():
        relation = f"{schema_identifier}.{_quoted_identifier(table)}"
        statements.extend(
            (
                f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM PUBLIC",
                "REVOKE ALL PRIVILEGES ON TABLE "
                f"{relation} FROM {role_identifier}",
            )
        )
        if privileges:
            statements.append(
                _render_grant(relation, role_identifier, privileges)
            )

    for table, privileges in PARSER_V2_SUPPORT_PRIVILEGES.items():
        relation = f"{schema_identifier}.{_quoted_identifier(table)}"
        statements.append(_render_grant(relation, role_identifier, privileges))

    return tuple(statements)


def revoke_public_database_temporary_enabled() -> bool:
    """Return whether deployment opted into shared-database TEMP hardening."""

    return os.environ.get(
        REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV,
        "",
    ).strip().casefold() in {"1", "true", "yes", "on"}


def _render_grant(
    relation: str,
    role_identifier: str,
    privileges: Sequence[str],
) -> str:
    privilege_sql = ", ".join(privileges)
    return f"GRANT {privilege_sql} ON TABLE {relation} TO {role_identifier}"


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
    "DEFAULT_POSTGIS_SCHEMA",
    "DEFAULT_RUNTIME_SCHEMA",
    "FORBIDDEN_APPEND_ONLY_PRIVILEGES",
    "FORBIDDEN_READ_ONLY_PRIVILEGES",
    "LEGAL_INTELLIGENCE_GLOBAL_PRIVILEGES",
    "LEGAL_INTELLIGENCE_READ_ONLY_SUPPORT_PRIVILEGES",
    "LEGAL_INTELLIGENCE_TENANT_PRIVILEGES",
    "PARSER_V2_MANAGED_SUPPORT_PRIVILEGES",
    "PARSER_V2_SUPPORT_PRIVILEGES",
    "PARSER_V2_TABLE_PRIVILEGES",
    "POSTGIS_SCHEMA_ENV",
    "REVOKE_PUBLIC_DATABASE_TEMPORARY_ENV",
    "RUNTIME_ROLE_ENV",
    "RUNTIME_SCHEMA_ENV",
    "RuntimePrivilegeProvisioningError",
    "assert_current_schema",
    "audit_runtime_privileges",
    "forbidden_table_privileges",
    "provision_parser_v2_runtime_role",
    "render_parser_v2_runtime_role_sql",
    "required_table_privileges",
    "revoke_public_database_temporary_enabled",
    "validate_database_identifier",
]
