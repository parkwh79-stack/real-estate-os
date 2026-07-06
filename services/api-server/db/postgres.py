from __future__ import annotations

"""PostgreSQL / PostGIS connection, config, and schema bootstrap.

This is the foundation layer for moving persistence off SQLite. It builds the
DSN from environment, lazily connects via psycopg (clear error if the driver is
not installed), and holds the PostGIS-enabled schema DDL. Routing every store to
Postgres + the live data migration is the deployment step; this module is
unit-tested for config/DSN/DDL without a live database.
"""

import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus


def postgres_configured() -> bool:
    return bool(os.environ.get("REALYTICS_DATABASE_URL") or os.environ.get("REALYTICS_PG_HOST"))


def build_dsn() -> Optional[str]:
    """Build a libpq DSN/URL from env, or None if not configured.

    Precedence: REALYTICS_DATABASE_URL (full URL) wins; otherwise assemble from
    REALYTICS_PG_HOST/PORT/DB/USER/PASSWORD (sensible defaults).
    """
    url = os.environ.get("REALYTICS_DATABASE_URL")
    if url:
        return url
    host = os.environ.get("REALYTICS_PG_HOST")
    if not host:
        return None
    port = os.environ.get("REALYTICS_PG_PORT", "5432")
    db = os.environ.get("REALYTICS_PG_DB", "realytics")
    user = os.environ.get("REALYTICS_PG_USER", "realytics")
    password = os.environ.get("REALYTICS_PG_PASSWORD", "")
    auth = f"{quote_plus(user)}:{quote_plus(password)}@" if password else f"{quote_plus(user)}@"
    return f"postgresql://{auth}{host}:{port}/{db}"


def safe_dsn(dsn: Optional[str]) -> Optional[str]:
    """Redact the password for logging/status."""
    if not dsn or "@" not in dsn:
        return dsn
    scheme_sep = "://"
    if scheme_sep not in dsn:
        return dsn
    scheme, rest = dsn.split(scheme_sep, 1)
    creds, host = rest.split("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        creds = f"{user}:***"
    return f"{scheme}{scheme_sep}{creds}@{host}"


def connect(dsn: Optional[str] = None):
    """Open a psycopg connection. Lazy-imports psycopg with a clear error."""
    dsn = dsn or build_dsn()
    if not dsn:
        raise RuntimeError("PostgreSQL is not configured (set REALYTICS_DATABASE_URL or REALYTICS_PG_HOST).")
    try:
        import psycopg  # noqa: WPS433  (lazy: only when Postgres backend is used)
    except ImportError as exc:  # pragma: no cover - depends on deploy env
        raise RuntimeError(
            "PostgreSQL selected but psycopg is not installed. "
            "Add psycopg to requirements and `pip install -r requirements.txt`."
        ) from exc
    return psycopg.connect(dsn)


# PostGIS extension + a JSONB-per-store schema mirroring the SQLite stores, plus
# a spatial table for Map Intelligence (land parcels with a geometry column).
POSTGIS_BOOTSTRAP_SQL = "CREATE EXTENSION IF NOT EXISTS postgis;"

JSONB_STORE_TABLES: List[str] = [
    "projects",
    "orchestrations",
    "reports",
    "market_evidence",
    "land_evidence",
    "risk_registers",
    "evidence_graphs",
    "chief_pod_artifacts",
    "pod_ai_reviews",
    "prompt_artifacts",
    "assumption_overrides",
    "restructuring_cures",
    "glossary_terms",
    "glossary_term_candidates",
    "project_snapshots",
    "async_jobs",
]


def schema_ddl() -> List[str]:
    """All DDL statements to bootstrap a fresh PostgreSQL/PostGIS database."""
    statements: List[str] = [POSTGIS_BOOTSTRAP_SQL]
    for table in JSONB_STORE_TABLES:
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {table} ("
            "id TEXT PRIMARY KEY, "
            "project_id TEXT, "
            "payload JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ DEFAULT now()"
            ");"
        )
        statements.append(f"CREATE INDEX IF NOT EXISTS idx_{table}_project ON {table}(project_id);")
    # Spatial table for parcel-level land geometry (Map Intelligence foundation).
    statements.append(
        "CREATE TABLE IF NOT EXISTS land_parcels ("
        "pnu TEXT PRIMARY KEY, "
        "project_id TEXT, "
        "official_land_price NUMERIC, "
        "geom geometry(MultiPolygon, 4326), "
        "payload JSONB"
        ");"
    )
    statements.append("CREATE INDEX IF NOT EXISTS idx_land_parcels_geom ON land_parcels USING GIST(geom);")
    return statements


def ensure_schema(conn) -> None:
    """Apply the schema DDL on an open psycopg connection (requires a live DB)."""
    with conn.cursor() as cur:
        for statement in schema_ddl():
            cur.execute(statement)
    conn.commit()


def status() -> Dict[str, Any]:
    dsn = build_dsn()
    driver_available = True
    try:
        import psycopg  # noqa: F401
    except ImportError:
        driver_available = False
    return {
        "configured": postgres_configured(),
        "dsn": safe_dsn(dsn),
        "driverInstalled": driver_available,
        "postgisBootstrap": POSTGIS_BOOTSTRAP_SQL,
        "tableCount": len(JSONB_STORE_TABLES) + 1,
    }
