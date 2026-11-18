from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path

import pytest

from db import postgres_runtime_pool as runtime_pool
from db.repositories.identity_membership_repository import (
    IdentityMembershipRepositoryUnavailable,
    PostgresIdentityMembershipRepository,
)


DIRECT_DSN = "postgresql://runtime:secret@db.example.test:5432/realytics"


class _FakeTransaction:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection

    def __enter__(self):
        assert self.connection.in_transaction is False
        self.connection.in_transaction = True
        self.connection.transaction_enters += 1
        self.connection.local_settings = {}
        self.connection.events.append("begin")
        return self

    def __exit__(self, exc_type, _exc, _traceback):
        self.connection.local_snapshots.append(
            dict(self.connection.local_settings)
        )
        if exc_type is None:
            self.connection.commits += 1
            self.connection.events.append("commit")
        else:
            self.connection.rollbacks += 1
            self.connection.events.append("rollback")
        self.connection.local_settings = {}
        self.connection.in_transaction = False
        return False


class _FakePipeline:
    def __init__(self, connection: "_FakeConnection") -> None:
        self.connection = connection

    def __enter__(self):
        assert self.connection.pipeline_active is False
        self.connection.pipeline_active = True
        self.connection.pipeline_enters += 1
        self.connection.events.append("pipeline_enter")
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        assert self.connection.in_transaction is False
        self.connection.events.append("pipeline_exit")
        self.connection.pipeline_exits += 1
        self.connection.pipeline_active = False
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self.in_transaction = False
        self.transaction_enters = 0
        self.commits = 0
        self.rollbacks = 0
        self.pipeline_active = False
        self.pipeline_enters = 0
        self.pipeline_exits = 0
        self.events: list[str] = []
        self.statements: list[
            tuple[str, tuple[str, ...] | None]
        ] = []
        self.local_settings: dict[str, str] = {}
        self.local_snapshots: list[dict[str, str]] = []
        self.readiness_result = (1,)
        self.membership_rows: list[tuple[object, ...]] = []
        self.membership_query_error: Exception | None = None
        self.cursor_executions: list[tuple[str, tuple[str, ...]]] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    def execute(
        self,
        sql: str,
        params: tuple[str, ...] | None = None,
    ):
        assert self.in_transaction is True
        self.statements.append((sql, params))
        if sql == runtime_pool.READINESS_SQL:
            assert params is None
            self.events.append("readiness")
            return self
        assert sql == runtime_pool.SET_LOCAL_CONFIG_SQL
        assert params is not None
        assert sql.count("%s") == len(params)
        assert sql.count("set_config(") == 7
        assert len(params) == 14
        self.events.append("settings")
        for index in range(0, len(params), 2):
            self.local_settings[params[index]] = params[index + 1]
        return self

    def fetchone(self):
        return self.readiness_result

    def cursor(self):
        return _FakeCursor(self)


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def execute(self, sql: str, params: tuple[str, ...]) -> None:
        self.connection.cursor_executions.append((sql, params))
        self.connection.events.append("business_execute")
        if self.connection.membership_query_error is not None:
            raise self.connection.membership_query_error

    def fetchall(self):
        self.connection.events.append("business_fetch")
        return self.connection.membership_rows


class _FakePool:
    def __init__(
        self,
        spec: runtime_pool.RuntimePoolSpec,
        *,
        fail_open: bool = False,
    ) -> None:
        self.spec = spec
        self.connection_object = _FakeConnection()
        self.fail_open = fail_open
        self.open_calls: list[bool] = []
        self.close_calls: list[float] = []
        self.checkout_timeouts: list[float] = []
        self.active_checkouts = 0
        self.returned_connections = 0

    def open(self, *, wait: bool = False) -> None:
        self.open_calls.append(wait)
        if self.fail_open:
            raise RuntimeError("injected pool startup failure")

    def close(self, *, timeout: float = 5.0) -> None:
        self.close_calls.append(timeout)

    @contextmanager
    def connection(self, *, timeout: float):
        self.checkout_timeouts.append(timeout)
        self.active_checkouts += 1
        try:
            yield self.connection_object
        finally:
            self.active_checkouts -= 1
            self.returned_connections += 1


