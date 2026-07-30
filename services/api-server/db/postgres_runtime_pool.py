"""Bounded PostgreSQL pools and transaction-scoped tenant context.

Runtime application traffic uses these pools. Schema migration commands keep
using the direct ``db.postgres.connect`` boundary and are intentionally not
routed through this module.
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from urllib.parse import parse_qs, quote_plus, urlsplit

from db.postgres_api_runtime_preflight import (
    API_DATABASE_ROLE_ENV,
    ApiRuntimePreflightError,
    assert_api_runtime_connection,
)
from db.postgres_identity_runtime_preflight import (
    DEFAULT_IDENTITY_DATABASE_ROLE,
    IDENTITY_DATABASE_ROLE_ENV,
    IdentityRuntimePreflightError,
    assert_identity_runtime_connection,
)


DEFAULT_CONNECTION_BUDGET = 20
DEFAULT_REPLICA_COUNT = 1
DEFAULT_ACQUIRE_TIMEOUT_SECONDS = 5.0
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_MAX_WAITING = 20
DEFAULT_MAX_LIFETIME_SECONDS = 1_800.0
DEFAULT_MAX_IDLE_SECONDS = 300.0
DEFAULT_RECONNECT_TIMEOUT_SECONDS = 30.0
DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
DEFAULT_LOCK_TIMEOUT_MS = 5_000
DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS = 15_000
TRANSACTION_POOLER_PORT = 6543
TRANSACTION_POOLER_OPT_IN_ENV = (
    "REALYTICS_ALLOW_SUPAVISOR_TRANSACTION_POOLER"
)
RUNTIME_POOL_ROLES_ENV = "REALYTICS_DB_POOL_ROLES"
HARDENED_ENVIRONMENTS = frozenset({"production", "prod", "staging"})
# Configure every transaction-local setting in one database round trip.  The
# runtime can be several network hops away (for example, a Supabase session
# pooler), so issuing one ``SELECT set_config`` per setting keeps a checked-out
# connection idle-in-transaction for the cumulative latency of seven queries.
# The values remain parameters and ``is_local=true`` still scopes every value
# to the surrounding ``connection.transaction()`` block.
SET_LOCAL_CONFIG_SQL = """
SELECT
    set_config(%s, %s, true),
    set_config(%s, %s, true),
    set_config(%s, %s, true),
    set_config(%s, %s, true),
    set_config(%s, %s, true),
    set_config(%s, %s, true),
    set_config(%s, %s, true)
