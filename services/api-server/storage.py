import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4


STATE_VERSION = 8
DEFAULT_STATE_FILE = Path(__file__).parent / "data" / "runtime" / "state.json"
DEFAULT_SQLITE_FILE = Path(__file__).parent / "data" / "runtime" / "realytics.sqlite3"
MAX_JSON_ACTIVITY_LOGS = 10000


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
        "land_evidence": {},
        "risk_registers": {},
        "project_snapshots": {},
        "user_activity_logs": [],
        "identity": {
            "tenants": {},
            "users": {},
            "sessions": {},
        },
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
            legacy_state.get("project_snapshots", {}),
            legacy_state.get("user_activity_logs", []),
            risk_registers=legacy_state.get("risk_registers", {}),
        )
        return legacy_state

    return None


def save_state(
    projects: Dict[str, Any],
    reports: Dict[str, Any],
    orchestrations: Dict[str, Any],
    project_id_counter: int,
    market_evidence: Dict[str, Any] | None = None,
    project_snapshots: Dict[str, Any] | None = None,
    user_activity_logs: list[Dict[str, Any]] | None = None,
    land_evidence: Dict[str, Any] | None = None,
    risk_registers: Dict[str, Any] | None = None,
) -> None:
    market_evidence = market_evidence or {}
    project_snapshots = project_snapshots or {}
    land_evidence = land_evidence or _load_existing_land_evidence_records()
    risk_registers = risk_registers or _load_existing_risk_registers()
    if user_activity_logs is None:
        user_activity_logs = _load_existing_user_activity_logs()
    identity = _load_existing_identity_records()
    if storage_backend() == "json":
        _save_json_state(projects, reports, orchestrations, project_id_counter, market_evidence, land_evidence, risk_registers, project_snapshots, user_activity_logs, identity)
        return
    _save_sqlite_state(projects, reports, orchestrations, project_id_counter, market_evidence, risk_registers, project_snapshots)


def record_user_activity_log(event: Dict[str, Any]) -> Dict[str, Any]:
    """Append a user/server activity event for troubleshooting and audit review."""
    record = {
        "eventId": event.get("eventId") or str(uuid4()),
        "eventType": event.get("eventType") or "api_request",
        "createdAt": event.get("createdAt") or datetime.now(timezone.utc).isoformat(),
        "userId": event.get("userId") or event.get("user_id") or "anonymous",
        "projectId": event.get("projectId") or event.get("project_id"),
        "runId": event.get("runId") or event.get("run_id"),
        "method": event.get("method"),
        "path": event.get("path"),
        "statusCode": event.get("statusCode") or event.get("status_code"),
        "durationMs": event.get("durationMs") or event.get("duration_ms"),
        "clientHost": event.get("clientHost") or event.get("client_host"),
        "userAgent": event.get("userAgent") or event.get("user_agent"),
        "requestId": event.get("requestId") or event.get("request_id"),
        "metadata": event.get("metadata") or {},
    }
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        logs = state.get("user_activity_logs", [])
        logs.append(record)
        state["user_activity_logs"] = logs[-MAX_JSON_ACTIVITY_LOGS:]
        _save_json_state(
            state["projects"],
            state["reports"],
            state["orchestrations"],
            state["project_id_counter"],
            state.get("market_evidence", {}),
            state.get("land_evidence", {}),
            state.get("risk_registers", {}),
            state.get("project_snapshots", {}),
            state["user_activity_logs"],
            state.get("identity", default_state()["identity"]),
        )
        return record

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO user_activity_logs(
                event_id, event_type, created_at, user_id, project_id, run_id,
                method, path, status_code, duration_ms, client_host, user_agent,
                request_id, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["eventId"],
                record["eventType"],
                record["createdAt"],
                record["userId"],
                record["projectId"],
                record["runId"],
                record["method"],
                record["path"],
                record["statusCode"],
                record["durationMs"],
                record["clientHost"],
                record["userAgent"],
                record["requestId"],
                json.dumps(record["metadata"], ensure_ascii=False),
            ),
        )
        conn.commit()
    return record


