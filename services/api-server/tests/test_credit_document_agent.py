from __future__ import annotations

from agents.credit_document_agent import CreditDocumentAgent


MOJIBAKE_MARKERS = ("嫄", "湲", "李", "遺꾩", "?좎", "?ъ", "?먭", "?뱀", "?듭")


def _assert_clean_korean_markdown(markdown: str) -> None:
    assert "억원" in markdown
    assert "—" in markdown
    assert not any(marker in markdown for marker in MOJIBAKE_MARKERS)


def _project_data():
    return {
        "project": {"name": "본리동 주상복합", "type": "mixed_use", "developer": "WAN2개발"},
        "land": {"address": "대구 달서구 본리동"},
        "feasibility_input": {"total_revenue": 165_600_000_000, "total_cost": 151_800_000_000, "finance_cost": 9_000_000_000},
        "pf_structure": [
            {"tranche": "Tranche A", "amount": 60_000_000_000, "interest_rate": 0.06, "fee_rate": 0.015, "all_in_cost": 0.075, "priority": 1},
            {"tranche": "Tranche B", "amount": 30_000_000_000, "interest_rate": 0.09, "fee_rate": 0.02, "all_in_cost": 0.11, "priority": 2},
        ],
        "covenants": {
            "claim_preservation": ["부동산담보신탁 1순위 우선수익권"],
            "drawdown_conditions": ["인허가 완료 확인"],
            "execution_order": ["1. 비용", "2. 이자", "3. 원금"],
            "eod": ["분양률 6개월 미달 시 EOD"],
        },
    }


def _artifacts():
    return {
        "pfStructure": {"ltv": 0.54, "ltc": 0.59, "exit_sale_rate": 0.64},
        "creditEnhancement": {"enhancementGrade": "B", "baseSeniorLtv": 0.6, "adjustedSeniorLtv": 0.52, "missingCriticalMeasures": []},
        "returnMetrics": {"metrics": {"projectIrr": {"value": 0.21}, "equityIrr": {"value": 0.31}, "equityMultiple": {"value": 1.4}, "icr": {"value": 1.2}}},
        "stressTest": {
            "scenarios": [
                {"key": "base", "name": "Base", "projectIrr": 0.21, "seniorStatus": "normal"},
                {"key": "tail", "name": "Tail", "projectIrr": -0.05, "seniorStatus": "senior_principal_loss_possible"},
            ],
            "worstCaseKey": "tail",
            "seniorBreakScenarioKey": "tail",
        },
        "marketabilityAnalysis": {"grade": "B", "score": 78},
        "legalPermitReview": {"status": "caution"},
        "developabilityScore": {"grade": "C", "overallScore": 61.0, "gaps": ["토지 확보·증빙"]},
        "creditReviewDecision": {
            "decision": "conditional_approval",
            "decisionLabel": "조건부 승인 가능",
            "status": "conditional",
            "confidence": 0.7,
            "approvalConditions": ["스트레스 시 Senior 원금 손실 가능 — 추가 신용보강 필요."],
            "requiredFollowUps": ["미확보 토지 증빙 보완"],
            "requiredWaivers": [],
            "keyRisks": [{"title": "Tail 스트레스 Senior 손실", "severity": "high", "mitigant": "추가 담보", "code": "STRESS_SENIOR_SHORTFALL"}],
            "mitigants": ["책임준공 확약"],
            "decisionReasons": ["Credit decision is 조건부 승인 가능.", "Stress base-case Project IRR is 21.0%."],
            "dataLimitations": [],
        },
        "approvalReadinessGate": {"pass": False, "blockers": [{"reason": "토지 등기 증빙 미확보"}]},
    }


def test_term_sheet_synthesises_facility_and_markdown():
    ts = CreditDocumentAgent().build_term_sheet(_project_data(), _artifacts())
    assert ts["schemaVersion"] == "realytics-term-sheet-v1"
    assert ts["facility"]["totalFacility"] == 90_000_000_000
    assert len(ts["facility"]["tranches"]) == 2
    assert ts["facility"]["ltv"] == 0.54
    assert ts["creditEnhancement"]["grade"] == "B"
    assert "부동산담보신탁 1순위 우선수익권" in ts["security"]
    assert ts["markdown"].startswith("# 거래조건서")
    assert "Tranche A" in ts["markdown"]
    assert "EOD" in ts["markdown"]
    assert "여신 한도 및 구조" in ts["markdown"]
    _assert_clean_korean_markdown(ts["markdown"])


def test_ic_memo_carries_recommendation_and_metrics():
    memo = CreditDocumentAgent().build_ic_memo(_project_data(), _artifacts())
    assert memo["schemaVersion"] == "realytics-ic-memo-v1"
    assert memo["recommendation"]["decisionLabel"] == "조건부 승인 가능"
    assert memo["recommendation"]["approvalReady"] is False
    assert memo["keyMetrics"]["projectIrr"] == 0.21
    assert memo["keyMetrics"]["developabilityGrade"] == "C"
    assert memo["keyMetrics"]["marketabilityGrade"] == "B"
    assert len(memo["keyRisks"]) == 1
    assert memo["approvalReadiness"]["blockers"] == ["토지 등기 증빙 미확보"]
    assert "# 여신심사보고서" in memo["markdown"]
    assert "조건부 승인 가능" in memo["markdown"]
    assert "핵심 지표" in memo["markdown"]
    _assert_clean_korean_markdown(memo["markdown"])


def test_documents_are_resilient_to_missing_artifacts():
    ts = CreditDocumentAgent().build_term_sheet({}, {})
    memo = CreditDocumentAgent().build_ic_memo({}, {})
    assert ts["facility"]["totalFacility"] is None
    assert ts["markdown"].startswith("# 거래조건서")
    assert memo["recommendation"]["decisionLabel"] is None
    assert "# 여신심사보고서" in memo["markdown"]