def _registry(
    environ: dict[str, str] | None = None,
) -> tuple[
    runtime_pool.RuntimePoolRegistry,
    dict[runtime_pool.RuntimePoolName, _FakePool],
]:
    pools: dict[runtime_pool.RuntimePoolName, _FakePool] = {}

    def factory(spec: runtime_pool.RuntimePoolSpec) -> _FakePool:
        pool = _FakePool(spec)
        pools[spec.name] = pool
        return pool

    registry = runtime_pool.build_runtime_pool_registry(
        environ=environ or {"REALYTICS_DATABASE_URL": DIRECT_DSN},
        pool_factory=factory,
    )
    return registry, pools


def test_default_registry_is_bounded_and_budgeted() -> None:
    registry, pools = _registry()

    assert registry.configured is True
    assert registry.opened is False
    assert registry.replica_count == 1
    assert registry.connection_budget == 20
    assert registry.required_connection_budget == 10
    assert {
        name: spec.max_size for name, spec in registry.specs.items()
    } == {
        runtime_pool.RuntimePoolName.API: 5,
        runtime_pool.RuntimePoolName.PF_WORKER: 3,
        runtime_pool.RuntimePoolName.IDENTITY: 2,
    }
    assert all(spec.min_size == 0 for spec in registry.specs.values())
    assert all(pool.open_calls == [] for pool in pools.values())


def test_distinct_dsns_and_pool_limits_are_configurable() -> None:
    env = {
        "REALYTICS_DATABASE_URL": DIRECT_DSN,
        "REALYTICS_PF_WORKER_DATABASE_URL": (
            "postgresql://pf:secret@pf.example.test:5432/realytics"
        ),
        "REALYTICS_IDENTITY_DATABASE_URL": (
            "postgresql://identity:secret@id.example.test:5432/realytics"
        ),
        "REALYTICS_API_DB_POOL_MAX": "4",
        "REALYTICS_PF_WORKER_DB_POOL_MAX": "2",
        "REALYTICS_IDENTITY_DB_POOL_MAX": "1",
        "REALYTICS_DB_POOL_TIMEOUT_SECONDS": "7.5",
        "REALYTICS_API_DB_POOL_TIMEOUT_SECONDS": "1.25",
        "REALYTICS_DB_CONNECT_TIMEOUT_SECONDS": "8",
        "REALYTICS_DB_POOL_MAX_WAITING": "9",
        "REALYTICS_DB_POOL_MAX_LIFETIME_SECONDS": "901",
        "REALYTICS_DB_POOL_MAX_IDLE_SECONDS": "91",
        "REALYTICS_DB_POOL_RECONNECT_TIMEOUT_SECONDS": "19",
        "REALYTICS_DB_STATEMENT_TIMEOUT_MS": "21000",
        "REALYTICS_DB_LOCK_TIMEOUT_MS": "2100",
        "REALYTICS_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": "8100",
    }

    registry, _pools = _registry(env)
    specs = registry.specs

    assert specs[runtime_pool.RuntimePoolName.API].dsn == DIRECT_DSN
    assert (
        specs[runtime_pool.RuntimePoolName.PF_WORKER].dsn
        == env["REALYTICS_PF_WORKER_DATABASE_URL"]
    )
    assert (
        specs[runtime_pool.RuntimePoolName.IDENTITY].dsn
        == env["REALYTICS_IDENTITY_DATABASE_URL"]
    )
    assert specs[runtime_pool.RuntimePoolName.API].acquire_timeout_seconds == 1.25
    assert (
        specs[
            runtime_pool.RuntimePoolName.PF_WORKER
        ].acquire_timeout_seconds
        == 7.5
    )
    assert all(spec.connect_timeout_seconds == 8 for spec in specs.values())
    assert all(spec.max_waiting == 9 for spec in specs.values())
    assert all(spec.max_lifetime_seconds == 901 for spec in specs.values())
    assert all(spec.max_idle_seconds == 91 for spec in specs.values())
    assert all(spec.reconnect_timeout_seconds == 19 for spec in specs.values())
    assert all(
        spec.transaction_timeouts
        == runtime_pool.TransactionTimeouts(
            statement_ms=21_000,
            lock_ms=2_100,
            idle_in_transaction_ms=8_100,
        )
        for spec in specs.values()
    )
    assert registry.required_connection_budget == 7
    assert "secret" not in repr(specs[runtime_pool.RuntimePoolName.API])


