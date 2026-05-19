from __future__ import annotations

from typing import Any, Dict, List

from core.calculations import calculate_cashflow_max_minus_details


def run_cash_flow_engine(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run a deterministic baseline cash-flow analysis from CashFlowRunRequest."""
    months = _project_month_count(request)
    sales_schedule = _sales_collection_schedule(request, months)
    cost_schedule = _cost_schedule(request, months)
    tranche_limits = _tranche_limits(request)
    pf_limit = sum(tranche["remaining"] for tranche in tranche_limits)

    rows = []
    beginning_operating_cash = 0.0
    beginning_repayment_reserve = 0.0
    cumulative_before_pf = 0.0
    pre_pf_net_cashflows = []
    total_pf_draw = 0.0

    for month_index in range(months + 1):
        period = f"M{month_index}"
        sales_collection = sales_schedule[month_index] if month_index < len(sales_schedule) else {}
        cost_out = cost_schedule[month_index] if month_index < len(cost_schedule) else {}
        cash_in_before_pf = sum(sales_collection.values())
        cash_out = sum(cost_out.values())
        sales_allocation = _allocate_sales_collection(sales_collection, month_index, months)
        pre_pf_net = cash_in_before_pf - cash_out
        cumulative_before_pf += pre_pf_net
        pre_pf_net_cashflows.append(pre_pf_net)

        required_draw = max(0.0, cash_out - beginning_operating_cash - sales_allocation["operating"])
        pf_draw_by_tranche = _draw_by_priority(required_draw, tranche_limits)
        pf_draw_total = sum(pf_draw_by_tranche.values())
        remaining_pf_limit = sum(tranche["remaining"] for tranche in tranche_limits)
        total_pf_draw += pf_draw_total

        ending_operating_cash = beginning_operating_cash + sales_allocation["operating"] + pf_draw_total - cash_out
        ending_repayment_reserve = beginning_repayment_reserve + sales_allocation["repaymentReserve"]
        ending_cash = ending_operating_cash + ending_repayment_reserve
        rows.append({
            "monthIndex": month_index,
            "period": period,
            "cashIn": {
                "salesCollection": sales_collection,
                "pfDraw": pf_draw_by_tranche,
                "totalPfDraw": pf_draw_total,
                "totalCashIn": cash_in_before_pf + pf_draw_total,
            },
            "cashOut": {
                **cost_out,
                "totalCashOut": cash_out,
            },
            "accounts": {
                "operatingAccountInflow": sales_allocation["operating"] + pf_draw_total,
                "repaymentReserveAccountInflow": sales_allocation["repaymentReserve"],
                "operatingAccountOutflow": cash_out,
                "repaymentReserveAccountOutflow": 0.0,
                "beginningOperatingCash": beginning_operating_cash,
                "endingOperatingCash": ending_operating_cash,
                "beginningRepaymentReserve": beginning_repayment_reserve,
                "endingRepaymentReserve": ending_repayment_reserve,
            },
            "netCashFlowBeforePfDraw": pre_pf_net,
            "pfDrawRequired": required_draw,
            "netCashFlow": cash_in_before_pf + pf_draw_total - cash_out,
            "beginningCash": beginning_operating_cash + beginning_repayment_reserve,
            "endingCash": ending_cash,
            "cumulativeCashFlowBeforePfDraw": cumulative_before_pf,
            "remainingPfLimit": remaining_pf_limit,
        })
        beginning_operating_cash = ending_operating_cash
        beginning_repayment_reserve = ending_repayment_reserve

    max_minus_details = calculate_cashflow_max_minus_details(pre_pf_net_cashflows)
    max_minus = max_minus_details["max_minus"]
    risk_flags = _risk_flags(max_minus, pf_limit, total_pf_draw, sum(tranche["remaining"] for tranche in tranche_limits))
    summary = {
        "projectId": request.get("project", {}).get("projectId"),
        "projectName": request.get("project", {}).get("projectName"),
        "totalRevenue": _total_revenue(request),
        "totalProjectCost": _total_project_cost(request),
        "pfLimit": pf_limit,
        "totalPfDraw": total_pf_draw,
        "undrawnPfLimit": sum(tranche["remaining"] for tranche in tranche_limits),
        "maxMinus": max_minus,
        "maxMinusMonth": max_minus_details["max_minus_month"],
        "requiredPfLimit": abs(max_minus),
        "endingOperatingCash": beginning_operating_cash,
        "endingRepaymentReserve": beginning_repayment_reserve,
        "endingCash": beginning_operating_cash + beginning_repayment_reserve,
        "status": "funding_gap_covered" if abs(max_minus) <= pf_limit else "pf_limit_shortfall",
    }
    return {
        "analysisType": "realytics_cash_flow_engine_v1",
        "status": "completed",
        "dataStatus": "complete" if not request.get("mappingDiagnostics", {}).get("warnings") else "partial",
        "summary": summary,
        "monthlyCashFlow": rows,
        "pfDrawSchedule": _pf_draw_schedule(rows),
        "salesCollectionSchedule": sales_schedule,
        "riskFlags": risk_flags,
        "reportFragments": _report_fragments(summary, risk_flags),
        "sourceMetadata": request.get("sourceMetadata", {}),
    }


def _project_month_count(request: Dict[str, Any]) -> int:
    schedule = request.get("schedule") or {}
    start = _planned_date(schedule.get("construction_start"))
    completion = _planned_date(schedule.get("completion"))
    if start and completion:
        return max(1, _month_diff(start, completion))
    return 36


def _sales_collection_schedule(request: Dict[str, Any], months: int) -> List[Dict[str, float]]:
    schedule = [{"contract": 0.0, "interim": 0.0, "balance": 0.0} for _ in range(months + 1)]
    for product in request.get("productMix", []):
        revenue = float(product.get("totalSalesRevenue") or 0.0)
        collection = product.get("defaultCollection") or "manual"
        for item in _collection_template(collection, months):
            month = min(months, item["month"])
            label = item["label"]
            bucket = "balance" if "balance" in label else "contract" if "contract" in label else "interim"
            schedule[month][bucket] += revenue * item["rate"]
    return schedule


def _collection_template(collection: str, months: int) -> List[Dict[str, Any]]:
    if collection in {"10-60-30", "asset_component_schedule"}:
        return [
            {"label": "contract", "month": 0, "rate": 0.10},
            {"label": "interim_1", "month": 4, "rate": 0.10},
            {"label": "interim_2", "month": 8, "rate": 0.10},
            {"label": "interim_3", "month": 12, "rate": 0.10},
            {"label": "interim_4", "month": 16, "rate": 0.10},
            {"label": "interim_5", "month": 20, "rate": 0.10},
            {"label": "interim_6", "month": 24, "rate": 0.10},
            {"label": "balance", "month": months, "rate": 0.30},
        ]
    if collection == "10-50-40":
        return [
            {"label": "contract", "month": 0, "rate": 0.10},
            {"label": "interim_1", "month": 6, "rate": 0.10},
            {"label": "interim_2", "month": 12, "rate": 0.10},
            {"label": "interim_3", "month": 18, "rate": 0.10},
            {"label": "interim_4", "month": 24, "rate": 0.10},
            {"label": "interim_5", "month": 30, "rate": 0.10},
            {"label": "balance", "month": months, "rate": 0.40},
        ]
    if collection == "forward_sale_closing":
        return [{"label": "balance", "month": months, "rate": 1.0}]
    return [{"label": "balance", "month": months, "rate": 1.0}]


def _cost_schedule(request: Dict[str, Any], months: int) -> List[Dict[str, float]]:
    costs = request.get("sourcesAndUses", {}).get("cost", {})
    schedule = [
        {
            "landCost": 0.0,
            "constructionCost": 0.0,
            "financialCost": 0.0,
            "otherCost": 0.0,
        }
        for _ in range(months + 1)
    ]
    schedule[0]["landCost"] = float(costs.get("landCost") or 0.0)
    construction = float(costs.get("directConstructionCost") or 0.0)
    financial = float(costs.get("financialCost") or 0.0)
    other = float(costs.get("otherCost") or 0.0)
    monthly_construction = construction / max(1, months)
    monthly_financial = financial / max(1, months)
    monthly_other = other / max(1, months)
    for month in range(1, months + 1):
        schedule[month]["constructionCost"] = monthly_construction
        schedule[month]["financialCost"] = monthly_financial
        schedule[month]["otherCost"] = monthly_other
    return schedule


def _pf_limit(request: Dict[str, Any]) -> float:
    return float(request.get("sourcesAndUses", {}).get("funding", {}).get("pfLoan") or 0.0)


def _tranche_limits(request: Dict[str, Any]) -> List[Dict[str, Any]]:
    tranches = []
    for index, tranche in enumerate(request.get("pfStructure", []), start=1):
        amount = float(tranche.get("amount") or 0.0)
        if amount <= 0:
            continue
        tranches.append({
            "tranche": tranche.get("tranche") or tranche.get("name") or f"Tranche {index}",
            "drawPriority": tranche.get("drawPriority") or tranche.get("repaymentPriority") or index,
            "remaining": amount,
        })
    if tranches:
        return sorted(tranches, key=lambda item: item["drawPriority"])
    pf_limit = _pf_limit(request)
    return [{"tranche": "PF Loan", "drawPriority": 1, "remaining": pf_limit}] if pf_limit else []


def _draw_by_priority(required_draw: float, tranche_limits: List[Dict[str, Any]]) -> Dict[str, float]:
    remaining_need = required_draw
    draw_by_tranche: Dict[str, float] = {}
    for tranche in tranche_limits:
        if remaining_need <= 0:
            break
        draw = min(remaining_need, tranche["remaining"])
        if draw > 0:
            tranche["remaining"] -= draw
            remaining_need -= draw
            draw_by_tranche[tranche["tranche"]] = draw
    return draw_by_tranche


def _allocate_sales_collection(sales_collection: Dict[str, float], month_index: int, completion_month: int) -> Dict[str, float]:
    """Allocate sales proceeds into operating and repayment-reserve accounts.

    Baseline rule:
    - Before completion, contract/interim cash is split 75% operating / 25% repayment reserve.
    - Before completion, balance cash is swept 100% to repayment reserve.
    - At or after completion, all sales proceeds are swept to repayment reserve.
    """
    total = sum(sales_collection.values())
    if month_index >= completion_month:
        return {"operating": 0.0, "repaymentReserve": total}

    contract_and_interim = float(sales_collection.get("contract") or 0.0) + float(sales_collection.get("interim") or 0.0)
    balance = float(sales_collection.get("balance") or 0.0)
    operating = contract_and_interim * 0.75
    repayment = contract_and_interim * 0.25 + balance
    return {"operating": operating, "repaymentReserve": repayment}


def _total_revenue(request: Dict[str, Any]) -> float:
    return float(request.get("sourcesAndUses", {}).get("revenue", {}).get("totalSalesRevenue") or 0.0)


def _total_project_cost(request: Dict[str, Any]) -> float:
    return float(request.get("sourcesAndUses", {}).get("cost", {}).get("totalProjectCost") or 0.0)


def _risk_flags(max_minus: float, pf_limit: float, total_pf_draw: float, remaining_pf_limit: float) -> List[Dict[str, Any]]:
    flags = []
    if abs(max_minus) > pf_limit:
        flags.append({
            "code": "MAX_MINUS_EXCEEDS_PF_LIMIT",
            "severity": "critical",
            "message": "Max Minus exceeds the available PF limit.",
        })
    if total_pf_draw > 0 and remaining_pf_limit <= 0:
        flags.append({
            "code": "PF_LIMIT_FULLY_DRAWN",
            "severity": "medium",
            "message": "PF limit is fully drawn during the cash-flow period.",
        })
    return flags


def _pf_draw_schedule(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "monthIndex": row["monthIndex"],
            "period": row["period"],
            "pfDraw": row["cashIn"]["pfDraw"],
            "totalPfDraw": row["cashIn"]["totalPfDraw"],
            "remainingPfLimit": row["remainingPfLimit"],
        }
        for row in rows
        if row["cashIn"]["totalPfDraw"] > 0
    ]


def _report_fragments(summary: Dict[str, Any], risk_flags: List[Dict[str, Any]]) -> Dict[str, str]:
    risk_text = "No critical cash-flow risk flag was generated." if not risk_flags else "; ".join(flag["code"] for flag in risk_flags)
    return {
        "cashFlowSummary": (
            f"Base cash-flow analysis estimates Max Minus at {summary['maxMinus']:,.0f} KRW "
            f"and required PF limit at {summary['requiredPfLimit']:,.0f} KRW."
        ),
        "pfDrawSummary": (
            f"Total PF draw is estimated at {summary['totalPfDraw']:,.0f} KRW, "
            f"with undrawn limit of {summary['undrawnPfLimit']:,.0f} KRW."
        ),
        "riskOpinion": risk_text,
    }


def _planned_date(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("plannedDate") or value.get("planned_date")
    return value if isinstance(value, str) else None


def _month_diff(start: str, end: str) -> int:
    start_year, start_month = [int(part) for part in start[:7].split("-")]
    end_year, end_month = [int(part) for part in end[:7].split("-")]
    return (end_year - start_year) * 12 + (end_month - start_month)
