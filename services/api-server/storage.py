import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict


STATE_VERSION = 3
DEFAULT_STATE_FILE = Path(__file__).parent / "data" / "runtime" / "state.json"
DEFAULT_SQLITE_FILE = Path(__file__).parent / "data" / "runtime" / "realytics.sqlite3"


def storage_backend() -> str:
    configured = os.environ.get("REALYTICS_STORAGE_BACKEND")
    if configured:
        return configured.lower()
    if os.environ.get("REALYTICS_STATE_FILE") and not os.environ.get("REALYTICS_SQLITE_FILE"):
        return "json"
    return "sqlite"


def state_file_path() -> Path:
    configured = os.environ.get("REALYTICS_STATE_FILE")
    return Path(configured) if configured else DEFAULT_STATE_FILE


def sqlite_file_path() -> Path:
    configured = os.environ.get("REALYTICS_SQLITE_FILE")
    return Path(configured) if configured else DEFAULT_SQLITE_FILE


def default_state() -> Dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "project_id_counter": 1,
        "projects": {},
        "reports": {},
        "orchestrations": {},
        "market_evidence": {},
    }


def load_state() -> Dict[str, Any] | None:
    if storage_backend() == "json":
        return _load_json_state()

    state = _load_sqlite_state()
    if state:
        return state

    legacy_state = _load_json_state()
    if legacy_state:
        save_state(
            legacy_state["projects"],
            legacy_state["reports"],
            legacy_state["orchestrations"],
            legacy_state["project_id_counter"],
            legacy_state.get("market_evidence", {}),
        )
        return legacy_state

    return None


def save_state(
    projects: Dict[str, Any],
    reports: Dict[str, Any],
    orchestrations: Dict[str, Any],
    project_id_counter: int,
    market_evidence: Dict[str, Any] | None = None,
) -> None:
    market_evidence = market_evidence or {}
    if storage_backend() == "json":
        _save_json_state(projects, reports, orchestrations, project_id_counter, market_evidence)
        return
    _save_sqlite_state(projects, reports, orchestrations, project_id_counter, market_evidence)


def _load_json_state() -> Dict[str, Any] | None:
    path = state_file_path()
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        state = json.load(f)
    return _normalize_state(state)


def _save_json_state(
    projects: Dict[str, Any],
    reports: Dict[str, Any],
    orchestrations: Dict[str, Any],
    project_id_counter: int,
    market_evidence: Dict[str, Any],
) -> None:
    path = state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    payload = _normalize_state({
        "version": STATE_VERSION,
        "project_id_counter": project_id_counter,
        "projects": projects,
        "reports": reports,
        "orchestrations": orchestrations,
        "market_evidence": market_evidence,
    })
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def _connect_sqlite() -> sqlite3.Connection:
    path = sqlite_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _ensure_sqlite_schema(conn)
    return conn


def _ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reports (
            project_id TEXT NOT NULL,
            report_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, report_id)
        );
        CREATE TABLE IF NOT EXISTS orchestrations (
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT,
            started_at TEXT,
            completed_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, run_id)
        );
        CREATE TABLE IF NOT EXISTS market_evidence (
            project_id TEXT NOT NULL,
            record_key TEXT NOT NULL,
            run_id TEXT,
            evidence_id TEXT,
            evidence_type TEXT,
            validation_status TEXT,
            created_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, record_key)
        );
        """
    )
    conn.commit()


def _load_sqlite_state() -> Dict[str, Any] | None:
    path = sqlite_file_path()
    if not path.exists():
        return None

    with _connect_sqlite() as conn:
        projects = {
            row["project_id"]: json.loads(row["payload_json"])
            for row in conn.execute("SELECT project_id, payload_json FROM projects")
        }
        reports: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, payload_json FROM reports ORDER BY project_id, version, created_at"
        ):
            reports.setdefault(row["project_id"], []).append(json.loads(row["payload_json"]))

        orchestrations: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, run_id, payload_json FROM orchestrations ORDER BY project_id, started_at"
        ):
            orchestrations.setdefault(row["project_id"], {})[row["run_id"]] = json.loads(row["payload_json"])

        market_evidence: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, record_key, payload_json FROM market_evidence ORDER BY project_id, created_at, record_key"
        ):
            market_evidence.setdefault(row["project_id"], {})[row["record_key"]] = json.loads(row["payload_json"])

        metadata = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM metadata")
        }

    if not projects and not reports and not orchestrations and not metadata:
        return None

    return _normalize_state({
        "version": int(metadata.get("version", STATE_VERSION)),
        "project_id_counter": int(metadata.get("project_id_counter", 1)),
        "projects": projects,
        "reports": reports,
        "orchestrations": orchestrations,
        "market_evidence": market_evidence,
    })


def _save_sqlite_state(
    projects: Dict[str, Any],
    reports: Dict[str, Any],
    orchestrations: Dict[str, Any],
    project_id_counter: int,
    market_evidence: Dict[str, Any],
) -> None:
    with _connect_sqlite() as conn:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM metadata")
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM reports")
        conn.execute("DELETE FROM orchestrations")
        conn.execute("DELETE FROM market_evidence")
        conn.executemany(
            "INSERT INTO metadata(key, value) VALUES(?, ?)",
            [
                ("version", str(STATE_VERSION)),
                ("project_id_counter", str(project_id_counter)),
            ],
        )
        for project_id, payload in projects.items():
            conn.execute(
                "INSERT INTO projects(project_id, payload_json, updated_at) VALUES(?, ?, ?)",
                (
                    project_id,
                    json.dumps(payload, ensure_ascii=False),
                    payload.get("_meta", {}).get("updated_at"),
                ),
            )
        for project_id, records in reports.items():
            for record in records:
                conn.execute(
                    """
                    INSERT INTO reports(project_id, report_id, version, created_at, payload_json)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        record.get("report_id"),
                        int(record.get("version", 1)),
                        record.get("created_at"),
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
        for project_id, runs in orchestrations.items():
            for run_id, run in runs.items():
                conn.execute(
                    """
                    INSERT INTO orchestrations(project_id, run_id, status, started_at, completed_at, payload_json)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        run_id,
                        run.get("status"),
                        run.get("started_at"),
                        run.get("completed_at"),
                        json.dumps(run, ensure_ascii=False),
                    ),
                )
        for project_id, records in market_evidence.items():
            iterable = records.values() if isinstance(records, dict) else records
            for record in iterable:
                conn.execute(
                    """
                    INSERT INTO market_evidence(
                        project_id, record_key, run_id, evidence_id, evidence_type,
                        validation_status, created_at, payload_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        record.get("recordKey") or record.get("record_key") or record.get("evidenceId"),
                        record.get("runId") or record.get("run_id"),
                        record.get("evidenceId"),
                        record.get("evidenceType"),
                        record.get("validationStatus"),
                        record.get("createdAt") or record.get("created_at"),
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
        conn.commit()


def _normalize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    normalized = default_state()
    normalized.update(state or {})
    normalized.setdefault("version", STATE_VERSION)
    normalized.setdefault("project_id_counter", 1)
    normalized.setdefault("projects", {})
    normalized.setdefault("reports", {})
    normalized.setdefault("orchestrations", {})
    normalized.setdefault("market_evidence", {})
    return normalized
