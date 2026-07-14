from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from pydantic import BaseModel

from agent_platform.contracts import (
    BrainProviderHealth,
    BrainRoutingEventRecord,
    BrainRoutingPolicyRecord,
    CodingPodJob,
    LearningCandidate,
    LearningEvaluation,
    LearningPromotionDecision,
    OutboxEventStatus,
    RuntimeOutboxEvent,
    ToolCallRecord,
)


T = TypeVar("T", bound=BaseModel)


class OperationalRepository:
    """Typed persistence facade for Agent Platform operational records."""

    def save(self, record: T) -> T:
        table = _table_for(record)
        from storage import upsert_operational_record

        payload = upsert_operational_record(table, record.model_dump(by_alias=True, mode="json"))
        return record.__class__.model_validate(payload)

    def get(self, model: type[T], record_id: str) -> T | None:
        from storage import get_operational_record

        payload = get_operational_record(_table_for_model(model), record_id)
        return model.model_validate(payload) if payload else None

    def list(
        self,
        model: type[T],
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[T]:
        from storage import list_operational_records

        records = list_operational_records(
            _table_for_model(model), filters=filters, limit=limit,
        )
        return [model.model_validate(item) for item in records]

    def enqueue_outbox(self, event: RuntimeOutboxEvent) -> RuntimeOutboxEvent:
        existing = self.list(
            RuntimeOutboxEvent,
            filters={"idempotency_key": event.idempotency_key},
            limit=1,
        )
        if existing:
            return existing[0]
        try:
            return self.save(event)
        except Exception:
            # The database uniqueness constraint wins cross-process races.
            existing = self.list(
                RuntimeOutboxEvent,
                filters={"idempotency_key": event.idempotency_key},
                limit=1,
            )
            if existing:
                return existing[0]
            raise

    def claim_outbox(
        self,
        *,
        worker_id: str,
        limit: int = 20,
        lease_seconds: int = 60,
    ) -> list[RuntimeOutboxEvent]:
        from storage import claim_runtime_outbox_event_records

        return [
            RuntimeOutboxEvent.model_validate(item)
            for item in claim_runtime_outbox_event_records(
                worker_id=worker_id,
                limit=limit,
                lease_seconds=lease_seconds,
            )
        ]

    def claim_coding_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> CodingPodJob | None:
        from storage import claim_coding_pod_job_record

        payload = claim_coding_pod_job_record(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        return CodingPodJob.model_validate(payload) if payload else None

    def mark_outbox_published(
        self,
        event_id: str,
        *,
        worker_id: str,
    ) -> RuntimeOutboxEvent:
        event = self._owned_outbox(event_id, worker_id)
        now = datetime.now(timezone.utc)
        return self.save(event.model_copy(update={
            "status": OutboxEventStatus.PUBLISHED,
            "lease_owner": None,
            "lease_expires_at": None,
            "published_at": now,
            "updated_at": now,
        }))

    def mark_outbox_failed(
        self,
        event_id: str,
        *,
        worker_id: str,
        error: str,
    ) -> RuntimeOutboxEvent:
        event = self._owned_outbox(event_id, worker_id)
        now = datetime.now(timezone.utc)
        exhausted = event.attempt_count >= event.max_attempts
        delay = min(900, 2 ** min(event.attempt_count, 9))
        return self.save(event.model_copy(update={
            "status": OutboxEventStatus.DEAD_LETTER if exhausted else OutboxEventStatus.RETRYING,
            "lease_owner": None,
            "lease_expires_at": None,
            "available_at": now + timedelta(seconds=delay),
            "last_error": error[:2000],
            "updated_at": now,
        }))

    def requeue_outbox(
        self,
        event_id: str,
        *,
        requested_by: str,
        reason: str,
    ) -> RuntimeOutboxEvent:
        event = self.get(RuntimeOutboxEvent, event_id)
        if event is None:
            raise KeyError(event_id)
        if event.status == OutboxEventStatus.PUBLISHED:
            raise RuntimeError("PUBLISHED_OUTBOX_EVENT_CANNOT_BE_REQUEUED")
        now = datetime.now(timezone.utc)
        return self.save(event.model_copy(update={
            "status": OutboxEventStatus.PENDING,
            "attempt_count": 0,
            "available_at": now,
            "lease_owner": None,
            "lease_expires_at": None,
            "last_error": None,
            "updated_at": now,
            "payload": {
                **event.payload,
                "manualRequeue": {
                    "requestedBy": requested_by,
                    "reason": reason,
                    "requestedAt": now.isoformat(),
                },
            },
        }))

    def _owned_outbox(self, event_id: str, worker_id: str) -> RuntimeOutboxEvent:
        event = self.get(RuntimeOutboxEvent, event_id)
        if event is None:
            raise KeyError(event_id)
        if event.status != OutboxEventStatus.PROCESSING or event.lease_owner != worker_id:
            raise RuntimeError("OUTBOX_LEASE_NOT_OWNED")
        return event


_MODEL_TABLES: dict[type[BaseModel], str] = {
    RuntimeOutboxEvent: "runtime_outbox_events",
    BrainProviderHealth: "brain_provider_health",
    BrainRoutingPolicyRecord: "brain_routing_policies",
    BrainRoutingEventRecord: "brain_routing_events",
    ToolCallRecord: "tool_calls",
    LearningCandidate: "learning_candidates",
    LearningEvaluation: "learning_evaluations",
    LearningPromotionDecision: "learning_promotions",
    CodingPodJob: "coding_pod_jobs",
}


def _table_for(record: BaseModel) -> str:
    return _table_for_model(record.__class__)


def _table_for_model(model: type[BaseModel]) -> str:
    try:
        return _MODEL_TABLES[model]
    except KeyError as exc:
        raise TypeError(f"Unsupported operational record: {model.__name__}") from exc
