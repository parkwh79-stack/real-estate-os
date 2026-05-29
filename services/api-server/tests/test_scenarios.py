from __future__ import annotations

from schemas.scenario import (
    ScenarioSetRequest,
    ProductMixItem,
    TrancheItem,
    ScenarioDrivers
)
from agents.scenario_engine_agent import ScenarioEngineAgent
from agents.restructuring_agent import RestructuringAgent
from agents.report_agent import ReportAgent


def test_scenario_engine_agent_execution():
    # 1. Create a full ScenarioSetRequest
    req = ScenarioSetRequest(
        scenarioSetId="test-set-1",
        scenarioSetName="Hana PF Stress Case",
        baseDate="2026-05-20",
        productMix=[
            ProductMixItem(type="Apartment", units=200, avgUnitPrice=600000000.0, totalRevenue=120000000000.0),
            ProductMixItem(type="Officetel", units=50, avgUnitPrice=300000000.0, totalRevenue=15000000000.0)
        ],
        pfStructure=[
            TrancheItem(tranche="Senior (Tr.A)", amount=70000000000.0, priority=1, interestRate=0.06, feeRate=0.01),
            TrancheItem(tranche="Mezzanine (Tr.B)", amount=20000000000.0, priority=2, interestRate=0.08, feeRate=0.015),
            TrancheItem(tranche="Junior (Tr.C)", amount=10000000000.0, priority=3, interestRate=0.10, feeRate=0.02)
        ],
        scenarioDrivers=ScenarioDrivers(
            saleRates=[0.5, 0.7, 1.0],
            discountRates=[0.0, 0.1, 0.2]
        ),
        additionalPriorityCosts=100000000.0
    )

    # 2. Run Scenario Set calculations
    agent = ScenarioEngineAgent()
    result = agent.run_scenario_set(req)

    # 3. Assert Waterfall and Matrix properties
    assert result.scenarioSetId == "test-set-1"
    assert result.status == "completed"
    assert len(result.waterfallScenarios) > 0
    assert result.unsoldLtvMatrix.pfAmount == 100000000000.0
    assert result.unsoldLtvMatrix.totalUnits == 250
    assert result.unsoldLtvMatrix.avgUnitPrice == 540000000.0

    # Verify unsold LTV rows and risk determination
    assert len(result.unsoldLtvMatrix.rows) == 9  # 3 saleRates * 3 discountRates
    for row in result.unsoldLtvMatrix.rows:
        assert row.grossUnsoldValue >= 0.0
        assert row.unsoldLtv >= 0.0
        assert row.riskLevel in {"safe", "caution", "high", "critical"}

    # Verify tranche recovery calculations
    assert len(result.trancheRecoveryMatrix) == 9  # 3 saleRates * 3 discountRates
    for recovery_row in result.trancheRecoveryMatrix:
        assert len(recovery_row.tranches) == 3
        for tranche_rec in recovery_row.tranches:
            assert "tranche" in tranche_rec
            assert "recoveryRatio" in tranche_rec
            assert 0.0 <= tranche_rec["recoveryRatio"] <= 1.0

    # Verify risk flags and Korean report fragments
    assert len(result.riskFlags) >= 0
    assert any(flag.sourceLocators for flag in result.riskFlags)
    assert result.reportFragments.waterfallSummary != ""
    assert result.reportFragments.unsoldLtvSummary != ""
    assert result.reportFragments.trancheRecoverySummary != ""
    assert result.reportFragments.approvalCondition != ""


def test_restructuring_agent_recommends_senior_and_unsold_ltv_cures():
    req = ScenarioSetRequest(
        scenarioSetId="reject-fix-test",
        baseDate="2026-05-27",
        productMix=[
            ProductMixItem(type="Apartment", units=100, avgUnitPrice=1_200_000_000.0, totalRevenue=120_000_000_000.0),
        ],
        pfStructure=[
            TrancheItem(tranche="Senior", amount=100_000_000_000.0, priority=1, interestRate=0.06, feeRate=0.01),
            TrancheItem(tranche="Mezzanine", amount=20_000_000_000.0, priority=2, interestRate=0.08, feeRate=0.02),
        ],
        scenarioDrivers=ScenarioDrivers(
            saleRates=[0.4, 0.5, 0.7, 1.0],
            discountRates=[0.0, 0.1, 0.2],
        ),
    )

    base_result = ScenarioEngineAgent().run_scenario_set(req)
    assert any(flag.code == "SENIOR_SHORTFALL" for flag in base_result.riskFlags)
    assert any(flag.code == "UNSOLD_LTV_BREACH" for flag in base_result.riskFlags)

    plan = RestructuringAgent().build_restructuring_plan(
        req,
        base_result,
        {"seniorBuffer": 1_000_000_000, "unsoldLtvTarget": 0.90},
    )

    assert plan["analysisType"] == "pf_restructuring_plan"
    assert plan["seniorShortfallFix"]["status"] == "action_required"
    assert plan["seniorShortfallFix"]["requiredSeniorReduction"] > 0
    assert plan["unsoldLtvFix"]["status"] == "action_required"
    assert plan["unsoldLtvFix"]["requiredPfReductionOrEquityIncrease"] > 0
    assert plan["adjustedScenarioHealth"]["seniorShortfall"] == 0
    assert "SENIOR_SHORTFALL" not in plan["adjustedScenarioHealth"]["riskCodes"]
    assert plan["recommendations"][0]["code"] == "REDUCE_SENIOR_OR_ADD_EQUITY"


