from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent_platform.contracts import (
    AgentExecutionContext,
    AgentRequest,
    AgentResult,
    AgentRunError,
    AgentRunRecord,
    AgentRunRepository,
    AgentRunStatus,
    AgentRunStepRecord,
    AgentRunStepStatus,
    Blocker,
    BrainUsageEvent,
    BrainUsageEventStatus,
    DurableAgentExecution,
    QualityMetrics,
)
from agent_platform.registry import AgentRegistry
from agent_platform.operational import build_outbox_event
from agent_platform.runtime.state_machine import (
    TERMINAL_AGENT_RUN_STATUSES,
    ensure_agent_run_transition,
)


class DurableAgentRuntimeError(RuntimeError):
    code = "DURABLE_AGENT_RUNTIME_ERROR"
    retryable = False


class AgentIdempotencyConflict(DurableAgentRuntimeError):
    code = "AGENT_IDEMPOTENCY_CONFLICT"


class AgentRunInProgress(DurableAgentRuntimeError):
    code = "AGENT_RUN_IN_PROGRESS"


class PriorAgentRunFailed(DurableAgentRuntimeError):
    code = "PRIOR_AGENT_RUN_FAILED"


class AgentExecutionFailed(DurableAgentRuntimeError):
    code = "AGENT_EXECUTION_FAILED"

    def __init__(self, message: str, *, error_code: str, retryable: bool = False):
        super().__init__(message)
        self.code = error_code
        self.retryable = retryable


class AgentBudgetExceeded(AgentExecutionFailed):
    pass


class AgentExecutionTimedOut(AgentExecutionFailed):
    pass