""".strip()
READINESS_SQL = "SELECT 1"
SAFE_RUNTIME_SEARCH_PATH = "pg_catalog, realytics, extensions"


class RuntimePoolName(StrEnum):
    API = "api"
    PF_WORKER = "pf_worker"
    IDENTITY = "identity"


_DEFAULT_POOL_MAX_SIZE = {
    RuntimePoolName.API: 5,
    RuntimePoolName.PF_WORKER: 3,
    RuntimePoolName.IDENTITY: 2,
}
_POOL_ENV_PREFIX = {
    RuntimePoolName.API: "REALYTICS_API_DB",
    RuntimePoolName.PF_WORKER: "REALYTICS_PF_WORKER_DB",
    RuntimePoolName.IDENTITY: "REALYTICS_IDENTITY_DB",
}
_POOL_DSN_ENV = {
    RuntimePoolName.API: "REALYTICS_API_DATABASE_URL",
    RuntimePoolName.PF_WORKER: "REALYTICS_PF_WORKER_DATABASE_URL",
    RuntimePoolName.IDENTITY: "REALYTICS_IDENTITY_DATABASE_URL",
}


class RuntimeDatabaseConfigurationError(RuntimeError):
    """Runtime PostgreSQL configuration cannot be used safely."""


class _RuntimePool(Protocol):
    def open(self, *, wait: bool = False) -> None: ...

    def close(self, *, timeout: float = 5.0) -> None: ...

    def connection(self, *, timeout: float): ...


@dataclass(frozen=True)
class TransactionTimeouts:
    statement_ms: int
    lock_ms: int
    idle_in_transaction_ms: int


@dataclass(frozen=True)
class RuntimePoolSpec:
    name: RuntimePoolName
    dsn: str = field(repr=False)
    min_size: int
    max_size: int
    acquire_timeout_seconds: float
    connect_timeout_seconds: int
    max_waiting: int
    max_lifetime_seconds: float
    max_idle_seconds: float
    reconnect_timeout_seconds: float
    transaction_timeouts: TransactionTimeouts
    transaction_pooler: bool
    expected_database_role: str | None = None
    runtime_schema: str = "realytics"
    postgis_schema: str = "extensions"
    require_role_preflight: bool = False


PoolFactory = Callable[[RuntimePoolSpec], _RuntimePool]


def _flag(environ: Mapping[str, str], key: str) -> bool:
    return str(environ.get(key) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_hardened(environ: Mapping[str, str]) -> bool:
    environment = str(
        environ.get("REALYTICS_ENVIRONMENT") or "development"
    ).strip().casefold()
    return environment in HARDENED_ENVIRONMENTS


def _positive_int(
    environ: Mapping[str, str],
    key: str,
    default: int,
) -> int:
    raw = str(environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeDatabaseConfigurationError(
            f"{key} must be a positive integer"
        ) from exc
    if value <= 0:
        raise RuntimeDatabaseConfigurationError(
            f"{key} must be a positive integer"
        )
    return value


def _positive_float(
    environ: Mapping[str, str],
    key: str,
    default: float,
) -> float:
    raw = str(environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeDatabaseConfigurationError(
            f"{key} must be a positive finite number"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise RuntimeDatabaseConfigurationError(
            f"{key} must be a positive finite number"
        )
    return value


def _setting_int(
    environ: Mapping[str, str],
    *,
    pool_prefix: str,
    suffix: str,
    global_key: str,
    default: int,
) -> int:
    pool_key = f"{pool_prefix}_{suffix}"
    if str(environ.get(pool_key) or "").strip():
        return _positive_int(environ, pool_key, default)
    return _positive_int(environ, global_key, default)


def _setting_float(
    environ: Mapping[str, str],
    *,
    pool_prefix: str,
    suffix: str,
    global_key: str,
    default: float,
) -> float:
    pool_key = f"{pool_prefix}_{suffix}"
    if str(environ.get(pool_key) or "").strip():
        return _positive_float(environ, pool_key, default)
    return _positive_float(environ, global_key, default)


def _base_runtime_dsn(environ: Mapping[str, str]) -> str | None:
    configured_url = str(
        environ.get("REALYTICS_DATABASE_URL") or ""
    ).strip()
    if configured_url:
        return configured_url

    host = str(environ.get("REALYTICS_PG_HOST") or "").strip()
    if not host:
        return None
    port = str(environ.get("REALYTICS_PG_PORT") or "5432").strip()
    database = str(
        environ.get("REALYTICS_PG_DB") or "realytics"
    ).strip()
    user = str(
        environ.get("REALYTICS_PG_USER") or "realytics"
    ).strip()
    password = str(environ.get("REALYTICS_PG_PASSWORD") or "")
    encoded_user = quote_plus(user)
    if password:
        auth = f"{encoded_user}:{quote_plus(password)}@"
    else:
        auth = f"{encoded_user}@"
    return f"postgresql://{auth}{host}:{port}/{database}"


_KEYWORD_PORT_RE = re.compile(
    r"(?:^|\s)port\s*=\s*(?:\"(?P<double>[^\"]+)\"|"
    r"'(?P<single>[^']+)'|(?P<bare>[^\s]+))",
    flags=re.IGNORECASE,
)


def _dsn_port(dsn: str) -> int:
    if "://" in dsn:
        parsed = urlsplit(dsn)
        if parsed.scheme not in {"postgresql", "postgres"}:
            raise RuntimeDatabaseConfigurationError(
                "Runtime database URL must use postgresql:// or postgres://"
            )
        if not parsed.hostname:
            raise RuntimeDatabaseConfigurationError(
                "Runtime database URL must include a host"
            )
        try:
            explicit_port = parsed.port
        except ValueError as exc:
            raise RuntimeDatabaseConfigurationError(
                "Runtime database URL has an invalid port"
            ) from exc
        query_ports = parse_qs(parsed.query).get("port", ())
        if query_ports:
            try:
                return int(query_ports[-1])
            except ValueError as exc:
                raise RuntimeDatabaseConfigurationError(
                    "Runtime database URL has an invalid port query parameter"
                ) from exc
        return explicit_port or 5432

    match = _KEYWORD_PORT_RE.search(dsn)
    if not match:
        return 5432
    raw_port = next(
        value for value in match.groupdict().values() if value is not None
    )
    try:
        return int(raw_port)
    except ValueError as exc:
        raise RuntimeDatabaseConfigurationError(
            "Runtime database DSN has an invalid port"
        ) from exc


def validate_runtime_dsn(
    dsn: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Validate the runtime endpoint and return whether it is port 6543."""

    env = os.environ if environ is None else environ
    normalized = str(dsn or "").strip()
    if not normalized:
        raise RuntimeDatabaseConfigurationError(
            "Runtime database DSN must not be blank"
        )

    allow_transaction_pooler = _flag(env, TRANSACTION_POOLER_OPT_IN_ENV)
    if _is_hardened(env) and allow_transaction_pooler:
        raise RuntimeDatabaseConfigurationError(
            f"{TRANSACTION_POOLER_OPT_IN_ENV}=true is forbidden in hardened "
            "environments"
        )

    transaction_pooler = _dsn_port(normalized) == TRANSACTION_POOLER_PORT
    if transaction_pooler and not allow_transaction_pooler:
        raise RuntimeDatabaseConfigurationError(
            "Supabase/Supavisor transaction-mode port 6543 is disabled for "
            "the persistent REALYTICS runtime. Use direct or session-mode "
            "port 5432. Local development requires the explicit "
            f"{TRANSACTION_POOLER_OPT_IN_ENV}=true opt-in."
        )
    return transaction_pooler


