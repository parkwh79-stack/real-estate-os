from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agent_platform.bootstrap import (
    POD_AI_REVIEW_AGENT_ID,
    build_default_agent_registry,
    run_pod_ai_review_with_contract,
)
from agent_platform.brains import AIConnectionBrainAdapter, CapabilityBrainRouter, MockBrain
from agent_platform.contracts import (
    ActorContext,
    AgentExecutionContext,
    AgentManifest,
    AgentRequest,
    BrainCapabilities,
    BrainLimits,
    BrainPrivacyPolicy,
    BrainProfile,
    BrainRequest,
    BrainRoutingRequest,
    CapabilityRequirement,
    DataClassification,
    ExecutionConstraints,
    ExecutionMode,
    ExecutionPolicy,
    FailurePolicy,
    ImplementationLevel,
    ProjectContext,
    RunBudget,
    SourceLocator,
)
from agent_platform.registry import AgentRegistryError, BrainRegistry
from agents.ai_connection import AIExtractionResult
from schemas.orchestration import PodAIReview


def _brain_profile(
    profile_id: str,
    *,
    provider: str,
    local: bool,
    classifications: list[DataClassification],
    quality: str = "standard",
) -> BrainProfile:
    return BrainProfile(
        brain_profile_id=profile_id,
        provider=provider,
        model=f"{provider}-model",
        capabilities=BrainCapabilities(
            structured_output=True,
            long_context=True,
            korean_reasoning_level="high",
        ),
        limits=BrainLimits(
            max_context_tokens=100_000,
            max_output_tokens=8_000,
            max_cost_per_run_usd=2,
        ),
        privacy=BrainPrivacyPolicy(
            allowed_data_classifications=classifications,
            local_execution=local,
        ),
        quality_tier=quality,
        cost_tier="standard",
        latency_tier="low" if local else "standard",
        preferred_tasks=["pod_ai_review"],
        metadata={
            "estimatedCostPer1kTokensUsd": 0 if local else 0.001,
            "healthStatus": "healthy",
            "historicalSuccessRate": 0.99,
            "historicalSampleCount": 2,  # ignored until the minimum sample size is met
        },
    )


def test_source_locator_is_typed_and_detects_explicit_location():
    locator = SourceLocator.from_mapping(
        {
            "sourceType": "excel_cell",
            "sourceName": "feasibility.xlsx",
            "sheet": "사업수지",
            "cell": "F22",
        }
    )
    assert locator.sheet_name == "사업수지"
    assert locator.cell_reference == "F22"
    assert locator.is_explicit is True
    assert SourceLocator(source_name="unlocated note").is_explicit is False


def test_llm_only_cannot_be_authoritative_deterministic_engine():
    with pytest.raises(ValidationError, match="LLM_ONLY"):
        ExecutionPolicy(
            mode=ExecutionMode.LLM_ONLY,
            deterministic_authority_required=True,
        )


def test_execution_context_never_serializes_runtime_secret():
    now = datetime.now(timezone.utc)
    actor = ActorContext(user_id="u1", tenant_id="t1")
    constraints = ExecutionConstraints(data_classification=DataClassification.CONFIDENTIAL)
    context = AgentExecutionContext(
        run_id="r1",
        correlation_id="c1",
        trace_id="t1",
        actor=actor,
        project=ProjectContext(project_id="p1"),
        constraints=constraints,
        budget=RunBudget(max_cost_usd=1, max_llm_calls=1, max_tool_calls=1),
        started_at=now,
        deadline_at=now + timedelta(minutes=1),
    )
    context.set_runtime_value("ai_context", {"api_key": "sk-must-not-persist"})
    dumped = json.dumps(context.model_dump(by_alias=True, mode="json"), ensure_ascii=False)
    assert "sk-must-not-persist" not in dumped
    assert context.get_runtime_value("ai_context")["api_key"] == "sk-must-not-persist"


def test_brain_profile_rejects_nested_inline_secret():
    with pytest.raises(ValidationError, match="secretRef"):
        BrainProfile(
            brain_profile_id="bad-profile",
            provider="openai",
            model="model",
            capabilities=BrainCapabilities(structured_output=True),
            limits=BrainLimits(max_context_tokens=1000, max_output_tokens=100, max_cost_per_run_usd=1),
            privacy=BrainPrivacyPolicy(allowed_data_classifications=[DataClassification.PUBLIC]),
            metadata={"providerConfig": {"api_key": "must-not-be-here"}},
        )


def test_agent_registry_rejects_duplicate_manifest_version():
    registry = build_default_agent_registry()
    duplicate = build_default_agent_registry().get(POD_AI_REVIEW_AGENT_ID, "1.0.0")
    with pytest.raises(AgentRegistryError, match="already registered"):
        registry.register(duplicate)


