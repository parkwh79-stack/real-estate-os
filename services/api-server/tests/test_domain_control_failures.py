from types import SimpleNamespace

import pytest

from agent_platform import domain_agent_runtime, domain_pod_runtime
from agent_platform.contracts import AgentRunStatus
from agent_platform.domain_runtime_scope import domain_tenant_id
from db.repositories.request_project_repository import project_request_scope
from agents.ai_connection import AIExtractionResult
from agents.pod_ai_reviewer_agent import PodAIReviewerAgent, _review_ai_context


@pytest.mark.parametrize("mapper", [domain_agent_runtime._result_mapper, domain_pod_runtime._result_mapper])
@pytest.mark.parametrize("raw,expected", [
    ({"status": "failed"}, AgentRunStatus.FAILED),
    ({"status": " ERROR "}, AgentRunStatus.FAILED),
    ({"status": "completed", "executionStatus": "failed"}, AgentRunStatus.FAILED),
    ({"analysisStatus": "timed_out"}, AgentRunStatus.FAILED),
    ({"success": False}, AgentRunStatus.FAILED),
    ({}, AgentRunStatus.FAILED),
    (None, AgentRunStatus.FAILED),
    ({"status": "cancelled"}, AgentRunStatus.CANCELLED),
    ({"status": "blocked"}, AgentRunStatus.BLOCKED),
    ({"status": "completed", "executionStatus": "blocked"}, AgentRunStatus.BLOCKED),
    ({"status": "blocking", "executionStatus": "failed"}, AgentRunStatus.FAILED),
    ({"status": "needs_review"}, AgentRunStatus.COMPLETED_WITH_REVIEW),
    ({"status": "completed_with_review"}, AgentRunStatus.COMPLETED_WITH_REVIEW),
    ({"status": "completed_with_warnings"}, AgentRunStatus.COMPLETED_WITH_REVIEW),
    ({"status": "completed", "amount": 0}, AgentRunStatus.COMPLETED),
])
def test_domain_results_do_not_promote_failures_to_completion(mapper, raw, expected):
    result = mapper(
        raw, SimpleNamespace(request_id="q1", payload={}), SimpleNamespace(run_id="r1"),
        SimpleNamespace(agent_id="a1", version="1", output_schema_name="artifact", output_schema_version="1"),
    )
    assert result.status is expected
    if expected in {AgentRunStatus.FAILED, AgentRunStatus.CANCELLED, AgentRunStatus.BLOCKED}:
        assert result.quality_metrics.deterministic_checks_passed is False


@pytest.mark.parametrize("mapper", [domain_agent_runtime._result_mapper, domain_pod_runtime._result_mapper])
@pytest.mark.parametrize("raw", [
    {"status": "blocking", "risks": [{"severity": "critical"}]},
    {"status": "blocking", "checks": [{"status": "blocking"}]},
    {"status": "blocking_issue", "findings": [{"severity": "blocking"}]},
    {"reviewStatus": "blocking", "findings": [{"severity": "blocking"}]},
    {"reviewStatus": "blocked", "findings": [{"severity": "blocking"}]},
    {"status": "rejected", "findings": [{"severity": "warning"}]},
])
def test_completed_business_blocker_remains_a_reviewable_analysis(mapper, raw):
    result = mapper(
        raw, SimpleNamespace(request_id="q1", payload={}), SimpleNamespace(run_id="r1"),
        SimpleNamespace(agent_id="a1", version="1", output_schema_name="artifact", output_schema_version="1"),
    )
    assert result.status is AgentRunStatus.COMPLETED_WITH_REVIEW
    assert result.quality_metrics.deterministic_checks_passed is True


def test_domain_actor_cannot_override_active_tenant_with_stale_ai_context():
    with project_request_scope(lambda: SimpleNamespace(tenant_id="tenant-a")):
        assert domain_tenant_id({}, {}) == "tenant-a"
        with pytest.raises(ValueError, match="TENANT_SCOPE_MISMATCH"):
            domain_tenant_id({"tenantId": "tenant-a"}, {"tenant_id": "tenant-b"})


def test_llm_review_cannot_hide_deterministic_pf_blocker():
    class AdvisoryAI:
        def extract_json(self, *_args, **_kwargs):
            return AIExtractionResult(data={"findings": [{"severity": "info", "message": "Looks clear"}]}, meta={})
    review = PodAIReviewerAgent(ai=AdvisoryAI()).review_pod(
        project_id="p1", pod_key="pf_credit",
        source_artifact={"scenariosEvaluation": {"riskFlags": [{"code": "SENIOR_SHORTFALL", "severity": "critical"}]}},
    )
    assert review["llmReviewAccepted"] is True
    assert review["reviewStatus"] == "blocking"
    assert any(item["severity"] == "blocking" for item in review["findings"])


def test_pod_review_passes_local_only_and_provider_exclusions_to_actual_connection():
    context = _review_ai_context(
        {"localOnly": True, "maxCostUsd": 0, "prohibitedProviders": ["openai"],
         "ai_context": {"provider": "local", "prohibited_providers": ["anthropic"]}},
        project_id="p1", agent_id="reviewer", evidence_ids=[],
    )
    assert context["local_only"] is True
    assert context["max_cost_usd"] == 0
    assert set(context["prohibited_providers"]) == {"anthropic", "openai"}
