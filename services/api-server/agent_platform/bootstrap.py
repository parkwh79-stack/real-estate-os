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
    AgentResult,
    AgentRunStatus,
    Blocker,
    BrainCapabilities,
    BrainLimits,
    BrainPrivacyPolicy,
    BrainProfile,
    BrainRoutingRequest,
    BrainUsage,
    CapabilityRequirement,
    DataClassification,
    EvidenceReference,
    EvidenceRequirement,
    ExecutionConstraints,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    Finding,
    ImplementationLevel,
    ProjectContext,
    QualityMetrics,
    ReviewRequirement,
    RunBudget,
    SourceLocator,
)
from agent_platform.registry import AgentRegistry
from agent_platform.registry import BrainRegistry
from agent_platform.brains import AIConnectionBrainAdapter, CapabilityBrainRouter
from agent_platform.runtime import DurableAgentRuntime, LegacyAgentAdapter, StorageAgentRunRepository
from agents.ai_connection import safe_ai_context
from agents.pod_ai_reviewer_agent import PodAIReviewerAgent


POD_AI_REVIEW_AGENT_ID = "pod_ai_reviewer_agent"
POD_AI_REVIEW_MANIFEST = AgentManifest(
    agent_id=POD_AI_REVIEW_AGENT_ID,
    version="1.1.0",
    display_name="Pod AI Reviewer Agent",
    domain="chief_orchestration",
    role="Evidence-aware review of deterministic Pod artifacts",
    description=(
        "Reviews persisted Chief Pod artifacts for evidence gaps, conflicts, and handoffs. "
        "It never replaces deterministic calculations or approval decisions."
    ),
    implementation_level=ImplementationLevel.CONTROLLED,
    supported_asset_types=[],
    supported_task_types=["pod_ai_review"],
    input_schema_name="ChiefPodArtifact",
    input_schema_version="2.0",
    output_schema_name="PodAIReview",
    output_schema_version="1.0",
    required_capabilities=[CapabilityRequirement(name="structured_output", required=False)],
    supported_data_classifications=[
        DataClassification.PUBLIC,
        DataClassification.INTERNAL,
        DataClassification.CONFIDENTIAL,
        DataClassification.RESTRICTED,
    ],
    execution_policy=ExecutionPolicy(
        mode=ExecutionMode.DETERMINISTIC_FIRST,
        llm_required=False,
        human_review=ReviewRequirement.CONDITIONAL,
        evidence_requirement=EvidenceRequirement.OPTIONAL,
        deterministic_authority_required=False,
        max_execution_seconds=180,
        max_llm_calls=1,
    ),
    failure_policy=FailurePolicy(
        max_retries=1,
        retryable_error_codes=["PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMIT"],
        deterministic_fallback=True,
        alternate_brain_fallback=True,
        fail_closed=False,
    ),
    rerun_triggers=["pod_artifact_changed", "evidence_added", "assumption_approved"],
    recommended_next_agents=["ChiefDealOrchestrator"],
    tags=["pod-review", "evidence-aware", "non-authoritative-llm"],
    metadata={
        "migration": "legacy-adapter",
        "productionLimitations": [
            "Brain selection still enters through the existing user AI context for this first vertical slice.",
        ],
        "durableRuntime": "realytics-durable-agent-runtime-v1",
    },
)


def build_default_agent_registry(*, reviewer: PodAIReviewerAgent | None = None) -> AgentRegistry:
    pod_reviewer = reviewer or PodAIReviewerAgent()

    def handler(request: AgentRequest, context: AgentExecutionContext, _plan: object) -> dict[str, Any]:
        payload = request.payload
        runtime_options = context.get_runtime_value("pod_review_options", {})
        return pod_reviewer.review_pod(
            project_id=str(request.project.project_id if request.project else ""),
            pod_key=str(payload["podKey"]),
            source_artifact=dict(payload["sourceArtifact"]),
            source_artifact_key=payload.get("sourceArtifactKey"),
            project_data=dict(payload.get("projectData") or {}),
            options=runtime_options,
        )

    registry = AgentRegistry()
    registry.register(
        LegacyAgentAdapter(
            manifest=POD_AI_REVIEW_MANIFEST,
            handler=handler,
            result_mapper=_map_pod_review_result,
        )
    )
    return registry


_DEFAULT_AGENT_REGISTRY: AgentRegistry | None = None


def default_agent_registry() -> AgentRegistry:
    global _DEFAULT_AGENT_REGISTRY
    if _DEFAULT_AGENT_REGISTRY is None:
        _DEFAULT_AGENT_REGISTRY = build_default_agent_registry()
    return _DEFAULT_AGENT_REGISTRY


