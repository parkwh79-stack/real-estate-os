from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers import project_jobs


def test_async_pf_review_uses_tenant_authoritative_project(monkeypatch):
    looked_up = []
    hydrated = []
    submitted = []
    normalized_project = {
        "tenant_id": "tenant-a",
        "project": {"id": "project-a", "name": "Normalized authority"},
    }

    monkeypatch.setattr(
        project_jobs,
        "authorize_request",
        lambda request, permission: {
            "user": {
                "userId": "analyst-a",
                "tenantId": "tenant-a",
                "role": "analyst",
            }
        },
    )

    def lookup(project_id, tenant_id):
        looked_up.append((project_id, tenant_id))
        return normalized_project if (project_id, tenant_id) == ("project-a", "tenant-a") else None

    def hydrate(project_id, input_data, authoritative_project):
        hydrated.append((project_id, input_data, authoritative_project))
        return {**authoritative_project, "hydrated": True}

    def submit(repository, project_id, source_data, options, *, background):
        submitted.append((repository, project_id, source_data, options, background))
        return {"jobId": "job-a", "status": "queued"}

    monkeypatch.setattr(project_jobs, "submit_pf_review_job", submit)
    app = FastAPI()
    app.include_router(
        project_jobs.build_project_jobs_router(
            project_lookup=lookup,
            source_builder=hydrate,
            orchestration_repository_factory=lambda tenant_id: f"repo:{tenant_id}",
        )
    )

    response = TestClient(app).post(
        "/projects/project-a/orchestrations/pf-review/async",
        json={"options": {"scenario": "base"}},
    )

    assert response.status_code == 200
    assert looked_up == [("project-a", "tenant-a")]
    assert hydrated[0][2] is normalized_project
    repository, project_id, source_data, options, background = submitted[0]
    assert repository == "repo:tenant-a"
    assert project_id == "project-a"
    assert source_data["hydrated"] is True
    assert options["tenantId"] == "tenant-a"
    assert options["requestedBy"] == "analyst-a"
    assert background is True


def test_async_pf_review_authenticates_before_project_lookup(monkeypatch):
    looked_up = []

    def reject(request, permission):
        raise HTTPException(status_code=401, detail="unauthorized")

    monkeypatch.setattr(project_jobs, "authorize_request", reject)
    app = FastAPI()
    app.include_router(
        project_jobs.build_project_jobs_router(
            project_lookup=lambda project_id, tenant_id: looked_up.append(
                (project_id, tenant_id)
            ),
            source_builder=lambda project_id, input_data, project: project,
            orchestration_repository_factory=lambda tenant_id: object(),
        )
    )

    response = TestClient(app).post(
        "/projects/project-a/orchestrations/pf-review/async",
        json={},
    )

    assert response.status_code == 401
    assert looked_up == []
