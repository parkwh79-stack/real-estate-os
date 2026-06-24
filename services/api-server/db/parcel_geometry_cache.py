from __future__ import annotations

"""Parcel geometry cache for Map Intelligence.

Caches resolved parcel boundary geometry (from VWorld) keyed by PNU so the map
does not re-fetch the same parcels on every load. Two backends:

- **Local SQLite** (default, always available): a dedicated cache file under the
  runtime dir — works on the dev box today.
- **PostGIS `land_parcels`** (gated): used when Postgres is configured and the
  driver is installed; the boundary lives in the real `geom` column so spatial
  queries (Map Intelligence) can use it. Lazy psycopg; SQL is unit-testable
  without a live database.

Non-fabricating and fail-safe: any backend error degrades to a cache miss (the
caller just re-resolves) — a cache problem never breaks the map or invents data.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from db.postgres import connect as pg_connect, postgres_configured

_DEFAULT_CACHE_FILE = Path(__file__).resolve().parents[1] / "data" / "runtime" / "parcel_geometry_cache.sqlite3"

_LOCAL_DDL = (
    "CREATE TABLE IF NOT EXISTS parcel_geometry_cache ("
    "pnu TEXT PRIMARY KEY, "
    "project_id TEXT, "
    "official_land_price REAL, "
    "geometry_json TEXT NOT NULL, "
    "source TEXT, "
    "updated_at TEXT"
    ")"
)

# PostGIS land_parcels SQL (exposed for unit tests; ST_Multi coerces a Polygon to
# the column's MultiPolygon type, geometry arrives as GeoJSON in EPSG:4326).
PG_SELECT_SQL = (
    "SELECT pnu, ST_AsGeoJSON(geom) AS geojson FROM land_parcels "
    "WHERE pnu = ANY(%(pnus)s) AND geom IS NOT NULL"
)
PG_UPSERT_SQL = (
    "INSERT INTO land_parcels (pnu, project_id, official_land_price, geom, payload) "
    "VALUES (%(pnu)s, %(project_id)s, %(price)s, "
    "ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%(geojson)s), 4326)), %(payload)s::jsonb) "
    "ON CONFLICT (pnu) DO UPDATE SET "
    "project_id = EXCLUDED.project_id, "
    "official_land_price = EXCLUDED.official_land_price, "
    "geom = EXCLUDED.geom, "
    "payload = EXCLUDED.payload"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_geometry(geometry: Any) -> bool:
    return isinstance(geometry, dict) and bool(geometry.get("type")) and bool(geometry.get("coordinates"))


class LocalParcelGeometryCache:
    backend = "local"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else Path(
            os.environ.get("REALYTICS_PARCEL_CACHE_FILE") or _DEFAULT_CACHE_FILE
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(_LOCAL_DDL)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def get(self, pnu: str) -> Optional[Dict[str, Any]]:
        return self.get_many([pnu]).get(pnu)

    def get_many(self, pnus: List[str]) -> Dict[str, Dict[str, Any]]:
        wanted = [p for p in pnus if p]
        if not wanted:
            return {}
        try:
            placeholders = ",".join("?" for _ in wanted)
            with self._conn() as conn:
                rows = conn.execute(
                    f"SELECT pnu, geometry_json FROM parcel_geometry_cache WHERE pnu IN ({placeholders})",
                    wanted,
                ).fetchall()
            out: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                try:
                    out[row["pnu"]] = json.loads(row["geometry_json"])
                except (TypeError, ValueError):
                    continue
            return out
        except sqlite3.Error:
            return {}

    def put(
        self,
        pnu: str,
        geometry: Dict[str, Any],
        *,
        project_id: Optional[str] = None,
        official_land_price: Optional[float] = None,
        source: str = "vworld_cadastral",
    ) -> bool:
        if not pnu or not _valid_geometry(geometry):
            return False
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO parcel_geometry_cache "
                    "(pnu, project_id, official_land_price, geometry_json, source, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (pnu, project_id, official_land_price, json.dumps(geometry), source, _now()),
                )
            return True
        except sqlite3.Error:
            return False


class PostgresParcelGeometryCache:
    backend = "postgres"

    def get(self, pnu: str) -> Optional[Dict[str, Any]]:
        return self.get_many([pnu]).get(pnu)

    def get_many(self, pnus: List[str]) -> Dict[str, Dict[str, Any]]:
        wanted = [p for p in pnus if p]
        if not wanted:
            return {}
        try:
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(PG_SELECT_SQL, {"pnus": wanted})
                    rows = cur.fetchall()
            out: Dict[str, Dict[str, Any]] = {}
            for pnu, geojson in rows:
                if geojson:
                    try:
                        out[pnu] = json.loads(geojson)
                    except (TypeError, ValueError):
                        continue
            return out
        except Exception:  # driver missing / DB unreachable — degrade to miss
            return {}

    def put(
        self,
        pnu: str,
        geometry: Dict[str, Any],
        *,
        project_id: Optional[str] = None,
        official_land_price: Optional[float] = None,
        source: str = "vworld_cadastral",
    ) -> bool:
        if not pnu or not _valid_geometry(geometry):
            return False
        payload = {"source": source, "updatedAt": _now()}
        try:
            with pg_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        PG_UPSERT_SQL,
                        {
                            "pnu": pnu,
                            "project_id": project_id,
                            "price": official_land_price,
                            "geojson": json.dumps(geometry),
                            "payload": json.dumps(payload),
                        },
                    )
                conn.commit()
            return True
        except Exception:  # never break the map on a cache write failure
            return False


def build_parcel_geometry_cache():
    """Pick the PostGIS cache when Postgres is configured + driver installed,
    else the local SQLite cache (always works)."""
    if postgres_configured():
        try:
            import psycopg  # noqa: F401
            return PostgresParcelGeometryCache()
        except ImportError:
            pass
    return LocalParcelGeometryCache()


def build_caching_resolver(cache, upstream: Callable[[str], Optional[Dict[str, Any]]], *, project_id: Optional[str] = None):
    """Wrap an upstream PNU→geometry resolver with a cache-first layer.

    Returns a resolver(pnu)->geometry|None whose `.stats` carries
    {cacheHits, fetched, misses}. Cache hits skip the upstream (VWorld) call;
    fresh fetches are written back so the next load is a hit.
    """
    stats = {"cacheHits": 0, "fetched": 0, "misses": 0}

    def resolver(pnu: str) -> Optional[Dict[str, Any]]:
        cached = cache.get(pnu)
        if _valid_geometry(cached):
            stats["cacheHits"] += 1
            return cached
        geometry = upstream(pnu)
        if _valid_geometry(geometry):
            cache.put(pnu, geometry, project_id=project_id)
            stats["fetched"] += 1
            return geometry
        stats["misses"] += 1
        return None

    resolver.stats = stats  # type: ignore[attr-defined]
    return resolver
