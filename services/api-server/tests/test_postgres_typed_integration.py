"""Optional live PostgreSQL test.

Set REALYTICS_TEST_DATABASE_URL to an isolated disposable database. The test
never falls back to the developer or production DSN.
"""

import os
from uuid import uuid4

import pytest

from db import postgres


TEST_DSN = os.environ.get("REALYTICS_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DSN,
    reason="REALYTICS_TEST_DATABASE_URL is not configured",
)


def test_typed_tables_round_trip_in_isolated_postgres():
    project_id = f"e2e-{uuid4()}"
    conn = postgres.connect(TEST_DSN)
    try:
        postgres.ensure_schema(conn)
        payload = {
            "projectId": project_id,
            "calculationRunId": "calc:test:1",
            "engineKey": "cash_flow_engine",
            "status": "completed",
        }
        postgres.upsert_typed_jsonb_record(
            "calculation_runs",
            {
                "project_id": project_id,
                "calculation_run_id": "calc:test:1",
                "engine_key": "cash_flow_engine",
                "engine_version": "test-v1",
                "status": "completed",
                "created_at": "2026-07-13T00:00:00+00:00",
                "updated_at": "2026-07-13T00:00:00+00:00",
            },
            payload,
            conn=conn,
        )
        records = postgres.list_typed_jsonb_records(
            "calculation_runs",
            where={"project_id": project_id, "engine_key": "cash_flow_engine"},
            conn=conn,
        )
        assert records == [payload]
    finally:
        with conn.cursor() as cursor:
            for table in (
                "deal_state_records",
                "calculation_runs",
                "recalculation_events",
                "agent_handoffs",
                "pod_ai_reviews",
                "chief_pod_artifacts",
            ):
                cursor.execute(f"DELETE FROM {table} WHERE project_id = %s", (project_id,))
        conn.commit()
        conn.close()
