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
    typed = {}
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "postgres")
    monkeypatch.setattr(postgres, "save_state_document", lambda state, **kw: shared.__setitem__("doc", state))
    monkeypatch.setattr(postgres, "load_state_document", lambda **kw: shared.get("doc"))
    monkeypatch.setattr(
        postgres,
        "upsert_typed_jsonb_record",
        lambda table, values, payload, **kw: typed.__setitem__(
            (table, values.get("project_id"), values.get("record_id")), payload
        ),
    )
    monkeypatch.setattr(
        postgres,
        "upsert_typed_jsonb_records",
        lambda table, records, **kw: [
            typed.__setitem__(
                (table, values.get("project_id"), values.get("record_id")), payload
            )
            for values, payload in records
        ],
    )
    monkeypatch.setattr(
        postgres,
        "list_typed_jsonb_records",
        lambda table, where=None, limit=100, **kw: [
            payload
            for (stored_table, project_id, _), payload in typed.items()
            if stored_table == table and project_id == (where or {}).get("project_id")
        ][:limit],
    )

    assert storage.storage_backend() == "postgres"
    assert storage._use_document_store() is True
    # A deal-state upsert goes through the document path -> PG save -> PG load.
    storage.upsert_deal_state_record("p1", {"recordType": "metric", "key": "k1", "value": 42})
    records = storage.list_deal_state_records("p1")
    assert any(r.get("key") == "k1" for r in records)
    assert "doc" in shared  # persisted via the Postgres document store
    assert any(key[0] == "deal_state_records" for key in typed)


class _TypedCursor:
    def __init__(self, statements, rows=None):
        self.statements = statements
        self.rows = rows or []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        self.rowcount = 1

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _TypedConn:
    def __init__(self, rows=None):
        self.statements = []
        self.rows = rows or []
        self.commits = 0

    def cursor(self):
        return _TypedCursor(self.statements, self.rows)

    def commit(self):
        self.commits += 1


def test_typed_postgres_calculation_run_upsert_uses_allowlisted_table(monkeypatch):
    monkeypatch.setattr(postgres, "ensure_schema", lambda conn: None)
    conn = _TypedConn()
    payload = {"calculationRunId": "calc:cash:1", "engineKey": "cash_flow_engine"}

    postgres.upsert_typed_jsonb_record(
        "calculation_runs",
        {
            "project_id": "p1",
            "calculation_run_id": "calc:cash:1",
            "engine_key": "cash_flow_engine",
            "engine_version": "v2",
            "status": "completed",
            "created_at": "2026-07-13T00:00:00Z",
            "updated_at": "2026-07-13T00:00:00Z",
        },
        payload,
        conn=conn,
    )

    sql, params = conn.statements[0]
    assert sql.startswith("INSERT INTO calculation_runs")
    assert "ON CONFLICT (project_id, calculation_run_id)" in sql
    assert params[0:3] == ["p1", "calc:cash:1", "cash_flow_engine"]
    assert json.loads(params[-1]) == payload


def test_typed_postgres_rejects_unknown_table():
    import pytest

    with pytest.raises(ValueError, match="Unsupported typed PostgreSQL table"):
        postgres.upsert_typed_jsonb_record(
            "untrusted_table",
            {"project_id": "p1"},
            {},
            conn=_TypedConn(),
        )


def test_postgres_agent_run_insert_and_optimistic_transition(monkeypatch):
    monkeypatch.setattr(postgres, "ensure_schema", lambda conn: None)
    conn = _TypedConn()
    payload = {
        "runId": "agent-run:1",
        "tenantId": "tenant-1",
        "agentId": "pod_ai_reviewer_agent",
        "idempotencyKey": "idem-1",
        "stateVersion": 1,
    }
    values = {
        "run_id": "agent-run:1",
        "project_id": "p1",
        "tenant_id": "tenant-1",
        "user_id": "u1",
        "request_id": "request-1",
        "agent_id": "pod_ai_reviewer_agent",
        "agent_version": "1.0.0",
        "task_type": "pod_ai_review",
        "idempotency_key": "idem-1",
        "input_hash": "hash-1",
        "status": "received",
        "state_version": 1,
        "created_at": "2026-07-14T00:00:00Z",
        "updated_at": "2026-07-14T00:00:00Z",
    }

    assert postgres.insert_typed_jsonb_record("agent_runs", values, payload, conn=conn) is True
    insert_sql, _ = conn.statements[0]
    assert insert_sql.startswith("INSERT INTO agent_runs")
    assert "ON CONFLICT DO NOTHING" in insert_sql

    next_payload = {**payload, "stateVersion": 2, "status": "capability_checked"}
    next_values = {**values, "state_version": 2, "status": "capability_checked"}
    assert postgres.update_agent_run_optimistic(
        next_values,
        next_payload,
        expected_state_version=1,
        conn=conn,
    ) is True
    update_sql, update_params = conn.statements[1]
    assert update_sql.startswith("UPDATE agent_runs SET")
    assert "WHERE run_id = %s AND state_version = %s" in update_sql
    assert update_params[-2:] == ["agent-run:1", 1]