def _dsn_for_pool(
    name: RuntimePoolName,
    *,
    environ: Mapping[str, str],
    base_dsn: str | None,
) -> str | None:
    dedicated = str(environ.get(_POOL_DSN_ENV[name]) or "").strip()
    return dedicated or base_dsn


def _selected_pool_names(
    environ: Mapping[str, str],
) -> tuple[RuntimePoolName, ...]:
    raw = str(environ.get(RUNTIME_POOL_ROLES_ENV) or "").strip()
    hardened = _is_hardened(environ)
    if not raw:
        if hardened:
            raise RuntimeDatabaseConfigurationError(
                f"{RUNTIME_POOL_ROLES_ENV} is required in hardened "
                "environments; use api,identity for the API process or "
                "pf_worker for the isolated PF worker"
            )
        return tuple(RuntimePoolName)

    requested = {
        item.strip().casefold() for item in raw.split(",") if item.strip()
    }
    allowed = {name.value for name in RuntimePoolName}
    unknown = sorted(requested - allowed)
    if unknown:
        raise RuntimeDatabaseConfigurationError(
            f"{RUNTIME_POOL_ROLES_ENV} contains unknown roles: "
            + ", ".join(unknown)
        )
    if not requested:
        raise RuntimeDatabaseConfigurationError(
            f"{RUNTIME_POOL_ROLES_ENV} must select at least one role"
        )

    selected = tuple(name for name in RuntimePoolName if name.value in requested)
    if hardened:
        selected_set = frozenset(selected)
        api_process_roles = frozenset({
            RuntimePoolName.API,
            RuntimePoolName.IDENTITY,
        })
        pf_worker_roles = frozenset({RuntimePoolName.PF_WORKER})
        if selected_set not in {api_process_roles, pf_worker_roles}:
            raise RuntimeDatabaseConfigurationError(
                f"{RUNTIME_POOL_ROLES_ENV} must be exactly api,identity for "
                "the hardened API process or pf_worker for the isolated "
                "hardened PF worker"
            )
    return selected


