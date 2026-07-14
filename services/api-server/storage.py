import base64
import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

from core.text_normalization import normalize_match_text


STATE_VERSION = 16
DEFAULT_STATE_FILE = Path(__file__).parent / "data" / "runtime" / "state.json"
DEFAULT_SQLITE_FILE = Path(__file__).parent / "data" / "runtime" / "realytics.sqlite3"
MAX_JSON_ACTIVITY_LOGS = 10000
ASSUMPTION_OVERRIDE_STATUSES = {"active", "revoked", "expired", "superseded"}
GLOSSARY_CANDIDATE_STATUSES = {"pending_review", "approved", "rejected", "needs_revision", "merged", "free_text"}
_AGENT_RUNTIME_LOCK = threading.RLock()

_OPERATIONAL_RECORD_SPECS: Dict[str, Dict[str, Any]] = {
    "runtime_outbox_events": {
        "store": "runtime_outbox_events",
        "key": "event_id",
        "aliases": {
            "event_id": "eventId", "tenant_id": "tenantId", "project_id": "projectId",
            "aggregate_type": "aggregateType", "aggregate_id": "aggregateId",
            "event_type": "eventType", "idempotency_key": "idempotencyKey", "status": "status",
            "attempt_count": "attemptCount", "max_attempts": "maxAttempts",
            "available_at": "availableAt", "lease_owner": "leaseOwner",
            "lease_expires_at": "leaseExpiresAt", "created_at": "createdAt",
            "updated_at": "updatedAt", "published_at": "publishedAt",
        },
    },
    "brain_provider_health": {
        "store": "brain_provider_health", "key": "health_id",
        "aliases": {
            "health_id": "healthId", "tenant_id": "tenantId", "brain_profile_id": "brainProfileId",
            "provider": "provider", "model": "model", "status": "status",
            "consecutive_failures": "consecutiveFailures", "checked_at": "checkedAt",
            "next_check_at": "nextCheckAt",
        },
    },
    "brain_routing_policies": {
        "store": "brain_routing_policies", "key": "policy_id",
        "aliases": {
            "policy_id": "policyId", "tenant_id": "tenantId", "user_id": "userId",
            "version": "version", "active": "active", "created_at": "createdAt",
        },
    },
    "brain_routing_events": {
        "store": "brain_routing_events", "key": "routing_event_id",
        "aliases": {
            "routing_event_id": "routingEventId", "run_id": "runId", "request_id": "requestId",
            "tenant_id": "tenantId", "user_id": "userId", "project_id": "projectId",
            "task_type": "taskType", "policy_id": "policyId", "policy_version": "policyVersion",
            "selected_profile_id": "selectedProfileId", "selected_provider": "selectedProvider",
            "selected_model": "selectedModel", "blocked": "blocked", "occurred_at": "occurredAt",
        },
    },
    "tool_calls": {
        "store": "tool_calls", "key": "tool_call_id",
        "aliases": {
            "tool_call_id": "request.toolCallId", "run_id": "request.runId",
            "step_id": "request.stepId", "tenant_id": "request.tenantId", "user_id": "request.userId",
            "project_id": "request.projectId", "tool_id": "request.toolId",
            "tool_version": "toolVersion", "status": "result.status",
            "side_effect_level": "sideEffectLevel", "idempotency_key": "request.idempotencyKey",
            "state_version": "stateVersion", "requested_at": "request.requestedAt",
            "completed_at": "result.completedAt",
        },
    },
    "learning_candidates": {
        "store": "learning_candidates", "key": "candidate_id",
        "aliases": {
            "candidate_id": "candidateId", "tenant_id": "tenantId", "project_id": "projectId",
            "candidate_type": "candidateType", "target_key": "targetKey", "status": "status",
            "created_by": "createdBy", "created_at": "createdAt", "updated_at": "updatedAt",
        },
    },
    "learning_evaluations": {
        "store": "learning_evaluations", "key": "evaluation_id",
        "aliases": {
            "evaluation_id": "evaluationId", "candidate_id": "candidateId", "tenant_id": "tenantId",
            "passed": "passed", "evaluated_at": "evaluatedAt",
        },
    },
    "learning_promotions": {
        "store": "learning_promotions", "key": "decision_id",
        "aliases": {
            "decision_id": "decisionId", "candidate_id": "candidateId", "tenant_id": "tenantId",
            "action": "action", "environment": "environment", "approved_by": "approvedBy",
            "approved_at": "approvedAt",
        },
    },
    "coding_pod_jobs": {
        "store": "coding_pod_jobs", "key": "job_id",
        "aliases": {
            "job_id": "jobId", "tenant_id": "tenantId", "requested_by": "requestedBy",
            "workspace_ref": "workspaceRef", "branch_name": "branchName", "status": "status",
            "idempotency_key": "idempotencyKey", "lease_owner": "leaseOwner",
            "lease_expires_at": "leaseExpiresAt", "heartbeat_at": "heartbeatAt",
            "attempt_count": "attemptCount", "max_attempts": "maxAttempts",
            "state_version": "stateVersion",
            "created_at": "createdAt", "updated_at": "updatedAt",
        },
    },
}


def storage_backend() -> str:
    configured = os.environ.get("REALYTICS_STORAGE_BACKEND")
    if configured:
        return configured.lower()
    if os.environ.get("REALYTICS_STATE_FILE") and not os.environ.get("REALYTICS_SQLITE_FILE"):
        return "json"
    return "sqlite"


def _use_document_store() -> bool:
    """True for the whole-state document backends (JSON file or Postgres).

    Both read/write the full state via `_load_json_state()` /
    `_save_json_state()`; only the persistence target differs (file vs the
    Postgres `app_state` JSONB row). The sqlite backend keeps its per-table
    incremental fast path and is never a document store.
    """
    return storage_backend() in ("json", "postgres")


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
        "chief_pod_artifacts": {},
        "pod_ai_reviews": {},
        "agent_handoffs": {},
        "agent_runs": {},
        "agent_run_steps": {},
        "brain_usage_events": {},
        "runtime_outbox_events": {},
        "brain_provider_health": {},
        "brain_routing_policies": {},
        "brain_routing_events": {},
        "tool_calls": {},
        "learning_candidates": {},
        "learning_evaluations": {},
        "learning_promotions": {},
        "coding_pod_jobs": {},
        "legal_reviews": {},
        "permit_reviews": {},
        "recalculation_events": {},
        "calculation_runs": {},
        "deal_state_records": {},
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
        "user_ai_profiles": {},
    }


def load_state() -> Dict[str, Any] | None:
    if _use_document_store():
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
            chief_pod_artifacts=legacy_state.get("chief_pod_artifacts", {}),
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
    chief_pod_artifacts: Dict[str, Any] | None = None,
    prompt_artifacts: Dict[str, Any] | None = None,
    assumption_overrides: Dict[str, Any] | None = None,
    restructuring_cures: Dict[str, Any] | None = None,
    glossary_term_candidates: Dict[str, Any] | None = None,
    pod_ai_reviews: Dict[str, Any] | None = None,
) -> None:
    market_evidence = market_evidence or {}
    project_snapshots = project_snapshots or {}
    land_evidence = land_evidence or _load_existing_land_evidence_records()
    risk_registers = risk_registers or _load_existing_risk_registers()
    evidence_graphs = evidence_graphs or _load_existing_evidence_graphs()
    chief_pod_artifacts = chief_pod_artifacts or _load_existing_chief_pod_artifacts()
    pod_ai_reviews = pod_ai_reviews or _load_existing_pod_ai_reviews()
    prompt_artifacts = prompt_artifacts or _load_existing_prompt_artifacts()
    assumption_overrides = assumption_overrides or _load_existing_assumption_overrides()
    restructuring_cures = restructuring_cures or _load_existing_restructuring_cures()
    glossary_term_candidates = glossary_term_candidates or _load_existing_glossary_term_candidates()
    if user_activity_logs is None:
        user_activity_logs = _load_existing_user_activity_logs()
    identity = _load_existing_identity_records()
    if _use_document_store():
        _save_json_state(projects, reports, orchestrations, project_id_counter, market_evidence, land_evidence, risk_registers, evidence_graphs, chief_pod_artifacts, prompt_artifacts, assumption_overrides, restructuring_cures, project_snapshots, user_activity_logs, identity, glossary_term_candidates=glossary_term_candidates, pod_ai_reviews=pod_ai_reviews)
        return
    _save_sqlite_state(projects, reports, orchestrations, project_id_counter, market_evidence, risk_registers, evidence_graphs, chief_pod_artifacts, prompt_artifacts, assumption_overrides, restructuring_cures, project_snapshots)


# --- Incremental (single-row) persistence ---------------------------------
# The full save_state() above DELETEs and re-INSERTs every table on each call,
# so a single project create/upload re-serializes all projects + orchestration
# runs (with their large artifacts) — ~13s on the pilot DB. The functions below
# write only the changed row. They are sqlite-only and return False on the json
# backend so callers can fall back to a full save_state().

def upsert_project_record(project_id: str, payload: Dict[str, Any], project_id_counter: int | None = None) -> bool:
    """Persist a single project row (sqlite). Returns False on the json backend."""
    if storage_backend() != "sqlite":
        return False
    now = datetime.now(timezone.utc).isoformat()
    with _connect_sqlite() as conn:
        conn.execute(
            "INSERT INTO projects(project_id, payload_json, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET payload_json=excluded.payload_json, updated_at=excluded.updated_at",
            (str(project_id), json.dumps(payload, ensure_ascii=False), now),
        )
        if project_id_counter is not None:
            conn.execute(
                "INSERT INTO metadata(key, value) VALUES('project_id_counter', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(project_id_counter),),
            )
        conn.commit()
    return True


def delete_project_record_cascade(project_id: str) -> bool:
    """Delete a single project and its project-scoped rows (sqlite)."""
    if storage_backend() != "sqlite":
        return False
    pid = str(project_id)
    with _connect_sqlite() as conn:
        for table in (
            "projects",
            "reports",
            "orchestrations",
            "market_evidence",
            "project_snapshots",
            "risk_registers",
            "evidence_graphs",
            "chief_pod_artifacts",
            "pod_ai_reviews",
            "agent_handoffs",
            "agent_runs",
            "agent_run_steps",
            "brain_usage_events",
            "legal_reviews",
            "permit_reviews",
            "prompt_artifacts",
            "assumption_overrides",
            "restructuring_cures",
            # Project-scoped stores that were previously orphaned on delete.
            "deal_state_records",
            "calculation_runs",
            "land_evidence",
            "recalculation_events",
            "user_activity_logs",
            "glossary_term_candidates",
        ):
            conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (pid,))
        conn.commit()
    return True


def _upsert_review_record(project_id: str, record: Dict[str, Any], store_key: str, table: str, id_column: str) -> Dict[str, Any]:
    """Persist a domain review in JSON/Postgres document state or SQLite."""
    now = datetime.now(timezone.utc).isoformat()
    review_id = str(record.get("reviewId") or record.get("review_id") or f"{store_key}:{uuid4()}")
    normalized = {
        **record,
        "schemaVersion": record.get("schemaVersion") or "realytics-legal-review-v1",
        "projectId": str(project_id),
        "reviewId": review_id,
        "createdAt": record.get("createdAt") or now,
        "updatedAt": now,
    }
    if _use_document_store():
        state = _load_json_state() or default_state()
        state.setdefault(store_key, {}).setdefault(str(project_id), {})[review_id] = normalized
        _save_json_state_from_state(state)
        return normalized
    with _connect_sqlite() as conn:
        conn.execute(
            f"""INSERT INTO {table}(project_id, {id_column}, status, updated_at, payload_json)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(project_id, {id_column}) DO UPDATE SET
                  status=excluded.status, updated_at=excluded.updated_at,
                  payload_json=excluded.payload_json""",
            (str(project_id), review_id, normalized.get("status"), normalized["updatedAt"], json.dumps(normalized, ensure_ascii=False)),
        )
        conn.commit()
    return normalized


def _upsert_postgres_typed_record(
    *,
    store_key: str,
    project_id: str,
    record_id: str,
    table: str,
    values: Dict[str, Any],
    normalized: Dict[str, Any],
) -> None:
    """Dual-write critical audit data to app_state and its typed table."""
    from db import postgres

    state = _load_json_state() or default_state()
    state.setdefault(store_key, {}).setdefault(str(project_id), {})[str(record_id)] = normalized
    _save_json_state_from_state(state)
    postgres.upsert_typed_jsonb_record(table, values, normalized)


def _agent_run_values(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": record.get("runId"),
        "project_id": record.get("projectId"),
        "tenant_id": record.get("tenantId"),
        "user_id": record.get("userId"),
        "request_id": record.get("requestId"),
        "correlation_id": record.get("correlationId"),
        "trace_id": record.get("traceId"),
        "agent_id": record.get("agentId"),
        "agent_version": record.get("agentVersion"),
        "task_type": record.get("taskType"),
        "idempotency_key": record.get("idempotencyKey"),
        "input_hash": record.get("inputHash"),
        "status": record.get("status"),
        "attempt_count": int(record.get("attemptCount") or 0),
        "max_attempts": int(record.get("maxAttempts") or 1),
        "cancellation_requested": bool(record.get("cancellationRequested")),
        "state_version": int(record.get("stateVersion") or 1),
        "lease_owner": record.get("leaseOwner"),
        "lease_expires_at": record.get("leaseExpiresAt"),
        "heartbeat_at": record.get("heartbeatAt"),
        "deadline_at": record.get("deadlineAt"),
        "consumed_cost_usd": float((record.get("budget") or {}).get("consumedCostUsd") or 0),
        "consumed_llm_calls": int((record.get("budget") or {}).get("consumedLlmCalls") or 0),
        "consumed_tool_calls": int((record.get("budget") or {}).get("consumedToolCalls") or 0),
        "retry_of_run_id": record.get("retryOfRunId"),
        "selected_brain_profile_id": record.get("selectedBrainProfileId"),
        "created_at": record.get("createdAt"),
        "updated_at": record.get("updatedAt"),
        "started_at": record.get("startedAt"),
        "completed_at": record.get("completedAt"),
    }


