from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agent_platform.contracts import (
    ActorContext,
    AgentExecutionContext,
    AgentManifest,
    AgentRequest,
    AgentResult,
    AgentRunRecord,
    AgentRunStatus,
    BrainUsage,
    DataClassification,
    EvidenceRequirement,
    ExecutionConstraints,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    ImplementationLevel,
    ProjectContext,
    QualityMetrics,
    ReviewRequirement,
    RunBudget,
)
from agent_platform.registry import AgentRegistry
from agent_platform.runtime import (
    AgentBudgetExceeded,
    AgentExecutionFailed,
    AgentIdempotencyConflict,
    DurableAgentRuntime,
    InMemoryAgentRunRepository,
    InvalidAgentRunTransition,
    LegacyAgentAdapter,
    StorageAgentRunRepository,
    ensure_agent_run_transition,
    agent_request_input_hash,
)


def _manifest(*, max_retries: int = 0) -> AgentManifest:
    return AgentManifest(
        agent_id="durable_test_agent",
        version="1.0.0",
        display_name="Durable test Agent",
        domain="qa",
        role="runtime verification",
        description="Verifies durable Agent runtime behavior.",
        implementation_level=ImplementationLevel.CONTROLLED,
        supported_task_types=["test_task"],
        input_schema_name="TestInput",
        input_schema_version="1.0",
        output_schema_name="TestOutput",
        output_schema_version="1.0",
        supported_data_classifications=[DataClassification.INTERNAL],
        execution_policy=ExecutionPolicy(
            mode=ExecutionMode.DETERMINISTIC_FIRST,
            evidence_requirement=EvidenceRequirement.NONE,
            human_review=ReviewRequirement.NONE,
        ),
        failure_policy=FailurePolicy(
            max_retries=max_retries,
            retryable_error_codes=["TRANSIENT_TEST_ERROR"],
        ),
    )


def _request(*, payload: dict | None = None, idempotency_key: str = "idem-1") -> AgentRequest:
    now = datetime.now(timezone.utc)
    return AgentRequest(
        request_id=f"request-{now.timestamp()}",
        agent_id="durable_test_agent",
        agent_version="1.0.0",
        task_type="test_task",
        actor=ActorContext(user_id="user-1", tenant_id="tenant-1", roles=["reviewer"]),
        project=ProjectContext(project_id="project-1"),
        constraints=ExecutionConstraints(data_classification=DataClassification.INTERNAL),
        input_schema_name="TestInput",
        input_schema_version="1.0",
        payload=payload or {"value": 1},
        idempotency_key=idempotency_key,
        requested_at=now,
    )


def _context(run_id: str) -> AgentExecutionContext:
    now = datetime.now(timezone.utc)
    actor = ActorContext(user_id="user-1", tenant_id="tenant-1", roles=["reviewer"])
    constraints = ExecutionConstraints(data_classification=DataClassification.INTERNAL)
    return AgentExecutionContext(
        run_id=run_id,
        correlation_id="correlation-1",
        trace_id="trace-1",
        actor=actor,
        project=ProjectContext(project_id="project-1"),
        constraints=constraints,
        budget=RunBudget(max_llm_calls=1, max_tool_calls=1),
        selected_brain_profile_id="brain-test",
        selected_provider="test-provider",
        selected_model="test-model",
        started_at=now,
        deadline_at=now + timedelta(minutes=1),
    )


def _runtime(handler, *, repository=None, max_retries: int = 0, result_mapper=None):
    registry = AgentRegistry()
    registry.register(
        LegacyAgentAdapter(
            manifest=_manifest(max_retries=max_retries),
            handler=handler,
            result_mapper=result_mapper,
        )
    )
    repo = repository or InMemoryAgentRunRepository()
    return DurableAgentRuntime(registry=registry, repository=repo), repo


def test_idempotent_replay_executes_handler_once():
    calls = {"count": 0}

    def handler(_request, _context, _plan):
        calls["count"] += 1
        return {"answer": 42}

    runtime, repo = _runtime(handler)
    request = _request()
    first = runtime.execute_sync(request, _context("run-1"))
    replay = runtime.execute_sync(request, _context("run-2"))

    assert calls["count"] == 1
    assert replay.replayed is True
    assert replay.run.run_id == first.run.run_id
    assert replay.result.output == {"answer": 42}
    assert len(repo.list_steps(first.run.run_id)) == 4


def test_same_idempotency_key_with_different_input_is_rejected():
    runtime, _repo = _runtime(lambda *_args: {"ok": True})
    runtime.execute_sync(_request(payload={"value": 1}), _context("run-1"))
    with pytest.raises(AgentIdempotencyConflict):
        runtime.execute_sync(_request(payload={"value": 2}), _context("run-2"))


