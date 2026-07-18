import pytest
from core.calculations import (
    sqm_to_py, py_to_sqm, calculate_total_revenue, calculate_total_cost,
    calculate_development_profit, calculate_profit_margin, calculate_ltv,
    calculate_ltc, calculate_dscr, calculate_all_in_cost, calculate_exit_sale_rate,
    calculate_cashflow_max_minus, simulate_waterfall, calculate_unsold_ltv,
    calculate_tranche_recovery, calculate_cashflow_max_minus_details,
    build_monthly_cash_flow, calculate_pf_metrics, calculate_feasibility_metrics,
    npv, calculate_irr, annualize_rate, calculate_irr_annualized,
    calculate_equity_multiple, calculate_payback_period, calculate_icr,
    calculate_return_metrics,
    calculate_sales_value_ltv, calculate_appraisal_ltv, calculate_unsold_value_ltv,
    calculate_senior_ltv, calculate_total_ltv, calculate_ltv_bundle,
    calculate_xnpv, calculate_xirr, calculate_tranche_xirr,
)
from core.risk_assessment import build_basic_risk_matrix

def test_sqm_to_py():
    assert sqm_to_py(100) == 30.25
    assert sqm_to_py(0) == 0.0

def test_py_to_sqm():
    assert py_to_sqm(30.25) == 100.0
    assert py_to_sqm(0) == 0.0

def test_calculate_total_revenue():
    assert calculate_total_revenue(1000, 200) == 1200
    assert calculate_total_revenue(1000) == 1000

def test_calculate_total_cost():
    assert calculate_total_cost(100, 200, 50, 50) == 400

def test_calculate_development_profit():
    assert calculate_development_profit(1000, 800) == 200
    assert calculate_development_profit(1000, 1200) == -200

def test_calculate_profit_margin():
    assert calculate_profit_margin(200, 1000) == 0.2
    assert calculate_profit_margin(200, 0) == 0.0

def test_calculate_feasibility_metrics_from_totals():
    result = calculate_feasibility_metrics({
        "total_revenue": 1200,
        "total_cost": 900,
    })
    assert result["development_profit"] == 300
    assert result["profit_margin"] == 0.25
    assert result["profit_on_cost"] == pytest.approx(0.3333333333)
    assert result["break_even_revenue"] == 900
    assert result["status"] == "strong"

def test_calculate_feasibility_metrics_from_components():
    result = calculate_feasibility_metrics({
        "sales_revenue": 1000,
        "other_revenue": 50,
        "land_cost": 250,
        "construction_cost": 500,
        "finance_cost": 80,
        "other_cost": 70,
    })
    assert result["total_revenue"] == 1050
    assert result["total_cost"] == 900
    assert result["development_profit"] == 150
    assert result["status"] in {"bankable", "thin", "weak", "strong"}

def test_calculate_ltv():
    assert calculate_ltv(500, 1000) == 0.5
    assert calculate_ltv(500, 0) == 0.0

def test_basis_specific_ltv_metrics_are_not_conflated():
    assert calculate_sales_value_ltv(500, 1000) == 0.5
    assert calculate_appraisal_ltv(500, 800) == 0.625
    assert calculate_unsold_value_ltv(300, 400) == 0.75
    assert calculate_senior_ltv(400, 800) == 0.5
    assert calculate_total_ltv(600, 800) == 0.75
    bundle = calculate_ltv_bundle({
        "total_debt": 600,
        "senior_debt": 400,
        "sales_value": 1000,
        "appraisal_value": 800,
        "adjusted_unsold_value": 500,
        "unsold_exposure": 300,
    })
    assert bundle["sales_value_ltv"] == 0.6
    assert bundle["appraisal_ltv"] == 0.75
    assert bundle["unsold_ltv"] == 0.6
    assert bundle["senior_ltv"] == 0.5

def test_calculate_ltc():
    assert calculate_ltc(800, 1000) == 0.8
    assert calculate_ltc(800, 0) == 0.0

def test_calculate_dscr():
    assert calculate_dscr(120, 100) == 1.2
    assert calculate_dscr(120, 0) == 0.0

def test_calculate_all_in_cost():
    assert calculate_all_in_cost(1000, 0.05, 0.02) == 0.07
    assert calculate_all_in_cost(0, 0.05, 0.02) == 0.0

def test_tranche_all_in_cost_uses_dated_xirr():
    cashflows = [
        {"date": "2026-01-01", "amount": 980},
        {"date": "2027-01-01", "amount": -1050},
    ]
    assert calculate_xnpv(0.0, cashflows) == -70
    assert calculate_xirr(cashflows) == pytest.approx(1050 / 980 - 1, abs=1e-8)
    result = calculate_tranche_xirr({"cashFlows": cashflows})
    assert result["status"] == "calculated"
    assert result["method"] == "dated_borrower_cashflow_xirr"
    assert result["value"] == pytest.approx(1050 / 980 - 1, abs=1e-8)

