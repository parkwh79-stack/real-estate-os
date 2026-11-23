"""Fail-closed checks for the dedicated pre-membership identity login.

The identity DSN can create only a principal's own onboarding profile and
access request under FORCE RLS.  Hardened startup therefore verifies the
actual login rather than trusting a DSN label: direct login is mandatory,
privileged/inherited attributes and ownership are forbidden, and the private
runtime schemas must be usable but never creatable by this role.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from db.postgres_runtime_privileges import validate_database_identifier


IDENTITY_DATABASE_ROLE_ENV = "REALYTICS_IDENTITY_DATABASE_ROLE"
DEFAULT_IDENTITY_DATABASE_ROLE = "realytics_identity"


class IdentityRuntimePreflightCode(StrEnum):
    WRONG_LOGIN_ROLE = "wrong_identity_login_role"
    ROLE_NOT_FOUND = "identity_role_not_found"
    ROLE_NOLOGIN = "identity_role_nologin"
    UNSAFE_ROLE_ATTRIBUTES = "unsafe_identity_role_attributes"
    INHERITED_ROLE_MEMBERSHIP = "identity_inherited_role_membership"
    MISSING_SCHEMA_USAGE = "identity_missing_schema_usage"
    FORBIDDEN_SCHEMA_CREATE = "identity_forbidden_schema_create"
    FORBIDDEN_OWNERSHIP = "identity_forbidden_ownership"


@dataclass(frozen=True)
class IdentityRuntimePreflightIssue:
    code: IdentityRuntimePreflightCode
    message: str
    objects: tuple[str, ...] = ()


class IdentityRuntimePreflightError(RuntimeError):
    """The connected identity login is unsafe for pre-membership traffic."""

    def __init__(self, issues: tuple[IdentityRuntimePreflightIssue, ...]) -> None:
        self.issues = issues
        codes = ", ".join(issue.code.value for issue in issues)
        super().__init__(
            "Identity runtime database preflight failed"
            + (f": {codes}" if codes else "")
        )


def inspect_identity_runtime_connection(
    connection: Any,
    *,
    expected_role: str,
    schema_name: str = "realytics",
    postgis_schema_name: str = "extensions",
) -> tuple[IdentityRuntimePreflightIssue, ...]:
    """Inspect the actual identity checkout and return secret-safe findings."""

    role = validate_database_identifier(
        expected_role,
        label="identity database role",
    )
    runtime_schema = validate_database_identifier(
        schema_name,
        label="database schema",
    )
    postgis_schema = validate_database_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    issues: list[IdentityRuntimePreflightIssue] = []

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, session_user")
        login_row = cursor.fetchone()
        current_role = str(login_row[0] or "") if login_row else ""
        session_role = (
            str(login_row[1] or "")
            if login_row and len(login_row) > 1
            else ""
        )
        if current_role != role or session_role != role:
            observed = tuple(
                dict.fromkeys(
                    item for item in (current_role, session_role) if item
                )
            )
            issues.append(
                IdentityRuntimePreflightIssue(
                    IdentityRuntimePreflightCode.WRONG_LOGIN_ROLE,
                    "The identity DSN did not authenticate directly as the "
                    "configured identity role.",
                    observed,
                )
            )

        cursor.execute(
            """
            SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                   rolinherit, rolreplication, rolbypassrls
              FROM pg_roles
             WHERE rolname = %s
            """,
            (role,),
        )
        role_state = cursor.fetchone()
        if role_state is None:
            issues.append(
                IdentityRuntimePreflightIssue(
                    IdentityRuntimePreflightCode.ROLE_NOT_FOUND,
                    "The configured identity database role does not exist.",
                    (role,),
                )
            )
            return tuple(issues)
        if not bool(role_state[0]):
            issues.append(
                IdentityRuntimePreflightIssue(
                    IdentityRuntimePreflightCode.ROLE_NOLOGIN,
                    "The identity database role must be a dedicated LOGIN.",
                    (role,),
                )
            )
        attribute_names = (
            "SUPERUSER",
            "CREATEDB",
            "CREATEROLE",
            "INHERIT",
            "REPLICATION",
            "BYPASSRLS",
        )
        unsafe = tuple(
            name
            for name, enabled in zip(
                attribute_names, role_state[1:], strict=True
            )
            if bool(enabled)
        )
        if unsafe:
            issues.append(
                IdentityRuntimePreflightIssue(
                    IdentityRuntimePreflightCode.UNSAFE_ROLE_ATTRIBUTES,
                    "The identity role has attributes forbidden by the RLS "
                    "boundary.",
                    unsafe,
                )
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
        parent_roles = tuple(str(row[0]) for row in cursor.fetchall())
        if parent_roles:
            issues.append(
                IdentityRuntimePreflightIssue(
                    IdentityRuntimePreflightCode.INHERITED_ROLE_MEMBERSHIP,
                    "The identity role must not be a member of other roles.",
                    parent_roles,
                )
            )

        missing_usage: list[str] = []
        forbidden_create: list[str] = []
        for scoped_schema in dict.fromkeys(
            (runtime_schema, postgis_schema)
        ):
            cursor.execute(
                "SELECT has_schema_privilege(%s, %s, 'USAGE')",
                (role, scoped_schema),
            )
            usage_row = cursor.fetchone()
            if not usage_row or not bool(usage_row[0]):
                missing_usage.append(scoped_schema)
            cursor.execute(
                "SELECT has_schema_privilege(%s, %s, 'CREATE')",
                (role, scoped_schema),
            )
            create_row = cursor.fetchone()
            if create_row and bool(create_row[0]):
                forbidden_create.append(scoped_schema)

        cursor.execute(
            """
            SELECT owned_object
              FROM (
                    SELECT 'database:' || db.datname AS owned_object
                      FROM pg_database AS db
                      JOIN pg_roles AS owner ON owner.oid = db.datdba
                     WHERE owner.rolname = %s
                       AND db.datname = current_database()
                    UNION ALL
                    SELECT 'schema:' || namespace.nspname
                      FROM pg_namespace AS namespace
                      JOIN pg_roles AS owner ON owner.oid = namespace.nspowner
                     WHERE owner.rolname = %s
                       AND namespace.nspname IN (%s, %s)
                    UNION ALL
                    SELECT 'relation:' || namespace.nspname || '.' || rel.relname
                      FROM pg_class AS rel
                      JOIN pg_namespace AS namespace
                        ON namespace.oid = rel.relnamespace
                      JOIN pg_roles AS owner ON owner.oid = rel.relowner
                     WHERE owner.rolname = %s
                       AND namespace.nspname IN (%s, %s)
                    UNION ALL
                    SELECT 'function:' || namespace.nspname || '.' || routine.proname
                      FROM pg_proc AS routine
                      JOIN pg_namespace AS namespace
                        ON namespace.oid = routine.pronamespace
                      JOIN pg_roles AS owner ON owner.oid = routine.proowner
                     WHERE owner.rolname = %s
                       AND namespace.nspname IN (%s, %s)
              ) AS identity_owned_objects
             ORDER BY owned_object
            """,
            (
                role,
                role,
                runtime_schema,
                postgis_schema,
                role,
                runtime_schema,
                postgis_schema,
                role,
                runtime_schema,
                postgis_schema,
            ),
        )
        owned_objects = tuple(str(row[0]) for row in cursor.fetchall())

    if missing_usage:
        issues.append(
            IdentityRuntimePreflightIssue(
                IdentityRuntimePreflightCode.MISSING_SCHEMA_USAGE,
                "The identity role is missing required schema USAGE.",
                tuple(missing_usage),
            )
        )
    if forbidden_create:
        issues.append(
            IdentityRuntimePreflightIssue(
                IdentityRuntimePreflightCode.FORBIDDEN_SCHEMA_CREATE,
                "The identity role has forbidden schema CREATE.",
                tuple(forbidden_create),
            )
        )
    if owned_objects:
        issues.append(
            IdentityRuntimePreflightIssue(
                IdentityRuntimePreflightCode.FORBIDDEN_OWNERSHIP,
                "The identity role must not own the database, runtime schemas, "
                "relations, or functions.",
                owned_objects,
            )
        )
    return tuple(issues)


def assert_identity_runtime_connection(
    connection: Any,
    *,
    expected_role: str,
    schema_name: str = "realytics",
    postgis_schema_name: str = "extensions",
) -> None:
    issues = inspect_identity_runtime_connection(
        connection,
        expected_role=expected_role,
        schema_name=schema_name,
        postgis_schema_name=postgis_schema_name,
    )
    if issues:
        raise IdentityRuntimePreflightError(issues)


__all__ = [
    "DEFAULT_IDENTITY_DATABASE_ROLE",
    "IDENTITY_DATABASE_ROLE_ENV",
    "IdentityRuntimePreflightCode",
    "IdentityRuntimePreflightError",
    "IdentityRuntimePreflightIssue",
    "assert_identity_runtime_connection",
    "inspect_identity_runtime_connection",
]