def _build_pool_spec(
    name: RuntimePoolName,
    *,
    dsn: str,
    environ: Mapping[str, str],
) -> RuntimePoolSpec:
    prefix = _POOL_ENV_PREFIX[name]
    transaction_pooler = validate_runtime_dsn(dsn, environ=environ)
    hardened = _is_hardened(environ)
    expected_database_role: str | None = None
    if name is RuntimePoolName.API:
        expected_database_role = str(
            environ.get(API_DATABASE_ROLE_ENV)
            or environ.get("REALYTICS_RUNTIME_DATABASE_ROLE")
            or "realytics_runtime"
        ).strip()
    elif name is RuntimePoolName.IDENTITY:
        expected_database_role = str(
            environ.get(IDENTITY_DATABASE_ROLE_ENV)
            or DEFAULT_IDENTITY_DATABASE_ROLE
        ).strip()
    return RuntimePoolSpec(
        name=name,
        dsn=dsn,
        min_size=0,
        max_size=_positive_int(
            environ,
            f"{prefix}_POOL_MAX",
            _DEFAULT_POOL_MAX_SIZE[name],
        ),
        acquire_timeout_seconds=_setting_float(
            environ,
            pool_prefix=prefix,
            suffix="POOL_TIMEOUT_SECONDS",
            global_key="REALYTICS_DB_POOL_TIMEOUT_SECONDS",
            default=DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
        ),
        connect_timeout_seconds=_setting_int(
            environ,
            pool_prefix=prefix,
            suffix="CONNECT_TIMEOUT_SECONDS",
            global_key="REALYTICS_DB_CONNECT_TIMEOUT_SECONDS",
            default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        ),
        max_waiting=_setting_int(
            environ,
            pool_prefix=prefix,
            suffix="POOL_MAX_WAITING",
            global_key="REALYTICS_DB_POOL_MAX_WAITING",
            default=DEFAULT_MAX_WAITING,
        ),
        max_lifetime_seconds=_setting_float(
            environ,
            pool_prefix=prefix,
            suffix="POOL_MAX_LIFETIME_SECONDS",
            global_key="REALYTICS_DB_POOL_MAX_LIFETIME_SECONDS",
            default=DEFAULT_MAX_LIFETIME_SECONDS,
        ),
        max_idle_seconds=_setting_float(
            environ,
            pool_prefix=prefix,
            suffix="POOL_MAX_IDLE_SECONDS",
            global_key="REALYTICS_DB_POOL_MAX_IDLE_SECONDS",
            default=DEFAULT_MAX_IDLE_SECONDS,
        ),
        reconnect_timeout_seconds=_setting_float(
            environ,
            pool_prefix=prefix,
            suffix="POOL_RECONNECT_TIMEOUT_SECONDS",
            global_key="REALYTICS_DB_POOL_RECONNECT_TIMEOUT_SECONDS",
            default=DEFAULT_RECONNECT_TIMEOUT_SECONDS,
        ),
        transaction_timeouts=TransactionTimeouts(
            statement_ms=_setting_int(
                environ,
                pool_prefix=prefix,
                suffix="STATEMENT_TIMEOUT_MS",
                global_key="REALYTICS_DB_STATEMENT_TIMEOUT_MS",
                default=DEFAULT_STATEMENT_TIMEOUT_MS,
            ),
            lock_ms=_setting_int(
                environ,
                pool_prefix=prefix,
                suffix="LOCK_TIMEOUT_MS",
                global_key="REALYTICS_DB_LOCK_TIMEOUT_MS",
                default=DEFAULT_LOCK_TIMEOUT_MS,
            ),
            idle_in_transaction_ms=_setting_int(
                environ,
                pool_prefix=prefix,
                suffix="IDLE_IN_TRANSACTION_TIMEOUT_MS",
                global_key="REALYTICS_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS",
                default=DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_MS,
            ),
        ),
        transaction_pooler=transaction_pooler,
        expected_database_role=expected_database_role,
        runtime_schema=str(
            environ.get("REALYTICS_DATABASE_SCHEMA") or "realytics"
        ).strip(),
        postgis_schema=str(
            environ.get("REALYTICS_POSTGIS_SCHEMA") or "extensions"
        ).strip(),
        require_role_preflight=(
            hardened
            and name in {RuntimePoolName.API, RuntimePoolName.IDENTITY}
        ),
    )


