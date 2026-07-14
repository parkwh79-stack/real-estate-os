from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from agent_platform.contracts.models import (
    AgentExecutionContext,
    AgentManifest,
    AgentRequest,
    AgentResult,
    AgentRunRecord,
    AgentRunStepRecord,
    BrainUsageEvent,
    BrainProfile,
    BrainRequest,
    BrainResponse,
    CapabilityCheck,
    ExecutionPlan,
    ValidationResult,
)
from agent_platform.contracts.operational import RuntimeOutboxEvent
from agent_platform.contracts.operational import ToolManifest, ToolRequest, ToolResult


@runtime_checkable
class AgentInterface(Protocol):
    manifest: AgentManifest

    def can_handle(self, request: AgentRequest) -> CapabilityCheck: ...

    def prepare(self, request: AgentRequest, context: AgentExecutionContext) -> ExecutionPlan: ...

    async def execute(
        self,
        request: AgentRequest,
        context: AgentExecutionContext,
        plan: ExecutionPlan,
    ) -> AgentResult: ...

    def validate(
        self,
        request: AgentRequest,
        result: AgentResult,
        context: AgentExecutionContext,
    ) -> ValidationResult: ...


@runtime_checkable
class BrainInterface(Protocol):
    profile: BrainProfile

    async def generate(self, request: BrainRequest) -> BrainResponse: ...

    async def health_check(self) -> bool: ...

    def estimate_cost(self, request: BrainRequest) -> float: ...


@runtime_checkable
class ToolInterface(Protocol):
    manifest: ToolManifest

    def execute(self, request: ToolRequest) -> ToolResult: ...


@runtime_checkable
class AgentRunRepository(Protocol):
    def create_run(self, record: AgentRunRecord) -> tuple[AgentRunRecord, bool]: ...

    def get_run(self, run_id: str) -> AgentRunRecord | None: ...

    def find_by_idempotency_key(
        self,
        *,
        tenant_id: str,
        agent_id: str,
        idempotency_key: str,
    ) -> AgentRunRecord | None: ...

    def save_run(
        self,
        record: AgentRunRecord,
        *,
        expected_version: int,
        outbox_event: RuntimeOutboxEvent | None = None,
    ) -> AgentRunRecord: ...

    def save_step(self, record: AgentRunStepRecord) -> AgentRunStepRecord: ...

    def list_steps(self, run_id: str) -> list[AgentRunStepRecord]: ...

    def save_brain_usage(self, event: BrainUsageEvent) -> BrainUsageEvent: ...

    def list_brain_usage(self, run_id: str) -> list[BrainUsageEvent]: ...

    def request_cancellation(self, run_id: str, *, reason: str | None = None) -> AgentRunRecord: ...

    def acquire_lease(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunRecord: ...

    def heartbeat(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> AgentRunRecord: ...

    def list_expired_leases(self, *, now: datetime, limit: int = 100) -> list[AgentRunRecord]: ...
