from __future__ import annotations

from typing import Any, Callable


def build_default_outbox_handlers() -> dict[str, Callable[[dict[str, Any]], None]]:
    return {
        "agent_run_state_changed": _verify_agent_run,
        "agent_run_recovery_required": _request_agent_run_recovery,
        "calculation_run_upserted": _materialize_calculation_run,
        "agent_handoff_upserted": _verify_agent_handoff,
        "parser.approved_snapshot.created": _verify_parser_approved_snapshot,
    }


def _verify_agent_run(payload: dict[str, Any]) -> None:
    from storage import get_agent_run_record

    run_id = str(payload.get("runId") or "")
    if not run_id or get_agent_run_record(run_id) is None:
        raise RuntimeError("OUTBOX_AGENT_RUN_NOT_FOUND")


def _request_agent_run_recovery(payload: dict[str, Any]) -> None:
    from storage import get_agent_run_record, upsert_recalculation_event_record

    run_id = str(payload.get("runId") or "")
    record = get_agent_run_record(run_id)
    if record is None:
        raise RuntimeError("OUTBOX_AGENT_RUN_NOT_FOUND")
    project_id = str(payload.get("projectId") or record.get("projectId") or "")
    if not project_id:
        raise RuntimeError("OUTBOX_RECOVERY_PROJECT_ID_MISSING")
    upsert_recalculation_event_record(project_id, {
        "eventId": f"agent-recovery:{run_id}",
        "runId": run_id,
        "sourcePod": record.get("agentId"),
        "trigger": "worker_lease_expired",
        "eventType": "agent_run_recovery_required",
        "status": "pending_recalculation",
        "metadata": {"source": "runtime_outbox"},
    })


def _materialize_calculation_run(payload: dict[str, Any]) -> None:
    from storage import materialize_calculation_run_record

    tenant_id = str(payload.get("tenantId") or "")
    project_id = str(payload.get("projectId") or "")
    run_id = str(payload.get("calculationRunId") or "")
    if not tenant_id or not project_id or not run_id:
        raise RuntimeError("OUTBOX_CALCULATION_REFERENCE_MISSING")
    materialize_calculation_run_record(
        project_id,
        run_id,
        tenant_id=tenant_id,
    )


def _verify_agent_handoff(payload: dict[str, Any]) -> None:
    from storage import list_agent_handoff_records

    project_id = str(payload.get("projectId") or "")
    handoff_id = str(payload.get("handoffId") or "")
    records = list_agent_handoff_records(project_id, limit=1000) if project_id else []
    if not any(str(item.get("handoffId")) == handoff_id for item in records):
        raise RuntimeError("OUTBOX_AGENT_HANDOFF_NOT_FOUND")


def _verify_parser_approved_snapshot(payload: dict[str, Any]) -> None:
    from db.repositories.parser_v2_repository import ParserV2PostgresRepository

    tenant_id = str(payload.get("tenantId") or "")
    project_id = str(payload.get("projectId") or "")
    snapshot_id = str(payload.get("snapshotId") or "")
    selection_ids = [str(item) for item in payload.get("selectionIds") or []]
    if not tenant_id or not project_id or not snapshot_id or not selection_ids:
        raise RuntimeError("OUTBOX_PARSER_SNAPSHOT_REFERENCE_MISSING")

    snapshot = ParserV2PostgresRepository().get_approved_snapshot(
        tenant_id,
        project_id,
        snapshot_id,
    )
    if snapshot is None:
        raise RuntimeError("OUTBOX_PARSER_SNAPSHOT_NOT_FOUND")
    if [str(item) for item in snapshot.get("selectionIds") or []] != selection_ids:
        raise RuntimeError("OUTBOX_PARSER_SNAPSHOT_SELECTIONS_MISMATCH")