def validate_connection_budget(
    specs: Mapping[RuntimePoolName, RuntimePoolSpec],
    *,
    replica_count: int,
    connection_budget: int,
) -> int:
    if replica_count <= 0:
        raise RuntimeDatabaseConfigurationError(
            "REALYTICS_DB_REPLICA_COUNT must be a positive integer"
        )
    if connection_budget <= 0:
        raise RuntimeDatabaseConfigurationError(
            "REALYTICS_DB_CONNECTION_BUDGET must be a positive integer"
        )
    required = replica_count * sum(spec.max_size for spec in specs.values())
    if required > connection_budget:
        raise RuntimeDatabaseConfigurationError(
            "Runtime PostgreSQL pool budget exceeded: "
            f"replicas({replica_count}) x pool_max_sum("
            f"{sum(spec.max_size for spec in specs.values())}) = "
            f"{required}, budget={connection_budget}"
        )
    return required


def _create_psycopg_pool(spec: RuntimePoolSpec) -> _RuntimePool:
    try:
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - deployment dependency
        raise RuntimeDatabaseConfigurationError(
            "PostgreSQL runtime pooling requires psycopg-pool==3.3.1"
        ) from exc

    connection_kwargs: dict[str, Any] = {
        "autocommit": False,
        "connect_timeout": spec.connect_timeout_seconds,
        "application_name": f"realytics-{spec.name.value}",
    }
    if spec.transaction_pooler:
        connection_kwargs["prepare_threshold"] = None

    return ConnectionPool(
        conninfo=spec.dsn,
        kwargs=connection_kwargs,
        min_size=spec.min_size,
        max_size=spec.max_size,
        open=False,
        name=f"realytics-{spec.name.value}",
        timeout=spec.acquire_timeout_seconds,
        max_waiting=spec.max_waiting,
        max_lifetime=spec.max_lifetime_seconds,
        max_idle=spec.max_idle_seconds,
        reconnect_timeout=spec.reconnect_timeout_seconds,
    )


class RuntimePoolRegistry:
    """Own all process-local pools and their shared connection budget."""

    def __init__(
        self,
        specs: Mapping[RuntimePoolName, RuntimePoolSpec],
        *,
        replica_count: int,
        connection_budget: int,
        pool_factory: PoolFactory = _create_psycopg_pool,
    ) -> None:
        self._specs = dict(specs)
        self.replica_count = replica_count
        self.connection_budget = connection_budget
        self.required_connection_budget = validate_connection_budget(
            self._specs,
            replica_count=replica_count,
            connection_budget=connection_budget,
        )
        self._pools = {
            name: pool_factory(spec) for name, spec in self._specs.items()
        }
        self._lock = threading.RLock()
        self._opened = False
        self._closed = False

    @property
    def configured(self) -> bool:
        return bool(self._pools)

    @property
    def opened(self) -> bool:
        return self._opened

    @property
    def specs(self) -> Mapping[RuntimePoolName, RuntimePoolSpec]:
        return dict(self._specs)

    def open_all(self) -> bool:
        with self._lock:
            if not self.configured:
                return False
            if self._closed:
                raise RuntimeDatabaseConfigurationError(
                    "Closed PostgreSQL pools cannot be reopened"
                )
            if self._opened:
                return True
            try:
                for pool in self._pools.values():
                    pool.open(wait=False)
                for name, pool in self._pools.items():
                    spec = self._specs[name]
                    with pool.connection(
                        timeout=spec.acquire_timeout_seconds
                    ) as connection:
                        with connection.transaction():
                            cursor = connection.execute(READINESS_SQL)
                            if cursor.fetchone() != (1,):
                                raise RuntimeDatabaseConfigurationError(
                                    "PostgreSQL runtime pool readiness query "
                                    f"failed for {name.value!r}"
                                )
                            if spec.require_role_preflight:
                                try:
                                    preflight = (
                                        assert_identity_runtime_connection
                                        if name is RuntimePoolName.IDENTITY
                                        else assert_api_runtime_connection
                                    )
                                    preflight(
                                        connection,
                                        expected_role=(
                                            spec.expected_database_role or ""
                                        ),
                                        schema_name=spec.runtime_schema,
                                        postgis_schema_name=spec.postgis_schema,
                                    )
                                except (
                                    ApiRuntimePreflightError,
                                    IdentityRuntimePreflightError,
                                ) as exc:
                                    raise RuntimeDatabaseConfigurationError(
                                        str(exc)
                                    ) from exc
            except Exception:
                for pool in reversed(tuple(self._pools.values())):
                    try:
                        pool.close(timeout=5.0)
                    except Exception:
                        pass
                self._closed = True
                raise
            self._opened = True
            return True

    def close_all(self) -> None:
        with self._lock:
            if not self._opened:
                return
            first_error: Exception | None = None
            for pool in reversed(tuple(self._pools.values())):
                try:
                    pool.close(timeout=5.0)
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
            self._opened = False
            self._closed = True
            if first_error is not None:
                raise first_error

    def require_pool(
        self,
        name: RuntimePoolName,
    ) -> tuple[_RuntimePool, RuntimePoolSpec]:
        if not self._opened:
            raise RuntimeDatabaseConfigurationError(
                "PostgreSQL runtime pools are not open"
            )
        try:
            return self._pools[name], self._specs[name]
        except KeyError as exc:
            raise RuntimeDatabaseConfigurationError(
                f"PostgreSQL runtime pool {name.value!r} is not configured"
            ) from exc


