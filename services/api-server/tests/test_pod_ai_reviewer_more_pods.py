from agents.pod_ai_reviewer_agent import (
    _cost_schedule_findings,
    _feasibility_cashflow_findings,
    _public_policy_findings,
    PodAIReviewerAgent,
)


def _cats(findings):
    return [f["category"] for f in findings]


# --- cost_schedule ---------------------------------------------------------
def test_cost_schedule_routes_engine_readiness_gaps():
    art = {
        "artifactType": "cost_schedule_pod_result",
        "costBreakdown": {},
        "schedule": {},
        "engineReadiness": {"blockingFields": ["total_cost", "schedule_or_cashflow_input"], "canRunCashFlow": False},
    }
    f = _cost_schedule_findings(art, "a:cs:1", {})
    cats = _cats(f)
    assert "missing_evidence" in cats  # cost breakdown / blocking fields
    assert "recalculation_needed" in cats  # canRunCashFlow False
    # every finding carries provenance
    assert all(any(loc.get("artifactKey") == "a:cs:1" for loc in x["sourceLocators"]) for x in f)


def test_cost_schedule_clean_readiness_still_flags_absent_contractor_ve():
    art = {
        "artifactType": "cost_schedule_pod_result",
        "costBreakdown": {"totalCost": 100},
        "schedule": {"completion": "2027-06"},
        "engineReadiness": {"blockingFields": [], "canRunCashFlow": True},
    }
    f = _cost_schedule_findings(art, None, {})
    # contractor_risk / ve_options always flagged as missing_evidence (medium)
    assert any(c == "missing_evidence" for c in _cats(f))
    assert not any(x["severity"] == "high" for x in f)  # no high blockers when ready


# --- feasibility_cashflow --------------------------------------------------
def test_feasibility_max_minus_breach_blocks():
    art = {
        "artifactType": "feasibility_cashflow_pod_result",
        "cashFlowRunResult": {"summary": {"status": "pf_limit_shortfall", "maxMinus": -5.0}, "riskFlags": [{"code": "MAX_MINUS_EXCEEDS_PF_LIMIT"}]},
    }
    f = _feasibility_cashflow_findings(art, "a:fc:1", {})
    assert any(x["severity"] == "blocking" and x["category"] == "risk_note" for x in f)
    assert "recalculation_needed" in _cats(f)
    assert any(x.get("context", {}).get("deterministicCashFlowStatus") == "pf_limit_shortfall" for x in f)


def test_feasibility_negative_irr_and_missing_fields():
    art = {
        "cashFlowRunResult": {"summary": {"status": "funding_gap_covered"}},
        "returnMetrics": {"metrics": {"projectIrr": {"status": "loss", "value": -0.03}}},
        "dataQuality": {"missingFields": ["feasibility_input.total_revenue"]},
    }
    f = _feasibility_cashflow_findings(art, "a:fc:2", {})
    assert any(x["category"] == "risk_note" and x["severity"] == "high" for x in f)  # negative IRR
    assert any(x["category"] == "missing_evidence" for x in f)  # missing fields


def test_feasibility_engine_not_run():
    art = {"cashFlowRunError": {"reason": "cash_flow_engine_input_not_ready"}}
    f = _feasibility_cashflow_findings(art, None, {})
    assert any(x["category"] == "missing_evidence" and x["severity"] == "high" for x in f)


# --- public_policy ---------------------------------------------------------
def test_public_policy_no_data_blocks():
    art = {"artifactType": "public_policy_pod_result", "developabilityScore": {"grade": "no_data", "gaps": [], "dataLimitations": ["regulation", "feasibility"]}}
    f = _public_policy_findings(art, "a:pp:1", {})
    assert any(x["severity"] == "blocking" and x["category"] == "missing_evidence" for x in f)


def test_public_policy_partial_dimensions_and_stub():
    art = {
        "artifactType": "public_policy_pod_result",
        "developabilityScore": {"grade": "C", "gaps": ["시장성"], "dataLimitations": ["시장성"], "dimensions": {"regulation": {"available": False}}},
        "policyNotes": ["Public-policy pod currently reuses developability until dedicated policy simulation agents are implemented."],
    }
    f = _public_policy_findings(art, "a:pp:2", {})
    cats = _cats(f)
    assert cats.count("missing_evidence") >= 2  # dataLimitations + regulation + stub
    assert "risk_note" in cats  # gaps
    assert all(any(loc.get("artifactKey") == "a:pp:2" for loc in x["sourceLocators"]) for x in f)


def test_review_pod_runs_for_all_new_pods():
    for pod in ("cost_schedule", "feasibility_cashflow", "public_policy"):
        r = PodAIReviewerAgent().review_pod(
            project_id="p1", pod_key=pod,
            source_artifact={"artifactType": f"{pod}_pod_result"},
            source_artifact_key=f"a:{pod}:1", project_data={},
            options={"podAiReviewMode": "deterministic"},
        )
        assert r["podKey"] == pod
        assert r["agentId"] != "PodAIReviewerAgent"  # dedicated reviewer id
        assert isinstance(r["findings"], list) and r["findings"]
