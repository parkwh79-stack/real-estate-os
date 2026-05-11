from typing import List, Dict, Any, Optional

def sqm_to_py(sqm: float) -> float:
    return sqm * 0.3025

def py_to_sqm(py: float) -> float:
    return py / 0.3025

def calculate_total_revenue(sales_revenue: float, other_revenue: float = 0.0) -> float:
    return sales_revenue + other_revenue

def calculate_total_cost(land_cost: float, construction_cost: float, finance_cost: float, other_cost: float) -> float:
    return land_cost + construction_cost + finance_cost + other_cost

def calculate_development_profit(total_revenue: float, total_cost: float) -> float:
    return total_revenue - total_cost

def calculate_profit_margin(development_profit: float, total_revenue: float) -> float:
    if total_revenue <= 0:
        return 0.0
    return development_profit / total_revenue

def calculate_ltv(pf_amount: float, total_revenue: float) -> float:
    if total_revenue <= 0:
        return 0.0
    return pf_amount / total_revenue

def calculate_ltc(pf_amount: float, total_cost: float) -> float:
    if total_cost <= 0:
        return 0.0
    return pf_amount / total_cost

def calculate_dscr(net_operating_income: float, debt_service: float) -> float:
    if debt_service <= 0:
        return 0.0
    return net_operating_income / debt_service

def calculate_all_in_cost(principal: float, interest_rate: float, fee_rate: float) -> float:
    if principal <= 0:
        return 0.0
    # simplified all-in-cost calculation for MVP: (Interest + Fees) / Principal annualized.
    # Assuming 1 year term for simplicity in this pure function, can be extended to term-based.
    return interest_rate + fee_rate

def calculate_exit_sale_rate(pf_amount: float, other_priority_costs: float, total_revenue: float) -> float:
    """Calculates the percentage of units that must be sold to cover PF and priority costs."""
    if total_revenue <= 0:
        return 0.0
    return (pf_amount + other_priority_costs) / total_revenue

def calculate_cashflow_max_minus(cashflows: List[float], initial_cash: float = 0.0) -> float:
    cumulative = initial_cash
    max_minus = min(0.0, cumulative)
    for cf in cashflows:
        cumulative += cf
        if cumulative < max_minus:
            max_minus = cumulative
    return max_minus

def simulate_waterfall(cash_available: float, tranches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Simulates a simple waterfall payment to tranches based on priority."""
    remaining_cash = cash_available
    results = []
    
    # Sort by priority ascending (1 is highest)
    sorted_tranches = sorted(tranches, key=lambda x: x.get('priority', 99))
    
    for tranche in sorted_tranches:
        amount_owed = tranche.get('amount', 0)
        interest_owed = amount_owed * tranche.get('interest_rate', 0.0)
        total_owed = amount_owed + interest_owed
        
        payment = min(remaining_cash, total_owed)
        remaining_cash -= payment
        
        results.append({
            "tranche": tranche.get("tranche", "Unknown"),
            "amount_owed": total_owed,
            "payment_received": payment,
            "shortfall": total_owed - payment
        })
        
        if remaining_cash <= 0:
            break
            
    # For any remaining tranches that received nothing
    for tranche in sorted_tranches[len(results):]:
        amount_owed = tranche.get('amount', 0)
        total_owed = amount_owed + (amount_owed * tranche.get('interest_rate', 0.0))
        results.append({
            "tranche": tranche.get("tranche", "Unknown"),
            "amount_owed": total_owed,
            "payment_received": 0.0,
            "shortfall": total_owed
        })
        
    return results

def calculate_unsold_ltv(pf_amount: float, total_units: int, unsold_units: int, avg_unit_price: float, discount_rate: float = 0.0) -> float:
    if unsold_units <= 0 or avg_unit_price <= 0:
        return 0.0
    unsold_value = unsold_units * avg_unit_price * (1.0 - discount_rate)
    if unsold_value <= 0:
        return 0.0
    return pf_amount / unsold_value

def calculate_tranche_recovery(tranche_amount: float, total_recovery_pool: float, prior_claims: float) -> float:
    available_for_tranche = max(0.0, total_recovery_pool - prior_claims)
    if tranche_amount <= 0:
        return 0.0
    recovery_rate = available_for_tranche / tranche_amount
    return min(1.0, recovery_rate)