def test_tranche_all_in_cost_requires_dated_cashflows():
    result = calculate_tranche_xirr({"amount": 1000, "interest_rate": 0.05, "fee_rate": 0.02})
    assert result["value"] is None
    assert result["status"] == "insufficient_input"

def test_calculate_exit_sale_rate():
    assert calculate_exit_sale_rate(500, 100, 1000) == 0.6
    assert calculate_exit_sale_rate(500, 100, 0) == 0.0

def test_calculate_cashflow_max_minus():
    assert calculate_cashflow_max_minus([100, -200, 50, -100]) == -150
    assert calculate_cashflow_max_minus([-100, -200, 50, -100]) == -350
    assert calculate_cashflow_max_minus([100, 200], initial_cash=0) == 0.0
    assert calculate_cashflow_max_minus([-100], initial_cash=50) == -50

def test_calculate_cashflow_max_minus_details():
    details = calculate_cashflow_max_minus_details([100, -250, 50], initial_cash=20)
    assert details["max_minus"] == -130
    assert details["max_minus_month"] == 2
    assert details["ending_cash"] == -80

def test_build_monthly_cash_flow_from_revenue_and_cost_schedules():
    result = build_monthly_cash_flow({
        "initial_cash": 10,
        "revenue_schedule": [{"period": "M1", "amount": 100}, {"period": "M2", "amount": 30}],
        "cost_schedule": [{"period": "M1", "amount": 60}, {"period": "M2", "amount": 120}],
    })
    assert result["period_count"] == 2
    assert result["rows"][0]["net_cashflow"] == 40
    assert result["rows"][1]["cumulative_cashflow"] == -40
    assert result["max_minus"] == -40
    assert result["funding_gap"] == 40
    assert result["status"] == "funding_required"

def test_build_monthly_cash_flow_from_legacy_net_cashflows():
    result = build_monthly_cash_flow({"cashflows": [100, -200, 50]})
    assert result["net_cashflows"] == [100.0, -200.0, 50.0]
    assert result["max_minus_period"] == "M2"
    assert result["total_cash_in"] == 150.0
    assert result["total_cash_out"] == 200.0

def test_simulate_waterfall():
    tranches = [
        {"tranche": "B", "amount": 200, "interest_rate": 0.1, "priority": 2},
        {"tranche": "A", "amount": 100, "interest_rate": 0.05, "priority": 1}
    ]
    # Total A owed = 105, Total B owed = 220
    
    # Case 1: Enough to pay A, not enough to fully pay B
    res1 = simulate_waterfall(200, tranches)
    assert res1[0]["tranche"] == "A"
    assert res1[0]["payment_received"] == 105
    assert res1[1]["tranche"] == "B"
    assert res1[1]["payment_received"] == 95
    assert res1[1]["shortfall"] == 125

    # Case 2: Not enough to fully pay A
    res2 = simulate_waterfall(50, tranches)
    assert res2[0]["payment_received"] == 50
    assert res2[0]["shortfall"] == 55
    assert res2[1]["payment_received"] == 0

def test_tranche_priority_labels_are_normalized_for_pf_metrics_and_waterfall():
    tranches = [
        {"tranche": "Tr.B", "amount": 200, "interest_rate": 0.10, "priority": "mezzanine"},
        {"tranche": "Tr.A", "amount": 100, "interest_rate": 0.05, "priority": "senior"},
    ]
    waterfall = simulate_waterfall(105, tranches)
    assert waterfall[0]["tranche"] == "Tr.A"
    assert waterfall[0]["payment_received"] == 105

    metrics = calculate_pf_metrics({
        "tranches": tranches,
        "total_revenue": 1000,
        "total_cost": 800,
        "collateral_value": 200,
    })
    assert metrics["metrics"]["senior_ltv"]["value"] == 0.5
    assert metrics["tranches"][0]["priority_rank"] == 1
    assert metrics["tranches"][1]["priority_rank"] == 2

def test_calculate_unsold_ltv():
    assert calculate_unsold_ltv(500, 100, 10, 100) == 0.5 # 500 / 1000
    assert calculate_unsold_ltv(500, 100, 10, 100, discount_rate=0.2) == 0.625 # 500 / 800
    assert calculate_unsold_ltv(500, 100, 0, 100) == 0.0

def test_calculate_tranche_recovery():
    assert calculate_tranche_recovery(100, 500, 200) == 1.0 # pool=500, prior=200, avail=300, claim=100 -> full
    assert calculate_tranche_recovery(200, 250, 100) == 0.75 # pool=250, prior=100, avail=150, claim=200 -> 150/200 = 0.75
    assert calculate_tranche_recovery(100, 50, 100) == 0.0 # pool=50, prior=100 -> avail=0

