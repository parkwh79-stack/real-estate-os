"""Isolated regressions for financial evidence and arithmetic boundaries."""
import json

import pytest

from agents.cash_sweep_agent import CashSweepAgent
from agents.cash_flow_engine import run_cash_flow_engine
from agents.covenant_eod_agent import CovenantEodAgent
from agents.covenant_monitoring_agent import CovenantMonitoringAgent
from agents.credit_enhancement_agent import CreditEnhancementAgent
from agents.credit_review_agent import CreditReviewAgent
from agents.deal_optimization_agent import DealOptimizationAgent
from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from agents.developability_score_agent import DevelopabilityScoreAgent
from agents.restructuring_agent import RestructuringAgent
from agents.scenario_engine_agent import ScenarioEngineAgent
from agents.stress_test_agent import StressTestAgent
from core.calculations import calculate_tranche_xirr
from core.financial_input_validation import FinancialInputError
from schemas.cash_sweep import CashSweepRequest, CashSweepTranche
from schemas.credit_enhancement import CreditEnhancementInput, CreditEnhancementRequest
from schemas.scenario import ProductMixItem, ScenarioDrivers, ScenarioSetRequest, TrancheItem, WaterfallRule
from schemas.stress_test import StressTestRequest


def scenario_request(**updates):
    payload = dict(
        scenarioSetId="financial-audit-fixture",
        productMix=[ProductMixItem(type="Apartment", units=100, avgUnitPrice=10, totalRevenue=1000)],
        pfStructure=[TrancheItem(tranche="Senior", amount=800, priority=5, interestRate=0, feeRate=0)],
        scenarioDrivers=ScenarioDrivers(saleRates=[0.7, 0.8], discountRates=[0], additionalCollateralValue=100),
    )
    payload.update(updates)
    return ScenarioSetRequest(**payload)


def test_missing_credit_gates_cannot_approve():
    result = CreditReviewAgent().decide({}, {})
    assert result["decision"] == "rework_required"
    assert result["humanReview"]["required"] is True
    assert any("riskRegister" in item for item in result["dataLimitations"])


def test_credit_nonfinite_value_is_not_silently_approved():
    artifacts = {key: {"status": "clear"} for key in (
        "riskRegister", "legalPermitReview", "dataQualityReview", "reconciliationGate"
    )}
    artifacts["pfStructure"] = {"exit_sale_rate": float("nan")}
    result = CreditReviewAgent().decide({}, artifacts)
    assert result["decision"] == "rework_required"
    json.dumps(result, allow_nan=False)


def test_missing_cost_does_not_become_one_hundred_percent_margin():
    result = DevelopabilityScoreAgent()._feasibility({"feasibility_input": {"total_revenue": 100}}, None)
    assert result["available"] is False


def test_nonfinite_market_score_does_not_become_full_marks():
    result = DevelopabilityScoreAgent()._marketability({"score": float("nan")})
    assert result["available"] is False


@pytest.mark.parametrize("evidence_ids,locators", [([""], []), (["  "], []), ([], [{}])])
def test_blank_enhancement_evidence_has_no_credit_effect(evidence_ids, locators):
    result = CreditEnhancementAgent().evaluate(CreditEnhancementRequest(
        baseSeniorLtv=0.7,
        enhancements=[CreditEnhancementInput(type="responsible_completion", evidenceIds=evidence_ids, sourceLocators=locators)],
    ))
    assert result.activeCount == 0
    assert result.adjustedSeniorLtv == 0.7
    assert result.measures[0].status == "needs_evidence"


def test_duplicate_tranche_identity_cannot_erase_principal():
    request = CashSweepRequest(monthlyOperatingCashflow=[0], tranches=[
        CashSweepTranche(tranche="Senior", amount=100, priority=1),
        CashSweepTranche(tranche="Senior", amount=200, priority=2),
    ])
    with pytest.raises(ValueError, match="unique"):
        CashSweepAgent().simulate(request)


def test_zero_principal_summary_agrees_with_repayment_result():
    result = CashSweepAgent().simulate(CashSweepRequest(monthlyOperatingCashflow=[0], tranches=[
        CashSweepTranche(tranche="Senior", amount=0, priority=1),
    ]))
    assert result.trancheFullRepaidMonth["Senior"] == 0
    assert "상환되지" not in result.summary


def test_nonfinite_covenant_metric_cannot_report_ok():
    result = CovenantMonitoringAgent().monitor({"ltv": float("nan"), "ltc": 0.6})
    assert result["overallStatus"] == "watch"
    assert result["invalidMetrics"] == ["ltv"]
    json.dumps(result, allow_nan=False)


