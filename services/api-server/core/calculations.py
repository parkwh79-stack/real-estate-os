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

def calculate_profit_on_cost(development_profit: float, total_cost: float) -> float:
    if total_cost <= 0:
        return 0.0
    return development_profit / total_cost

def calculate_feasibility_metrics(input_data: Dict[str, Any]) -> Dict[str, Any]:
    sales_revenue = _to_float(input_data.get("sales_revenue", 0.0))
    other_revenue = _to_float(input_data.get("other_revenue", 0.0))
    total_revenue = _to_float(input_data.get("total_revenue", 0.0))
    if total_revenue <= 0:
        total_revenue = calculate_total_revenue(sales_revenue, other_revenue)

    land_cost = _to_float(input_data.get("land_cost", 0.0))
    construction_cost = _to_float(input_data.get("construction_cost", 0.0))
    finance_cost = _to_float(input_data.get("finance_cost", 0.0))
    other_cost = _to_float(input_data.get("other_cost", 0.0))
    total_cost = _to_float(input_data.get("total_cost", 0.0))
    if total_cost <= 0:
        total_cost = calculate_total_cost(land_cost, construction_cost, finance_cost, other_cost)

    development_profit = calculate_development_profit(total_revenue, total_cost)
    profit_margin = calculate_profit_margin(development_profit, total_revenue)
    profit_on_cost = calculate_profit_on_cost(development_profit, total_cost)
    cost_ratio = total_cost / total_revenue if total_revenue > 0 else 0.0
    break_even_revenue = total_cost
    revenue_surplus_to_break_even = total_revenue - break_even_revenue

    return {
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "development_profit": development_profit,
        "profit_margin": profit_margin,
        "profit_on_cost": profit_on_cost,
        "cost_ratio": cost_ratio,
        "break_even_revenue": break_even_revenue,
        "revenue_surplus_to_break_even": revenue_surplus_to_break_even,
        "status": _feasibility_status(profit_margin, profit_on_cost),
        "inputs": {
            "sales_revenue": sales_revenue,
            "other_revenue": other_revenue,
            "land_cost": land_cost,
            "construction_cost": construction_cost,
            "finance_cost": finance_cost,
            "other_cost": other_cost,
        },
    }

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

def calculate_cashflow_max_minus_details(cashflows: List[float], initial_cash: float = 0.0) -> Dict[str, Any]:
    cumulative = initial_cash
    min_balance = min(0.0, cumulative)
    min_index = 0 if cumulative < 0 else None
    cumulative_values = []

    for index, cf in enumerate(cashflows, start=1):
        cumulative += cf
        cumulative_values.append(cumulative)
        if cumulative < min_balance:
            min_balance = cumulative
            min_index = index

    return {
        "max_minus": min_balance,
        "max_minus_month": min_index,
        "ending_cash": cumulative,
        "cumulative_cashflows": cumulative_values,
    }

