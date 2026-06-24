from __future__ import annotations

from agents.deal_optimization_agent import DealOptimizationAgent
from schemas.credit_policy import get_credit_policy_profile


def _policy():
    return get_credit_policy_profile("standard")


def test_healthy_deal_is_approvable():
    pd = {"feasibility_input": {"total_revenue": 200_000_000_000, "total_cost": 160_000_000_000, "construction_cost": 100_000_000_000},
          "pf_structure": [{"amount": 80_000_000_000}]}
    art = {"pfStructure": {"ltv": 0.5, "exit_sale_rate": 0.4}, "creditEnhancement": {"adjustedSeniorLtv": 0.5}, "creditReviewDecision": {"decision": "approve"}}
    res = DealOptimizationAgent().optimize(pd, art, policy=_policy())
    assert res["verdict"] == "approvable"
    assert res["levers"] == []


def test_thin_margin_yields_negotiable_levers():
    # margin ~3.6% (<10%) but closeable by cost cut/price up within bounds
    pd = {"feasibility_input": {"total_revenue": 165_600_000_000, "total_cost": 159_600_000_000, "construction_cost": 100_000_000_000},
          "pf_structure": [{"amount": 80_000_000_000}]}
    art = {"pfStructure": {"ltv": 0.5, "exit_sale_rate": 0.4}, "creditEnhancement": {"adjustedSeniorLtv": 0.5}, "creditReviewDecision": {"decision": "conditional_approval"}}
    res = DealOptimizationAgent().optimize(pd, art, policy=_policy())
    types = {l["type"] for l in res["levers"]}
    assert "construction_cost_down" in types and "sale_price_up" in types
    # at least one borrower action proposed
    assert res["borrowerActions"]
    assert res["verdict"] in {"negotiable_conditional", "not_recommended"}


def test_reject_decision_is_not_recommended():
    pd = {"feasibility_input": {"total_revenue": 100_000_000_000, "total_cost": 130_000_000_000, "construction_cost": 90_000_000_000},
          "pf_structure": [{"amount": 90_000_000_000}]}
    art = {"pfStructure": {"ltv": 0.9, "exit_sale_rate": 0.95}, "creditEnhancement": {"adjustedSeniorLtv": 0.9}, "creditReviewDecision": {"decision": "reject_recommended"}}
    res = DealOptimizationAgent().optimize(pd, art, policy=_policy())
    assert res["verdict"] == "not_recommended"
    assert "곤란" in res["verdictLabel"]


def test_high_ltv_proposes_equity_or_pf_reduction():
    pd = {"feasibility_input": {"total_revenue": 200_000_000_000, "total_cost": 150_000_000_000, "construction_cost": 100_000_000_000},
          "pf_structure": [{"amount": 90_000_000_000}]}
    art = {"pfStructure": {"ltv": 0.8, "exit_sale_rate": 0.4}, "creditEnhancement": {"adjustedSeniorLtv": 0.8}, "creditReviewDecision": {"decision": "conditional_approval"}}
    res = DealOptimizationAgent().optimize(pd, art, policy=_policy())  # standard cap 0.65 default
    types = {l["type"] for l in res["levers"]}
    assert "equity_increase_or_pf_reduction" in types
    assert any("자기자본" in a for a in res["borrowerActions"])
