from agents.risk_register_agent import RiskRegisterAgent
from use_cases.orchestration_approval import review_risk_register_row_use_case, unresolved_risk_reviews


def test_risk_register_agent_consolidates_agent_risks():
    result = RiskRegisterAgent().build(
        {"project": {"name": "Risk Register Test"}},
        {
            "landAgentReview": {
                "blockingIssues": ["Registry evidence is missing for parcel 1."],
                "dataLimitations": ["Official land price coverage is below 100%."],
                "summary": {
                    "pnuCoverageRatio": 0.5,
                    "officialLandPriceCoverageRatio": 0,
                    "registryEvidenceCoverageRatio": 0,
                    "acquisitionAreaRatio": 0.8,
                },
            },
            "marketabilityAnalysis": {
                "riskFlagDetails": [
                    {
                        "code": "NO_COMPARABLE_DATA",
                        "severity": "high",
                        "category": "pricing",
                        "message": "Comparable evidence is missing.",
                    }
                ]
            },
            "legalPermitReview": {
                "checks": [
                    {
                        "key": "trust_security",
                        "title": "Trust and security package",
                        "severity": "caution",
                        "findings": ["Trust security needs confirmation."],
                    }
                ]
            },
            "developerFeasibility": {
                "cashFlowRunResult": {
                    "riskFlags": [
                        {
                            "code": "MAX_MINUS_EXCEEDS_PF_LIMIT",
                            "severity": "critical",
                            "message": "Max Minus exceeds PF limit.",
                        }
                    ]
                }
            },
            "pfStructure": {
                "metrics": {
                    "ltv": {"value": 0.8},
                    "ltc": {"value": 0.9},
                    "exit_sale_rate": {"value": 0.75},
                }
            },
            "scenariosEvaluation": {
                "riskFlags": [
                    {
                        "code": "UNSOLD_LTV_BREACH",
                        "severity": "high",
                        "message": "Unsold LTV exceeds 90%.",
                    }
                ]
            },
            "dataQualityReview": {
                "checks": [
                    {
                        "key": "sales_revenue_consistency",
                        "label": "Total sales revenue consistency",
                        "status": "blocking",
                        "message": "Revenue differs beyond tolerance.",
                    }
                ]
            },
            "riskAssessment": {
                "items": [
                    {
                        "category": "Financing Risk",
                        "item": "PF leverage",
                        "grade": "High",
                        "description": "LTC is high.",
                        "probability": 4,
                        "mitigation": "Increase equity.",
                    }
                ]
            },
        },
    )

    assert result["analysisType"] == "risk_register"
    assert result["status"] == "blocking"
    assert result["summary"]["riskCount"] >= 8
    assert result["summary"]["humanReviewRequiredCount"] > 0
    assert result["approvalConditions"]
    assert result["creditReviewInputs"]["recommendedDecisionBand"] == "rework_required"
    codes = {risk["code"] for risk in result["risks"]}
    assert "NO_COMPARABLE_DATA" in codes
    assert "MAX_MINUS_EXCEEDS_PF_LIMIT" in codes
    assert "UNSOLD_LTV_BREACH" in codes
    assert "SALES_REVENUE_CONSISTENCY" in codes
    assert all(risk["sourceLocators"] for risk in result["risks"])


def test_risk_register_row_review_preserves_audit_history_and_refreshes_readiness():
    run = {
        "handoffs": [],
        "artifacts": {
            "riskRegister": {
                "risks": [
                    {
                        "riskId": "R-MKT-001",
                        "title": "No comparable data",
                        "severity": "high",
                        "requiresHumanReview": True,
                        "status": "open",
                        "reviewStatus": "pending_review",
                        "reviewHistory": [],
                    }
                ],
                "topRisks": [
                    {
                        "riskId": "R-MKT-001",
                        "title": "No comparable data",
                        "severity": "high",
                        "requiresHumanReview": True,
                        "status": "open",
                        "reviewStatus": "pending_review",
                    }
                ],
                "summary": {"humanReviewRequiredCount": 1},
            },
            "creditReviewDecision": {
                "decision": "conditional_approval",
                "keyRisks": [{"riskId": "R-MKT-001", "title": "No comparable data"}],
                "humanReview": {"required": True, "reason": "1 row requires review."},
            },
            "approvalReadinessGate": {
                "pass": False,
                "summary": {"unresolvedRiskReviewCount": 1},
            },
        },
    }

    assert len(unresolved_risk_reviews(run)) == 1
    reviewed = review_risk_register_row_use_case(
        run,
        "R-MKT-001",
        {
            "review_status": "waived",
            "reviewed_by": "credit_officer",
            "comment": "Accepted with covenant.",
            "waiver_reason": "Committee-approved exception.",
        },
        "2026-05-25T00:00:00+09:00",
    )

    risk = reviewed["artifacts"]["riskRegister"]["risks"][0]
    assert risk["reviewStatus"] == "waived"
    assert risk["requiresHumanReview"] is False
    assert risk["reviewHistory"][-1]["waiverReason"] == "Committee-approved exception."
    assert reviewed["artifacts"]["riskRegister"]["reviewSummary"]["unresolvedReviewCount"] == 0
    assert reviewed["artifacts"]["creditReviewDecision"]["keyRisks"][0]["reviewStatus"] == "waived"
    assert reviewed["artifacts"]["approvalReadinessGate"]["summary"]["unresolvedRiskReviewCount"] == 0
    assert unresolved_risk_reviews(reviewed) == []
