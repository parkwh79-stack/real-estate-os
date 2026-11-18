from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from db.repositories.request_project_repository import project_request_scope
import use_cases.approval_readiness_gate as gate


class Connection:
    def __init__(self, rows=()):
        self.rows = rows
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self

    def execute(self, query, params=None):
        self.queries.append((query, params))
        return self

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return None


@pytest.mark.parametrize("permit_read_fails", [False, True])
def test_worker_readiness_reads_calculation_override_and_legal_through_worker_pool(
    monkeypatch, permit_read_fails,
):
    calls = []
    class ReadinessConnection(Connection):
        def execute(self, query, params=None):
            result = super().execute(query, params)
            if permit_read_fails and "FROM project_permit_review_versions" in query:
                raise RuntimeError("permit query failed")
            return result

    connection = ReadinessConnection()
    @contextmanager
    def worker_transaction(tenant):
        calls.append(tenant)
        yield connection
    monkeypatch.setattr(gate, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(gate, "api_tenant_transaction", lambda *_: pytest.fail("Worker used API pool"))
    for name in ("list_deal_state_records", "list_pod_ai_review_records"):
        monkeypatch.setattr(gate, name, lambda *_, **__: [])
    for name in ("list_assumption_override_records", "list_legal_review_records", "list_permit_review_records"):
        monkeypatch.setattr(gate, name, lambda *_, **__: pytest.fail("Legacy app_state read"))
    with project_request_scope(
        lambda: SimpleNamespace(tenant_id="tenant-a"),
        transaction_factory=worker_transaction, worker_id="worker-a",
    ):
        result = gate.build_deal_state_approval_readiness("p1", tenant_id="tenant-a")
    assert calls == ["tenant-a"] * 5
    sql_text = "\n".join(query for query, _ in connection.queries)
    assert "realytics.calculation_runs" in sql_text
    assert "realytics.assumption_overrides" in sql_text
    assert "FROM project_legal_review_versions" in sql_text
    assert "FROM project_permit_review_versions" in sql_text
    assert "legal_evidence_snapshots" in sql_text
    assert result["legalEvidenceNormalizedReason"] == "normalized_snapshot_missing"
    assert result["legalReviewRecordReadable"] is True
    assert result["legalReviewStatus"] == "not_run"
    assert result["permitReviewRecordReadable"] is not permit_read_fails
    assert result["permitReviewStatus"] == ("unavailable" if permit_read_fails else "not_run")
    assert result["pass"] is False


def test_worker_readiness_tenant_mismatch_fails_before_any_pool_checkout(monkeypatch):
    monkeypatch.setattr(gate, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(gate, "list_deal_state_records", lambda *_, **__: [])
    with project_request_scope(
        lambda: SimpleNamespace(tenant_id="tenant-a"),
        transaction_factory=lambda *_: pytest.fail("Wrong tenant checkout"), worker_id="worker-a",
    ):
        result = gate.build_deal_state_approval_readiness("p1", tenant_id="tenant-b")
    assert result["readable"] is False
    assert result["gates"][0]["code"] == "DEAL_STATE_RECORDS_READABLE"


def test_worker_scope_missing_factory_cannot_fall_back_to_api():
    with project_request_scope(lambda: SimpleNamespace(tenant_id="tenant-a"), worker_id="worker-a"):
        with pytest.raises(RuntimeError, match="dedicated transaction"):
            gate._readiness_transaction_factory("tenant-a")


def test_normalized_override_read_is_tenant_scoped_and_excludes_expired_payload():
    now = datetime.now(timezone.utc)
    rows = [
        ("tenant-a", "p1", "approved-1", "cost", "cost.total", "active", "reviewer", now, now,
         {"projectId": "p1", "status": "pending", "value": 1}),
        ("tenant-a", "p1", "expired-1", "cost", "cost.total", "active", "reviewer", now, now,
         {"projectId": "p1", "expiresAt": "2000-01-01T00:00:00Z"}),
    ]
    connection = Connection(rows)
    @contextmanager
    def transaction(tenant):
        assert tenant == "tenant-a"
        yield connection
    with project_request_scope(lambda: SimpleNamespace(tenant_id="tenant-a"), transaction_factory=transaction):
        records = gate.load_active_normalized_assumption_overrides("p1", tenant_id="tenant-a")
    assert [record["overrideId"] for record in records] == ["approved-1"]
    assert records[0]["status"] == "active"
    assert connection.queries[0][1] == ("tenant-a", "p1")
    assert "tenant_id = %s AND project_id = %s AND status = 'active'" in connection.queries[0][0]


def test_normalized_override_row_cannot_supply_another_tenants_payload():
    connection = Connection([("tenant-a", "p1", "o1", None, None, "active", None, None, None, {"tenantId": "tenant-b"})])
    @contextmanager
    def transaction(_):
        yield connection
    with project_request_scope(lambda: SimpleNamespace(tenant_id="tenant-a"), transaction_factory=transaction):
        with pytest.raises(ValueError, match="tenant mismatch"):
            gate.load_active_normalized_assumption_overrides("p1", tenant_id="tenant-a")
