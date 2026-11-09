from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from db.repositories.first_tenant_admin_bootstrap_repository import (
    BOOTSTRAP_EVENT_TYPE,
    FirstTenantAdminBootstrapConflict,
    FirstTenantAdminBootstrapError,
    PostgresFirstTenantAdminBootstrapRepository,
    build_first_tenant_admin_plan,
)
from scripts import bootstrap_first_tenant_admin as command


NOW = datetime(2026, 7, 27, 6, 30, tzinfo=timezone.utc)
SUBJECT = "11111111-2222-4333-8444-555555555555"
ISSUER = "https://example-project.supabase.co/auth/v1"


def plan(**overrides):
    values = {
        "tenant_id": "realytics-pilot",
        "tenant_name": "REALYTICS Pilot Workspace",
        "issuer": ISSUER,
        "subject": SUBJECT,
        "provider": "google",
        "operator_issuer": "urn:realytics:platform-operator",
        "operator_subject": "change:pilot-bootstrap-2026-07-27",
        "display_name": "Pilot Administrator",
    }
    values.update(overrides)
    return build_first_tenant_admin_plan(**values)


class FakeCursor:
    def __init__(
        self,
        *,
        schema: str = "realytics",
        auth_user: tuple[Any, ...] | None = (
            SUBJECT,
            "pilot@example.com",
            True,
        ),
        tenant: tuple[Any, ...] | None = None,
        principal: tuple[Any, ...] | None = None,
        membership: tuple[Any, ...] | None = None,
        membership_count: int = 0,
        event: tuple[Any, ...] | None = None,
        fail_on: str | None = None,
    ) -> None:
        self.schema = schema
        self.auth_user = auth_user
        self.tenant = tenant
        self.principal = principal
        self.membership = membership
        self.membership_count = membership_count
        self.event = event
        self.fail_on = fail_on
        self.statement = ""
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def execute(
        self,
        statement: str,
        params: tuple[Any, ...] | None = None,
    ) -> None:
        self.statement = statement
        self.calls.append((statement, params))
        if self.fail_on and self.fail_on in statement:
            raise RuntimeError("injected database failure")

    def fetchone(self):
        if "SELECT current_schema()" in self.statement:
            return (self.schema,)
        if "FROM auth.users AS auth_user" in self.statement:
            return self.auth_user
        if '.tenants' in self.statement and "SELECT name, status" in self.statement:
            return self.tenant
        if '.principals' in self.statement and "SELECT principal_type" in self.statement:
            return self.principal
        if '.tenant_memberships' in self.statement and "COUNT(*)" in self.statement:
            return (self.membership_count,)
        if '.tenant_memberships' in self.statement and "SELECT role" in self.statement:
            return self.membership
        if (
            '.tenant_identity_access_events' in self.statement
            and "SELECT" in self.statement
        ):
            return self.event
        return None


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.value = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self):
        return self.value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closes += 1


def repository(connection: FakeConnection):
    return PostgresFirstTenantAdminBootstrapRepository(
        connection_factory=lambda: connection,
        schema_name="realytics",
        now=lambda: NOW,
    )


def current_event(bootstrap_plan):
    return (
        BOOTSTRAP_EVENT_TYPE,
        None,
        bootstrap_plan.operator_issuer,
        bootstrap_plan.operator_subject,
        bootstrap_plan.issuer,
        bootstrap_plan.subject,
        bootstrap_plan.event_payload,
    )


def test_apply_bootstraps_tenant_principal_membership_and_event_atomically():
    bootstrap_plan = plan()
    cursor = FakeCursor()
    connection = FakeConnection(cursor)

    result = repository(connection).bootstrap(bootstrap_plan)

    assert result.action == "bootstrapped"
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closes == 1
    inserts = [call for call in cursor.calls if "INSERT INTO" in call[0]]
    assert len(inserts) == 4
    assert any('.tenants' in sql for sql, _params in inserts)
    assert any('.principals' in sql for sql, _params in inserts)
    assert any('.tenant_memberships' in sql for sql, _params in inserts)
    assert any('.tenant_identity_access_events' in sql for sql, _params in inserts)
    assert any("pg_advisory_xact_lock" in sql for sql, _params in cursor.calls)
    assert any(
        "LOCK TABLE" in sql and "SHARE ROW EXCLUSIVE" in sql
        for sql, _params in cursor.calls
    )
    assert any(
        params == ("realytics.tenant_id", bootstrap_plan.tenant_id)
        for _sql, params in cursor.calls
    )

    auth_lookup = next(
        call for call in cursor.calls if "FROM auth.users AS auth_user" in call[0]
    )
    assert auth_lookup[1] == ("google", SUBJECT)
    assert "email =" not in auth_lookup[0].casefold()
    membership_insert = next(
        call for call in inserts if '.tenant_memberships' in call[0]
    )
    assert membership_insert[1][:3] == (
        "realytics-pilot",
        ISSUER,
        SUBJECT,
    )
    event_insert = next(
        call for call in inserts if '.tenant_identity_access_events' in call[0]
    )
    assert "pilot@example.com" not in repr(event_insert[1])


def test_default_inspection_is_read_only_and_reports_would_bootstrap():
    connection = FakeConnection(FakeCursor())

    result = repository(connection).inspect(plan())

    assert result.action == "would_bootstrap"
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closes == 1
    assert not any(
        "INSERT INTO" in statement
        for statement, _params in connection.value.calls
    )


