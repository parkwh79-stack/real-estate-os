from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

import main
from db.repositories.activity_log_repository import (
    PostgresActivityLogRepository,
)


class _Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


class _Connection:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Result:
        self.calls.append((sql, params))
        return _Result(self.row)


def test_activity_append_uses_resolved_identity_and_one_append_only_insert() -> None:
    connection = _Connection(("evt-1",))

    @contextmanager
    def transaction(tenant_id: str):
        assert tenant_id == "tenant-1"
        yield connection

    repository = PostgresActivityLogRepository(
        tenant_id="tenant-1",
        user_id="issuer::subject",
        transaction_factory=transaction,
    )
    event_id = repository.append_api_request(
        {
            "eventId": "evt-1",
            "tenantId": "forged-tenant",
            "userId": "raw-oauth-sub",
            "projectId": "project-1",
            "method": "GET",
            "path": "/projects/project-1/chief-orchestration",
            "statusCode": 200,
            "durationMs": 12.5,
            "metadata": {"queryKeys": ["tab"]},
        }
    )

    assert event_id == "evt-1"
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "INSERT INTO realytics.user_activity_logs" in sql
    assert "statement_timestamp()" in sql
    assert "ON CONFLICT (event_id) DO NOTHING" in sql
    assert params[0] == "evt-1"
    assert params[1] == "issuer::subject"
    assert params[-1] == "tenant-1"
    assert "forged-tenant" not in params
    assert "raw-oauth-sub" not in params


def test_activity_middleware_appends_with_membership_resolved_user(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    class _ProjectRepository:
        tenant_id = "tenant-1"
        @staticmethod
        def get(project_id: str) -> dict[str, Any]:
            return {
                "id": project_id,
                "project": {"id": project_id, "name": "검토 사업"},
                "_meta": {"tenantId": "tenant-1", "stateVersion": 1},
            }

    class _ActivityRepository:
        @staticmethod
        def append_api_request(event: dict[str, Any]) -> str:
            captured["event"] = dict(event)
            return str(event["eventId"])

    def activity_repository(*, tenant_id: str, user_id: str):
        captured["tenant_id"] = tenant_id
        captured["user_id"] = user_id
        return _ActivityRepository()

    monkeypatch.setattr(main, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(
        main.projects,
        "project_repository",
        lambda _request: _ProjectRepository(),
    )
    monkeypatch.setattr(main, "enforce_project_tenant_scope", lambda *_args: None)
    monkeypatch.setattr(
        main,
        "identity_context_from_request",
        lambda _request: {
            "user": {
                "tenantId": "tenant-1",
                "userId": "https://issuer.example::oauth-subject",
            }
        },
    )
    monkeypatch.setattr(main, "normalized_activity_repository", activity_repository)
    legacy_calls = []
    def forbidden_legacy_activity(event):
        legacy_calls.append(event)
        raise AssertionError("PostgreSQL activity must use the normalized repository")
    monkeypatch.setattr(main, "record_user_activity_log", forbidden_legacy_activity)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/projects/project-1/chief-orchestration",
        "raw_path": b"/projects/project-1/chief-orchestration",
        "query_string": b"tab=approval",
        "headers": [],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    request = Request(scope)

    async def call_next(_request: Request):
        return JSONResponse({"ok": True}, status_code=200)

    response = asyncio.run(main.user_activity_middleware(request, call_next))

    assert response.status_code == 200
    assert captured["tenant_id"] == "tenant-1"
    assert captured["user_id"] == "https://issuer.example::oauth-subject"
    assert captured["event"]["projectId"] == "project-1"
    assert captured["event"]["statusCode"] == 200
    assert captured["event"]["metadata"] == {"queryKeys": ["tab"]}
    assert legacy_calls == []


def test_reserved_project_routes_are_not_activity_project_ids() -> None:
    assert main._activity_project_id("/projects/recent-work") is None
    assert main._activity_project_id("/projects/intake/continuation-check") is None
    assert main._activity_project_id("/projects/monitoring/outbox") is None
    assert main._activity_project_id("/projects/project-1/report") == "project-1"
