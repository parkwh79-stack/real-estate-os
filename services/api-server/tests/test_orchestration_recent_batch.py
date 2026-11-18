from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from db.repositories import orchestration_repository as orchestration_module
from db.repositories.orchestration_repository import (
    OrchestrationRepository,
    PostgresOrchestrationRepository,
)


class _TenantRepository:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> _Result:
        self.calls.append((sql, params))
        return _Result(self.rows)


def _run(project_id: str, run_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "run_id": run_id,
        "workflow": "pf_review",
        "status": "completed",
        "executionStatus": "completed",
        "approvalStatus": "not_requested",
        "started_at": "2026-07-28T00:00:00+00:00",
        "completed_at": "2026-07-28T00:10:00+00:00",
        "steps": [],
        "errors": [],
    }


def _repository(connection: _Connection) -> PostgresOrchestrationRepository:
    tenant_id = "batch-tenant"

    @contextmanager
    def transaction(request_tenant_id: str):
        assert request_tenant_id == tenant_id
        yield connection

    return PostgresOrchestrationRepository(
        tenant_id=tenant_id,
        report_repository=_TenantRepository(tenant_id),  # type: ignore[arg-type]
        project_snapshot_repository=_TenantRepository(tenant_id),  # type: ignore[arg-type]
        transaction_factory=transaction,
    )


def test_latest_run_batch_uses_one_tenant_scoped_query() -> None:
    connection = _Connection(
        [
            ("p-1", _run("p-1", "r-1")),
            ("p-2", _run("p-2", "r-2")),
        ]
    )

    result = _repository(connection).latest_metadata_by_project(
        ["p-1", "p-2", "p-1"]
    )

    assert set(result) == {"p-1", "p-2"}
    assert result["p-1"]["run_id"] == "r-1"
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "DISTINCT ON (project_id)" in sql
    assert "tenant_id = %s" in sql
    assert "COALESCE(completed_at, started_at) DESC NULLS LAST" in sql
    assert params == ("batch-tenant", ["p-1", "p-2"])


def test_latest_run_batch_rejects_row_or_payload_outside_scope() -> None:
    outside_row = _Connection([("outside", _run("outside", "r-1"))])
    with pytest.raises(ValueError, match="outside its scope"):
        _repository(outside_row).latest_metadata_by_project(["p-1"])

    mismatched_payload = _Connection([("p-1", _run("p-2", "r-1"))])
    with pytest.raises(ValueError, match="lineage"):
        _repository(mismatched_payload).latest_metadata_by_project(["p-1"])


def test_recent_run_semantics_keep_new_running_run_ahead_of_old_completed_run(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        orchestration_module,
        "list_orchestration_run_records",
        lambda _project_id: [],
    )
    old_completed = {
        **_run("p-1", "completed-old"),
        "started_at": "2026-07-27T00:00:00+00:00",
        "completed_at": "2026-07-27T01:00:00+00:00",
    }
    new_running = {
        **_run("p-1", "running-new"),
        "status": "running",
        "executionStatus": "running",
        "started_at": "2026-07-28T08:00:00+00:00",
        "completed_at": None,
    }
    repository = OrchestrationRepository(
        {"p-1": {"completed-old": old_completed, "running-new": new_running}},
        report_repository=object(),  # type: ignore[arg-type]
        market_evidence_repository=object(),  # type: ignore[arg-type]
        project_snapshot_repository=object(),  # type: ignore[arg-type]
        persist=lambda: None,
    )

    latest = repository.latest_metadata_by_project(["p-1"])

    assert latest["p-1"]["run_id"] == "running-new"
    assert latest["p-1"]["executionStatus"] == "running"