def test_exact_existing_bootstrap_is_idempotently_current():
    bootstrap_plan = plan()
    cursor = FakeCursor(
        tenant=(bootstrap_plan.tenant_name, "active"),
        principal=(
            "human",
            "active",
            "platform_operator_approved_supabase_google",
        ),
        membership=(
            "admin",
            "active",
            1,
            bootstrap_plan.membership_source,
        ),
        membership_count=3,
        event=current_event(bootstrap_plan),
    )
    connection = FakeConnection(cursor)

    result = repository(connection).bootstrap(bootstrap_plan)

    assert result.action == "current"
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert not any("INSERT INTO" in sql for sql, _params in cursor.calls)


@pytest.mark.parametrize(
    "cursor,match",
    [
        (
            FakeCursor(tenant=("Another Workspace", "active")),
            "another name or status",
        ),
        (
            FakeCursor(
                tenant=("REALYTICS Pilot Workspace", "active"),
                membership_count=1,
            ),
            "first-admin bootstrap is closed",
        ),
        (
            FakeCursor(
                principal=("human", "revoked", "provider-verified"),
            ),
            "non-active human principal",
        ),
    ],
)
def test_conflicting_authority_fails_closed_without_writes(cursor, match):
    connection = FakeConnection(cursor)

    with pytest.raises(FirstTenantAdminBootstrapConflict, match=match):
        repository(connection).bootstrap(plan())

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert not any("INSERT INTO" in sql for sql, _params in cursor.calls)


def test_partial_membership_without_audit_event_fails_closed():
    bootstrap_plan = plan()
    cursor = FakeCursor(
        tenant=(bootstrap_plan.tenant_name, "active"),
        principal=("human", "active", "provider-verified"),
        membership=(
            "admin",
            "active",
            1,
            bootstrap_plan.membership_source,
        ),
        membership_count=1,
    )
    connection = FakeConnection(cursor)

    with pytest.raises(
        FirstTenantAdminBootstrapConflict,
        match="partial membership/audit bootstrap state",
    ):
        repository(connection).bootstrap(bootstrap_plan)

    assert connection.rollbacks == 1


def test_subject_must_exist_with_the_requested_supabase_provider():
    missing_provider = FakeConnection(
        FakeCursor(auth_user=(SUBJECT, "pilot@example.com", False))
    )

    with pytest.raises(
        FirstTenantAdminBootstrapError,
        match="not linked to the requested provider",
    ):
        repository(missing_provider).bootstrap(plan())

    assert missing_provider.rollbacks == 1
    assert not any(
        "INSERT INTO" in sql for sql, _params in missing_provider.value.calls
    )


def test_database_failure_rolls_back_the_whole_bootstrap():
    connection = FakeConnection(
        FakeCursor(fail_on='INSERT INTO "realytics".tenant_memberships')
    )

    with pytest.raises(RuntimeError, match="injected database failure"):
        repository(connection).bootstrap(plan())

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closes == 1


def test_plan_canonicalizes_only_identity_safe_values():
    value = plan(issuer=ISSUER + "/", provider="GOOGLE")

    assert value.issuer == ISSUER
    assert value.subject == SUBJECT
    assert value.provider == "google"
    assert "pilot@example.com" not in value.event_payload
    assert value.event_id == plan().event_id

    with pytest.raises(ValueError, match="exact Supabase Auth user UUID"):
        plan(subject="pilot@example.com")
    with pytest.raises(ValueError, match="exact HTTPS Supabase Auth issuer"):
        plan(issuer="http://example.test/auth/v1")


def command_args(*extra: str) -> list[str]:
    return [
        "--tenant-id",
        "realytics-pilot",
        "--tenant-name",
        "REALYTICS Pilot Workspace",
        "--issuer",
        ISSUER,
        "--subject",
        SUBJECT,
        "--operator-subject",
        "change:pilot-bootstrap-2026-07-27",
        *extra,
    ]


def test_cli_never_falls_back_to_runtime_or_identity_dsn(monkeypatch):
    monkeypatch.delenv(command.MIGRATION_DATABASE_URL_ENV, raising=False)
    monkeypatch.setenv("REALYTICS_DATABASE_URL", "postgresql://runtime-secret")
    monkeypatch.setenv(
        "REALYTICS_IDENTITY_DATABASE_URL",
        "postgresql://identity-secret",
    )

    with pytest.raises(RuntimeError, match="deployment-role DSN"):
        command.main(command_args())


@pytest.mark.parametrize(
    "extra,expected_method,expected_action",
    [
        ((), "inspect", "would_bootstrap"),
        (("--apply",), "bootstrap", "bootstrapped"),
    ],
)
def test_cli_requires_explicit_apply_for_mutation(
    monkeypatch,
    capsys,
    extra,
    expected_method,
    expected_action,
):
    marker = object()
    calls: list[tuple[str, Any]] = []

    class Repository:
        def __init__(self, **kwargs):
            assert set(kwargs) == {"connection_factory", "schema_name"}

        def inspect(self, value):
            calls.append(("inspect", value))
            return SimpleNamespace(
                as_dict=lambda: {"action": "would_bootstrap"}
            )

        def bootstrap(self, value):
            calls.append(("bootstrap", value))
            return SimpleNamespace(as_dict=lambda: {"action": "bootstrapped"})

    monkeypatch.setenv(
        command.MIGRATION_DATABASE_URL_ENV,
        "postgresql://migration-secret",
    )
    monkeypatch.setattr(
        command,
        "build_first_tenant_admin_plan",
        lambda **_kwargs: marker,
    )
    monkeypatch.setattr(
        command,
        "PostgresFirstTenantAdminBootstrapRepository",
        Repository,
    )

    assert command.main(command_args(*extra)) == 0
    assert calls == [(expected_method, marker)]
    output = capsys.readouterr().out
    assert json.loads(output)["action"] == expected_action
    assert "migration-secret" not in output
