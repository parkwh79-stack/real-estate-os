from fastapi.testclient import TestClient

from main import app


def test_login_event_and_api_request_are_available_in_activity_log():
    client = TestClient(app)

    login = client.post(
        "/audit/login-event",
        headers={"x-realytics-user-id": "audit-user"},
        json={"provider": "local-test", "status": "success", "tenantId": "tenant-a"},
    )

    assert login.status_code == 200
    assert login.json()["record"]["eventType"] == "login"
    assert login.json()["record"]["userId"] == "audit-user"

    health = client.get("/projects/", headers={"x-realytics-user-id": "audit-user"})
    assert health.status_code == 200

    logs = client.get("/audit/user-activity", params={"user_id": "audit-user", "limit": 20}).json()["logs"]
    event_types = {record["eventType"] for record in logs}
    paths = {record.get("path") for record in logs}

    assert "login" in event_types
    assert "api_request" in event_types
    assert "/projects/" in paths


def test_report_export_actions_create_sensitive_activity_logs():
    client = TestClient(app)
    project_id = client.get("/projects/").json()[0]["project"]["id"]

    generated = client.post(
        f"/projects/{project_id}/reports/generate",
        headers={"x-realytics-user-id": "report-auditor"},
        json={},
    )
    assert generated.status_code == 200
    report_id = generated.json()["report_id"]

    export = client.post(
        f"/projects/{project_id}/reports/{report_id}/exports",
        headers={"x-realytics-user-id": "report-auditor"},
        json={"format": "markdown"},
    )
    assert export.status_code == 200
    export_id = export.json()["export_id"]

    download = client.get(
        f"/projects/{project_id}/reports/{report_id}/exports/{export_id}/download",
        headers={"x-realytics-user-id": "report-auditor"},
    )
    assert download.status_code == 200

    logs = client.get(
        "/audit/user-activity",
        params={"user_id": "report-auditor", "limit": 50},
    ).json()["logs"]
    event_types = {record["eventType"] for record in logs}

    assert "report_export_created" in event_types
    assert "report_export_download" in event_types


def test_activity_summary_and_csv_export_are_available():
    client = TestClient(app)

    client.post(
        "/audit/login-event",
        headers={"x-realytics-user-id": "export-user"},
        json={"provider": "local-test", "status": "success"},
    )
    client.get("/projects/", headers={"x-realytics-user-id": "export-user"})

    summary = client.get(
        "/audit/user-activity/summary",
        params={"user_id": "export-user", "limit": 20},
    )
    assert summary.status_code == 200
    body = summary.json()["summary"]
    assert body["totalEvents"] >= 2
    assert any(row["eventType"] == "login" for row in body["eventTypeCounts"])
    assert any(row["userId"] == "export-user" for row in body["userCounts"])

    csv_export = client.get(
        "/audit/user-activity/export",
        params={"user_id": "export-user", "format": "csv", "limit": 20},
    )
    assert csv_export.status_code == 200
    assert "eventId,eventType,createdAt,userId" in csv_export.text
    assert "export-user" in csv_export.text