def test_brain_router_applies_privacy_and_local_only_hard_filters():
    registry = BrainRegistry()
    cloud = MockBrain(
        _brain_profile(
            "cloud-reviewer",
            provider="gemini",
            local=False,
            classifications=[DataClassification.PUBLIC, DataClassification.INTERNAL],
            quality="high",
        )
    )
    local = MockBrain(
        _brain_profile(
            "local-reviewer",
            provider="local",
            local=True,
            classifications=list(DataClassification),
        )
    )
    registry.register(cloud)
    registry.register(local)
    result = CapabilityBrainRouter(registry).route(
        BrainRoutingRequest(
            task_type="pod_ai_review",
            risk_level="high",
            data_classification="restricted",
            required_capabilities=[CapabilityRequirement(name="structured_output")],
            context_token_estimate=10_000,
            estimated_output_tokens=1_000,
            tenant_id="t1",
            user_id="u1",
            max_cost_usd=0,
            local_only=True,
        )
    )
    assert result.selected_profile_id == "local-reviewer"
    cloud_candidate = next(item for item in result.candidates if item.brain_profile_id == "cloud-reviewer")
    assert "DATA_CLASSIFICATION_NOT_ALLOWED" in cloud_candidate.rejection_reasons
    assert "LOCAL_EXECUTION_REQUIRED" in cloud_candidate.rejection_reasons


class _FakeAIConnection:
    def extract_json(self, prompt, *, system, ai_context):
        return AIExtractionResult(
            data={"review": "ok"},
            meta={
                **ai_context,
                "token_usage": {"promptTokens": 12, "completionTokens": 4},
                "ai_brain_used_event_id": "evt-1",
            },
        )


def test_existing_ai_connection_is_exposed_through_brain_interface():
    profile = _brain_profile(
        "gemini-json",
        provider="gemini",
        local=False,
        classifications=[DataClassification.CONFIDENTIAL],
    )
    adapter = AIConnectionBrainAdapter(
        profile=profile,
        ai_context={"provider": "gemini", "model": "gemini-test", "direct_model_call": True},
        connection=_FakeAIConnection(),
    )
    response = asyncio.run(
        adapter.generate(
            BrainRequest(
                system_instructions="Return JSON.",
                messages=[{"role": "user", "content": "Review evidence."}],
                max_output_tokens=100,
            )
        )
    )
    assert response.structured_output == {"review": "ok"}
    assert response.input_tokens == 12
    assert response.raw_metadata["aiBrainUsedEventId"] == "evt-1"


def test_chief_pod_reviewer_runs_through_platform_contract_without_secret_leak():
    registry = build_default_agent_registry()
    output = run_pod_ai_review_with_contract(
        project_id="p-agent-platform",
        pod_key="cost_schedule",
        source_artifact={
            "artifactType": "cost_schedule_pod_result",
            "sourceLocator": {"sourceType": "chief_pod_artifact", "artifactKey": "artifact:cost:1"},
            "evidenceId": "evidence:cost:1",
            "costBreakdown": {"totalCost": 100},
            "schedule": {"completion": "2028-06"},
            "engineReadiness": {"blockingFields": [], "canRunCashFlow": True},
        },
        source_artifact_key="artifact:cost:1",
        project_data={"projectType": "mixed_use"},
        options={
            "podAiReviewMode": "deterministic",
            "ai_context": {
                "provider": "openai",
                "model": "test-model",
                "api_key": "sk-ephemeral-must-not-appear",
                "user_id": "u1",
                "tenant_id": "t1",
            },
        },
        registry=registry,
    )
    assert output["agentPlatform"]["agentId"] == POD_AI_REVIEW_AGENT_ID
    assert output["agentPlatform"]["implementationLevel"] == ImplementationLevel.CONTROLLED.value
    assert output["agentPlatform"]["qualityMetrics"]["schemaValid"] is True
    assert "sk-ephemeral-must-not-appear" not in json.dumps(output, ensure_ascii=False)
    assert PodAIReview.model_validate(output).agent_platform["agentId"] == POD_AI_REVIEW_AGENT_ID


def test_restricted_cloud_review_fails_closed_to_deterministic_mode():
    output = run_pod_ai_review_with_contract(
        project_id="p-restricted",
        pod_key="public_policy",
        source_artifact={"artifactType": "public_policy_pod_result"},
        source_artifact_key="artifact:policy:1",
        project_data={},
        options={
            "dataClassification": "restricted",
            "ai_context": {
                "provider": "gemini",
                "runtime_provider": "openai_compatible",
                "model": "gemini-test",
                "api_key": "restricted-secret",
                "direct_model_call": True,
                "supports_json_mode": True,
                "is_local": False,
                "user_id": "u1",
                "tenant_id": "t1",
            },
        },
    )
    routing = output["agentPlatform"]["brainRouting"]
    assert routing["blocked"] is True
    assert "DATA_CLASSIFICATION_NOT_ALLOWED" in routing["candidates"][0]["rejectionReasons"]
    assert output["llmReviewAccepted"] is False
    assert "restricted-secret" not in json.dumps(output, ensure_ascii=False)
