from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel

from agent_platform.contracts import (
    ActorContext,
    AgentExecutionContext,
    AgentManifest,
    AgentRequest,
    AgentResult,
    AgentRunStatus,
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
    SourceLocator,
)
from agent_platform.operational import OperationalRepository
from agent_platform.registry import AgentRegistry
from agent_platform.runtime import (
    DurableAgentRuntime,
    LegacyAgentAdapter,
    StorageAgentRunRepository,
    agent_request_input_hash,
)
from agent_platform.tools import ToolExecutor, build_default_tool_registry
from agents.ai_connection import safe_ai_context


def run_domain_agent_callable_with_contract(
    *,
    project_id: str,
    agent_id: str,
    task_type: str,
    domain: str,
    input_snapshot: dict[str, Any],
    executor: Callable[[], Any],
    options: dict[str, Any] | None = None,
    parent_run_id: str | None = None,
    agent_version: str = "1.0.0",
    output_schema_name: str = "DomainAgentArtifact",
    deterministic_authority_required: bool = False,
    llm_required: bool = False,
) -> tuple[Any, dict[str, Any]]:
    """Execute an existing domain Agent through the durable control plane.

    This is the controlled migration boundary for the many proven domain
    implementations that predate AgentManifest/AgentRequest. The callable is
    executed inside the runtime, not recorded after the fact.
    """

    options = dict(options or {})
    normalized_agent_id = _identifier(agent_id)
    manifest = AgentManifest(
        agent_id=normalized_agent_id,
        version=agent_version,
        display_name=agent_id,
        domain=domain,
        role=f"Execute {task_type} through the shared Agent Runtime",
        description="Controlled runtime adapter for an existing REALYTICS domain Agent.",
        implementation_level=ImplementationLevel.CONTROLLED,
        supported_task_types=[task_type],
        input_schema_name="DomainAgentInputSnapshot",
        input_schema_version="1.0",
        output_schema_name=output_schema_name,
        output_schema_version="1.0",
        supported_data_classifications=list(DataClassification),
        execution_policy=ExecutionPolicy(
            mode=ExecutionMode.DETERMINISTIC_FIRST,
            llm_required=llm_required,
            human_review=ReviewRequirement.CONDITIONAL,
            evidence_requirement=EvidenceRequirement.OPTIONAL,
            deterministic_authority_required=deterministic_authority_required,
            max_execution_seconds=int(options.get("agentTimeoutSeconds") or 600),
            max_tool_calls=int(options.get("maxToolCalls") or 50),
            max_llm_calls=int(options.get("maxLlmCalls") or (3 if llm_required else 1)),
        ),
        failure_policy=FailurePolicy(
            max_retries=1,
            retryable_error_codes=[
                "PROVIDER_TIMEOUT",
                "PROVIDER_RATE_LIMIT",
                "UPSTREAM_TEMPORARY_FAILURE",
            ],
            deterministic_fallback=not llm_required,
            alternate_brain_fallback=llm_required,
            fail_closed=deterministic_authority_required,
        ),
        tags=["domain-agent", domain, "durable-runtime", "legacy-migration"],
        metadata={
            "adapter": "LegacyAgentAdapter",
            "migrationStatus": "controlled",
        },
    )

    def handler(_request, context, _plan):
        callback = context.get_runtime_value("domain_agent_executor")
        if not callable(callback):
            raise RuntimeError("DOMAIN_AGENT_EXECUTOR_MISSING")
        return callback()

    registry = AgentRegistry()
    registry.register(
        LegacyAgentAdapter(
            manifest=manifest,
            handler=handler,
            result_mapper=_result_mapper,
        )
    )

    now = datetime.now(timezone.utc)
    safe_context = safe_ai_context(options.get("ai_context") or options.get("aiContext") or {})
    actor = ActorContext(
        user_id=str(safe_context.get("user_id") or options.get("userId") or "anonymous"),
        tenant_id=str(safe_context.get("tenant_id") or options.get("tenantId") or "default"),
        roles=[str(item) for item in options.get("actorRoles") or []],
    )
    classification = _classification(options.get("dataClassification") or "confidential")
    request = AgentRequest(
        request_id=f"agent-request:{normalized_agent_id}:{uuid4()}",
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        task_type=task_type,
        actor=actor,
        project=ProjectContext(
            project_id=project_id,
            asset_type=str(input_snapshot.get("assetType") or "") or None,
        ),
        constraints=ExecutionConstraints(
            data_classification=classification,
            max_cost_usd=float(options.get("maxCostUsd") or 0),
            timeout_seconds=manifest.execution_policy.max_execution_seconds,
            preferred_brain_profile_id=safe_context.get("brain_profile_id"),
            prohibited_providers=[str(item) for item in options.get("prohibitedProviders") or []],
            local_only=bool(options.get("localOnly")),
        ),
        input_schema_name=manifest.input_schema_name,
        input_schema_version=manifest.input_schema_version,
        payload={"inputSnapshot": input_snapshot},
        idempotency_key="pending",
        requested_at=now,
        metadata={
            "source": "ChiefDealOrchestrator",
            "parentRunId": parent_run_id,
            "requestPayloadPersisted": False,
        },
    )
    request = request.model_copy(update={"idempotency_key": agent_request_input_hash(request)})
    run_id = str(options.get("childAgentRunId") or f"agent-run:{normalized_agent_id}:{uuid4()}")
    context = AgentExecutionContext(
        run_id=run_id,
        correlation_id=str(options.get("correlationId") or parent_run_id or run_id),
        trace_id=str(options.get("traceId") or parent_run_id or run_id),
        actor=actor,
        project=request.project,
        constraints=request.constraints,
        budget=RunBudget(
            max_cost_usd=request.constraints.max_cost_usd or 0,
            max_llm_calls=manifest.execution_policy.max_llm_calls,
            max_tool_calls=manifest.execution_policy.max_tool_calls,
        ),
        selected_brain_profile_id=safe_context.get("brain_profile_id"),
        selected_provider=safe_context.get("provider"),
        selected_model=safe_context.get("model"),
        started_at=now,
        deadline_at=now + timedelta(seconds=manifest.execution_policy.max_execution_seconds),
    )
    context.set_runtime_value("domain_agent_executor", executor)
    context.set_runtime_value(
        "tool_executor",
        ToolExecutor(
            registry=build_default_tool_registry(),
            repository=OperationalRepository(),
        ),
    )
    execution = DurableAgentRuntime(
        registry=registry,
        repository=StorageAgentRunRepository(),
    ).execute_sync(request, context)
    output = execution.result.output
    if isinstance(output, dict) and output.get("__wrappedScalarResult__") is True:
        output = output.get("value")
    return output, {
        "schemaVersion": "realytics-domain-agent-runtime-envelope-v1",
        "parentRunId": parent_run_id,
        "agentRunId": execution.run.run_id,
        "agentId": execution.run.agent_id,
        "agentVersion": execution.run.agent_version,
        "taskType": execution.run.task_type,
        "status": execution.run.status.value,
        "stateVersion": execution.run.state_version,
        "attemptCount": execution.run.attempt_count,
        "replayed": execution.replayed,
        "stepIds": execution.step_ids,
        "brainUsageEventIds": execution.brain_usage_event_ids,
        "inputHash": execution.run.input_hash,
    }


