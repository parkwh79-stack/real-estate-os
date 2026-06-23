from agents.credit_review_agent import CreditReviewAgent


def test_credit_review_agent_requires_rework_for_blocking_quality_and_legal():
    result = CreditReviewAgent().decide(
        {"project": {"name": "Credit Review Test"}},
        {
            "riskRegister": {
                "status": "blocking",
                "summary": {
                    "riskCount": 3,
                    "criticalCount": 1,
                    "highCount": 1,
                    "humanReviewRequiredCount": 2,
                },
                "topRisks": [
                    {
                        "riskId": "R-DQ-001",
                        "category": "Data Quality Risk",
                        "code": "SALES_REVENUE_CONSISTENCY",
                        "title": "Sales revenue conflict",
                        "severity": "critical",
                        "score": 15,
                        "mitigant": "Select authoritative source.",
                        "sourceAgent": "DataQualityAgent",
                    }
                ],
                "risks": [
                    {
                        "riskId": "R-DQ-001",
                        "code": "SALES_REVENUE_CONSISTENCY",
                        "title": "Sales revenue conflict",
                        "severity": "critical",
                        "status": "open",
                        "mitigant": "Select authoritative source.",
                    }
                ],
                "approvalConditions": ["Resolve sales revenue conflict."],
                "creditReviewInputs": {"recommendedDecisionBand": "보완 후 재심"},
            },
            "legalPermitReview": {"status": "blocking_issue", "requiredFollowUps": ["Attach trust evidence."]},
            "dataQualityReview": {"status": "blocking", "dataLimitations": ["Revenue conflict."]},
            "reconciliationGate": {"status": "clear"},
            "marketabilityAnalysis": {"grade": "B", "score": 72, "dataStatus": "complete"},
            "pfStructure": {"exit_sale_rate": 0.6, "ltv": 0.55, "ltc": 0.7},
        },
    )

    assert result["analysisType"] == "credit_review_decision"
    assert result["decision"] == "rework_required"
    assert result["decisionLabel"] == "보완 후 재심"
    assert result["humanReview"]["required"] is True
    assert result["approvalConditions"]
    assert result["requiredWaivers"]


def test_credit_review_agent_allows_clean_approval():
    result = CreditReviewAgent().decide(
        {"project": {"name": "Clean Credit Review"}},
        {
            "riskRegister": {
                "status": "clear",
                "summary": {
                    "riskCount": 0,
                    "criticalCount": 0,
                    "highCount": 0,
                    "humanReviewRequiredCount": 0,
                },
                "topRisks": [],
                "risks": [],
                "approvalConditions": [],
                "creditReviewInputs": {},
            },
            "legalPermitReview": {"status": "clear", "requiredFollowUps": []},
            "dataQualityReview": {"status": "clear", "dataLimitations": []},
            "reconciliationGate": {"status": "clear"},
            "marketabilityAnalysis": {"grade": "A", "score": 88, "dataStatus": "complete"},
            "pfStructure": {"exit_sale_rate": 0.55, "ltv": 0.5, "ltc": 0.65},
        },
    )

    assert result["decision"] == "approve"
    assert result["decisionLabel"] == "승인 가능"
    assert result["humanReview"]["required"] is False


def _clean_artifacts():
    return {
        "riskRegister": {"status": "clear", "summary": {"riskCount": 0, "criticalCount": 0, "highCount": 0, "humanReviewRequiredCount": 0},
                          "topRisks": [], "risks": [], "approvalConditions": [], "creditReviewInputs": {}},
        "legalPermitReview": {"status": "clear", "requiredFollowUps": []},
        "dataQualityReview": {"status": "clear", "dataLimitations": []},
        "reconciliationGate": {"status": "clear"},
        "marketabilityAnalysis": {"grade": "A", "score": 88, "dataStatus": "complete"},
        "pfStructure": {"exit_sale_rate": 0.55, "ltv": 0.5, "ltc": 0.65},
    }


def _stress(scenarios):
    return {"scenarioCount": len(scenarios), "scenarios": scenarios, "worstCaseKey": scenarios[-1]["key"],
            "seniorBreakScenarioKey": next((s["key"] for s in scenarios if s["seniorStatus"] == "senior_principal_loss_possible"), None)}


def test_credit_review_tail_stress_forces_conditional_and_adds_condition():
    artifacts = _clean_artifacts()
    artifacts["stressTest"] = _stress([
        {"key": "base", "projectIrr": 0.20, "seniorStatus": "normal"},
        {"key": "tail", "projectIrr": -0.4, "seniorStatus": "senior_principal_loss_possible"},
    ])
    result = CreditReviewAgent().decide({"project": {"name": "Tail stress"}}, artifacts)
    assert result["decision"] == "conditional_approval"
    assert "tail" in result["summary"]["stressSeniorBreakScenarios"]
    assert any("Senior principal loss" in r for r in result["decisionReasons"])
    assert any("스트레스 시 Senior 원금 손실" in c for c in result["approvalConditions"])


def test_credit_review_base_senior_break_is_reject():
    artifacts = _clean_artifacts()
    artifacts["stressTest"] = _stress([
        {"key": "base", "projectIrr": 0.05, "seniorStatus": "senior_principal_loss_possible"},
    ])
    result = CreditReviewAgent().decide({"project": {"name": "Base break"}}, artifacts)
    assert result["decision"] == "reject_recommended"


def test_credit_review_negative_base_irr_requires_rework_and_reports_icr():
    artifacts = _clean_artifacts()
    artifacts["stressTest"] = _stress([{"key": "base", "projectIrr": -0.05, "seniorStatus": "normal"}])
    artifacts["returnMetrics"] = {"hurdleRate": 0.12, "metrics": {"icr": {"value": 1.4}}}
    result = CreditReviewAgent().decide({"project": {"name": "Neg IRR"}}, artifacts)
    assert result["decision"] == "rework_required"
    assert result["summary"]["icr"] == 1.4
    assert any("ICR" in r for r in result["decisionReasons"])


def test_credit_review_base_irr_below_hurdle_is_conditional():
    artifacts = _clean_artifacts()
    artifacts["stressTest"] = _stress([{"key": "base", "projectIrr": 0.08, "seniorStatus": "normal"}])
    artifacts["returnMetrics"] = {"hurdleRate": 0.12, "metrics": {}}
    result = CreditReviewAgent().decide({"project": {"name": "Below hurdle"}}, artifacts)
    assert result["decision"] == "conditional_approval"
    assert result["summary"]["projectIrrBase"] == 0.08
