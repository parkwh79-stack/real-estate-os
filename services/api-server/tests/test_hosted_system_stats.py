import sqlite3

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
import storage
from routers import auth


@pytest.mark.parametrize("backend,environment", [("postgres", "development"), ("sqlite", "production")])
def test_hosted_stats_reports_unavailable_without_touching_local_sqlite(monkeypatch, backend, environment):
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", environment)
    monkeypatch.setattr(storage, "storage_backend", lambda: backend)
    monkeypatch.setattr(main, "storage_backend", lambda: backend)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Hosted statistics accessed SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.raises(HTTPException) as failure:
        auth.get_system_stats()
    assert failure.value.status_code == 503
    assert failure.value.detail["error"] == "normalized_system_statistics_unavailable"
    response = TestClient(main.app).get("/auth/stats")
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["error"] == "normalized_system_statistics_unavailable"


def test_local_pilot_stats_retains_the_existing_count_response(monkeypatch, tmp_path):
    path = tmp_path / "local-statistics.sqlite3"
    with sqlite3.connect(path) as connection:
        for table in ("users", "user_activity_logs", "projects"):
            connection.execute(f"CREATE TABLE {table} (id TEXT)")
            connection.execute(f"INSERT INTO {table} (id) VALUES (?)", ("one",))
    monkeypatch.setenv("REALYTICS_ENVIRONMENT", "development")
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(path))
    result = auth.get_system_stats()
    assert result["schemaVersion"] == "realytics-system-stats-v1"
    assert result["usersCount"] == result["dataUsageCount"] == result["projectsCount"] == 1
