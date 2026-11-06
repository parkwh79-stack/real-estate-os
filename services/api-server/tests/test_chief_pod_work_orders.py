"""MOCK-DATA: routing and lifecycle, no model or external API calls."""
from copy import deepcopy

import pytest

from schemas.orchestration import MissingInputRegisterItem
from use_cases.chief_pod_work_orders import missing_input_work_plan, route_review_work_orders
from use_cases import chief_deal_orchestration as chief


def _missing():
    return [MissingInputRegisterItem(itemId="market_evidence_missing", category="market",
        description="시장 근거 필요", requiredFor=["market_product", "pf_credit"], severity="high",
        acceptableSources=["국토부 실거래가", "업로드 분양사례"])]


def _review(target="MarketEvidenceAgent", action="collect_public_evidence", **payload):
    return {"projectId": "p", "reviewId": "review-1", "podKey": "pf_credit", "agentId": "PFCreditPodAIReviewer",
            "reviewMode": "llm_evidence_aware_review", "sourceArtifactKey": "artifact-1", "inputEvidenceIds": ["e1"],
            "handoffs": [{"toAgent": target, "handoffType": "work_order",
                          "payload": {"action": action, "missingItemId": "market_evidence_missing", **payload}}]}


def _route(review, state=None):
    state = state if state is not None else {}
    saved = []
    dispatched = route_review_work_orders(review=review,
        artifact={"artifactKey": "artifact-1", "projectId": "p", "outputHash": "h", "inputHash": "i"},
        missing=_missing(), state=state, now="2026-10-15T00:00:00Z", handoff_sink=saved.append)
    return state, saved, dispatched


def test_valid_llm_collection_is_typed_auditable_and_deduplicated():
    state, saved, dispatched = _route(_review())
    assert dispatched[0]["podKey"] == "market_product"
    assert saved[0]["status"] == "accepted"
    assert saved[0]["payload"]["sourceReviewId"] == "review-1"
    assert saved[0]["evidenceIds"] == ["e1"]
    _, duplicate, dispatched = _route(_review(), state)
    assert duplicate == dispatched == []
    assert len(state["workOrders"]) == 1


@pytest.mark.parametrize("review", [
    _review(target="ShellAgent"),
    _review(action="execute_code"),
    _review(totalRevenue=999),
    _review(url="https://untrusted.invalid"),
    _review(target="DevelopmentFeasibilityAgent", action="rerun_pod"),
    _review(podKey="pf_credit"),
    {**_review(), "sourceArtifactKey": "other-artifact"},
    {**_review(), "projectId": "another-project"},
])
def test_unrecognized_mutating_ambiguous_or_unbound_proposals_never_execute(review):
    state, saved, dispatched = _route(review)
    assert dispatched == []
    assert state["workOrders"][0]["status"] == "needs_review"
    assert saved[0]["requiresHumanReview"] is True


def test_shared_agent_requires_valid_destination_and_material_requests_do_not_block_analysis():
    state, _, dispatched = _route(_review(target="DevelopmentFeasibilityAgent", action="rerun_pod", podKey="cost_schedule"))
    assert dispatched[0]["podKey"] == "cost_schedule"
    state, _, dispatched = _route(_review(target="pf_credit", action="request_material"))
    assert dispatched == []
    assert state["workOrders"][0]["status"] == "needs_material"


def test_missing_work_names_each_pod_and_real_sources():
    rows = missing_input_work_plan(_missing())
    assert {row["podKey"] for row in rows} == {"market_product", "pf_credit"}
    assert all(row["collectionPod"] == "market_product" for row in rows)
    assert all("국토부 실거래가" in row["acceptableSources"] for row in rows)


def test_no_model_fallback_routes_only_supported_missing_public_inputs():
    review = {**_review(), "reviewMode": "deterministic_fallback_review", "handoffs": []}
    _, saved, dispatched = _route(review)
    assert len(dispatched) == 1
    assert dispatched[0]["podKey"] == "market_product"
    assert dispatched[0]["reviewMode"] == "deterministic_fallback_review"
    assert saved[0]["payload"]["decisionBasis"] == "missing_input_register"


@pytest.mark.parametrize("fail_target", [False, True])
def test_llm_work_order_enters_existing_cascade_executes_and_survives_replanning(monkeypatch, fail_target):
    project = {"project": {"name": "합성 업무 배정"},
        "source_documents": [{"documentType": "business_plan"}],
        "land": {"pnu": "1111010100100010000", "officialLandPricePerSqm": 100},
        "chief_orchestration_state": {"podRuns": {"land_legal": {"status": "completed"}}}}
    executed = []
    def executor(**kwargs):
        executed.append(kwargs["pod_key"])
        return {"artifactType": "test", "value": kwargs["pod_key"]}
    # Runtime envelopes are tested separately; exercise the real routing, event
    # lifecycle, cascade and plan here with deterministic child results.
    def runtime(**kwargs):
        if fail_target and kwargs["pod_key"] == "market_product":
            executed.append("market_product")
            kwargs["project_data"]["market"] = {"unpublishedMutation": True}
            raise RuntimeError("controlled child failure")
        return executor(pod_key=kwargs["pod_key"]), {"status": "completed"}
    monkeypatch.setattr(chief, "run_domain_pod_with_contract", runtime)
    def reviewer(**kwargs):
        review = _review(target="market_product", action="rerun_pod")
        review.update(projectId=kwargs["project_id"], sourceArtifactKey=kwargs["source_artifact_key"], podKey=kwargs["pod_key"])
        if kwargs["pod_key"] == "market_product":
            review["handoffs"] = []
        return review
    monkeypatch.setattr(chief, "run_pod_ai_review_with_contract", reviewer)
    monkeypatch.setattr(chief, "_load_pod_ai_review_findings", lambda _: {})
    artifacts = []
    plan = chief.run_chief_deal_pod_with_cascade_use_case("p", "cost_schedule", project,
        artifact_sink=lambda item: artifacts.append(deepcopy(item)) or item, max_rounds=1)
    assert executed == ["cost_schedule", "market_product"]
    order = plan["taskGraph"]["workOrders"][0]
    assert order["status"] == ("failed" if fail_target else "executed")
    if fail_target:
        assert plan["autoCascadeSummary"]["failedCount"] == 1
        assert "market" not in project
        assert "resultArtifactKey" not in order
    else:
        assert order["resultArtifactKey"].startswith("artifact:market_product:")
    assert artifacts[0]["inputSnapshot"]
    reopened = chief.plan_chief_deal_orchestration_use_case("p", deepcopy(project))
    assert reopened["taskGraph"]["workOrders"] == plan["taskGraph"]["workOrders"]


def test_actual_reviewer_prompts_for_supported_agents_and_preserves_deterministic_findings():
    from agents.ai_connection import AIExtractionResult
    from agents.pod_ai_reviewer_agent import PodAIReviewerAgent
    class FakeAI:
        def extract_json(self, prompt, **kwargs):
            assert "MarketEvidenceAgent" in prompt and "publicInputOwners" in prompt
            return AIExtractionResult(data={"confidence": 0.8, "findings": [], "handoffs": _review()["handoffs"]},
                                      meta={"provider": "test"})
    review = PodAIReviewerAgent(ai=FakeAI()).review_pod(project_id="p", pod_key="pf_credit",
        source_artifact={"result": {}}, source_artifact_key="artifact-1", project_data={})
    assert review["reviewMode"] == "llm_evidence_aware_review"
    assert review["deterministicFindings"]
    _, _, dispatched = _route(review)
    assert dispatched[0]["podKey"] == "market_product"
