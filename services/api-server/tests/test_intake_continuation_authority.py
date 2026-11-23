from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from fastapi.testclient import TestClient

from db.repositories.postgres_project_repository import (
    PostgresProjectRepository as ActualPostgresProjectRepository,
)
from main import app
from routers import projects


class _RowsResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return deepcopy(self._rows)


class _TenantProjectConnection:
    def __init__(self, payload_by_tenant: dict[str, dict[str, Any]]) -> None:
        self._payload_by_tenant = payload_by_tenant
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    def execute(
        self,
        sql: str,
        params: tuple[Any, ...],
    ) -> _RowsResult:
        compact = " ".join(sql.split())
        self.queries.append((compact, params))
        assert "FROM realytics.projects" in compact
        assert "tenant_id = %s" in compact
        assert len(params) == 1
        tenant_id = str(params[0])
        payload = self._payload_by_tenant.get(tenant_id)
        rows = []
        if payload is not None:
            rows.append(
                (
                    "shared-project-id",
                    tenant_id,
                    1,
                    deepcopy(payload),
                )
            )
        return _RowsResult(rows)


def _session_headers(
    client: TestClient,
    *,
    tenant_id: str,
    user_id: str,
) -> dict[str, str]:
    assert client.post(
        "/auth/tenants",
        json={"tenantId": tenant_id, "name": tenant_id},
    ).status_code == 200
    assert client.post(
        "/auth/users",
        json={
            "userId": user_id,
            "tenantId": tenant_id,
            "displayName": user_id,
            "role": "reviewer",
        },
    ).status_code == 200
    session = client.post(
        "/auth/session",
        json={
            "userId": user_id,
            "tenantId": tenant_id,
            "provider": "local-test",
        },
    )
    assert session.status_code == 200
    return {"x-realytics-session-id": session.json()["session"]["sessionId"]}


def test_continuation_check_uses_authorized_postgres_projection_for_colliding_id(
    monkeypatch,
) -> None:
    """A legacy or other-tenant row with the same ID cannot supply fingerprints."""

    client = TestClient(app)
    tenant_a = "continuation-authority-tenant-a"
    tenant_b = "continuation-authority-tenant-b"
    user_a = "continuation-authority-user-a"
    user_b = "continuation-authority-user-b"
    headers_a = _session_headers(
        client,
        tenant_id=tenant_a,
        user_id=user_a,
    )
    headers_b = _session_headers(
        client,
        tenant_id=tenant_b,
        user_id=user_b,
    )

    payload_by_tenant = {
        tenant_a: {
            "project": {"id": "forged-a", "name": "Tenant A project"},
            "_meta": {"createdBy": user_a},
            "source_documents": [
                {
                    "originalFileName": "tenant-a.pdf",
                    "sha256": "tenant-a-hash",
                }
            ],
        },
        tenant_b: {
            "project": {"id": "forged-b", "name": "Tenant B project"},
            "_meta": {"createdBy": user_b},
            "source_documents": [
                {
                    "originalFileName": "tenant-b.pdf",
                    "sha256": "tenant-b-hash",
                }
            ],
        },
    }
    connection = _TenantProjectConnection(payload_by_tenant)
    transaction_tenants: list[str] = []

    @contextmanager
    def tenant_transaction(tenant_id: str):
        transaction_tenants.append(tenant_id)
        yield connection

    def postgres_repository_factory(*, tenant_id: str):
        return ActualPostgresProjectRepository(
            tenant_id=tenant_id,
            transaction_factory=tenant_transaction,
        )

    monkeypatch.setattr(projects, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(
        projects,
        "PostgresProjectRepository",
        postgres_repository_factory,
    )
    monkeypatch.setitem(
        projects.PROJECTS_DB,
        "shared-project-id",
        {
            "project": {
                "id": "shared-project-id",
                "name": "Legacy cross-tenant collision",
            },
            "_meta": {"tenantId": "legacy-other-tenant"},
            "source_documents": [
                {
                    "originalFileName": "legacy-other-tenant.pdf",
                    "sha256": "legacy-other-tenant-hash",
                }
            ],
        },
    )

    files = [
        {"name": "tenant-a.pdf", "sha256": "tenant-a-hash"},
        {"name": "tenant-b.pdf", "sha256": "tenant-b-hash"},
        {
            "name": "legacy-other-tenant.pdf",
            "sha256": "legacy-other-tenant-hash",
        },
    ]
    response_a = client.post(
        "/projects/intake/continuation-check",
        headers=headers_a,
        json={"files": files},
    )
    response_b = client.post(
        "/projects/intake/continuation-check",
        headers=headers_b,
        json={"files": files},
    )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert response_a.json()["matches"] == [
        {
            "projectId": "shared-project-id",
            "projectName": "Tenant A project",
            "matchedFiles": [
                {
                    "fileName": "tenant-a.pdf",
                    "matchedDocument": {
                        "originalFileName": "tenant-a.pdf",
                        "sha256": "tenant-a-hash",
                    },
                }
            ],
            "nextAction": "resume_existing_review",
        }
    ]
    assert response_b.json()["matches"] == [
        {
            "projectId": "shared-project-id",
            "projectName": "Tenant B project",
            "matchedFiles": [
                {
                    "fileName": "tenant-b.pdf",
                    "matchedDocument": {
                        "originalFileName": "tenant-b.pdf",
                        "sha256": "tenant-b-hash",
                    },
                }
            ],
            "nextAction": "resume_existing_review",
        }
    ]
    assert transaction_tenants == [tenant_a, tenant_b]
    assert [params for _sql, params in connection.queries] == [
        (tenant_a,),
        (tenant_b,),
    ]
