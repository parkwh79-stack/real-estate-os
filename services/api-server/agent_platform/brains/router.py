from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping
from uuid import uuid4

from agent_platform.contracts import (
    BrainRoutingCandidate,
    BrainRoutingRequest,
    BrainRoutingResult,
    BrainProviderHealth,
    BrainRoutingEventRecord,
    BrainRoutingPolicyRecord,
    ProviderHealthStatus,
)
from agent_platform.operational import OperationalRepository
from agent_platform.registry import BrainRegistry


DEFAULT_ROUTING_WEIGHTS = {
    "userPreference": 0.20,
    "taskFit": 0.20,
    "historicalSuccess": 0.20,
    "quality": 0.15,
    "costEfficiency": 0.10,
    "latency": 0.10,
    "availability": 0.05,
}


class CapabilityBrainRouter:
    """Deterministic hard-filter/soft-score Brain selector with audit rationale."""

    def __init__(
        self,
        registry: BrainRegistry,
        *,
        policy_version: str = "realytics-brain-routing-v1",
        weights: Mapping[str, float] | None = None,
        repository: OperationalRepository | None = None,
    ) -> None:
        self.registry = registry
        self.policy_version = policy_version
        self.weights = {**DEFAULT_ROUTING_WEIGHTS, **dict(weights or {})}
        self.repository = repository

    def route(self, request: BrainRoutingRequest) -> BrainRoutingResult:
        request, policy = self._apply_persisted_policy(request)
        candidates: list[BrainRoutingCandidate] = []
        eligible: list[tuple[float, str, object]] = []
        for brain in self.registry.list_enabled():
            profile = brain.profile
            reasons: list[str] = []
            if request.data_classification not in profile.privacy.allowed_data_classifications:
                reasons.append("DATA_CLASSIFICATION_NOT_ALLOWED")
            if profile.provider in request.prohibited_providers:
                reasons.append("PROVIDER_PROHIBITED")
            if policy and policy.allowed_providers and profile.provider not in policy.allowed_providers:
                reasons.append("PROVIDER_NOT_ALLOWED_BY_POLICY")
            if request.local_only and not profile.privacy.local_execution:
                reasons.append("LOCAL_EXECUTION_REQUIRED")
            if request.task_type in profile.prohibited_tasks:
                reasons.append("TASK_PROHIBITED")
            required_tokens = request.context_token_estimate + request.estimated_output_tokens
            if required_tokens > profile.limits.max_context_tokens:
                reasons.append("CONTEXT_LIMIT_EXCEEDED")
            if request.estimated_output_tokens > profile.limits.max_output_tokens:
                reasons.append("OUTPUT_LIMIT_EXCEEDED")
            for capability in request.required_capabilities:
                if capability.required and not profile.capabilities.supports(capability.name, capability.minimum_level):
                    reasons.append(f"CAPABILITY_MISSING:{capability.name}")
            estimated_cost = _estimated_cost(profile, required_tokens)
            if request.max_cost_usd >= 0 and estimated_cost > request.max_cost_usd:
                reasons.append("RUN_BUDGET_EXCEEDED")
            health_status = self._health_status(request.tenant_id, profile.brain_profile_id, profile.metadata)
            if health_status not in {ProviderHealthStatus.HEALTHY, ProviderHealthStatus.DEGRADED}:
                reasons.append("PROVIDER_UNHEALTHY")

            components: dict[str, float] = {}
            score = None
            if not reasons:
                components = _score_components(profile, request, estimated_cost)
                score = round(sum(components[key] * self.weights[key] for key in self.weights), 6)
                eligible.append((score, profile.brain_profile_id, brain))
            candidates.append(
                BrainRoutingCandidate(
                    brain_profile_id=profile.brain_profile_id,
                    eligible=not reasons,
                    rejection_reasons=reasons,
                    score=score,
                    score_components=components,
                    estimated_cost_usd=estimated_cost,
                )
            )

        if not eligible:
            return self._persist_result(request, policy, BrainRoutingResult(
                candidates=candidates,
                routing_policy_version=self.policy_version,
                selected_at=datetime.now(timezone.utc),
                blocked=True,
                blocked_reason="No Brain profile satisfied capability, privacy, health, and budget hard filters.",
            ))
        selected = sorted(eligible, key=lambda item: (-item[0], item[1]))[0][2]
        fallbacks = [item[2].profile.brain_profile_id for item in sorted(eligible, key=lambda item: (-item[0], item[1]))[1:]]
        return self._persist_result(request, policy, BrainRoutingResult(
            selected_profile_id=selected.profile.brain_profile_id,
            selected_provider=selected.profile.provider,
            selected_model=selected.profile.model,
            candidates=sorted(candidates, key=lambda item: item.brain_profile_id),
            routing_policy_version=self.policy_version,
            fallback_profile_ids=fallbacks,
            selected_at=datetime.now(timezone.utc),
        ))

    def _apply_persisted_policy(
        self,
        request: BrainRoutingRequest,
    ) -> tuple[BrainRoutingRequest, BrainRoutingPolicyRecord | None]:
        if self.repository is None:
            return request, None
        policies = self.repository.list(
            BrainRoutingPolicyRecord,
            filters={"tenant_id": request.tenant_id, "active": True},
            limit=100,
        )
        user_policy = next((item for item in policies if item.user_id == request.user_id), None)
        tenant_policy = next((item for item in policies if item.user_id is None), None)
        policy = user_policy or tenant_policy
        if policy is None:
            return request, None
        prohibited = sorted(set(request.prohibited_providers) | set(policy.prohibited_providers))
        preferred = request.preferred_profile_id
        if not preferred and policy.preferred_profile_ids:
            preferred = policy.preferred_profile_ids[0]
        max_cost = request.max_cost_usd
        if policy.max_cost_usd is not None:
            max_cost = min(max_cost, policy.max_cost_usd) if max_cost > 0 else policy.max_cost_usd
        return request.model_copy(update={
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "prohibited_providers": prohibited,
            "preferred_profile_id": preferred,
            "max_cost_usd": max_cost,
            "local_only": request.local_only or policy.local_only,
        }), policy

    def _health_status(
        self,
        tenant_id: str,
        profile_id: str,
        metadata: Mapping[str, object],
    ) -> ProviderHealthStatus:
        if self.repository is not None:
            records = self.repository.list(
                BrainProviderHealth,
                filters={"tenant_id": tenant_id, "brain_profile_id": profile_id},
                limit=1,
            )
            if records:
                return records[0].status
        try:
            return ProviderHealthStatus(str(metadata.get("healthStatus") or "healthy"))
        except ValueError:
            return ProviderHealthStatus.UNKNOWN

    def _persist_result(
        self,
        request: BrainRoutingRequest,
        policy: BrainRoutingPolicyRecord | None,
        result: BrainRoutingResult,
    ) -> BrainRoutingResult:
        if self.repository is None:
            return result
        event_id = f"brain-routing:{uuid4()}"
        event = BrainRoutingEventRecord(
            routing_event_id=event_id,
            run_id=request.run_id,
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            project_id=request.project_id,
            task_type=request.task_type,
            policy_id=policy.policy_id if policy else request.policy_id,
            policy_version=policy.version if policy else request.policy_version,
            selected_profile_id=result.selected_profile_id,
            selected_provider=result.selected_provider,
            selected_model=result.selected_model,
            blocked=result.blocked,
            blocked_reason=result.blocked_reason,
            candidates=[item.model_dump(by_alias=True, mode="json") for item in result.candidates],
            occurred_at=result.selected_at,
            metadata={"routingPolicyVersion": result.routing_policy_version},
        )
        self.repository.save(event)
        return result.model_copy(update={"routing_event_id": event_id})