def build_monthly_cash_flow(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """Builds a normalized monthly cash-flow table and Max Minus summary.

    Supported inputs:
    - {"cashflows": [net, net, ...]}
    - {"revenue_schedule": [...], "cost_schedule": [...]}
    - {"cash_in": [...], "cash_out": [...]}
    Each schedule item can be a number or a dict with amount/value and month/period.
    """
    initial_cash = float(input_data.get("initial_cash", 0.0))
    net_cashflows = input_data.get("cashflows")
    if isinstance(net_cashflows, list):
        rows = []
        cumulative = initial_cash
        for index, value in enumerate(net_cashflows, start=1):
            net = _to_float(value)
            cash_in = max(net, 0.0)
            cash_out = abs(min(net, 0.0))
            cumulative += net
            rows.append({
                "month": index,
                "period": f"M{index}",
                "cash_in": cash_in,
                "cash_out": cash_out,
                "net_cashflow": net,
                "cumulative_cashflow": cumulative,
            })
        details = calculate_cashflow_max_minus_details([row["net_cashflow"] for row in rows], initial_cash)
        return _cashflow_summary(rows, details, initial_cash)

    revenue_schedule = (
        input_data.get("revenue_schedule")
        or input_data.get("cash_in")
        or input_data.get("cashIn")
        or []
    )
    cost_schedule = (
        input_data.get("cost_schedule")
        or input_data.get("cash_out")
        or input_data.get("cashOut")
        or []
    )
    cash_in_series = _schedule_to_series(revenue_schedule)
    cash_out_series = _schedule_to_series(cost_schedule)
    months = max(len(cash_in_series), len(cash_out_series), int(input_data.get("months", 0) or 0))

    rows = []
    cumulative = initial_cash
    for index in range(months):
        in_item = cash_in_series[index] if index < len(cash_in_series) else {}
        out_item = cash_out_series[index] if index < len(cash_out_series) else {}
        cash_in = in_item.get("amount", 0.0)
        cash_out = out_item.get("amount", 0.0)
        period = in_item.get("period") or out_item.get("period") or f"M{index + 1}"
        net = cash_in - cash_out
        cumulative += net
        rows.append({
            "month": index + 1,
            "period": period,
            "cash_in": cash_in,
            "cash_out": cash_out,
            "net_cashflow": net,
            "cumulative_cashflow": cumulative,
        })

    details = calculate_cashflow_max_minus_details([row["net_cashflow"] for row in rows], initial_cash)
    return _cashflow_summary(rows, details, initial_cash)

def calculate_pf_metrics(input_data: Dict[str, Any]) -> Dict[str, Any]:
    tranches = input_data.get("tranches", [])
    total_pf = float(input_data.get("pf_amount", 0.0) or 0.0)
    if tranches:
        total_pf = sum(_to_float(tranche.get("amount", 0.0)) for tranche in tranches)

    total_revenue = _to_float(input_data.get("total_revenue", 0.0))
    total_cost = _to_float(input_data.get("total_cost", 0.0))
    other_priority_costs = _to_float(input_data.get("other_priority_costs", input_data.get("finance_cost", 0.0)))
    noi = _to_float(input_data.get("net_operating_income", input_data.get("noi", 0.0)))
    debt_service = _to_float(input_data.get("debt_service", 0.0))

    tranche_results = []
    annual_debt_service = 0.0
    fee_total = 0.0
    for tranche in sorted(tranches, key=lambda item: item.get("priority", 99)):
        amount = _to_float(tranche.get("amount", 0.0))
        interest_rate = _to_float(tranche.get("interest_rate", tranche.get("rate", 0.0)))
        fee_rate = _to_float(tranche.get("fee_rate", 0.0))
        interest_amount = amount * interest_rate
        fee_amount = amount * fee_rate
        annual_debt_service += interest_amount
        fee_total += fee_amount
        tranche_results.append({
            "tranche": tranche.get("tranche", tranche.get("name", "Unknown")),
            "priority": tranche.get("priority"),
            "amount": amount,
            "interest_rate": interest_rate,
            "fee_rate": fee_rate,
            "all_in_cost": calculate_all_in_cost(amount, interest_rate, fee_rate),
            "annual_interest": interest_amount,
            "fee_amount": fee_amount,
        })

    effective_debt_service = debt_service or annual_debt_service
    ltv = calculate_ltv(total_pf, total_revenue)
    ltc = calculate_ltc(total_pf, total_cost)
    dscr = calculate_dscr(noi, effective_debt_service)
    exit_sale_rate = calculate_exit_sale_rate(total_pf, other_priority_costs, total_revenue)

    return {
        "total_pf_amount": total_pf,
        "total_revenue": total_revenue,
        "total_cost": total_cost,
        "other_priority_costs": other_priority_costs,
        "annual_debt_service": effective_debt_service,
        "fee_total": fee_total,
        "metrics": {
            "ltv": {"value": ltv, "status": _lower_is_better_status(ltv, 0.60, 0.75, 0.90)},
            "ltc": {"value": ltc, "status": _lower_is_better_status(ltc, 0.70, 0.85, 1.00)},
            "dscr": {"value": dscr, "status": _higher_is_better_status(dscr, 1.30, 1.10, 1.00)},
            "exit_sale_rate": {
                "value": exit_sale_rate,
                "status": _lower_is_better_status(exit_sale_rate, 0.60, 0.75, 0.90),
            },
        },
        "tranches": tranche_results,
        "summary": _pf_metric_summary(ltv, ltc, dscr, exit_sale_rate),
    }

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

def _schedule_to_series(schedule: List[Any]) -> List[Dict[str, Any]]:
    series = []
    for index, item in enumerate(schedule, start=1):
        if isinstance(item, dict):
            amount = _to_float(
                item.get("amount", item.get("value", item.get("cashflow", item.get("cash_flow", 0.0))))
            )
            period = item.get("period") or item.get("month") or f"M{index}"
        else:
            amount = _to_float(item)
            period = f"M{index}"
        series.append({"period": str(period), "amount": amount})
    return series

def _cashflow_summary(rows: List[Dict[str, Any]], details: Dict[str, Any], initial_cash: float) -> Dict[str, Any]:
    max_minus_month = details["max_minus_month"]
    max_minus_period = None
    if max_minus_month and 1 <= max_minus_month <= len(rows):
        max_minus_period = rows[max_minus_month - 1]["period"]

    return {
        "initial_cash": initial_cash,
        "period_count": len(rows),
        "rows": rows,
        "net_cashflows": [row["net_cashflow"] for row in rows],
        "cumulative_cashflows": details["cumulative_cashflows"],
        "total_cash_in": sum(row["cash_in"] for row in rows),
        "total_cash_out": sum(row["cash_out"] for row in rows),
        "ending_cash": details["ending_cash"],
        "max_minus": details["max_minus"],
        "max_minus_month": max_minus_month,
        "max_minus_period": max_minus_period,
        "funding_gap": abs(details["max_minus"]),
        "status": "funding_required" if details["max_minus"] < 0 else "self_funding",
    }

def _to_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

def _lower_is_better_status(value: float, safe: float, caution: float, high: float) -> str:
    if value <= safe:
        return "safe"
    if value <= caution:
        return "caution"
    if value <= high:
        return "high"
    return "critical"

def _higher_is_better_status(value: float, safe: float, caution: float, high: float) -> str:
    if value >= safe:
        return "safe"
    if value >= caution:
        return "caution"
    if value >= high:
        return "high"
    return "critical"

def _pf_metric_summary(ltv: float, ltc: float, dscr: float, exit_sale_rate: float) -> str:
    statuses = [
        _lower_is_better_status(ltv, 0.60, 0.75, 0.90),
        _lower_is_better_status(ltc, 0.70, 0.85, 1.00),
        _higher_is_better_status(dscr, 1.30, 1.10, 1.00),
        _lower_is_better_status(exit_sale_rate, 0.60, 0.75, 0.90),
    ]
    if "critical" in statuses:
        return "PF metrics include critical thresholds requiring restructuring or additional equity."
    if "high" in statuses:
        return "PF metrics are bankable only with mitigation, covenants, or pricing adjustment."
    if "caution" in statuses:
        return "PF metrics are generally workable but require credit review attention."
    return "PF metrics are within conservative review thresholds."

def _feasibility_status(profit_margin: float, profit_on_cost: float) -> str:
    if profit_margin < 0 or profit_on_cost < 0:
        return "loss"
    if profit_margin >= 0.18 and profit_on_cost >= 0.22:
        return "strong"
    if profit_margin >= 0.12 and profit_on_cost >= 0.15:
        return "bankable"
    if profit_margin >= 0.07 and profit_on_cost >= 0.10:
        return "thin"
    return "weak"
