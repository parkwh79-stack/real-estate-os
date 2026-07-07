from __future__ import annotations

"""PostgreSQL / PostGIS connection, config, and schema bootstrap.

This is the foundation layer for moving persistence off SQLite. It builds the
DSN from environment, lazily connects via psycopg (clear error if the driver is
not installed), and holds the PostGIS-enabled schema DDL. Routing every store to
Postgres + the live data migration is the deployment step; this module is
unit-tested for config/DSN/DDL without a live database.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

# Authoritative schema lives in a versioned .sql migration so a DBA and the app
# share one source of truth (faithful mirror of storage._ensure_sqlite_schema).
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
INITIAL_SCHEMA_FILE = MIGRATIONS_DIR / "0001_initial_schema.sql"


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


def _split_sql_statements(sql: str) -> List[str]:
    """Split a .sql file into executable statements, dropping comments/blank lines."""
    lines = [line for line in sql.splitlines() if not line.lstrip().startswith("--")]
    body = "\n".join(lines)
    return [f"{stmt.strip()};" for stmt in body.split(";") if stmt.strip()]


def schema_ddl() -> List[str]:
    """All DDL statements to bootstrap a fresh PostgreSQL/PostGIS database.

    Loads the authoritative versioned migration (0001_initial_schema.sql) so the
    app's ensure_schema() and a manually-run migration provision identical tables
    — a faithful, typed mirror of the SQLite stores (not a generic JSONB stub).
    """
    if INITIAL_SCHEMA_FILE.exists():
        return _split_sql_statements(INITIAL_SCHEMA_FILE.read_text(encoding="utf-8"))
    # Fallback (should not happen in a packaged deploy): minimal spatial bootstrap.
    return [
        POSTGIS_BOOTSTRAP_SQL,
        "CREATE TABLE IF NOT EXISTS land_parcels (pnu TEXT PRIMARY KEY, project_id TEXT, "
        "official_land_price NUMERIC, geom geometry(MultiPolygon, 4326), payload JSONB);",
        "CREATE INDEX IF NOT EXISTS idx_land_parcels_geom ON land_parcels USING GIST(geom);",
    ]


def ensure_schema(conn) -> None:
    """Apply the schema DDL on an open psycopg connection (requires a live DB)."""
    with conn.cursor() as cur:
        for statement in schema_ddl():
            cur.execute(statement)
    conn.commit()


# --- State document store --------------------------------------------------
# Dual-backend v1: the whole application state is persisted as a single JSONB
# document row in `app_state`, mirroring the JSON-file backend but on Postgres
# (durable, network-shared, backup-friendly). This is the safe env-switch step;
# native per-first-class-table persistence (using the 0001 schema) is a later
# deployment optimization. Connection is injectable so the mapping is unit-
# testable without a live database or the psycopg driver.

APP_STATE_TABLE = "app_state"
APP_STATE_ROW_ID = "singleton"

APP_STATE_DDL = (
    f"CREATE TABLE IF NOT EXISTS {APP_STATE_TABLE} ("
    "id TEXT PRIMARY KEY, document JSONB NOT NULL, "
    "updated_at TIMESTAMPTZ DEFAULT now());"
)


def _ensure_app_state_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(APP_STATE_DDL)
    conn.commit()


def save_state_document(state: Dict[str, Any], *, conn=None) -> None:
    """Persist the entire state dict as one JSONB row (upsert). Opens/owns a
    connection when one is not injected."""
    import json as _json

    owns = conn is None
    if owns:
        conn = connect()
    try:
        _ensure_app_state_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {APP_STATE_TABLE}(id, document, updated_at) "
                f"VALUES (%s, %s::jsonb, now()) "
                f"ON CONFLICT (id) DO UPDATE SET document = EXCLUDED.document, updated_at = now()",
                (APP_STATE_ROW_ID, _json.dumps(state, ensure_ascii=False)),
            )
        conn.commit()
    finally:
        if owns:
            conn.close()


def load_state_document(*, conn=None) -> Optional[Dict[str, Any]]:
    """Read the state document, or None when it has not been written yet.

    Accepts JSONB (driver returns a dict) or a JSON string, so it works across
    psycopg configurations and the unit-test fakes."""
    import json as _json

    owns = conn is None
    if owns:
        conn = connect()
    try:
        _ensure_app_state_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT document FROM {APP_STATE_TABLE} WHERE id = %s",
                (APP_STATE_ROW_ID,),
            )
            row = cur.fetchone()
        if not row or row[0] is None:
            return None
        document = row[0]
        return _json.loads(document) if isinstance(document, (str, bytes, bytearray)) else document
    finally:
        if owns:
            conn.close()


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
        "stateStore": {"mode": "state_document_v1", "table": APP_STATE_TABLE},
    }