def test_nonfinite_covenant_threshold_cannot_suppress_breach():
    with pytest.raises(ValueError, match="ltv"):
        CovenantMonitoringAgent().monitor({"ltv": 0.9}, thresholds={"ltv": float("nan")})


def test_dscr_alert_uses_multiple_not_percent():
    result = CovenantMonitoringAgent().monitor({"dscr": 1.0})
    assert "1.00x" in result["alerts"][0]["message"]
    assert "100.00%" not in result["alerts"][0]["message"]


def test_explicit_zero_cost_buffer_is_not_defaulted():
    result = CovenantEodAgent().build({"costs": {"constructionCost": 1000}}, policy={"costOverrunBuffer": 0})
    covenant = next(item for item in result["covenants"] if item["code"] == "COST_OVERRUN_BUFFER")
    assert covenant["thresholdValue"] == 1000


def test_profitability_alternatives_are_one_curable_gap():
    verdict, _ = DealOptimizationAgent._verdict(
        {"decision": "conditional_approval"}, 0.05,
        [{"type": "construction_cost_down", "feasible": False}, {"type": "sale_price_up", "feasible": True}],
        {"status": "clear"},
    )
    assert verdict == "negotiable_conditional"


def test_missing_margin_does_not_become_approvable_after_legal_gate():
    verdict, _ = DealOptimizationAgent._verdict({"decision": "approve"}, None, [], {"status": "clear"})
    assert verdict == "rework_required"


def test_unsold_units_do_not_lose_one_unit_to_binary_float_rounding():
    result = ScenarioEngineAgent().run_scenario_set(scenario_request())
    row = next(row for row in result.unsoldLtvMatrix.rows if row.saleRate == 0.8)
    assert row.unsoldUnits == 20
    recovery = next(row for row in result.trancheRecoveryMatrix if row.saleRate == 0.8 and row.discountRate == 0)
    assert recovery.recoveryPool == 1000


def test_restructuring_uses_existing_collateral_and_declared_seniority():
    request = scenario_request()
    result = ScenarioEngineAgent().run_scenario_set(request)
    plan = RestructuringAgent().build_restructuring_plan(request, result, options={"seniorBuffer": 0})
    fix = plan["unsoldLtvFix"]
    worst = max(result.unsoldLtvMatrix.rows, key=lambda row: row.unsoldLtv)
    assert fix["requiredPfReductionOrEquityIncrease"] == round(800 - worst.collateralAdjustedValue * 0.9)
    assert fix["requiredAdditionalCollateralValue"] == round(800 / 0.9 - worst.collateralAdjustedValue)
    assert plan["seniorShortfallFix"]["status"] == "action_required"
    assert plan["seniorShortfallFix"]["buffer"] == 0
    assert plan["baseScenarioHealth"]["seniorShortfall"] == 100


def test_restructuring_rejects_different_parent_scenario_id():
    request = scenario_request()
    other = ScenarioEngineAgent().run_scenario_set(scenario_request(scenarioSetId="different-parent"))
    with pytest.raises(ValueError, match="scenarioSetId"):
        RestructuringAgent().build_restructuring_plan(request, other)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_financial_inputs_fail_before_calculation(invalid):
    operations = [
        lambda: CreditEnhancementAgent().evaluate(CreditEnhancementRequest(baseSeniorLtv=invalid)),
        lambda: CashSweepAgent().simulate(CashSweepRequest(monthlyOperatingCashflow=[invalid], tranches=[])),
        lambda: StressTestAgent().run_stress_test(StressTestRequest(totalRevenue=invalid)),
        lambda: DevelopmentFeasibilityAgent().analyze({"feasibility_input": {"total_revenue": invalid}}),
        lambda: ScenarioEngineAgent().run_scenario_set(scenario_request(
            scenarioDrivers=ScenarioDrivers(additionalCollateralValue=invalid))),
    ]
    for operation in operations:
        with pytest.raises(ValueError, match="finite"):
            operation()


def test_negative_cash_sweep_principal_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        CashSweepAgent().simulate(CashSweepRequest(monthlyOperatingCashflow=[100], tranches=[
            CashSweepTranche(tranche="Senior", amount=-10, priority=1),
        ]))


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), None, True, "bad amount"])
def test_invalid_dated_xirr_cashflow_cannot_be_calculated(invalid):
    result = calculate_tranche_xirr({"cashFlows": [
        {"date": "2026-01-01", "amount": -100},
        {"date": "2027-01-01", "amount": 110},
        {"date": "2027-01-01", "amount": invalid},
    ]})
    assert result["value"] is None
    assert result["status"] == "invalid_cashflow"
    json.dumps(result, allow_nan=False)


