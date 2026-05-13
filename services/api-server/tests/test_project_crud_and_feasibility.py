from fastapi.testclient import TestClient

from main import app
from routers import projects


def test_project_crud_create_update_delete_cascades_runtime_records(monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)

    created = client.post(
        "/projects/",
        json={
            "project": {"name": "CRUD QA Project", "type": "mixed_use"},
            "feasibility_input": {"total_revenue": 1000, "total_cost": 800},
        },
    )
    assert created.status_code == 200
    body = created.json()
    project_id = body["project"]["id"]
    assert body["id"] == project_id
    assert body["project"]["name"] == "CRUD QA Project"
    assert body["project"]["status"] == "draft"
    assert body["_meta"]["created_at"]

    fetched = client.get(f"/projects/{project_id}")
    assert fetched.status_code == 200
    assert fetched.json()["project"]["id"] == project_id

    updated = client.put(
        f"/projects/{project_id}",
        json={"project": {"status": "pf_review"}, "land": {"address": "Seoul"}},
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["project"]["status"] == "pf_review"
    assert updated_body["land"]["address"] == "Seoul"

    projects.REPORTS_DB[project_id] = [{"report_id": "r-1"}]
    projects.ORCHESTRATIONS_DB[project_id] = {"run-1": {"status": "completed"}}
    deleted = client.delete(f"/projects/{project_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert project_id not in projects.PROJECTS_DB
    assert project_id not in projects.REPORTS_DB
    assert project_id not in projects.ORCHESTRATIONS_DB
    assert client.get(f"/projects/{project_id}").status_code == 404


def test_feasibility_calculate_endpoint_uses_project_defaults_and_overrides(monkeypatch):
    monkeypatch.setattr(projects, "persist_state", lambda: None)
    client = TestClient(app)
    created = client.post(
        "/projects/",
        json={
            "project": {"name": "Feasibility QA Project"},
            "feasibility_input": {
                "total_revenue": 1000,
                "total_cost": 700,
                "finance_cost": 40,
            },
        },
    ).json()
    project_id = created["project"]["id"]

    base_result = client.post(f"/projects/{project_id}/feasibility/calculate", json={})
    assert base_result.status_code == 200
    assert base_result.json()["development_profit"] == 300
    assert base_result.json()["status"] == "strong"

    override_result = client.post(
        f"/projects/{project_id}/feasibility/calculate",
        json={"total_cost": 950},
    )
    assert override_result.status_code == 200
    assert override_result.json()["development_profit"] == 50
    assert override_result.json()["status"] in {"weak", "thin"}

    client.delete(f"/projects/{project_id}")
