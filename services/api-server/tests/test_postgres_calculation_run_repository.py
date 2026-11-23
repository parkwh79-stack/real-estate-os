from contextlib import contextmanager

import pytest
from fastapi import HTTPException, Request

from db.repositories.calculation_run_repository import (
    CalculationRunRepositoryError,
    CalculationRunPayloadError,
    PostgresCalculationRunRepository,
    select_local_export_runs,
)
from routers import projects


DEPENDENTS = ("waterfall", "tranche_recovery", "unsold_ltv", "discount_stress")


def _run(run_id, engine_key, created_at, *, parent_id=None, status="completed"):
    record = {
        "calculationRunId": run_id,
        "engineKey": engine_key,
        "createdAt": created_at,
        "status": status,
        "resultSnapshot": {},
    }
    if parent_id:
        record["inputSnapshot"] = {"parentCalculationRunId": parent_id}
    return record


def test_local_export_selection_is_complete_and_lineage_bound():
    records = [
        _run("anchor-old", "cash_flow_engine", "2026-01-01T00:00:00Z"),
        _run("anchor-current", "cash_flow_engine", "2026-02-01T00:00:00Z"),
        _run("waterfall-wrong-new", "waterfall", "2026-03-02T00:00:00Z", parent_id="anchor-old"),
        _run("waterfall-current", "waterfall", "2026-03-01T00:00:00Z", parent_id="anchor-current"),
        *[
            _run(f"{key}-current", key, "2026-03-01T00:00:00Z", parent_id="anchor-current")
            for key in DEPENDENTS[1:]
        ],
    ]

    selected = select_local_export_runs(records, dependent_engine_keys=DEPENDENTS)

    assert selected["reportEligible"] is True
    assert selected["anchor"]["calculationRunId"] == "anchor-current"
    assert selected["runs"]["waterfall"]["calculationRunId"] == "waterfall-current"
    assert selected["missingEngineKeys"] == []
    assert selected["ineligibleEngineKeys"] == []


def test_local_export_selection_marks_unbound_run_ineligible():
    records = [
        _run("anchor-current", "cash_flow_engine", "2026-02-01T00:00:00Z"),
        _run("waterfall-wrong", "waterfall", "2026-03-01T00:00:00Z", parent_id="other-anchor"),
    ]

    selected = select_local_export_runs(records, dependent_engine_keys=DEPENDENTS)

    assert selected["reportEligible"] is False
    assert selected["ineligibleEngineKeys"] == ["waterfall"]
    assert selected["missingEngineKeys"] == [
        "tranche_recovery",
        "unsold_ltv",
        "discount_stress",
    ]


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return _Result(self.rows)


def test_postgres_export_selection_uses_one_tenant_scoped_statement():
    rows = [
        {
            "row_kind": "anchor",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "calculation_run_id": "anchor-a",
            "engine_key": "cash_flow_engine",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
            "payload_json": {"resultSnapshot": {}},
            "parent_calculation_run_id": None,
            "candidate_count": None,
            "eligible_count": None,
        }
    ]
    for key in DEPENDENTS:
        rows.extend(
            [
                {
                    "row_kind": "eligible",
                    "tenant_id": "tenant-a",
                    "project_id": "project-a",
                    "calculation_run_id": f"{key}-a",
                    "engine_key": key,
                    "status": "completed",
                    "created_at": "2026-01-02T00:00:00Z",
                    "payload_json": {"resultSnapshot": {}},
                    "parent_calculation_run_id": "anchor-a",
                    "candidate_count": None,
                    "eligible_count": None,
                },
                {
                    "row_kind": "presence",
                    "tenant_id": "tenant-a",
                    "project_id": "project-a",
                    "calculation_run_id": None,
                    "engine_key": key,
                    "status": None,
                    "created_at": None,
                    "payload_json": None,
                    "parent_calculation_run_id": None,
                    "candidate_count": 1,
                    "eligible_count": 1,
                },
            ]
        )
    connection = _Connection(rows)
    transaction_tenants = []

    @contextmanager
    def transaction(tenant_id):
        transaction_tenants.append(tenant_id)
        yield connection

    selected = PostgresCalculationRunRepository(
        tenant_id="tenant-a",
        transaction_factory=transaction,
    ).select_export_runs("project-a", dependent_engine_keys=DEPENDENTS)

    assert selected["reportEligible"] is True
    assert transaction_tenants == ["tenant-a"]
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "FROM realytics.calculation_runs" in sql
    assert sql.count("tenant_id = %s") == 2
    assert params[1:3] == ("tenant-a", "project-a")
    assert params[5:7] == ("tenant-a", "project-a")


