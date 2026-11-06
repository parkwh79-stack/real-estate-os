"""Isolated compatibility-store reads must not deserialize unrelated history."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

import storage
from db.repositories import request_project_repository


@pytest.fixture
def isolated_sqlite(tmp_path, monkeypatch):
    db_path = tmp_path / "project-lookup.sqlite3"
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(db_path))
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(tmp_path / "legacy-state.json"))
    return db_path


def _seed_with_unrelated_invalid_history(db_path):
    project = {
        "project": {"id": "31", "name": "조회 대상"},
        "_meta": {"tenant_id": "tenant-31", "version": 7},
        "schedule": [{"key": "completion", "month_index": 24}],
    }
    storage.upsert_project_record("31", project, project_id_counter=32)
    invalid_json = '{"unrelated_large_payload":"' + "x" * 1_048_576
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO projects(project_id, payload_json) VALUES(?, ?)", ("other", invalid_json))
        conn.execute(
            "INSERT INTO reports(project_id, report_id, version, created_at, payload_json) VALUES(?, ?, ?, ?, ?)",
            ("other", "bad-report", 1, "2026-01-01", invalid_json),
        )
        conn.execute(
            "INSERT INTO orchestrations(project_id, run_id, payload_json) VALUES(?, ?, ?)",
            ("other", "bad-run", invalid_json),
        )
    return project, len(invalid_json)


@pytest.mark.parametrize("project_id", ["31", 31])
def test_lookup_returns_exact_project_without_decoding_other_projects_or_history(isolated_sqlite, project_id):
    expected, invalid_length = _seed_with_unrelated_invalid_history(isolated_sqlite)

    assert storage.get_project_record(project_id) == expected

    with sqlite3.connect(isolated_sqlite) as conn:
        assert conn.execute("SELECT length(payload_json) FROM reports WHERE report_id = 'bad-report'").fetchone()[0] == invalid_length
        assert conn.execute("SELECT value FROM metadata WHERE key = 'project_id_counter'").fetchone()[0] == "32"


@pytest.mark.parametrize("project_id", ["missing", "31' OR 1=1 --"])
def test_missing_lookup_is_none_even_with_invalid_unrelated_history(isolated_sqlite, project_id):
    _seed_with_unrelated_invalid_history(isolated_sqlite)
    assert storage.get_project_record(project_id) is None


def test_zero_project_id_is_normalized_without_changing_payload(isolated_sqlite):
    payload = {"project": {"id": "0", "name": "알려진 0 ID"}, "_meta": {"version": 0}}
    storage.upsert_project_record(0, payload)
    assert storage.get_project_record(0) == payload
    assert storage.get_project_record("0") == payload


def test_malformed_requested_project_is_not_silently_replaced_with_empty_data(isolated_sqlite):
    _seed_with_unrelated_invalid_history(isolated_sqlite)
    with pytest.raises(json.JSONDecodeError):
        storage.get_project_record("other")


def test_json_backend_keeps_legacy_state_lookup(tmp_path, monkeypatch):
    state_path = tmp_path / "legacy-state.json"
    expected = {"project": {"id": "31", "name": "JSON 원본"}, "_meta": {"version": 3}}
    state_path.write_text(json.dumps({"projects": {"31": expected}}), encoding="utf-8")
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "json")
    monkeypatch.setenv("REALYTICS_STATE_FILE", str(state_path))

    assert storage.get_project_record(31) == expected
    assert storage.get_project_record("missing") is None


def test_missing_sqlite_file_keeps_legacy_json_import(isolated_sqlite, tmp_path):
    expected = {"project": {"id": "31", "name": "최초 이관"}}
    (tmp_path / "legacy-state.json").write_text(json.dumps({"projects": {"31": expected}}), encoding="utf-8")

    assert storage.get_project_record(31) == expected
    assert isolated_sqlite.exists()
    assert storage.get_project_record("31") == expected


def test_existing_empty_sqlite_keeps_legacy_json_import(isolated_sqlite, tmp_path):
    expected = {"project": {"id": "31", "name": "빈 DB 최초 이관"}}
    (tmp_path / "legacy-state.json").write_text(json.dumps({"projects": {"31": expected}}), encoding="utf-8")
    sqlite3.connect(isolated_sqlite).close()

    assert storage.get_project_record(31) == expected
    with sqlite3.connect(isolated_sqlite) as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE project_id = '31'").fetchone()[0] == 1


@pytest.mark.parametrize("existing_record", ["report", "metadata"])
def test_populated_sqlite_does_not_import_legacy_json_for_missing_id(isolated_sqlite, tmp_path, existing_record):
    (tmp_path / "legacy-state.json").write_text(json.dumps({"projects": {"31": {"project": {"id": "31", "name": "오래된 JSON"}}}}), encoding="utf-8")
    storage.upsert_project_record("setup", {"project": {"id": "setup"}})
    with sqlite3.connect(isolated_sqlite) as conn:
        conn.execute("DELETE FROM projects WHERE project_id = 'setup'")
        if existing_record == "report":
            conn.execute("INSERT INTO reports(project_id, report_id, payload_json) VALUES(?, ?, ?)", ("other", "unrelated", "malformed unrelated report"))
        else:
            conn.execute("INSERT INTO metadata(key, value) VALUES(?, ?)", ("version", "16"))

    assert storage.get_project_record("31") is None
    with sqlite3.connect(isolated_sqlite) as conn:
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 0


@pytest.mark.parametrize("backend", ["postgres", "postgresql"])
def test_postgres_lookup_preserves_verified_request_scope(backend, monkeypatch):
    expected = {"project": {"id": "31"}, "_meta": {"tenant_id": "request-tenant", "version": 9}}
    requested = []
    def get_scoped(project_id):
        requested.append(project_id)
        return expected
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", backend)
    monkeypatch.setattr(request_project_repository, "current_project_scope", lambda: SimpleNamespace(get=get_scoped))

    assert storage.get_project_record(31) == expected
    assert requested == ["31"]


def test_postgres_lookup_still_requires_verified_scope(monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(request_project_repository, "current_project_scope", lambda: None)
    with pytest.raises(RuntimeError, match="verified request/job scope"):
        storage.get_project_record("31")
