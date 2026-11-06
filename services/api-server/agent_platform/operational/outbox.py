from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent_platform.contracts import RuntimeOutboxEvent
from agent_platform.operational.repository import OperationalRepository


class RuntimeOutboxDispatcher:
    """At-least-once dispatcher; consumers must honor the event idempotency key."""

    def __init__(
        self,
        *,
        repository: OperationalRepository,
        handlers: dict[str, Callable[[dict[str, Any]], None]],
    ) -> None:
        self._repository = repository
        self._handlers = handlers

    def publish_batch(self, *, worker_id: str, limit: int = 20) -> dict[str, int]:
        events = self._repository.claim_outbox(worker_id=worker_id, limit=limit)
        completed = 0
        failed = 0
        for event in events:
            try:
                handler = self._handlers.get(event.event_type)
                if handler is None:
                    raise RuntimeError(f"No outbox handler registered for {event.event_type}")
                payload = dict(event.payload)
                payload_tenant_id = str(payload.get("tenantId") or "")
                if payload_tenant_id and payload_tenant_id != event.tenant_id:
                    raise RuntimeError("OUTBOX_TENANT_SCOPE_MISMATCH")
                payload["tenantId"] = event.tenant_id
                if event.project_id:
                    payload_project_id = str(payload.get("projectId") or "")
                    if (
                        payload_project_id
                        and payload_project_id != event.project_id
                    ):
                        raise RuntimeError("OUTBOX_PROJECT_SCOPE_MISMATCH")
                    payload["projectId"] = event.project_id
                handler(payload)
                self._repository.mark_outbox_published(event.event_id, worker_id=worker_id)
                completed += 1
            except Exception as exc:
                self._repository.mark_outbox_failed(
                    event.event_id, worker_id=worker_id, error=str(exc),
                )
                failed += 1
        return {"claimed": len(events), "published": completed, "failed": failed}


def build_outbox_event(
    *,
    tenant_id: str,
    project_id: str | None,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> RuntimeOutboxEvent:
    now = datetime.now(timezone.utc)
    return RuntimeOutboxEvent(
        event_id=f"outbox:{uuid4()}",
        tenant_id=tenant_id,
        project_id=project_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        payload=payload,
        available_at=now,
        created_at=now,
        updated_at=now,
    )