def test_postgres_export_selection_rejects_out_of_scope_rows():
    connection = _Connection(
        [
            {
                "row_kind": "anchor",
                "tenant_id": "tenant-b",
                "project_id": "project-a",
                "calculation_run_id": "anchor-a",
                "engine_key": "cash_flow_engine",
                "status": "completed",
                "created_at": "2026-01-01T00:00:00Z",
                "payload_json": {},
                "parent_calculation_run_id": None,
                "candidate_count": None,
                "eligible_count": None,
            }
        ]
    )

    @contextmanager
    def transaction(_tenant_id):
        yield connection

    with pytest.raises(CalculationRunPayloadError):
        PostgresCalculationRunRepository(
            tenant_id="tenant-a",
            transaction_factory=transaction,
        ).select_export_runs("project-a", dependent_engine_keys=DEPENDENTS)


def test_postgres_list_runs_uses_tenant_scoped_transaction_and_filter():
    connection = _Connection(
        [
            {
                "row_kind": "run",
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "calculation_run_id": "run-a",
                "engine_key": "cash_flow_engine",
                "status": "completed",
                "created_at": "2026-01-01T00:00:00Z",
                "payload_json": {"resultSnapshot": {"maxMinus": 100}},
            }
        ]
    )
    transaction_tenants = []

    @contextmanager
    def transaction(tenant_id):
        transaction_tenants.append(tenant_id)
        yield connection

    records = PostgresCalculationRunRepository(
        tenant_id="tenant-a",
        transaction_factory=transaction,
    ).list_runs(
        "project-a",
        engine_key="cash_flow_engine",
        limit=25,
    )

    assert transaction_tenants == ["tenant-a"]
    assert records == [
        {
            "resultSnapshot": {"maxMinus": 100},
            "tenantId": "tenant-a",
            "projectId": "project-a",
            "calculationRunId": "run-a",
            "engineKey": "cash_flow_engine",
            "status": "completed",
        }
    ]
    assert len(connection.calls) == 1
    sql, params = connection.calls[0]
    assert "FROM realytics.calculation_runs" in sql
    assert "tenant_id = %s" in sql
    assert "project_id = %s" in sql
    assert "engine_key = %s" in sql
    assert "ORDER BY created_at DESC NULLS LAST" in sql
    assert params == ("tenant-a", "project-a", "cash_flow_engine", 25)


def test_postgres_list_runs_rejects_out_of_scope_rows():
    connection = _Connection(
        [
            {
                "row_kind": "run",
                "tenant_id": "tenant-b",
                "project_id": "project-a",
                "calculation_run_id": "run-a",
                "engine_key": "cash_flow_engine",
                "status": "completed",
                "created_at": "2026-01-01T00:00:00Z",
                "payload_json": {},
            }
        ]
    )

    @contextmanager
    def transaction(_tenant_id):
        yield connection

    with pytest.raises(CalculationRunPayloadError):
        PostgresCalculationRunRepository(
            tenant_id="tenant-a",
            transaction_factory=transaction,
        ).list_runs("project-a")