class _TransientError(RuntimeError):
    code = "TRANSIENT_TEST_ERROR"
    retryable = True


def test_retry_policy_persists_failed_attempt_then_completes():
    calls = {"count": 0}

    def handler(_request, _context, _plan):
        calls["count"] += 1
        if calls["count"] == 1:
            raise _TransientError("temporary provider failure")
        return {"ok": True}

    runtime, repo = _runtime(handler, max_retries=1)
    execution = runtime.execute_sync(_request(), _context("run-retry"))
    steps = repo.list_steps(execution.run.run_id)

    assert calls["count"] == 2
    assert execution.run.status == AgentRunStatus.COMPLETED
    assert execution.run.attempt_count == 2
    assert execution.run.error_history[0].code == "TRANSIENT_TEST_ERROR"
    assert [step.status.value for step in steps if step.step_type == "agent_execution"] == ["failed", "completed"]


def test_cancellation_requested_during_execution_discards_completed_output():
    repo = InMemoryAgentRunRepository()

    def handler(_request, context, _plan):
        repo.request_cancellation(context.run_id, reason="user requested stop")
        return {"mustNotBecomeAuthoritative": True}

    runtime, _repo = _runtime(handler, repository=repo)
    execution = runtime.execute_sync(_request(), _context("run-cancel"))

    assert execution.run.status == AgentRunStatus.CANCELLED
    assert execution.result.status == AgentRunStatus.CANCELLED
    assert execution.result.output is None
    assert execution.run.cancellation_reason == "user requested stop"


def test_cancellation_takes_precedence_over_handler_exception():
    repo = InMemoryAgentRunRepository()

    def handler(_request, context, _plan):
        repo.request_cancellation(context.run_id, reason="operator stop")
        raise _TransientError("provider was still running")

    runtime, _repo = _runtime(handler, repository=repo, max_retries=1)
    execution = runtime.execute_sync(_request(), _context("run-cancel-error"))
    execution_steps = [
        step for step in repo.list_steps(execution.run.run_id)
        if step.step_type == "agent_execution"
    ]

    assert execution.run.status == AgentRunStatus.CANCELLED
    assert execution.run.attempt_count == 1
    assert execution_steps[0].status.value == "cancelled"


def test_terminal_state_transition_is_forbidden():
    with pytest.raises(InvalidAgentRunTransition, match="Terminal"):
        ensure_agent_run_transition(AgentRunStatus.COMPLETED, AgentRunStatus.EXECUTING)


def test_capability_exception_is_persisted_as_terminal_failure():
    runtime, repo = _runtime(lambda *_args: {"ok": True})
    agent = runtime._registry.get("durable_test_agent", "1.0.0")

    def fail_capability(_request):
        raise RuntimeError("capability service unavailable")

    agent.can_handle = fail_capability
    with pytest.raises(AgentExecutionFailed, match="capability service unavailable"):
        runtime.execute_sync(_request(), _context("run-capability-failure"))

    persisted = repo.get_run("run-capability-failure")
    steps = repo.list_steps("run-capability-failure")
    assert persisted is not None
    assert persisted.status == AgentRunStatus.FAILED
    assert persisted.last_error.code == "RUNTIMEERROR"
    assert steps[0].step_type == "capability_check"
    assert steps[0].status.value == "failed"


def test_validation_exception_is_persisted_as_terminal_failure():
    runtime, repo = _runtime(lambda *_args: {"ok": True})
    agent = runtime._registry.get("durable_test_agent", "1.0.0")

    def fail_validation(_request, _result, _context):
        raise RuntimeError("validator unavailable")

    agent.validate = fail_validation
    with pytest.raises(AgentExecutionFailed, match="validator unavailable"):
        runtime.execute_sync(_request(), _context("run-validation-failure"))

    persisted = repo.get_run("run-validation-failure")
    steps = repo.list_steps("run-validation-failure")
    assert persisted is not None
    assert persisted.status == AgentRunStatus.FAILED
    assert persisted.last_error.code == "RUNTIMEERROR"
    assert steps[-1].step_type == "validation"
    assert steps[-1].status.value == "failed"


def test_runtime_enforces_result_budget_before_output_becomes_authoritative():
    def result_mapper(raw, request, context, manifest):
        quality = QualityMetrics(
            schema_valid=True,
            evidence_coverage=1,
            consistency_score=1,
            completeness_score=1,
            deterministic_checks_passed=True,
        )
        return AgentResult(
            run_id=context.run_id,
            request_id=request.request_id,
            agent_id=manifest.agent_id,
            agent_version=manifest.version,
            status=AgentRunStatus.COMPLETED,
            output_schema_name=manifest.output_schema_name,
            output_schema_version=manifest.output_schema_version,
            output=raw,
            confidence=1,
            quality_metrics=quality,
            metadata={"llmCallCount": 2},
            created_at=datetime.now(timezone.utc),
        )

    runtime, repo = _runtime(
        lambda *_args: {"ok": True},
        result_mapper=result_mapper,
    )
    with pytest.raises(AgentBudgetExceeded, match="LLM-call budget"):
        runtime.execute_sync(_request(), _context("run-budget"))
    persisted = repo.get_run("run-budget")
    assert persisted and persisted.status == AgentRunStatus.FAILED
    assert persisted.last_error.code == "AGENT_BUDGET_EXCEEDED"
    assert persisted.result_envelope is None


