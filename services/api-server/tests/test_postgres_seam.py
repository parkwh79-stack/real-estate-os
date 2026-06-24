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
