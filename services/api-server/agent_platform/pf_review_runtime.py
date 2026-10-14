from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from agent_platform.contracts import (
    ActorContext,
    AgentExecutionContext,
    AgentManifest,
    AgentRequest,
    DataClassification,
    EvidenceRequirement,
    ExecutionConstraints,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    ImplementationLevel,
    ProjectContext,
    ReviewRequirement,
    RunBudget,
)
from agent_platform.registry import AgentRegistry
from agent_platform.runtime import DurableAgentRuntime, LegacyAgentAdapter, StorageAgentRunRepository
from use_cases.pf_review import run_and_save_pf_review_use_case


PF_REVIEW_AGENT_ID = "pf_review_orchestration_agent"
PF_REVIEW_MANIFEST = AgentManifest(
    agent_id=PF_REVIEW_AGENT_ID,
    version="1.0.5",
    display_name="PF Review Orchestration Agent",
    domain="pf_credit",
    role="Execute and persist the deterministic PF review workflow",
    description=(
        "Runs the existing PF review orchestration behind the common durable Agent contract. "
        "Financial calculations remain deterministic and evidence-aware."
    ),
    implementation_level=ImplementationLevel.PRODUCTION_CRITICAL,
    supported_asset_types=[],
    supported_task_types=["pf_review"],
    input_schema_name="PFReviewRunRequest",
    input_schema_version="1.0",
    output_schema_name="PFReviewRunResult",
    output_schema_version="1.0",
    supported_data_classifications=[
        DataClassification.INTERNAL,
        DataClassification.CONFIDENTIAL,
        DataClassification.RESTRICTED,
    ],
    execution_policy=ExecutionPolicy(
        mode=ExecutionMode.DETERMINISTIC_FIRST,
        llm_required=False,
        human_review=ReviewRequirement.CONDITIONAL,
        evidence_requirement=EvidenceRequirement.OPTIONAL,
        deterministic_authority_required=True,
        max_execution_seconds=1800,
        max_llm_calls=20,
        max_tool_calls=100,
    ),
    failure_policy=FailurePolicy(
        max_retries=2,
        retryable_error_codes=["PROVIDER_TIMEOUT", "CONNECTOR_TIMEOUT", "DATABASE_UNAVAILABLE"],
        deterministic_fallback=True,
        alternate_brain_fallback=True,
        fail_closed=True,
    ),
    rerun_triggers=["evidence_added", "assumption_approved", "calculation_input_changed"],
    metadata={"durableRuntime": "realytics-durable-agent-runtime-v1"},
)


def run_pf_review_with_durable_runtime(
    orchestration_repository: Any,
    project_id: str,
    project_data: dict[str, Any],
    options: dict[str, Any] | None = None,
    *,
    agent_run_id: str | None = None,
) -> dict[str, Any]:
    """Execute PF review with persisted lifecycle, idempotency, retry and outbox."""
    options = dict(options or {})
    metadata = project_data.get("_meta") if isinstance(project_data.get("_meta"), dict) else {}
    tenant_id = str(options.get("tenantId") or metadata.get("tenantId") or metadata.get("tenant_id") or "default")
    user_id = str(options.get("requestedBy") or options.get("requested_by") or metadata.get("lastUpdatedBy") or "system")
    now = datetime.now(timezone.utc)
    run_id = agent_run_id or f"agent-run:pf-review:{uuid4()}"

    def handler(request: AgentRequest, context: AgentExecutionContext, _plan: object) -> dict[str, Any]:
        repository = context.get_runtime_value("orchestration_repository")
        return run_and_save_pf_review_use_case(
            repository,
            str(request.project.project_id if request.project else project_id),
            dict(request.payload.get("projectData") or {}),
            dict(request.payload.get("options") or {}),
        )

    registry = AgentRegistry()
    registry.register(LegacyAgentAdapter(manifest=PF_REVIEW_MANIFEST, handler=handler))
    request = AgentRequest(
        request_id=f"agent-request:pf-review:{uuid4()}",
        agent_id=PF_REVIEW_AGENT_ID,
        agent_version=PF_REVIEW_MANIFEST.version,
        task_type="pf_review",
        actor=ActorContext(user_id=user_id, tenant_id=tenant_id, roles=list(options.get("actorRoles") or [])),
        project=ProjectContext(
            project_id=project_id,
            asset_type=_asset_type(project_data),
            project_version=str(metadata.get("projectVersion") or metadata.get("updated_at") or "") or None,
        ),
        constraints=ExecutionConstraints(
            data_classification=DataClassification.CONFIDENTIAL,
            timeout_seconds=PF_REVIEW_MANIFEST.execution_policy.max_execution_seconds,
            deterministic_only=False,
        ),
        input_schema_name=PF_REVIEW_MANIFEST.input_schema_name,
        input_schema_version=PF_REVIEW_MANIFEST.input_schema_version,
        payload={"projectData": project_data, "options": _without_secrets(options)},
        idempotency_key=_idempotency_key(
            project_id,
            project_data,
            options,
            agent_version=PF_REVIEW_MANIFEST.version,
        ),
        requested_at=now,
        metadata={"source": "pf_review_async_job", "secretsPersisted": False},
    )
    context = AgentExecutionContext(
        run_id=run_id,
        correlation_id=str(options.get("correlationId") or run_id),
        trace_id=str(options.get("traceId") or run_id),
        actor=request.actor,
        project=request.project,
        constraints=request.constraints,
        budget=RunBudget(
            max_cost_usd=float(options.get("maxCostUsd") or 0),
            max_llm_calls=PF_REVIEW_MANIFEST.execution_policy.max_llm_calls,
            max_tool_calls=PF_REVIEW_MANIFEST.execution_policy.max_tool_calls,
        ),
        started_at=now,
        deadline_at=now + timedelta(seconds=PF_REVIEW_MANIFEST.execution_policy.max_execution_seconds),
    )
    context.set_runtime_value("orchestration_repository", orchestration_repository)
    execution = DurableAgentRuntime(
        registry=registry,
        repository=StorageAgentRunRepository(),
    ).execute_sync(request, context)
    output = dict(execution.result.output or {})
    return {
        "output": output,
        "agentRunId": execution.run.run_id,
        "agentRunStatus": execution.run.status.value,
        "replayed": execution.replayed,
        "stepIds": execution.step_ids,
    }


def _idempotency_key(
    project_id: str,
    project_data: dict[str, Any],
    options: dict[str, Any],
    *,
    agent_version: str = PF_REVIEW_MANIFEST.version,
) -> str:
    payload = {
        "agentVersion": agent_version,
        "projectId": project_id,
        "projectData": project_data,
        "options": _without_secrets(options),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"pf-review:{project_id}:{digest}"


def _without_secrets(value: Any) -> Any:
    secret_keys = {"apikey", "api_key", "authorization", "token", "secret", "password"}
    if isinstance(value, dict):
        return {
            key: _without_secrets(item)
            for key, item in value.items()
            if str(key).lower() not in secret_keys
        }
    if isinstance(value, list):
        return [_without_secrets(item) for item in value]
    return value


def _asset_type(project_data: dict[str, Any]) -> str | None:
    project = project_data.get("project") if isinstance(project_data.get("project"), dict) else {}
    return project.get("assetType") or project.get("type") or project_data.get("assetType")