def test_calculate_pf_metrics_returns_statuses_and_tranche_costs():
    result = calculate_pf_metrics({
        "tranches": [
            {"tranche": "A", "amount": 600, "interest_rate": 0.06, "fee_rate": 0.01, "priority": 1},
            {"tranche": "B", "amount": 200, "interest_rate": 0.09, "fee_rate": 0.02, "priority": 2},
        ],
        "total_revenue": 1200,
        "total_cost": 1000,
        "other_priority_costs": 100,
        "net_operating_income": 80,
    })
    assert result["total_pf_amount"] == 800
    assert result["metrics"]["ltv"]["value"] == pytest.approx(0.6666666667)
    assert result["metrics"]["ltc"]["value"] == 0.8
    assert result["metrics"]["exit_sale_rate"]["value"] == 0.75
    assert result["metrics"]["sales_value_ltv"]["value"] == pytest.approx(0.6666666667)
    assert result["metrics"]["ltv"]["basis"] == "gross_sales_value"
    assert result["metrics"]["appraisal_ltv"]["status"] == "insufficient_input"
    assert result["tranches"][0]["all_in_cost"] is None
    assert result["tranches"][0]["all_in_cost_status"] == "insufficient_input"
    assert result["tranches"][0]["nominal_rate_plus_fee_proxy"] == pytest.approx(0.07)
    assert result["metrics"]["dscr"]["status"] in {"safe", "caution", "high", "critical"}

def test_npv_discounts_future_cashflows():
    assert npv(0.0, [-100, 50, 50, 50]) == 50.0
    assert npv(0.1, [-100, 110]) == pytest.approx(0.0, abs=1e-9)

def test_calculate_irr_simple_two_period():
    assert calculate_irr([-100, 110]) == pytest.approx(0.10, abs=1e-6)

def test_calculate_irr_multi_period():
    # -1000 with three 500 inflows -> periodic IRR ~23.375%
    assert calculate_irr([-1000, 500, 500, 500]) == pytest.approx(0.23375, abs=1e-4)

def test_calculate_irr_returns_none_without_sign_change():
    assert calculate_irr([100, 200, 300]) is None
    assert calculate_irr([-100, -200]) is None
    assert calculate_irr([50]) is None

def test_annualize_rate():
    assert annualize_rate(0.01, 12) == pytest.approx((1.01 ** 12) - 1)
    assert annualize_rate(0.1, 1) == pytest.approx(0.1)
    assert annualize_rate(None) is None

def test_calculate_irr_annualized_matches_periodic_when_annual():
    assert calculate_irr_annualized([-100, 110], periods_per_year=1) == pytest.approx(0.10, abs=1e-6)

def test_calculate_equity_multiple():
    assert calculate_equity_multiple([-100, -50, 300]) == pytest.approx(2.0)
    assert calculate_equity_multiple([100, 200]) == 0.0  # no contribution
    assert calculate_equity_multiple([-100, 120]) == pytest.approx(1.2)

def test_calculate_payback_period_interpolates():
    assert calculate_payback_period([-100, 40, 40, 40]) == pytest.approx(3.5)
    assert calculate_payback_period([-100, 40, 40]) is None  # never recovers
    assert calculate_payback_period([100, 50]) == 0.0  # never negative

def test_calculate_icr():
    assert calculate_icr(150, 100) == 1.5
    assert calculate_icr(150, 0) == 0.0

def test_calculate_return_metrics_bundle():
    result = calculate_return_metrics({
        "project_cashflows": [-1000, 500, 500, 500],
        "equity_cashflows": [-300, 0, 0, 700],
        "periods_per_year": 1,
        "hurdle_rate": 0.12,
        "net_operating_income": 180,
        "interest_expense": 100,
    })
    assert result["metrics"]["projectIrr"]["value"] == pytest.approx(0.23375, abs=1e-3)
    assert result["metrics"]["projectIrr"]["meetsHurdle"] is True
    assert result["metrics"]["equityMultiple"]["value"] == pytest.approx(700 / 300)
    assert result["metrics"]["paybackPeriodMonths"]["value"] == pytest.approx(3 + 300 / 700)
    assert result["metrics"]["icr"]["value"] == pytest.approx(1.8)
    assert result["metrics"]["icr"]["status"] == "safe"

def test_calculate_return_metrics_derives_from_schedules():
    result = calculate_return_metrics({
        "revenue_schedule": [0, 0, 1300],
        "cost_schedule": [1000, 0, 0],
        "periods_per_year": 12,
    })
    assert result["inputs"]["projectCashflowPeriods"] == 3
    assert result["metrics"]["projectIrr"]["value"] is not None

def test_build_basic_risk_matrix_infers_project_risks():
    matrix = build_basic_risk_matrix({
        "feasibility_input": {"total_revenue": 1000, "total_cost": 900},
        "pf_structure": [{"amount": 850}],
        "schedule": {},
        "land_acquisition": [{"share": 0.5}],
        "covenants": {},
    })
    assert matrix["matrixType"] == "basic_real_estate_pf_risk_matrix"
    assert matrix["overall"]["overall_grade"] in {"High", "Critical"}
    assert matrix["topRisks"]
    assert len(matrix["heatmap"]) == 25