class DurableAgentRuntime:
    """Persisted Agent lifecycle with state guards, idempotency, retry, and cancellation."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        repository: AgentRunRepository,
        worker_id: str | None = None,
        lease_seconds: int = 60,
    ):
        self._registry = registry
        self._repository = repository
        self._worker_id = worker_id or f"inline-agent-worker:{uuid4()}"
        self._lease_seconds = max(10, lease_seconds)

    def execute_sync(
        self,
        request: AgentRequest,
        context: AgentExecutionContext,
    ) -> DurableAgentExecution:
        agent = self._registry.get(request.agent_id, request.agent_version)
        manifest = agent.manifest
        input_hash = agent_request_input_hash(request)
        existing = self._repository.find_by_idempotency_key(
            tenant_id=request.actor.tenant_id,
            agent_id=request.agent_id,
            idempotency_key=request.idempotency_key,
        )
        if existing:
            return self._resolve_existing(existing, input_hash)

        now = datetime.now(timezone.utc)
        run = AgentRunRecord(
            run_id=context.run_id,
            request_id=request.request_id,
            correlation_id=context.correlation_id,
            trace_id=context.trace_id,
            tenant_id=request.actor.tenant_id,
            user_id=request.actor.user_id,
            project_id=request.project.project_id if request.project else None,
            agent_id=manifest.agent_id,
            agent_version=manifest.version,
            task_type=request.task_type,
            idempotency_key=request.idempotency_key,
            input_hash=input_hash,
            max_attempts=manifest.failure_policy.max_retries + 1,
            deadline_at=context.deadline_at,
            budget=context.budget,
            selected_brain_profile_id=context.selected_brain_profile_id,
            selected_provider=context.selected_provider,
            selected_model=context.selected_model,
            request_summary=_request_summary(request),
            created_at=now,
            updated_at=now,
            started_at=now,
            metadata={
                "implementationLevel": manifest.implementation_level.value,
                "failurePolicy": manifest.failure_policy.model_dump(by_alias=True, mode="json"),
                "secretsPersisted": False,
            },
        )
        run, created = self._repository.create_run(run)
        if not created:
            return self._resolve_existing(run, input_hash)
        run = self._repository.acquire_lease(
            run.run_id,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
        )

        context.set_runtime_value(
            "cancellation_probe",
            lambda: bool(
                (persisted := self._repository.get_run(context.run_id))
                and persisted.cancellation_requested
            ),
        )
        step_ids: list[str] = []

        if context.is_cancellation_requested():
            return self._cancel(run, request, manifest, step_ids)

        capability_step = self._start_step(run, AgentRunStatus.CAPABILITY_CHECKED, "capability_check", "Capability check")
        step_ids.append(capability_step.step_id)
        try:
            capability = agent.can_handle(request)
        except Exception as exc:
            if context.is_cancellation_requested():
                self._finish_step(capability_step, success=False, cancelled=True)
                return self._cancel(run, request, manifest, step_ids)
            error = _run_error(exc, retryable=False)
            self._finish_step(capability_step, success=False, error=error)
            self._transition(run, AgentRunStatus.FAILED, error=error)
            raise AgentExecutionFailed(str(exc), error_code=error.code, retryable=False) from exc
        self._finish_step(capability_step, success=True, metadata={"canHandle": capability.can_handle})
        if context.is_cancellation_requested():
            return self._cancel(run, request, manifest, step_ids)
        if not capability.can_handle:
            result = _blocked_result(request, context, manifest, capability.issues)
            run = self._transition(run, AgentRunStatus.BLOCKED, result=result)
            return self._outcome(run, result, step_ids, replayed=False)
        run = self._transition(run, AgentRunStatus.CAPABILITY_CHECKED)
        run = self._heartbeat(run)

        if context.is_cancellation_requested():
            return self._cancel(run, request, manifest, step_ids)

        planning_step = self._start_step(run, AgentRunStatus.PLANNED, "planning", "Execution plan")
        step_ids.append(planning_step.step_id)
        try:
            plan = agent.prepare(request, context)
        except Exception as exc:
            if context.is_cancellation_requested():
                self._finish_step(planning_step, success=False, cancelled=True)
                return self._cancel(run, request, manifest, step_ids)
            error = _run_error(exc, retryable=False)
            self._finish_step(planning_step, success=False, error=error)
            self._transition(run, AgentRunStatus.FAILED, error=error)
            raise AgentExecutionFailed(str(exc), error_code=error.code, retryable=False) from exc
        self._finish_step(planning_step, success=True, metadata={"planId": plan.plan_id})
        run = self._transition(run, AgentRunStatus.PLANNED)
        run = self._heartbeat(run)

        if context.selected_brain_profile_id:
            run = self._transition(run, AgentRunStatus.BRAIN_SELECTED)

        last_execution_step_id: str | None = None
        result: AgentResult | None = None
        attempt = 0
        while attempt < run.max_attempts:
            if context.is_cancellation_requested():
                return self._cancel(run, request, manifest, step_ids)
            attempt += 1
            run = self._transition(run, AgentRunStatus.EXECUTING, attempt_count=attempt)
            execution_step = self._start_step(
                run,
                AgentRunStatus.EXECUTING,
                "agent_execution",
                "Agent execution",
                attempt_no=attempt,
            )
            last_execution_step_id = execution_step.step_id
            step_ids.append(execution_step.step_id)
            try:
                self._assert_deadline(run, context)
                if not hasattr(agent, "execute_sync"):
                    raise AgentExecutionFailed(
                        "Synchronous runtime requires a sync-capable Agent adapter.",
                        error_code="SYNC_EXECUTION_NOT_SUPPORTED",
                    )
                result = agent.execute_sync(request, context, plan)
                self._assert_deadline(run, context)
                self._finish_step(
                    execution_step,
                    success=True,
                    output_hash=_hash_json(result.model_dump(by_alias=True, mode="json")),
                )
                break
            except Exception as exc:
                if context.is_cancellation_requested():
                    self._finish_step(execution_step, success=False, cancelled=True)
                    return self._cancel(run, request, manifest, step_ids)
                code = str(getattr(exc, "code", exc.__class__.__name__.upper()))
                retryable = bool(
                    getattr(exc, "retryable", False)
                    or code in manifest.failure_policy.retryable_error_codes
                )
                error = _run_error(exc, retryable=retryable, code=code)
                self._finish_step(execution_step, success=False, error=error)
                failed_usage_ids = self._record_failed_brain_usage(run, execution_step.step_id, error)
                if retryable and attempt < run.max_attempts:
                    run = self._transition(
                        run,
                        AgentRunStatus.RETRYING,
                        error=error,
                        brain_usage_event_ids=failed_usage_ids,
                    )
                    continue
                self._transition(
                    run,
                    AgentRunStatus.FAILED,
                    error=error,
                    brain_usage_event_ids=failed_usage_ids,
                )
                raise AgentExecutionFailed(str(exc), error_code=code, retryable=retryable) from exc

        if result is None:
            error = AgentRunError(
                code="AGENT_RESULT_MISSING",
                message="Agent execution ended without a result.",
                retryable=False,
                occurred_at=datetime.now(timezone.utc),
            )
            self._transition(run, AgentRunStatus.FAILED, error=error)
            raise AgentExecutionFailed(error.message, error_code=error.code)

        if context.is_cancellation_requested():
            return self._cancel(run, request, manifest, step_ids)

        run = self._heartbeat(run)
        consumed_budget = self._consume_result_budget(context, result)
        budget_error = self._budget_error(consumed_budget)
        if budget_error:
            error = AgentRunError(
                code="AGENT_BUDGET_EXCEEDED",
                message=budget_error,
                retryable=False,
                occurred_at=datetime.now(timezone.utc),
            )
            self._transition(run, AgentRunStatus.FAILED, error=error, budget=consumed_budget)
            raise AgentBudgetExceeded(error.message, error_code=error.code)
        context.budget = consumed_budget
        run = self._transition(run, AgentRunStatus.VALIDATING, budget=consumed_budget)
        validation_step = self._start_step(run, AgentRunStatus.VALIDATING, "validation", "Result validation")
        step_ids.append(validation_step.step_id)
        try:
            validation = agent.validate(request, result, context)
        except Exception as exc:
            if context.is_cancellation_requested():
                self._finish_step(validation_step, success=False, cancelled=True)
                return self._cancel(run, request, manifest, step_ids)
            error = _run_error(exc, retryable=False)
            self._finish_step(validation_step, success=False, error=error)
            self._transition(run, AgentRunStatus.FAILED, error=error)
            raise AgentExecutionFailed(str(exc), error_code=error.code, retryable=False) from exc
        if context.is_cancellation_requested():
            self._finish_step(validation_step, success=False, cancelled=True)
            return self._cancel(run, request, manifest, step_ids)
        if not validation.valid:
            error = AgentRunError(
                code="AGENT_OUTPUT_VALIDATION_FAILED",
                message=", ".join(item.code for item in validation.issues),
                retryable=False,
                occurred_at=datetime.now(timezone.utc),
            )
            self._finish_step(validation_step, success=False, error=error)
            self._transition(run, AgentRunStatus.FAILED, error=error)
            raise AgentExecutionFailed(error.message, error_code=error.code)
        self._finish_step(
            validation_step,
            success=True,
            metadata={"requiresHumanReview": validation.requires_human_review},
        )

        safe_result = _sanitize_result(result)
        target = safe_result.status
        if target not in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.COMPLETED_WITH_REVIEW,
            AgentRunStatus.WAITING_FOR_HUMAN,
            AgentRunStatus.BLOCKED,
            AgentRunStatus.CANCELLED,
        }:
            target = AgentRunStatus.COMPLETED_WITH_REVIEW if safe_result.requires_human_review else AgentRunStatus.COMPLETED
        usage_ids: list[str] = list(run.brain_usage_event_ids)
        if safe_result.brain_usage:
            usage_event = self._brain_usage_event(run, safe_result, last_execution_step_id)
            self._repository.save_brain_usage(usage_event)
            usage_ids.append(usage_event.event_id)
        run = self._transition(
            run,
            target,
            result=safe_result,
            brain_usage_event_ids=usage_ids,
        )
        return self._outcome(run, safe_result, step_ids, replayed=False)

    def request_cancellation(self, run_id: str, *, reason: str | None = None) -> AgentRunRecord:
        return self._repository.request_cancellation(run_id, reason=reason)

    def _resolve_existing(self, existing: AgentRunRecord, input_hash: str) -> DurableAgentExecution:
        if existing.input_hash != input_hash:
            raise AgentIdempotencyConflict(
                "The idempotency key already belongs to a request with a different canonical input hash."
            )
        if existing.status not in TERMINAL_AGENT_RUN_STATUSES:
            raise AgentRunInProgress(f"Agent run is already {existing.status.value}: {existing.run_id}")
        if existing.result_envelope is None:
            raise PriorAgentRunFailed(f"Prior Agent run has no replayable result: {existing.run_id}")
        return self._outcome(
            existing,
            existing.result_envelope,
            [item.step_id for item in self._repository.list_steps(existing.run_id)],
            replayed=True,
        )

    def _transition(
        self,
        run: AgentRunRecord,
        target: AgentRunStatus,
        *,
        result: AgentResult | None = None,
        error: AgentRunError | None = None,
        attempt_count: int | None = None,
        brain_usage_event_ids: list[str] | None = None,
        budget: Any | None = None,
    ) -> AgentRunRecord:
        current = self._repository.get_run(run.run_id) or run
        ensure_agent_run_transition(current.status, target)
        now = datetime.now(timezone.utc)
        history = list(current.error_history)
        if error:
            history.append(error)
        completed_at = now if target in TERMINAL_AGENT_RUN_STATUSES else current.completed_at
        updated = current.model_copy(
            update={
                "status": target,
                "attempt_count": attempt_count if attempt_count is not None else current.attempt_count,
                "result_envelope": result or current.result_envelope,
                "last_error": error or current.last_error,
                "error_history": history,
                "artifact_ids": list(result.artifact_ids) if result else current.artifact_ids,
                "brain_usage_event_ids": brain_usage_event_ids or current.brain_usage_event_ids,
                "budget": budget or current.budget,
                "lease_owner": None if target in TERMINAL_AGENT_RUN_STATUSES else current.lease_owner,
                "lease_expires_at": None if target in TERMINAL_AGENT_RUN_STATUSES else current.lease_expires_at,
                "completed_at": completed_at,
                "updated_at": now,
                "state_version": current.state_version + 1,
            }
        )
        event_type = (
            "agent_run_recovery_required"
            if error and error.code == "WORKER_LEASE_EXPIRED"
            else "agent_run_state_changed"
        )
        outbox = build_outbox_event(
            tenant_id=updated.tenant_id,
            project_id=updated.project_id,
            aggregate_type="agent_run",
            aggregate_id=updated.run_id,
            event_type=event_type,
            idempotency_key=f"agent-run:{updated.run_id}:state:{updated.state_version}",
            payload={
                "runId": updated.run_id,
                "agentId": updated.agent_id,
                "projectId": updated.project_id,
                "status": updated.status.value,
                "stateVersion": updated.state_version,
                "errorCode": error.code if error else None,
                "artifactIds": list(updated.artifact_ids),
            },
        )
        return self._repository.save_run(
            updated,
            expected_version=current.state_version,
            outbox_event=outbox,
        )

    def recover_expired_runs(self, *, limit: int = 100) -> list[AgentRunRecord]:
        """Fail abandoned leases and emit a durable replay request for the orchestrator."""
        recovered: list[AgentRunRecord] = []
        now = datetime.now(timezone.utc)
        for stale in self._repository.list_expired_leases(now=now, limit=limit):
            current = self._repository.get_run(stale.run_id)
            if current is None or current.status in TERMINAL_AGENT_RUN_STATUSES:
                continue
            error = AgentRunError(
                code="WORKER_LEASE_EXPIRED",
                message=(
                    "The Agent worker lease expired. The orchestrator must reconstruct "
                    "the request from durable project and artifact references."
                ),
                retryable=True,
                occurred_at=now,
                details={
                    "leaseOwner": current.lease_owner,
                    "leaseExpiresAt": current.lease_expires_at,
                },
            )
            recovered.append(self._transition(current, AgentRunStatus.FAILED, error=error))
        return recovered

    def _heartbeat(self, run: AgentRunRecord) -> AgentRunRecord:
        return self._repository.heartbeat(
            run.run_id,
            worker_id=self._worker_id,
            lease_seconds=self._lease_seconds,
        )

    @staticmethod
    def _assert_deadline(run: AgentRunRecord, context: AgentExecutionContext) -> None:
        del run
        if datetime.now(timezone.utc) <= context.deadline_at:
            return
        raise AgentExecutionTimedOut(
            "Agent execution exceeded its deterministic deadline.",
            error_code="AGENT_EXECUTION_TIMEOUT",
            retryable=False,
        )

    @staticmethod
    def _consume_result_budget(context: AgentExecutionContext, result: AgentResult):
        usage = result.brain_usage
        llm_calls = int(result.metadata.get("llmCallCount") or (1 if usage else 0))
        tool_calls = int(result.metadata.get("toolCallCount") or 0)
        cost = float((usage.estimated_cost_usd if usage else 0) or 0)
        return context.budget.model_copy(update={
            "consumed_cost_usd": context.budget.consumed_cost_usd + cost,
            "consumed_llm_calls": context.budget.consumed_llm_calls + llm_calls,
            "consumed_tool_calls": context.budget.consumed_tool_calls + tool_calls,
        })

    @staticmethod
    def _budget_error(budget: Any) -> str | None:
        if budget.max_cost_usd > 0 and budget.consumed_cost_usd > budget.max_cost_usd:
            return "Agent cost budget was exceeded."
        if budget.max_llm_calls > 0 and budget.consumed_llm_calls > budget.max_llm_calls:
            return "Agent LLM-call budget was exceeded."
        if budget.max_tool_calls > 0 and budget.consumed_tool_calls > budget.max_tool_calls:
            return "Agent tool-call budget was exceeded."
        return None

    def _record_failed_brain_usage(
        self,
        run: AgentRunRecord,
        step_id: str | None,
        error: AgentRunError,
    ) -> list[str]:
        if not any((run.selected_brain_profile_id, run.selected_provider, run.selected_model)):
            return []
        event = BrainUsageEvent(
            event_id=f"brain-usage:{uuid4()}",
            run_id=run.run_id,
            step_id=step_id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            agent_id=run.agent_id,
            agent_version=run.agent_version,
            brain_profile_id=run.selected_brain_profile_id,
            provider=run.selected_provider,
            model=run.selected_model,
            status=BrainUsageEventStatus.FAILED,
            fallback_reason=error.message,
            occurred_at=error.occurred_at,
            metadata={"errorCode": error.code, "credentialsPersisted": False},
        )
        self._repository.save_brain_usage(event)
        return [*run.brain_usage_event_ids, event.event_id]

    def _start_step(
        self,
        run: AgentRunRecord,
        stage: AgentRunStatus,
        step_type: str,
        name: str,
        *,
        attempt_no: int | None = None,
    ) -> AgentRunStepRecord:
        sequence_no = len(self._repository.list_steps(run.run_id)) + 1
        step = AgentRunStepRecord(
            step_id=f"agent-run-step:{uuid4()}",
            run_id=run.run_id,
            project_id=run.project_id,
            sequence_no=sequence_no,
            stage=stage,
            step_type=step_type,
            name=name,
            status=AgentRunStepStatus.RUNNING,
            attempt_no=attempt_no,
            input_hash=run.input_hash,
            started_at=datetime.now(timezone.utc),
        )
        return self._repository.save_step(step)

    def _finish_step(
        self,
        step: AgentRunStepRecord,
        *,
        success: bool,
        output_hash: str | None = None,
        error: AgentRunError | None = None,
        metadata: dict[str, Any] | None = None,
        cancelled: bool = False,
    ) -> AgentRunStepRecord:
        status = AgentRunStepStatus.CANCELLED if cancelled else (
            AgentRunStepStatus.COMPLETED if success else AgentRunStepStatus.FAILED
        )
        updated = step.model_copy(
            update={
                "status": status,
                "output_hash": output_hash,
                "error": error,
                "completed_at": datetime.now(timezone.utc),
                "metadata": {**step.metadata, **(metadata or {})},
            }
        )
        return self._repository.save_step(updated)

    def _cancel(
        self,
        run: AgentRunRecord,
        request: AgentRequest,
        manifest: Any,
        step_ids: list[str],
    ) -> DurableAgentExecution:
        result = _cancelled_result(request, run, manifest)
        cancelled = self._transition(run, AgentRunStatus.CANCELLED, result=result)
        return self._outcome(cancelled, result, step_ids, replayed=False)

    def _brain_usage_event(
        self,
        run: AgentRunRecord,
        result: AgentResult,
        step_id: str | None,
    ) -> BrainUsageEvent:
        usage = result.brain_usage
        assert usage is not None
        output = result.output or {}
        ai_used = output.get("aiBrainUsed") if isinstance(output.get("aiBrainUsed"), dict) else {}
        llm_accepted = result.metadata.get("llmReviewAccepted")
        fallback_reason = None
        status = BrainUsageEventStatus.COMPLETED
        if llm_accepted is False:
            status = BrainUsageEventStatus.FALLBACK
            fallback_reason = str(output.get("llmError") or "LLM review was not accepted; deterministic review remained authoritative.")
        return BrainUsageEvent(
            event_id=f"brain-usage:{uuid4()}",
            run_id=run.run_id,
            step_id=step_id,
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            agent_id=run.agent_id,
            agent_version=run.agent_version,
            brain_profile_id=usage.brain_profile_id or run.selected_brain_profile_id,
            provider=usage.provider or run.selected_provider,
            model=usage.model or run.selected_model,
            prompt_version=usage.prompt_version,
            status=status,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            estimated_cost_usd=usage.estimated_cost_usd,
            latency_ms=usage.latency_ms,
            fallback_reason=fallback_reason,
            occurred_at=datetime.now(timezone.utc),
            metadata={
                "promptPersisted": False,
                "responsePersisted": False,
                "credentialsPersisted": False,
                "legacyAiBrainUsedEventId": (
                    ai_used.get("ai_brain_used_event_id")
                    or ai_used.get("aiBrainUsedEventId")
                ),
            },
        )

    def _outcome(
        self,
        run: AgentRunRecord,
        result: AgentResult,
        step_ids: list[str],
        *,
        replayed: bool,
    ) -> DurableAgentExecution:
        return DurableAgentExecution(
            run=run,
            result=result,
            replayed=replayed,
            step_ids=step_ids,
            brain_usage_event_ids=list(run.brain_usage_event_ids),
        )


def agent_request_input_hash(request: AgentRequest) -> str:
    canonical = {
        "agentId": request.agent_id,
        "agentVersion": request.agent_version,
        "taskType": request.task_type,
        "project": request.project.model_dump(by_alias=True, mode="json") if request.project else None,
        "constraints": request.constraints.model_dump(by_alias=True, mode="json"),
        "inputSchemaName": request.input_schema_name,
        "inputSchemaVersion": request.input_schema_version,
        "payload": request.payload,
        "evidence": [item.model_dump(by_alias=True, mode="json") for item in request.evidence],
        "upstreamResults": request.upstream_results,
        "metadata": _redact_secrets(request.metadata),
    }
    return _hash_json(canonical)


def _request_summary(request: AgentRequest) -> dict[str, Any]:
    return {
        "inputSchemaName": request.input_schema_name,
        "inputSchemaVersion": request.input_schema_version,
        "evidenceIds": [item.evidence_id for item in request.evidence],
        "upstreamResultIds": list(request.upstream_results),
        "dataClassification": request.constraints.data_classification.value,
        "preferredBrainProfileId": request.constraints.preferred_brain_profile_id,
        "metadata": _redact_secrets(request.metadata),
        "requestPayloadPersisted": False,
    }


def _run_error(
    exc: Exception,
    *,
    retryable: bool,
    code: str | None = None,
) -> AgentRunError:
    return AgentRunError(
        code=code or str(getattr(exc, "code", exc.__class__.__name__.upper())),
        message=str(exc),
        retryable=retryable,
        exception_type=exc.__class__.__name__,
        occurred_at=datetime.now(timezone.utc),
    )


def _blocked_result(request: AgentRequest, context: AgentExecutionContext, manifest: Any, issues: list[Any]) -> AgentResult:
    quality = QualityMetrics(
        schema_valid=True,
        evidence_coverage=0,
        consistency_score=1,
        completeness_score=0,
        deterministic_checks_passed=False,
    )
    return AgentResult(
        run_id=context.run_id,
        request_id=request.request_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        status=AgentRunStatus.BLOCKED,
        output_schema_name=manifest.output_schema_name,
        output_schema_version=manifest.output_schema_version,
        blockers=[
            Blocker(code=item.code, severity="blocking", message=item.message)
            for item in issues
            if item.blocking
        ],
        confidence=0,
        quality_metrics=quality,
        requires_human_review=True,
        review_reasons=[item.message for item in issues if item.blocking],
        created_at=datetime.now(timezone.utc),
    )


def _cancelled_result(request: AgentRequest, run: AgentRunRecord, manifest: Any) -> AgentResult:
    quality = QualityMetrics(
        schema_valid=True,
        evidence_coverage=0,
        consistency_score=1,
        completeness_score=0,
        deterministic_checks_passed=False,
    )
    return AgentResult(
        run_id=run.run_id,
        request_id=request.request_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        status=AgentRunStatus.CANCELLED,
        output_schema_name=manifest.output_schema_name,
        output_schema_version=manifest.output_schema_version,
        confidence=0,
        quality_metrics=quality,
        created_at=datetime.now(timezone.utc),
    )


def _sanitize_result(result: AgentResult) -> AgentResult:
    return AgentResult.model_validate(_redact_secrets(result.model_dump(by_alias=True, mode="json")))


def _redact_secrets(value: Any) -> Any:
    sensitive_suffixes = ("apikey", "accesstoken", "refreshtoken", "password", "secret")
    sensitive_prefixes = ("authorization",)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).replace("-", "").replace("_", "").lower()
            is_sensitive = normalized.startswith(sensitive_prefixes) or normalized.endswith(sensitive_suffixes)
            result[key] = "[REDACTED]" if is_sensitive else _redact_secrets(item)
        return result
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def _hash_json(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