def _result_mapper(raw: Any, request, context, manifest) -> AgentResult:
    if isinstance(raw, BaseModel):
        output: Any = raw.model_dump(by_alias=True, mode="json")
    elif isinstance(raw, dict):
        output = dict(raw)
    else:
        output = {"__wrappedScalarResult__": True, "value": raw}
    locators = _collect_locators(output)
    evidence_ids = sorted(_collect_values(output, {"evidenceId", "evidence_id"}))
    explicit_count = sum(1 for locator in locators if locator.is_explicit)
    quality = QualityMetrics(
        schema_valid=True,
        evidence_coverage=min(1.0, explicit_count / max(1, len(evidence_ids))),
        consistency_score=1.0,
        completeness_score=1.0 if output else 0.0,
        deterministic_checks_passed=True,
    )
    status_text = str(output.get("status") or output.get("reviewStatus") or "completed").lower()
    needs_review = status_text in {
        "blocked",
        "blocking_issue",
        "needs_review",
        "needs_human_review",
        "partial",
        "warning",
        "caution",
    }
    return AgentResult(
        run_id=context.run_id,
        request_id=request.request_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        status=AgentRunStatus.COMPLETED_WITH_REVIEW if needs_review else AgentRunStatus.COMPLETED,
        output_schema_name=manifest.output_schema_name,
        output_schema_version=manifest.output_schema_version,
        output=output,
        evidence_ids=evidence_ids,
        source_locators=locators,
        confidence=quality.deterministic_confidence(),
        quality_metrics=quality,
        requires_human_review=needs_review,
        review_reasons=["Domain Agent returned a review-required status."] if needs_review else [],
        created_at=datetime.now(timezone.utc),
        metadata={
            "adapter": "LegacyAgentAdapter",
            "toolCallCount": int(output.get("toolCallCount") or 0),
            "llmCallCount": int(output.get("llmCallCount") or 0),
        },
    )


def _collect_values(value: Any, keys: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys:
                if isinstance(item, list):
                    found.update(str(entry) for entry in item if entry)
                elif item:
                    found.add(str(item))
            else:
                found.update(_collect_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_values(item, keys))
    return found


def _collect_locators(value: Any) -> list[SourceLocator]:
    raw: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"sourceLocator", "source_locator"} and isinstance(item, dict):
                raw.append(item)
            elif key in {"sourceLocators", "source_locators"} and isinstance(item, list):
                raw.extend(entry for entry in item if isinstance(entry, dict))
            else:
                raw.extend(_raw_locators(item))
    elif isinstance(value, list):
        raw.extend(_raw_locators(value))
    result: list[SourceLocator] = []
    seen: set[str] = set()
    for item in raw:
        try:
            locator = SourceLocator.from_mapping(item)
        except (TypeError, ValueError):
            continue
        key = json.dumps(locator.model_dump(by_alias=True, mode="json"), sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(locator)
    return result


def _raw_locators(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"sourceLocator", "source_locator"} and isinstance(item, dict):
                found.append(item)
            elif key in {"sourceLocators", "source_locators"} and isinstance(item, list):
                found.extend(entry for entry in item if isinstance(entry, dict))
            else:
                found.extend(_raw_locators(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_raw_locators(item))
    return found


def _classification(value: Any) -> DataClassification:
    try:
        return DataClassification(str(value).lower())
    except ValueError:
        return DataClassification.CONFIDENTIAL


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_")
    return normalized or "domain_agent"