def create_agent_run_record(record: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    """Atomically create an Agent run, enforcing tenant/Agent idempotency."""
    normalized = dict(record)
    values = _agent_run_values(normalized)
    run_id = str(values.get("run_id") or "")
    if not run_id:
        raise ValueError("runId is required")
    backend = storage_backend()
    if backend == "postgres":
        from db import postgres

        created = postgres.insert_typed_jsonb_record("agent_runs", values, normalized)
        persisted = normalized if created else get_agent_run_by_idempotency_record(
            tenant_id=str(values.get("tenant_id") or ""),
            agent_id=str(values.get("agent_id") or ""),
            idempotency_key=str(values.get("idempotency_key") or ""),
        )
        if created:
            state = _load_json_state() or default_state()
            state.setdefault("agent_runs", {})[run_id] = normalized
            _save_json_state_from_state(state)
        return persisted or normalized, created
    if backend == "json":
        with _AGENT_RUNTIME_LOCK:
            state = _load_json_state() or default_state()
            existing = next(
                (
                    item for item in state.setdefault("agent_runs", {}).values()
                    if item.get("tenantId") == values.get("tenant_id")
                    and item.get("agentId") == values.get("agent_id")
                    and item.get("idempotencyKey") == values.get("idempotency_key")
                ),
                None,
            )
            if existing:
                return existing, False
            state["agent_runs"][run_id] = normalized
            _save_json_state_from_state(state)
            return normalized, True
    with _AGENT_RUNTIME_LOCK, _connect_sqlite() as conn:
        try:
            conn.execute(
                """
                INSERT INTO agent_runs(
                    run_id, project_id, tenant_id, user_id, request_id, correlation_id, trace_id,
                    agent_id, agent_version, task_type, idempotency_key, input_hash, status,
                    attempt_count, max_attempts, cancellation_requested, state_version,
                    lease_owner, lease_expires_at, heartbeat_at, deadline_at,
                    consumed_cost_usd, consumed_llm_calls, consumed_tool_calls, retry_of_run_id,
                    selected_brain_profile_id, created_at, updated_at, started_at, completed_at,
                    payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values.values(), json.dumps(normalized, ensure_ascii=False)),
            )
            conn.commit()
            return normalized, True
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT payload_json FROM agent_runs WHERE tenant_id = ? AND agent_id = ? AND idempotency_key = ?",
                (values["tenant_id"], values["agent_id"], values["idempotency_key"]),
            ).fetchone()
            return (json.loads(row["payload_json"]) if row else normalized), False


def get_agent_run_record(run_id: str) -> Dict[str, Any] | None:
    if storage_backend() == "postgres":
        from db import postgres

        return postgres.get_typed_jsonb_record("agent_runs", {"run_id": str(run_id)})
    if storage_backend() == "json":
        return (_load_json_state() or default_state()).get("agent_runs", {}).get(str(run_id))
    with _connect_sqlite() as conn:
        row = conn.execute("SELECT payload_json FROM agent_runs WHERE run_id = ?", (str(run_id),)).fetchone()
    return json.loads(row["payload_json"]) if row else None


def get_agent_run_by_idempotency_record(
    *,
    tenant_id: str,
    agent_id: str,
    idempotency_key: str,
) -> Dict[str, Any] | None:
    if storage_backend() == "postgres":
        from db import postgres

        return postgres.get_typed_jsonb_record(
            "agent_runs",
            {"tenant_id": tenant_id, "agent_id": agent_id, "idempotency_key": idempotency_key},
        )
    if storage_backend() == "json":
        return next(
            (
                item for item in (_load_json_state() or default_state()).get("agent_runs", {}).values()
                if item.get("tenantId") == tenant_id
                and item.get("agentId") == agent_id
                and item.get("idempotencyKey") == idempotency_key
            ),
            None,
        )
    with _connect_sqlite() as conn:
        row = conn.execute(
            "SELECT payload_json FROM agent_runs WHERE tenant_id = ? AND agent_id = ? AND idempotency_key = ?",
            (tenant_id, agent_id, idempotency_key),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def update_agent_run_record(
    run_id: str,
    record: Dict[str, Any],
    *,
    expected_state_version: int,
    outbox_event: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Persist one state transition with optimistic concurrency control."""
    normalized = dict(record)
    values = _agent_run_values(normalized)
    if str(values.get("run_id")) != str(run_id):
        raise ValueError("runId does not match the target record")
    backend = storage_backend()
    if backend == "postgres":
        from db import postgres

        if outbox_event:
            outbox_spec = _operational_spec("runtime_outbox_events")
            outbox_values = {
                column: _payload_path_value(outbox_event, alias)
                for column, alias in outbox_spec["aliases"].items()
            }
            updated = postgres.update_agent_run_with_outbox(
                values,
                normalized,
                expected_state_version=expected_state_version,
                outbox_values=outbox_values,
                outbox_payload=outbox_event,
            )
        else:
            updated = postgres.update_agent_run_optimistic(
                values,
                normalized,
                expected_state_version=expected_state_version,
            )
        if not updated:
            raise RuntimeError("AGENT_RUN_VERSION_CONFLICT")
        state = _load_json_state() or default_state()
        state.setdefault("agent_runs", {})[str(run_id)] = normalized
        if outbox_event:
            state.setdefault("runtime_outbox_events", {})[str(outbox_event["eventId"])] = outbox_event
        _save_json_state_from_state(state)
        return normalized
    if backend == "json":
        with _AGENT_RUNTIME_LOCK:
            state = _load_json_state() or default_state()
            current = state.setdefault("agent_runs", {}).get(str(run_id))
            if not current:
                raise KeyError(run_id)
            if int(current.get("stateVersion") or 1) != int(expected_state_version):
                raise RuntimeError("AGENT_RUN_VERSION_CONFLICT")
            state["agent_runs"][str(run_id)] = normalized
            if outbox_event:
                state.setdefault("runtime_outbox_events", {})[str(outbox_event["eventId"])] = outbox_event
            _save_json_state_from_state(state)
            return normalized
    with _AGENT_RUNTIME_LOCK, _connect_sqlite() as conn:
        assignments = [f"{column} = ?" for column in values if column != "run_id"]
        params = [values[column] for column in values if column != "run_id"]
        assignments.append("payload_json = ?")
        params.extend([json.dumps(normalized, ensure_ascii=False), str(run_id), int(expected_state_version)])
        cursor = conn.execute(
            f"UPDATE agent_runs SET {', '.join(assignments)} WHERE run_id = ? AND state_version = ?",
            params,
        )
        if cursor.rowcount != 1:
            raise RuntimeError("AGENT_RUN_VERSION_CONFLICT")
        if outbox_event:
            spec = _operational_spec("runtime_outbox_events")
            outbox_values = {
                column: _payload_path_value(outbox_event, alias)
                for column, alias in spec["aliases"].items()
            }
            columns = list(outbox_values)
            conn.execute(
                f"INSERT OR IGNORE INTO runtime_outbox_events({', '.join(columns)}, payload_json) "
                f"VALUES({', '.join('?' for _ in columns)}, ?)",
                [*outbox_values.values(), json.dumps(outbox_event, ensure_ascii=False, default=str)],
            )
        conn.commit()
    return normalized


def list_agent_run_records(
    *,
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    if storage_backend() == "postgres":
        from db import postgres

        where: Dict[str, Any] = {}
        if project_id:
            where["project_id"] = str(project_id)
        if status:
            where["status"] = status
        return postgres.list_typed_jsonb_records("agent_runs", where=where, limit=limit)
    if storage_backend() == "json":
        records = list((_load_json_state() or default_state()).get("agent_runs", {}).values())
    else:
        query = "SELECT payload_json FROM agent_runs WHERE 1=1"
        params: list[Any] = []
        if project_id:
            query += " AND project_id = ?"
            params.append(str(project_id))
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if project_id:
        records = [item for item in records if str(item.get("projectId") or "") == str(project_id)]
    if status:
        records = [item for item in records if item.get("status") == status]
    return sorted(records, key=lambda item: item.get("updatedAt") or "", reverse=True)[:limit]


def upsert_agent_run_step_record(record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(record)
    values = {
        "step_id": normalized.get("stepId"),
        "run_id": normalized.get("runId"),
        "project_id": normalized.get("projectId"),
        "sequence_no": normalized.get("sequenceNo"),
        "stage": normalized.get("stage"),
        "step_type": normalized.get("stepType"),
        "status": normalized.get("status"),
        "attempt_no": normalized.get("attemptNo"),
        "error_code": (normalized.get("error") or {}).get("code"),
        "started_at": normalized.get("startedAt"),
        "completed_at": normalized.get("completedAt"),
    }
    if storage_backend() == "postgres":
        from db import postgres

        postgres.upsert_typed_jsonb_record("agent_run_steps", values, normalized)
        state = _load_json_state() or default_state()
        state.setdefault("agent_run_steps", {}).setdefault(str(values["run_id"]), {})[str(values["step_id"])] = normalized
        _save_json_state_from_state(state)
    elif storage_backend() == "json":
        with _AGENT_RUNTIME_LOCK:
            state = _load_json_state() or default_state()
            state.setdefault("agent_run_steps", {}).setdefault(str(values["run_id"]), {})[str(values["step_id"])] = normalized
            _save_json_state_from_state(state)
    else:
        with _connect_sqlite() as conn:
            conn.execute(
                """
                INSERT INTO agent_run_steps(
                    step_id, run_id, project_id, sequence_no, stage, step_type, status,
                    attempt_no, error_code, started_at, completed_at, payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(step_id) DO UPDATE SET
                    status=excluded.status, error_code=excluded.error_code,
                    completed_at=excluded.completed_at, payload_json=excluded.payload_json
                """,
                (*values.values(), json.dumps(normalized, ensure_ascii=False)),
            )
            conn.commit()
    return normalized


def list_agent_run_step_records(run_id: str) -> list[Dict[str, Any]]:
    if storage_backend() == "postgres":
        from db import postgres

        return postgres.list_typed_jsonb_records("agent_run_steps", where={"run_id": str(run_id)}, limit=1000)
    if storage_backend() == "json":
        records = list((_load_json_state() or default_state()).get("agent_run_steps", {}).get(str(run_id), {}).values())
    else:
        with _connect_sqlite() as conn:
            records = [
                json.loads(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM agent_run_steps WHERE run_id = ? ORDER BY sequence_no",
                    (str(run_id),),
                )
            ]
    return sorted(records, key=lambda item: int(item.get("sequenceNo") or 0))


def upsert_brain_usage_event_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist usage metadata only; prompts, responses, and credentials are forbidden."""
    normalized = dict(record)
    values = {
        "event_id": normalized.get("eventId"),
        "run_id": normalized.get("runId"),
        "step_id": normalized.get("stepId"),
        "tenant_id": normalized.get("tenantId"),
        "project_id": normalized.get("projectId"),
        "agent_id": normalized.get("agentId"),
        "agent_version": normalized.get("agentVersion"),
        "brain_profile_id": normalized.get("brainProfileId"),
        "provider": normalized.get("provider"),
        "model": normalized.get("model"),
        "status": normalized.get("status"),
        "input_tokens": normalized.get("inputTokens"),
        "output_tokens": normalized.get("outputTokens"),
        "estimated_cost_usd": normalized.get("estimatedCostUsd"),
        "latency_ms": normalized.get("latencyMs"),
        "occurred_at": normalized.get("occurredAt"),
    }
    if storage_backend() == "postgres":
        from db import postgres

        postgres.upsert_typed_jsonb_record("brain_usage_events", values, normalized)
        state = _load_json_state() or default_state()
        state.setdefault("brain_usage_events", {}).setdefault(str(values["run_id"]), {})[str(values["event_id"])] = normalized
        _save_json_state_from_state(state)
    elif storage_backend() == "json":
        with _AGENT_RUNTIME_LOCK:
            state = _load_json_state() or default_state()
            state.setdefault("brain_usage_events", {}).setdefault(str(values["run_id"]), {})[str(values["event_id"])] = normalized
            _save_json_state_from_state(state)
    else:
        with _connect_sqlite() as conn:
            conn.execute(
                """
                INSERT INTO brain_usage_events(
                    event_id, run_id, step_id, tenant_id, project_id, agent_id, agent_version,
                    brain_profile_id, provider, model, status, input_tokens, output_tokens,
                    estimated_cost_usd, latency_ms, occurred_at, payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET payload_json=excluded.payload_json
                """,
                (*values.values(), json.dumps(normalized, ensure_ascii=False)),
            )
            conn.commit()
    return normalized


def list_brain_usage_event_records(run_id: str) -> list[Dict[str, Any]]:
    if storage_backend() == "postgres":
        from db import postgres

        return postgres.list_typed_jsonb_records("brain_usage_events", where={"run_id": str(run_id)}, limit=1000)
    if storage_backend() == "json":
        records = list((_load_json_state() or default_state()).get("brain_usage_events", {}).get(str(run_id), {}).values())
    else:
        with _connect_sqlite() as conn:
            records = [
                json.loads(row["payload_json"])
                for row in conn.execute(
                    "SELECT payload_json FROM brain_usage_events WHERE run_id = ? ORDER BY occurred_at",
                    (str(run_id),),
                )
            ]
    return sorted(records, key=lambda item: item.get("occurredAt") or "")


def _new_runtime_outbox_payload(
    *,
    tenant_id: str,
    project_id: str | None,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    updated_at: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    event_id = f"outbox:{uuid4()}"
    return {
        "schemaVersion": "realytics-runtime-outbox-event-v1",
        "eventId": event_id,
        "tenantId": tenant_id,
        "projectId": project_id,
        "aggregateType": aggregate_type,
        "aggregateId": aggregate_id,
        "eventType": event_type,
        "idempotencyKey": f"{aggregate_type}:{aggregate_id}:{event_type}:{updated_at}",
        "status": "pending",
        "payload": payload,
        "attemptCount": 0,
        "maxAttempts": 8,
        "availableAt": updated_at,
        "leaseOwner": None,
        "leaseExpiresAt": None,
        "lastError": None,
        "createdAt": updated_at,
        "updatedAt": updated_at,
        "publishedAt": None,
    }


def _runtime_outbox_values(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        column: _payload_path_value(event, alias)
        for column, alias in _OPERATIONAL_RECORD_SPECS["runtime_outbox_events"]["aliases"].items()
    }


def _insert_sqlite_runtime_outbox(conn: sqlite3.Connection, event: Dict[str, Any]) -> None:
    values = _runtime_outbox_values(event)
    columns = list(values)
    conn.execute(
        f"INSERT OR IGNORE INTO runtime_outbox_events({', '.join(columns)}, payload_json) "
        f"VALUES({', '.join('?' for _ in columns)}, ?)",
        [*values.values(), json.dumps(event, ensure_ascii=False)],
    )


def upsert_operational_record(table: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an Agent Platform control-plane record in a first-class store."""
    spec = _operational_spec(table)
    normalized = dict(record)
    values = {
        column: _payload_path_value(normalized, alias)
        for column, alias in spec["aliases"].items()
    }
    key_column = str(spec["key"])
    key_value = values.get(key_column)
    if key_value in {None, ""}:
        raise ValueError(f"{spec['aliases'][key_column]} is required")
    backend = storage_backend()
    if backend == "postgres":
        from db import postgres

        postgres.upsert_typed_jsonb_record(table, values, normalized)
        with _AGENT_RUNTIME_LOCK:
            state = _load_json_state() or default_state()
            state.setdefault(spec["store"], {})[str(key_value)] = normalized
            _save_json_state_from_state(state)
        return normalized
    if backend == "json":
        with _AGENT_RUNTIME_LOCK:
            state = _load_json_state() or default_state()
            state.setdefault(spec["store"], {})[str(key_value)] = normalized
            _save_json_state_from_state(state)
        return normalized
    columns = list(values)
    update_columns = [column for column in columns if column != key_column]
    sql = (
        f"INSERT INTO {table}({', '.join(columns)}, payload_json) "
        f"VALUES({', '.join('?' for _ in columns)}, ?) "
        f"ON CONFLICT({key_column}) DO UPDATE SET "
        + ", ".join([*(f"{column}=excluded.{column}" for column in update_columns), "payload_json=excluded.payload_json"])
    )
    with _connect_sqlite() as conn:
        conn.execute(sql, [*values.values(), json.dumps(normalized, ensure_ascii=False, default=str)])
        conn.commit()
    return normalized


def get_operational_record(table: str, record_id: str) -> Dict[str, Any] | None:
    spec = _operational_spec(table)
    key_column = str(spec["key"])
    if storage_backend() == "postgres":
        from db import postgres

        return postgres.get_typed_jsonb_record(table, {key_column: str(record_id)})
    if storage_backend() == "json":
        return (_load_json_state() or default_state()).get(spec["store"], {}).get(str(record_id))
    with _connect_sqlite() as conn:
        row = conn.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column} = ?",
            (str(record_id),),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_operational_records(
    table: str,
    *,
    filters: Dict[str, Any] | None = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    spec = _operational_spec(table)
    filters = dict(filters or {})
    allowed = set(spec["aliases"])
    if any(column not in allowed for column in filters):
        raise ValueError(f"Unsupported filter for {table}")
    if storage_backend() == "postgres":
        from db import postgres

        return postgres.list_typed_jsonb_records(table, where=filters, limit=limit)
    if storage_backend() == "json":
        records = list((_load_json_state() or default_state()).get(spec["store"], {}).values())
        for column, expected in filters.items():
            alias = spec["aliases"][column]
            records = [item for item in records if _payload_path_value(item, alias) == expected]
        return records[: max(1, min(int(limit), 1000))]
    clauses = [f"{column} = ?" for column in filters]
    sql = f"SELECT payload_json FROM {table}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY rowid DESC LIMIT ?"
    params = [*filters.values(), max(1, min(int(limit), 1000))]
    with _connect_sqlite() as conn:
        return [json.loads(row["payload_json"]) for row in conn.execute(sql, params)]


def claim_runtime_outbox_event_records(
    *,
    worker_id: str,
    limit: int = 20,
    lease_seconds: int = 60,
) -> list[Dict[str, Any]]:
    """Lease due outbox events so concurrent workers cannot publish the same event."""
    now = datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(seconds=max(5, lease_seconds))
    backend = storage_backend()
    if backend == "postgres":
        from db import postgres

        return postgres.claim_runtime_outbox_records(
            worker_id=worker_id,
            now=now,
            lease_expires_at=lease_expires_at,
            limit=limit,
        )
    if backend == "json":
        with _AGENT_RUNTIME_LOCK:
            state = _load_json_state() or default_state()
            records = list(state.setdefault("runtime_outbox_events", {}).values())
            eligible = [
                item for item in records
                if _outbox_is_claimable(item, now)
            ]
            eligible.sort(key=lambda item: (item.get("availableAt") or "", item.get("createdAt") or ""))
            claimed = []
            for item in eligible[: max(1, min(int(limit), 100))]:
                updated = _claimed_outbox_payload(item, worker_id, now, lease_expires_at)
                state["runtime_outbox_events"][str(updated["eventId"])] = updated
                claimed.append(updated)
            _save_json_state_from_state(state)
            return claimed
    with _AGENT_RUNTIME_LOCK, _connect_sqlite() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT event_id, payload_json
            FROM runtime_outbox_events
            WHERE (
                (status IN ('pending', 'retrying') AND available_at <= ?)
                OR (status = 'processing' AND lease_expires_at < ?)
            )
            ORDER BY available_at, created_at
            LIMIT ?
            """,
            (now.isoformat(), now.isoformat(), max(1, min(int(limit), 100))),
        ).fetchall()
        claimed = []
        for row in rows:
            updated = _claimed_outbox_payload(
                json.loads(row["payload_json"]), worker_id, now, lease_expires_at,
            )
            conn.execute(
                """
                UPDATE runtime_outbox_events
                SET status = ?, attempt_count = ?, lease_owner = ?, lease_expires_at = ?,
                    updated_at = ?, payload_json = ?
                WHERE event_id = ?
                """,
                (
                    updated["status"], updated["attemptCount"], updated["leaseOwner"],
                    updated["leaseExpiresAt"], updated["updatedAt"],
                    json.dumps(updated, ensure_ascii=False), updated["eventId"],
                ),
            )
            claimed.append(updated)
        conn.commit()
        return claimed


def claim_coding_pod_job_record(
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> Dict[str, Any] | None:
    """Atomically lease one queued or abandoned Coding Pod job."""
    now = datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(seconds=max(30, lease_seconds))
    backend = storage_backend()
    if backend == "postgres":
        from db import postgres

        return postgres.claim_coding_pod_job_record(
            worker_id=worker_id,
            now=now,
            lease_expires_at=lease_expires_at,
        )
    if backend == "json":
        with _AGENT_RUNTIME_LOCK:
            state = _load_json_state() or default_state()
            records = list(state.setdefault("coding_pod_jobs", {}).values())
            eligible = [item for item in records if _coding_job_is_claimable(item, now)]
            eligible.sort(key=lambda item: (item.get("createdAt") or "", item.get("jobId") or ""))
            if not eligible:
                return None
            updated = _claimed_coding_job_payload(eligible[0], worker_id, now, lease_expires_at)
            state["coding_pod_jobs"][str(updated["jobId"])] = updated
            _save_json_state_from_state(state)
            return updated
    with _AGENT_RUNTIME_LOCK, _connect_sqlite() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT job_id, payload_json
            FROM coding_pod_jobs
            WHERE attempt_count < max_attempts AND (
                status = 'queued'
                OR (status IN ('leased', 'running') AND lease_expires_at < ?)
            )
            ORDER BY created_at, job_id
            LIMIT 1
            """,
            (now.isoformat(),),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        updated = _claimed_coding_job_payload(
            json.loads(row["payload_json"]), worker_id, now, lease_expires_at,
        )
        conn.execute(
            """
            UPDATE coding_pod_jobs
            SET status = ?, lease_owner = ?, lease_expires_at = ?, heartbeat_at = ?,
                attempt_count = ?, max_attempts = ?, state_version = ?, updated_at = ?,
                payload_json = ?
            WHERE job_id = ?
            """,
            (
                updated["status"], updated["leaseOwner"], updated["leaseExpiresAt"],
                updated["heartbeatAt"], updated["attemptCount"], updated["maxAttempts"],
                updated["stateVersion"], updated["updatedAt"],
                json.dumps(updated, ensure_ascii=False), updated["jobId"],
            ),
        )
        conn.commit()
        return updated


def _coding_job_is_claimable(record: Dict[str, Any], now: datetime) -> bool:
    if int(record.get("attemptCount") or 0) >= int(record.get("maxAttempts") or 3):
        return False
    status = str(record.get("status") or "")
    if status == "queued":
        return True
    if status in {"leased", "running"}:
        expires = record.get("leaseExpiresAt")
        return bool(expires and _parse_datetime(expires) < now)
    return False


def _claimed_coding_job_payload(
    record: Dict[str, Any],
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
) -> Dict[str, Any]:
    updated = dict(record)
    updated.update({
        "status": "leased",
        "leaseOwner": worker_id,
        "leaseExpiresAt": lease_expires_at.isoformat(),
        "heartbeatAt": now.isoformat(),
        "attemptCount": int(record.get("attemptCount") or 0) + 1,
        "maxAttempts": int(record.get("maxAttempts") or 3),
        "stateVersion": int(record.get("stateVersion") or 1) + 1,
        "updatedAt": now.isoformat(),
    })
    return updated


def _outbox_is_claimable(record: Dict[str, Any], now: datetime) -> bool:
    status = str(record.get("status") or "")
    if status in {"pending", "retrying"}:
        return _parse_datetime(record.get("availableAt")) <= now
    if status == "processing":
        expires = record.get("leaseExpiresAt")
        return bool(expires and _parse_datetime(expires) < now)
    return False


def _claimed_outbox_payload(
    record: Dict[str, Any],
    worker_id: str,
    now: datetime,
    lease_expires_at: datetime,
) -> Dict[str, Any]:
    updated = dict(record)
    updated.update({
        "status": "processing",
        "attemptCount": int(record.get("attemptCount") or 0) + 1,
        "leaseOwner": worker_id,
        "leaseExpiresAt": lease_expires_at.isoformat(),
        "updatedAt": now.isoformat(),
    })
    return updated


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _operational_spec(table: str) -> Dict[str, Any]:
    spec = _OPERATIONAL_RECORD_SPECS.get(table)
    if spec is None:
        raise ValueError(f"Unsupported operational table: {table}")
    return spec


def _payload_path_value(payload: Dict[str, Any], path: str) -> Any:
    value: Any = payload
    for segment in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def _list_review_records(project_id: str, store_key: str, table: str, limit: int = 100) -> list[Dict[str, Any]]:
    if _use_document_store():
        state = _load_json_state() or default_state()
        records = list(state.get(store_key, {}).get(str(project_id), {}).values())
    else:
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(
                f"SELECT payload_json FROM {table} WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
                (str(project_id), limit),
            )]
    return sorted(records, key=lambda item: item.get("updatedAt") or item.get("createdAt") or "", reverse=True)[:limit]


def upsert_legal_review_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    return _upsert_review_record(project_id, record, "legal_reviews", "legal_reviews", "review_id")


def list_legal_review_records(project_id: str, limit: int = 100) -> list[Dict[str, Any]]:
    return _list_review_records(project_id, "legal_reviews", "legal_reviews", limit)


def upsert_permit_review_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    return _upsert_review_record(project_id, record, "permit_reviews", "permit_reviews", "review_id")


def list_permit_review_records(project_id: str, limit: int = 100) -> list[Dict[str, Any]]:
    return _list_review_records(project_id, "permit_reviews", "permit_reviews", limit)


def upsert_orchestration_run(project_id: str, run_id: str, run: Dict[str, Any]) -> bool:
    """Persist a single orchestration run row (sqlite)."""
    if storage_backend() != "sqlite":
        return False
    with _connect_sqlite() as conn:
        conn.execute(
            "INSERT INTO orchestrations(project_id, run_id, status, started_at, completed_at, payload_json) "
            "VALUES(?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, run_id) DO UPDATE SET "
            "status=excluded.status, started_at=excluded.started_at, "
            "completed_at=excluded.completed_at, payload_json=excluded.payload_json",
            (
                str(project_id),
                str(run_id),
                run.get("status"),
                run.get("started_at"),
                run.get("completed_at"),
                json.dumps(run, ensure_ascii=False),
            ),
        )
        conn.commit()
    return True


def upsert_report_record(project_id: str, record: Dict[str, Any]) -> bool:
    """Persist a single report row (sqlite)."""
    if storage_backend() != "sqlite":
        return False
    with _connect_sqlite() as conn:
        conn.execute(
            "INSERT INTO reports(project_id, report_id, version, created_at, payload_json) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, report_id) DO UPDATE SET "
            "version=excluded.version, created_at=excluded.created_at, payload_json=excluded.payload_json",
            (
                str(project_id),
                record.get("report_id"),
                int(record.get("version", 1)),
                record.get("created_at"),
                json.dumps(record, ensure_ascii=False),
            ),
        )
        conn.commit()
    return True


def upsert_project_snapshot_record(project_id: str, record: Dict[str, Any]) -> bool:
    """Persist a single project snapshot row (sqlite)."""
    if storage_backend() != "sqlite":
        return False
    with _connect_sqlite() as conn:
        conn.execute(
            "INSERT INTO project_snapshots(project_id, snapshot_id, run_id, created_at, payload_json) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(project_id, snapshot_id) DO UPDATE SET "
            "run_id=excluded.run_id, created_at=excluded.created_at, payload_json=excluded.payload_json",
            (
                str(project_id),
                record.get("snapshotId") or record.get("snapshot_id"),
                record.get("runId") or record.get("run_id"),
                record.get("createdAt") or record.get("created_at"),
                json.dumps(record, ensure_ascii=False),
            ),
        )
        conn.commit()
    return True


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
    if _use_document_store():
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
            state.get("chief_pod_artifacts", {}),
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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


def _stable_deal_state_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts if part is not None and str(part) != "")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"dsr_{digest}"


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _materialize_risk_register_records(project_id: str, run_id: str, risk_register: Dict[str, Any]) -> None:
    for index, risk in enumerate(_as_list(risk_register.get("risks"))):
        if not isinstance(risk, dict):
            continue
        code = risk.get("code") or risk.get("riskCode") or risk.get("risk_id") or risk.get("riskId") or f"risk_{index + 1}"
        source_locator = risk.get("sourceLocator") or risk.get("source_locator") or risk.get("evidenceLocator") or {}
        upsert_deal_state_record(
            project_id,
            {
                **risk,
                "recordId": risk.get("recordId") or _stable_deal_state_id(project_id, "risk_register", run_id, code, index),
                "recordType": "risk_issue",
                "key": code,
                "sourceType": "risk_register",
                "sourceLocator": source_locator or {"runId": run_id, "registerId": risk_register.get("registerId")},
                "riskRegisterRunId": run_id,
            },
        )


def _materialize_evidence_graph_records(project_id: str, run_id: str, graph: Dict[str, Any]) -> None:
    for index, node in enumerate(_as_list(graph.get("nodes"))):
        if not isinstance(node, dict):
            continue
        node_type = node.get("type") or "node"
        metadata = _as_dict(node.get("metadata"))
        node_id = node.get("id") or f"node_{index + 1}"
        source_locator = metadata.get("sourceLocator") or {
            key: metadata.get(key)
            for key in ("source", "path", "page", "cell", "apiRowId", "documentId", "evidenceId")
            if metadata.get(key) is not None
        }
        if node_type == "risk":
            record_type = "risk_issue"
        elif node_type in {"source_document", "source_locator", "evidence"}:
            record_type = "evidence"
        elif node_type in {"metric", "calculation"}:
            record_type = "metric"
        elif node_type in {"assumption", "assumption_value"}:
            record_type = "assumption"
        else:
            continue
        upsert_deal_state_record(
            project_id,
            {
                "recordId": _stable_deal_state_id(project_id, "evidence_graph", run_id, node_id),
                "recordType": record_type,
                "key": metadata.get("key") or metadata.get("code") or metadata.get("riskId") or node_id,
                "value": metadata.get("value"),
                "unit": metadata.get("unit"),
                "label": node.get("label") or metadata.get("title"),
                "sourceType": "evidence_graph",
                "sourceLocator": source_locator or {"runId": run_id, "graphId": graph.get("graphId"), "nodeId": node_id},
                "evidenceGraphRunId": run_id,
                "evidenceGraphNodeId": node_id,
                "confidence": metadata.get("confidence"),
            },
        )


def _materialize_chief_artifact_records(project_id: str, artifact: Dict[str, Any]) -> None:
    result = _as_dict(artifact.get("result"))
    artifact_key = artifact.get("artifactKey")
    pod_key = artifact.get("podKey")
    base_locator = {
        "artifactKey": artifact_key,
        "podKey": pod_key,
        "artifactType": artifact.get("artifactType"),
    }
    materialized: set[str] = set()
    pending_records: list[Dict[str, Any]] = []
    id_keys = {
        "id", "ids", "page", "row", "index", "year", "month", "monthindex",
        "priority", "rank", "units", "unitcount",
    }

    def locator_for(path: str, inherited: Dict[str, Any], node: Any = None) -> Dict[str, Any]:
        explicit = {}
        if isinstance(node, dict):
            explicit = _as_dict(node.get("sourceLocator") or node.get("source_locator"))
        return {**base_locator, **inherited, **explicit, "artifactPath": path}

    def persist_reference(record_type: str, value: Any, path: str, locator: Dict[str, Any]) -> None:
        if value in (None, ""):
            return
        reference = str(value)
        record_id = _stable_deal_state_id(
            project_id, f"chief_artifact_{record_type}", artifact_key, path, reference
        )
        if record_id in materialized:
            return
        materialized.add(record_id)
        pending_records.append(
            {
                "recordId": record_id,
                "recordType": record_type,
                "key": reference,
                "sourceType": "chief_pod_artifact",
                "sourceLocator": locator,
            }
        )

    def walk(node: Any, path: str, inherited_locator: Dict[str, Any]) -> None:
        current_locator = locator_for(path, inherited_locator, node)
        if isinstance(node, dict):
            for key, value in node.items():
                child_path = f"{path}.{key}"
                normalized_key = str(key).replace("_", "").lower()
                if normalized_key in {"riskflags", "riskissues"} and isinstance(value, list):
                    for index, risk in enumerate(value):
                        if not isinstance(risk, dict):
                            continue
                        risk_path = f"{child_path}[{index}]"
                        code = risk.get("code") or risk.get("riskCode") or f"risk_{index + 1}"
                        record_id = _stable_deal_state_id(
                            project_id, "chief_artifact_risk", artifact_key, risk_path, code
                        )
                        if record_id not in materialized:
                            materialized.add(record_id)
                            pending_records.append(
                                {
                                    **risk,
                                    "recordId": record_id,
                                    "recordType": "risk_issue",
                                    "key": code,
                                    "sourceType": "chief_pod_artifact",
                                    "sourceLocator": locator_for(risk_path, current_locator, risk),
                                }
                            )
                    continue
                if normalized_key in {"evidenceid", "evidenceids", "inputevidenceids", "sourceevidenceids"}:
                    references = value if isinstance(value, list) else [value]
                    for index, reference in enumerate(references):
                        persist_reference("evidence", reference, f"{child_path}[{index}]", current_locator)
                    continue
                if normalized_key in {"assumptionid", "assumptionids", "inputassumptionids"}:
                    references = value if isinstance(value, list) else [value]
                    for index, reference in enumerate(references):
                        persist_reference("assumption", reference, f"{child_path}[{index}]", current_locator)
                    continue
                if normalized_key in {"sourcelocator", "sourcelocators"}:
                    continue
                walk(value, child_path, current_locator)
            return
        if isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", current_locator)
            return
        if isinstance(node, bool) or not isinstance(node, (int, float)):
            return
        leaf_key = path.rsplit(".", 1)[-1].split("[", 1)[0].replace("_", "").lower()
        if leaf_key in id_keys or leaf_key.endswith("id") or leaf_key.endswith("ids"):
            return
        record_id = _stable_deal_state_id(project_id, "chief_artifact_metric", artifact_key, path)
        if record_id in materialized:
            return
        materialized.add(record_id)
        metric_path_parts = path.removeprefix("result.").split(".")
        canonical_metric_path = ".".join(part for part in metric_path_parts if part != "metrics")
        pending_records.append(
            {
                "recordId": record_id,
                "recordType": "metric",
                "key": f"{pod_key}.{canonical_metric_path}" if pod_key else canonical_metric_path,
                "value": node,
                "sourceType": "chief_pod_artifact",
                "sourceLocator": current_locator,
            }
        )

    walk(result, "result", {})
    upsert_deal_state_records(project_id, pending_records)


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

    if _use_document_store():
        state = _load_json_state() or default_state()
        state.setdefault("risk_registers", {})
        state["risk_registers"].setdefault(project_id, {})[run_id] = normalized
        _save_json_state_from_state(state)
        _materialize_risk_register_records(project_id, run_id, normalized)
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
    _materialize_risk_register_records(project_id, run_id, normalized)
    return normalized


def get_risk_register_record(project_id: str, run_id: str) -> Dict[str, Any] | None:
    if _use_document_store():
        state = _load_json_state() or default_state()
        return state.get("risk_registers", {}).get(project_id, {}).get(run_id)
    with _connect_sqlite() as conn:
        row = conn.execute(
            "SELECT payload_json FROM risk_registers WHERE project_id = ? AND run_id = ?",
            (project_id, run_id),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_risk_register_records(project_id: str) -> list[Dict[str, Any]]:
    if _use_document_store():
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

    if _use_document_store():
        state = _load_json_state() or default_state()
        state.setdefault("evidence_graphs", {})
        state["evidence_graphs"].setdefault(project_id, {})[run_id] = normalized
        _save_json_state_from_state(state)
        _materialize_evidence_graph_records(project_id, run_id, normalized)
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
    _materialize_evidence_graph_records(project_id, run_id, normalized)
    return normalized


def get_evidence_graph_record(project_id: str, run_id: str) -> Dict[str, Any] | None:
    if _use_document_store():
        state = _load_json_state() or default_state()
        return state.get("evidence_graphs", {}).get(project_id, {}).get(run_id)
    with _connect_sqlite() as conn:
        row = conn.execute(
            "SELECT payload_json FROM evidence_graphs WHERE project_id = ? AND run_id = ?",
            (project_id, run_id),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_evidence_graph_records(project_id: str) -> list[Dict[str, Any]]:
    if _use_document_store():
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


def upsert_chief_pod_artifact_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a Chief Deal Orchestrator pod artifact as a first-class record."""
    now = datetime.now(timezone.utc).isoformat()
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    artifact_type = record.get("artifactType") or result.get("artifactType") or "chief_pod_result"
    artifact_key = record.get("artifactKey") or record.get("artifact_key") or f"artifact:{record.get('podKey') or record.get('pod_key') or 'pod'}:{uuid4()}"
    normalized = {
        **record,
        "schemaVersion": record.get("schemaVersion") or "realytics-chief-pod-artifact-v1",
        "projectId": project_id,
        "artifactKey": str(artifact_key),
        "podKey": record.get("podKey") or record.get("pod_key"),
        "artifactType": artifact_type,
        "status": record.get("status") or "completed",
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }

    if storage_backend() == "postgres":
        _upsert_postgres_typed_record(
            store_key="chief_pod_artifacts",
            project_id=project_id,
            record_id=normalized["artifactKey"],
            table="chief_pod_artifacts",
            values={
                "project_id": project_id,
                "artifact_key": normalized["artifactKey"],
                "pod_key": normalized.get("podKey"),
                "artifact_type": normalized.get("artifactType"),
                "status": normalized.get("status"),
                "created_at": normalized.get("createdAt"),
                "updated_at": normalized.get("updatedAt"),
            },
            normalized=normalized,
        )
        _materialize_chief_artifact_records(project_id, normalized)
        return normalized
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("chief_pod_artifacts", {})
        state["chief_pod_artifacts"].setdefault(project_id, {})[normalized["artifactKey"]] = normalized
        _save_json_state_from_state(state)
        _materialize_chief_artifact_records(project_id, normalized)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO chief_pod_artifacts(
                project_id, artifact_key, pod_key, artifact_type, status,
                created_at, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, artifact_key) DO UPDATE SET
                pod_key=excluded.pod_key,
                artifact_type=excluded.artifact_type,
                status=excluded.status,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                normalized["artifactKey"],
                normalized.get("podKey"),
                normalized.get("artifactType"),
                normalized.get("status"),
                normalized.get("createdAt"),
                normalized.get("updatedAt"),
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    _materialize_chief_artifact_records(project_id, normalized)
    return normalized


def get_chief_pod_artifact_record(project_id: str, artifact_key: str) -> Dict[str, Any] | None:
    if storage_backend() == "postgres":
        from db import postgres

        return postgres.get_typed_jsonb_record(
            "chief_pod_artifacts",
            {"project_id": project_id, "artifact_key": artifact_key},
        )
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        return state.get("chief_pod_artifacts", {}).get(project_id, {}).get(artifact_key)
    with _connect_sqlite() as conn:
        row = conn.execute(
            "SELECT payload_json FROM chief_pod_artifacts WHERE project_id = ? AND artifact_key = ?",
            (project_id, artifact_key),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_chief_pod_artifact_records(
    project_id: str,
    *,
    pod_key: str | None = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    if storage_backend() == "postgres":
        from db import postgres

        where = {"project_id": project_id}
        if pod_key:
            where["pod_key"] = pod_key
        records = postgres.list_typed_jsonb_records("chief_pod_artifacts", where=where, limit=limit)
    elif storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("chief_pod_artifacts", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM chief_pod_artifacts WHERE project_id = ?"
        params: list[Any] = [project_id]
        if pod_key:
            query += " AND pod_key = ?"
            params.append(pod_key)
        query += " ORDER BY created_at DESC, artifact_key DESC LIMIT ?"
        params.append(limit)
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if pod_key:
        records = [record for record in records if record.get("podKey") == pod_key]
    return sorted(records, key=lambda item: item.get("createdAt") or "", reverse=True)[:limit]


def upsert_pod_ai_review_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a Pod AI Reviewer output as a first-class audit record."""
    now = datetime.now(timezone.utc).isoformat()
    review_id = record.get("reviewId") or record.get("review_id") or f"pod-ai-review:{record.get('podKey') or record.get('pod_key') or 'pod'}:{uuid4()}"
    normalized = {
        **record,
        "schemaVersion": record.get("schemaVersion") or "realytics-pod-ai-review-v1",
        "projectId": project_id,
        "reviewId": str(review_id),
        "podKey": record.get("podKey") or record.get("pod_key"),
        "sourceArtifactKey": record.get("sourceArtifactKey") or record.get("source_artifact_key"),
        "agentId": record.get("agentId") or record.get("agent_id") or "PodAIReviewerAgent",
        "reviewStatus": record.get("reviewStatus") or record.get("review_status") or "warning",
        "agentRunId": record.get("agentRunId") or (((record.get("agentPlatform") or {}).get("durableRuntime") or {}).get("agentRunId")),
        "aiBrainUsedEventId": record.get("aiBrainUsedEventId") or next(
            iter((((record.get("agentPlatform") or {}).get("durableRuntime") or {}).get("brainUsageEventIds") or [])),
            None,
        ),
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }

    if storage_backend() == "postgres":
        _upsert_postgres_typed_record(
            store_key="pod_ai_reviews",
            project_id=project_id,
            record_id=normalized["reviewId"],
            table="pod_ai_reviews",
            values={
                "project_id": project_id,
                "review_id": normalized["reviewId"],
                "pod_key": normalized.get("podKey"),
                "source_artifact_key": normalized.get("sourceArtifactKey"),
                "agent_id": normalized.get("agentId"),
                "review_status": normalized.get("reviewStatus"),
                "ai_brain_used_event_id": normalized.get("aiBrainUsedEventId"),
                "created_at": normalized.get("createdAt"),
                "updated_at": normalized.get("updatedAt"),
            },
            normalized=normalized,
        )
        return normalized
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("pod_ai_reviews", {})
        state["pod_ai_reviews"].setdefault(project_id, {})[normalized["reviewId"]] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO pod_ai_reviews(
                project_id, review_id, pod_key, source_artifact_key, agent_id,
                review_status, ai_brain_used_event_id, created_at, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, review_id) DO UPDATE SET
                pod_key=excluded.pod_key,
                source_artifact_key=excluded.source_artifact_key,
                agent_id=excluded.agent_id,
                review_status=excluded.review_status,
                ai_brain_used_event_id=excluded.ai_brain_used_event_id,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                normalized["reviewId"],
                normalized.get("podKey"),
                normalized.get("sourceArtifactKey"),
                normalized.get("agentId"),
                normalized.get("reviewStatus"),
                normalized.get("aiBrainUsedEventId"),
                normalized.get("createdAt"),
                normalized.get("updatedAt"),
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def get_pod_ai_review_record(project_id: str, review_id: str) -> Dict[str, Any] | None:
    if storage_backend() == "postgres":
        from db import postgres

        return postgres.get_typed_jsonb_record(
            "pod_ai_reviews", {"project_id": project_id, "review_id": review_id}
        )
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        return state.get("pod_ai_reviews", {}).get(project_id, {}).get(review_id)
    with _connect_sqlite() as conn:
        row = conn.execute(
            "SELECT payload_json FROM pod_ai_reviews WHERE project_id = ? AND review_id = ?",
            (project_id, review_id),
        ).fetchone()
    return json.loads(row["payload_json"]) if row else None


def list_pod_ai_review_records(
    project_id: str,
    *,
    pod_key: str | None = None,
    review_status: str | None = None,
    source_artifact_key: str | None = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    if storage_backend() == "postgres":
        from db import postgres

        where = {"project_id": project_id}
        if pod_key:
            where["pod_key"] = pod_key
        if review_status:
            where["review_status"] = review_status
        if source_artifact_key:
            where["source_artifact_key"] = source_artifact_key
        records = postgres.list_typed_jsonb_records("pod_ai_reviews", where=where, limit=limit)
    elif storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("pod_ai_reviews", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM pod_ai_reviews WHERE project_id = ?"
        params: list[Any] = [project_id]
        if pod_key:
            query += " AND pod_key = ?"
            params.append(pod_key)
        if review_status:
            query += " AND review_status = ?"
            params.append(review_status)
        if source_artifact_key:
            query += " AND source_artifact_key = ?"
            params.append(source_artifact_key)
        query += " ORDER BY updated_at DESC, review_id DESC LIMIT ?"
        params.append(limit)
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if pod_key:
        records = [record for record in records if record.get("podKey") == pod_key]
    if review_status:
        records = [record for record in records if record.get("reviewStatus") == review_status]
    if source_artifact_key:
        records = [record for record in records if record.get("sourceArtifactKey") == source_artifact_key]
    return sorted(records, key=lambda item: item.get("updatedAt") or item.get("createdAt") or "", reverse=True)[:limit]


def upsert_agent_handoff_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a typed AgentHandoffEnvelope as an auditable communication row."""
    now = datetime.now(timezone.utc).isoformat()
    handoff_id = record.get("handoffId") or record.get("handoff_id") or f"handoff:{uuid4()}"
    normalized = {
        **record,
        "schemaVersion": record.get("schemaVersion") or "realytics-agent-handoff-v1",
        "projectId": str(project_id),
        "handoffId": str(handoff_id),
        "runId": record.get("runId") or record.get("run_id"),
        "fromAgent": record.get("fromAgent") or record.get("from_agent"),
        "toAgent": record.get("toAgent") or record.get("to_agent"),
        "handoffType": record.get("handoffType") or record.get("handoff_type") or "pod_artifact_ready",
        "status": record.get("status") or "created",
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }
    outbox = _new_runtime_outbox_payload(
        tenant_id=str(record.get("tenantId") or "legacy"),
        project_id=str(project_id),
        aggregate_type="agent_handoff",
        aggregate_id=normalized["handoffId"],
        event_type="agent_handoff_upserted",
        updated_at=now,
        payload={
            "projectId": str(project_id),
            "handoffId": normalized["handoffId"],
            "runId": normalized.get("runId"),
            "fromAgent": normalized.get("fromAgent"),
            "toAgent": normalized.get("toAgent"),
        },
    )
    if storage_backend() == "postgres":
        from db import postgres

        postgres.upsert_typed_jsonb_record_with_outbox(
            "agent_handoffs",
            {
                "project_id": str(project_id),
                "handoff_id": normalized["handoffId"],
                "run_id": normalized.get("runId"),
                "from_agent": normalized.get("fromAgent"),
                "to_agent": normalized.get("toAgent"),
                "handoff_type": normalized.get("handoffType"),
                "status": normalized.get("status"),
                "created_at": normalized.get("createdAt"),
                "updated_at": normalized.get("updatedAt"),
            },
            normalized,
            _runtime_outbox_values(outbox),
            outbox,
        )
        state = _load_json_state() or default_state()
        state.setdefault("agent_handoffs", {}).setdefault(str(project_id), {})[normalized["handoffId"]] = normalized
        state.setdefault("runtime_outbox_events", {})[outbox["eventId"]] = outbox
        _save_json_state_from_state(state)
        return normalized
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("agent_handoffs", {}).setdefault(str(project_id), {})[normalized["handoffId"]] = normalized
        state.setdefault("runtime_outbox_events", {})[outbox["eventId"]] = outbox
        _save_json_state_from_state(state)
        return normalized
    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO agent_handoffs(
                project_id, handoff_id, run_id, from_agent, to_agent, handoff_type,
                status, created_at, updated_at, payload_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, handoff_id) DO UPDATE SET
                run_id=excluded.run_id,
                from_agent=excluded.from_agent,
                to_agent=excluded.to_agent,
                handoff_type=excluded.handoff_type,
                status=excluded.status,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                str(project_id),
                normalized["handoffId"],
                normalized.get("runId"),
                normalized.get("fromAgent"),
                normalized.get("toAgent"),
                normalized.get("handoffType"),
                normalized.get("status"),
                normalized.get("createdAt"),
                normalized.get("updatedAt"),
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        _insert_sqlite_runtime_outbox(conn, outbox)
        conn.commit()
    return normalized


def list_agent_handoff_records(
    project_id: str,
    *,
    from_agent: str | None = None,
    to_agent: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    if storage_backend() == "postgres":
        from db import postgres

        where: Dict[str, Any] = {"project_id": str(project_id)}
        if from_agent:
            where["from_agent"] = from_agent
        if to_agent:
            where["to_agent"] = to_agent
        if status:
            where["status"] = status
        return postgres.list_typed_jsonb_records("agent_handoffs", where=where, limit=limit)
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("agent_handoffs", {}).get(str(project_id), {}).values())
    else:
        query = "SELECT payload_json FROM agent_handoffs WHERE project_id = ?"
        params: list[Any] = [str(project_id)]
        for column, value in (("from_agent", from_agent), ("to_agent", to_agent), ("status", status)):
            if value:
                query += f" AND {column} = ?"
                params.append(value)
        query += " ORDER BY created_at DESC, handoff_id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if from_agent:
        records = [row for row in records if row.get("fromAgent") == from_agent]
    if to_agent:
        records = [row for row in records if row.get("toAgent") == to_agent]
    if status:
        records = [row for row in records if row.get("status") == status]
    return sorted(records, key=lambda item: item.get("createdAt") or "", reverse=True)[:limit]


def _normalize_deal_state_record(
    project_id: str,
    record: Dict[str, Any],
    *,
    now: str | None = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc).isoformat()
    record_type = record.get("recordType") or record.get("record_type") or "metric"
    record_id = record.get("recordId") or record.get("record_id") or f"{record_type}:{record.get('key') or uuid4()}"
    return {
        **record,
        "schemaVersion": record.get("schemaVersion") or "realytics-deal-state-record-v1",
        "projectId": project_id,
        "recordId": str(record_id),
        "recordType": record_type,
        "key": record.get("key") or record.get("metricKey") or record.get("riskCode") or record.get("evidenceId") or record.get("assumptionId"),
        "value": record.get("value"),
        "unit": record.get("unit"),
        "sourceType": record.get("sourceType") or record.get("source_type") or "calculation_run",
        "sourceLocator": record.get("sourceLocator") or record.get("source_locator") or {},
        "calculationRunId": record.get("calculationRunId") or record.get("calculation_run_id"),
        "confidence": record.get("confidence"),
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }


def _deal_state_typed_values(project_id: str, normalized: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "project_id": project_id,
        "record_id": normalized["recordId"],
        "record_type": normalized.get("recordType"),
        "key": normalized.get("key"),
        "calculation_run_id": normalized.get("calculationRunId"),
        "source_type": normalized.get("sourceType"),
        "review_status": normalized.get("reviewStatus"),
        "updated_at": normalized.get("updatedAt"),
    }


def upsert_deal_state_records(
    project_id: str,
    records: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Persist a materialized deal-state batch in one backend transaction."""
    if not records:
        return []
    now = datetime.now(timezone.utc).isoformat()
    normalized_rows = [
        _normalize_deal_state_record(project_id, record, now=now) for record in records
    ]
    if storage_backend() == "postgres":
        from db import postgres

        state = _load_json_state() or default_state()
        project_records = state.setdefault("deal_state_records", {}).setdefault(project_id, {})
        for normalized in normalized_rows:
            project_records[normalized["recordId"]] = normalized
        _save_json_state_from_state(state)
        postgres.upsert_typed_jsonb_records(
            "deal_state_records",
            [
                (_deal_state_typed_values(project_id, normalized), normalized)
                for normalized in normalized_rows
            ],
        )
        return normalized_rows
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        project_records = state.setdefault("deal_state_records", {}).setdefault(project_id, {})
        for normalized in normalized_rows:
            project_records[normalized["recordId"]] = normalized
        _save_json_state_from_state(state)
        return normalized_rows

    with _connect_sqlite() as conn:
        conn.executemany(
            """
            INSERT INTO deal_state_records(
                project_id, record_id, record_type, key, calculation_run_id,
                source_type, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, record_id) DO UPDATE SET
                record_type=excluded.record_type,
                key=excluded.key,
                calculation_run_id=excluded.calculation_run_id,
                source_type=excluded.source_type,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            [
                (
                    project_id,
                    normalized["recordId"],
                    normalized["recordType"],
                    normalized.get("key"),
                    normalized.get("calculationRunId"),
                    normalized.get("sourceType"),
                    normalized.get("updatedAt"),
                    json.dumps(normalized, ensure_ascii=False),
                )
                for normalized in normalized_rows
            ],
        )
        conn.commit()
    return normalized_rows


def upsert_deal_state_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist one Evidence/Assumption/Metric/RiskIssue as a queryable first-class row."""
    return upsert_deal_state_records(project_id, [record])[0]


def _deal_state_record_has_source_locator(record: Dict[str, Any]) -> bool:
    """True when the record carries a usable source locator (any non-empty value)."""
    locator = record.get("sourceLocator") or record.get("source_locator")
    if not isinstance(locator, dict):
        return False
    return any(value not in (None, "", [], {}) for value in locator.values())


def list_deal_state_records(
    project_id: str,
    *,
    record_type: str | None = None,
    calculation_run_id: str | None = None,
    review_status: str | None = None,
    missing_locator: bool | None = None,
    latest_only: bool = False,
    limit: int = 200,
) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit or 200), 1000))
    if storage_backend() == "postgres":
        from db import postgres

        where: Dict[str, Any] = {"project_id": project_id}
        if record_type:
            where["record_type"] = record_type
        if calculation_run_id:
            where["calculation_run_id"] = calculation_run_id
        if review_status and review_status != "unreviewed":
            where["review_status"] = review_status
        records = postgres.list_typed_jsonb_records("deal_state_records", where=where, limit=limit)
    elif storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("deal_state_records", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM deal_state_records WHERE project_id = ?"
        params: list[Any] = [project_id]
        if record_type:
            query += " AND record_type = ?"
            params.append(record_type)
        if calculation_run_id:
            query += " AND calculation_run_id = ?"
            params.append(calculation_run_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if record_type:
        records = [record for record in records if record.get("recordType") == record_type]
    if calculation_run_id:
        records = [record for record in records if record.get("calculationRunId") == calculation_run_id]
    if review_status is not None:
        # "unreviewed" matches records with no explicit reviewStatus set yet.
        if review_status == "unreviewed":
            records = [r for r in records if not r.get("reviewStatus")]
        else:
            records = [r for r in records if r.get("reviewStatus") == review_status]
    if missing_locator is not None:
        records = [
            r for r in records
            if _deal_state_record_has_source_locator(r) != bool(missing_locator)
        ]
    if latest_only:
        # A deal-state record is an audit trail, but reviewer screens need the
        # latest actionable row per business key.  Materializers intentionally
        # retain historical rows for audit, so collapse only at read time.
        source_priority = {
            "risk_register": 3,
            "chief_pod_artifact": 2,
            "evidence_graph": 1,
        }
        latest_by_key: Dict[str, Dict[str, Any]] = {}
        for record in records:
            key = str(record.get("key") or record.get("recordId") or "")
            current = latest_by_key.get(key)
            if current is None:
                latest_by_key[key] = record
                continue
            current_rank = source_priority.get(str(current.get("sourceType") or ""), 0)
            candidate_rank = source_priority.get(str(record.get("sourceType") or ""), 0)
            current_stamp = str(current.get("updatedAt") or current.get("createdAt") or "")
            candidate_stamp = str(record.get("updatedAt") or record.get("createdAt") or "")
            if candidate_rank > current_rank or (candidate_rank == current_rank and candidate_stamp > current_stamp):
                latest_by_key[key] = record
        records = list(latest_by_key.values())
    return sorted(records, key=lambda item: item.get("updatedAt") or "", reverse=True)[:limit]


def upsert_calculation_run_record(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist deterministic calculation output with input evidence and assumptions."""
    now = datetime.now(timezone.utc).isoformat()
    engine_key = record.get("engineKey") or record.get("engine_key") or record.get("calculationType") or "calculation"
    run_id = record.get("calculationRunId") or record.get("calculation_run_id") or f"calc:{engine_key}:{uuid4()}"
    run_source_locator = record.get("sourceLocator") or record.get("source_locator") or {
        "calculationRunId": str(run_id),
        "engineKey": engine_key,
    }
    normalized = {
        **record,
        "schemaVersion": record.get("schemaVersion") or "realytics-calculation-run-v2",
        "projectId": project_id,
        "calculationRunId": str(run_id),
        "engineKey": engine_key,
        "engineVersion": record.get("engineVersion") or record.get("engine_version") or "realytics-calculation-engine-v1",
        "status": record.get("status") or "completed",
        "inputEvidenceIds": record.get("inputEvidenceIds") or record.get("input_evidence_ids") or [],
        "assumptionIds": record.get("assumptionIds") or record.get("assumption_ids") or [],
        "outputMetrics": record.get("outputMetrics") or record.get("output_metrics") or [],
        "inputSnapshot": record.get("inputSnapshot") or record.get("input_snapshot") or {},
        # Keep the deterministic engine result for report/UI readback. The
        # registry metrics remain the queryable index; this snapshot preserves
        # monthly rows and matrix details without recomputation.
        "resultSnapshot": record.get("resultSnapshot") or record.get("result_snapshot") or {},
        "riskIssues": record.get("riskIssues") or record.get("risk_issues") or [],
        "riskIssueIds": record.get("riskIssueIds") or record.get("risk_issue_ids") or _risk_issue_ids(record),
        "scenario": record.get("scenario") or record.get("scenarioContext") or record.get("scenario_context") or {},
        "artifactRef": record.get("artifactRef") or record.get("artifact_ref") or {},
        "sourceLocator": run_source_locator,
        "createdAt": record.get("createdAt") or record.get("created_at") or now,
        "updatedAt": now,
    }
    outbox = _new_runtime_outbox_payload(
        tenant_id=str(record.get("tenantId") or "legacy"),
        project_id=str(project_id),
        aggregate_type="calculation_run",
        aggregate_id=normalized["calculationRunId"],
        event_type="calculation_run_upserted",
        updated_at=now,
        payload={
            "projectId": str(project_id),
            "calculationRunId": normalized["calculationRunId"],
            "engineKey": normalized.get("engineKey"),
            "status": normalized.get("status"),
        },
    )

    if storage_backend() == "postgres":
        from db import postgres

        postgres.upsert_typed_jsonb_record_with_outbox(
            "calculation_runs",
            {
                "project_id": project_id,
                "calculation_run_id": normalized["calculationRunId"],
                "engine_key": normalized.get("engineKey"),
                "engine_version": normalized.get("engineVersion"),
                "status": normalized.get("status"),
                "created_at": normalized.get("createdAt"),
                "updated_at": normalized.get("updatedAt"),
            },
            normalized,
            _runtime_outbox_values(outbox),
            outbox,
        )
        state = _load_json_state() or default_state()
        state.setdefault("calculation_runs", {}).setdefault(str(project_id), {})[
            normalized["calculationRunId"]
        ] = normalized
        state.setdefault("runtime_outbox_events", {})[outbox["eventId"]] = outbox
        _save_json_state_from_state(state)
    elif storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("calculation_runs", {})
        state["calculation_runs"].setdefault(project_id, {})[normalized["calculationRunId"]] = normalized
        state.setdefault("runtime_outbox_events", {})[outbox["eventId"]] = outbox
        _save_json_state_from_state(state)
    else:
        with _connect_sqlite() as conn:
            conn.execute(
                """
                INSERT INTO calculation_runs(
                    project_id, calculation_run_id, engine_key, engine_version,
                    status, created_at, updated_at, payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, calculation_run_id) DO UPDATE SET
                    engine_key=excluded.engine_key,
                    engine_version=excluded.engine_version,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    project_id,
                    normalized["calculationRunId"],
                    normalized["engineKey"],
                    normalized["engineVersion"],
                    normalized["status"],
                    normalized["createdAt"],
                    normalized["updatedAt"],
                    json.dumps(normalized, ensure_ascii=False),
                ),
            )
            _insert_sqlite_runtime_outbox(conn, outbox)
            conn.commit()

    _materialize_calculation_run_record(project_id, normalized)
    return normalized


def materialize_calculation_run_record(project_id: str, calculation_run_id: str) -> list[Dict[str, Any]]:
    """Idempotently rebuild Deal State projections from a durable CalculationRun."""
    run = next(
        (
            item for item in list_calculation_run_records(project_id, limit=500)
            if str(item.get("calculationRunId")) == str(calculation_run_id)
        ),
        None,
    )
    if run is None:
        raise KeyError(f"CalculationRun not found: {calculation_run_id}")
    return _materialize_calculation_run_record(project_id, run)


def _materialize_calculation_run_record(
    project_id: str,
    normalized: Dict[str, Any],
) -> list[Dict[str, Any]]:
    materialized_records: list[Dict[str, Any]] = []
    for metric in normalized.get("outputMetrics") or []:
        if isinstance(metric, dict):
            key = metric.get("key") or metric.get("metricKey")
            metric_source_locator = metric.get("sourceLocator") or metric.get("source_locator") or run_source_locator
            materialized_records.append(
                {
                    **metric,
                    "recordId": metric.get("recordId") or f"metric:{normalized['calculationRunId']}:{key or uuid4()}",
                    "recordType": "metric",
                    "calculationRunId": normalized["calculationRunId"],
                    "sourceType": "calculation_run",
                    "sourceLocator": metric_source_locator,
                }
            )
    for risk in normalized.get("riskIssues") or []:
        if isinstance(risk, dict):
            code = risk.get("code") or risk.get("riskCode")
            risk_locators = risk.get("sourceLocators") or risk.get("source_locators") or []
            risk_source_locator = (
                risk.get("sourceLocator")
                or risk.get("source_locator")
                or (risk_locators[0] if isinstance(risk_locators, list) and risk_locators else None)
                or run_source_locator
            )
            materialized_records.append(
                {
                    **risk,
                    "recordId": risk.get("recordId") or f"risk:{normalized['calculationRunId']}:{code or uuid4()}",
                    "recordType": "risk_issue",
                    "key": code,
                    "calculationRunId": normalized["calculationRunId"],
                    "sourceType": "calculation_run",
                    "sourceLocator": risk_source_locator,
                }
            )
    for assumption_id in normalized.get("assumptionIds") or []:
        materialized_records.append(
            {
                "recordId": f"assumption:{normalized['calculationRunId']}:{assumption_id}",
                "recordType": "assumption",
                "key": str(assumption_id),
                "calculationRunId": normalized["calculationRunId"],
                "sourceType": "calculation_run",
                "sourceLocator": {"calculationRunId": normalized["calculationRunId"]},
            }
        )
    for evidence_id in normalized.get("inputEvidenceIds") or []:
        materialized_records.append(
            {
                "recordId": f"evidence:{normalized['calculationRunId']}:{evidence_id}",
                "recordType": "evidence",
                "key": str(evidence_id),
                "calculationRunId": normalized["calculationRunId"],
                "sourceType": "calculation_run",
                "sourceLocator": {"calculationRunId": normalized["calculationRunId"]},
            }
        )
    if materialized_records:
        upsert_deal_state_records(project_id, materialized_records)
    return materialized_records


def _risk_issue_ids(record: Dict[str, Any]) -> list[str]:
    """Derive stable risk identifiers without replacing the full risk payload."""
    identifiers: list[str] = []
    for risk in record.get("riskIssues") or record.get("risk_issues") or []:
        if isinstance(risk, dict):
            value = risk.get("riskIssueId") or risk.get("riskId") or risk.get("code") or risk.get("riskCode")
        else:
            value = risk
        if value not in (None, "") and str(value) not in identifiers:
            identifiers.append(str(value))
    return identifiers


def list_calculation_run_records(
    project_id: str,
    *,
    engine_key: str | None = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 500))
    if storage_backend() == "postgres":
        from db import postgres

        where: Dict[str, Any] = {"project_id": project_id}
        if engine_key:
            where["engine_key"] = engine_key
        records = postgres.list_typed_jsonb_records("calculation_runs", where=where, limit=limit)
    elif storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("calculation_runs", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM calculation_runs WHERE project_id = ?"
        params: list[Any] = [project_id]
        if engine_key:
            query += " AND engine_key = ?"
            params.append(engine_key)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if engine_key:
        records = [record for record in records if record.get("engineKey") == engine_key]
    return sorted(records, key=lambda item: item.get("createdAt") or "", reverse=True)[:limit]


def upsert_recalculation_event_record(project_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an Event Bus recalculation trigger as queryable deal-room history."""
    now = datetime.now(timezone.utc).isoformat()
    target_pods = event.get("targetPods") if isinstance(event.get("targetPods"), list) else []
    target_pod = event.get("targetPod") or event.get("target_pod") or ",".join(str(item) for item in target_pods)
    event_id = (
        event.get("eventId")
        or event.get("event_id")
        or f"recalc:{event.get('trigger') or 'event'}:{event.get('sourcePod') or 'pod'}:{target_pod or 'targets'}:{uuid4()}"
    )
    normalized = {
        **event,
        "schemaVersion": event.get("schemaVersion") or "realytics-recalculation-event-v1",
        "projectId": project_id,
        "eventId": str(event_id),
        "runId": event.get("runId") or event.get("run_id"),
        "sourcePod": event.get("sourcePod") or event.get("source_pod"),
        "targetPod": target_pod or None,
        "targetPods": target_pods or event.get("targetPods") or [],
        "trigger": event.get("trigger") or event.get("triggerName") or event.get("trigger_name"),
        "eventType": event.get("eventType") or event.get("event_type") or "recalculation_triggered",
        "status": event.get("status") or "pending_recalculation",
        "createdAt": event.get("createdAt") or event.get("created_at") or event.get("timestamp") or now,
        "updatedAt": now,
    }

    if storage_backend() == "postgres":
        _upsert_postgres_typed_record(
            store_key="recalculation_events",
            project_id=project_id,
            record_id=normalized["eventId"],
            table="recalculation_events",
            values={
                "project_id": project_id,
                "event_id": normalized["eventId"],
                "run_id": normalized.get("runId"),
                "source_pod": normalized.get("sourcePod"),
                "target_pod": normalized.get("targetPod"),
                "trigger_name": normalized.get("trigger"),
                "event_type": normalized.get("eventType"),
                "status": normalized.get("status"),
                "created_at": normalized.get("createdAt"),
                "updated_at": normalized.get("updatedAt"),
            },
            normalized=normalized,
        )
        return normalized
    if storage_backend() == "json":
        state = _load_json_state() or default_state()
        state.setdefault("recalculation_events", {})
        state["recalculation_events"].setdefault(project_id, {})[normalized["eventId"]] = normalized
        _save_json_state_from_state(state)
        return normalized

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO recalculation_events(
                project_id, event_id, run_id, source_pod, target_pod, trigger_name,
                event_type, status, created_at, updated_at, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, event_id) DO UPDATE SET
                run_id=excluded.run_id,
                source_pod=excluded.source_pod,
                target_pod=excluded.target_pod,
                trigger_name=excluded.trigger_name,
                event_type=excluded.event_type,
                status=excluded.status,
                updated_at=excluded.updated_at,
                payload_json=excluded.payload_json
            """,
            (
                project_id,
                normalized["eventId"],
                normalized.get("runId"),
                normalized.get("sourcePod"),
                normalized.get("targetPod"),
                normalized.get("trigger"),
                normalized.get("eventType"),
                normalized.get("status"),
                normalized.get("createdAt"),
                normalized.get("updatedAt"),
                json.dumps(normalized, ensure_ascii=False),
            ),
        )
        conn.commit()
    return normalized


def list_recalculation_event_records(
    project_id: str,
    *,
    run_id: str | None = None,
    source_pod: str | None = None,
    target_pod: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[Dict[str, Any]]:
    if storage_backend() == "postgres":
        from db import postgres

        where: Dict[str, Any] = {"project_id": project_id}
        if run_id:
            where["run_id"] = run_id
        if source_pod:
            where["source_pod"] = source_pod
        if status:
            where["status"] = status
        records = postgres.list_typed_jsonb_records("recalculation_events", where=where, limit=limit)
    elif storage_backend() == "json":
        state = _load_json_state() or default_state()
        records = list(state.get("recalculation_events", {}).get(project_id, {}).values())
    else:
        query = "SELECT payload_json FROM recalculation_events WHERE project_id = ?"
        params: list[Any] = [project_id]
        if run_id:
            query += " AND run_id = ?"
            params.append(run_id)
        if source_pod:
            query += " AND source_pod = ?"
            params.append(source_pod)
        if target_pod:
            query += " AND target_pod LIKE ?"
            params.append(f"%{target_pod}%")
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, event_id DESC LIMIT ?"
        params.append(limit)
        with _connect_sqlite() as conn:
            records = [json.loads(row["payload_json"]) for row in conn.execute(query, params)]
    if run_id:
        records = [record for record in records if record.get("runId") == run_id]
    if source_pod:
        records = [record for record in records if record.get("sourcePod") == source_pod]
    if target_pod:
        records = [record for record in records if target_pod in str(record.get("targetPod") or "")]
    if status:
        records = [record for record in records if record.get("status") == status]
    return sorted(records, key=lambda item: item.get("createdAt") or "", reverse=True)[:limit]


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

    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
        state = _load_json_state() or default_state()
        return state.get("identity", {}).get("users", {}).get(user_id)
    with _connect_sqlite() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return _user_row_to_record(row) if row else None


def get_user_session(session_id: str) -> Dict[str, Any] | None:
    if _use_document_store():
        state = _load_json_state() or default_state()
        return state.get("identity", {}).get("sessions", {}).get(session_id)
    with _connect_sqlite() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return _session_row_to_record(row) if row else None


def list_users(*, tenant_id: str | None = None, limit: int = 100) -> list[Dict[str, Any]]:
    limit = max(1, min(int(limit or 100), 1000))
    if _use_document_store():
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


def upsert_user_ai_profile(record: Dict[str, Any]) -> Dict[str, Any]:
    """Store one user's BYOK/default AI profile.

    The API key is encrypted for local persistence and never returned by read
    endpoints. Production deployments should replace this local encryption with
    a secret-manager backed reference.
    """
    now = datetime.now(timezone.utc).isoformat()
    user_id = str(record.get("userId") or record.get("user_id") or "")
    if not user_id:
        raise ValueError("userId is required")
    existing = get_user_ai_profile(user_id, include_secret=True) or {}
    provider = str(record.get("provider") or existing.get("provider") or "gemini").lower()
    model = record.get("model") or existing.get("model") or _default_model_for_provider(provider)
    raw_key = record.get("apiKey") or record.get("api_key")
    encrypted_key = existing.get("apiKeyEncrypted")
    masked_key = existing.get("apiKeyMasked")
    if raw_key:
        encrypted_key = _encrypt_secret(str(raw_key))
        masked_key = _mask_secret(str(raw_key))
    elif record.get("clearApiKey"):
        encrypted_key = None
        masked_key = None

    profile = {
        "userId": user_id,
        "tenantId": record.get("tenantId") or record.get("tenant_id") or existing.get("tenantId"),
        "provider": provider,
        "model": model,
        "status": record.get("status") or existing.get("status") or "active",
        "apiKeyEncrypted": encrypted_key,
        "apiKeyMasked": masked_key,
        "apiKeyConfigured": bool(encrypted_key),
        "allowSystemFallback": bool(record.get("allowSystemFallback", existing.get("allowSystemFallback", True))),
        "fallbackProvider": record.get("fallbackProvider") or existing.get("fallbackProvider") or "gemini",
        "fallbackModel": record.get("fallbackModel") or existing.get("fallbackModel") or "gemini-2.5-flash",
        "createdAt": existing.get("createdAt") or record.get("createdAt") or now,
        "updatedAt": now,
        "metadata": record.get("metadata") or existing.get("metadata") or {},
    }

    if _use_document_store():
        state = _load_json_state() or default_state()
        state.setdefault("user_ai_profiles", {})[user_id] = profile
        _save_json_state_from_state(state)
        return _sanitize_user_ai_profile(profile)

    with _connect_sqlite() as conn:
        conn.execute(
            """
            INSERT INTO user_ai_profiles(user_id, tenant_id, provider, model, status, api_key_encrypted,
                api_key_masked, allow_system_fallback, fallback_provider, fallback_model, created_at, updated_at, metadata_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                tenant_id=excluded.tenant_id,
                provider=excluded.provider,
                model=excluded.model,
                status=excluded.status,
                api_key_encrypted=excluded.api_key_encrypted,
                api_key_masked=excluded.api_key_masked,
                allow_system_fallback=excluded.allow_system_fallback,
                fallback_provider=excluded.fallback_provider,
                fallback_model=excluded.fallback_model,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                profile["userId"],
                profile["tenantId"],
                profile["provider"],
                profile["model"],
                profile["status"],
                profile["apiKeyEncrypted"],
                profile["apiKeyMasked"],
                1 if profile["allowSystemFallback"] else 0,
                profile["fallbackProvider"],
                profile["fallbackModel"],
                profile["createdAt"],
                profile["updatedAt"],
                json.dumps(profile["metadata"], ensure_ascii=False),
            ),
        )
        conn.commit()
    return _sanitize_user_ai_profile(profile)


def get_user_ai_profile(user_id: str, *, include_secret: bool = False) -> Dict[str, Any] | None:
    if not user_id:
        return None
    if _use_document_store():
        state = _load_json_state() or default_state()
        profile = state.get("user_ai_profiles", {}).get(user_id)
    else:
        with _connect_sqlite() as conn:
            row = conn.execute("SELECT * FROM user_ai_profiles WHERE user_id = ?", (str(user_id),)).fetchone()
        profile = _user_ai_profile_row_to_record(row) if row else None
    if not profile:
        return None
    if include_secret:
        return profile
    return _sanitize_user_ai_profile(profile)


def delete_user_ai_profile(user_id: str) -> bool:
    if not user_id:
        return False
    if _use_document_store():
        state = _load_json_state() or default_state()
        removed = state.setdefault("user_ai_profiles", {}).pop(user_id, None) is not None
        _save_json_state_from_state(state)
        return removed
    with _connect_sqlite() as conn:
        cur = conn.execute("DELETE FROM user_ai_profiles WHERE user_id = ?", (str(user_id),))
        conn.commit()
        return cur.rowcount > 0


def decrypt_user_ai_api_key(user_id: str) -> str | None:
    profile = get_user_ai_profile(user_id, include_secret=True)
    encrypted = profile.get("apiKeyEncrypted") if profile else None
    if not encrypted:
        return None
    return _decrypt_secret(str(encrypted))


def _default_model_for_provider(provider: str) -> str:
    return {
        "gemini": "gemini-2.5-flash",
        "openai": "gpt-4o",
        "anthropic": "claude-sonnet-4-6",
        "groq": "llama-3.3-70b-versatile",
        "deepseek": "deepseek-chat",
        "local": "qwen2.5",
    }.get(provider, "gemini-2.5-flash")


def _sanitize_user_ai_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in profile.items()
        if key not in {"apiKeyEncrypted"}
    }


def _mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return f"{secret[:2]}***{secret[-2:]}"
    return f"{secret[:4]}***{secret[-4:]}"


def _fernet():
    from cryptography.fernet import Fernet
    secret = (
        os.environ.get("REALYTICS_SECRET_KEY")
        or os.environ.get("REALYTICS_AI_SECRET_KEY")
        or "realytics-local-pilot-secret-change-before-production"
    )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def _decrypt_secret(ciphertext: str) -> str | None:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


def _load_json_state() -> Dict[str, Any] | None:
    if storage_backend() == "postgres":
        from db import postgres

        document = postgres.load_state_document()
        return _normalize_state(document) if document else None
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
    chief_pod_artifacts: Dict[str, Any],
    prompt_artifacts: Dict[str, Any],
    assumption_overrides: Dict[str, Any],
    restructuring_cures: Dict[str, Any],
    project_snapshots: Dict[str, Any],
    user_activity_logs: list[Dict[str, Any]],
    identity: Dict[str, Any] | None = None,
    glossary_terms: Dict[str, Any] | None = None,
    glossary_term_candidates: Dict[str, Any] | None = None,
    user_ai_profiles: Dict[str, Any] | None = None,
    recalculation_events: Dict[str, Any] | None = None,
    calculation_runs: Dict[str, Any] | None = None,
    deal_state_records: Dict[str, Any] | None = None,
    pod_ai_reviews: Dict[str, Any] | None = None,
    legal_reviews: Dict[str, Any] | None = None,
    permit_reviews: Dict[str, Any] | None = None,
    agent_handoffs: Dict[str, Any] | None = None,
    agent_runs: Dict[str, Any] | None = None,
    agent_run_steps: Dict[str, Any] | None = None,
    brain_usage_events: Dict[str, Any] | None = None,
    operational_stores: Dict[str, Dict[str, Any]] | None = None,
) -> None:
    path = state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    existing_state = _load_json_state() or default_state()
    payload_data = {
        "version": STATE_VERSION,
        "project_id_counter": project_id_counter,
        "projects": projects,
        "reports": reports,
        "orchestrations": orchestrations,
        "market_evidence": market_evidence,
        "land_evidence": land_evidence,
        "risk_registers": risk_registers,
        "evidence_graphs": evidence_graphs,
        "chief_pod_artifacts": chief_pod_artifacts,
        "pod_ai_reviews": pod_ai_reviews or existing_state.get("pod_ai_reviews", {}),
        "agent_handoffs": agent_handoffs or existing_state.get("agent_handoffs", {}),
        "agent_runs": agent_runs or existing_state.get("agent_runs", {}),
        "agent_run_steps": agent_run_steps or existing_state.get("agent_run_steps", {}),
        "brain_usage_events": brain_usage_events or existing_state.get("brain_usage_events", {}),
        "legal_reviews": legal_reviews or existing_state.get("legal_reviews", {}),
        "permit_reviews": permit_reviews or existing_state.get("permit_reviews", {}),
        "recalculation_events": recalculation_events or existing_state.get("recalculation_events", {}),
        "calculation_runs": calculation_runs or existing_state.get("calculation_runs", {}),
        "deal_state_records": deal_state_records or existing_state.get("deal_state_records", {}),
        "prompt_artifacts": prompt_artifacts,
        "assumption_overrides": assumption_overrides,
        "restructuring_cures": restructuring_cures,
        "glossary_terms": glossary_terms or _load_existing_glossary_terms(),
        "glossary_term_candidates": glossary_term_candidates or _load_existing_glossary_term_candidates(),
        "project_snapshots": project_snapshots,
        "user_activity_logs": user_activity_logs[-MAX_JSON_ACTIVITY_LOGS:],
        "identity": identity or default_state()["identity"],
        "user_ai_profiles": user_ai_profiles or existing_state.get("user_ai_profiles", {}),
    }
    for store_name in _OPERATIONAL_RECORD_SPECS:
        payload_data[store_name] = (operational_stores or {}).get(
            store_name,
            existing_state.get(store_name, {}),
        )
    payload = _normalize_state(payload_data)
    if storage_backend() == "postgres":
        from db import postgres

        postgres.save_state_document(payload)
        return
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
        CREATE TABLE IF NOT EXISTS chief_pod_artifacts (
            project_id TEXT NOT NULL,
            artifact_key TEXT NOT NULL,
            pod_key TEXT,
            artifact_type TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, artifact_key)
        );
        CREATE INDEX IF NOT EXISTS idx_chief_pod_artifacts_project_pod
            ON chief_pod_artifacts(project_id, pod_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_chief_pod_artifacts_project_updated
            ON chief_pod_artifacts(project_id, updated_at);
        CREATE TABLE IF NOT EXISTS pod_ai_reviews (
            project_id TEXT NOT NULL,
            review_id TEXT NOT NULL,
            pod_key TEXT,
            source_artifact_key TEXT,
            agent_id TEXT,
            review_status TEXT,
            ai_brain_used_event_id TEXT,
            created_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, review_id)
        );
        CREATE INDEX IF NOT EXISTS idx_pod_ai_reviews_project_pod
            ON pod_ai_reviews(project_id, pod_key, updated_at);
        CREATE INDEX IF NOT EXISTS idx_pod_ai_reviews_project_status
            ON pod_ai_reviews(project_id, review_status, updated_at);
        CREATE TABLE IF NOT EXISTS agent_handoffs (
            project_id TEXT NOT NULL,
            handoff_id TEXT NOT NULL,
            run_id TEXT,
            from_agent TEXT,
            to_agent TEXT,
            handoff_type TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, handoff_id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_handoffs_project_route
            ON agent_handoffs(project_id, from_agent, to_agent, created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_handoffs_project_status
            ON agent_handoffs(project_id, status, updated_at);
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id TEXT PRIMARY KEY,
            project_id TEXT,
            tenant_id TEXT NOT NULL,
            user_id TEXT,
            request_id TEXT NOT NULL,
            correlation_id TEXT,
            trace_id TEXT,
            agent_id TEXT NOT NULL,
            agent_version TEXT NOT NULL,
            task_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 1,
            cancellation_requested INTEGER NOT NULL DEFAULT 0,
            state_version INTEGER NOT NULL DEFAULT 1,
            lease_owner TEXT,
            lease_expires_at TEXT,
            heartbeat_at TEXT,
            deadline_at TEXT,
            consumed_cost_usd REAL NOT NULL DEFAULT 0,
            consumed_llm_calls INTEGER NOT NULL DEFAULT 0,
            consumed_tool_calls INTEGER NOT NULL DEFAULT 0,
            retry_of_run_id TEXT,
            selected_brain_profile_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            payload_json TEXT NOT NULL,
            UNIQUE (tenant_id, agent_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_runs_project_updated
            ON agent_runs(project_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_status_updated
            ON agent_runs(status, updated_at);
        CREATE TABLE IF NOT EXISTS agent_run_steps (
            step_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            project_id TEXT,
            sequence_no INTEGER NOT NULL,
            stage TEXT NOT NULL,
            step_type TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt_no INTEGER,
            error_code TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            payload_json TEXT NOT NULL,
            UNIQUE (run_id, sequence_no)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run_sequence
            ON agent_run_steps(run_id, sequence_no);
        CREATE TABLE IF NOT EXISTS brain_usage_events (
            event_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            step_id TEXT,
            tenant_id TEXT NOT NULL,
            project_id TEXT,
            agent_id TEXT NOT NULL,
            agent_version TEXT NOT NULL,
            brain_profile_id TEXT,
            provider TEXT,
            model TEXT,
            status TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost_usd REAL,
            latency_ms INTEGER,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_brain_usage_events_run
            ON brain_usage_events(run_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_brain_usage_events_project
            ON brain_usage_events(project_id, occurred_at);
        CREATE TABLE IF NOT EXISTS runtime_outbox_events (
            event_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 8,
            available_at TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            published_at TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_outbox_claim
            ON runtime_outbox_events(status, available_at, lease_expires_at);
        CREATE TABLE IF NOT EXISTS brain_provider_health (
            health_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            brain_profile_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            checked_at TEXT NOT NULL,
            next_check_at TEXT,
            payload_json TEXT NOT NULL,
            UNIQUE(tenant_id, brain_profile_id)
        );
        CREATE TABLE IF NOT EXISTS brain_routing_policies (
            policy_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT,
            version INTEGER NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(tenant_id, user_id, version)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_brain_routing_policy_scope_version
            ON brain_routing_policies(tenant_id, COALESCE(user_id, ''), version);
        CREATE TABLE IF NOT EXISTS brain_routing_events (
            routing_event_id TEXT PRIMARY KEY,
            run_id TEXT,
            request_id TEXT,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            project_id TEXT,
            task_type TEXT NOT NULL,
            policy_id TEXT,
            policy_version INTEGER,
            selected_profile_id TEXT,
            selected_provider TEXT,
            selected_model TEXT,
            blocked INTEGER NOT NULL DEFAULT 0,
            occurred_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_brain_routing_events_run
            ON brain_routing_events(run_id, occurred_at);
        CREATE TABLE IF NOT EXISTS tool_calls (
            tool_call_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            step_id TEXT,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            project_id TEXT,
            tool_id TEXT NOT NULL,
            tool_version TEXT NOT NULL,
            status TEXT NOT NULL,
            side_effect_level TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            state_version INTEGER NOT NULL DEFAULT 1,
            requested_at TEXT NOT NULL,
            completed_at TEXT,
            payload_json TEXT NOT NULL,
            UNIQUE(tenant_id, tool_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id, requested_at);
        CREATE TABLE IF NOT EXISTS learning_candidates (
            candidate_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            project_id TEXT,
            candidate_type TEXT NOT NULL,
            target_key TEXT NOT NULL,
            status TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_evaluations (
            evaluation_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            passed INTEGER NOT NULL,
            evaluated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS learning_promotions (
            decision_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            action TEXT NOT NULL,
            environment TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS coding_pod_jobs (
            job_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            workspace_ref TEXT NOT NULL,
            branch_name TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at TEXT,
            heartbeat_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            state_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(tenant_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_coding_pod_jobs_claim
            ON coding_pod_jobs(status, lease_expires_at, created_at);
        CREATE TABLE IF NOT EXISTS legal_reviews (
            project_id TEXT NOT NULL,
            review_id TEXT NOT NULL,
            status TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, review_id)
        );
        CREATE INDEX IF NOT EXISTS idx_legal_reviews_project_updated
            ON legal_reviews(project_id, updated_at);
        CREATE TABLE IF NOT EXISTS permit_reviews (
            project_id TEXT NOT NULL,
            review_id TEXT NOT NULL,
            status TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, review_id)
        );
        CREATE INDEX IF NOT EXISTS idx_permit_reviews_project_updated
            ON permit_reviews(project_id, updated_at);
        CREATE TABLE IF NOT EXISTS recalculation_events (
            project_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            run_id TEXT,
            source_pod TEXT,
            target_pod TEXT,
            trigger_name TEXT,
            event_type TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_recalculation_events_project_run
            ON recalculation_events(project_id, run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_recalculation_events_project_target
            ON recalculation_events(project_id, target_pod, status, created_at);
        CREATE TABLE IF NOT EXISTS calculation_runs (
            project_id TEXT NOT NULL,
            calculation_run_id TEXT NOT NULL,
            engine_key TEXT,
            engine_version TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, calculation_run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_calculation_runs_project_engine
            ON calculation_runs(project_id, engine_key, created_at);
        CREATE TABLE IF NOT EXISTS deal_state_records (
            project_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            record_type TEXT,
            key TEXT,
            calculation_run_id TEXT,
            source_type TEXT,
            updated_at TEXT,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (project_id, record_id)
        );
        CREATE INDEX IF NOT EXISTS idx_deal_state_records_project_type
            ON deal_state_records(project_id, record_type, updated_at);
        CREATE INDEX IF NOT EXISTS idx_deal_state_records_project_calculation
            ON deal_state_records(project_id, calculation_run_id, record_type);
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
        CREATE TABLE IF NOT EXISTS user_ai_profiles (
            user_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            provider TEXT,
            model TEXT,
            status TEXT,
            api_key_encrypted TEXT,
            api_key_masked TEXT,
            allow_system_fallback INTEGER,
            fallback_provider TEXT,
            fallback_model TEXT,
            created_at TEXT,
            updated_at TEXT,
            metadata_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_user_ai_profiles_tenant
            ON user_ai_profiles(tenant_id, updated_at);
        """
    )
    _ensure_sqlite_column(conn, "agent_runs", "lease_owner", "TEXT")
    _ensure_sqlite_column(conn, "agent_runs", "lease_expires_at", "TEXT")
    _ensure_sqlite_column(conn, "agent_runs", "heartbeat_at", "TEXT")
    _ensure_sqlite_column(conn, "agent_runs", "deadline_at", "TEXT")
    _ensure_sqlite_column(conn, "agent_runs", "consumed_cost_usd", "REAL NOT NULL DEFAULT 0")
    _ensure_sqlite_column(conn, "agent_runs", "consumed_llm_calls", "INTEGER NOT NULL DEFAULT 0")
    _ensure_sqlite_column(conn, "agent_runs", "consumed_tool_calls", "INTEGER NOT NULL DEFAULT 0")
    _ensure_sqlite_column(conn, "agent_runs", "retry_of_run_id", "TEXT")
    _ensure_sqlite_column(conn, "coding_pod_jobs", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
    _ensure_sqlite_column(conn, "coding_pod_jobs", "max_attempts", "INTEGER NOT NULL DEFAULT 3")
    _ensure_sqlite_column(conn, "coding_pod_jobs", "state_version", "INTEGER NOT NULL DEFAULT 1")
    conn.commit()


def _ensure_sqlite_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


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

        chief_pod_artifacts: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, artifact_key, payload_json FROM chief_pod_artifacts ORDER BY project_id, created_at, artifact_key"
        ):
            chief_pod_artifacts.setdefault(row["project_id"], {})[row["artifact_key"]] = json.loads(row["payload_json"])

        pod_ai_reviews: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, review_id, payload_json FROM pod_ai_reviews ORDER BY project_id, updated_at, review_id"
        ):
            pod_ai_reviews.setdefault(row["project_id"], {})[row["review_id"]] = json.loads(row["payload_json"])

        agent_runs = {
            row["run_id"]: json.loads(row["payload_json"])
            for row in conn.execute("SELECT run_id, payload_json FROM agent_runs ORDER BY updated_at, run_id")
        }
        agent_run_steps: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT run_id, step_id, payload_json FROM agent_run_steps ORDER BY run_id, sequence_no"
        ):
            agent_run_steps.setdefault(row["run_id"], {})[row["step_id"]] = json.loads(row["payload_json"])
        brain_usage_events: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT run_id, event_id, payload_json FROM brain_usage_events ORDER BY run_id, occurred_at"
        ):
            brain_usage_events.setdefault(row["run_id"], {})[row["event_id"]] = json.loads(row["payload_json"])

        recalculation_events: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, event_id, payload_json FROM recalculation_events ORDER BY project_id, created_at, event_id"
        ):
            recalculation_events.setdefault(row["project_id"], {})[row["event_id"]] = json.loads(row["payload_json"])

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
    if not projects and not reports and not orchestrations and not risk_registers and not evidence_graphs and not chief_pod_artifacts and not pod_ai_reviews and not agent_runs and not agent_run_steps and not brain_usage_events and not recalculation_events and not prompt_artifacts and not assumption_overrides and not restructuring_cures and not glossary_terms and not glossary_term_candidates and not metadata and not has_identity_records:
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
        "chief_pod_artifacts": chief_pod_artifacts,
        "pod_ai_reviews": pod_ai_reviews,
        "agent_runs": agent_runs,
        "agent_run_steps": agent_run_steps,
        "brain_usage_events": brain_usage_events,
        "recalculation_events": recalculation_events,
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
    chief_pod_artifacts: Dict[str, Any],
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
        conn.execute("DELETE FROM chief_pod_artifacts")
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
        for project_id, records in chief_pod_artifacts.items():
            iterable = records.values() if isinstance(records, dict) else records
            for record in iterable:
                artifact_key = record.get("artifactKey") or record.get("artifact_key")
                if not artifact_key:
                    continue
                result = record.get("result") if isinstance(record.get("result"), dict) else {}
                conn.execute(
                    """
                    INSERT INTO chief_pod_artifacts(
                        project_id, artifact_key, pod_key, artifact_type, status,
                        created_at, updated_at, payload_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        artifact_key,
                        record.get("podKey") or record.get("pod_key"),
                        record.get("artifactType") or result.get("artifactType"),
                        record.get("status"),
                        record.get("createdAt") or record.get("created_at"),
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
    normalized.setdefault("chief_pod_artifacts", {})
    normalized.setdefault("pod_ai_reviews", {})
    normalized.setdefault("agent_handoffs", {})
    normalized.setdefault("agent_runs", {})
    normalized.setdefault("agent_run_steps", {})
    normalized.setdefault("brain_usage_events", {})
    normalized.setdefault("legal_reviews", {})
    normalized.setdefault("permit_reviews", {})
    normalized.setdefault("recalculation_events", {})
    normalized.setdefault("calculation_runs", {})
    normalized.setdefault("deal_state_records", {})
    normalized.setdefault("prompt_artifacts", {})
    normalized.setdefault("assumption_overrides", {})
    normalized.setdefault("restructuring_cures", {})
    normalized.setdefault("glossary_terms", {})
    normalized.setdefault("glossary_term_candidates", {})
    normalized.setdefault("project_snapshots", {})
    normalized.setdefault("user_activity_logs", [])
    normalized.setdefault("user_ai_profiles", {})
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
        state.get("chief_pod_artifacts", {}),
        state.get("prompt_artifacts", {}),
        state.get("assumption_overrides", {}),
        state.get("restructuring_cures", {}),
        state.get("project_snapshots", {}),
        state.get("user_activity_logs", []),
        state.get("identity", default_state()["identity"]),
        state.get("glossary_terms", {}),
        state.get("glossary_term_candidates", {}),
        state.get("user_ai_profiles", {}),
        recalculation_events=state.get("recalculation_events", {}),
        calculation_runs=state.get("calculation_runs", {}),
        deal_state_records=state.get("deal_state_records", {}),
        pod_ai_reviews=state.get("pod_ai_reviews", {}),
        legal_reviews=state.get("legal_reviews", {}),
        permit_reviews=state.get("permit_reviews", {}),
        agent_handoffs=state.get("agent_handoffs", {}),
        agent_runs=state.get("agent_runs", {}),
        agent_run_steps=state.get("agent_run_steps", {}),
        brain_usage_events=state.get("brain_usage_events", {}),
        operational_stores={
            store_name: state.get(store_name, {})
            for store_name in _OPERATIONAL_RECORD_SPECS
        },
    )


def _load_existing_user_activity_logs() -> list[Dict[str, Any]]:
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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


def _load_existing_chief_pod_artifacts() -> Dict[str, Any]:
    if _use_document_store():
        state = _load_json_state()
        return (state or {}).get("chief_pod_artifacts", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        artifacts: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, artifact_key, payload_json FROM chief_pod_artifacts ORDER BY project_id, created_at, artifact_key"
        ):
            artifacts.setdefault(row["project_id"], {})[row["artifact_key"]] = json.loads(row["payload_json"])
        return artifacts


def _load_existing_pod_ai_reviews() -> Dict[str, Any]:
    if _use_document_store():
        state = _load_json_state()
        return (state or {}).get("pod_ai_reviews", {})
    path = sqlite_file_path()
    if not path.exists():
        return {}
    with _connect_sqlite() as conn:
        reviews: Dict[str, Any] = {}
        for row in conn.execute(
            "SELECT project_id, review_id, payload_json FROM pod_ai_reviews ORDER BY project_id, updated_at, review_id"
        ):
            reviews.setdefault(row["project_id"], {})[row["review_id"]] = json.loads(row["payload_json"])
        return reviews


def _load_existing_prompt_artifacts() -> Dict[str, Any]:
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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
    if _use_document_store():
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

    if _use_document_store():
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
    if _use_document_store():
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
    return normalize_match_text(value)


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
    if _use_document_store():
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


def _user_ai_profile_row_to_record(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "userId": row["user_id"],
        "tenantId": row["tenant_id"],
        "provider": row["provider"],
        "model": row["model"],
        "status": row["status"],
        "apiKeyEncrypted": row["api_key_encrypted"],
        "apiKeyMasked": row["api_key_masked"],
        "apiKeyConfigured": bool(row["api_key_encrypted"]),
        "allowSystemFallback": bool(row["allow_system_fallback"]),
        "fallbackProvider": row["fallback_provider"],
        "fallbackModel": row["fallback_model"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "metadata": _json_loads_optional(row["metadata_json"]),
    }
