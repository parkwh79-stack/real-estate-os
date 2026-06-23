import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4


STATE_VERSION = 13
DEFAULT_STATE_FILE = Path(__file__).parent / "data" / "runtime" / "state.json"
DEFAULT_SQLITE_FILE = Path(__file__).parent / "data" / "runtime" / "realytics.sqlite3"
MAX_JSON_ACTIVITY_LOGS = 10000
ASSUMPTION_OVERRIDE_STATUSES = {"active", "revoked", "expired", "superseded"}
GLOSSARY_CANDIDATE_STATUSES = {"pending_review", "approved", "rejected", "needs_revision", "merged"}


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
        "evidence_graphs": {},
        "prompt_artifacts": {},
        "assumption_overrides": {},
        "restructuring_cures": {},
        "glossary_terms": {},
        "glossary_term_candidates": {},
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
            evidence_graphs=legacy_state.get("evidence_graphs", {}),
            prompt_artifacts=legacy_state.get("prompt_artifacts", {}),
            assumption_overrides=legacy_state.get("assumption_overrides", {}),
            restructuring_cures=legacy_state.get("restructuring_cures", {}),
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
    evidence_graphs: Dict[str, Any] | None = None,
    prompt_artifacts: Dict[str, Any] | None = None,
    assumption_overrides: Dict[str, Any] | None = None,
    restructuring_cures: Dict[str, Any] | None = None,
    glossary_term_candidates: Dict[str, Any] | None = None,
) -> None:
    market_evidence = market_evidence or {}
    project_snapshots = project_snapshots or {}
    land_evidence = land_evidence or _load_existing_land_evidence_records()
    risk_registers = risk_registers or _load_existing_risk_registers()
    evidence_graphs = evidence_graphs or _load_existing_evidence_graphs()
    prompt_artifacts = prompt_artifacts or _load_existing_prompt_artifacts()
    assumption_overrides = assumption_overrides or _load_existing_assumption_overrides()
    restructuring_cures = restructuring_cures or _load_existing_restructuring_cures()
    glossary_term_candidates = glossary_term_candidates or _load_existing_glossary_term_candidates()
    if user_activity_logs is None:
        user_activity_logs = _load_existing_user_activity_logs()
    identity = _load_existing_identity_records()
    if storage_backend() == "json":
        _save_json_state(projects, reports, orchestrations, project_id_counter, market_evidence, land_evidence, risk_registers, evidence_graphs, prompt_artifacts, assumption_overrides, restructuring_cures, project_snapshots, user_activity_logs, identity, glossary_term_candidates=glossary_term_candidates)
        return
    _save_sqlite_state(projects, reports, orchestrations, project_id_counter, market_evidence, risk_registers, evidence_graphs, prompt_artifacts, assumption_overrides, restructuring_cures, project_snapshots)


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
            state.get("evidence_graphs", {}),
            state.get("prompt_artifacts", {}),
            state.get("assumption_overrides", {}),
            state.get("restructuring_cures", {}),
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