def test_replica_pool_budget_is_enforced_before_pool_creation() -> None:
    factory_called = False

    def factory(_spec):
        nonlocal factory_called
        factory_called = True
        raise AssertionError("factory must not run for an invalid budget")

    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match=r"replicas\(3\).*30, budget=20",
    ):
        runtime_pool.build_runtime_pool_registry(
            environ={
                "REALYTICS_DATABASE_URL": DIRECT_DSN,
                "REALYTICS_DB_REPLICA_COUNT": "3",
            },
            pool_factory=factory,
        )

    assert factory_called is False

    accepted, _pools = _registry(
        {
            "REALYTICS_DATABASE_URL": DIRECT_DSN,
            "REALYTICS_DB_REPLICA_COUNT": "3",
            "REALYTICS_DB_CONNECTION_BUDGET": "30",
        }
    )
    assert accepted.required_connection_budget == 30


def test_hardened_api_process_requires_explicit_roles_and_dedicated_dsns() -> None:
    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match=runtime_pool.RUNTIME_POOL_ROLES_ENV,
    ):
        runtime_pool.build_runtime_pool_registry(
            environ={
                "REALYTICS_ENVIRONMENT": "production",
                "REALYTICS_DATABASE_URL": DIRECT_DSN,
            },
            pool_factory=lambda spec: _FakePool(spec),
        )

    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match="REALYTICS_API_DATABASE_URL",
    ):
        runtime_pool.build_runtime_pool_registry(
            environ={
                "REALYTICS_ENVIRONMENT": "production",
                runtime_pool.RUNTIME_POOL_ROLES_ENV: "api,identity",
                "REALYTICS_DATABASE_URL": DIRECT_DSN,
            },
            pool_factory=lambda spec: _FakePool(spec),
        )

    dedicated_env = {
        "REALYTICS_ENVIRONMENT": "production",
        runtime_pool.RUNTIME_POOL_ROLES_ENV: "identity,api",
        "REALYTICS_API_DATABASE_ROLE": "realytics_runtime",
        "REALYTICS_IDENTITY_DATABASE_ROLE": "realytics_identity",
        "REALYTICS_DATABASE_URL": DIRECT_DSN,
        "REALYTICS_API_DATABASE_URL": (
            "postgresql://api:secret@api.example.test:5432/realytics"
        ),
        "REALYTICS_IDENTITY_DATABASE_URL": (
            "postgresql://identity:secret@id.example.test:5432/realytics"
        ),
    }
    registry, pools = _registry(dedicated_env)

    assert set(registry.specs) == {
        runtime_pool.RuntimePoolName.API,
        runtime_pool.RuntimePoolName.IDENTITY,
    }
    assert registry.required_connection_budget == 7
    assert set(pools) == set(registry.specs)
    api_spec = registry.specs[runtime_pool.RuntimePoolName.API]
    assert api_spec.expected_database_role == "realytics_runtime"
    assert api_spec.require_role_preflight is True
    identity_spec = registry.specs[runtime_pool.RuntimePoolName.IDENTITY]
    assert identity_spec.expected_database_role == "realytics_identity"
    assert identity_spec.require_role_preflight is True


def test_hardened_api_registry_runs_role_preflight_before_ready(
    monkeypatch,
) -> None:
    registry, _pools = _registry(
        {
            "REALYTICS_ENVIRONMENT": "production",
            runtime_pool.RUNTIME_POOL_ROLES_ENV: "api,identity",
            "REALYTICS_API_DATABASE_URL": DIRECT_DSN,
            "REALYTICS_API_DATABASE_ROLE": "realytics_runtime",
            "REALYTICS_IDENTITY_DATABASE_ROLE": "realytics_identity",
            "REALYTICS_IDENTITY_DATABASE_URL": (
                "postgresql://identity:secret@id.example.test:5432/realytics"
            ),
        }
    )
    calls: list[tuple[str, object, str, str, str]] = []

    def api_preflight(
        connection,
        *,
        expected_role,
        schema_name,
        postgis_schema_name,
    ) -> None:
        calls.append(
            (
                "api",
                connection,
                expected_role,
                schema_name,
                postgis_schema_name,
            )
        )

    monkeypatch.setattr(
        runtime_pool,
        "assert_api_runtime_connection",
        api_preflight,
    )

    def identity_preflight(
        connection,
        *,
        expected_role,
        schema_name,
        postgis_schema_name,
    ) -> None:
        calls.append(
            (
                "identity",
                connection,
                expected_role,
                schema_name,
                postgis_schema_name,
            )
        )

    monkeypatch.setattr(
        runtime_pool,
        "assert_identity_runtime_connection",
        identity_preflight,
    )

    assert registry.open_all() is True
    assert len(calls) == 2
    assert {(call[0], *call[2:]) for call in calls} == {
        ("api", "realytics_runtime", "realytics", "extensions"),
        ("identity", "realytics_identity", "realytics", "extensions"),
    }


