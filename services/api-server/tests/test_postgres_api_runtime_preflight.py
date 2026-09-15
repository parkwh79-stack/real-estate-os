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
    required_table_privileges,
    required_column_privileges,
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
        if "AS runtime_owned_objects" in normalized:
            self.rows = [(item,) for item in self.connection.owned_objects]
            return
        if "AS expected_function" in normalized:
            self.rows = [(item,) for item in self.connection.missing_functions]
            return
        if "WITH unlisted_runtime_relations" in normalized:
            self.rows = [(item,) for item in self.connection.unlisted_privileges]
            return
        if "WITH runtime_table_contract" in normalized:
            _schema, entries, _role = params
            self.rows = [
                (f"{entry['relation_name']}.{entry['privilege']}", entry["required"])
                for entry in json.loads(entries)
                if ((entry["relation_name"], entry["privilege"]) in self.connection.table_grants)
                != entry["required"]
            ]
            return
        if "runtime_column_contract" in normalized:
            entries, all_entries, _schema, _schema_again, _role = params
            self.rows = [
                (f"{entry['relation_name']}.{entry['column_name']}.{entry['privilege']}", entry["required"])
                for entry in json.loads(entries)
                if self.connection.column_allowed(entry["relation_name"], entry["column_name"], entry["privilege"])
                != entry["required"]
            ]
            for entry in json.loads(all_entries):
                for table, column, privilege in self.connection.column_grants:
                    if (table, privilege) == (entry["relation_name"], entry["privilege"]) and column not in entry["allowed_columns"]:
                        self.rows.append((f"{table}.{column}.{privilege}", False))
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
            for table, privileges in required_table_privileges().items()
            for privilege in privileges
        }
        self.sequence_grants = {
            (sequence, privilege)
            for sequence, privileges
            in PROJECT_RUNTIME_SEQUENCE_PRIVILEGES.items()
            for privilege in privileges
        }
        self.closed = False
        self.owned_objects: tuple[str, ...] = ()
        self.missing_functions: tuple[str, ...] = ()
        self.unlisted_privileges: tuple[str, ...] = ()
        self.column_grants = {
            (table, column, privilege)
            for table, grants in required_column_privileges().items()
            for privilege, columns in grants.items()
            for column in columns
        }

    def column_allowed(self, table, column, privilege) -> bool:
        return (table, privilege) in self.table_grants or (table, column, privilege) in self.column_grants

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


def test_preflight_inspects_actual_api_database_not_only_named_deployment_role() -> None:
    deployment = _FakeConnection(current_role="deployment", session_role="deployment")
    runtime = _FakeConnection()
    runtime.table_grants.remove(("calculation_runs", "INSERT"))
    result = run_api_runtime_preflight(
        environ=_environment(),
        connector=lambda dsn: deployment if dsn == MIGRATION_DSN else runtime,
    )
    assert result.ok is False
    assert any("calculation_runs.INSERT" in issue.objects for issue in result.issues)
    assert deployment.closed and runtime.closed


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


@pytest.mark.parametrize("table", ["calculation_runs", "runtime_outbox_events", "async_jobs", "agent_runs"])
def test_api_startup_rejects_missing_durable_write_privilege(table) -> None:
    connection = _FakeConnection()
    connection.table_grants.remove((table, "INSERT"))
    with pytest.raises(ApiRuntimePreflightError) as captured:
        assert_api_runtime_connection(connection, expected_role=ROLE)
    assert any(f"{table}.INSERT" in issue.objects for issue in captured.value.result.issues)


def test_api_rejects_database_owner_even_with_exact_table_acl() -> None:
    connection = _FakeConnection()
    connection.owned_objects = ("database:postgres",)
    with pytest.raises(ApiRuntimePreflightError) as captured:
        assert_api_runtime_connection(connection, expected_role=ROLE)
    assert ApiRuntimePreflightCode.FORBIDDEN_OWNERSHIP in _codes(captured.value.result.issues)


@pytest.mark.parametrize("grant", [("projects", "project_id", "REFERENCES"), ("user_activity_logs", "payload_json", "UPDATE")])
def test_api_rejects_column_only_project_and_audit_privileges(grant) -> None:
    connection = _FakeConnection()
    connection.column_grants.add(grant)
    with pytest.raises(ApiRuntimePreflightError) as captured:
        assert_api_runtime_connection(connection, expected_role=ROLE)
    assert any(".".join(grant) in issue.objects for issue in captured.value.result.issues)


