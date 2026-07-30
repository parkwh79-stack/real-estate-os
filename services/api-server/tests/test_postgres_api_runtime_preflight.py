from __future__ import annotations

import json

import pytest

from db.postgres_api_runtime_preflight import (
    API_DATABASE_ROLE_ENV,
    API_DATABASE_URL_ENV,
    MIGRATION_DATABASE_URL_ENV,
    ApiRuntimePreflightCode,
    ApiRuntimePreflightError,
    assert_api_runtime_connection,
    inspect_api_runtime_role,
    run_api_runtime_preflight,
)
from db.postgres_runtime_privileges import (
    PROJECT_RUNTIME_SEQUENCE_PRIVILEGES,
    PROJECT_RUNTIME_TABLE_PRIVILEGES,
)


ROLE = "realytics_runtime"
MIGRATION_DSN = "postgresql://deployment:secret@db.test:5432/postgres"
API_DSN = "postgresql://realytics_runtime:secret@db.test:5432/postgres"


class _FakeCursor:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection
        self.row = None
        self.rows: list[tuple[object, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def execute(self, query: str, params=None) -> None:
        normalized = " ".join(query.split())
        self.row = None
        self.rows = []
        if normalized == "SELECT current_user, session_user":
            self.row = (
                self.connection.current_role,
                self.connection.session_role,
            )
            return
        if "FROM pg_roles" in normalized and "rolcanlogin" in normalized:
            self.row = self.connection.role_state
            return
        if "FROM pg_auth_members" in normalized:
            self.rows = [(role,) for role in self.connection.parent_roles]
            return
        if normalized.startswith("SELECT has_schema_privilege"):
            _role, schema, privilege = params
            self.row = (
                (str(schema), str(privilege))
                in self.connection.schema_grants,
            )
            return
        if "has_table_privilege" in normalized:
            _role, privilege, _schema, relation = params
            self.row = (
                (str(relation), str(privilege))
                in self.connection.table_grants,
            )
            return
        if "has_sequence_privilege" in normalized:
            _role, privilege, _schema, relation = params
            self.row = (
                (str(relation), str(privilege))
                in self.connection.sequence_grants,
            )
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    def fetchone(self):
        return self.row

    def fetchall(self):
        return list(self.rows)


class _FakeConnection:
    def __init__(
        self,
        *,
        current_role: str = ROLE,
        session_role: str = ROLE,
        role_state: tuple[object, ...] | None = (
            True,
            False,
            False,
            False,
            False,
            False,
            False,
        ),
    ) -> None:
        self.current_role = current_role
        self.session_role = session_role
        self.role_state = role_state
        self.parent_roles: tuple[str, ...] = ()
        self.schema_grants = {
            ("realytics", "USAGE"),
            ("extensions", "USAGE"),
        }
        self.table_grants = {
            (table, privilege)
            for table, privileges in PROJECT_RUNTIME_TABLE_PRIVILEGES.items()
            for privilege in privileges
        }
        self.sequence_grants = {
            (sequence, privilege)
            for sequence, privileges
            in PROJECT_RUNTIME_SEQUENCE_PRIVILEGES.items()
            for privilege in privileges
        }
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def _codes(issues) -> set[ApiRuntimePreflightCode]:
    return {issue.code for issue in issues}


def _environment() -> dict[str, str]:
    return {
        MIGRATION_DATABASE_URL_ENV: MIGRATION_DSN,
        API_DATABASE_URL_ENV: API_DSN,
        API_DATABASE_ROLE_ENV: ROLE,
        "REALYTICS_DATABASE_SCHEMA": "realytics",
        "REALYTICS_POSTGIS_SCHEMA": "extensions",
    }


def test_exact_login_role_and_project_privileges_pass() -> None:
    connection = _FakeConnection()

    assert_api_runtime_connection(connection, expected_role=ROLE)


def test_role_inspection_distinguishes_nologin_and_bypassrls() -> None:
    connection = _FakeConnection(
        role_state=(False, False, False, False, False, False, True),
    )

    issues = inspect_api_runtime_role(connection, expected_role=ROLE)

    assert _codes(issues) >= {
        ApiRuntimePreflightCode.ROLE_NOLOGIN,
        ApiRuntimePreflightCode.UNSAFE_ROLE_ATTRIBUTES,
    }
    unsafe = next(
        issue
        for issue in issues
        if issue.code is ApiRuntimePreflightCode.UNSAFE_ROLE_ATTRIBUTES
    )
    assert unsafe.objects == ("BYPASSRLS",)


def test_role_inspection_reports_exact_missing_and_forbidden_grants() -> None:
    connection = _FakeConnection()
    connection.table_grants.remove(("projects", "INSERT"))
    connection.table_grants.add(("projects", "DELETE"))
    connection.sequence_grants.remove(("project_id_sequence", "USAGE"))
    connection.sequence_grants.add(("project_id_sequence", "UPDATE"))

    issues = inspect_api_runtime_role(connection, expected_role=ROLE)

    by_code = {issue.code: issue for issue in issues}
    assert by_code[
        ApiRuntimePreflightCode.MISSING_PROJECT_PRIVILEGE
    ].objects == ("projects.INSERT",)
    assert by_code[
        ApiRuntimePreflightCode.FORBIDDEN_PROJECT_PRIVILEGE
    ].objects == ("projects.DELETE",)
    assert by_code[
        ApiRuntimePreflightCode.MISSING_SEQUENCE_PRIVILEGE
    ].objects == ("project_id_sequence.USAGE",)
    assert by_code[
        ApiRuntimePreflightCode.FORBIDDEN_SEQUENCE_PRIVILEGE
    ].objects == ("project_id_sequence.UPDATE",)


def test_environment_preflight_names_every_missing_contract_value() -> None:
    result = run_api_runtime_preflight(environ={})

    assert result.ok is False
    assert _codes(result.issues) == {
        ApiRuntimePreflightCode.MISSING_API_DSN,
        ApiRuntimePreflightCode.MISSING_MIGRATION_DSN,
        ApiRuntimePreflightCode.MISSING_EXPECTED_ROLE,
    }


def test_nologin_is_reported_before_runtime_connection_attempt() -> None:
    deployment = _FakeConnection(
        current_role="deployment",
        session_role="deployment",
        role_state=(False, False, False, False, False, False, False),
    )
    connected_dsns: list[str] = []

    def connector(dsn: str):
        connected_dsns.append(dsn)
        if dsn == MIGRATION_DSN:
            return deployment
        raise AssertionError("NOLOGIN must stop before runtime connection")

    result = run_api_runtime_preflight(
        environ=_environment(),
        connector=connector,
    )

    assert ApiRuntimePreflightCode.ROLE_NOLOGIN in _codes(result.issues)
    assert connected_dsns == [MIGRATION_DSN]
    assert deployment.closed is True


def test_wrong_runtime_login_role_is_distinct_from_privilege_failures() -> None:
    deployment = _FakeConnection(
        current_role="deployment",
        session_role="deployment",
    )
    runtime = _FakeConnection(
        current_role="postgres",
        session_role="postgres",
    )

    result = run_api_runtime_preflight(
        environ=_environment(),
        connector=lambda dsn: (
            deployment if dsn == MIGRATION_DSN else runtime
        ),
    )

    assert _codes(result.issues) == {
        ApiRuntimePreflightCode.WRONG_LOGIN_ROLE
    }
    assert result.connected_role == "postgres"
    assert deployment.closed is True
    assert runtime.closed is True


def test_connection_failures_never_echo_dsn_or_driver_exception() -> None:
    secret = "do-not-leak-this-password"
    environ = _environment()
    environ[MIGRATION_DATABASE_URL_ENV] = (
        f"postgresql://deployment:{secret}@db.test/postgres"
    )

    def connector(dsn: str):
        raise RuntimeError(f"could not connect with {dsn}")

    result = run_api_runtime_preflight(
        environ=environ,
        connector=connector,
    )
    rendered = json.dumps(result.to_safe_dict())

    assert _codes(result.issues) == {
        ApiRuntimePreflightCode.DEPLOYMENT_INSPECTION_FAILED
    }
    assert secret not in rendered
    assert "postgresql://" not in rendered


def test_assertion_error_contains_codes_but_not_credentials() -> None:
    connection = _FakeConnection(
        current_role="postgres",
        session_role="postgres",
    )

    with pytest.raises(ApiRuntimePreflightError) as captured:
        assert_api_runtime_connection(connection, expected_role=ROLE)

    assert "wrong_login_role" in str(captured.value)
    assert "secret" not in str(captured.value)
