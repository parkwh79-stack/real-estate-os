"""Unit tests for the Postgres state-document store WITHOUT a live database.

A fake psycopg-style connection captures executed SQL + params and simulates
the single-row app_state store, so the save/load round-trip and the storage.py
dual-backend seam are verified offline (psycopg is not installed in CI here).
"""
import json

from db import postgres


class _FakeCursor:
    def __init__(self, store):
        self._store = store
        self._result = None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("CREATE TABLE"):
            return
        if s.startswith("INSERT INTO app_state"):
            # params = (id, json_document)
            self._store["row"] = (json.loads(params[1]),)
            return
        if s.startswith("SELECT document FROM app_state"):
            self._result = self._store.get("row")
            return
        raise AssertionError(f"unexpected SQL: {s}")

    def fetchone(self):
        return self._result

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, store):
        self._store = store
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self._store)

    def commit(self):
        self.commits += 1

    def close(self):
        pass


def test_save_then_load_round_trips_the_state_document():
    store = {}
    conn = _FakeConn(store)
    state = {
        "version": 3,
        "project_id_counter": 7,
        "projects": {"642": {"project": {"name": "본리동", "id": "642"}}},
        "deal_state_records": {"642": {"m1": {"recordType": "metric", "value": 1}}},
    }

    postgres.save_state_document(state, conn=conn)
    loaded = postgres.load_state_document(conn=conn)

    assert loaded == state
    assert loaded["projects"]["642"]["project"]["name"] == "본리동"  # unicode survives
    assert conn.commits >= 1


def test_load_returns_none_when_empty():
    assert postgres.load_state_document(conn=_FakeConn({})) is None


def test_load_accepts_jsonb_dict_and_json_string():
    # psycopg may return JSONB already parsed (dict) or as text — both work.
    dict_store = {"row": ({"projects": {}},)}
    assert postgres.load_state_document(conn=_FakeConn(dict_store)) == {"projects": {}}
    str_store = {"row": (json.dumps({"projects": {"1": {}}}),)}
    assert postgres.load_state_document(conn=_FakeConn(str_store)) == {"projects": {"1": {}}}


def test_storage_seam_routes_to_postgres(monkeypatch):
    """storage._load_json_state / _save_json_state route to the PG document store
    when the backend is postgres — the whole dual-backend seam, offline."""
    import storage

    shared = {}
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(postgres, "save_state_document", lambda state, **kw: shared.__setitem__("doc", state))
    monkeypatch.setattr(postgres, "load_state_document", lambda **kw: shared.get("doc"))

    assert storage.storage_backend() == "postgres"
    assert storage._use_document_store() is True
    # A deal-state upsert goes through the document path -> PG save -> PG load.
    storage.upsert_deal_state_record("p1", {"recordType": "metric", "key": "k1", "value": 42})
    records = storage.list_deal_state_records("p1")
    assert any(r.get("key") == "k1" for r in records)
    assert "doc" in shared  # persisted via the Postgres document store
