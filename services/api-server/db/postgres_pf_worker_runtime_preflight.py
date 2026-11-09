"""Fail closed unless the isolated PF worker uses its direct, limited login."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from db.postgres_api_runtime_preflight import (
    _collect_relation_findings, _has_schema_privilege, _parent_roles, _role_state,
)
from db.postgres_pf_worker_privileges import (
    PF_WORKER_TABLE_PRIVILEGES,
    PF_WORKER_FORBIDDEN_TABLE_PRIVILEGES,
    PF_WORKER_FORBIDDEN_SEQUENCE_PRIVILEGES,
)
from db.postgres_privilege_inspection import (
    inspect_privilege_manifest, owned_runtime_objects, missing_function_privileges,
    unlisted_schema_privileges,
)
from db.postgres_runtime_privileges import (
    validate_database_identifier, LEGAL_INTELLIGENCE_RUNTIME_FUNCTIONS,
    LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS,
)


@dataclass(frozen=True)
class PFWorkerRuntimePreflightIssue:
    code: str
    objects: tuple[str, ...] = ()


class PFWorkerRuntimePreflightError(RuntimeError):
    def __init__(self, issues: tuple[PFWorkerRuntimePreflightIssue, ...]) -> None:
        self.issues = issues
        super().__init__("PF worker database preflight failed: " + ", ".join(issue.code for issue in issues))


def inspect_pf_worker_runtime_connection(
    connection: Any, *, expected_role: str, schema_name: str = "realytics",
    postgis_schema_name: str = "extensions",
) -> tuple[PFWorkerRuntimePreflightIssue, ...]:
    role = validate_database_identifier(expected_role, label="PF worker database role")
    schema = validate_database_identifier(schema_name, label="database schema")
    postgis = validate_database_identifier(postgis_schema_name, label="PostGIS schema")
    issues = []
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, session_user")
        login = cursor.fetchone()
        if not login or tuple(login) != (role, role):
            issues.append(PFWorkerRuntimePreflightIssue("wrong_pf_worker_login_role"))
        state = _role_state(cursor, role_name=role)
        if state is None:
            return (*issues, PFWorkerRuntimePreflightIssue("pf_worker_role_not_found"))
        if not bool(state[0]):
            issues.append(PFWorkerRuntimePreflightIssue("pf_worker_role_nologin"))
        unsafe = tuple(name for name, enabled in zip(
            ("SUPERUSER", "CREATEDB", "CREATEROLE", "INHERIT", "REPLICATION", "BYPASSRLS"),
            state[1:], strict=True,
        ) if bool(enabled))
        if unsafe:
            issues.append(PFWorkerRuntimePreflightIssue("unsafe_pf_worker_role_attributes", unsafe))
        parents = _parent_roles(cursor, role_name=role)
        if parents:
            issues.append(PFWorkerRuntimePreflightIssue("pf_worker_inherited_role_membership", parents))
        for scoped_schema in dict.fromkeys((schema, postgis)):
            if not _has_schema_privilege(cursor, role_name=role, schema_name=scoped_schema, privilege="USAGE"):
                issues.append(PFWorkerRuntimePreflightIssue("pf_worker_missing_schema_usage", (scoped_schema,)))
            if _has_schema_privilege(cursor, role_name=role, schema_name=scoped_schema, privilege="CREATE"):
                issues.append(PFWorkerRuntimePreflightIssue("pf_worker_forbidden_schema_create", (scoped_schema,)))
        owned = owned_runtime_objects(cursor, role=role, schemas=(schema, postgis))
        if owned:
            issues.append(PFWorkerRuntimePreflightIssue("pf_worker_forbidden_ownership", owned))
        missing, forbidden = inspect_privilege_manifest(
            cursor, role=role, schema=schema,
            required_tables=PF_WORKER_TABLE_PRIVILEGES,
            forbidden_tables=PF_WORKER_FORBIDDEN_TABLE_PRIVILEGES,
        )
        forbidden_sequences = _collect_relation_findings(
            cursor, role_name=role, schema_name=schema,
            manifest=PF_WORKER_FORBIDDEN_SEQUENCE_PRIVILEGES, sequence=True, expected=False,
        )
        unlisted = unlisted_schema_privileges(
            cursor, role=role, schema=schema, allowed_tables=tuple(PF_WORKER_TABLE_PRIVILEGES),
        )
        missing = (*missing, *missing_function_privileges(
            cursor, role=role, signatures=(
                *(f'"{schema}".{signature}' for signature in LEGAL_INTELLIGENCE_RUNTIME_FUNCTIONS),
                *(f'"{postgis}".{signature}' for signature in LEGAL_INTELLIGENCE_RUNTIME_EXTENSION_FUNCTIONS),
            ),
        ))
        if missing:
            issues.append(PFWorkerRuntimePreflightIssue("pf_worker_missing_runtime_privilege", missing))
        if forbidden or forbidden_sequences or unlisted:
            issues.append(PFWorkerRuntimePreflightIssue(
                "pf_worker_forbidden_runtime_privilege", (*forbidden, *forbidden_sequences, *unlisted),
            ))
    return tuple(issues)


def assert_pf_worker_runtime_connection(connection: Any, **kwargs: Any) -> None:
    try:
        issues = inspect_pf_worker_runtime_connection(connection, **kwargs)
    except Exception:
        raise PFWorkerRuntimePreflightError((
            PFWorkerRuntimePreflightIssue("pf_worker_role_inspection_failed"),
        )) from None
    if issues:
        raise PFWorkerRuntimePreflightError(issues)
