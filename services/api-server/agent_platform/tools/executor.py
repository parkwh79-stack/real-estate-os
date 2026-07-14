from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from agent_platform.contracts import (
    ToolCallRecord,
    ToolCallStatus,
    ToolRequest,
    ToolResult,
    ToolSideEffectLevel,
)
from agent_platform.operational import OperationalRepository
from agent_platform.registry import ToolRegistry


class ToolExecutionDenied(RuntimeError):
    pass


class ToolExecutor:
    """Audited tool boundary with role, classification, approval, and idempotency checks."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        repository: OperationalRepository,
        approval_resolver: Callable[[str, ToolRequest], bool] | None = None,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._approval_resolver = approval_resolver

    def execute(self, request: ToolRequest) -> ToolResult:
        existing = self._repository.list(
            ToolCallRecord,
            filters={
                "tenant_id": request.tenant_id,
                "tool_id": request.tool_id,
                "idempotency_key": request.idempotency_key,
            },
            limit=1,
        )
        if existing:
            return existing[0].result

        tool = self._registry.get(request.tool_id, request.tool_version)
        manifest = tool.manifest
        self._enforce_policy(manifest, request)
        now = datetime.now(timezone.utc)
        running = ToolCallRecord(
            request=request,
            result=ToolResult(
                tool_call_id=request.tool_call_id,
                status=ToolCallStatus.RUNNING,
                started_at=now,
            ),
            tool_version=manifest.version,
            side_effect_level=manifest.side_effect_level,
        )
        try:
            self._repository.save(running)
        except Exception:
            # A concurrent process may have won the unique
            # (tenant, tool, idempotency key) insert. Reuse that authoritative
            # call instead of executing the side effect twice.
            existing = self._repository.list(
                ToolCallRecord,
                filters={
                    "tenant_id": request.tenant_id,
                    "tool_id": request.tool_id,
                    "idempotency_key": request.idempotency_key,
                },
                limit=1,
            )
            if existing:
                return existing[0].result
            raise
        try:
            result = tool.execute(request)
            if result.tool_call_id != request.tool_call_id:
                raise RuntimeError("TOOL_CALL_ID_MISMATCH")
            if result.status not in {ToolCallStatus.COMPLETED, ToolCallStatus.FAILED}:
                raise RuntimeError("TOOL_RESULT_NOT_TERMINAL")
        except Exception as exc:
            result = ToolResult(
                tool_call_id=request.tool_call_id,
                status=ToolCallStatus.FAILED,
                error_code=str(getattr(exc, "code", exc.__class__.__name__.upper())),
                error_message=str(exc),
                started_at=now,
                completed_at=datetime.now(timezone.utc),
            )
            self._repository.save(running.model_copy(update={
                "result": result,
                "state_version": running.state_version + 1,
            }))
            raise
        self._repository.save(running.model_copy(update={
            "result": result,
            "state_version": running.state_version + 1,
        }))
        return result

    def _enforce_policy(self, manifest, request: ToolRequest) -> None:
        if request.data_classification not in manifest.supported_data_classifications:
            raise ToolExecutionDenied("TOOL_DATA_CLASSIFICATION_DENIED")
        if manifest.required_roles and not set(manifest.required_roles).intersection(request.actor_roles):
            raise ToolExecutionDenied("TOOL_ROLE_DENIED")
        approval_required = manifest.requires_human_approval or manifest.side_effect_level in {
            ToolSideEffectLevel.EXTERNAL_WRITE,
            ToolSideEffectLevel.DESTRUCTIVE,
        }
        if not approval_required:
            return
        if not request.approval_id:
            raise ToolExecutionDenied("TOOL_HUMAN_APPROVAL_REQUIRED")
        if self._approval_resolver is None or not self._approval_resolver(request.approval_id, request):
            raise ToolExecutionDenied("TOOL_HUMAN_APPROVAL_INVALID")
