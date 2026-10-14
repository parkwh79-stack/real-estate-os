"""Explicit, analysis-only monthly debt policy; never inferred from loan terms."""
from __future__ import annotations

import math
from typing import Any

from core.financial_input_validation import FinancialInputError

MONTHLY_FINANCING_POLICY = {
    "method": "reviewed_monthly_v1",
    "interestBasis": "opening_balance",
    "annualDayBasis": "monthly_12",
    "feeBasis": "committed_limit_first_draw",
    "maturityBasis": "project_month_zero",
    "repaymentSource": "repayment_reserve",
    "costTreatment": "replace_budget_finance_cost",
    "overdueInterestBasis": "contract_rate_simple",
    "revolving": "non_revolving",
    "rounding": "unrounded_analysis",
}

_UNSUPPORTED_MONTHLY_FINANCING_OPTIONS = frozenset({
    "repaymentSchedule", "repayment_schedule", "amortization",
    "interestCapitalization", "interest_capitalization", "penaltyRate", "penalty_rate",
    "drawSchedule", "draw_schedule", "variableRate", "variable_rate",
    "interestRateSchedule", "interest_rate_schedule", "rateResetSchedule", "rate_reset_schedule",
})


def _error(message: str, code: str = "invalid_monthly_financing"):
    raise FinancialInputError(message, code=code)


def validate_monthly_financing_policy(value: Any) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value != MONTHLY_FINANCING_POLICY:
        _error("월별 금융 계산은 검토한 monthly v1 정책의 모든 항목을 명시해야 합니다. 다른 금융 옵션은 지원하지 않습니다.",
               "unsupported_monthly_financing_policy")
    return dict(value)


def reject_unsupported_monthly_financing_options(value: Any) -> None:
    """Check original rows before a mapper can discard unsupported loan terms."""
    rows = value if isinstance(value, list) else [value]
    for row in rows:
        if isinstance(row, dict) and not _UNSUPPORTED_MONTHLY_FINANCING_OPTIONS.isdisjoint(row):
            identity = row.get("tranche_id") or row.get("trancheId") or row.get("tranche") or row.get("name") or "대출 조건"
            _error(
                f"{identity}: 개별 인출·분할 상환·이자 자본화·연체가산·변동금리 조건은 지원하지 않습니다.",
                "unsupported_monthly_financing_option",
            )


def _number(value: Any, field: str, *, minimum=0, maximum=9_007_199_254_740_991, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or value < minimum or value > maximum or not math.isfinite(value)
            or (integer and value != int(value))):
        _error(f"{field}: 명시적인 유효한 숫자가 필요합니다. 미입력과 0을 구분하세요.")
    return float(value)


