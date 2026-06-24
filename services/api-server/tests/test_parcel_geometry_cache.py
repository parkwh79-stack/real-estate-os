from __future__ import annotations

from db.parcel_geometry_cache import (
    PG_SELECT_SQL,
    PG_UPSERT_SQL,
    LocalParcelGeometryCache,
    build_caching_resolver,
    build_parcel_geometry_cache,
)

_GEOM = {"type": "MultiPolygon", "coordinates": [[[[127.0, 37.0], [127.1, 37.0], [127.1, 37.1], [127.0, 37.0]]]]}


def _cache(tmp_path):
    return LocalParcelGeometryCache(tmp_path / "cache.sqlite3")


def test_local_cache_put_get_roundtrip(tmp_path):
    cache = _cache(tmp_path)
    assert cache.get("PNU-1") is None
    assert cache.put("PNU-1", _GEOM, project_id="42", official_land_price=3150000.0) is True
    assert cache.get("PNU-1") == _GEOM
    assert cache.get_many(["PNU-1", "PNU-2"]) == {"PNU-1": _GEOM}


def test_local_cache_rejects_invalid_geometry(tmp_path):
    cache = _cache(tmp_path)
    assert cache.put("PNU-1", {"type": "Polygon"}, ) is False  # no coordinates
    assert cache.put("", _GEOM) is False
    assert cache.get("PNU-1") is None


def test_caching_resolver_avoids_refetch(tmp_path):
    cache = _cache(tmp_path)
    calls = []

    def upstream(pnu):
        calls.append(pnu)
        return _GEOM

    resolver = build_caching_resolver(cache, upstream, project_id="42")
    # first resolution fetches from upstream and writes back
    assert resolver("PNU-1") == _GEOM
    # second resolution is served from cache — upstream NOT called again
    assert resolver("PNU-1") == _GEOM
    assert calls == ["PNU-1"]
    assert resolver.stats == {"cacheHits": 1, "fetched": 1, "misses": 0}

    # a brand-new resolver instance also hits the persisted cache (no fetch)
    calls2 = []
    resolver2 = build_caching_resolver(cache, lambda p: calls2.append(p))
    assert resolver2("PNU-1") == _GEOM
    assert calls2 == []
    assert resolver2.stats["cacheHits"] == 1


def test_caching_resolver_counts_miss_when_upstream_empty(tmp_path):
    cache = _cache(tmp_path)
    resolver = build_caching_resolver(cache, lambda pnu: None)
    assert resolver("PNU-X") is None
    assert resolver.stats == {"cacheHits": 0, "fetched": 0, "misses": 1}
    assert cache.get("PNU-X") is None


def test_factory_uses_local_when_postgres_not_configured(monkeypatch):
    monkeypatch.delenv("REALYTICS_DATABASE_URL", raising=False)
    monkeypatch.delenv("REALYTICS_PG_HOST", raising=False)
    cache = build_parcel_geometry_cache()
    assert cache.backend == "local"


def test_postgres_sql_targets_land_parcels_geom():
    assert "land_parcels" in PG_UPSERT_SQL
    assert "ST_GeomFromGeoJSON" in PG_UPSERT_SQL and "ST_Multi" in PG_UPSERT_SQL
    assert "ON CONFLICT (pnu)" in PG_UPSERT_SQL
    assert "ST_AsGeoJSON(geom)" in PG_SELECT_SQL
