from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from main import app
from routers import projects


class _ProjectListRepository:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def list(self) -> list[dict[str, Any]]:
        return self.records


class _RunListRepository:
    def __init__(self, records: dict[str, list[dict[str, Any]]]) -> None:
        self.records = records
        self.requested_project_ids: list[str] = []

    def list_metadata(self, project_id: str) -> list[dict[str, Any]]:
        self.requested_project_ids.append(project_id)
        return self.records.get(project_id, [])

    def latest_metadata_by_project(
        self,
        project_ids: list[str],
    ) -> dict[str, dict[str, Any]]:
        self.requested_project_ids.extend(project_ids)
        return {
            project_id: max(
                self.records[project_id],
                key=lambda item: str(
                    item.get("completed_at")
                    or item.get("started_at")
                    or ""
                ),
            )
            for project_id in project_ids
            if self.records.get(project_id)
        }


def _session_headers(client: TestClient) -> dict[str, str]:
    tenant_id = "recent-work-contract-tenant"
    user_id = "recent-work-contract-user"
    assert client.post(
        "/auth/tenants",
        json={"tenantId": tenant_id, "name": "Recent Work Contract"},
    ).status_code == 200
    assert client.post(
        "/auth/users",
        json={
            "userId": user_id,
            "tenantId": tenant_id,
            "displayName": "Recent Work Reviewer",
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


def test_recent_work_returns_resumable_typed_items_without_cross_tenant_rows(
    monkeypatch,
) -> None:
    client = TestClient(app)
    headers = _session_headers(client)
    tenant_id = "recent-work-contract-tenant"
    user_id = "recent-work-contract-user"

    records = [
        {
            "id": "review-project",
            "project": {
                "id": "review-project",
                "name": "승인 검토 사업",
                "status": "under_review",
            },
            "_meta": {
                "tenantId": tenant_id,
                "createdBy": user_id,
                "updated_at": "2026-07-27T08:00:00+00:00",
            },
            "source_documents": [
                {
                    "documentType": "business_plan",
                    "originalFileName": "사업계획서.pdf",
                    "sha256": "review-source-hash",
                }
            ],
            "pending_reviews": [{"id": "pending-1"}],
            "risk_register": {"risks": [{"riskId": "risk-1"}]},
            "chief_orchestration_state": {
                "approvalReadinessGate": {
                    "status": "not_ready_for_approval"
                }
            },
        },
        {
            "id": "analysis-project",
            "project": {
                "id": "analysis-project",
                "name": "현금흐름 분석 사업",
                "status": "active",
            },
            "_meta": {
                "tenantId": tenant_id,
                "createdBy": user_id,
                "updated_at": "2026-07-26T08:00:00+00:00",
            },
            "source_documents": [
                {
                    "documentType": "feasibility_workbook",
                    "originalFileName": "사업수지.xlsx",
                }
            ],
        },
        {
            "id": "project/with?reserved",
            "project": {
                "id": "project/with?reserved",
                "name": "신규 자료 검토",
                "status": "draft",
            },
            "_meta": {
                "tenantId": tenant_id,
                "createdBy": user_id,
                "updated_at": "2026-07-25T08:00:00+00:00",
            },
        },
        {
            "id": "other-tenant-project",
            "project": {
                "id": "other-tenant-project",
                "name": "다른 Tenant 비공개 사업",
                "status": "active",
            },
            "_meta": {
                "tenantId": "other-tenant",
                "createdBy": "other-user",
                "updated_at": "2026-07-28T08:00:00+00:00",
            },
        },
    ]
    runs = _RunListRepository(
        {
            "review-project": [
                {
                    "run_id": "review-run-1",
                    "project_id": "review-project",
                    "workflow": "pf_review",
                    "status": "completed_with_warnings",
                    "executionStatus": "completed_with_warnings",
                    "approvalStatus": "pending",
                    "started_at": "2026-07-28T07:00:00+00:00",
                    "completed_at": "2026-07-28T07:30:00+00:00",
                }
            ],
            "analysis-project": [
                {
                    "run_id": "analysis-run-1",
                    "project_id": "analysis-project",
                    "workflow": "pf_review",
                    "status": "completed",
                    "executionStatus": "completed",
                    "approvalStatus": "not_requested",
                    "started_at": "2026-07-27T07:00:00+00:00",
                    "completed_at": "2026-07-27T07:20:00+00:00",
                }
            ],
        }
    )

    monkeypatch.setattr(
        projects,
        "project_repository",
        lambda request: _ProjectListRepository(records),
    )
    monkeypatch.setattr(
        projects,
        "orchestration_repository",
        lambda request: runs,
    )
    monkeypatch.setattr(
        projects,
        "source_document_repository",
        lambda: (_ for _ in ()).throw(
            AssertionError("recent work must not use the global source store")
        ),
    )

    response = client.get(
        "/projects/recent-work?limit=20",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    body = response.json()
    assert body["schemaVersion"] == "realytics-recent-work-v2"
    assert body["count"] == 3
    assert [item["projectId"] for item in body["items"]] == [
        "review-project",
        "analysis-project",
        "project/with?reserved",
    ]
    assert "other-tenant-project" not in runs.requested_project_ids

    by_project = {item["projectId"]: item for item in body["items"]}
    review = by_project["review-project"]
    assert review["kind"] == "review"
    assert review["id"] == "review-run-1"
    assert review["title"] == "승인 검토 사업"
    assert review["status"] == "pending"
    assert review["executionStatus"] == "completed_with_warnings"
    assert review["approvalStatus"] == "pending"
    assert review["updatedAt"] == "2026-07-28T07:30:00+00:00"
    assert review["href"] == "/project/review-project/chief-orchestration"
    assert review["lastWorkedAt"] == review["updatedAt"]
    assert review["latestRunStatus"] == review["executionStatus"]
    assert review["sourceDocumentCount"] == 1
    assert review["openReviewCount"] == 1
    assert review["riskCount"] == 1
    assert "원본 자료 1건" in review["summary"]
    assert "승인 pending" in review["summary"]

    analysis = by_project["analysis-project"]
    assert analysis["kind"] == "analysis"
    assert analysis["status"] == "completed"
    assert analysis["approvalStatus"] == "not_requested"
    assert analysis["href"] == "/project/analysis-project/chief-orchestration"

    project = by_project["project/with?reserved"]
    assert project["kind"] == "project"
    assert project["id"] == "project/with?reserved"
    assert project["status"] == "draft"
    assert project["executionStatus"] == "not_started"
    assert project["approvalStatus"] == "not_requested"
    assert project["updatedAt"] == "2026-07-25T08:00:00+00:00"
    assert project["href"] == "/project/project%2Fwith%3Freserved/input-package"
    assert project["latestRunStatus"] is None


def test_recent_work_limit_validation_is_fail_fast() -> None:
    client = TestClient(app)
    headers = _session_headers(client)

    assert client.get(
        "/projects/recent-work?limit=0",
        headers=headers,
    ).status_code == 422
    assert client.get(
        "/projects/recent-work?limit=21",
        headers=headers,
    ).status_code == 422