def test_hardened_pf_worker_must_run_with_only_its_dedicated_pool() -> None:
    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match="must be exactly",
    ):
        runtime_pool.build_runtime_pool_registry(
            environ={
                "REALYTICS_ENVIRONMENT": "staging",
                runtime_pool.RUNTIME_POOL_ROLES_ENV: (
                    "api,identity,pf_worker"
                ),
                "REALYTICS_API_DATABASE_URL": DIRECT_DSN,
                "REALYTICS_IDENTITY_DATABASE_URL": DIRECT_DSN,
                "REALYTICS_PF_WORKER_DATABASE_URL": DIRECT_DSN,
            },
            pool_factory=lambda spec: _FakePool(spec),
        )

    registry, pools = _registry(
        {
            "REALYTICS_ENVIRONMENT": "staging",
            runtime_pool.RUNTIME_POOL_ROLES_ENV: "pf_worker",
            "REALYTICS_PF_WORKER_DATABASE_ROLE": "realytics_pf_worker",
            "REALYTICS_PF_WORKER_DATABASE_URL": (
                "postgresql://pf:secret@pf.example.test:5432/realytics"
            ),
        }
    )

    assert set(registry.specs) == {
        runtime_pool.RuntimePoolName.PF_WORKER
    }
    assert registry.required_connection_budget == 3
    assert set(pools) == {runtime_pool.RuntimePoolName.PF_WORKER}
    spec = registry.specs[runtime_pool.RuntimePoolName.PF_WORKER]
    assert spec.expected_database_role == "realytics_pf_worker"
    assert spec.require_role_preflight is True


def test_hardened_worker_checks_role_before_becoming_ready(monkeypatch) -> None:
    from db.postgres_pf_worker_runtime_preflight import PFWorkerRuntimePreflightError, PFWorkerRuntimePreflightIssue
    registry, pools = _registry({
        "REALYTICS_ENVIRONMENT": "staging",
        runtime_pool.RUNTIME_POOL_ROLES_ENV: "pf_worker",
        "REALYTICS_PF_WORKER_DATABASE_URL": DIRECT_DSN,
        "REALYTICS_PF_WORKER_DATABASE_ROLE": "realytics_pf_worker",
    })
    called = []
    def reject(connection, **kwargs):
        called.append(kwargs["expected_role"])
        raise PFWorkerRuntimePreflightError((PFWorkerRuntimePreflightIssue("wrong_pf_worker_login_role"),))
    monkeypatch.setattr(runtime_pool, "assert_pf_worker_runtime_connection", reject)
    with pytest.raises(runtime_pool.RuntimeDatabaseConfigurationError, match="wrong_pf_worker_login_role"):
        registry.open_all()
    assert called == ["realytics_pf_worker"]
    assert registry.opened is False
    assert pools[runtime_pool.RuntimePoolName.PF_WORKER].close_calls


@pytest.mark.parametrize("role", [None, "realytics_runtime", "realytics_identity"])
def test_hardened_worker_requires_distinct_explicit_role(role) -> None:
    env = {
        "REALYTICS_ENVIRONMENT": "staging",
        runtime_pool.RUNTIME_POOL_ROLES_ENV: "pf_worker",
        "REALYTICS_PF_WORKER_DATABASE_URL": DIRECT_DSN,
    }
    if role:
        env["REALYTICS_PF_WORKER_DATABASE_ROLE"] = role
    with pytest.raises(runtime_pool.RuntimeDatabaseConfigurationError):
        _registry(env)


@pytest.mark.parametrize(
    "dsn",
    [
        DIRECT_DSN,
        (
            "postgresql://postgres.project:secret@"
            "aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"
        ),
        "host=localhost port=55432 dbname=realytics user=runtime",
    ],
)
def test_direct_session_and_non_transaction_ports_are_accepted(dsn: str) -> None:
    assert runtime_pool.validate_runtime_dsn(dsn, environ={}) is False


