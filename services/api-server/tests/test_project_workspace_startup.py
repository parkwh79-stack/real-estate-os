import json
import sqlite3

import pytest

import storage


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    database = tmp_path / "workspace.sqlite3"
    state_file = tmp_path / "legacy.json"
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(database))
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(state_file))
    return database, state_file


def test_startup_keeps_resumable_data_without_parsing_unrelated_history(isolated_store):
    database, _ = isolated_store
    storage.save_state({"7": {"project": {"id": "7", "name": "Existing"}}},
        {"7": [{"report_id": "saved-report", "report": {"value": 42}}]},
        {"7": {"saved-run": {"run_id": "saved-run", "status": "completed"}}}, 8,
        project_snapshots={"7": {"snapshot-7": {"snapshotId": "snapshot-7", "value": 42}}})
    # A large malformed historical artifact must not block project startup.
    broken_payload = "not-json-" + "x" * 2_000_000
    with storage._connect_sqlite() as conn:
        conn.execute("INSERT INTO chief_pod_artifacts (project_id, artifact_key, payload_json) VALUES (?, ?, ?)",
            ("old", "old-artifact", broken_payload))
    state = storage.load_state(project_workspace_only=True)
    assert state["projects"]["7"]["project"]["name"] == "Existing"
    assert state["reports"]["7"][0]["report"]["value"] == 42
    assert state["orchestrations"]["7"]["saved-run"]["status"] == "completed"
    assert state["project_snapshots"]["7"]["snapshot-7"]["value"] == 42
    assert state["project_id_counter"] == 8
    storage.upsert_project_record("7", {"project": {"id": "7", "name": "Edited"}}, 8)
    reopened = storage.load_state(project_workspace_only=True)
    assert reopened["projects"]["7"]["project"]["name"] == "Edited"
    assert reopened["reports"] == state["reports"]
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT payload_json FROM chief_pod_artifacts").fetchone()[0] == broken_payload
    # The full-history reader still exposes corruption to callers that need it.
    with pytest.raises(json.JSONDecodeError):
        storage.load_state()


def test_history_only_database_does_not_resurrect_stale_legacy_projects(isolated_store):
    _, state_file = isolated_store
    state_file.write_text(json.dumps({"projects": {"stale": {"id": "stale"}}}), encoding="utf-8")
    with storage._connect_sqlite() as conn:
        conn.execute("INSERT INTO chief_pod_artifacts (project_id, artifact_key, payload_json) VALUES ('old', 'old', 'broken')")
    state = storage.load_state(project_workspace_only=True)
    assert state["projects"] == {}
    assert state["project_id_counter"] == 1


def test_empty_database_retains_legacy_json_migration(isolated_store):
    _, state_file = isolated_store
    source = {**storage.default_state(), "projects": {"4": {"project": {"id": "4", "name": "Legacy"}}},
        "reports": {"4": [{"report_id": "legacy-report"}]}, "project_id_counter": 5}
    state_file.write_text(json.dumps(source), encoding="utf-8")
    first = storage.load_state(project_workspace_only=True)
    state_file.unlink()
    second = storage.load_state(project_workspace_only=True)
    assert first["projects"] == second["projects"] == source["projects"]
    assert first["reports"] == second["reports"] == source["reports"]
    assert second["project_id_counter"] == 5
