from agents.pod_ai_reviewer_agent import _pf_credit_findings, PodAIReviewerAgent


def _codes(findings, category=None):
    return [f for f in findings if category is None or f["category"] == category]


def test_pf_credit_findings_route_blockers_and_gaps():
    artifact = {
        "artifactType": "pf_credit_pod_result",
        "pfStructure": {"tranches": [{"tranche": "Senior", "amount": 800}]},
        "scenariosEvaluation": {
            "scenarioSetId": "set-99",
            "riskFlags": [
                {"code": "SENIOR_SHORTFALL", "severity": "critical"},
                {"code": "UNSOLD_LTV_BREACH", "severity": "critical"},
                {"code": "TRIGGER_REQUIRED", "severity": "medium"},
            ],
        },
        "creditReviewDecision": {"decision": "conditional_approval"},
    }
    project = {"covenants": {"discount_triggers": [{"rate": 0.1}]}}

    findings = _pf_credit_findings(artifact, "artifact:pf_credit:1", project)
    cats = {f["category"] for f in findings}

    # Senior shortfall -> blocking risk_note + recalculation_needed
    assert any(f["severity"] == "blocking" and f["category"] == "risk_note" for f in findings)
    assert "recalculation_needed" in cats
    # Unsold LTV breach + tranche trigger -> risk_notes
    assert sum(f["category"] == "risk_note" for f in findings) >= 3
    # PF structure present + covenants present -> NO missing_evidence for those
    assert "missing_evidence" not in cats
    # Provenance: scenario set + artifact key attached
    st = {loc.get("sourceType") for f in findings for loc in f["sourceLocators"]}
    assert "scenario_set" in st and "chief_pod_artifact" in st
    # Deterministic credit decision carried as context, not re-decided
    assert any(f.get("context", {}).get("deterministicCreditDecision") == "conditional_approval" for f in findings)


def test_pf_credit_findings_flag_missing_structure_and_scenarios():
    findings = _pf_credit_findings({"artifactType": "pf_credit_pod_result"}, None, {})
    cats = [f["category"] for f in findings]
    # No pf structure, no scenarios, no covenants -> three missing_evidence findings
    assert cats.count("missing_evidence") >= 3
    msgs = " ".join(f["message"] for f in findings)
    assert "PF 구조" in msgs and "시나리오" in msgs and "Covenant" in msgs


def test_review_pod_runs_for_pf_credit():
    review = PodAIReviewerAgent().review_pod(
        project_id="p1",
        pod_key="pf_credit",
        source_artifact={"artifactType": "pf_credit_pod_result", "scenariosEvaluation": {"riskFlags": [{"code": "SENIOR_SHORTFALL", "severity": "critical"}]}},
        source_artifact_key="artifact:pf_credit:1",
        project_data={},
        options={"podAiReviewMode": "deterministic"},
    )
    assert review["podKey"] == "pf_credit"
    assert review["agentId"] == "PFCreditPodAIReviewer"
    assert review["reviewStatus"] == "blocking"  # senior shortfall is blocking
    assert any(f["category"] == "risk_note" for f in review["findings"])