def list_user_activity_logs(
    *,
    limit: int = 100,
    user_id: str | None = None,
    project_id: str | None = None,
    event_type: str | None = None,
) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 1000))
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        logs = list(state.get("user_activity_logs", []))
        logs = _filter_user_activity_logs(logs, user_id=user_id, project_id=project_id, event_type=event_type)
        return list(reversed(logs))[:limit]

    query = "SELECT * FROM user_activity_logs WHERE 1=1"
    params: list[Any] = []
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _connect_sqlite() as conn:
        return [_activity_row_to_record(row) for row in conn.execute(query, params)]


def upsert_land_evidence_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    evidence_type = record.get("evidenceType") or record.get("evidence_type") or "land_evidence"
    pnu = record.get("pnu") or record.get("resolvedPnu")
    parcel_key = record.get("parcelKey") or record.get("parcel_key") or pnu or record.get("addressFragment")
    evidence_id = record.get("evidenceId") or record.get("evidence_id") or f"{evidence_type}:{parcel_key or uuid4()}"
    record_key = record.get("recordKey") or record.get("record_key") or evidence_id
    normalized = {
        **record,
        "recordKey": str(record_key),
        "projectId": project_id,
        "evidenceId": str(evidence_id),
        "evidenceType": evidence_type,
        "pnu": pnu,
        "parcelKey": parcel_key,
        "sourceName": record.get("sourceName") or record.get("source") or "manual_upload",
        "sourceType": record.get("sourceType") or record.get("source_type") or "manual_upload",
        "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("source_url_or_evidence_id") or evidence_id,
        "baseYear": record.get("baseYear") or record.get("base_year") or record.get("officialLandPriceBaseYear"),
        "baseDate": record.get("baseDate") or record.get("base_date") or record.get("officialLandPriceBaseDate"),
        "verificationStatus": record.get("verificationStatus") or record.get("verification_status") or "attached_pending_review",
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("land_evidence", {})
        state["land_evidence"].setdefault(project_id, {})[normalized["recordKey"]] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO land_evidence(
                project_id, record_key, evidence_id, evidence_type, pnu, parcel_key,
                source_name, source_type, source_locator, base_year, base_date,
                verification_status, created_at, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, record_key) DO UPDATE SET
                evidence_id=excluded.evidence_id,
                evidence_type=excluded.evidence_type,
                pnu=excluded.pnu,
                parcel_key=excluded.parcel_key,
                source_name=excluded.source_name,
                source_type=excluded.source_type,
                source_locator=excluded.source_locator,
                base_year=excluded.base_year,
                base_date=excluded.base_date,
                verification_status=excluded.verification_status,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                normalized["recordKey"],
                normalized["evidenceId"],
                normalized["evidenceType"],
                normalized["pnu"],
                normalized["parcelKey"],
                normalized["sourceName"],
                normalized["sourceType"],
                normalized["sourceUrlOrEvidenceId"],
                normalized["baseYear"],
                normalized["baseDate"],
                normalized["verificationStatus"],
                normalized["createdAt"],
                normalized["updatedAt"],
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def list_land_evidence_records(
    project_id: str,
    *,
    pnu: str | None = None,
    evidence_type: str | None = None,
) -> list[Dict[str, Any]]:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("land_evidence", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM land_evidence WHERE project_id = ?"
        params: list[Any] = [project_id]
        if pnu:
            query += " AND pnu = ?"
            params.append(pnu)
        if evidence_type:
            query += " AND evidence_type = ?"
            params.append(evidence_type)
        query += " ORDER BY updated_at DESC, record_key"
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if pnu:
        records = [record for record in records if record.get("pnu") == pnu]
    if evidence_type:
        records = [record for record in records if record.get("evidenceType") == evidence_type]
    return records


def upsert_risk_register_record(
    project_id: str,
    run_id: str,
    risk_register: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist the latest Risk Register artifact separately from the orchestration run."""
    now = datetime.now(timezone.utc).isoformat()
    normalized = {
        **(risk_register or {}),
        "schemaVersion": (risk_register or {}).get("schemaVersion") or "realytics-risk-register-v1",
        "projectId": project_id,
        "runId": run_id,
        "registerId": (risk_register or {}).get("registerId") or f"{run_id}:risk-register",
        "status": (risk_register or {}).get("status") or "unknown",
        "updatedAt": now,
    }
    normalized.setdefault("risks", [])
    normalized.setdefault("reviewSummary", {})
    normalized["riskCount"] = len(normalized.get("risks") or [])

    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("risk_registers", {})
        state["risk_registers"].setdefault(project_id, {})[run_id] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO risk_registers(
                project_id, run_id, register_id, status, risk_count,
                updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, run_id) DO UPDATE SET
                register_id=excluded.register_id,
                status=excluded.status,
                risk_count=excluded.risk_count,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                run_id,
                normalized["registerId"],
                normalized["status"],
                normalized["riskCount"],
                normalized["updatedAt"],
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def get_risk_register_record(project_id: str, run_id: str) -> Dict[str, Any] | None:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        return state.get("risk_registers", {}).get(project_id, {}).get(run_id)
    with _connect_sqlite() as conn:
        row = conn.execute(
            "SELECT payload_json FROM risk_registers WHERE project_id = ? AND run_id = ?",
            (project_id, run_id),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_risk_register_records(project_id: str) -> list[Dict[str, Any]]:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("risk_registers", {}).get(project_id, {}).values())
    else:
        with _connect_sqlite() as conn:
            records = [
                json.loads(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM risk_registers WHERE project_id = ? ORDER BY updated_at DESC",
                    (project_id,),
                )
            ]
    return sorted(records, key=lambda item: item.get("updatedAt") or "", reverse=True)


def upsert_tenant(record: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    tenant = {
        "tenantId": record.get("tenantId") or record.get("tenant_id") or "default",
        "name": record.get("name") or record.get("tenantName") or record.get("tenant_name") or "Default Workspace",
        "status": record.get("status") or "active",
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
        "metadata": record.get("metadata") or {},
    }
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("identity", default_state()["identity"])
        existing = state["identity"].setdefault("tenants", {}).get(tenant["tenantId"], {})
        tenant["createdAt"] = existing.get("createdAt", tenant["createdAt"])
        state["identity"]["tenants"][tenant["tenantId"]] = {**existing, **tenant}
        _save_json_state_from_state(state)
        return state["identity"]["tenants"][tenant["tenantId"]]

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO tenants(tenant_id, name, status, created_at, updated_at, metadata_json)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id) DO UPDATE SET
                name=excluded.name,
                status=excluded.status,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                tenant["tenantId"],
                tenant["name"],
                tenant["status"],
                tenant["createdAt"],
                tenant["updatedAt"],
                json.dumps(tenant["metadata"], ensure_ascii=False),
            ),
        )
        conn.commit()
    return tenant


def upsert_user(record: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    user_id = record.get("userId") or record.get("user_id") or record.get("email")
    if not user_id:
        raise ValueError("userId or email is required")
    tenant_id = record.get("tenantId") or record.get("tenant_id") or "default"
    upsert_tenant({"tenantId": tenant_id, "name": record.get("tenantName") or record.get("tenant_name") or "Default Workspace"})
    user = {
        "userId": str(user_id),
        "tenantId": tenant_id,
        "email": record.get("email"),
        "displayName": record.get("displayName") or record.get("display_name") or str(user_id),
        "role": record.get("role") or "analyst",
        "status": record.get("status") or "active",
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
        "metadata": record.get("metadata") or {},
    }
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("identity", default_state()["identity"])
        existing = state["identity"].setdefault("users", {}).get(user["userId"], {})
        user["createdAt"] = existing.get("createdAt", user["createdAt"])
        state["identity"]["users"][user["userId"]] = {**existing, **user}
        _save_json_state_from_state(state)
        return state["identity"]["users"][user["userId"]]

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, tenant_id, email, display_name, role, status, created_at, updated_at, metadata_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                tenant_id=excluded.tenant_id,
                email=excluded.email,
                display_name=excluded.display_name,
                role=excluded.role,
                status=excluded.status,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                user["userId"],
                user["tenantId"],
                user["email"],
                user["displayName"],
                user["role"],
                user["status"],
                user["createdAt"],
                user["updatedAt"],
                json.dumps(user["metadata"], ensure_ascii=False),
            ),
        )
        conn.commit()
    return user


def create_user_session(record: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    user_id = record.get("userId") or record.get("user_id") or record.get("email")
    existing_user = get_user(str(user_id)) if user_id else None
    user = existing_user or upsert_user(record)
    session = {
        "sessionId": record.get("sessionId") or record.get("session_id") or str(uuid4()),
        "userId": user["userId"],
        "tenantId": user["tenantId"],
        "provider": record.get("provider") or "local",
        "status": record.get("status") or "active",
        "createdAt": now,
        "lastSeenAt": now,
        "expiresAt": record.get("expiresAt") or record.get("expires_at"),
        "metadata": record.get("metadata") or {},
    }
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("identity", default_state()["identity"])
        state["identity"].setdefault("sessions", {})[session["sessionId"]] = session
        _save_json_state_from_state(state)
        return session

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO sessions(session_id, user_id, tenant_id, provider, status, created_at, last_seen_at, expires_at, metadata_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                status=excluded.status,
                last_seen_at=excluded.last_seen_at,
                expires_at=excluded.expires_at,
                metadata_json=excluded.metadata_json
            """,
            (
                session["sessionId"],
                session["userId"],
                session["tenantId"],
                session["provider"],
                session["status"],
                session["createdAt"],
                session["lastSeenAt"],
                session["expiresAt"],
                json.dumps(session["metadata"], ensure_ascii=False),
            ),
        )
        conn.commit()
    return session


def get_user(user_id: str) -> Dict[str, Any] | None:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        return state.get("identity", {}).get("users", {}).get(user_id)
    with _connect_sqlite() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return _user_row_to_record(row) if row else None


def get_user_session(session_id: str) -> Dict[str, Any] | None:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        return state.get("identity", {}).get("sessions", {}).get(session_id)
    with _connect_sqlite() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return _session_row_to_record(row) if row else None


def list_users(*, tenant_id: str | None = None, limit: int = 100) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 1000))
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        users = list(state.get("identity", {}).get("users", {}).values())
        if tenant_id:
            users = [user for user in users if user.get("tenantId") == tenant_id]
        return sorted(users, key=lambda item: item.get("updatedAt") or "", reverse=True)[:limit]

    query = "SELECT * FROM users WHERE 1=1"
    params: list[Any] = []
    if tenant_id:
        query += " AND tenant_id = ?"
        params.append(tenant_id)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _connect_sqlite() as conn:
        return [_user_row_to_record(row) for row in conn.execute(query, params)]


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
    land_evidence: Dict[str, Any],
    risk_registers: Dict[str, Any],
    project_snapshots: Dict[str, Any],
    user_activity_logs: list[Dict[str, Any]],
    identity: Dict[str, Any] | None = None,
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
        "land_evidence": land_evidence,
        "risk_registers": risk_registers,
        "project_snapshots": project_snapshots,
        "user_activity_logs": user_activity_logs[-MAX_JSON_ACTIVITY_LOGS:],
        "identity": identity or default_state()["identity"],
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
        CREATE TABLE IF NOT EXISTS land_evidence (
            project_id TEXT NOT NULL,
            record_key TEXT NOT NULL,
            evidence_id TEXT,
            evidence_type TEXT,
            pnu TEXT,
            parcel_key TEXT,
            source_name TEXT,
            source_type TEXT,
            source_locator TEXT,
            base_year TEXT,
            base_date TEXT,
            verification_status TEXT,
            created_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, record_key)
        );
        CREATE INDEX IF NOT EXISTS idx_land_evidence_project_pnu
            ON land_evidence(project_id, pnu);
        CREATE INDEX IF NOT EXISTS idx_land_evidence_project_type
            ON land_evidence(project_id, evidence_type);
        CREATE TABLE IF NOT EXISTS risk_registers (
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            register_id TEXT,
            status TEXT,
            risk_count INTEGER,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_risk_registers_project_updated
            ON risk_registers(project_id, updated_at);
        CREATE TABLE IF NOT EXISTS project_snapshots (
            project_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, snapshot_id)
        );
        CREATE TABLE IF NOT EXISTS user_activity_logs (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            user_id TEXT,
            project_id TEXT,
            run_id TEXT,
            method TEXT,
            path TEXT,
            status_code INTEGER,
            duration_ms REAL,
            client_host TEXT,
            user_agent TEXT,
            request_id TEXT,
            metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_user_activity_logs_user_created
            ON user_activity_logs(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_user_activity_logs_project_created
            ON user_activity_logs(project_id, created_at);
        CREATE TABLE IF NOT EXISTS tenants (
            tenant_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            metadata_json TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            email TEXT,
            display_name TEXT,
            role TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_users_tenant
            ON users(tenant_id, updated_at);
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT,
            tenant_id TEXT,
            provider TEXT,
            status TEXT,
            created_at TEXT,
            last_seen_at TEXT,
            expires_at TEXT,
            metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user
            ON sessions(user_id, last_seen_at);
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

        project_snapshots: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, snapshot_id, payload_json FROM project_snapshots ORDER BY project_id, created_at"
        ):
                project_snapshots.setdefault(row["project_id"], {})[row["snapshot_id"]] = json.loads(row["payload_json"])

        land_evidence: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, record_key, payload_json FROM land_evidence ORDER BY project_id, updated_at, record_key"
        ):
            land_evidence.setdefault(row["project_id"], {})[row["record_key"]] = json.loads(row["payload_json"])

        risk_registers: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, run_id, payload_json FROM risk_registers ORDER BY project_id, updated_at"
        ):
            risk_registers.setdefault(row["project_id"], {})[row["run_id"]] = json.loads(row["payload_json"])

        user_activity_logs = [
            _activity_row_to_record(row)
            for row in conn.execute(
                "SELECT * FROM user_activity_logs ORDER BY created_at DESC LIMIT ?",
                (MAX_JSON_ACTIVITY_LOGS,),
            )
        ]
        user_activity_logs.reverse()

        metadata = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM metadata")
        }
        identity = {
            "tenants": {
                row["tenant_id"]: _tenant_row_to_record(row)
                for row in conn.execute("SELECT * FROM tenants")
            },
            "users": {
                row["user_id"]: _user_row_to_record(row)
                for row in conn.execute("SELECT * FROM users")
            },
            "sessions": {
                row["session_id"]: _session_row_to_record(row)
                for row in conn.execute("SELECT * FROM sessions")
            },
        }

    has_identity_records = any(identity.get(key) for key in ("tenants", "users", "sessions"))
    if not projects and not reports and not orchestrations and not risk_registers and not metadata and not has_identity_records:
        return None

    return _normalize_state({
        "version": int(metadata.get("version", STATE_VERSION)),
        "project_id_counter": int(metadata.get("project_id_counter", 1)),
        "projects": projects,
        "reports": reports,
        "orchestrations": orchestrations,
        "market_evidence": market_evidence,
        "land_evidence": land_evidence,
        "risk_registers": risk_registers,
        "project_snapshots": project_snapshots,
        "user_activity_logs": user_activity_logs,
        "identity": identity,
    })


