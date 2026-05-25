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
