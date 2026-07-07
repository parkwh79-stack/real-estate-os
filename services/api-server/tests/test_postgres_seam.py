from __future__ import annotations

from db import postgres


_PG_ENV = ["REALYTICS_DATABASE_URL", "REALYTICS_PG_HOST", "REALYTICS_PG_PORT",
           "REALYTICS_PG_DB", "REALYTICS_PG_USER", "REALYTICS_PG_PASSWORD"]


def _clear(mp):
    for k in _PG_ENV:
        mp.delenv(k, raising=False)


def test_not_configured_by_default(monkeypatch):
    _clear(monkeypatch)
    assert postgres.postgres_configured() is False
    assert postgres.build_dsn() is None


def test_database_url_wins(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("REALYTICS_DATABASE_URL", "postgresql://u:p@db:5432/realytics")
    assert postgres.build_dsn() == "postgresql://u:p@db:5432/realytics"


def test_dsn_assembled_from_parts(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("REALYTICS_PG_HOST", "localhost")
    monkeypatch.setenv("REALYTICS_PG_USER", "realytics")
    monkeypatch.setenv("REALYTICS_PG_PASSWORD", "s3cr3t")
    dsn = postgres.build_dsn()
    assert dsn.startswith("postgresql://realytics:s3cr3t@localhost:5432/realytics")
    # password redacted in safe form
    assert "s3cr3t" not in postgres.safe_dsn(dsn)
    assert ":***@" in postgres.safe_dsn(dsn)


def test_schema_ddl_has_postgis_and_spatial_table():
    ddl = postgres.schema_ddl()
    joined = "\n".join(ddl)
    assert "CREATE EXTENSION IF NOT EXISTS postgis" in joined
    assert "geometry(MultiPolygon, 4326)" in joined
    assert any("orchestrations" in s for s in ddl)
    assert any("USING GIST(geom)" in s for s in ddl)


def test_schema_ddl_mirrors_all_sqlite_stores():
    """The migration must cover every persisted store, not a generic subset."""
    joined = "\n".join(postgres.schema_ddl())
    # Tables that back the calculation registry, deal-state review, audit, and
    # identity — the ones the earlier generic JSONB stub omitted.
    for table in (
        "calculation_runs",
        "deal_state_records",
        "recalculation_events",
        "user_activity_logs",
        "tenants",
        "users",
        "sessions",
        "user_ai_profiles",
        "metadata",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table} " in joined, table
    # Typed columns the reviewer filters rely on must exist on deal_state_records.
    assert "review_status" in joined
    # Every statement is terminated (loader split is clean).
    assert all(s.rstrip().endswith(";") for s in postgres.schema_ddl())


def test_initial_migration_file_exists():
    assert postgres.INITIAL_SCHEMA_FILE.exists()


def test_connect_without_config_errors(monkeypatch):
    _clear(monkeypatch)
    import pytest

    with pytest.raises(RuntimeError, match="not configured"):
        postgres.connect()


def test_status_shape(monkeypatch):
    _clear(monkeypatch)
    st = postgres.status()
    assert st["configured"] is False
    assert "driverInstalled" in st
    assert st["tableCount"] >= 10
