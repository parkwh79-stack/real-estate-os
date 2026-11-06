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


def test_history_is_read_only_when_its_project_is_opened(isolated_store):
    database, _ = isolated_store
    storage.save_state({"good": {"name": "Good"}, "bad": {"name": "Bad"}},
        {"good": [{"report_id": "r", "report": {"value": 42}}]}, {}, 3)
    with storage._connect_sqlite() as conn:
        conn.execute("INSERT INTO reports(project_id, report_id, payload_json) VALUES ('bad', 'broken', 'invalid-json')")
    state = storage.load_state(project_workspace_only=True)
    assert set(state["reports"]) == {"good", "bad"}
    assert len(state["reports"]) == 2
    assert state["reports"].get("missing") is None
    assert state["reports"]["good"][0]["report"]["value"] == 42
    with pytest.raises(json.JSONDecodeError):
        state["reports"]["bad"]
    # A failed read is not permanently cached as an empty result.
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE reports SET payload_json = ? WHERE project_id = 'bad'",
            (json.dumps({"report_id": "broken", "report": {"value": 3}}),))
    assert state["reports"]["bad"][0]["report"]["value"] == 3


def test_deferred_full_save_preserves_unopened_projects(isolated_store):
    storage.save_state({"a": {"name": "A"}, "b": {"name": "B"}},
        {"b": [{"report_id": "untouched", "report": {"value": 23}}]},
        {"b": {"run-b": {"run_id": "run-b", "status": "completed"}}}, 3,
        project_snapshots={"b": {"snapshot-b": {"snapshotId": "snapshot-b", "value": 11}}})
    state = storage.load_state(project_workspace_only=True)
    state["projects"]["a"]["name"] = "Edited A"
    storage.save_state(state["projects"], state["reports"], state["orchestrations"], 3,
        state["market_evidence"], state["project_snapshots"])
    reopened = storage.load_state()
    assert reopened["projects"]["a"]["name"] == "Edited A"
    assert reopened["reports"]["b"][0]["report"]["value"] == 23
    assert reopened["orchestrations"]["b"]["run-b"]["status"] == "completed"
    assert reopened["project_snapshots"]["b"]["snapshot-b"]["value"] == 11


def test_corrupt_deferred_history_prevents_full_save_before_deletion(isolated_store):
    database, _ = isolated_store
    storage.save_state({"a": {"name": "Original"}}, {}, {}, 2)
    with storage._connect_sqlite() as conn:
        conn.execute("INSERT INTO reports(project_id, report_id, payload_json) VALUES ('a', 'r', 'broken')")
    state = storage.load_state(project_workspace_only=True)
    state["projects"]["a"]["name"] = "Unsaved"
    with pytest.raises(json.JSONDecodeError):
        storage.save_state(state["projects"], state["reports"], state["orchestrations"], 2)
    with sqlite3.connect(database) as conn:
        assert json.loads(conn.execute("SELECT payload_json FROM projects").fetchone()[0])["name"] == "Original"
        assert conn.execute("SELECT payload_json FROM reports").fetchone()[0] == "broken"


def test_deferred_reads_keep_the_original_database_path(isolated_store, tmp_path, monkeypatch):
    storage.save_state({"a": {}}, {"a": [{"report_id": "original"}]}, {}, 2)
    state = storage.load_state(project_workspace_only=True)
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "other.sqlite3"))
    storage.save_state({"a": {}}, {"a": [{"report_id": "other"}]}, {}, 2)
    assert state["reports"]["a"][0]["report_id"] == "original"


def test_router_startup_defers_other_project_history(isolated_store, monkeypatch):
    from routers import projects

    storage.save_state({"a": {"project": {"id": "a"}}}, {}, {}, 2)
    with storage._connect_sqlite() as conn:
        conn.execute("INSERT INTO orchestrations(project_id, run_id, payload_json) VALUES ('old', 'broken', 'broken')")
    for name in ("PROJECTS_DB", "REPORTS_DB", "ORCHESTRATIONS_DB", "MARKET_EVIDENCE_DB", "PROJECT_SNAPSHOTS_DB"):
        monkeypatch.setattr(projects, name, {})
    monkeypatch.setattr(projects, "project_id_counter", 1)
    projects.load_fixtures()
    assert projects.PROJECTS_DB["a"]["project"]["id"] == "a"
    assert "old" in projects.ORCHESTRATIONS_DB
    with pytest.raises(json.JSONDecodeError):
        projects.ORCHESTRATIONS_DB["old"]