class ReviewedMonthlyFinancing:
    """Non-revolving commitment, monthly opening-balance interest and bullet repayment.

    Interest/fees are operating uses and may consume available debt capacity.
    Only repayment-reserve cash repays principal. No default terms, equity injection,
    penalty interest, cash sweep before maturity or interest capitalization is inferred.
    """

    def __init__(self, request: dict):
        self.policy = validate_monthly_financing_policy(request.get("monthlyFinancing"))
        rows = request.get("pfStructure")
        if not isinstance(rows, list) or not rows:
            _error("월별 금융 계산에 검토한 대출 조건이 필요합니다.")
        self.tranches = []
        seen = set()
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                _error("대출 조건 행을 확인하세요.")
            reject_unsupported_monthly_financing_options(row)
            identity = row.get("trancheId") or row.get("tranche") or row.get("name")
            if not isinstance(identity, str) or not identity.strip() or identity in seen:
                _error("대출 행 식별자는 비어 있거나 중복될 수 없습니다.")
            seen.add(identity)
            amount = _number(row.get("amount"), f"{identity} 대출금액", integer=True)
            interest = _number(row.get("interestRate"), f"{identity} 연 금리", maximum=1)
            fee = _number(row.get("feeRate"), f"{identity} 수수료율", maximum=1)
            maturity = int(_number(row.get("maturityMonths"), f"{identity} M0 기준 만기 개월", minimum=1, maximum=600, integer=True))
            source = row.get("repaymentSource")
            if not isinstance(source, str) or source not in {"repayment_reserve", "분양대금", "분양대금 상환계정", "sales_proceeds"}:
                _error(f"{identity}: 상환 재원은 명시적인 repayment_reserve만 지원합니다.", "unsupported_monthly_repayment_source")
            # Explicit canonical debt class is required; equity is never debt capacity.
            priority = row.get("priority") or row.get("name")
            if not isinstance(priority, str) or priority not in {"senior", "mezzanine", "junior", "bridge", "other"}:
                _error(f"{identity}: 지원하는 대출 분류를 명시하세요.")
            draw_priority = _number(row.get("drawPriority", index), f"{identity} 인출 순위", minimum=1, maximum=100, integer=True)
            repayment_priority = _number(row.get("repaymentPriority", index), f"{identity} 상환 순위", minimum=1, maximum=100, integer=True)
            self.tranches.append({
                "trancheId": identity, "name": row.get("tranche") or identity,
                "limit": amount, "remaining": amount, "outstanding": 0.0,
                "interestRate": interest, "feeRate": fee, "maturityMonths": maturity,
                "repaymentSource": source,
                "drawPriority": draw_priority, "repaymentPriority": repayment_priority,
                "hasDrawn": False, "totalDraw": 0.0, "totalInterest": 0.0,
                "totalFee": 0.0, "totalPrincipalRepaid": 0.0,
            })
        self.tranches.sort(key=lambda item: item["drawPriority"])

    def month(self, month_index: int, *, non_finance_cost: float, operating_cash: float, repayment_reserve: float) -> dict:
        snapshots = {
            item["trancheId"]: {
                "trancheId": item["trancheId"], "openingBalance": item["outstanding"],
                "draw": 0.0, "interest": item["outstanding"] * item["interestRate"] / 12,
                "fee": 0.0, "principalRepayment": 0.0, "maturityMonth": item["maturityMonths"],
            } for item in self.tranches
        }
        interest_total = sum(row["interest"] for row in snapshots.values())
        required = max(0.0, non_finance_cost + interest_total - operating_cash)
        remaining_need = required
        for item in self.tranches:
            if remaining_need <= 0 or month_index >= item["maturityMonths"]:
                continue
            first_fee = 0.0 if item["hasDrawn"] else item["limit"] * item["feeRate"]
            draw = min(item["remaining"], remaining_need + first_fee)
            if draw <= first_fee:
                continue
            row = snapshots[item["trancheId"]]
            row["draw"], row["fee"] = draw, first_fee
            item["hasDrawn"] = True
            item["remaining"] -= draw
            item["outstanding"] += draw
            remaining_need = max(0.0, remaining_need - draw + first_fee)
        reserve = max(0.0, repayment_reserve)
        for item in sorted(self.tranches, key=lambda tranche: tranche["repaymentPriority"]):
            row = snapshots[item["trancheId"]]
            if month_index >= item["maturityMonths"]:
                principal = min(reserve, item["outstanding"])
                reserve -= principal
                item["outstanding"] -= principal
                row["principalRepayment"] = principal
            row["closingBalance"] = item["outstanding"]
            row["undrawnCommitment"] = item["remaining"]
            row["overduePrincipal"] = item["outstanding"] if month_index >= item["maturityMonths"] else 0.0
            row["balanceReconciliationDifference"] = row["openingBalance"] + row["draw"] - row["principalRepayment"] - row["closingBalance"]
            for target, key in (("totalDraw", "draw"), ("totalInterest", "interest"),
                                ("totalFee", "fee"), ("totalPrincipalRepaid", "principalRepayment")):
                item[target] += row[key]
        fees = sum(row["fee"] for row in snapshots.values())
        return {
            "debtBalances": list(snapshots.values()),
            "drawByTranche": {key: row["draw"] for key, row in snapshots.items() if row["draw"] > 0},
            "interest": interest_total, "fees": fees, "financialCost": interest_total + fees,
            "principalRepayment": sum(row["principalRepayment"] for row in snapshots.values()),
            "outstandingDebt": sum(item["outstanding"] for item in self.tranches),
            "overdueDebt": sum(row["overduePrincipal"] for row in snapshots.values()),
            "availableDrawCapacity": sum(item["remaining"] for item in self.tranches if month_index < item["maturityMonths"]),
            "expiredUndrawnCommitment": sum(item["remaining"] for item in self.tranches if month_index >= item["maturityMonths"]),
            "unfundedOperatingCost": remaining_need,
            "requiredDraw": required + fees,
        }

    def summary(self, *, budget_financial_cost: float, budget_project_cost: float) -> dict:
        interest = sum(item["totalInterest"] for item in self.tranches)
        fees = sum(item["totalFee"] for item in self.tranches)
        drawn = sum(item["totalDraw"] for item in self.tranches)
        repaid = sum(item["totalPrincipalRepaid"] for item in self.tranches)
        outstanding = sum(item["outstanding"] for item in self.tranches)
        return {
            "policy": self.policy, "analysisOnly": True, "reportEligible": False,
            "budgetFinancialCost": budget_financial_cost,
            "budgetTotalProjectCost": budget_project_cost,
            "calculatedInterest": interest, "calculatedFees": fees,
            "calculatedFinancialCost": interest + fees,
            "financialCostDifference": interest + fees - budget_financial_cost,
            "calculatedTotalProjectCost": budget_project_cost - budget_financial_cost + interest + fees,
            "totalDraw": drawn, "totalPrincipalRepaid": repaid, "outstandingDebt": outstanding,
            "debtReconciliationDifference": drawn - repaid - outstanding,
            "tranches": [{key: value for key, value in item.items() if key != "hasDrawn"} for item in self.tranches],
            "limitations": ["analysis_only_reviewed_policy", "no_penalty_interest", "no_interest_capitalization",
                            "no_pre_maturity_cash_sweep", "non_revolving_commitment", "equity_injection_not_modelled"],
        }
