from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import RLock

from agent_platform.contracts import (
    AgentRunRecord,
    AgentRunStepRecord,
    AgentRunStatus,
    BrainUsageEvent,
    RuntimeOutboxEvent,
)
from agent_platform.runtime.state_machine import TERMINAL_AGENT_RUN_STATUSES


class AgentRunRepositoryError(RuntimeError):
    pass


class AgentRunVersionConflict(AgentRunRepositoryError):
    pass


class StorageAgentRunRepository:
    """Runtime repository backed by REALYTICS JSON, SQLite, or PostgreSQL storage."""

    def create_run(self, record: AgentRunRecord) -> tuple[AgentRunRecord, bool]:
        from storage import create_agent_run_record

        payload, created = create_agent_run_record(record.model_dump(by_alias=True, mode="json"))
        return AgentRunRecord.model_validate(payload), created

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        from storage import get_agent_run_record

        payload = get_agent_run_record(run_id)
        return AgentRunRecord.model_validate(payload) if payload else None

    def find_by_idempotency_key(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        idempotency_key: str,
    ) -> AgentRunRecord | None:
        from storage import get_agent_run_by_idempotency_record

        payload = get_agent_run_by_idempotency_record(
            tenant_id=tenant_id,
            agent_id=agent_id,
            idempotency_key=idempotency_key,
        )
        return AgentRunRecord.model_validate(payload) if payload else None

    def save_run(
        self,
        record: AgentRunRecord,
        *,
        expected_version: int,
        outbox_event: RuntimeOutboxEvent | None = None,
    ) -> AgentRunRecord:
        from storage import update_agent_run_record

        try:
            payload = update_agent_run_record(
                record.run_id,
                record.model_dump(by_alias=True, mode="json"),
                expected_state_version=expected_version,
                outbox_event=(
                    outbox_event.model_dump(by_alias=True, mode="json")
                    if outbox_event else None
                ),
            )
        except RuntimeError as exc:
            if str(exc) == "AGENT_RUN_VERSION_CONFLICT":
                raise AgentRunVersionConflict(str(exc)) from exc
            raise
        return AgentRunRecord.model_validate(payload)

    def save_step(self, record: AgentRunStepRecord) -> AgentRunStepRecord:
        from storage import upsert_agent_run_step_record

        payload = upsert_agent_run_step_record(record.model_dump(by_alias=True, mode="json"))
        return AgentRunStepRecord.model_validate(payload)

    def list_steps(self, run_id: str) -> list[AgentRunStepRecord]:
        from storage import list_agent_run_step_records

        return [AgentRunStepRecord.model_validate(item) for item in list_agent_run_step_records(run_id)]

    def save_brain_usage(self, event: BrainUsageEvent) -> BrainUsageEvent:
        from storage import upsert_brain_usage_event_record

        payload = upsert_brain_usage_event_record(event.model_dump(by_alias=True, mode="json"))
        return BrainUsageEvent.model_validate(payload)

    def list_brain_usage(self, run_id: str) -> list[BrainUsageEvent]:
        from storage import list_brain_usage_event_records

        return [BrainUsageEvent.model_validate(item) for item in list_brain_usage_event_records(run_id)]

    def request_cancellation(self, run_id: str, *, reason: str | None = None) -> AgentRunRecord:
        current = self.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if current.status in TERMINAL_AGENT_RUN_STATUSES:
            return current
        now = datetime.now(timezone.utc)
        updated = current.model_copy(
            update={
                "cancellation_requested": True,
                "cancellation_reason": reason,
                "updated_at": now,
                "state_version": current.state_version + 1,
            }
        )
        return self.save_run(updated, expected_version=current.state_version)

    def acquire_lease(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunRecord:
        current = self.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        now = datetime.now(timezone.utc)
        if (
            current.lease_owner
            and current.lease_owner != worker_id
            and current.lease_expires_at
            and current.lease_expires_at > now
        ):
            raise AgentRunRepositoryError("AGENT_RUN_LEASE_HELD")
        updated = current.model_copy(update={
            "lease_owner": worker_id,
            "lease_expires_at": now + timedelta(seconds=max(5, lease_seconds)),
            "heartbeat_at": now,
            "updated_at": now,
            "state_version": current.state_version + 1,
        })
        return self.save_run(updated, expected_version=current.state_version)

    def heartbeat(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunRecord:
        current = self.get_run(run_id)
        if current is None:
            raise KeyError(run_id)
        if current.lease_owner != worker_id:
            raise AgentRunRepositoryError("AGENT_RUN_LEASE_NOT_OWNED")
        now = datetime.now(timezone.utc)
        updated = current.model_copy(update={
            "lease_expires_at": now + timedelta(seconds=max(5, lease_seconds)),
            "heartbeat_at": now,
            "updated_at": now,
            "state_version": current.state_version + 1,
        })
        return self.save_run(updated, expected_version=current.state_version)

    def list_expired_leases(self, *, now: datetime, limit: int = 100) -> list[AgentRunRecord]:
        from storage import list_agent_run_records

        records = [AgentRunRecord.model_validate(item) for item in list_agent_run_records(limit=limit)]
        return [
            item for item in records
            if item.status not in TERMINAL_AGENT_RUN_STATUSES
            and item.lease_expires_at is not None
            and item.lease_expires_at < now
        ]


class InMemoryAgentRunRepository:
    """Deterministic repository for runtime unit tests; never used as production persistence."""

    def __init__(self) -> None:
        self._runs: dict[str, AgentRunRecord] = {}
        self._idempotency: dict[tuple[str, str, str], str] = {}
        self._steps: dict[str, dict[str, AgentRunStepRecord]] = {}
        self._usage: dict[str, dict[str, BrainUsageEvent]] = {}
        self._outbox: dict[str, RuntimeOutboxEvent] = {}
        self._lock = RLock()

    def create_run(self, record: AgentRunRecord) -> tuple[AgentRunRecord, bool]:
        key = (record.tenant_id, record.agent_id, record.idempotency_key)
        with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id:
                return deepcopy(self._runs[existing_id]), False
            self._runs[record.run_id] = deepcopy(record)
            self._idempotency[key] = record.run_id
            return deepcopy(record), True

    def get_run(self, run_id: str) -> AgentRunRecord | None:
        with self._lock:
            record = self._runs.get(run_id)
            return deepcopy(record) if record else None

    def find_by_idempotency_key(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        idempotency_key: str,
    ) -> AgentRunRecord | None:
        with self._lock:
            run_id = self._idempotency.get((tenant_id, agent_id, idempotency_key))
            return deepcopy(self._runs[run_id]) if run_id else None

    def save_run(
        self,
        record: AgentRunRecord,
        *,
        expected_version: int,
        outbox_event: RuntimeOutboxEvent | None = None,
    ) -> AgentRunRecord:
        with self._lock:
            current = self._runs.get(record.run_id)
            if current is None:
                raise KeyError(record.run_id)
            if current.state_version != expected_version:
                raise AgentRunVersionConflict("AGENT_RUN_VERSION_CONFLICT")
            self._runs[record.run_id] = deepcopy(record)
            if outbox_event is not None:
                self._outbox[outbox_event.event_id] = deepcopy(outbox_event)
            return deepcopy(record)

    def save_step(self, record: AgentRunStepRecord) -> AgentRunStepRecord:
        with self._lock:
            self._steps.setdefault(record.run_id, {})[record.step_id] = deepcopy(record)
            return deepcopy(record)

    def list_steps(self, run_id: str) -> list[AgentRunStepRecord]:
        with self._lock:
            records = list(self._steps.get(run_id, {}).values())
        return sorted((deepcopy(item) for item in records), key=lambda item: item.sequence_no)

    def save_brain_usage(self, event: BrainUsageEvent) -> BrainUsageEvent:
        with self._lock:
            self._usage.setdefault(event.run_id, {})[event.event_id] = deepcopy(event)
            return deepcopy(event)

    def list_brain_usage(self, run_id: str) -> list[BrainUsageEvent]:
        with self._lock:
            records = list(self._usage.get(run_id, {}).values())
        return sorted((deepcopy(item) for item in records), key=lambda item: item.occurred_at)

    def request_cancellation(self, run_id: str, *, reason: str | None = None) -> AgentRunRecord:
        with self._lock:
            current = self._runs.get(run_id)
            if current is None:
                raise KeyError(run_id)
            if current.status in TERMINAL_AGENT_RUN_STATUSES:
                return deepcopy(current)
            updated = current.model_copy(
                update={
                    "cancellation_requested": True,
                    "cancellation_reason": reason,
                    "updated_at": datetime.now(timezone.utc),
                    "state_version": current.state_version + 1,
                }
            )
            self._runs[run_id] = deepcopy(updated)
            return deepcopy(updated)

    def acquire_lease(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunRecord:
        with self._lock:
            current = self._runs.get(run_id)
            if current is None:
                raise KeyError(run_id)
            now = datetime.now(timezone.utc)
            if current.lease_owner and current.lease_owner != worker_id and current.lease_expires_at and current.lease_expires_at > now:
                raise AgentRunRepositoryError("AGENT_RUN_LEASE_HELD")
            updated = current.model_copy(update={
                "lease_owner": worker_id,
                "lease_expires_at": now + timedelta(seconds=max(5, lease_seconds)),
                "heartbeat_at": now,
                "updated_at": now,
                "state_version": current.state_version + 1,
            })
            self._runs[run_id] = deepcopy(updated)
            return deepcopy(updated)

    def heartbeat(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunRecord:
        with self._lock:
            current = self._runs.get(run_id)
            if current is None:
                raise KeyError(run_id)
            if current.lease_owner != worker_id:
                raise AgentRunRepositoryError("AGENT_RUN_LEASE_NOT_OWNED")
            now = datetime.now(timezone.utc)
            updated = current.model_copy(update={
                "lease_expires_at": now + timedelta(seconds=max(5, lease_seconds)),
                "heartbeat_at": now,
                "updated_at": now,
                "state_version": current.state_version + 1,
            })
            self._runs[run_id] = deepcopy(updated)
            return deepcopy(updated)

    def list_expired_leases(self, *, now: datetime, limit: int = 100) -> list[AgentRunRecord]:
        with self._lock:
            values = list(self._runs.values())
        return [
            deepcopy(item) for item in values
            if item.status not in TERMINAL_AGENT_RUN_STATUSES
            and item.lease_expires_at is not None
            and item.lease_expires_at < now
        ][:limit]