def test_postgres_get_run_uses_exact_tenant_project_reference():
    connection = _Connection(
        [
            {
                "row_kind": "run",
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "calculation_run_id": "run-a",
                "engine_key": "waterfall",
                "status": "completed",
                "created_at": "2026-01-01T00:00:00Z",
                "payload_json": {"resultSnapshot": {}},
            }
        ]
    )

    @contextmanager
    def transaction(_tenant_id):
        yield connection

    record = PostgresCalculationRunRepository(
        tenant_id="tenant-a",
        transaction_factory=transaction,
    ).get_run("project-a", "run-a")

    assert record and record["calculationRunId"] == "run-a"
    sql, params = connection.calls[0]
    assert "calculation_run_id = %s" in sql
    assert params == ("tenant-a", "project-a", "run-a")


def test_postgres_latest_runs_by_engine_selects_in_sql():
    connection = _Connection(
        [
            {
                "row_kind": "run",
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "calculation_run_id": "cashflow-a",
                "engine_key": "cash_flow_engine",
                "status": "completed",
                "created_at": "2026-01-02T00:00:00Z",
                "payload_json": {},
            },
            {
                "row_kind": "run",
                "tenant_id": "tenant-a",
                "project_id": "project-a",
                "calculation_run_id": "waterfall-a",
                "engine_key": "waterfall",
                "status": "completed",
                "created_at": "2026-01-03T00:00:00Z",
                "payload_json": {},
            },
        ]
    )

    @contextmanager
    def transaction(_tenant_id):
        yield connection

    records = PostgresCalculationRunRepository(
        tenant_id="tenant-a",
        transaction_factory=transaction,
    ).latest_runs_by_engine("project-a")

    assert {record["engineKey"] for record in records} == {
        "cash_flow_engine",
        "waterfall",
    }
    sql, params = connection.calls[0]
    assert "SELECT DISTINCT ON (engine_key)" in sql
    assert "ORDER BY engine_key" in sql
    assert params == ("tenant-a", "project-a")


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/projects/project-a/calculation-runs",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


def _stub_calculation_run_route_boundary(monkeypatch):
    monkeypatch.setattr(
        projects,
        "authoritative_project",
        lambda project_id, _request: {
            "project": {"id": project_id},
            "_meta": {"tenantId": "tenant-a"},
        },
    )
    monkeypatch.setattr(projects, "authorize_request", lambda *_args: None)
    monkeypatch.setattr(
        projects,
        "enforce_project_tenant_scope",
        lambda *_args: None,
    )
    monkeypatch.setattr(projects, "tenant_id_from_request", lambda _request: "tenant-a")
    monkeypatch.setattr(projects, "storage_backend", lambda: "postgres")
    monkeypatch.setattr(
        projects,
        "list_calculation_run_records",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy CalculationRun storage must not be used")
        ),
    )


def test_calculation_run_route_uses_verified_tenant_postgres_repository(monkeypatch):
    _stub_calculation_run_route_boundary(monkeypatch)
    calls = []

    class _Repository:
        def __init__(self, *, tenant_id):
            calls.append(("init", tenant_id))

        def list_runs(self, project_id, *, engine_key, limit):
            calls.append(("list", project_id, engine_key, limit))
            return [{"calculationRunId": "run-a"}]

    monkeypatch.setattr(projects, "PostgresCalculationRunRepository", _Repository)

    result = projects.list_project_calculation_runs(
        "project-a",
        _request(),
        engine_key="cash_flow_engine",
        limit=25,
    )

    assert calls == [
        ("init", "tenant-a"),
        ("list", "project-a", "cash_flow_engine", 25),
    ]
    assert result["count"] == 1
    assert result["records"] == [{"calculationRunId": "run-a"}]


def test_calculation_run_route_fails_closed_when_authority_is_unavailable(
    monkeypatch,
):
    _stub_calculation_run_route_boundary(monkeypatch)

    class _Repository:
        def __init__(self, *, tenant_id):
            assert tenant_id == "tenant-a"

        def list_runs(self, *_args, **_kwargs):
            raise CalculationRunRepositoryError("database unavailable")

    monkeypatch.setattr(projects, "PostgresCalculationRunRepository", _Repository)

    with pytest.raises(HTTPException) as exc_info:
        projects.list_project_calculation_runs(
            "project-a",
            _request(),
            engine_key=None,
            limit=100,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error": "calculation_run_authority_unavailable"
    }