def run_pod_ai_review_with_contract(
    *,
    project_id: str,
    pod_key: str,
    source_artifact: dict[str, Any],
    source_artifact_key: str | None,
    project_data: dict[str, Any] | None,
    options: dict[str, Any] | None,
    registry: AgentRegistry | None = None,
) -> dict[str, Any]:
    """Run the existing Pod reviewer through the common contract without persisting secrets."""
    options = dict(options or {})
    ai_context = dict(options.get("ai_context") or options.get("aiContext") or {})
    safe_context = safe_ai_context(ai_context)
    now = datetime.now(timezone.utc)
    classification = _classification(options.get("dataClassification") or "confidential")
    actor = ActorContext(
        user_id=str(safe_context.get("user_id") or "anonymous"),
        tenant_id=str(safe_context.get("tenant_id") or "default"),
        roles=[str(item) for item in options.get("actorRoles") or []],
    )
    project = ProjectContext(
        project_id=project_id,
        asset_type=_asset_type(project_data or {}),
        project_version=str((project_data or {}).get("updatedAt") or "") or None,
    )
    constraints = ExecutionConstraints(
        data_classification=classification,
        max_cost_usd=float(options.get("maxCostUsd") or 0),
        timeout_seconds=POD_AI_REVIEW_MANIFEST.execution_policy.max_execution_seconds,
        preferred_brain_profile_id=safe_context.get("brain_profile_id"),
        prohibited_providers=[str(item) for item in options.get("prohibitedProviders") or []],
        local_only=bool(options.get("localOnly")),
        deterministic_only=str(options.get("podAiReviewMode") or "").lower() in {"deterministic", "deterministic_only"},
    )
    evidence = _evidence_references(source_artifact)
    request_id = f"agent-request:{POD_AI_REVIEW_AGENT_ID}:{uuid4()}"
    orchestration_run_id = str(options.get("runId") or options.get("run_id") or "") or None
    run_id = str(options.get("agentRunId") or options.get("agent_run_id") or f"agent-run:{uuid4()}")
    runtime_identity = {
        "provider": safe_context.get("provider"),
        "model": safe_context.get("model"),
        "brainProfileId": safe_context.get("brain_profile_id"),
        "reviewMode": options.get("podAiReviewMode"),
    }
    request = AgentRequest(
        request_id=request_id,
        agent_id=POD_AI_REVIEW_AGENT_ID,
        agent_version=POD_AI_REVIEW_MANIFEST.version,
        task_type="pod_ai_review",
        actor=actor,
        project=project,
        constraints=constraints,
        input_schema_name=POD_AI_REVIEW_MANIFEST.input_schema_name,
        input_schema_version=POD_AI_REVIEW_MANIFEST.input_schema_version,
        payload={
            "podKey": pod_key,
            "sourceArtifact": source_artifact,
            "sourceArtifactKey": source_artifact_key,
            "projectData": project_data or {},
        },
        evidence=evidence,
        upstream_results=[source_artifact_key] if source_artifact_key else [],
        idempotency_key=_idempotency_key(
            project_id,
            pod_key,
            source_artifact_key,
            source_artifact,
            runtime_identity=runtime_identity,
        ),
        requested_at=now,
        metadata={
            "source": "ChiefDealOrchestrator",
            "orchestrationRunId": orchestration_run_id,
            "brainSelection": runtime_identity,
            "secretBearingFieldsPersisted": False,
        },
    )
    brain_routing = _route_runtime_brain(
        ai_context=ai_context,
        classification=classification,
        constraints=constraints,
        actor=actor,
    )
    runtime_options = dict(options)
    if brain_routing.get("blocked") and not constraints.deterministic_only:
        runtime_options["podAiReviewMode"] = "deterministic"
        runtime_options["brainRoutingFallbackReason"] = brain_routing.get("blockedReason")
    context = AgentExecutionContext(
        run_id=run_id,
        correlation_id=str(options.get("correlationId") or orchestration_run_id or run_id),
        trace_id=str(options.get("traceId") or orchestration_run_id or run_id),
        actor=actor,
        project=project,
        constraints=constraints,
        budget=RunBudget(
            max_cost_usd=constraints.max_cost_usd or 0,
            max_llm_calls=POD_AI_REVIEW_MANIFEST.execution_policy.max_llm_calls,
            max_tool_calls=POD_AI_REVIEW_MANIFEST.execution_policy.max_tool_calls,
        ),
        selected_brain_profile_id=brain_routing.get("selectedProfileId") or safe_context.get("brain_profile_id"),
        selected_provider=brain_routing.get("selectedProvider") or safe_context.get("provider"),
        selected_model=brain_routing.get("selectedModel") or safe_context.get("model"),
        started_at=now,
        deadline_at=now + timedelta(seconds=POD_AI_REVIEW_MANIFEST.execution_policy.max_execution_seconds),
    )
    # The original options can carry an ephemeral BYOK value. It is kept in a
    # Pydantic PrivateAttr and is therefore absent from model_dump and persistence.
    context.set_runtime_value("pod_review_options", runtime_options)
    active_registry = registry or default_agent_registry()
    runtime = DurableAgentRuntime(
        registry=active_registry,
        repository=StorageAgentRunRepository(),
    )
    execution = runtime.execute_sync(request, context)
    result = execution.result
    agent = active_registry.get(POD_AI_REVIEW_AGENT_ID, POD_AI_REVIEW_MANIFEST.version)
    validation = agent.validate(request, result, context)
    if not validation.valid:
        raise RuntimeError("Pod AI Review contract validation failed: " + ", ".join(item.code for item in validation.issues))
    output = dict(result.output or {})
    output["agentPlatform"] = {
        "schemaVersion": "realytics-agent-platform-envelope-v1",
        "requestId": result.request_id,
        "runId": result.run_id,
        "agentId": result.agent_id,
        "agentVersion": result.agent_version,
        "status": result.status.value,
        "confidence": result.confidence,
        "qualityMetrics": result.quality_metrics.model_dump(by_alias=True, mode="json"),
        "validation": validation.model_dump(by_alias=True, mode="json"),
        "brainRouting": brain_routing,
        "implementationLevel": POD_AI_REVIEW_MANIFEST.implementation_level.value,
        "durableRuntime": {
            "schemaVersion": "realytics-durable-agent-runtime-v1",
            "agentRunId": execution.run.run_id,
            "status": execution.run.status.value,
            "attemptCount": execution.run.attempt_count,
            "maxAttempts": execution.run.max_attempts,
            "stateVersion": execution.run.state_version,
            "replayed": execution.replayed,
            "stepIds": execution.step_ids,
            "brainUsageEventIds": execution.brain_usage_event_ids,
            "orchestrationRunId": orchestration_run_id,
        },
    }
    return output


