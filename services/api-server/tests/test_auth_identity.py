from fastapi.testclient import TestClient

from main import app


def test_auth_identity_session_and_me_context():
    client = TestClient(app)

    tenant = client.post(
        "/auth/tenants",
        json={"tenantId": "tenant-auth", "name": "Auth Tenant"},
    )
    assert tenant.status_code == 200
    assert tenant.json()["tenant"]["tenantId"] == "tenant-auth"

    user = client.post(
        "/auth/users",
        json={
            "userId": "auth-user",
            "tenantId": "tenant-auth",
            "email": "auth@example.com",
            "displayName": "Auth User",
            "role": "reviewer",
        },
    )
    assert user.status_code == 200
    assert user.json()["user"]["role"] == "reviewer"

    session = client.post(
        "/auth/session",
        json={
            "userId": "auth-user",
            "tenantId": "tenant-auth",
            "provider": "local-test",
            "host": "web",
        },
    )
    assert session.status_code == 200
    session_id = session.json()["session"]["sessionId"]

    me = client.get("/auth/me", headers={"x-realytics-session-id": session_id})
    assert me.status_code == 200
    body = me.json()
    assert body["authenticated"] is True
    assert body["user"]["userId"] == "auth-user"
    assert "risk:review" in body["permissions"]

    projects = client.get("/projects/", headers={"x-realytics-session-id": session_id})
    assert projects.status_code == 200

    logs = client.get("/audit/user-activity", params={"user_id": "auth-user", "limit": 20}).json()["logs"]
    assert any(record["eventType"] == "login" for record in logs)
    assert any(record.get("path") == "/projects/" for record in logs)


def test_auth_me_returns_anonymous_context_when_user_missing():
    client = TestClient(app)

    me = client.get("/auth/me", headers={"x-realytics-user-id": "missing-user"})

    assert me.status_code == 200
    assert me.json()["authenticated"] is False
    assert me.json()["user"]["role"] == "anonymous"


def test_strict_rbac_blocks_viewer_report_export(monkeypatch):
    monkeypatch.setenv("REALYTICS_RBAC_MODE", "strict")
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]
    report = client.post(f"/projects/{project_id}/reports/generate", json={})
    assert report.status_code == 200
    report_id = report.json()["report_id"]

    viewer_session = client.post(
        "/auth/session",
        json={
            "userId": "viewer-user",
            "tenantId": "tenant-auth",
            "role": "viewer",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    blocked = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        headers={"x-realytics-session-id": viewer_session},
        json={"format": "markdown"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["requiredPermission"] == "report:export"

    reviewer_session = client.post(
        "/auth/session",
        json={
            "userId": "reviewer-user",
            "tenantId": "tenant-auth",
            "role": "reviewer",
            "provider": "local-test",
        },
    ).json()["session"]["sessionId"]
    allowed = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        headers={"x-realytics-session-id": reviewer_session},
        json={"format": "markdown"},
    )
    assert allowed.status_code == 200