@pytest.mark.parametrize(
    "dsn",
    [
        (
            "postgresql://postgres.project:secret@"
            "aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres"
        ),
        (
            "postgresql://postgres.project:secret@"
            "aws-0-ap-northeast-2.pooler.supabase.com/postgres?port=6543"
        ),
        "host=pooler.supabase.com port='6543' dbname=postgres",
    ],
)
def test_transaction_pooler_requires_explicit_local_opt_in(dsn: str) -> None:
    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match="transaction-mode port 6543",
    ):
        runtime_pool.validate_runtime_dsn(dsn, environ={})

    assert runtime_pool.validate_runtime_dsn(
        dsn,
        environ={
            runtime_pool.TRANSACTION_POOLER_OPT_IN_ENV: "true",
            "REALYTICS_ENVIRONMENT": "development",
        },
    ) is True


def test_transaction_pooler_opt_in_is_forbidden_when_hardened() -> None:
    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match="forbidden in hardened",
    ):
        runtime_pool.validate_runtime_dsn(
            DIRECT_DSN,
            environ={
                runtime_pool.TRANSACTION_POOLER_OPT_IN_ENV: "true",
                "REALYTICS_ENVIRONMENT": "production",
            },
        )


def test_unconfigured_local_registry_does_not_create_or_open_pools() -> None:
    factory_calls = 0

    def factory(_spec):
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("unconfigured local startup must stay offline")

    registry = runtime_pool.build_runtime_pool_registry(
        environ={},
        pool_factory=factory,
    )

    assert registry.configured is False
    assert registry.open_all() is False
    assert registry.opened is False
    registry.close_all()
    assert factory_calls == 0


def test_pool_lifecycle_opens_once_and_closes_once() -> None:
    registry, pools = _registry()

    assert registry.open_all() is True
    assert registry.open_all() is True
    assert registry.opened is True
    assert all(pool.open_calls == [False] for pool in pools.values())
    assert all(
        pool.connection_object.statements
        == [(runtime_pool.READINESS_SQL, None)]
        for pool in pools.values()
    )
    assert all(
        pool.checkout_timeouts == [pool.spec.acquire_timeout_seconds]
        for pool in pools.values()
    )

    registry.close_all()
    registry.close_all()
    assert registry.opened is False
    assert all(pool.close_calls == [5.0] for pool in pools.values())

    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match="cannot be reopened",
    ):
        registry.open_all()


def test_partial_pool_startup_failure_closes_every_pool() -> None:
    pools: dict[runtime_pool.RuntimePoolName, _FakePool] = {}

    def factory(spec: runtime_pool.RuntimePoolSpec) -> _FakePool:
        pool = _FakePool(
            spec,
            fail_open=(
                spec.name is runtime_pool.RuntimePoolName.PF_WORKER
            ),
        )
        pools[spec.name] = pool
        return pool

    registry = runtime_pool.build_runtime_pool_registry(
        environ={"REALYTICS_DATABASE_URL": DIRECT_DSN},
        pool_factory=factory,
    )

    with pytest.raises(RuntimeError, match="startup failure"):
        registry.open_all()

    assert registry.opened is False
    assert all(pool.close_calls == [5.0] for pool in pools.values())


def test_readiness_query_failure_closes_every_pool() -> None:
    pools: dict[runtime_pool.RuntimePoolName, _FakePool] = {}

    def factory(spec: runtime_pool.RuntimePoolSpec) -> _FakePool:
        pool = _FakePool(spec)
        if spec.name is runtime_pool.RuntimePoolName.IDENTITY:
            pool.connection_object.readiness_result = (0,)
        pools[spec.name] = pool
        return pool

    registry = runtime_pool.build_runtime_pool_registry(
        environ={"REALYTICS_DATABASE_URL": DIRECT_DSN},
        pool_factory=factory,
    )

    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match="'identity'",
    ):
        registry.open_all()

    assert registry.opened is False
    assert all(pool.close_calls == [5.0] for pool in pools.values())


