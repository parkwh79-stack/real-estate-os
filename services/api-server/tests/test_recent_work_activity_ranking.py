from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from db.repositories.postgres_project_repository import (
    PostgresProjectRepository,
)
from db.repositories.recent_activity_repository import (
    RecentActivityRepositoryError,
)
from main import app
from routers import projects


class _PostgresProjectListRepository(PostgresProjectRepository):
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def list(self) -> list[dict[str, Any]]:
        return list(self.records)


class _NoRuns:
    def __init__(self) -> None:
        self.batch_project_ids: list[str] = []

    def latest_metadata_by_project(
        self,
        project_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        self.batch_project_ids = list(project_ids)
        return {}

    def list_metadata(self, _project_id: str) -> list[dict[str, Any]]:
        raise AssertionError("recent work must use the batch run projection")


class _ActivityProjection:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.project_ids: list[str] = []

    def latest_successful_project_activity(
        self,
        project_ids: list[str],
    ) -> dict[str, str]:
        self.project_ids = list(project_ids)
        return dict(self.values)


def _session_headers(client: TestClient) -> dict[str, str]:
    tenant_id = "recent-activity-ranking-tenant"
    user_id = "recent-activity-ranking-user"
    assert client.post(
        "/auth/tenants",
        json={"tenantId": tenant_id, "name": "Recent Activity Ranking"},
    ).status_code == 200
    assert client.post(
        "/auth/users",
        json={
            "userId": user_id,
            "tenantId": tenant_id,
            "displayName": "Recent Activity User",
            "role": "reviewer",
        },
    ).status_code == 200
    response = client.post(
        "/auth/session",
        json={
            "userId": user_id,
            "tenantId": tenant_id,
            "provider": "local-test",
        },
    )
    assert response.status_code == 200
    return {
        "x-realytics-session-id": response.json()["session"]["sessionId"]
    }


def _record(project_id: str, updated_at: str) -> dict[str, Any]:
    return {
        "id": project_id,
        "project": {
            "id": project_id,
            "name": f"Project {project_id}",
            "status": "draft",
        },
        "_meta": {
            "tenantId": "recent-activity-ranking-tenant",
            "createdBy": "recent-activity-ranking-user",
            "updated_at": updated_at,
        },
    }


def test_postgres_recent_work_prefers_user_activity_and_only_falls_back_when_absent(
    monkeypatch,
) -> None:
    client = TestClient(app)
    headers = _session_headers(client)
    records = [
        _record("recent-route", "2026-07-10T00:00:00+00:00"),
        _record("no-activity", "2026-07-20T00:00:00+00:00"),
        _record("old-route", "2026-07-30T00:00:00+00:00"),
    ]
    activity = _ActivityProjection(
        {
            "recent-route": "2026-08-01T00:00:00+00:00",
            "old-route": "2026-07-01T00:00:00+00:00",
        }
    )
    captured_scope: dict[str, str] = {}

    def activity_repository(*, tenant_id: str, user_id: str):
        captured_scope.update(tenant_id=tenant_id, user_id=user_id)
        return activity

    monkeypatch.setattr(
        projects,
        "project_repository",
        lambda _request: _PostgresProjectListRepository(records),
    )
    runs = _NoRuns()
    monkeypatch.setattr(
        projects,
        "orchestration_repository",
        lambda _request: runs,
    )
    monkeypatch.setattr(
        projects,
        "recent_project_activity_repository",
        activity_repository,
    )

    response = client.get("/projects/recent-work?limit=20", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "realytics-recent-work-v2"
    assert [item["projectId"] for item in body["items"]] == [
        "recent-route",
        "no-activity",
        "old-route",
    ]
    assert captured_scope == {
        "tenant_id": "recent-activity-ranking-tenant",
        "user_id": "recent-activity-ranking-user",
    }
    assert activity.project_ids == [
        "recent-route",
        "no-activity",
        "old-route",
    ]
    assert runs.batch_project_ids == activity.project_ids

    by_project = {item["projectId"]: item for item in body["items"]}
    assert by_project["recent-route"]["lastActivityAt"] == (
        "2026-08-01T00:00:00+00:00"
    )
    assert by_project["recent-route"]["updatedAt"] == (
        "2026-08-01T00:00:00+00:00"
    )
    assert by_project["recent-route"]["recordUpdatedAt"] == (
        "2026-07-10T00:00:00+00:00"
    )
    assert by_project["no-activity"]["lastActivityAt"] is None
    assert by_project["no-activity"]["updatedAt"] == (
        "2026-07-20T00:00:00+00:00"
    )
    # Activity exists, so the newer project-row timestamp is not substituted.
    assert by_project["old-route"]["updatedAt"] == (
        "2026-07-01T00:00:00+00:00"
    )


def test_postgres_recent_work_does_not_fallback_when_activity_authority_fails(
    monkeypatch,
) -> None:
    client = TestClient(app)
    headers = _session_headers(client)

    class _UnavailableActivity:
        def latest_successful_project_activity(self, _project_ids):
            raise RecentActivityRepositoryError("unavailable")

    monkeypatch.setattr(
        projects,
        "project_repository",
        lambda _request: _PostgresProjectListRepository(
            [_record("project-1", "2026-07-20T00:00:00+00:00")]
        ),
    )
    monkeypatch.setattr(
        projects,
        "recent_project_activity_repository",
        lambda **_scope: _UnavailableActivity(),
    )

    response = client.get("/projects/recent-work", headers=headers)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "error": "recent_activity_authority_unavailable"
    }
