from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from agent_platform.brains.router import CapabilityBrainRouter
from agent_platform.contracts import (
    BrainCapabilities,
    BrainLimits,
    BrainPrivacyPolicy,
    BrainProfile,
    BrainProviderHealth,
    BrainRoutingRequest,
    DataClassification,
    ProviderHealthStatus,
    RiskLevel,
)
from agent_platform.operational import OperationalRepository
from agent_platform.registry import BrainRegistry


class _RouteOnlyBrain:
    def __init__(self, profile: BrainProfile) -> None:
        self.profile = profile

    async def generate(self, _request):
        raise RuntimeError("Route-only Brain profiles cannot execute calls")

    async def health_check(self) -> bool:
        return True

    def estimate_cost(self, _request) -> float:
        return 0.0


def route_ai_connection_context(
    context: dict[str, Any],
    *,
    task_type: str = "structured_extraction",
    context_token_estimate: int = 0,
    estimated_output_tokens: int = 2000,
) -> dict[str, Any]:
    """Apply the central Brain Router immediately before any legacy AIConnection call."""
    _, result = route_ai_connection_candidates(
        [context],
        task_type=task_type,
        context_token_estimate=context_token_estimate,
        estimated_output_tokens=estimated_output_tokens,
    )
    return result


def route_ai_connection_candidates(
    contexts: list[dict[str, Any]],
    *,
    task_type: str = "structured_extraction",
    context_token_estimate: int = 0,
    estimated_output_tokens: int = 2000,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Route across BYOK, environment fallback, and local execution candidates."""
    if not contexts:
        raise ValueError("At least one AI execution context is required")
    registry = BrainRegistry()
    candidates_by_profile: dict[str, dict[str, Any]] = {}
    for context in contexts:
        profile = brain_profile_from_ai_context(context)
        if profile.brain_profile_id in candidates_by_profile:
            continue
        candidates_by_profile[profile.brain_profile_id] = context
        registry.register(_RouteOnlyBrain(profile))
    repository = OperationalRepository()
    context = contexts[0]
    preferred_profile = brain_profile_from_ai_context(context).brain_profile_id
    classification = _classification(context.get("data_classification") or context.get("dataClassification"))
    result = CapabilityBrainRouter(registry, repository=repository).route(
        BrainRoutingRequest(
            task_type=task_type,
            risk_level=_risk_level(context.get("risk_level") or context.get("riskLevel")),
            data_classification=classification,
            context_token_estimate=max(0, int(context_token_estimate)),
            estimated_output_tokens=max(0, int(estimated_output_tokens)),
            tenant_id=str(context.get("tenant_id") or context.get("tenantId") or "default"),
            user_id=str(context.get("user_id") or context.get("userId") or "anonymous"),
            project_id=_optional_string(context.get("project_id") or context.get("projectId")),
            run_id=_optional_string(context.get("run_id") or context.get("runId")),
            request_id=_optional_string(context.get("request_id") or context.get("requestId")),
            preferred_profile_id=preferred_profile,
            prohibited_providers=[str(item) for item in context.get("prohibited_providers") or []],
            max_cost_usd=float(context.get("max_cost_usd") or 0),
            local_only=bool(context.get("local_only")),
        )
    )
    selected = candidates_by_profile.get(str(result.selected_profile_id)) if result.selected_profile_id else None
    return selected, result.model_dump(by_alias=True, mode="json")


def record_brain_provider_result(
    context: dict[str, Any],
    *,
    success: bool,
    latency_ms: int | None = None,
    error: str | None = None,
) -> BrainProviderHealth:
    repository = OperationalRepository()
    profile = brain_profile_from_ai_context(context)
    tenant_id = str(context.get("tenant_id") or context.get("tenantId") or "default")
    health_id = f"brain-health:{tenant_id}:{profile.brain_profile_id}"
    current = repository.get(BrainProviderHealth, health_id)
    failures = 0 if success else int(current.consecutive_failures if current else 0) + 1
    status = ProviderHealthStatus.HEALTHY if success else (
        ProviderHealthStatus.UNAVAILABLE if failures >= 3 else ProviderHealthStatus.DEGRADED
    )
    now = datetime.now(timezone.utc)
    record = BrainProviderHealth(
        health_id=health_id,
        tenant_id=tenant_id,
        brain_profile_id=profile.brain_profile_id,
        provider=profile.provider,
        model=profile.model,
        status=status,
        consecutive_failures=failures,
        latency_ms=latency_ms,
        failure_code=None if success else "BRAIN_CALL_FAILED",
        failure_message=None if success else (error or "Unknown provider failure")[:1000],
        checked_at=now,
        next_check_at=None if success else now + timedelta(seconds=min(300, 2 ** min(failures, 8))),
        metadata={"source": "AIConnection", "credentialsPersisted": False},
    )
    return repository.save(record)


def brain_profile_from_ai_context(context: dict[str, Any]) -> BrainProfile:
    provider = str(context.get("provider") or "unknown")
    model = str(context.get("model") or "unknown")
    is_local = bool(context.get("is_local"))
    allowed = list(DataClassification) if is_local else [
        DataClassification.PUBLIC,
        DataClassification.INTERNAL,
        DataClassification.CONFIDENTIAL,
    ]
    secret_ref = None
    if context.get("api_key"):
        secret_ref = f"ephemeral-byok:{context.get('user_id') or 'anonymous'}"
    elif context.get("api_key_env"):
        secret_ref = f"env:{context.get('api_key_env')}"
    return BrainProfile(
        brain_profile_id=str(context.get("brain_profile_id") or f"runtime:{provider}:{model}"),
        provider=provider,
        model=model,
        capabilities=BrainCapabilities(
            structured_output=bool(context.get("supports_json_mode")),
            tool_calling=bool(context.get("supports_tool_calling")),
            long_context=True,
            vision=bool(context.get("supports_vision")),
            code_generation=bool(context.get("supports_code_generation")),
            korean_reasoning_level=str(context.get("korean_reasoning_level") or "medium"),
        ),
        limits=BrainLimits(
            max_context_tokens=int(context.get("max_context_tokens") or 100_000),
            max_output_tokens=int(context.get("max_output_tokens") or 8_000),
            max_cost_per_run_usd=float(context.get("max_cost_per_run_usd") or 10),
        ),
        privacy=BrainPrivacyPolicy(
            allowed_data_classifications=allowed,
            data_retention_allowed=False,
            local_execution=is_local,
        ),
        secret_ref=secret_ref,
        preferred_tasks=["structured_extraction", "pod_ai_review", "report_review"],
        metadata={
            "healthStatus": "healthy" if context.get("direct_model_call") else "unavailable",
            "estimatedCostPer1kTokensUsd": float(context.get("estimated_cost_per_1k_tokens_usd") or 0),
            "selectionSource": context.get("selection_source"),
        },
    )


def _classification(value: Any) -> DataClassification:
    try:
        return DataClassification(str(value or "confidential").lower())
    except ValueError:
        return DataClassification.CONFIDENTIAL


def _risk_level(value: Any) -> RiskLevel:
    try:
        return RiskLevel(str(value or "high").lower())
    except ValueError:
        return RiskLevel.HIGH


def _optional_string(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None