def test_stress_rejects_parent_cost_disagreement_before_running_engine(monkeypatch):
    def unexpected_engine_call(*args, **kwargs):
        pytest.fail("Mismatched parent input must fail before the engine runs")
    monkeypatch.setattr("agents.stress_test_agent.run_cash_flow_engine", unexpected_engine_call)
    request = StressTestRequest(totalRevenue=1000, landCost=100, baseCashFlowRequest={
        "sourcesAndUses": {
            "cost": {"landCost": 200, "totalProjectCost": 200},
            "revenue": {"totalSalesRevenue": 1000},
        },
    })
    with pytest.raises(ValueError, match="cash_flow_bound_stress_mismatch: landCost"):
        StressTestAgent().run_stress_test(request)


@pytest.mark.parametrize("updates", [
    {"currency": "USD"}, {"unit": "MILLION_KRW"},
    {"waterfallRule": WaterfallRule(includeInterest=False)},
    {"waterfallRule": WaterfallRule(includeFees=False)},
    {"waterfallRule": WaterfallRule(priorityOrder=["junior", "senior_principal"])},
])
def test_unsupported_scenario_contract_is_rejected_not_ignored(updates):
    with pytest.raises(FinancialInputError, match="KRW|waterfallRule"):
        ScenarioEngineAgent().run_scenario_set(scenario_request(**updates))


def test_cash_flow_cannot_complete_with_undistributed_total_cost():
    request = {
        "sourcesAndUses": {
            "revenue": {"totalSalesRevenue": 1000},
            "cost": {"totalProjectCost": 600},
            "funding": {"pfLoan": 300},
        },
        "productMix": [{"totalSalesRevenue": 1000, "defaultCollection": "10-60-30"}],
    }
    before = json.dumps(request, sort_keys=True)
    with pytest.raises(FinancialInputError, match="cash_flow_cost_total_mismatch"):
        run_cash_flow_engine(request)
    assert json.dumps(request, sort_keys=True) == before


def test_cash_flow_total_check_allows_only_numerical_rounding():
    with pytest.raises(FinancialInputError, match="cash_flow_cost_total_mismatch"):
        run_cash_flow_engine({"sourcesAndUses": {"cost": {
            "totalProjectCost": 1_000_000_000_000,
            "directConstructionCost": 999_500_000_000,
        }}})
    valid = run_cash_flow_engine({"sourcesAndUses": {"cost": {
        "totalProjectCost": 1000,
        "directConstructionCost": 1000,
    }}})
    assert sum(row["cashOut"]["totalCashOut"] for row in valid["monthlyCashFlow"]) == pytest.approx(1000)


def test_stress_input_error_cannot_become_uniform_fallback(monkeypatch):
    def invalid_engine(*args, **kwargs):
        raise FinancialInputError("cash_flow_cost_total_mismatch")
    monkeypatch.setattr("agents.stress_test_agent.run_cash_flow_engine", invalid_engine)
    with pytest.raises(FinancialInputError, match="cash_flow_cost_total_mismatch"):
        StressTestAgent().run_stress_test(StressTestRequest(totalRevenue=1000, landCost=100))


def test_development_partial_analysis_preserves_missing_cost_allocation():
    result = DevelopmentFeasibilityAgent().analyze({
        "project": {"name": "Partial cost evidence", "type": "apartment"},
        "land": {"area_sqm": 100}, "regulation": {"applied_far": 200},
        "feasibility_input": {"total_revenue": 1000, "total_cost": 600},
        "pf_structure": [{"name": "Senior", "amount": 300, "priority": "senior"}],
        "schedule": {"construction_start": "2026-01-01", "completion": "2027-01-01"},
    })
    assert result["status"] == "completed_with_review"
    assert result["dataStatus"] == "partial"
    assert result["cashFlowRunResult"] is None
    assert result["costs"]["totalCost"] == 600
    assert result["developerReturns"]["developmentProfit"] == 400
    assert result["dataQuality"]["blockingIssues"][0]["code"] == "cash_flow_cost_total_mismatch"
    assert "cash_flow.monthly_cost_allocation" in result["dataQuality"]["missingFields"]
    assert result["pfReadiness"]["isPfReviewReady"] is False
    for key in ("canRunCashFlow", "canRunPfScenarios", "canRunUnsoldLtvMatrix"):
        assert result["downstreamEngineReadiness"][key] is False


def test_development_does_not_swallow_other_financial_input_errors(monkeypatch):
    def invalid_engine(*args, **kwargs):
        raise FinancialInputError("unexpected financial input contract")
    monkeypatch.setattr("agents.development_feasibility_agent.run_cash_flow_engine", invalid_engine)
    with pytest.raises(FinancialInputError, match="unexpected financial"):
        DevelopmentFeasibilityAgent().analyze({})
