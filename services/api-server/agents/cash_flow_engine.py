from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from core.calculations import calculate_cashflow_max_minus_details
from core.financial_input_validation import FinancialInputError, require_finite_numbers
from core.schedule import schedule_month_indices
from core.reviewed_monthly_financing import ReviewedMonthlyFinancing, validate_monthly_financing_policy
from core.cash_flow_periods import (
    calculation_period_label, captured_period_basis, interest_calculation,
    is_source_sequence, period_basis, require_monthly_financing_period,
)

MAX_CASH_FLOW_HORIZON_MONTHS = 600


def run_cash_flow_engine(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run a deterministic baseline cash-flow analysis from CashFlowRunRequest."""
    require_finite_numbers(request, "cashFlow")
    period_basis(request)
    monthly_policy = validate_monthly_financing_policy(request.get("monthlyFinancing"))
    if monthly_policy is not None:
        require_monthly_financing_period(request)
    financing = ReviewedMonthlyFinancing(request) if monthly_policy is not None else None
    months = _project_month_count(request)
    sales_schedule = _sales_collection_schedule(request, months)
    if financing is not None and not math.isclose(
        sum(sum(row.values()) for row in sales_schedule), _total_revenue(request), rel_tol=1e-9, abs_tol=1e-6
    ):
        raise FinancialInputError(
            "월별 수금 합계와 총매출이 일치하지 않습니다. 원본의 수금 일정과 수정 매출을 대사해 주세요. "
            "잔여 미수금·미분양을 별도로 남기는 계약은 현재 월별 금융 검토 방식에서 지원하지 않습니다.",
            code="monthly_financing_sales_total_mismatch",
        )
    cost_schedule, cost_schedule_provenance = _cost_schedule(request, months)
    declared_cost = _total_project_cost(request)
    scheduled_cost = sum(sum(row.values()) for row in cost_schedule)
    if declared_cost > 0 and not math.isclose(scheduled_cost, declared_cost, rel_tol=1e-9, abs_tol=1e-6):
        raise FinancialInputError(
            "cash_flow_cost_total_mismatch: monthly costs do not reconcile to totalProjectCost; "
            "a reviewed cost allocation is required.",
            code="cash_flow_cost_total_mismatch",
        )
    allocation_policy = _collection_allocation_policy(request)
    sales_schedule_provenance = _sales_schedule_provenance(request)
    tranche_limits = _tranche_limits(request)
    if financing is not None:
        tranche_limits = financing.tranches
    pf_limit = sum(tranche["remaining"] for tranche in tranche_limits)

    rows = []
    beginning_operating_cash = 0.0
    beginning_repayment_reserve = 0.0
    cumulative_before_pf = 0.0
    pre_pf_net_cashflows = []
    total_pf_draw = 0.0
    cumulative_sales_collection = 0.0
    total_revenue = _total_revenue(request)
    budget_financial_cost = sum(row.get("financialCost", 0.0) for row in cost_schedule)
    completion_month = schedule_month_indices(request.get("schedule")).get("completion", months) if financing else months

    for month_index in range(months + 1):
        period = calculation_period_label(request, month_index)
        sales_collection = sales_schedule[month_index] if month_index < len(sales_schedule) else {}
        cost_out = dict(cost_schedule[month_index]) if month_index < len(cost_schedule) else {}
        cash_in_before_pf = sum(sales_collection.values())
        cumulative_sales_collection += cash_in_before_pf
        cumulative_sales_rate = cumulative_sales_collection / total_revenue if total_revenue > 0 else 0.0
        sales_allocation = _allocate_sales_collection(
            sales_collection,
            month_index,
            completion_month,
            cumulative_sales_rate,
            allocation_policy,
        )
        financing_month = None
        if financing is not None:
            cost_out["financialCost"] = 0.0
            financing_month = financing.month(
                month_index, non_finance_cost=sum(cost_out.values()),
                operating_cash=beginning_operating_cash + sales_allocation["operating"],
                repayment_reserve=beginning_repayment_reserve + sales_allocation["repaymentReserve"],
            )
            cost_out["financialCost"] = financing_month["financialCost"]
        cash_out = sum(cost_out.values())
        pre_pf_net = cash_in_before_pf - cash_out
        cumulative_before_pf += pre_pf_net
        pre_pf_net_cashflows.append(pre_pf_net)

        required_draw = max(0.0, cash_out - beginning_operating_cash - sales_allocation["operating"])
        pf_draw_by_tranche = financing_month["drawByTranche"] if financing_month is not None else _draw_by_priority(required_draw, tranche_limits)
        pf_draw_total = sum(pf_draw_by_tranche.values())
        remaining_pf_limit = sum(tranche["remaining"] for tranche in tranche_limits)
        total_pf_draw += pf_draw_total

        ending_operating_cash = beginning_operating_cash + sales_allocation["operating"] + pf_draw_total - cash_out
        principal_repayment = financing_month["principalRepayment"] if financing_month is not None else 0.0
        ending_repayment_reserve = beginning_repayment_reserve + sales_allocation["repaymentReserve"] - principal_repayment
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
                "repaymentReserveAccountOutflow": principal_repayment,
                "beginningOperatingCash": beginning_operating_cash,
                "endingOperatingCash": ending_operating_cash,
                "beginningRepaymentReserve": beginning_repayment_reserve,
                "endingRepaymentReserve": ending_repayment_reserve,
            },
            "netCashFlowBeforePfDraw": pre_pf_net,
            "pfDrawRequired": required_draw,
            "netCashFlow": cash_in_before_pf + pf_draw_total - cash_out - principal_repayment,
            "beginningCash": beginning_operating_cash + beginning_repayment_reserve,
            "endingCash": ending_cash,
            "cumulativeCashFlowBeforePfDraw": cumulative_before_pf,
            "remainingPfLimit": remaining_pf_limit,
            "scheduleBasis": {
                "costSchedule": cost_schedule_provenance["sourceMode"],
                "collectionAllocation": allocation_policy["sourceMode"],
            },
        })
        if financing_month is not None:
            rows[-1]["monthlyFinancing"] = financing_month
            # Principal is a financing transfer, never a project expense.
            rows[-1]["loanRepayment"] = {row["trancheId"]: row["principalRepayment"]
                                              for row in financing_month["debtBalances"] if row["principalRepayment"] > 0}
        beginning_operating_cash = ending_operating_cash
        beginning_repayment_reserve = ending_repayment_reserve

    max_minus_details = calculate_cashflow_max_minus_details(pre_pf_net_cashflows)
    max_minus = max_minus_details["max_minus"]
    schedule_provenance = {
        "costSchedule": cost_schedule_provenance,
        "salesCollection": sales_schedule_provenance,
        "collectionAllocation": allocation_policy,
    }
    schedule_provenance["reportEligible"] = all(
        bool(item.get("reportEligible"))
        for item in (cost_schedule_provenance, sales_schedule_provenance, allocation_policy)
    )
    if is_source_sequence(request):
        schedule_provenance["periodBasis"] = {
            "sourceMode": "source_sequence_duration_unconfirmed", "reportEligible": False,
            "warnings": ["원본 순서와 실제 달력 월차의 연결이 미확인입니다."],
        }
        schedule_provenance["reportEligible"] = False
    if financing is not None:
        schedule_provenance["monthlyFinancing"] = {"sourceMode": "reviewed_analysis_policy", "reportEligible": False,
                                                   "policy": financing.policy, "warnings": ["monthly_financing_analysis_only"]}
        schedule_provenance["reportEligible"] = False
    risk_flags = _risk_flags(
        max_minus,
        pf_limit,
        total_pf_draw,
        sum(tranche["remaining"] for tranche in tranche_limits),
        schedule_provenance,
    )
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
    result = {
        "analysisType": "realytics_cash_flow_engine_v1",
        "engineVersion": "cash_flow_engine-v2",
        "status": "completed",
        "dataStatus": "complete" if not request.get("mappingDiagnostics", {}).get("warnings") and schedule_provenance["reportEligible"] else "partial",
        "summary": summary,
        "monthlyCashFlow": rows,
        "pfDrawSchedule": _pf_draw_schedule(rows),
        "salesCollectionSchedule": sales_schedule,
        "riskFlags": risk_flags,
        "scheduleProvenance": schedule_provenance,
        "reportFragments": _report_fragments(summary, risk_flags, schedule_provenance),
        "sourceMetadata": request.get("sourceMetadata", {}),
    }
    captured_period = captured_period_basis(request, rows, monthly_financing=financing is not None)
    if captured_period is not None:
        captured_period["interestCalculation"] = interest_calculation(rows, monthly_financing=financing is not None)
        result["periodBasis"] = captured_period
    if financing is not None:
        finance_summary = financing.summary(budget_financial_cost=budget_financial_cost, budget_project_cost=scheduled_cost)
        actual_cost = sum(row["cashOut"]["totalCashOut"] for row in rows)
        finance_summary["costReconciliationDifference"] = actual_cost - finance_summary["calculatedTotalProjectCost"]
        finance_summary["endingUnfundedOperatingCost"] = max(0.0, -beginning_operating_cash)
        finance_summary["peakUnfundedOperatingCost"] = max(row["monthlyFinancing"]["unfundedOperatingCost"] for row in rows)
        finance_summary["overdueDebt"] = rows[-1]["monthlyFinancing"]["overdueDebt"]
        finance_summary["availableDrawCapacity"] = rows[-1]["monthlyFinancing"]["availableDrawCapacity"]
        finance_summary["expiredUndrawnCommitment"] = rows[-1]["monthlyFinancing"]["expiredUndrawnCommitment"]
        finance_summary["reconciliation"] = {key: value for key, value in finance_summary.items()
                                              if isinstance(value, (int, float)) and not isinstance(value, bool)}
        summary.update(totalProjectCost=actual_cost, outstandingDebt=finance_summary["outstandingDebt"],
                       totalPrincipalRepaid=finance_summary["totalPrincipalRepaid"],
                       unfundedOperatingCost=finance_summary["endingUnfundedOperatingCost"],
                       overdueDebt=finance_summary["overdueDebt"],
                       financialCost=finance_summary["calculatedFinancialCost"],
                       developmentProfit=summary["totalRevenue"] - actual_cost)
        if finance_summary["peakUnfundedOperatingCost"] > 1e-6 or finance_summary["outstandingDebt"] > 1e-6:
            summary["status"] = "monthly_financing_shortfall"
        if finance_summary["overdueDebt"] > 1e-6:
            risk_flags.append({"code": "MONTHLY_DEBT_UNREPAID", "severity": "critical", "message": "만기 이후 미상환 원금이 남아 있습니다."})
        if finance_summary["peakUnfundedOperatingCost"] > 1e-6:
            risk_flags.append({"code": "MONTHLY_OPERATING_COST_UNFUNDED", "severity": "critical", "message": "월별 운영·금융비용을 충당할 자금이 부족합니다."})
        result.update(monthlyFinancing=finance_summary, analysisOnly=True, reportEligible=False, dataStatus="partial")
        result["reportFragments"] = _report_fragments(summary, risk_flags, schedule_provenance)
    return result


def _project_month_count(request: Dict[str, Any]) -> int:
    schedule = schedule_month_indices(request.get("schedule"))
    if request.get("monthlyFinancing") is not None and not {"construction_start", "completion"} <= schedule.keys():
        raise FinancialInputError("월별 금융 검토 계산에는 착공·준공 일정이 모두 필요합니다. 기본 36개월을 대신 넣지 않습니다.",
                                  code="monthly_financing_schedule_required")
    candidates = [max(1, schedule["completion"])
                  if {"construction_start", "completion"} <= schedule.keys() else 36]
    candidates.extend(schedule.values())

    explicit_cost = request.get("costSchedule") or request.get("cost_schedule") or {}
    cost_rows = explicit_cost.get("rows") if isinstance(explicit_cost, dict) else explicit_cost
    for fallback_index, row in enumerate(cost_rows or []):
        if isinstance(row, dict):
            candidates.append(
                _non_negative_int(row.get("monthIndex", row.get("month_index", fallback_index)), fallback_index)
            )

    for product in request.get("productMix") or []:
        if not isinstance(product, dict):
            continue
        for step in product.get("collectionSteps") or []:
            if not isinstance(step, dict):
                continue
            monthly_amounts = step.get("monthlyCollectionAmounts")
            if isinstance(monthly_amounts, list) and monthly_amounts:
                candidates.append(len(monthly_amounts) - 1)
            candidates.append(_non_negative_int(step.get("dueMonth"), 0))

    for tranche in request.get("pfStructure") or []:
        if isinstance(tranche, dict):
            candidates.append(_non_negative_int(tranche.get("maturityMonths"), 0))
    horizon = max(candidates)
    if horizon > MAX_CASH_FLOW_HORIZON_MONTHS:
        raise ValueError(
            f"cash-flow horizon {horizon} exceeds the supported maximum "
            f"of {MAX_CASH_FLOW_HORIZON_MONTHS} months"
        )
    return horizon


def _non_negative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _sales_collection_schedule(request: Dict[str, Any], months: int) -> List[Dict[str, float]]:
    schedule = [{"contract": 0.0, "interim": 0.0, "balance": 0.0} for _ in range(months + 1)]
    for product in request.get("productMix", []):
        revenue = float(product.get("totalSalesRevenue") or 0.0)
        explicit_steps = product.get("collectionSteps") or []
        if explicit_steps:
            _apply_explicit_collection_steps(schedule, explicit_steps, months, revenue)
            continue
        collection = product.get("defaultCollection") or "manual"
        for item in _collection_template(collection, months):
            month = min(months, item["month"])
            label = item["label"]
            bucket = "balance" if "balance" in label else "contract" if "contract" in label else "interim"
            schedule[month][bucket] += revenue * item["rate"]
    return schedule


def _apply_explicit_collection_steps(
    schedule: List[Dict[str, float]],
    steps: List[Dict[str, Any]],
    months: int,
    revenue: float,
) -> None:
    """Apply workbook-derived collection rows before default asset schedules."""
    for step in steps:
        label = str(step.get("stage") or step.get("label") or "interim").lower()
        bucket = _collection_bucket(label)
        monthly_amounts = step.get("monthlyCollectionAmounts")
        if isinstance(monthly_amounts, list) and monthly_amounts:
            for month_index, amount in enumerate(monthly_amounts[: len(schedule)]):
                try:
                    schedule[month_index][bucket] += float(amount or 0.0)
                except (TypeError, ValueError):
                    continue
            continue
        due_month = step.get("dueMonth") or step.get("month")
        rate = step.get("collectionRate") or step.get("rate") or 0.0
        try:
            month = min(months, max(0, int(due_month or 0)))
            schedule[month][bucket] += revenue * float(rate)
        except (TypeError, ValueError):
            continue


def _collection_bucket(label: str) -> str:
    normalized_label = str(label).casefold()
    if any(token in normalized_label for token in ("balance", "\uC794\uAE08", "final")):
        return "balance"
    if any(token in normalized_label for token in ("contract", "\uACC4\uC57D", "deposit")):
        return "contract"
    if any(token in label for token in ("balance", "잔금", "final")):
        return "balance"
    if any(token in label for token in ("contract", "계약", "deposit")):
        return "contract"
    return "interim"


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


def _cost_schedule(request: Dict[str, Any], months: int) -> Tuple[List[Dict[str, float]], Dict[str, Any]]:
    costs = request.get("sourcesAndUses", {}).get("cost", {})
    explicit = request.get("costSchedule") or request.get("cost_schedule") or {}
    explicit_rows = explicit.get("rows") if isinstance(explicit, dict) else explicit
    if isinstance(explicit_rows, list) and explicit_rows:
        mapped = _explicit_cost_schedule(explicit_rows, months)
        if any(sum(row.values()) for row in mapped):
            approval_status = str((explicit if isinstance(explicit, dict) else {}).get("approvalStatus") or "unapproved")
            locator = (explicit if isinstance(explicit, dict) else {}).get("sourceLocator") or _row_source_locator(explicit_rows)
            source_mode = "approved_schedule" if approval_status == "approved" else "evidence_schedule" if locator else "explicit_estimate"
            totals_warning = _cost_schedule_reconciliation_warning(mapped, costs)
            report_eligible = source_mode == "approved_schedule" and not totals_warning
            warnings = [totals_warning] if totals_warning else []
            if source_mode != "approved_schedule":
                warnings.append("monthly_cost_schedule_not_approved")
            return mapped, {
                "sourceMode": source_mode,
                "approvalStatus": approval_status,
                "sourceLocator": locator,
                "reportEligible": report_eligible,
                "warnings": warnings,
            }

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
    construction_weights = _s_curve_weights(months)
    monthly_financial = financial / max(1, months)
    for month in range(1, months + 1):
        weight = construction_weights[month - 1]
        schedule[month]["constructionCost"] = construction * weight
        schedule[month]["financialCost"] = monthly_financial
        schedule[month]["otherCost"] = other * weight
    return schedule, {
        "sourceMode": "benchmark_estimate",
        "approvalStatus": "unapproved",
        "sourceLocator": None,
        "reportEligible": False,
        "warnings": [
            "monthly_cost_schedule_missing",
            "construction_and_other_costs_use_deterministic_s_curve_estimate",
            "financial_cost_uses_even_monthly_estimate",
        ],
    }


def _explicit_cost_schedule(rows: List[Dict[str, Any]], months: int) -> List[Dict[str, float]]:
    schedule = [
        {"landCost": 0.0, "constructionCost": 0.0, "financialCost": 0.0, "otherCost": 0.0}
        for _ in range(months + 1)
    ]
    aliases = {
        "landCost": ("landCost", "land_cost"),
        "constructionCost": ("constructionCost", "directConstructionCost", "construction_cost"),
        "financialCost": ("financialCost", "financeCost", "financial_cost"),
        "otherCost": ("otherCost", "indirectCost", "other_cost"),
    }
    for fallback_index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        try:
            month_index = int(row.get("monthIndex", row.get("month_index", fallback_index)))
        except (TypeError, ValueError):
            continue
        if month_index < 0 or month_index > months:
            continue
        for target, source_keys in aliases.items():
            for source_key in source_keys:
                if source_key in row:
                    try:
                        schedule[month_index][target] += abs(float(row.get(source_key) or 0.0))
                    except (TypeError, ValueError):
                        pass
                    break
    return schedule


def _s_curve_weights(months: int) -> List[float]:
    if months <= 0:
        return []
    raw = []
    for month in range(1, months + 1):
        x = month / (months + 1)
        raw.append(max(0.0, x * (1.0 - x)))
    denominator = sum(raw) or 1.0
    return [value / denominator for value in raw]


def _row_source_locator(rows: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("sourceLocator"), dict) and row["sourceLocator"]:
            return row["sourceLocator"]
    return None


def _cost_schedule_reconciliation_warning(schedule: List[Dict[str, float]], costs: Dict[str, Any]) -> str | None:
    expected = {
        "landCost": float(costs.get("landCost") or 0.0),
        "constructionCost": float(costs.get("directConstructionCost") or 0.0),
        "financialCost": float(costs.get("financialCost") or 0.0),
        "otherCost": float(costs.get("otherCost") or 0.0),
    }
    actual = {key: sum(row.get(key, 0.0) for row in schedule) for key in expected}
    for key, expected_amount in expected.items():
        if expected_amount <= 0:
            continue
        if abs(actual[key] - expected_amount) > max(1.0, expected_amount * 0.001):
            return f"cost_schedule_total_mismatch:{key}"
    return None


def _pf_limit(request: Dict[str, Any]) -> float:
    return float(request.get("sourcesAndUses", {}).get("funding", {}).get("pfLoan") or 0.0)


def _tranche_limits(request: Dict[str, Any]) -> List[Dict[str, Any]]:
    tranches = []
    explicit_equity_only = bool(request.get("pfStructure"))
    for index, tranche in enumerate(request.get("pfStructure", []), start=1):
        if tranche.get("priority") == "equity" or tranche.get("name") == "equity":
            continue
        explicit_equity_only = False
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
    if explicit_equity_only:
        return []
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


def _collection_allocation_policy(request: Dict[str, Any]) -> Dict[str, Any]:
    raw = request.get("collectionAllocationRules") or request.get("collection_allocation_rules") or {}
    rules = raw.get("rules") if isinstance(raw, dict) else raw
    rules = [dict(item) for item in rules if isinstance(item, dict)] if isinstance(rules, list) else []
    approval_status = str((raw if isinstance(raw, dict) else {}).get("approvalStatus") or "unapproved")
    locator = (raw if isinstance(raw, dict) else {}).get("sourceLocator")
    if rules:
        source_mode = "approved_rule" if approval_status == "approved" else "evidence_rule" if locator else "explicit_estimate"
        return {
            "rules": rules,
            "sourceMode": source_mode,
            "approvalStatus": approval_status,
            "sourceLocator": locator,
            "reportEligible": source_mode == "approved_rule",
            "warnings": [] if source_mode == "approved_rule" else ["collection_allocation_rule_not_approved"],
        }
    return {
        "rules": [],
        "sourceMode": "benchmark_estimate",
        "approvalStatus": "unapproved",
        "sourceLocator": None,
        "reportEligible": False,
        "warnings": ["collection_allocation_rule_missing", "benchmark_75_25_rule_used_for_analysis_only"],
    }


def _sales_schedule_provenance(request: Dict[str, Any]) -> Dict[str, Any]:
    products = [row for row in request.get("productMix", []) if isinstance(row, dict)]
    explicit = [row for row in products if row.get("collectionSteps")]
    locators = [row.get("collectionSourceLocator") for row in explicit if isinstance(row.get("collectionSourceLocator"), dict)]
    approval_status = str((request.get("sourceMetadata") or {}).get("approvalStatus") or "unapproved")
    if explicit:
        source_mode = "approved_schedule" if approval_status == "approved" else "evidence_schedule" if locators else "explicit_estimate"
        return {
            "sourceMode": source_mode,
            "approvalStatus": approval_status,
            "sourceLocator": locators[0] if locators else None,
            "reportEligible": source_mode == "approved_schedule",
            "warnings": [] if source_mode == "approved_schedule" else ["sales_collection_schedule_not_approved"],
        }
    return {
        "sourceMode": "asset_template_estimate",
        "approvalStatus": "unapproved",
        "sourceLocator": None,
        "reportEligible": False,
        "warnings": ["sales_collection_schedule_missing", "asset_template_used_for_analysis_only"],
    }


def _allocate_sales_collection(
    sales_collection: Dict[str, float],
    month_index: int,
    completion_month: int,
    cumulative_sales_rate: float,
    policy: Dict[str, Any],
) -> Dict[str, float]:
    """Allocate sales proceeds into operating and repayment-reserve accounts.

    Baseline rule:
    - Before completion, contract/interim cash is split 75% operating / 25% repayment reserve.
    - Before completion, balance cash is swept 100% to repayment reserve.
    - At or after completion, all sales proceeds are swept to repayment reserve.
    """
    total = sum(sales_collection.values())
    rule = _matching_allocation_rule(policy.get("rules") or [], month_index, completion_month, cumulative_sales_rate)
    if rule:
        all_to_repayment = _rate(rule, "allSalesProceedsToRepayment", "all_sales_proceeds_to_repayment")
        if all_to_repayment is not None:
            repayment = total * all_to_repayment
            return {"operating": total - repayment, "repaymentReserve": repayment}
        contract_and_interim = float(sales_collection.get("contract") or 0.0) + float(sales_collection.get("interim") or 0.0)
        balance = float(sales_collection.get("balance") or 0.0)
        repayment_rate = _rate(rule, "contractAndInterimToRepayment", "contract_and_interim_to_repayment")
        operating_rate = _rate(rule, "contractAndInterimToOperating", "contract_and_interim_to_operating")
        if repayment_rate is None:
            repayment_rate = 1.0 - (operating_rate if operating_rate is not None else 0.0)
        balance_repayment_rate = _rate(rule, "balanceToRepayment", "balance_to_repayment")
        if balance_repayment_rate is None:
            balance_repayment_rate = 1.0
        repayment = contract_and_interim * repayment_rate + balance * balance_repayment_rate
        return {"operating": total - repayment, "repaymentReserve": repayment}

    if month_index >= completion_month:
        return {"operating": 0.0, "repaymentReserve": total}

    contract_and_interim = float(sales_collection.get("contract") or 0.0) + float(sales_collection.get("interim") or 0.0)
    balance = float(sales_collection.get("balance") or 0.0)
    operating = contract_and_interim * 0.75
    repayment = contract_and_interim * 0.25 + balance
    return {"operating": operating, "repaymentReserve": repayment}


def _matching_allocation_rule(
    rules: List[Dict[str, Any]],
    month_index: int,
    completion_month: int,
    cumulative_sales_rate: float,
) -> Dict[str, Any] | None:
    after_completion = month_index >= completion_month
    for rule in rules:
        phase = str(rule.get("phase") or rule.get("condition") or "").casefold()
        if "after_completion" in phase and not after_completion:
            continue
        if "before_completion" in phase and after_completion:
            continue
        minimum = _rate(rule, "minSalesRate", "min_sales_rate")
        maximum = _rate(rule, "maxSalesRate", "max_sales_rate")
        if minimum is not None and cumulative_sales_rate < minimum:
            continue
        if maximum is not None and cumulative_sales_rate > maximum:
            continue
        return rule
    return None


def _rate(payload: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key not in payload:
            continue
        try:
            value = float(payload[key])
        except (TypeError, ValueError):
            return None
        return min(1.0, max(0.0, value))
    return None


def _total_revenue(request: Dict[str, Any]) -> float:
    return float(request.get("sourcesAndUses", {}).get("revenue", {}).get("totalSalesRevenue") or 0.0)


def _total_project_cost(request: Dict[str, Any]) -> float:
    return float(request.get("sourcesAndUses", {}).get("cost", {}).get("totalProjectCost") or 0.0)


def _risk_flags(
    max_minus: float,
    pf_limit: float,
    total_pf_draw: float,
    remaining_pf_limit: float,
    schedule_provenance: Dict[str, Any],
) -> List[Dict[str, Any]]:
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
    for component, provenance in schedule_provenance.items():
        if component == "reportEligible" or not isinstance(provenance, dict):
            continue
        if provenance.get("reportEligible"):
            continue
        flags.append({
            "code": f"CASH_FLOW_{component.upper()}_ESTIMATED",
            "severity": "high" if component == "costSchedule" else "medium",
            "message": f"{component} is not backed by an approved schedule and is not report-eligible.",
            "sourceLocator": provenance.get("sourceLocator"),
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


def _report_fragments(
    summary: Dict[str, Any],
    risk_flags: List[Dict[str, Any]],
    schedule_provenance: Dict[str, Any],
) -> Dict[str, Any]:
    risk_text = "No critical cash-flow risk flag was generated." if not risk_flags else "; ".join(flag["code"] for flag in risk_flags)
    return {
        "reportEligible": bool(schedule_provenance.get("reportEligible")),
        "usageRestriction": None if schedule_provenance.get("reportEligible") else "review_required_not_for_approval_or_final_report",
        "cashFlowSummary": (
            f"{'Approved' if schedule_provenance.get('reportEligible') else 'Preliminary'} cash-flow analysis estimates Max Minus at {summary['maxMinus']:,.0f} KRW "
            f"and required PF limit at {summary['requiredPfLimit']:,.0f} KRW."
        ),
        "pfDrawSummary": (
            f"Total PF draw is estimated at {summary['totalPfDraw']:,.0f} KRW, "
            f"with undrawn limit of {summary['undrawnPfLimit']:,.0f} KRW."
        ),
        "riskOpinion": risk_text,
    }