def test_api_tenant_transaction_is_explicit_parameterized_and_does_not_bleed() -> None:
    registry, pools = _registry(
        {
            "REALYTICS_DATABASE_URL": DIRECT_DSN,
            "REALYTICS_API_DB_POOL_TIMEOUT_SECONDS": "1.5",
            "REALYTICS_API_DB_STATEMENT_TIMEOUT_MS": "12000",
            "REALYTICS_API_DB_LOCK_TIMEOUT_MS": "1400",
            "REALYTICS_API_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": "4300",
        }
    )
    registry.open_all()
    connection = pools[
        runtime_pool.RuntimePoolName.API
    ].connection_object

    with runtime_pool.api_tenant_transaction(
        " tenant-a ",
        registry=registry,
    ) as checked_out:
        assert checked_out is connection
        assert connection.in_transaction is True
        assert connection.local_settings == {
            "realytics.tenant_id": "tenant-a",
            "realytics.principal_issuer": "",
            "realytics.principal_subject": "",
            "search_path": runtime_pool.SAFE_RUNTIME_SEARCH_PATH,
            "statement_timeout": "12000ms",
            "lock_timeout": "1400ms",
            "idle_in_transaction_session_timeout": "4300ms",
        }

    assert connection.in_transaction is False
    assert connection.local_settings == {}
    assert connection.commits == 2
    assert connection.rollbacks == 0
    business_statements = [
        (sql, params)
        for sql, params in connection.statements
        if sql != runtime_pool.READINESS_SQL
    ]
    assert len(business_statements) == 1
    assert all(
        sql == runtime_pool.SET_LOCAL_CONFIG_SQL
        for sql, _params in business_statements
    )
    assert all(
        "tenant-a" not in sql for sql, _params in business_statements
    )
    assert pools[
        runtime_pool.RuntimePoolName.API
    ].checkout_timeouts == [1.5, 1.5]

    with runtime_pool.api_tenant_transaction(
        "tenant-b",
        registry=registry,
    ):
        assert connection.local_settings["realytics.tenant_id"] == "tenant-b"

    assert connection.local_settings == {}
    assert [
        snapshot["realytics.tenant_id"]
        for snapshot in connection.local_snapshots
        if "realytics.tenant_id" in snapshot
    ] == ["tenant-a", "tenant-b"]


def test_pipeline_batches_begin_settings_business_read_and_commit(
    monkeypatch,
) -> None:
    registry, pools = _registry(
        {
            "REALYTICS_DATABASE_URL": DIRECT_DSN,
            "REALYTICS_IDENTITY_DB_POOL_MAX": "1",
        }
    )
    registry.open_all()
    pool = pools[runtime_pool.RuntimePoolName.IDENTITY]
    connection = pool.connection_object
    connection.events.clear()
    monkeypatch.setattr(
        runtime_pool,
        "_supports_psycopg_pipeline",
        lambda checked_connection: checked_connection is connection,
    )

    with runtime_pool.identity_transaction(registry=registry) as checked_out:
        with checked_out.cursor() as cursor:
            cursor.execute(
                "SELECT tenant_id FROM realytics.memberships WHERE id = %s",
                ("membership-1",),
            )
            assert cursor.fetchall() == []

    assert connection.events == [
        "pipeline_enter",
        "begin",
        "settings",
        "business_execute",
        "business_fetch",
        "commit",
        "pipeline_exit",
    ]
    assert connection.pipeline_enters == 1
    assert connection.pipeline_exits == 1
    assert connection.in_transaction is False
    assert connection.pipeline_active is False
    assert connection.local_settings == {}
    assert pool.active_checkouts == 0
    assert pool.returned_connections == 2  # readiness + business transaction


def test_pipeline_rolls_back_and_returns_connection_after_business_error(
    monkeypatch,
) -> None:
    registry, pools = _registry(
        {
            "REALYTICS_DATABASE_URL": DIRECT_DSN,
            "REALYTICS_IDENTITY_DB_POOL_MAX": "1",
        }
    )
    registry.open_all()
    pool = pools[runtime_pool.RuntimePoolName.IDENTITY]
    connection = pool.connection_object
    connection.events.clear()
    connection.membership_query_error = RuntimeError("injected query failure")
    monkeypatch.setattr(
        runtime_pool,
        "_supports_psycopg_pipeline",
        lambda checked_connection: checked_connection is connection,
    )

    with pytest.raises(RuntimeError, match="injected query failure"):
        with runtime_pool.identity_transaction(
            registry=registry
        ) as checked_out:
            with checked_out.cursor() as cursor:
                cursor.execute(
                    "SELECT tenant_id FROM realytics.memberships WHERE id = %s",
                    ("membership-1",),
                )

    assert connection.events == [
        "pipeline_enter",
        "begin",
        "settings",
        "business_execute",
        "rollback",
        "pipeline_exit",
    ]
    assert connection.pipeline_enters == 1
    assert connection.pipeline_exits == 1
    assert connection.commits == 1  # readiness only
    assert connection.rollbacks == 1
    assert connection.in_transaction is False
    assert connection.pipeline_active is False
    assert connection.local_settings == {}
    assert pool.active_checkouts == 0
    assert pool.returned_connections == 2