def build_runtime_pool_registry(
    *,
    environ: Mapping[str, str] | None = None,
    pool_factory: PoolFactory = _create_psycopg_pool,
) -> RuntimePoolRegistry:
    env = os.environ if environ is None else environ
    base_dsn = _base_runtime_dsn(env)
    specs: dict[RuntimePoolName, RuntimePoolSpec] = {}
    selected_names = _selected_pool_names(env)
    hardened = _is_hardened(env)
    for name in selected_names:
        dedicated_dsn = str(env.get(_POOL_DSN_ENV[name]) or "").strip()
        if hardened and not dedicated_dsn:
            raise RuntimeDatabaseConfigurationError(
                f"{_POOL_DSN_ENV[name]} is required for the hardened "
                f"{name.value} runtime pool; REALYTICS_DATABASE_URL is not "
                "a fallback for role-scoped runtime traffic"
            )
        if (
            hardened
            and name in {RuntimePoolName.API, RuntimePoolName.IDENTITY}
        ):
            role_env = (
                API_DATABASE_ROLE_ENV
                if name is RuntimePoolName.API
                else IDENTITY_DATABASE_ROLE_ENV
            )
            if not str(env.get(role_env) or "").strip():
                raise RuntimeDatabaseConfigurationError(
                    f"{role_env} is required for the hardened {name.value} "
                    "runtime pool so the login role can be verified"
                )
        dsn = _dsn_for_pool(
            name,
            environ=env,
            base_dsn=None if hardened else base_dsn,
        )
        if dsn:
            specs[name] = _build_pool_spec(
                name,
                dsn=dsn,
                environ=env,
            )

    replica_count = _positive_int(
        env,
        "REALYTICS_DB_REPLICA_COUNT",
        DEFAULT_REPLICA_COUNT,
    )
    connection_budget = _positive_int(
        env,
        "REALYTICS_DB_CONNECTION_BUDGET",
        DEFAULT_CONNECTION_BUDGET,
    )
    return RuntimePoolRegistry(
        specs,
        replica_count=replica_count,
        connection_budget=connection_budget,
        pool_factory=pool_factory,
    )


_registry_lock = threading.RLock()
_runtime_pool_registry: RuntimePoolRegistry | None = None


def get_runtime_pool_registry() -> RuntimePoolRegistry:
    global _runtime_pool_registry
    with _registry_lock:
        if _runtime_pool_registry is None:
            _runtime_pool_registry = build_runtime_pool_registry()
        return _runtime_pool_registry


def open_runtime_pools() -> RuntimePoolRegistry:
    registry = get_runtime_pool_registry()
    registry.open_all()
    return registry