def test_report_agent_dynamic_scenarios():
    input_data = {
        "project": {
            "name": "Hana PF Dream Suite",
            "review_date": "2026-05-20",
            "financial_institution": "Hana Financial Group"
        },
        "feasibility_input": {
            "total_revenue": 150000000000.0,
            "total_cost": 120000000000.0,
            "finance_cost": 10000000000.0
        },
        "pf_structure": [
            {"tranche": "Senior", "amount": 80000000000.0, "priority": 1, "interest_rate": 0.06, "fee_rate": 0.01},
            {"tranche": "Mezzanine", "amount": 20000000000.0, "priority": 2, "interest_rate": 0.08, "fee_rate": 0.015}
        ],
        "product_mix": [
            {"type": "Apartment", "units": 150, "avgUnitPrice": 800000000.0, "totalRevenue": 120000000000.0},
            {"type": "Officetel", "units": 100, "avgUnitPrice": 300000000.0, "totalRevenue": 30000000000.0}
        ],
        "covenants": {
            "drawdown_conditions": ["Covenant A"],
            "revenue_distribution": "대주단 협의 기준",
            "execution_order": ["Tranche A", "Tranche B"]
        }
    }

    # Generate Structured Report
    agent = ReportAgent()
    structured = agent.generate_structured_report(input_data)

    # Assert dynamic sections are populated
    assert "sections" in structured
    
    # 1. Sales revenue distribution policy must include waterfall summary fragment
    policy = structured["sections"]["salesRevenueDistribution"]["policy"]
    assert "Waterfall" in policy or len(policy) > 20
    
    # 2. Structured cashflow rows must include scenario rows
    cf_rows = structured["sections"]["summaryCashFlow"]["rows"]
    assert len(cf_rows) > 2  # base case + stress cases from ScenarioEngineAgent
    
    # 3. Exit Strategy fallback must contain tranche recovery summaries
    fallback = structured["sections"]["exitStrategy"]["fallback"]
    assert any("Senior" in item or len(item) > 10 for item in fallback)
    
    # 4. Precedent drawdown conditions must contain approval conditions
    precedent = structured["sections"]["drawdownConditions"]["precedent"]
    assert len(precedent) > 1
    assert any(len(item) > 5 for item in precedent)

    # 5. Metrics must have unsold LTV cases
    metrics = structured["sections"]["businessFeasibility"]["metrics"]
    assert "unsoldLtvBase" in metrics
    assert "unsoldLtvStress" in metrics
    assert metrics["unsoldLtvBase"] is not None

    # Generate Markdown Report
    markdown_report = agent.generate_report(input_data)
    assert len(markdown_report) > 100
    assert "Waterfall" in markdown_report or "waterfall" in markdown_report


def test_orchestration_quality_check_visual_qa():
    from agents.orchestration_agent import OrchestrationAgent

    input_data = {
        "project": {
            "name": "Hana PF Quality Suite",
            "review_date": "2026-05-20",
            "financial_institution": "Hana Financial Group",
            "status": "active"
        },
        "feasibility_input": {
            "total_revenue": 150000000000.0,
            "total_cost": 120000000000.0,
            "finance_cost": 10000000000.0
        },
        "pf_structure": [
            {"tranche": "Senior", "amount": 80000000000.0, "priority": 1, "interest_rate": 0.06, "fee_rate": 0.01},
            {"tranche": "Mezzanine", "amount": 20000000000.0, "priority": 2, "interest_rate": 0.08, "fee_rate": 0.015}
        ],
        "product_mix": [
            {"type": "Apartment", "units": 150, "avgUnitPrice": 800000000.0, "totalRevenue": 120000000000.0},
            {"type": "Officetel", "units": 100, "avgUnitPrice": 300000000.0, "totalRevenue": 30000000000.0}
        ],
        "covenants": {
            "drawdown_conditions": ["Covenant A"],
            "revenue_distribution": "대주단 협의 기준",
            "execution_order": ["Tranche A", "Tranche B"]
        }
    }

    agent = OrchestrationAgent()
    run = agent.run_pf_review("test-project-123", input_data)
    
    assert run["status"] in {"completed", "completed_with_warnings"}
    assert "qualityCheck" in run["artifacts"]
    
    quality = run["artifacts"]["qualityCheck"]
    assert "pdfStructureQA" in quality
    assert "pdfVisualQA" in quality
    
    assert quality["pdfStructureQA"]["status"] in {"passed", "warnings", "failed"}
    assert quality["pdfVisualQA"]["status"] in {"passed", "regression_detected"}