def _worker_connection():
    from db.postgres_pf_worker_privileges import PF_WORKER_TABLE_PRIVILEGES
    connection = _FakeConnection(current_role="realytics_pf_worker", session_role="realytics_pf_worker")
    connection.table_grants = {
        (table, privilege) for table, privileges in PF_WORKER_TABLE_PRIVILEGES.items()
        for privilege in privileges
    }
    connection.column_grants = set()
    connection.sequence_grants = set()
    return connection


def test_pf_worker_exact_direct_login_and_execution_grants_pass() -> None:
    from db.postgres_pf_worker_runtime_preflight import assert_pf_worker_runtime_connection
    assert_pf_worker_runtime_connection(_worker_connection(), expected_role="realytics_pf_worker")


@pytest.mark.parametrize("unsafe_index", range(1, 7))
def test_pf_worker_rejects_all_privileged_role_attributes(unsafe_index) -> None:
    from db.postgres_pf_worker_runtime_preflight import assert_pf_worker_runtime_connection, PFWorkerRuntimePreflightError
    connection = _worker_connection()
    attributes = list(connection.role_state)
    attributes[unsafe_index] = True
    connection.role_state = tuple(attributes)
    with pytest.raises(PFWorkerRuntimePreflightError, match="unsafe_pf_worker_role_attributes"):
        assert_pf_worker_runtime_connection(connection, expected_role="realytics_pf_worker")


@pytest.mark.parametrize("table", [
    "report_publications", "parser_review_decisions", "parser_approved_snapshots",
    "parser_canonical_selections", "legal_review_decisions", "legal_approval_delegations",
    "tenant_memberships", "onboarding_profiles", "projects", "calculation_runs", "deal_state_records",
])
def test_pf_worker_rejects_human_approval_identity_and_input_writes(table) -> None:
    from db.postgres_pf_worker_runtime_preflight import assert_pf_worker_runtime_connection, PFWorkerRuntimePreflightError
    connection = _worker_connection()
    connection.table_grants.add((table, "INSERT"))
    with pytest.raises(PFWorkerRuntimePreflightError, match="pf_worker_forbidden_runtime_privilege"):
        assert_pf_worker_runtime_connection(connection, expected_role="realytics_pf_worker")


def test_pf_worker_rejects_column_only_identity_grant() -> None:
    from db.postgres_pf_worker_runtime_preflight import assert_pf_worker_runtime_connection, PFWorkerRuntimePreflightError
    connection = _worker_connection()
    connection.column_grants.add(("tenant_memberships", "role", "UPDATE"))
    with pytest.raises(PFWorkerRuntimePreflightError, match="pf_worker_forbidden_runtime_privilege"):
        assert_pf_worker_runtime_connection(connection, expected_role="realytics_pf_worker")


@pytest.mark.parametrize("grant", ["app_state.SELECT", "user_ai_profiles.column.SELECT", "learning_promotions.INSERT"])
def test_pf_worker_rejects_unlisted_private_relation_access(grant) -> None:
    from db.postgres_pf_worker_runtime_preflight import assert_pf_worker_runtime_connection, PFWorkerRuntimePreflightError
    connection = _worker_connection()
    connection.unlisted_privileges = (grant,)
    with pytest.raises(PFWorkerRuntimePreflightError, match="pf_worker_forbidden_runtime_privilege"):
        assert_pf_worker_runtime_connection(connection, expected_role="realytics_pf_worker")


@pytest.mark.parametrize("defect,code", [
    ("session", "wrong_pf_worker_login_role"),
    ("nologin", "pf_worker_role_nologin"),
    ("membership", "pf_worker_inherited_role_membership"),
    ("ownership", "pf_worker_forbidden_ownership"),
    ("missing_write", "pf_worker_missing_runtime_privilege"),
    ("sequence", "pf_worker_forbidden_runtime_privilege"),
])
def test_pf_worker_startup_rejects_unsafe_or_incomplete_checkout(defect, code) -> None:
    from db.postgres_pf_worker_runtime_preflight import assert_pf_worker_runtime_connection, PFWorkerRuntimePreflightError
    connection = _worker_connection()
    if defect == "session":
        connection.session_role = "postgres"
    elif defect == "nologin":
        connection.role_state = (False, *connection.role_state[1:])
    elif defect == "membership":
        connection.parent_roles = ("realytics_runtime",)
    elif defect == "ownership":
        connection.owned_objects = ("function:realytics.approval_helper",)
    elif defect == "missing_write":
        connection.table_grants.remove(("async_jobs", "UPDATE"))
    elif defect == "sequence":
        connection.sequence_grants.add(("project_id_sequence", "USAGE"))
    with pytest.raises(PFWorkerRuntimePreflightError, match=code):
        assert_pf_worker_runtime_connection(connection, expected_role="realytics_pf_worker")
