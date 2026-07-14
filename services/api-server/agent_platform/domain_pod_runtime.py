from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

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
from agent_platform.registry import AgentRegistry
from agent_platform.operational import OperationalRepository
from agent_platform.runtime import (
    DurableAgentRuntime,
    LegacyAgentAdapter,
    StorageAgentRunRepository,
    agent_request_input_hash,
)
from agents.ai_connection import safe_ai_context
from agent_platform.tools import ToolExecutor, build_default_tool_registry


DOMAIN_PODS = {
    "land_legal",
    "market_product",
    "cost_schedule",
    "feasibility_cashflow",
    "pf_credit",
    "public_policy",
}


def run_domain_pod_with_contract(
    *,
    project_id: str,
    pod_key: str,
    input_snapshot: dict[str, Any],
    project_data: dict[str, Any],
    options: dict[str, Any],
    executor: Callable[[], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if pod_key not in DOMAIN_PODS:
        raise ValueError(f"Unsupported domain Pod: {pod_key}")
    manifest = _manifest(pod_key)

    def handler(_request, context, _plan):
        callback = context.get_runtime_value("domain_pod_executor")
        if not callable(callback):
            raise RuntimeError("DOMAIN_POD_EXECUTOR_MISSING")
        return callback()

    registry = AgentRegistry()
    registry.register(LegacyAgentAdapter(
        manifest=manifest,
        handler=handler,
        result_mapper=_result_mapper,
    ))
    now = datetime.now(timezone.utc)
    safe_context = safe_ai_context(options.get("ai_context") or options.get("aiContext") or {})
    actor = ActorContext(
        user_id=str(safe_context.get("user_id") or options.get("userId") or "anonymous"),
        tenant_id=str(safe_context.get("tenant_id") or options.get("tenantId") or "default"),
        roles=[str(item) for item in options.get("actorRoles") or []],
    )
    classification = _classification(options.get("dataClassification") or "confidential")
    input_hash = _digest(input_snapshot)
    run_id = str(options.get("domainAgentRunId") or f"agent-run:{pod_key}:{uuid4()}")
    request = AgentRequest(
        request_id=f"agent-request:{pod_key}:{uuid4()}",
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        task_type=f"chief_pod:{pod_key}",
        actor=actor,
        project=ProjectContext(
            project_id=project_id,
            asset_type=str(project_data.get("assetType") or project_data.get("projectType") or "") or None,
            project_version=str(project_data.get("updatedAt") or "") or None,
        ),
        constraints=ExecutionConstraints(
            data_classification=classification,
            max_cost_usd=float(options.get("maxCostUsd") or 0),
            timeout_seconds=manifest.execution_policy.max_execution_seconds,
            preferred_brain_profile_id=safe_context.get("brain_profile_id"),
            prohibited_providers=[str(item) for item in options.get("prohibitedProviders") or []],
            local_only=bool(options.get("localOnly")),
        ),
        input_schema_name="ChiefPodInputSnapshot",
        input_schema_version="2.0",
        payload={"podKey": pod_key, "inputSnapshot": input_snapshot},
        upstream_results=[
            str(item.get("artifactKey"))
            for item in input_snapshot.get("dependencyArtifacts") or []
            if isinstance(item, dict) and item.get("artifactKey")
        ],
        # Replaced below with the canonical AgentRequest hash. A temporary value
        # is needed only because the contract requires the field at validation.
        idempotency_key=f"pending:{input_hash}",
        requested_at=now,
        metadata={
            "source": "ChiefDealOrchestrator",
            "orchestrationRunId": options.get("runId") or options.get("run_id"),
            "requestPayloadPersisted": False,
        },
    )
    # The idempotency key must cover exactly the same execution semantics as the
    # persisted input hash, including Agent/schema versions and constraints.
    # This lets an exact replay reuse a Run while changed inputs or a deployed
    # Agent version create a new durable Run.
    request = request.model_copy(update={
        "idempotency_key": agent_request_input_hash(request),
    })
    context = AgentExecutionContext(
        run_id=run_id,
        correlation_id=str(options.get("correlationId") or options.get("runId") or run_id),
        trace_id=str(options.get("traceId") or options.get("runId") or run_id),
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
    context.set_runtime_value("domain_pod_executor", executor)
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
    artifact = dict(execution.result.output or {})
    envelope = {
        "schemaVersion": "realytics-domain-pod-runtime-envelope-v1",
        "agentRunId": execution.run.run_id,
        "agentId": execution.run.agent_id,
        "status": execution.run.status.value,
        "stateVersion": execution.run.state_version,
        "attemptCount": execution.run.attempt_count,
        "replayed": execution.replayed,
        "stepIds": execution.step_ids,
        "brainUsageEventIds": execution.brain_usage_event_ids,
        "inputHash": input_hash,
    }
    return artifact, envelope


def _manifest(pod_key: str) -> AgentManifest:
    return AgentManifest(
        agent_id=f"chief_{pod_key}_agent",
        version="1.1.0",
        display_name=f"Chief {pod_key.replace('_', ' ').title()} Agent",
        domain=pod_key,
        role="Execute deterministic domain Pod and persist its control-plane lifecycle",
        description="Contract wrapper for a Chief Deal Room domain Pod.",
        implementation_level=ImplementationLevel.CONTROLLED,
        supported_task_types=[f"chief_pod:{pod_key}"],
        input_schema_name="ChiefPodInputSnapshot",
        input_schema_version="2.0",
        output_schema_name="ChiefPodArtifact",
        output_schema_version="2.0",
        supported_data_classifications=list(DataClassification),
        execution_policy=ExecutionPolicy(
            mode=ExecutionMode.DETERMINISTIC_FIRST,
            llm_required=False,
            human_review=ReviewRequirement.CONDITIONAL,
            # The Chief Orchestrator has already applied each Pod's missing-input
            # gate. The Pod may also be the component that creates first-class
            # evidence, so an empty pre-run evidence array cannot block it here.
            evidence_requirement=EvidenceRequirement.OPTIONAL,
            deterministic_authority_required=pod_key in {"feasibility_cashflow", "pf_credit"},
            max_execution_seconds=600,
            max_tool_calls=50,
            max_llm_calls=5,
        ),
        failure_policy=FailurePolicy(
            max_retries=1,
            retryable_error_codes=["PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMIT", "UPSTREAM_TEMPORARY_FAILURE"],
            deterministic_fallback=True,
            fail_closed=pod_key in {"feasibility_cashflow", "pf_credit"},
        ),
        tags=["chief-pod", pod_key, "durable-runtime"],
    )


def _result_mapper(raw, request, context, manifest) -> AgentResult:
    artifact = dict(raw or {})
    evidence_ids = sorted(_collect_values(artifact, {"evidenceId", "evidence_id"}))
    locators = _collect_locators(artifact)
    explicit_count = sum(1 for item in locators if item.is_explicit)
    expected = max(1, len(evidence_ids))
    status_value = str(artifact.get("status") or artifact.get("analysisStatus") or "completed")
    needs_review = status_value in {"warning", "needs_review", "partial", "blocked"}
    quality = QualityMetrics(
        schema_valid=bool(artifact),
        evidence_coverage=min(1.0, explicit_count / expected),
        consistency_score=0.8,
        completeness_score=0.65 if needs_review else 0.9,
        deterministic_checks_passed=bool(artifact),
    )
    return AgentResult(
        run_id=context.run_id,
        request_id=request.request_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        status=AgentRunStatus.COMPLETED_WITH_REVIEW if needs_review else AgentRunStatus.COMPLETED,
        output_schema_name=manifest.output_schema_name,
        output_schema_version=manifest.output_schema_version,
        output=artifact,
        evidence_ids=evidence_ids,
        source_locators=locators,
        confidence=quality.deterministic_confidence(),
        quality_metrics=quality,
        requires_human_review=needs_review,
        review_reasons=["Domain Pod reported an incomplete or review-required status."] if needs_review else [],
        created_at=datetime.now(timezone.utc),
        metadata={
            "podKey": request.payload.get("podKey"),
            "toolCallCount": int(artifact.get("toolCallCount") or 0),
            "llmCallCount": int(artifact.get("llmCallCount") or 0),
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
                raw.extend(item.model_dump(by_alias=True, mode="json") for item in _collect_locators(item))
    elif isinstance(value, list):
        for item in value:
            raw.extend(entry.model_dump(by_alias=True, mode="json") for entry in _collect_locators(item))
    result: list[SourceLocator] = []
    seen: set[str] = set()
    for item in raw:
        try:
            locator = SourceLocator.from_mapping(item)
        except (TypeError, ValueError):
            continue
        key = locator.model_dump_json(by_alias=True)
        if key not in seen:
            seen.add(key)
            result.append(locator)
    return result


def _classification(value: Any) -> DataClassification:
    try:
        return DataClassification(str(value).lower())
    except ValueError:
        return DataClassification.CONFIDENTIAL


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