def close_runtime_pools() -> None:
    global _runtime_pool_registry
    with _registry_lock:
        registry = _runtime_pool_registry
        _runtime_pool_registry = None
    if registry is not None:
        registry.close_all()


def _set_transaction_locals(
    connection: Any,
    *,
    tenant_id: str,
    principal_issuer: str = "",
    principal_subject: str = "",
    timeouts: TransactionTimeouts,
) -> None:
    settings = (
        ("realytics.tenant_id", tenant_id),
        ("realytics.principal_issuer", principal_issuer),
        ("realytics.principal_subject", principal_subject),
        ("search_path", SAFE_RUNTIME_SEARCH_PATH),
        ("statement_timeout", f"{timeouts.statement_ms}ms"),
        ("lock_timeout", f"{timeouts.lock_ms}ms"),
        (
            "idle_in_transaction_session_timeout",
            f"{timeouts.idle_in_transaction_ms}ms",
        ),
    )
    parameters = tuple(
        value
        for setting in settings
        for value in setting
    )
    connection.execute(SET_LOCAL_CONFIG_SQL, parameters)


def _supports_psycopg_pipeline(connection: Any) -> bool:
    """Return whether this checkout can safely use psycopg pipeline mode.

    Runtime database dependencies are intentionally lazy imports so local MVP
    environments can still start without PostgreSQL extras.  Checking both the
    concrete connection type and the linked libpq capability avoids treating a
    similarly named method on a fake or third-party DB-API connection as the
    psycopg wire-protocol pipeline.
    """

    try:
        import psycopg
    except ImportError:  # pragma: no cover - optional development dependency
        return False

    connection_type = getattr(psycopg, "Connection", None)
    capabilities = getattr(psycopg, "capabilities", None)
    has_pipeline = getattr(capabilities, "has_pipeline", None)
    pipeline = getattr(connection, "pipeline", None)
    return bool(
        connection_type is not None
        and isinstance(connection, connection_type)
        and callable(pipeline)
        and callable(has_pipeline)
        and has_pipeline()
    )


@contextmanager
def _pipeline_if_supported(connection: Any) -> Iterator[bool]:
    """Batch one explicit transaction without changing its security scope.

    ``Connection.transaction()`` remains the owner of BEGIN/COMMIT/ROLLBACK.
    Pipeline mode only changes when those ordered protocol messages are
    flushed.  In particular, the tenant/search-path/timeouts are still
    transaction-local and are sent before any repository statement.  This is
    safe for transaction pooling because no session-level state is introduced;
    prepared statements remain disabled separately for port 6543.
    """

    if not _supports_psycopg_pipeline(connection):
        yield False
        return

    with connection.pipeline():
        yield True


@contextmanager
def _runtime_transaction(
    *,
    pool_name: RuntimePoolName,
    tenant_id: str,
    principal_issuer: str = "",
    principal_subject: str = "",
    registry: RuntimePoolRegistry | None,
) -> Iterator[Any]:
    selected_registry = registry or get_runtime_pool_registry()
    pool, spec = selected_registry.require_pool(pool_name)
    with pool.connection(
        timeout=spec.acquire_timeout_seconds
    ) as connection:
        # Enclose the entire unit of work so BEGIN, local settings, repository
        # statements, and COMMIT/ROLLBACK can share psycopg's pipeline.  Fetch
        # calls may establish an earlier flush, but ordering and atomicity are
        # unchanged and the pool sees an idle connection only after the
        # pipeline has synchronized on exit.
        with _pipeline_if_supported(connection):
            with connection.transaction():
                _set_transaction_locals(
                    connection,
                    tenant_id=tenant_id,
                    principal_issuer=principal_issuer,
                    principal_subject=principal_subject,
                    timeouts=spec.transaction_timeouts,
                )
                yield connection


def _required_tenant_id(tenant_id: str) -> str:
    normalized = str(tenant_id or "").strip()
    if not normalized:
        raise ValueError(
            "A non-blank tenant_id is required for a tenant-scoped "
            "PostgreSQL transaction"
        )
    return normalized