def _estimated_cost(profile: object, token_estimate: int) -> float:
    rate = float(profile.metadata.get("estimatedCostPer1kTokensUsd") or 0)
    return round((token_estimate / 1000) * rate, 6)


def _score_components(profile: object, request: BrainRoutingRequest, estimated_cost: float) -> dict[str, float]:
    sample_count = int(profile.metadata.get("historicalSampleCount") or 0)
    historical = float(profile.metadata.get("historicalSuccessRate") or 0.5) if sample_count >= 20 else 0.5
    quality = {"economy": 0.45, "standard": 0.7, "high": 0.9}.get(profile.quality_tier, 0.6)
    latency = {"low": 0.9, "standard": 0.7, "high": 0.4}.get(profile.latency_tier, 0.6)
    availability = 0.7 if str(profile.metadata.get("healthStatus")) == "degraded" else 1.0
    budget = request.max_cost_usd
    cost_efficiency = 1.0 if budget == 0 and estimated_cost == 0 else max(0.0, 1.0 - estimated_cost / max(budget, 0.000001))
    return {
        "userPreference": 1.0 if request.preferred_profile_id == profile.brain_profile_id else 0.5,
        "taskFit": 1.0 if request.task_type in profile.preferred_tasks else 0.6,
        "historicalSuccess": max(0.0, min(1.0, historical)),
        "quality": quality,
        "costEfficiency": cost_efficiency,
        "latency": latency,
        "availability": availability,
    }