def _route_runtime_brain(
    *,
    ai_context: dict[str, Any],
    classification: DataClassification,
    constraints: ExecutionConstraints,
    actor: ActorContext,
) -> dict[str, Any]:
    if constraints.deterministic_only:
        return {
            "routingPolicyVersion": "realytics-brain-routing-v1",
            "selectedProfileId": None,
            "blocked": False,
            "deterministicOnly": True,
            "candidates": [],
        }
    if not ai_context.get("direct_model_call"):
        return {
            "routingPolicyVersion": "realytics-brain-routing-v1",
            "selectedProfileId": None,
            "blocked": True,
            "blockedReason": "No callable Brain was available; deterministic Pod review was used.",
            "candidates": [],
        }
    profile = _brain_profile_from_context(ai_context)
    brains = BrainRegistry()
    brains.register(AIConnectionBrainAdapter(profile=profile, ai_context=ai_context))
    max_cost = constraints.max_cost_usd
    if max_cost is None:
        max_cost = profile.limits.max_cost_per_run_usd
    result = CapabilityBrainRouter(brains).route(
        BrainRoutingRequest(
            task_type="pod_ai_review",
            risk_level="high",
            data_classification=classification,
            required_capabilities=POD_AI_REVIEW_MANIFEST.required_capabilities,
            context_token_estimate=16_000,
            estimated_output_tokens=2_000,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            preferred_profile_id=profile.brain_profile_id,
            prohibited_providers=constraints.prohibited_providers,
            max_cost_usd=max_cost,
            local_only=constraints.local_only,
        )
    )
    return result.model_dump(by_alias=True, mode="json")


