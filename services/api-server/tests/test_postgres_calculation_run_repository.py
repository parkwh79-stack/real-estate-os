from contextlib import contextmanager

import pytest

from db.repositories.calculation_run_repository import (
    CalculationRunPayloadError,
    PostgresCalculationRunRepository,
    select_local_export_runs,
)


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
