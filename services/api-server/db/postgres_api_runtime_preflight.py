"""Fail-closed diagnostics for the dedicated API PostgreSQL login.

The migration connection is used only to inspect role metadata and effective
grants.  Runtime traffic is verified through ``REALYTICS_API_DATABASE_URL`` so
an administrator DSN cannot accidentally satisfy the application readiness
gate.  No connection string or database exception text is included in results.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from db.postgres_runtime_privileges import (
    DEFAULT_POSTGIS_SCHEMA,
    PROJECT_RUNTIME_FORBIDDEN_PRIVILEGES,
    PROJECT_RUNTIME_SEQUENCE_FORBIDDEN_PRIVILEGES,
    PROJECT_RUNTIME_SEQUENCE_PRIVILEGES,
    PROJECT_RUNTIME_TABLE_PRIVILEGES,
    validate_database_identifier,
)


API_DATABASE_URL_ENV = "REALYTICS_API_DATABASE_URL"
API_DATABASE_ROLE_ENV = "REALYTICS_API_DATABASE_ROLE"
MIGRATION_DATABASE_URL_ENV = "REALYTICS_MIGRATION_DATABASE_URL"
# The dedicated production API contract is intentionally narrower than the
# legacy privilege helper's public-schema compatibility default. REALYTICS
# business authority lives in the private ``realytics`` schema.
DEFAULT_API_RUNTIME_SCHEMA = "realytics"


class ApiRuntimePreflightCode(StrEnum):
    MISSING_API_DSN = "missing_api_dsn"
    MISSING_MIGRATION_DSN = "missing_migration_dsn"
    MISSING_EXPECTED_ROLE = "missing_expected_role"
    INVALID_CONFIGURATION = "invalid_configuration"
    DEPLOYMENT_INSPECTION_FAILED = "deployment_inspection_failed"
    API_CONNECTION_FAILED = "api_connection_failed"
    ROLE_NOT_FOUND = "role_not_found"
    ROLE_NOLOGIN = "role_nologin"
    WRONG_LOGIN_ROLE = "wrong_login_role"
    UNSAFE_ROLE_ATTRIBUTES = "unsafe_role_attributes"
    INHERITED_ROLE_MEMBERSHIP = "inherited_role_membership"
    MISSING_SCHEMA_USAGE = "missing_schema_usage"
    FORBIDDEN_SCHEMA_CREATE = "forbidden_schema_create"
    MISSING_PROJECT_PRIVILEGE = "missing_project_privilege"
    FORBIDDEN_PROJECT_PRIVILEGE = "forbidden_project_privilege"
    MISSING_SEQUENCE_PRIVILEGE = "missing_sequence_privilege"
    FORBIDDEN_SEQUENCE_PRIVILEGE = "forbidden_sequence_privilege"


@dataclass(frozen=True)
class ApiRuntimePreflightIssue:
    code: ApiRuntimePreflightCode
    message: str
    objects: tuple[str, ...] = ()

    def to_safe_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "code": self.code.value,
            "message": self.message,
        }
        if self.objects:
            result["objects"] = list(self.objects)
        return result


@dataclass(frozen=True)
class ApiRuntimePreflightResult:
    expected_role: str | None
    connected_role: str | None
    issues: tuple[ApiRuntimePreflightIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "expectedRole": self.expected_role,
            "connectedRole": self.connected_role,
            "issues": [issue.to_safe_dict() for issue in self.issues],
        }


class ApiRuntimePreflightError(RuntimeError):
    """The API database login is unsafe or cannot satisfy its contract."""

    def __init__(self, result: ApiRuntimePreflightResult) -> None:
        self.result = result
        codes = ", ".join(issue.code.value for issue in result.issues)
        super().__init__(
            "API runtime database preflight failed"
            + (f": {codes}" if codes else "")
        )


def _role_state(cursor: Any, *, role_name: str) -> tuple[Any, ...] | None:
    cursor.execute(
        """
        SELECT
            rolcanlogin,
            rolsuper,
            rolcreatedb,
            rolcreaterole,
            rolinherit,
            rolreplication,
            rolbypassrls
        FROM pg_roles
        WHERE rolname = %s
        """,
        (role_name,),
    )
    return cursor.fetchone()


def _parent_roles(cursor: Any, *, role_name: str) -> tuple[str, ...]:
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
        (role_name,),
    )
    return tuple(str(row[0]) for row in cursor.fetchall())


def _has_schema_privilege(
    cursor: Any,
    *,
    role_name: str,
    schema_name: str,
    privilege: str,
) -> bool:
    cursor.execute(
        "SELECT has_schema_privilege(%s, %s, %s)",
        (role_name, schema_name, privilege),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _has_relation_privilege(
    cursor: Any,
    *,
    role_name: str,
    schema_name: str,
    relation_name: str,
    privilege: str,
    sequence: bool,
) -> bool:
    checker = "has_sequence_privilege" if sequence else "has_table_privilege"
    cursor.execute(
        f"""
        SELECT CASE
            WHEN relation_oid IS NULL THEN FALSE
            ELSE {checker}(%s, relation_oid, %s)
        END
        FROM (
            SELECT to_regclass(
                format('%%I.%%I', %s::text, %s::text)
            ) AS relation_oid
        ) AS runtime_relation
        """,
        (role_name, privilege, schema_name, relation_name),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _collect_relation_findings(
    cursor: Any,
    *,
    role_name: str,
    schema_name: str,
    manifest: Mapping[str, Sequence[str]],
    sequence: bool,
    expected: bool,
) -> tuple[str, ...]:
    findings: list[str] = []
    for relation_name, privileges in manifest.items():
        for privilege in privileges:
            granted = _has_relation_privilege(
                cursor,
                role_name=role_name,
                schema_name=schema_name,
                relation_name=relation_name,
                privilege=privilege,
                sequence=sequence,
            )
            if granted is not expected:
                findings.append(f"{relation_name}.{privilege}")
    return tuple(sorted(findings))


def inspect_api_runtime_role(
    connection: Any,
    *,
    expected_role: str,
    schema_name: str = DEFAULT_API_RUNTIME_SCHEMA,
    postgis_schema_name: str = DEFAULT_POSTGIS_SCHEMA,
) -> tuple[ApiRuntimePreflightIssue, ...]:
    """Inspect one named role without assuming the connection uses that role."""

    role = validate_database_identifier(
        expected_role,
        label="API database role",
    )
    schema = validate_database_identifier(
        schema_name,
        label="database schema",
    )
    postgis_schema = validate_database_identifier(
        postgis_schema_name,
        label="PostGIS schema",
    )
    issues: list[ApiRuntimePreflightIssue] = []

    with connection.cursor() as cursor:
        state = _role_state(cursor, role_name=role)
        if state is None:
            return (
                ApiRuntimePreflightIssue(
                    ApiRuntimePreflightCode.ROLE_NOT_FOUND,
                    "The configured API database role does not exist.",
                    (role,),
                ),
            )

        if not bool(state[0]):
            issues.append(
                ApiRuntimePreflightIssue(
                    ApiRuntimePreflightCode.ROLE_NOLOGIN,
                    "The API database role is NOLOGIN; activate LOGIN only "
                    "through the approved secret-management workflow.",
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
        unsafe_attributes = tuple(
            name
            for name, enabled in zip(attribute_names, state[1:], strict=True)
            if bool(enabled)
        )
        if unsafe_attributes:
            issues.append(
                ApiRuntimePreflightIssue(
                    ApiRuntimePreflightCode.UNSAFE_ROLE_ATTRIBUTES,
                    "The API database role has attributes forbidden by the "
                    "least-privilege/RLS boundary.",
                    unsafe_attributes,
                )
            )

        parent_roles = _parent_roles(cursor, role_name=role)
        if parent_roles:
            issues.append(
                ApiRuntimePreflightIssue(
                    ApiRuntimePreflightCode.INHERITED_ROLE_MEMBERSHIP,
                    "The API database role must not be a member of other roles.",
                    parent_roles,
                )
            )

        missing_schema_usage: list[str] = []
        forbidden_schema_create: list[str] = []
        for scoped_schema in dict.fromkeys((schema, postgis_schema)):
            if not _has_schema_privilege(
                cursor,
                role_name=role,
                schema_name=scoped_schema,
                privilege="USAGE",
            ):
                missing_schema_usage.append(scoped_schema)
            if _has_schema_privilege(
                cursor,
                role_name=role,
                schema_name=scoped_schema,
                privilege="CREATE",
            ):
                forbidden_schema_create.append(scoped_schema)

        missing_tables = _collect_relation_findings(
            cursor,
            role_name=role,
            schema_name=schema,
            manifest=PROJECT_RUNTIME_TABLE_PRIVILEGES,
            sequence=False,
            expected=True,
        )
        forbidden_tables = _collect_relation_findings(
            cursor,
            role_name=role,
            schema_name=schema,
            manifest=PROJECT_RUNTIME_FORBIDDEN_PRIVILEGES,
            sequence=False,
            expected=False,
        )
        missing_sequences = _collect_relation_findings(
            cursor,
            role_name=role,
            schema_name=schema,
            manifest=PROJECT_RUNTIME_SEQUENCE_PRIVILEGES,
            sequence=True,
            expected=True,
        )
        forbidden_sequences = _collect_relation_findings(
            cursor,
            role_name=role,
            schema_name=schema,
            manifest=PROJECT_RUNTIME_SEQUENCE_FORBIDDEN_PRIVILEGES,
            sequence=True,
            expected=False,
        )

    grouped_findings = (
        (
            missing_schema_usage,
            ApiRuntimePreflightCode.MISSING_SCHEMA_USAGE,
            "The API database role is missing schema USAGE.",
        ),
        (
            forbidden_schema_create,
            ApiRuntimePreflightCode.FORBIDDEN_SCHEMA_CREATE,
            "The API database role has forbidden schema CREATE.",
        ),
        (
            missing_tables,
            ApiRuntimePreflightCode.MISSING_PROJECT_PRIVILEGE,
            "The API database role is missing required project privileges.",
        ),
        (
            forbidden_tables,
            ApiRuntimePreflightCode.FORBIDDEN_PROJECT_PRIVILEGE,
            "The API database role has broader project privileges than allowed.",
        ),
        (
            missing_sequences,
            ApiRuntimePreflightCode.MISSING_SEQUENCE_PRIVILEGE,
            "The API database role is missing project sequence USAGE.",
        ),
        (
            forbidden_sequences,
            ApiRuntimePreflightCode.FORBIDDEN_SEQUENCE_PRIVILEGE,
            "The API database role can inspect or reset the project sequence.",
        ),
    )
    for findings, code, message in grouped_findings:
        if findings:
            issues.append(
                ApiRuntimePreflightIssue(code, message, tuple(findings))
            )
    return tuple(issues)


def inspect_api_runtime_login(
    connection: Any,
    *,
    expected_role: str,
) -> tuple[str | None, tuple[ApiRuntimePreflightIssue, ...]]:
    """Verify that the dedicated DSN authenticated as the expected role."""

    role = validate_database_identifier(
        expected_role,
        label="API database role",
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, session_user")
        row = cursor.fetchone()
    current_role = str(row[0] or "") if row else ""
    session_role = str(row[1] or "") if row and len(row) > 1 else ""
    if current_role == role and session_role == role:
        return current_role, ()
    observed = tuple(
        dict.fromkeys(item for item in (current_role, session_role) if item)
    )
    return (
        current_role or session_role or None,
        (
            ApiRuntimePreflightIssue(
                ApiRuntimePreflightCode.WRONG_LOGIN_ROLE,
                "REALYTICS_API_DATABASE_URL did not authenticate directly "
                "as the configured API database role.",
                observed,
            ),
        ),
    )


def assert_api_runtime_connection(
    connection: Any,
    *,
    expected_role: str,
    schema_name: str = DEFAULT_API_RUNTIME_SCHEMA,
    postgis_schema_name: str = DEFAULT_POSTGIS_SCHEMA,
) -> None:
    """Raise a secret-safe error when a live API checkout is not production-safe."""

    connected_role, login_issues = inspect_api_runtime_login(
        connection,
        expected_role=expected_role,
    )
    role_issues = inspect_api_runtime_role(
        connection,
        expected_role=expected_role,
        schema_name=schema_name,
        postgis_schema_name=postgis_schema_name,
    )
    result = ApiRuntimePreflightResult(
        expected_role=expected_role,
        connected_role=connected_role,
        issues=(*login_issues, *role_issues),
    )
    if not result.ok:
        raise ApiRuntimePreflightError(result)


def _configured(environ: Mapping[str, str], key: str) -> str:
    return str(environ.get(key) or "").strip()


def run_api_runtime_preflight(
    *,
    environ: Mapping[str, str] | None = None,
    connector: Callable[[str], Any] | None = None,
) -> ApiRuntimePreflightResult:
    """Run the read-only deployment and runtime checks from environment config."""

    env = os.environ if environ is None else environ
    api_dsn = _configured(env, API_DATABASE_URL_ENV)
    migration_dsn = _configured(env, MIGRATION_DATABASE_URL_ENV)
    expected_role = _configured(env, API_DATABASE_ROLE_ENV)
    schema_name = _configured(env, "REALYTICS_DATABASE_SCHEMA") or (
        DEFAULT_API_RUNTIME_SCHEMA
    )
    postgis_schema_name = _configured(env, "REALYTICS_POSTGIS_SCHEMA") or (
        DEFAULT_POSTGIS_SCHEMA
    )
    issues: list[ApiRuntimePreflightIssue] = []
    if not api_dsn:
        issues.append(
            ApiRuntimePreflightIssue(
                ApiRuntimePreflightCode.MISSING_API_DSN,
                f"Set {API_DATABASE_URL_ENV} to the dedicated API login DSN.",
            )
        )
    if not migration_dsn:
        issues.append(
            ApiRuntimePreflightIssue(
                ApiRuntimePreflightCode.MISSING_MIGRATION_DSN,
                f"Set {MIGRATION_DATABASE_URL_ENV} for read-only role inspection.",
            )
        )
    if not expected_role:
        issues.append(
            ApiRuntimePreflightIssue(
                ApiRuntimePreflightCode.MISSING_EXPECTED_ROLE,
                f"Set {API_DATABASE_ROLE_ENV} to the dedicated login role.",
            )
        )
    if issues:
        return ApiRuntimePreflightResult(expected_role or None, None, tuple(issues))

    try:
        expected_role = validate_database_identifier(
            expected_role,
            label="API database role",
        )
        schema_name = validate_database_identifier(
            schema_name,
            label="database schema",
        )
        postgis_schema_name = validate_database_identifier(
            postgis_schema_name,
            label="PostGIS schema",
        )
    except Exception:
        return ApiRuntimePreflightResult(
            expected_role or None,
            None,
            (
                ApiRuntimePreflightIssue(
                    ApiRuntimePreflightCode.INVALID_CONFIGURATION,
                    "API role and schema names must be simple PostgreSQL "
                    "identifiers.",
                ),
            ),
        )

    if connector is None:
        from db import postgres

        connector = postgres.connect

    deployment_connection = None
    try:
        deployment_connection = connector(migration_dsn)
        issues.extend(
            inspect_api_runtime_role(
                deployment_connection,
                expected_role=expected_role,
                schema_name=schema_name,
                postgis_schema_name=postgis_schema_name,
            )
        )
    except Exception:
        return ApiRuntimePreflightResult(
            expected_role,
            None,
            (
                ApiRuntimePreflightIssue(
                    ApiRuntimePreflightCode.DEPLOYMENT_INSPECTION_FAILED,
                    "The deployment DSN could not inspect the API role; "
                    "credentials and exception details were suppressed.",
                ),
            ),
        )
    finally:
        if deployment_connection is not None:
            deployment_connection.close()

    blocking_role_codes = {
        ApiRuntimePreflightCode.ROLE_NOT_FOUND,
        ApiRuntimePreflightCode.ROLE_NOLOGIN,
        ApiRuntimePreflightCode.UNSAFE_ROLE_ATTRIBUTES,
        ApiRuntimePreflightCode.INHERITED_ROLE_MEMBERSHIP,
    }
    if any(issue.code in blocking_role_codes for issue in issues):
        return ApiRuntimePreflightResult(expected_role, None, tuple(issues))

    runtime_connection = None
    connected_role: str | None = None
    try:
        runtime_connection = connector(api_dsn)
        connected_role, login_issues = inspect_api_runtime_login(
            runtime_connection,
            expected_role=expected_role,
        )
        issues.extend(login_issues)
    except Exception:
        issues.append(
            ApiRuntimePreflightIssue(
                ApiRuntimePreflightCode.API_CONNECTION_FAILED,
                "The dedicated API DSN could not connect; verify LOGIN, "
                "password, SSL, and network reachability. Details were "
                "suppressed to protect credentials.",
            )
        )
    finally:
        if runtime_connection is not None:
            runtime_connection.close()

    return ApiRuntimePreflightResult(
        expected_role,
        connected_role,
        tuple(issues),
    )


__all__ = [
    "API_DATABASE_ROLE_ENV",
    "API_DATABASE_URL_ENV",
    "DEFAULT_API_RUNTIME_SCHEMA",
    "MIGRATION_DATABASE_URL_ENV",
    "ApiRuntimePreflightCode",
    "ApiRuntimePreflightError",
    "ApiRuntimePreflightIssue",
    "ApiRuntimePreflightResult",
    "assert_api_runtime_connection",
    "inspect_api_runtime_login",
    "inspect_api_runtime_role",
    "run_api_runtime_preflight",
]