def test_non_psycopg_connection_gracefully_uses_regular_transaction(
    monkeypatch,
) -> None:
    registry, pools = _registry()
    registry.open_all()
    pool = pools[runtime_pool.RuntimePoolName.API]
    connection = pool.connection_object
    connection.events.clear()
    monkeypatch.setattr(
        runtime_pool,
        "_supports_psycopg_pipeline",
        lambda _connection: False,
    )

    with runtime_pool.api_tenant_transaction(
        "tenant-a",
        registry=registry,
    ) as checked_out:
        with checked_out.cursor() as cursor:
            cursor.execute(
                "SELECT project_id FROM realytics.projects WHERE id = %s",
                ("project-1",),
            )
            assert cursor.fetchall() == []

    assert connection.events == [
        "begin",
        "settings",
        "business_execute",
        "business_fetch",
        "commit",
    ]
    assert connection.pipeline_enters == 0
    assert connection.pipeline_exits == 0
    assert pool.active_checkouts == 0
    assert pool.returned_connections == 2


def test_pipeline_capability_rejects_non_psycopg_connection() -> None:
    # A third-party or test double may expose a method with the same name.  It
    # must not enter the libpq-specific path without being a psycopg checkout.
    connection = _FakeConnection()

    assert runtime_pool._supports_psycopg_pipeline(connection) is False


def test_tenant_transaction_rejects_blank_before_checkout() -> None:
    registry, pools = _registry()
    registry.open_all()
    checkout_count = len(
        pools[runtime_pool.RuntimePoolName.API].checkout_timeouts
    )

    with pytest.raises(ValueError, match="non-blank tenant_id"):
        with runtime_pool.api_tenant_transaction("   ", registry=registry):
            raise AssertionError("unreachable")

    assert pools[
        runtime_pool.RuntimePoolName.API
    ].checkout_timeouts[checkout_count:] == []


def test_failed_pf_worker_transaction_rolls_back_and_clears_locals() -> None:
    registry, pools = _registry()
    registry.open_all()
    connection = pools[
        runtime_pool.RuntimePoolName.PF_WORKER
    ].connection_object

    with pytest.raises(RuntimeError, match="injected work failure"):
        with runtime_pool.pf_worker_tenant_transaction(
            "tenant-pf",
            registry=registry,
        ):
            raise RuntimeError("injected work failure")

    assert connection.commits == 1
    assert connection.rollbacks == 1
    assert connection.local_settings == {}
    assert connection.local_snapshots[-1]["realytics.tenant_id"] == "tenant-pf"


def test_identity_transaction_explicitly_clears_tenant_context() -> None:
    registry, pools = _registry()
    registry.open_all()
    connection = pools[
        runtime_pool.RuntimePoolName.IDENTITY
    ].connection_object

    with runtime_pool.identity_transaction(registry=registry):
        assert connection.local_settings["realytics.tenant_id"] == ""
        assert connection.local_settings["realytics.principal_issuer"] == ""
        assert connection.local_settings["realytics.principal_subject"] == ""

    assert connection.local_settings == {}
    assert connection.local_snapshots[-1]["realytics.tenant_id"] == ""


def test_identity_principal_transaction_sets_verified_pair_without_tenant() -> None:
    registry, pools = _registry()
    registry.open_all()
    connection = pools[
        runtime_pool.RuntimePoolName.IDENTITY
    ].connection_object

    with runtime_pool.identity_principal_transaction(
        " https://issuer.example/ ",
        " subject-1 ",
        registry=registry,
    ):
        assert connection.local_settings == {
            "realytics.tenant_id": "",
            "realytics.principal_issuer": "https://issuer.example",
            "realytics.principal_subject": "subject-1",
            "search_path": runtime_pool.SAFE_RUNTIME_SEARCH_PATH,
            "statement_timeout": "30000ms",
            "lock_timeout": "5000ms",
            "idle_in_transaction_session_timeout": "15000ms",
        }

    assert connection.local_settings == {}
    assert connection.local_snapshots[-1]["realytics.tenant_id"] == ""