@contextmanager
def api_tenant_transaction(
    tenant_id: str,
    *,
    registry: RuntimePoolRegistry | None = None,
) -> Iterator[Any]:
    with _runtime_transaction(
        pool_name=RuntimePoolName.API,
        tenant_id=_required_tenant_id(tenant_id),
        registry=registry,
    ) as connection:
        yield connection


def _required_principal_key(
    *,
    issuer: str,
    subject: str,
) -> tuple[str, str]:
    normalized_issuer = str(issuer or "").strip().rstrip("/")
    normalized_subject = str(subject or "").strip()
    if not normalized_issuer or not normalized_subject:
        raise ValueError(
            "Non-blank issuer and subject are required for a "
            "principal-scoped PostgreSQL transaction"
        )
    return normalized_issuer, normalized_subject


@contextmanager
def api_principal_transaction(
    issuer: str,
    subject: str,
    *,
    registry: RuntimePoolRegistry | None = None,
) -> Iterator[Any]:
    """Open API work before tenant membership exists.

    Only the verified bearer ``iss + sub`` pair may be supplied by the server.
    New onboarding tables enforce this transaction-local principal scope with
    FORCE RLS.  The tenant setting is explicitly blank, so this context cannot
    read tenant-scoped business relations through their RLS policies.
    """

    normalized_issuer, normalized_subject = _required_principal_key(
        issuer=issuer,
        subject=subject,
    )
    with _runtime_transaction(
        pool_name=RuntimePoolName.API,
        tenant_id="",
        principal_issuer=normalized_issuer,
        principal_subject=normalized_subject,
        registry=registry,
    ) as connection:
        yield connection


@contextmanager
def pf_worker_tenant_transaction(
    tenant_id: str,
    *,
    registry: RuntimePoolRegistry | None = None,
) -> Iterator[Any]:
    with _runtime_transaction(
        pool_name=RuntimePoolName.PF_WORKER,
        tenant_id=_required_tenant_id(tenant_id),
        registry=registry,
    ) as connection:
        yield connection


@contextmanager
def identity_transaction(
    *,
    registry: RuntimePoolRegistry | None = None,
) -> Iterator[Any]:
    """Use the identity pool without carrying a tenant from a prior checkout."""

    with _runtime_transaction(
        pool_name=RuntimePoolName.IDENTITY,
        tenant_id="",
        registry=registry,
    ) as connection:
        yield connection


@contextmanager
def identity_principal_transaction(
    issuer: str,
    subject: str,
    *,
    registry: RuntimePoolRegistry | None = None,
) -> Iterator[Any]:
    """Open the identity pool for a verified principal before membership.

    This is the deployable self-service onboarding path: production currently
    provides a dedicated identity DSN, while the API role can remain NOLOGIN
    until its secret-manager activation is complete.  FORCE RLS on the
    onboarding relations binds every row to this exact signed-token ``iss`` +
    ``sub`` pair and the tenant setting remains blank.
    """

    normalized_issuer, normalized_subject = _required_principal_key(
        issuer=issuer,
        subject=subject,
    )
    with _runtime_transaction(
        pool_name=RuntimePoolName.IDENTITY,
        tenant_id="",
        principal_issuer=normalized_issuer,
        principal_subject=normalized_subject,
        registry=registry,
    ) as connection:
        yield connection


__all__ = [
    "DEFAULT_CONNECTION_BUDGET",
    "DEFAULT_REPLICA_COUNT",
    "RuntimeDatabaseConfigurationError",
    "RuntimePoolName",
    "RuntimePoolRegistry",
    "RuntimePoolSpec",
    "RUNTIME_POOL_ROLES_ENV",
    "SAFE_RUNTIME_SEARCH_PATH",
    "SET_LOCAL_CONFIG_SQL",
    "TRANSACTION_POOLER_OPT_IN_ENV",
    "TransactionTimeouts",
    "api_tenant_transaction",
    "api_principal_transaction",
    "build_runtime_pool_registry",
    "close_runtime_pools",
    "get_runtime_pool_registry",
    "identity_transaction",
    "identity_principal_transaction",
    "open_runtime_pools",
    "pf_worker_tenant_transaction",
    "validate_connection_budget",
    "validate_runtime_dsn",
]