def _brain_profile_from_context(ai_context: dict[str, Any]) -> BrainProfile:
    safe = safe_ai_context(ai_context)
    provider = str(safe.get("provider") or "unknown")
    model = str(safe.get("model") or "unknown")
    is_local = bool(safe.get("is_local"))
    allowed = list(DataClassification) if is_local else [
        DataClassification.PUBLIC,
        DataClassification.INTERNAL,
        DataClassification.CONFIDENTIAL,
    ]
    secret_ref = None
    if ai_context.get("api_key"):
        secret_ref = f"user-ai-profile:{safe.get('user_id') or 'ephemeral'}"
    elif ai_context.get("api_key_env"):
        secret_ref = f"env:{ai_context.get('api_key_env')}"
    return BrainProfile(
        brain_profile_id=f"runtime:{provider}:{model}",
        provider=provider,
        model=model,
        capabilities=BrainCapabilities(
            structured_output=bool(safe.get("supports_json_mode") or provider == "anthropic"),
            long_context=True,
            korean_reasoning_level="medium",
        ),
        limits=BrainLimits(
            max_context_tokens=int(safe.get("max_context_tokens") or 100_000),
            max_output_tokens=int(safe.get("max_output_tokens") or 8_000),
            max_cost_per_run_usd=float(safe.get("max_cost_per_run_usd") or 10),
        ),
        privacy=BrainPrivacyPolicy(
            allowed_data_classifications=allowed,
            data_retention_allowed=False,
            local_execution=is_local,
        ),
        secret_ref=secret_ref,
        preferred_tasks=["pod_ai_review"],
        quality_tier=str(safe.get("quality_tier") or "standard"),
        cost_tier="unknown",
        latency_tier="standard",
        metadata={
            "healthStatus": "healthy",
            "costKnown": False,
            "selectionSource": safe.get("selection_source"),
            "estimatedCostPer1kTokensUsd": 0,
        },
    )


def _map_pod_review_result(
    raw: Any,
    request: AgentRequest,
    context: AgentExecutionContext,
    manifest: AgentManifest,
) -> AgentResult:
    output = dict(raw or {})
    findings: list[Finding] = []
    blockers: list[Blocker] = []
    all_locators: list[SourceLocator] = []
    evidence_ids = {item.evidence_id for item in request.evidence}
    for index, item in enumerate(output.get("findings") or []):
        if not isinstance(item, dict):
            continue
        locators = _typed_locators(item.get("sourceLocators") or [])
        all_locators.extend(locators)
        item_evidence = [str(value) for value in item.get("evidenceIds") or []]
        evidence_ids.update(item_evidence)
        severity = str(item.get("severity") or "medium")
        finding_id = str(item.get("findingId") or f"finding:pod-review:{index}:{uuid4()}")
        message = str(item.get("message") or item.get("description") or "Pod review finding")
        findings.append(
            Finding(
                finding_id=finding_id,
                category=str(item.get("category") or "review"),
                title=str(item.get("title") or item.get("category") or "Pod review"),
                description=message,
                severity=severity,
                evidence_ids=item_evidence,
                source_locators=locators,
                confidence=float(output.get("confidence") or 0.5),
            )
        )
        if severity == "blocking":
            blockers.append(
                Blocker(
                    code=str(item.get("code") or str(item.get("category") or "POD_REVIEW_BLOCKER").upper()),
                    severity="blocking",
                    message=message,
                    resolution_hint=item.get("recommendedAction"),
                )
            )
    all_locators.extend(_typed_locators(output.get("sourceLocators") or []))
    all_locators = _deduplicate_locators(all_locators)
    explicit_locator_count = sum(1 for item in all_locators if item.is_explicit)
    evidence_denominator = max(len(evidence_ids), 1)
    evidence_coverage = min(1.0, explicit_locator_count / evidence_denominator)
    deterministic_findings = [item for item in output.get("deterministicFindings") or [] if isinstance(item, dict)]
    has_data_conflict = any(item.get("category") == "data_quality" for item in deterministic_findings)
    review_status = str(output.get("reviewStatus") or "warning")
    quality = QualityMetrics(
        schema_valid=bool(output.get("schemaVersion") and output.get("reviewId")),
        evidence_coverage=evidence_coverage,
        consistency_score=0.65 if has_data_conflict else 0.9,
        completeness_score={"pass": 1.0, "warning": 0.75, "needs_human_review": 0.65, "blocking": 0.45}.get(review_status, 0.6),
        deterministic_checks_passed=bool(deterministic_findings),
        unsupported_claim_count=0,
    )
    high_findings = [item for item in findings if item.severity in {"high", "blocking"}]
    requires_review = bool(high_findings or review_status in {"blocking", "needs_human_review"})
    status = AgentRunStatus.BLOCKED if blockers else (
        AgentRunStatus.COMPLETED_WITH_REVIEW if requires_review or review_status != "pass" else AgentRunStatus.COMPLETED
    )
    ai_used = output.get("aiBrainUsed") if isinstance(output.get("aiBrainUsed"), dict) else {}
    usage = ai_used.get("token_usage") if isinstance(ai_used.get("token_usage"), dict) else {}
    brain_usage = BrainUsage(
        brain_profile_id=ai_used.get("brain_profile_id"),
        provider=ai_used.get("provider"),
        model=ai_used.get("model"),
        input_tokens=usage.get("promptTokens"),
        output_tokens=usage.get("completionTokens"),
    ) if ai_used else None
    actions = [
        str(item.get("recommendedAction"))
        for item in output.get("findings") or []
        if isinstance(item, dict) and item.get("recommendedAction")
    ]
    return AgentResult(
        run_id=context.run_id,
        request_id=request.request_id,
        agent_id=manifest.agent_id,
        agent_version=manifest.version,
        status=status,
        output_schema_name=manifest.output_schema_name,
        output_schema_version=manifest.output_schema_version,
        output=output,
        findings=findings,
        blockers=blockers,
        evidence_ids=sorted(evidence_ids),
        source_locators=all_locators,
        artifact_ids=[str(request.payload.get("sourceArtifactKey"))] if request.payload.get("sourceArtifactKey") else [],
        confidence=quality.deterministic_confidence(),
        recommended_next_agents=list(manifest.recommended_next_agents),
        rerun_recommendations=actions,
        brain_usage=brain_usage,
        quality_metrics=quality,
        requires_human_review=requires_review,
        review_reasons=[item.description for item in high_findings],
        created_at=datetime.now(timezone.utc),
        metadata={
            "adapter": "LegacyAgentAdapter",
            "sourceArtifactKey": request.payload.get("sourceArtifactKey"),
            "llmReviewAccepted": bool(output.get("llmReviewAccepted")),
        },
    )


