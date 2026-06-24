from __future__ import annotations

from agents.credit_review_agent import CreditReviewAgent
from schemas.credit_policy import (
    get_credit_policy_profile,
    resolve_credit_policy,
)


def _clean_artifacts(**overrides):
    """Artifacts that yield 'approve' under the standard policy."""
    artifacts = {
        "riskRegister": {"status": "clear", "summary": {}, "risks": [], "topRisks": [], "creditReviewInputs": {}},
        "legalPermitReview": {"status": "clear"},
        "dataQualityReview": {"status": "clear"},
        "reconciliationGate": {"status": "clear"},
        "marketabilityAnalysis": {"grade": "B", "score": 80, "dataStatus": "complete"},
        "pfStructure": {"ltv": 0.5, "ltc": 0.6, "exit_sale_rate": 0.50},
        "returnMetrics": {"metrics": {"projectIrr": {"value": 0.20}}},
        "stressTest": {"scenarios": [{"key": "base", "projectIrr": 0.20, "seniorStatus": "normal"}], "worstCaseKey": "base", "seniorBreakScenarioKey": None},
        "creditEnhancement": {"adjustedSeniorLtv": 0.55},
    }
    artifacts.update(overrides)
    return artifacts


def _decision(artifacts, policy_id):
    policy = get_credit_policy_profile(policy_id)
    return CreditReviewAgent().decide({}, artifacts, policy=policy)["decision"]


def test_clean_deal_approves_under_all_policies():
    art = _clean_artifacts()
    assert _decision(art, "standard") == "approve"
    assert _decision(art, "aggressive") == "approve"


def test_marketability_grade_threshold_is_policy_specific():
    # Grade C: rework only under conservative (rework grades C/D/E), not standard (D/E).
    art = _clean_artifacts(marketabilityAnalysis={"grade": "C", "score": 65, "dataStatus": "complete"})
    assert _decision(art, "standard") == "approve"
    assert _decision(art, "conservative") == "rework_required"


def test_exit_sale_rate_threshold_is_policy_specific():
    # Exit 0.88: rework under standard (>=0.85) and conservative (>=0.80), not aggressive (>=0.92).
    art = _clean_artifacts(pfStructure={"ltv": 0.5, "ltc": 0.6, "exit_sale_rate": 0.88})
    assert _decision(art, "standard") == "rework_required"
    assert _decision(art, "aggressive") == "approve"


def test_hurdle_rate_is_policy_specific():
    # Base IRR 0.13: below conservative hurdle (0.15) -> conditional; above standard (0.12) -> approve.
    art = _clean_artifacts(
        returnMetrics={"metrics": {"projectIrr": {"value": 0.13}}},
        stressTest={"scenarios": [{"key": "base", "projectIrr": 0.13, "seniorStatus": "normal"}], "seniorBreakScenarioKey": None},
    )
    assert _decision(art, "standard") == "approve"
    assert _decision(art, "conservative") == "conditional_approval"


def test_max_adjusted_senior_ltv_caps_at_conditional():
    # Adjusted senior LTV 0.65 exceeds conservative cap (0.60) -> conditional; standard has no cap.
    art = _clean_artifacts(creditEnhancement={"adjustedSeniorLtv": 0.65})
    assert _decision(art, "standard") == "approve"
    assert _decision(art, "conservative") == "conditional_approval"


def test_policy_profile_is_surfaced_in_output():
    decision = CreditReviewAgent().decide({}, _clean_artifacts(), policy=get_credit_policy_profile("aggressive"))
    assert decision["policyProfile"]["profileId"] == "aggressive"
    assert decision["policyProfile"]["hurdleRate"] == 0.10


def test_resolve_credit_policy_from_project_data():
    assert resolve_credit_policy({}).profileId == "standard"
    assert resolve_credit_policy({"credit_policy_profile": "conservative"}).profileId == "conservative"
    inline = resolve_credit_policy({"credit_policy": {"profileId": "standard", "hurdleRate": 0.2}})
    assert inline.profileId == "standard"
    assert inline.hurdleRate == 0.2


def test_decide_resolves_policy_from_project_data_when_not_passed():
    art = _clean_artifacts(returnMetrics={"metrics": {"projectIrr": {"value": 0.13}}},
                           stressTest={"scenarios": [{"key": "base", "projectIrr": 0.13, "seniorStatus": "normal"}]})
    standard = CreditReviewAgent().decide({}, art)["decision"]
    conservative = CreditReviewAgent().decide({"credit_policy_profile": "conservative"}, art)["decision"]
    assert standard == "approve"
    assert conservative == "conditional_approval"