def _save_sqlite_state(
    projects: Dict[str, Any],
    reports: Dict[str, Any],
    orchestrations: Dict[str, Any],
    project_id_counter: int,
    market_evidence: Dict[str, Any],
    risk_registers: Dict[str, Any],
    project_snapshots: Dict[str, Any],
) -> None:
    with _connect_sqlite() as conn:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM metadata")
        conn.execute("DELETE FROM projects")
        conn.execute("DELETE FROM reports")
        conn.execute("DELETE FROM orchestrations")
        conn.execute("DELETE FROM market_evidence")
        conn.execute("DELETE FROM risk_registers")
        conn.execute("DELETE FROM project_snapshots")
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
        for project_id, records in risk_registers.items():
            iterable = records.values() if isinstance(records, dict) else records
            for record in iterable:
                run_id = record.get("runId") or record.get("run_id")
                if not run_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO risk_registers(
                        project_id, run_id, register_id, status, risk_count,
                        updated_at, payload_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        run_id,
                        record.get("registerId") or record.get("register_id") or f"{run_id}:risk-register",
                        record.get("status"),
                        len(record.get("risks") or []),
                        record.get("updatedAt") or record.get("updated_at"),
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
        for project_id, records in project_snapshots.items():
            iterable = records.values() if isinstance(records, dict) else records
            for record in iterable:
                conn.execute(
                    """
                    INSERT INTO project_snapshots(project_id, snapshot_id, run_id, created_at, payload_json)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        record.get("snapshotId") or record.get("snapshot_id"),
                        record.get("runId") or record.get("run_id"),
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
    normalized.setdefault("land_evidence", {})
    normalized.setdefault("risk_registers", {})
    normalized.setdefault("project_snapshots", {})
    normalized.setdefault("user_activity_logs", [])
    normalized.setdefault("identity", default_state()["identity"])
    normalized["identity"].setdefault("tenants", {})
    normalized["identity"].setdefault("users", {})
    normalized["identity"].setdefault("sessions", {})
    return normalized


def _save_json_state_from_state(state: Dict[str, Any]) -> None:
    state = _normalize_state(state)
    _save_json_state(
        state["projects"],
        state["reports"],
        state["orchestrations"],
        state["project_id_counter"],
        state.get("market_evidence", {}),
        state.get("land_evidence", {}),
        state.get("risk_registers", {}),
        state.get("project_snapshots", {}),
        state.get("user_activity_logs", []),
        state.get("identity", default_state()["identity"]),
    )


def _load_existing_user_activity_logs() -> list[Dict[str, Any]]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("user_activity_logs", [])
    path = sqlite_file_path()
    if not path.exists():
        return []
    with _connect_sqlite() as conn:
        logs = [
            _activity_row_to_record(row)
            for row in conn.execute(
                "SELECT * FROM user_activity_logs ORDER BY created_at DESC LIMIT ?",
                (MAX_JSON_ACTIVITY_LOGS,),
            )
        ]
    logs.reverse()
    return logs


def _load_existing_land_evidence_records() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("land_evidence", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        land_evidence: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, record_key, payload_json FROM land_evidence ORDER BY project_id, updated_at, record_key"
        ):
            land_evidence.setdefault(row["project_id"], {})[row["record_key"]] = json.loads(row["payload_json"])
        return land_evidence


def _load_existing_risk_registers() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("risk_registers", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        risk_registers: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, run_id, payload_json FROM risk_registers ORDER BY project_id, updated_at"
        ):
            risk_registers.setdefault(row["project_id"], {})[row["run_id"]] = json.loads(row["payload_json"])
        return risk_registers


def _load_existing_identity_records() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or default_state()).get("identity", default_state()["identity"])
    path = sqlite_file_path()
    if not path.exists():
        return default_state()["identity"]
    with _connect_sqlite() as conn:
        return {
            "tenants": {
                row["tenant_id"]: _tenant_row_to_record(row)
                for row in conn.execute("SELECT * FROM tenants")
            },
            "users": {
                row["user_id"]: _user_row_to_record(row)
                for row in conn.execute("SELECT * FROM users")
            },
            "sessions": {
                row["session_id"]: _session_row_to_record(row)
                for row in conn.execute("SELECT * FROM sessions")
            },
        }


def _filter_user_activity_logs(
    logs: list[Dict[str, Any]],
    *,
    user_id: str | None,
    project_id: str | None,
    event_type: str | None,
) -> list[Dict[str, Any]]:
    filtered = logs
    if user_id:
        filtered = [item for item in filtered if item.get("userId") == user_id]
    if project_id:
        filtered = [item for item in filtered if item.get("projectId") == project_id]
    if event_type:
        filtered = [item for item in filtered if item.get("eventType") == event_type]
    return filtered


def _activity_row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
    metadata = {}
    if row["metadata_json"]:
        try:
            metadata = json.loads(row["metadata_json"])
        except json.JSONDecodeError:
            metadata = {"raw": row["metadata_json"]}
    return {
        "eventId": row["event_id"],
        "eventType": row["event_type"],
        "createdAt": row["created_at"],
        "userId": row["user_id"],
        "projectId": row["project_id"],
        "runId": row["run_id"],
        "method": row["method"],
        "path": row["path"],
        "statusCode": row["status_code"],
        "durationMs": row["duration_ms"],
        "clientHost": row["client_host"],
        "userAgent": row["user_agent"],
        "requestId": row["request_id"],
        "metadata": metadata,
    }


def _json_loads_optional(value: str | None) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}


def _tenant_row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "tenantId": row["tenant_id"],
        "name": row["name"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "metadata": _json_loads_optional(row["metadata_json"]),
    }


def _user_row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "userId": row["user_id"],
        "tenantId": row["tenant_id"],
        "email": row["email"],
        "displayName": row["display_name"],
        "role": row["role"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "metadata": _json_loads_optional(row["metadata_json"]),
    }


def _session_row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "sessionId": row["session_id"],
        "userId": row["user_id"],
        "tenantId": row["tenant_id"],
        "provider": row["provider"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "lastSeenAt": row["last_seen_at"],
        "expiresAt": row["expires_at"],
        "metadata": _json_loads_optional(row["metadata_json"]),
    }
