from __future__ import annotations

from agents.deal_optimization_agent import DealOptimizationAgent
from schemas.credit_policy import get_credit_policy_profile


def _policy():
    return get_credit_policy_profile("standard")


def _legal_clear():
    return {
        "buildableEnvelope": {
            "eligibility": "eligible",
            "reportGrade": True,
            "regulatoryConstraintSnapshotId": "reg-snapshot-1",
            "siteParcelSnapshot": {"parcelSetHash": "a" * 64},
            "regulatoryApproval": {
                "approvalStatus": "approved",
                "approvalDecisionId": "reg-approval-1",
                "sourceHash": "b" * 64,
            },
            "snapshotBindingChecks": [
                {"name": "snapshot_id", "passed": True},
                {"name": "approval_repository_verification", "passed": True},
            ],
        }
    }


def _verified_legal_approval_decision():
    return {
        "approvalStatus": "approved",
        "approvalDecisionId": "reg-approval-1",
        "snapshotId": "reg-snapshot-1",
        "parcelSetHash": "a" * 64,
        "sourceHash": "b" * 64,
    }


def test_healthy_deal_is_approvable():
    pd = {"feasibility_input": {"total_revenue": 200_000_000_000, "total_cost": 160_000_000_000, "construction_cost": 100_000_000_000},
          "pf_structure": [{"amount": 80_000_000_000}]}
    art = {**_legal_clear(), "pfStructure": {"ltv": 0.5, "exit_sale_rate": 0.4}, "creditEnhancement": {"adjustedSeniorLtv": 0.5}, "creditReviewDecision": {"decision": "approve"}}
    res = DealOptimizationAgent().optimize(
        pd,
        art,
        policy=_policy(),
        verified_legal_approval_decision=_verified_legal_approval_decision(),
    )
    assert res["verdict"] == "approvable"
    assert res["levers"] == []
    assert res["legalEligibilityGate"]["approvalRepositoryDecisionVerified"] is True


def test_self_attested_envelope_cannot_clear_legal_gate():
    project = {
        "feasibility_input": {
            "total_revenue": 200_000_000_000,
            "total_cost": 160_000_000_000,
            "construction_cost": 100_000_000_000,
        },
        "pf_structure": [{"amount": 80_000_000_000}],
    }
    artifacts = {
        **_legal_clear(),
        "pfStructure": {"ltv": 0.5, "exit_sale_rate": 0.4},
        "creditEnhancement": {"adjustedSeniorLtv": 0.5},
        "creditReviewDecision": {"decision": "approve"},
    }

    result = DealOptimizationAgent().optimize(project, artifacts, policy=_policy())

    assert result["verdict"] == "rework_required"
    assert result["legalEligibilityGate"]["status"] == "pending"
    assert result["legalEligibilityGate"]["approvalRepositoryDecisionVerified"] is False


def test_thin_margin_yields_negotiable_levers():
    # margin ~3.6% (<10%) but closeable by cost cut/price up within bounds
    pd = {"feasibility_input": {"total_revenue": 165_600_000_000, "total_cost": 159_600_000_000, "construction_cost": 100_000_000_000},
          "pf_structure": [{"amount": 80_000_000_000}]}
    art = {**_legal_clear(), "pfStructure": {"ltv": 0.5, "exit_sale_rate": 0.4}, "creditEnhancement": {"adjustedSeniorLtv": 0.5}, "creditReviewDecision": {"decision": "conditional_approval"}}
    res = DealOptimizationAgent().optimize(
        pd,
        art,
        policy=_policy(),
        verified_legal_approval_decision=_verified_legal_approval_decision(),
    )
    types = {l["type"] for l in res["levers"]}
    assert "construction_cost_down" in types and "sale_price_up" in types
    # at least one borrower action proposed
    assert res["borrowerActions"]
    assert res["verdict"] in {"negotiable_conditional", "not_recommended"}


def test_reject_decision_is_not_recommended():
    pd = {"feasibility_input": {"total_revenue": 100_000_000_000, "total_cost": 130_000_000_000, "construction_cost": 90_000_000_000},
          "pf_structure": [{"amount": 90_000_000_000}]}
    art = {**_legal_clear(), "pfStructure": {"ltv": 0.9, "exit_sale_rate": 0.95}, "creditEnhancement": {"adjustedSeniorLtv": 0.9}, "creditReviewDecision": {"decision": "reject_recommended"}}
    res = DealOptimizationAgent().optimize(
        pd,
        art,
        policy=_policy(),
        verified_legal_approval_decision=_verified_legal_approval_decision(),
    )
    assert res["verdict"] == "not_recommended"
    assert "곤란" in res["verdictLabel"]


def test_legal_blocking_issue_can_never_be_optimized_as_approvable():
    project = {
        "feasibility_input": {
            "total_revenue": 200_000_000_000,
            "total_cost": 160_000_000_000,
            "construction_cost": 100_000_000_000,
        },
        "pf_structure": [{"amount": 80_000_000_000}],
    }
    artifacts = {
        "legalPermitReview": {"status": "blocking_issue"},
        "buildableEnvelope": {"eligibility": "ineligible", "reportGrade": False},
        "pfStructure": {"ltv": 0.5, "exit_sale_rate": 0.4},
        "creditReviewDecision": {"decision": "approve"},
    }

    result = DealOptimizationAgent().optimize(project, artifacts, policy=_policy())

    assert result["verdict"] == "not_recommended"
    assert result["legalEligibilityGate"]["status"] == "blocking"


def test_credit_rework_required_is_not_approvable_without_financial_levers():
    project = {
        "feasibility_input": {
            "total_revenue": 200_000_000_000,
            "total_cost": 160_000_000_000,
            "construction_cost": 100_000_000_000,
        },
        "pf_structure": [{"amount": 80_000_000_000}],
    }
    artifacts = {
        **_legal_clear(),
        "pfStructure": {"ltv": 0.5, "exit_sale_rate": 0.4},
        "creditReviewDecision": {"decision": "rework_required"},
    }

    result = DealOptimizationAgent().optimize(
        project,
        artifacts,
        policy=_policy(),
        verified_legal_approval_decision=_verified_legal_approval_decision(),
    )

    assert result["verdict"] == "rework_required"


def test_high_ltv_proposes_equity_or_pf_reduction():
    pd = {"feasibility_input": {"total_revenue": 200_000_000_000, "total_cost": 150_000_000_000, "construction_cost": 100_000_000_000},
          "pf_structure": [{"amount": 90_000_000_000}]}
    art = {**_legal_clear(), "pfStructure": {"ltv": 0.8, "exit_sale_rate": 0.4}, "creditEnhancement": {"adjustedSeniorLtv": 0.8}, "creditReviewDecision": {"decision": "conditional_approval"}}
    res = DealOptimizationAgent().optimize(
        pd,
        art,
        policy=_policy(),
        verified_legal_approval_decision=_verified_legal_approval_decision(),
    )  # standard cap 0.65 default
    types = {l["type"] for l in res["levers"]}
    assert "equity_increase_or_pf_reduction" in types
    assert any("자기자본" in a for a in res["borrowerActions"])