def _evidence_references(source_artifact: dict[str, Any]) -> list[EvidenceReference]:
    evidence_ids = sorted(_collect_key_values(source_artifact, {"evidenceId", "evidence_id"}))
    locators = _typed_locators(_collect_locator_dicts(source_artifact))
    references: list[EvidenceReference] = []
    for index, evidence_id in enumerate(evidence_ids):
        locator = next((item for item in locators if item.evidence_id == evidence_id), None)
        if locator is None and index < len(locators):
            locator = locators[index]
        references.append(
            EvidenceReference(
                evidence_id=evidence_id,
                evidence_type=(locator.source_type if locator else None) or "agent_artifact_evidence",
                source_name=(locator.source_name if locator else None) or "Chief Pod artifact",
                source_locator=locator,
            )
        )
    return references


def _collect_key_values(value: Any, keys: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in keys and item is not None:
                if isinstance(item, (list, tuple, set)):
                    found.update(str(entry) for entry in item if entry is not None)
                else:
                    found.add(str(item))
            else:
                found.update(_collect_key_values(item, keys))
    elif isinstance(value, list):
        for item in value:
            found.update(_collect_key_values(item, keys))
    return found


def _collect_locator_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"sourceLocator", "source_locator"} and isinstance(item, dict):
                found.append(item)
            elif key in {"sourceLocators", "source_locators"} and isinstance(item, list):
                found.extend(entry for entry in item if isinstance(entry, dict))
            else:
                found.extend(_collect_locator_dicts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_locator_dicts(item))
    return found


def _typed_locators(values: list[Any]) -> list[SourceLocator]:
    result: list[SourceLocator] = []
    for item in values:
        if isinstance(item, SourceLocator):
            result.append(item)
        elif isinstance(item, dict):
            try:
                result.append(SourceLocator.from_mapping(item))
            except (TypeError, ValueError):
                continue
    return result


def _deduplicate_locators(values: list[SourceLocator]) -> list[SourceLocator]:
    result: list[SourceLocator] = []
    seen: set[str] = set()
    for item in values:
        key = json.dumps(item.model_dump(by_alias=True, mode="json"), sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _classification(value: Any) -> DataClassification:
    try:
        return DataClassification(str(value).lower())
    except ValueError:
        return DataClassification.CONFIDENTIAL


def _asset_type(project_data: dict[str, Any]) -> str | None:
    return (
        project_data.get("assetType")
        or project_data.get("asset_type")
        or project_data.get("projectType")
        or project_data.get("project_type")
    )


def _idempotency_key(
    project_id: str,
    pod_key: str,
    source_artifact_key: str | None,
    source_artifact: dict[str, Any],
    *,
    runtime_identity: dict[str, Any] | None = None,
) -> str:
    stable = {
        "projectId": project_id,
        "podKey": pod_key,
        "sourceArtifactKey": source_artifact_key,
        "inputHash": source_artifact.get("inputHash"),
        "outputHash": source_artifact.get("outputHash"),
        "runtimeIdentity": runtime_identity or {},
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode("utf-8")).hexdigest()
