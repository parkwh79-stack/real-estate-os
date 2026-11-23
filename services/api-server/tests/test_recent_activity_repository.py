from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import pytest

from db.repositories.recent_activity_repository import (
    PostgresRecentActivityRepository,
    RecentActivityRepositoryError,
)


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def fetchall(self) -> list[Any]:
        return list(self.rows)


class _Connection:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Result:
        self.statements.append((" ".join(sql.split()), params))
        return _Result(self.rows)


class _Transactions:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.tenant_ids: list[str] = []

    @contextmanager
    def __call__(self, tenant_id: str):
        self.tenant_ids.append(tenant_id)
        yield self.connection


def test_latest_activity_repeats_tenant_user_and_success_scope() -> None:
    connection = _Connection(
        [
            (
                "tenant-a",
                "user-a",
                "project-2",
                datetime(2026, 7, 28, 3, 4, tzinfo=timezone.utc),
            ),
            {
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "project_id": "project-1",
                "last_activity_at": "2026-07-27T01:02:03+00:00",
            },
        ]
    )
    transactions = _Transactions(connection)
    repository = PostgresRecentActivityRepository(
        tenant_id="tenant-a",
        user_id="user-a",
        transaction_factory=transactions,
    )

    result = repository.latest_successful_project_activity(
        ["project-1", "project-2", "project-1", ""],
    )

    assert result == {
        "project-1": "2026-07-27T01:02:03+00:00",
        "project-2": "2026-07-28T03:04:00+00:00",
    }
    assert transactions.tenant_ids == ["tenant-a"]
    assert len(connection.statements) == 1
    sql, params = connection.statements[0]
    assert "FROM realytics.user_activity_logs" in sql
    assert "tenant_id = %s" in sql
    assert "user_id = %s" in sql
    assert "project_id = ANY(%s::text[])" in sql
    assert "event_type = 'api_request'" in sql
    assert "status_code >= 200" in sql
    assert "status_code < 400" in sql
    assert "path = '/projects/' || project_id" in sql
    assert "starts_with(path, '/projects/' || project_id || '/')" in sql
    assert params == (
        "tenant-a",
        "user-a",
        ["project-1", "project-2"],
    )


def test_empty_project_scope_does_not_checkout_a_connection() -> None:
    transactions = _Transactions(_Connection())
    repository = PostgresRecentActivityRepository(
        tenant_id="tenant-a",
        user_id="user-a",
        transaction_factory=transactions,
    )

    assert repository.latest_successful_project_activity(["", " "]) == {}
    assert transactions.tenant_ids == []


@pytest.mark.parametrize(
    "row",
    [
        ("tenant-b", "user-a", "project-1", "2026-07-28T00:00:00Z"),
        ("tenant-a", "user-b", "project-1", "2026-07-28T00:00:00Z"),
        ("tenant-a", "user-a", "project-2", "2026-07-28T00:00:00Z"),
        ("tenant-a", "user-a", "project-1", None),
    ],
)
def test_out_of_scope_or_incomplete_rows_fail_closed(row: Any) -> None:
    transactions = _Transactions(_Connection([row]))
    repository = PostgresRecentActivityRepository(
        tenant_id="tenant-a",
        user_id="user-a",
        transaction_factory=transactions,
    )

    with pytest.raises(RecentActivityRepositoryError):
        repository.latest_successful_project_activity(["project-1"])


def test_database_errors_are_exposed_as_typed_unavailability() -> None:
    class _BrokenConnection:
        def execute(self, _sql: str, _params: tuple[Any, ...]):
            raise PermissionError("SELECT denied")

    transactions = _Transactions(_BrokenConnection())  # type: ignore[arg-type]
    repository = PostgresRecentActivityRepository(
        tenant_id="tenant-a",
        user_id="user-a",
        transaction_factory=transactions,
    )

    with pytest.raises(
        RecentActivityRepositoryError,
        match="Normalized recent activity is unavailable",
    ):
        repository.latest_successful_project_activity(["project-1"])


@pytest.mark.parametrize("field", ["tenant_id", "user_id"])
def test_blank_identity_scope_is_rejected(field: str) -> None:
    kwargs = {"tenant_id": "tenant-a", "user_id": "user-a"}
    kwargs[field] = ""

    with pytest.raises(ValueError, match=field):
        PostgresRecentActivityRepository(**kwargs)