def upsert_evidence_graph_record(
    project_id: str,
    run_id: str,
    evidence_graph: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist an Evidence Graph artifact as a first-class audit graph record."""
    now = datetime.now(timezone.utc).isoformat()
    graph = evidence_graph or {}
    summary = graph.get("summary") if isinstance(graph.get("summary"), dict) else {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    normalized = {
        **graph,
        "schemaVersion": graph.get("schemaVersion") or "realytics-evidence-graph-v1",
        "projectId": project_id,
        "runId": run_id,
        "graphId": graph.get("graphId") or graph.get("graph_id") or f"{run_id}:evidence-graph",
        "updatedAt": now,
    }
    normalized.setdefault("nodes", nodes)
    normalized.setdefault("edges", edges)
    normalized.setdefault("summary", summary)
    normalized["nodeCount"] = int(summary.get("nodeCount") or len(nodes))
    normalized["edgeCount"] = int(summary.get("edgeCount") or len(edges))
    normalized["riskNodeCount"] = int(summary.get("riskNodeCount") or len([node for node in nodes if node.get("type") == "risk"]))
    normalized["sourceLocatorCount"] = int(summary.get("sourceLocatorCount") or len([node for node in nodes if node.get("type") == "source_locator"]))

    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("evidence_graphs", {})
        state["evidence_graphs"].setdefault(project_id, {})[run_id] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO evidence_graphs(
                project_id, run_id, graph_id, node_count, edge_count,
                risk_node_count, source_locator_count, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, run_id) DO UPDATE SET
                graph_id=excluded.graph_id,
                node_count=excluded.node_count,
                edge_count=excluded.edge_count,
                risk_node_count=excluded.risk_node_count,
                source_locator_count=excluded.source_locator_count,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                run_id,
                normalized["graphId"],
                normalized["nodeCount"],
                normalized["edgeCount"],
                normalized["riskNodeCount"],
                normalized["sourceLocatorCount"],
                normalized["updatedAt"],
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def get_evidence_graph_record(project_id: str, run_id: str) -> Dict[str, Any] | None:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        return state.get("evidence_graphs", {}).get(project_id, {}).get(run_id)
    with _connect_sqlite() as conn:
        row = conn.execute(
            "SELECT payload_json FROM evidence_graphs WHERE project_id = ? AND run_id = ?",
            (project_id, run_id),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_evidence_graph_records(project_id: str) -> list[Dict[str, Any]]:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("evidence_graphs", {}).get(project_id, {}).values())
    else:
        with _connect_sqlite() as conn:
            records = [
                json.loads(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM evidence_graphs WHERE project_id = ? ORDER BY updated_at DESC",
                    (project_id,),
                )
            ]
    return sorted(records, key=lambda item: item.get("updatedAt") or "", reverse=True)


def upsert_prompt_artifact_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a generated AI work-order or prompt draft as an auditable artifact."""
    now = datetime.now(timezone.utc).isoformat()
    artifact_type = record.get("artifactType") or record.get("artifact_type") or "ai_work_order"
    workflow = record.get("workflow") or record.get("recommendedWorkflow") or record.get("recommended_workflow")
    artifact_id = record.get("artifactId") or record.get("artifact_id") or f"{artifact_type}:{workflow or 'general'}:{uuid4()}"
    normalized = {
        **record,
        "schemaVersion": record.get("schemaVersion") or "realytics-prompt-artifact-v1",
        "projectId": project_id,
        "artifactId": str(artifact_id),
        "artifactType": artifact_type,
        "workflow": workflow,
        "title": record.get("title") or "AI 업무 주문서",
        "body": record.get("body") or record.get("prompt") or "",
        "createdBy": record.get("createdBy") or record.get("created_by") or "system",
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }

    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("prompt_artifacts", {})
        state["prompt_artifacts"].setdefault(project_id, {})[normalized["artifactId"]] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO prompt_artifacts(
                project_id, artifact_id, artifact_type, workflow,
                created_by, created_at, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, artifact_id) DO UPDATE SET
                artifact_type=excluded.artifact_type,
                workflow=excluded.workflow,
                created_by=excluded.created_by,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                normalized["artifactId"],
                normalized["artifactType"],
                normalized.get("workflow"),
                normalized.get("createdBy"),
                normalized.get("createdAt"),
                normalized.get("updatedAt"),
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def get_prompt_artifact_record(project_id: str, artifact_id: str) -> Dict[str, Any] | None:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        return state.get("prompt_artifacts", {}).get(project_id, {}).get(artifact_id)
    with _connect_sqlite() as conn:
        row = conn.execute(
            "SELECT payload_json FROM prompt_artifacts WHERE project_id = ? AND artifact_id = ?",
            (project_id, artifact_id),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_prompt_artifact_records(
    project_id: str,
    *,
    artifact_type: str | None = None,
    workflow: str | None = None,
) -> list[Dict[str, Any]]:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("prompt_artifacts", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM prompt_artifacts WHERE project_id = ?"
        params: list[Any] = [project_id]
        if artifact_type:
            query += " AND artifact_type = ?"
            params.append(artifact_type)
        if workflow:
            query += " AND workflow = ?"
            params.append(workflow)
        query += " ORDER BY updated_at DESC, artifact_id"
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if artifact_type:
        records = [record for record in records if record.get("artifactType") == artifact_type]
    if workflow:
        records = [record for record in records if record.get("workflow") == workflow]
    return sorted(records, key=lambda item: item.get("updatedAt") or "", reverse=True)


def upsert_assumption_override_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a human-approved canonical assumption override for a data-quality conflict."""
    now = datetime.now(timezone.utc).isoformat()
    check_key = record.get("checkKey") or record.get("check_key") or record.get("dataQualityCheckKey")
    canonical_field = record.get("canonicalField") or record.get("canonical_field") or record.get("field")
    if not check_key:
        raise ValueError("checkKey is required")
    if not canonical_field:
        raise ValueError("canonicalField is required")
    status = record.get("status") or "active"
    if status not in ASSUMPTION_OVERRIDE_STATUSES:
        raise ValueError("status must be active, revoked, expired, or superseded")
    override_id = record.get("overrideId") or record.get("override_id") or f"{check_key}:{canonical_field}:{uuid4()}"
    normalized = {
        **record,
        "overrideId": str(override_id),
        "projectId": project_id,
        "checkKey": str(check_key),
        "canonicalField": str(canonical_field),
        "approvedValue": record.get("approvedValue") if "approvedValue" in record else record.get("approved_value"),
        "unit": record.get("unit"),
        "status": status,
        "approvedBy": record.get("approvedBy") or record.get("approved_by") or "system",
        "approvedAt": record.get("approvedAt") or record.get("approved_at") or now,
        "reason": record.get("reason") or record.get("comment") or record.get("reviewComment") or "",
        "sourceLocators": record.get("sourceLocators") or record.get("source_locators") or [],
        "evidenceIds": record.get("evidenceIds") or record.get("evidence_ids") or [],
        "appliesToRunId": record.get("appliesToRunId") or record.get("applies_to_run_id"),
        "statusReason": record.get("statusReason") or record.get("status_reason") or record.get("revokeReason") or record.get("expireReason") or "",
        "statusChangedBy": record.get("statusChangedBy") or record.get("status_changed_by") or record.get("approvedBy") or record.get("approved_by") or "system",
        "statusChangedAt": record.get("statusChangedAt") or record.get("status_changed_at") or now,
        "expiresAt": record.get("expiresAt") or record.get("expires_at"),
        "supersededByOverrideId": record.get("supersededByOverrideId") or record.get("superseded_by_override_id"),
        "supersededAt": record.get("supersededAt") or record.get("superseded_at"),
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("assumption_overrides", {})
        project_records = state["assumption_overrides"].setdefault(project_id, {})
        _supersede_existing_active_assumption_overrides(project_records, normalized, now)
        project_records[normalized["overrideId"]] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        _supersede_sqlite_active_assumption_overrides(conn, project_id, normalized, now)
        conn.execute(
            """
            INSERT INTO assumption_overrides(
                project_id, override_id, check_key, canonical_field, status,
                approved_by, approved_at, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, override_id) DO UPDATE SET
                check_key=excluded.check_key,
                canonical_field=excluded.canonical_field,
                status=excluded.status,
                approved_by=excluded.approved_by,
                approved_at=excluded.approved_at,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                normalized["overrideId"],
                normalized["checkKey"],
                normalized["canonicalField"],
                normalized["status"],
                normalized["approvedBy"],
                normalized["approvedAt"],
                normalized["updatedAt"],
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def list_assumption_override_records(
    project_id: str,
    *,
    status: str | None = None,
    check_key: str | None = None,
) -> list[Dict[str, Any]]:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("assumption_overrides", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM assumption_overrides WHERE project_id = ?"
        params: list[Any] = [project_id]
        if check_key:
            query += " AND check_key = ?"
            params.append(check_key)
        query += " ORDER BY updated_at DESC, override_id"
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if check_key:
        records = [record for record in records if record.get("checkKey") == check_key]
    records = [_with_effective_assumption_override_status(record) for record in records]
    if status:
        records = [record for record in records if record.get("effectiveStatus") == status]
    return sorted(records, key=lambda item: item.get("updatedAt") or "", reverse=True)


def _with_effective_assumption_override_status(record: Dict[str, Any]) -> Dict[str, Any]:
    effective = record.get("status") or "active"
    expires_at = record.get("expiresAt") or record.get("expires_at")
    if effective == "active" and expires_at and _iso_datetime_is_past(str(expires_at)):
        effective = "expired"
    if record.get("effectiveStatus") == effective:
        return record
    return {**record, "effectiveStatus": effective}


def _iso_datetime_is_past(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= datetime.now(timezone.utc)
    except ValueError:
        return False


def _supersede_existing_active_assumption_overrides(
    records: Dict[str, Any],
    new_record: Dict[str, Any],
    now: str,
) -> None:
    if new_record.get("status") != "active":
        return
    for override_id, existing in list(records.items()):
        if override_id == new_record["overrideId"]:
            continue
        if existing.get("status", "active") != "active":
            continue
        if existing.get("checkKey") != new_record.get("checkKey"):
            continue
        if existing.get("canonicalField") != new_record.get("canonicalField"):
            continue
        records[override_id] = {
            **existing,
            "status": "superseded",
            "effectiveStatus": "superseded",
            "supersededByOverrideId": new_record["overrideId"],
            "supersededAt": now,
            "statusChangedBy": new_record.get("approvedBy") or "system",
            "statusChangedAt": now,
            "statusReason": f"Superseded by {new_record['overrideId']}.",
            "updatedAt": now,
        }


def _supersede_sqlite_active_assumption_overrides(
    conn: sqlite3.Connection,
    project_id: str,
    new_record: Dict[str, Any],
    now: str,
) -> None:
    if new_record.get("status") != "active":
        return
    rows = conn.execute(
        """
        SELECT override_id, payload_json
        FROM assumption_overrides
        WHERE project_id = ?
          AND status = 'active'
          AND check_key = ?
          AND canonical_field = ?
          AND override_id <> ?
        """,
        (
            project_id,
            new_record.get("checkKey"),
            new_record.get("canonicalField"),
            new_record.get("overrideId"),
        ),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        payload.update({
            "status": "superseded",
            "effectiveStatus": "superseded",
            "supersededByOverrideId": new_record["overrideId"],
            "supersededAt": now,
            "statusChangedBy": new_record.get("approvedBy") or "system",
            "statusChangedAt": now,
            "statusReason": f"Superseded by {new_record['overrideId']}.",
            "updatedAt": now,
        })
        conn.execute(
            """
            UPDATE assumption_overrides
            SET status = ?, updated_at = ?, payload_json = ?
            WHERE project_id = ? AND override_id = ?
            """,
            ("superseded", now, json.dumps(payload, ensure_ascii=False), project_id, row["override_id"]),
        )


def upsert_restructuring_cure_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a reviewer-selected PF restructuring cure for scenario blockers."""
    now = datetime.now(timezone.utc).isoformat()
    cure_type = record.get("cureType") or record.get("cure_type") or record.get("type")
    if not cure_type:
        raise ValueError("cureType is required")
    status = record.get("status") or "active"
    if status not in {"active", "revoked", "expired", "superseded"}:
        raise ValueError("status must be active, revoked, expired, or superseded")
    cure_id = record.get("cureId") or record.get("cure_id") or f"{cure_type}:{uuid4()}"
    normalized = {
        **record,
        "cureId": str(cure_id),
        "projectId": project_id,
        "cureType": str(cure_type),
        "status": status,
        "riskCodes": record.get("riskCodes") or record.get("risk_codes") or [],
        "amount": record.get("amount"),
        "amountUnit": record.get("amountUnit") or record.get("amount_unit") or "KRW",
        "targetMetric": record.get("targetMetric") or record.get("target_metric"),
        "targetValue": record.get("targetValue") if "targetValue" in record else record.get("target_value"),
        "description": record.get("description") or record.get("memo") or record.get("reviewComment") or "",
        "sourceName": record.get("sourceName") or record.get("source_name") or "manual_restructuring_cure",
        "sourceType": record.get("sourceType") or record.get("source_type") or "manual_upload",
        "sourceUrlOrEvidenceId": record.get("sourceUrlOrEvidenceId") or record.get("source_url_or_evidence_id") or str(cure_id),
        "sourceLocators": record.get("sourceLocators") or record.get("source_locators") or [],
        "evidenceIds": record.get("evidenceIds") or record.get("evidence_ids") or [],
        "reviewedBy": record.get("reviewedBy") or record.get("reviewed_by") or "credit_officer",
        "reviewedAt": record.get("reviewedAt") or record.get("reviewed_at") or now,
        "appliesToRunId": record.get("appliesToRunId") or record.get("applies_to_run_id"),
        "expiresAt": record.get("expiresAt") or record.get("expires_at"),
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("restructuring_cures", {})
        state["restructuring_cures"].setdefault(project_id, {})[normalized["cureId"]] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO restructuring_cures(
                project_id, cure_id, cure_type, status, reviewed_by,
                reviewed_at, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, cure_id) DO UPDATE SET
                cure_type=excluded.cure_type,
                status=excluded.status,
                reviewed_by=excluded.reviewed_by,
                reviewed_at=excluded.reviewed_at,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                normalized["cureId"],
                normalized["cureType"],
                normalized["status"],
                normalized["reviewedBy"],
                normalized["reviewedAt"],
                normalized["updatedAt"],
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def list_restructuring_cure_records(
    project_id: str,
    *,
    status: str | None = None,
    cure_type: str | None = None,
) -> list[Dict[str, Any]]:
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("restructuring_cures", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM restructuring_cures WHERE project_id = ?"
        params: list[Any] = [project_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        if cure_type:
            query += " AND cure_type = ?"
            params.append(cure_type)
        query += " ORDER BY updated_at DESC, cure_id"
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if status:
        records = [record for record in records if record.get("status") == status]
    if cure_type:
        records = [record for record in records if record.get("cureType") == cure_type]
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
    evidence_graphs: Dict[str, Any],
    prompt_artifacts: Dict[str, Any],
    assumption_overrides: Dict[str, Any],
    restructuring_cures: Dict[str, Any],
    project_snapshots: Dict[str, Any],
    user_activity_logs: list[Dict[str, Any]],
    identity: Dict[str, Any] | None = None,
    glossary_terms: Dict[str, Any] | None = None,
    glossary_term_candidates: Dict[str, Any] | None = None,
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
        "evidence_graphs": evidence_graphs,
        "prompt_artifacts": prompt_artifacts,
        "assumption_overrides": assumption_overrides,
        "restructuring_cures": restructuring_cures,
        "glossary_terms": glossary_terms or _load_existing_glossary_terms(),
        "glossary_term_candidates": glossary_term_candidates or _load_existing_glossary_term_candidates(),
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
        CREATE TABLE IF NOT EXISTS evidence_graphs (
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            graph_id TEXT,
            node_count INTEGER,
            edge_count INTEGER,
            risk_node_count INTEGER,
            source_locator_count INTEGER,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_evidence_graphs_project_updated
            ON evidence_graphs(project_id, updated_at);
        CREATE TABLE IF NOT EXISTS prompt_artifacts (
            project_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_type TEXT,
            workflow TEXT,
            created_by TEXT,
            created_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, artifact_id)
        );
        CREATE INDEX IF NOT EXISTS idx_prompt_artifacts_project_updated
            ON prompt_artifacts(project_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_prompt_artifacts_project_workflow
            ON prompt_artifacts(project_id, workflow);
        CREATE TABLE IF NOT EXISTS assumption_overrides (
            project_id TEXT NOT NULL,
            override_id TEXT NOT NULL,
            check_key TEXT,
            canonical_field TEXT,
            status TEXT,
            approved_by TEXT,
            approved_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, override_id)
        );
        CREATE INDEX IF NOT EXISTS idx_assumption_overrides_project_status
            ON assumption_overrides(project_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_assumption_overrides_project_check
            ON assumption_overrides(project_id, check_key);
        CREATE TABLE IF NOT EXISTS restructuring_cures (
            project_id TEXT NOT NULL,
            cure_id TEXT NOT NULL,
            cure_type TEXT,
            status TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, cure_id)
        );
        CREATE INDEX IF NOT EXISTS idx_restructuring_cures_project_status
            ON restructuring_cures(project_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_restructuring_cures_project_type
            ON restructuring_cures(project_id, cure_type);
        CREATE TABLE IF NOT EXISTS glossary_terms (
            term_id TEXT PRIMARY KEY,
            canonical_field TEXT,
            review_status TEXT,
            category TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_glossary_terms_canonical_field
            ON glossary_terms(canonical_field, review_status);
        CREATE INDEX IF NOT EXISTS idx_glossary_terms_category
            ON glossary_terms(category, review_status);
        CREATE TABLE IF NOT EXISTS glossary_term_candidates (
            candidate_id TEXT PRIMARY KEY,
            status TEXT,
            surface TEXT,
            normalized_surface TEXT,
            canonical_field TEXT,
            category TEXT,
            project_id TEXT,
            source_field TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_glossary_term_candidates_status
            ON glossary_term_candidates(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_glossary_term_candidates_surface
            ON glossary_term_candidates(normalized_surface, status);
        CREATE INDEX IF NOT EXISTS idx_glossary_term_candidates_project
            ON glossary_term_candidates(project_id, status);
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

        evidence_graphs: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, run_id, payload_json FROM evidence_graphs ORDER BY project_id, updated_at"
        ):
            evidence_graphs.setdefault(row["project_id"], {})[row["run_id"]] = json.loads(row["payload_json"])

        prompt_artifacts: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, artifact_id, payload_json FROM prompt_artifacts ORDER BY project_id, updated_at, artifact_id"
        ):
            prompt_artifacts.setdefault(row["project_id"], {})[row["artifact_id"]] = json.loads(row["payload_json"])

        assumption_overrides: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, override_id, payload_json FROM assumption_overrides ORDER BY project_id, updated_at, override_id"
        ):
            assumption_overrides.setdefault(row["project_id"], {})[row["override_id"]] = json.loads(row["payload_json"])

        restructuring_cures: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, cure_id, payload_json FROM restructuring_cures ORDER BY project_id, updated_at, cure_id"
        ):
            restructuring_cures.setdefault(row["project_id"], {})[row["cure_id"]] = json.loads(row["payload_json"])

        glossary_terms = {
            row["term_id"]: json.loads(row["payload_json"])
            for row in conn.execute("SELECT term_id, payload_json FROM glossary_terms ORDER BY updated_at, term_id")
        }
        glossary_term_candidates = {
            row["candidate_id"]: json.loads(row["payload_json"])
            for row in conn.execute("SELECT candidate_id, payload_json FROM glossary_term_candidates ORDER BY updated_at, candidate_id")
        }

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
    if not projects and not reports and not orchestrations and not risk_registers and not evidence_graphs and not prompt_artifacts and not assumption_overrides and not restructuring_cures and not glossary_terms and not glossary_term_candidates and not metadata and not has_identity_records:
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
        "evidence_graphs": evidence_graphs,
        "prompt_artifacts": prompt_artifacts,
        "assumption_overrides": assumption_overrides,
        "restructuring_cures": restructuring_cures,
        "glossary_terms": glossary_terms,
        "glossary_term_candidates": glossary_term_candidates,
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
    evidence_graphs: Dict[str, Any],
    prompt_artifacts: Dict[str, Any],
    assumption_overrides: Dict[str, Any],
    restructuring_cures: Dict[str, Any],
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
        conn.execute("DELETE FROM evidence_graphs")
        conn.execute("DELETE FROM prompt_artifacts")
        conn.execute("DELETE FROM assumption_overrides")
        conn.execute("DELETE FROM restructuring_cures")
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
        for project_id, records in evidence_graphs.items():
            iterable = records.values() if isinstance(records, dict) else records
            for record in iterable:
                run_id = record.get("runId") or record.get("run_id")
                if not run_id:
                    continue
                summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
                nodes = record.get("nodes") if isinstance(record.get("nodes"), list) else []
                edges = record.get("edges") if isinstance(record.get("edges"), list) else []
                conn.execute(
                    """
                    INSERT INTO evidence_graphs(
                        project_id, run_id, graph_id, node_count, edge_count,
                        risk_node_count, source_locator_count, updated_at, payload_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        run_id,
                        record.get("graphId") or record.get("graph_id") or f"{run_id}:evidence-graph",
                        int(record.get("nodeCount") or summary.get("nodeCount") or len(nodes)),
                        int(record.get("edgeCount") or summary.get("edgeCount") or len(edges)),
                        int(record.get("riskNodeCount") or summary.get("riskNodeCount") or len([node for node in nodes if node.get("type") == "risk"])),
                        int(record.get("sourceLocatorCount") or summary.get("sourceLocatorCount") or len([node for node in nodes if node.get("type") == "source_locator"])),
                        record.get("updatedAt") or record.get("updated_at"),
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
        for project_id, records in prompt_artifacts.items():
            iterable = records.values() if isinstance(records, dict) else records
            for record in iterable:
                artifact_id = record.get("artifactId") or record.get("artifact_id")
                if not artifact_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO prompt_artifacts(
                        project_id, artifact_id, artifact_type, workflow,
                        created_by, created_at, updated_at, payload_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        artifact_id,
                        record.get("artifactType") or record.get("artifact_type"),
                        record.get("workflow"),
                        record.get("createdBy") or record.get("created_by"),
                        record.get("createdAt") or record.get("created_at"),
                        record.get("updatedAt") or record.get("updated_at"),
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
        for project_id, records in assumption_overrides.items():
            iterable = records.values() if isinstance(records, dict) else records
            for record in iterable:
                override_id = record.get("overrideId") or record.get("override_id")
                if not override_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO assumption_overrides(
                        project_id, override_id, check_key, canonical_field, status,
                        approved_by, approved_at, updated_at, payload_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        override_id,
                        record.get("checkKey") or record.get("check_key"),
                        record.get("canonicalField") or record.get("canonical_field"),
                        record.get("status"),
                        record.get("approvedBy") or record.get("approved_by"),
                        record.get("approvedAt") or record.get("approved_at"),
                        record.get("updatedAt") or record.get("updated_at"),
                        json.dumps(record, ensure_ascii=False),
                    ),
                )
        for project_id, records in restructuring_cures.items():
            iterable = records.values() if isinstance(records, dict) else records
            for record in iterable:
                cure_id = record.get("cureId") or record.get("cure_id")
                if not cure_id:
                    continue
                conn.execute(
                    """
                    INSERT INTO restructuring_cures(
                        project_id, cure_id, cure_type, status, reviewed_by,
                        reviewed_at, updated_at, payload_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        cure_id,
                        record.get("cureType") or record.get("cure_type"),
                        record.get("status"),
                        record.get("reviewedBy") or record.get("reviewed_by"),
                        record.get("reviewedAt") or record.get("reviewed_at"),
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
    normalized.setdefault("evidence_graphs", {})
    normalized.setdefault("prompt_artifacts", {})
    normalized.setdefault("assumption_overrides", {})
    normalized.setdefault("restructuring_cures", {})
    normalized.setdefault("glossary_terms", {})
    normalized.setdefault("glossary_term_candidates", {})
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
        state.get("evidence_graphs", {}),
        state.get("prompt_artifacts", {}),
        state.get("assumption_overrides", {}),
        state.get("restructuring_cures", {}),
        state.get("project_snapshots", {}),
        state.get("user_activity_logs", []),
        state.get("identity", default_state()["identity"]),
        state.get("glossary_terms", {}),
        state.get("glossary_term_candidates", {}),
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


def _load_existing_evidence_graphs() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("evidence_graphs", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        evidence_graphs: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, run_id, payload_json FROM evidence_graphs ORDER BY project_id, updated_at"
        ):
            evidence_graphs.setdefault(row["project_id"], {})[row["run_id"]] = json.loads(row["payload_json"])
        return evidence_graphs


def _load_existing_prompt_artifacts() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("prompt_artifacts", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        prompt_artifacts: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, artifact_id, payload_json FROM prompt_artifacts ORDER BY project_id, updated_at, artifact_id"
        ):
            prompt_artifacts.setdefault(row["project_id"], {})[row["artifact_id"]] = json.loads(row["payload_json"])
        return prompt_artifacts


def _load_existing_assumption_overrides() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("assumption_overrides", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        assumption_overrides: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, override_id, payload_json FROM assumption_overrides ORDER BY project_id, updated_at, override_id"
        ):
            assumption_overrides.setdefault(row["project_id"], {})[row["override_id"]] = json.loads(row["payload_json"])
        return assumption_overrides


def _load_existing_restructuring_cures() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("restructuring_cures", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        restructuring_cures: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, cure_id, payload_json FROM restructuring_cures ORDER BY project_id, updated_at, cure_id"
        ):
            restructuring_cures.setdefault(row["project_id"], {})[row["cure_id"]] = json.loads(row["payload_json"])
        return restructuring_cures


def _load_existing_glossary_term_candidates() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("glossary_term_candidates", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        return {
            row["candidate_id"]: json.loads(row["payload_json"])
            for row in conn.execute(
                "SELECT candidate_id, payload_json FROM glossary_term_candidates ORDER BY updated_at, candidate_id"
            )
        }


def list_glossary_term_records(
    *,
    query: str | None = None,
    category: str | None = None,
    canonical_field: str | None = None,
    review_status: str | None = None,
) -> list[Dict[str, Any]]:
    terms = list(_load_existing_glossary_terms().values())
    normalized_query = _normalize_glossary_search_text(query)
    filtered: list[Dict[str, Any]] = []
    for term in terms:
        if category and term.get("category") != category:
            continue
        if canonical_field and term.get("canonicalField") != canonical_field:
            continue
        if review_status and term.get("reviewStatus") != review_status:
            continue
        if normalized_query:
            haystack = " ".join(
                [
                    str(term.get("termId") or ""),
                    str(term.get("preferredTerm") or ""),
                    str(term.get("displayNameKo") or ""),
                    str(term.get("displayNameEn") or ""),
                    " ".join(str(alias) for alias in term.get("aliases") or []),
                    str(term.get("definitionKo") or ""),
                    str(term.get("canonicalField") or ""),
                ]
            )
            if normalized_query not in _normalize_glossary_search_text(haystack):
                continue
        filtered.append(term)
    return sorted(filtered, key=lambda item: (item.get("category") or "", item.get("displayNameKo") or item.get("termId") or ""))


def list_glossary_term_candidate_records(
    *,
    query: str | None = None,
    status: str | None = None,
    project_id: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 500))
    candidates = list(_load_existing_glossary_term_candidates().values())
    normalized_query = _normalize_glossary_search_text(query)
    filtered: list[Dict[str, Any]] = []
    for candidate in candidates:
        if status and candidate.get("status") != status:
            continue
        if project_id and candidate.get("projectId") != project_id:
            continue
        if category and candidate.get("category") != category:
            continue
        if normalized_query:
            haystack = " ".join(
                [
                    str(candidate.get("surface") or ""),
                    str(candidate.get("sourceField") or ""),
                    str(candidate.get("suggestedCanonicalField") or ""),
                    str(candidate.get("reviewReason") or ""),
                ]
            )
            if normalized_query not in _normalize_glossary_search_text(haystack):
                continue
        filtered.append(candidate)
    return sorted(
        filtered,
        key=lambda item: (item.get("status") or "", item.get("lastSeenAt") or item.get("updatedAt") or ""),
        reverse=True,
    )[:limit]


def get_glossary_term_candidate_record(candidate_id: str) -> Dict[str, Any] | None:
    if not candidate_id:
        return None
    return _load_existing_glossary_term_candidates().get(candidate_id)


def upsert_glossary_term_candidate_record(record: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    surface = str(record.get("surface") or "").strip()
    if not surface:
        raise ValueError("surface is required")
    normalized_surface = _normalize_glossary_search_text(surface)
    source_field = str(record.get("sourceField") or record.get("source_field") or "").strip()
    project_id = record.get("projectId") or record.get("project_id")
    suggested_canonical = record.get("suggestedCanonicalField") or record.get("canonicalField") or record.get("canonical_field")
    candidate_id = record.get("candidateId") or record.get("candidate_id") or _glossary_candidate_id(
        surface=surface,
        source_field=source_field,
        project_id=project_id,
        suggested_canonical=suggested_canonical,
    )
    existing = get_glossary_term_candidate_record(candidate_id)
    incoming_status = record.get("status")
    existing_status = (existing or {}).get("status")
    if existing_status in {"approved", "rejected", "merged"} and incoming_status == "pending_review":
        status = existing_status
    else:
        status = incoming_status or existing_status or "pending_review"
    if status not in GLOSSARY_CANDIDATE_STATUSES:
        raise ValueError(f"status must be one of {sorted(GLOSSARY_CANDIDATE_STATUSES)}")

    record_occurrence = record.get("recordOccurrence", True)
    occurrence = {
        "at": record.get("lastSeenAt") or record.get("last_seen_at") or now,
        "projectId": project_id,
        "runId": record.get("runId") or record.get("run_id"),
        "sourceField": source_field,
        "sourceLocator": record.get("sourceLocator") or record.get("source_locator") or {},
        "confidence": record.get("confidence"),
        "surface": surface,
    }
    occurrences = list((existing or {}).get("occurrences") or [])
    if record_occurrence and occurrence not in occurrences:
        occurrences.append(occurrence)
    occurrences = occurrences[-20:]

    normalized = {
        **(existing or {}),
        **record,
        "schemaVersion": record.get("schemaVersion") or "realytics-glossary-term-candidate-v1",
        "candidateId": candidate_id,
        "surface": surface,
        "normalizedSurface": normalized_surface,
        "status": status,
        "projectId": project_id,
        "runId": record.get("runId") or record.get("run_id") or (existing or {}).get("runId"),
        "sourceField": source_field,
        "sourceLocator": record.get("sourceLocator") or record.get("source_locator") or (existing or {}).get("sourceLocator") or {},
        "suggestedTermId": record.get("suggestedTermId") or record.get("suggested_term_id") or (existing or {}).get("suggestedTermId"),
        "suggestedCanonicalField": suggested_canonical or (existing or {}).get("suggestedCanonicalField"),
        "category": record.get("category") or (existing or {}).get("category") or "unclassified",
        "confidence": record.get("confidence") if record.get("confidence") is not None else (existing or {}).get("confidence"),
        "occurrenceCount": int((existing or {}).get("occurrenceCount") or 0) + (1 if record_occurrence else 0),
        "occurrences": occurrences,
        "firstSeenAt": (existing or {}).get("firstSeenAt") or now,
        "lastSeenAt": occurrence["at"],
        "createdAt": (existing or {}).get("createdAt") or now,
        "updatedAt": now,
        "reviewHistory": list((existing or {}).get("reviewHistory") or []),
    }
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("glossary_term_candidates", {})
        state["glossary_term_candidates"][candidate_id] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO glossary_term_candidates(
                candidate_id, status, surface, normalized_surface, canonical_field,
                category, project_id, source_field, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                status = excluded.status,
                surface = excluded.surface,
                normalized_surface = excluded.normalized_surface,
                canonical_field = excluded.canonical_field,
                category = excluded.category,
                project_id = excluded.project_id,
                source_field = excluded.source_field,
                updated_at = excluded.updated_at,
                payload_json = excluded.payload_json
            """,
            (
                candidate_id,
                normalized.get("status"),
                normalized.get("surface"),
                normalized.get("normalizedSurface"),
                normalized.get("suggestedCanonicalField"),
                normalized.get("category"),
                normalized.get("projectId"),
                normalized.get("sourceField"),
                normalized.get("updatedAt"),
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def update_glossary_term_candidate_status_record(
    candidate_id: str,
    *,
    status: str,
    actor: str = "anonymous",
    reason: str | None = None,
    term_id: str | None = None,
    canonical_field: str | None = None,
) -> Dict[str, Any]:
    current = get_glossary_term_candidate_record(candidate_id)
    if not current:
        raise KeyError("glossary term candidate not found")
    if status not in GLOSSARY_CANDIDATE_STATUSES:
        raise ValueError(f"status must be one of {sorted(GLOSSARY_CANDIDATE_STATUSES)}")
    now = datetime.now(timezone.utc).isoformat()
    history = list(current.get("reviewHistory") or [])
    history.append({
        "at": now,
        "by": actor,
        "status": status,
        "reason": reason,
        "termId": term_id,
        "canonicalField": canonical_field,
    })
    updated = {
        **current,
        "recordOccurrence": False,
        "status": status,
        "reviewedBy": actor,
        "reviewedAt": now,
        "reviewReason": reason,
        "approvedTermId": term_id or current.get("approvedTermId"),
        "approvedCanonicalField": canonical_field or current.get("approvedCanonicalField"),
        "reviewHistory": history,
        "updatedAt": now,
    }
    return upsert_glossary_term_candidate_record(updated)


def get_glossary_term_record(term_id: str) -> Dict[str, Any] | None:
    if not term_id:
        return None
    return _load_existing_glossary_terms().get(term_id)


def upsert_glossary_term_record(record: Dict[str, Any]) -> Dict[str, Any]:
    term_id = record.get("termId") or record.get("term_id")
    if not term_id:
        raise ValueError("termId is required")
    record = dict(record)
    record["termId"] = term_id
    record["updatedAt"] = record.get("updatedAt") or datetime.now(timezone.utc).isoformat()
    record["createdAt"] = record.get("createdAt") or record["updatedAt"]
    record["version"] = int(record.get("version") or 1)

    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("glossary_terms", {})
        state["glossary_terms"][term_id] = record
        _save_json_state_from_state(state)
        return record

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO glossary_terms(term_id, canonical_field, review_status, category, updated_at, payload_json)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(term_id) DO UPDATE SET
                canonical_field = excluded.canonical_field,
                review_status = excluded.review_status,
                category = excluded.category,
                updated_at = excluded.updated_at,
                payload_json = excluded.payload_json
            """,
            (
                term_id,
                record.get("canonicalField") or record.get("canonical_field"),
                record.get("reviewStatus") or record.get("review_status"),
                record.get("category"),
                record.get("updatedAt"),
                json.dumps(record, ensure_ascii=False),
            ),
        )
        conn.commit()
    return record


def _load_existing_glossary_terms() -> Dict[str, Any]:
    if storage_backend() == "json":
        state = _load_json_state()
        return (state or {}).get("glossary_terms", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        return {
            row["term_id"]: json.loads(row["payload_json"])
            for row in conn.execute("SELECT term_id, payload_json FROM glossary_terms ORDER BY updated_at, term_id")
        }


def _normalize_glossary_search_text(value: str | None) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _glossary_candidate_id(
    *,
    surface: str,
    source_field: str | None = None,
    project_id: str | None = None,
    suggested_canonical: str | None = None,
) -> str:
    fingerprint = "|".join(
        [
            _normalize_glossary_search_text(project_id),
            _normalize_glossary_search_text(surface),
            _normalize_glossary_search_text(source_field),
            _normalize_glossary_search_text(suggested_canonical),
        ]
    )
    digest = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]
    return f"gtc_{digest}"


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