def test_runtime_rejects_execution_after_deadline():
    calls = {"count": 0}

    def handler(*_args):
        calls["count"] += 1
        return {"ok": True}

    runtime, repo = _runtime(handler)
    context = _context("run-timeout")
    context.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(AgentExecutionFailed) as exc:
        runtime.execute_sync(_request(), context)
    assert exc.value.code == "AGENT_EXECUTION_TIMEOUT"
    assert calls["count"] == 0
    assert repo.get_run("run-timeout").status == AgentRunStatus.FAILED


def test_expired_worker_lease_generates_recovery_outbox_event():
    repo = InMemoryAgentRunRepository()
    runtime, _ = _runtime(lambda *_args: {"ok": True}, repository=repo)
    request = _request(idempotency_key="expired-worker")
    now = datetime.now(timezone.utc)
    run = AgentRunRecord(
        run_id="run-expired",
        request_id=request.request_id,
        correlation_id="correlation-expired",
        trace_id="trace-expired",
        tenant_id="tenant-1",
        user_id="user-1",
        project_id="project-1",
        agent_id=request.agent_id,
        agent_version=request.agent_version,
        task_type=request.task_type,
        idempotency_key=request.idempotency_key,
        input_hash=agent_request_input_hash(request),
        status=AgentRunStatus.EXECUTING,
        lease_owner="dead-worker",
        lease_expires_at=now - timedelta(seconds=1),
        created_at=now - timedelta(minutes=1),
        updated_at=now - timedelta(minutes=1),
    )
    repo.create_run(run)
    recovered = runtime.recover_expired_runs()
    assert recovered[0].status == AgentRunStatus.FAILED
    assert recovered[0].last_error.code == "WORKER_LEASE_EXPIRED"
    assert any(
        event.event_type == "agent_run_recovery_required"
        for event in repo._outbox.values()
    )


def test_sqlite_repository_persists_run_steps_and_secret_free_brain_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "runtime.sqlite3"))

    def result_mapper(raw, request, context, manifest):
        quality = QualityMetrics(
            schema_valid=True,
            evidence_coverage=1,
            consistency_score=1,
            completeness_score=1,
            deterministic_checks_passed=True,
        )
        return AgentResult(
            run_id=context.run_id,
            request_id=request.request_id,
            agent_id=manifest.agent_id,
            agent_version=manifest.version,
            status=AgentRunStatus.COMPLETED,
            output_schema_name=manifest.output_schema_name,
            output_schema_version=manifest.output_schema_version,
            output={
                "ok": raw["ok"],
                "api_key": "must-be-redacted",
                "geminiApiKey": "gemini-must-be-redacted",
                "nested": {
                    "clientSecret": "client-secret-must-be-redacted",
                    "authorizationHeader": "bearer-must-be-redacted",
                },
            },
            confidence=1,
            brain_usage=BrainUsage(
                brain_profile_id="brain-test",
                provider="test-provider",
                model="test-model",
                input_tokens=11,
                output_tokens=3,
            ),
            quality_metrics=quality,
            created_at=datetime.now(timezone.utc),
        )

    runtime, _repo = _runtime(
        lambda *_args: {"ok": True},
        repository=StorageAgentRunRepository(),
        result_mapper=result_mapper,
    )
    execution = runtime.execute_sync(_request(), _context("run-sqlite"))

    reopened = StorageAgentRunRepository()
    persisted = reopened.get_run(execution.run.run_id)
    steps = reopened.list_steps(execution.run.run_id)
    usage = reopened.list_brain_usage(execution.run.run_id)
    serialized = json.dumps(persisted.model_dump(by_alias=True, mode="json"), ensure_ascii=False)

    assert persisted is not None
    assert persisted.status == AgentRunStatus.COMPLETED
    assert len(steps) == 4
    assert usage[0].input_tokens == 11
    assert usage[0].output_tokens == 3
    assert "must-be-redacted" not in serialized
    assert "gemini-must-be-redacted" not in serialized
    assert "client-secret-must-be-redacted" not in serialized
    assert "bearer-must-be-redacted" not in serialized
    assert "[REDACTED]" in serialized
