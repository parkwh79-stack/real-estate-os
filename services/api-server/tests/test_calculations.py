import pytest
from core.calculations import (
    sqm_to_py, py_to_sqm, calculate_total_revenue, calculate_total_cost,
    calculate_development_profit, calculate_profit_margin, calculate_ltv,
    calculate_ltc, calculate_dscr, calculate_all_in_cost, calculate_exit_sale_rate,
    calculate_cashflow_max_minus, simulate_waterfall, calculate_unsold_ltv,
    calculate_tranche_recovery
)

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

def test_calculate_ltv():
    assert calculate_ltv(500, 1000) == 0.5
    assert calculate_ltv(500, 0) == 0.0

def test_calculate_ltc():
    assert calculate_ltc(800, 1000) == 0.8
    assert calculate_ltc(800, 0) == 0.0

def test_calculate_dscr():
    assert calculate_dscr(120, 100) == 1.2
    assert calculate_dscr(120, 0) == 0.0

def test_calculate_all_in_cost():
    assert calculate_all_in_cost(1000, 0.05, 0.02) == 0.07
    assert calculate_all_in_cost(0, 0.05, 0.02) == 0.0

def test_calculate_exit_sale_rate():
    assert calculate_exit_sale_rate(500, 100, 1000) == 0.6
    assert calculate_exit_sale_rate(500, 100, 0) == 0.0

def test_calculate_cashflow_max_minus():
    assert calculate_cashflow_max_minus([100, -200, 50, -100]) == -150
    assert calculate_cashflow_max_minus([-100, -200, 50, -100]) == -350
    assert calculate_cashflow_max_minus([100, 200], initial_cash=0) == 0.0
    assert calculate_cashflow_max_minus([-100], initial_cash=50) == -50

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

def test_calculate_unsold_ltv():
    assert calculate_unsold_ltv(500, 100, 10, 100) == 0.5 # 500 / 1000
    assert calculate_unsold_ltv(500, 100, 10, 100, discount_rate=0.2) == 0.625 # 500 / 800
    assert calculate_unsold_ltv(500, 100, 0, 100) == 0.0

def test_calculate_tranche_recovery():
    assert calculate_tranche_recovery(100, 500, 200) == 1.0 # pool=500, prior=200, avail=300, claim=100 -> full
    assert calculate_tranche_recovery(200, 250, 100) == 0.75 # pool=250, prior=100, avail=150, claim=200 -> 150/200 = 0.75
    assert calculate_tranche_recovery(100, 50, 100) == 0.0 # pool=50, prior=100 -> avail=0