def test_deferred_collection_keeps_nested_updates_and_one_load_under_concurrency():
    from concurrent.futures import ThreadPoolExecutor
    from db.lazy_project_collection import LazyProjectCollection

    calls = []
    def load(project_id):
        calls.append(project_id)
        return {"items": []}
    store = LazyProjectCollection(["a"], load)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store["a"], range(8)))
    results[0]["items"].append("saved")
    assert all(result["items"] == ["saved"] for result in results)
    assert calls == ["a"]
    store.clear()
    store.setdefault("a", {"items": ["new"]})
    assert store["a"] == {"items": ["new"]}
    assert calls == ["a"]


def test_report_export_saves_one_record_without_loading_unrelated_history(isolated_store):
    from db.repositories.report_repository import ReportRepository

    storage.save_state({"a": {}}, {}, {}, 2)
    with storage._connect_sqlite() as conn:
        conn.execute("INSERT INTO reports(project_id, report_id, payload_json) VALUES ('bad', 'broken', 'broken')")
    state = storage.load_state(project_workspace_only=True)
    def no_full_save():
        pytest.fail("A report export must not rewrite the whole workspace")
    repository = ReportRepository(state["reports"], no_full_save, storage.upsert_report_record)
    record = repository.create("a", {"template": "synthetic-report", "title": "Test report",
        "qualityChecks": {"actualSectionCount": 1, "expectedSectionCount": 1, "isComplete": True}}, "Saved body")
    export = repository.add_export(record, {"format": "markdown", "content": "Saved body"})
    reopened = storage.load_state(project_workspace_only=True)
    saved = ReportRepository(reopened["reports"], no_full_save).get("a", record["report_id"])
    assert saved["report_markdown"] == "Saved body"
    assert saved["exports"][0]["export_id"] == export["export_id"]


def test_market_run_and_review_persist_without_full_workspace_save(isolated_store):
    from db.repositories.market_evidence_repository import MarketEvidenceRepository
    from use_cases.evidence_review import review_market_evidence_use_case

    storage.save_state({"a": {}}, {}, {}, 2)
    with storage._connect_sqlite() as conn:
        conn.execute("INSERT INTO market_evidence(project_id, record_key, payload_json) VALUES ('bad', 'broken', 'broken')")
    state = storage.load_state(project_workspace_only=True)
    def no_full_save():
        pytest.fail("Market evidence changes must not rewrite the whole workspace")
    repository = MarketEvidenceRepository(state["market_evidence"], no_full_save, storage.upsert_market_evidence_records)
    repository.add_from_run("a", {"run_id": "run-a", "artifacts": {"marketEvidenceReview": {
        "evidenceRecords": [{"evidenceId": "e1", "validationStatus": "pass", "value": 42}]}}})
    reopened = storage.load_state(project_workspace_only=True)
    assert reopened["market_evidence"]["a"]["run-a:e1"]["value"] == 42
    review_market_evidence_use_case(repository,
        {"recordKey": "run-a:e1", "reviewStatus": "needs_revision", "comment": "Recheck source"},
        "2026-10-15T03:00:00Z", "a")
    reviewed = storage.load_state(project_workspace_only=True)["market_evidence"]["a"]["run-a:e1"]
    assert reviewed["reviewStatus"] == "needs_revision"
    assert reviewed["reviewHistory"][0]["reviewComment"] == "Recheck source"
    with pytest.raises(json.JSONDecodeError):
        state["market_evidence"]["bad"]


def test_project_deletion_does_not_materialize_corrupt_histories(isolated_store):
    from db.repositories.project_repository import ProjectRepository

    database, _ = isolated_store
    storage.save_state({"a": {"name": "Delete me"}, "b": {"name": "Keep me"}},
        {"b": [{"report_id": "keep"}]}, {}, 3)
    with storage._connect_sqlite() as conn:
        conn.execute("INSERT INTO reports(project_id, report_id, payload_json) VALUES ('a', 'broken', 'broken')")
    state = storage.load_state(project_workspace_only=True)
    repository = ProjectRepository(state["projects"], state["reports"], state["orchestrations"],
        state["market_evidence"], state["project_snapshots"],
        lambda: pytest.fail("Use a project cascade, not full replacement"), lambda: "3",
        storage.delete_project_record_cascade)
    assert repository.delete("a")["name"] == "Delete me"
    assert "a" not in state["reports"]
    assert state["reports"]["b"][0]["report_id"] == "keep"
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT project_id FROM projects").fetchall() == [("b",)]
        assert conn.execute("SELECT project_id, report_id FROM reports").fetchall() == [("b", "keep")]