def test_successful_membership_lookup_commits_and_returns_identity_connection() -> None:
    registry, pools = _registry(
        {
            "REALYTICS_DATABASE_URL": DIRECT_DSN,
            "REALYTICS_IDENTITY_DB_POOL_MAX": "1",
        }
    )
    registry.open_all()
    pool = pools[runtime_pool.RuntimePoolName.IDENTITY]
    connection = pool.connection_object
    connection.membership_rows = [
        (
            "https://issuer.example",
            "subject-1",
            "human",
            "active",
            "person@example.com",
            "Person",
            None,
            "tenant-a",
            "active",
            "admin",
            "active",
            1,
        )
    ]
    repository = PostgresIdentityMembershipRepository(
        transaction_factory=lambda: runtime_pool.identity_transaction(
            registry=registry
        )
    )

    directory = repository.load_directory(
        issuer="https://issuer.example/",
        subject="subject-1",
    )

    assert directory.principal is not None
    assert directory.memberships[0].tenant_id == "tenant-a"
    assert connection.commits == 2  # readiness + membership lookup
    assert connection.rollbacks == 0
    assert connection.in_transaction is False
    assert pool.active_checkouts == 0
    assert pool.returned_connections == 2
    assert len(connection.cursor_executions) == 1


def test_failed_membership_lookup_rolls_back_and_returns_identity_connection() -> None:
    registry, pools = _registry(
        {
            "REALYTICS_DATABASE_URL": DIRECT_DSN,
            "REALYTICS_IDENTITY_DB_POOL_MAX": "1",
        }
    )
    registry.open_all()
    pool = pools[runtime_pool.RuntimePoolName.IDENTITY]
    connection = pool.connection_object
    connection.membership_query_error = RuntimeError("injected query failure")
    repository = PostgresIdentityMembershipRepository(
        transaction_factory=lambda: runtime_pool.identity_transaction(
            registry=registry
        )
    )

    with pytest.raises(IdentityMembershipRepositoryUnavailable):
        repository.load_directory(
            issuer="https://issuer.example",
            subject="subject-1",
        )

    assert connection.commits == 1  # readiness only
    assert connection.rollbacks == 1
    assert connection.in_transaction is False
    assert connection.local_settings == {}
    assert pool.active_checkouts == 0
    assert pool.returned_connections == 2
    assert len(connection.cursor_executions) == 1


def test_transactions_require_an_open_pool_registry() -> None:
    registry, _pools = _registry()

    with pytest.raises(
        runtime_pool.RuntimeDatabaseConfigurationError,
        match="not open",
    ):
        with runtime_pool.api_tenant_transaction(
            "tenant-a",
            registry=registry,
        ):
            raise AssertionError("unreachable")


def test_fastapi_lifespan_owns_runtime_pool_lifecycle(monkeypatch) -> None:
    import main

    events: list[str] = []
    sentinel = object()

    def open_pools():
        events.append("open")
        return sentinel

    def close_pools():
        events.append("close")

    monkeypatch.setattr(main, "open_runtime_pools", open_pools)
    monkeypatch.setattr(main, "close_runtime_pools", close_pools)

    async def exercise() -> None:
        async with main.lifespan(main.app):
            assert main.app.state.postgres_runtime_pools is sentinel
            events.append("serve")
        assert main.app.state.postgres_runtime_pools is None

    asyncio.run(exercise())

    assert events == ["open", "serve", "close"]


def test_runtime_dependencies_are_exactly_pinned() -> None:
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8")

    assert "psycopg[binary]==3.3.4" in requirements
    assert "psycopg-pool==3.3.1" in requirements
    assert "psycopg[binary]>=" not in requirements


def test_migrations_remain_on_the_direct_connection_boundary() -> None:
    migrate_script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "migrate_postgres.py"
    ).read_text(encoding="utf-8")

    assert "postgres.connect(dsn)" in migrate_script
    assert "postgres_runtime_pool" not in migrate_script
