from pathlib import Path

from storage import default_state, load_state, save_state, storage_backend


def test_json_state_storage_round_trip(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(state_path))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "json")

    save_state(
        projects={"1": {"project": {"id": "1", "name": "Stored Project"}}},
        reports={"1": [{"report_id": "r-1"}]},
        orchestrations={"1": {"run-1": {"status": "completed"}}},
        project_id_counter=2,
        market_evidence={"1": {"run-1:ev-1": {"recordKey": "run-1:ev-1", "evidenceId": "ev-1"}}},
    )

    state = load_state()

    assert state["project_id_counter"] == 2
    assert state["projects"]["1"]["project"]["name"] == "Stored Project"
    assert state["reports"]["1"][0]["report_id"] == "r-1"
    assert state["orchestrations"]["1"]["run-1"]["status"] == "completed"
    assert state["market_evidence"]["1"]["run-1:ev-1"]["evidenceId"] == "ev-1"
    assert Path(state_path).exists()


def test_sqlite_state_storage_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / "realytics.sqlite3"
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(db_path))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")

    save_state(
        projects={"1": {"project": {"id": "1", "name": "SQLite Project"}, "_meta": {"updated_at": "now"}}},
        reports={"1": [{"report_id": "r-1", "version": 1, "created_at": "now"}]},
        orchestrations={"1": {"run-1": {"run_id": "run-1", "status": "completed", "started_at": "now"}}},
        project_id_counter=3,
        market_evidence={
            "1": {
                "run-1:ev-1": {
                    "recordKey": "run-1:ev-1",
                    "runId": "run-1",
                    "evidenceId": "ev-1",
                    "evidenceType": "sale_comparable",
                    "validationStatus": "pass",
                    "createdAt": "now",
                }
            }
        },
    )

    state = load_state()

    assert storage_backend() == "sqlite"
    assert state["project_id_counter"] == 3
    assert state["projects"]["1"]["project"]["name"] == "SQLite Project"
    assert state["reports"]["1"][0]["report_id"] == "r-1"
    assert state["orchestrations"]["1"]["run-1"]["status"] == "completed"
    assert state["market_evidence"]["1"]["run-1:ev-1"]["evidenceType"] == "sale_comparable"
    assert Path(db_path).exists()


def test_sqlite_state_load_migrates_legacy_json_state(tmp_path, monkeypatch):
    json_path = tmp_path / "state.json"
    db_path = tmp_path / "realytics.sqlite3"
    json_path.write_text(
        """
        {
          "version": 1,
          "project_id_counter": 4,
          "projects": {"1": {"project": {"id": "1", "name": "Legacy JSON"}}},
          "reports": {},
          "orchestrations": {}
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(json_path))
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(db_path))
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")

    state = load_state()

    assert state["projects"]["1"]["project"]["name"] == "Legacy JSON"
    assert state["project_id_counter"] == 4
    assert Path(db_path).exists()


def test_default_state_shape():
    state = default_state()

    assert state["projects"] == {}
    assert state["reports"] == {}
    assert state["orchestrations"] == {}
    assert state["market_evidence"] == {}
