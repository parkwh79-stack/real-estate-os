"""Deterministic rental operations, refinance capacity and exit settlement."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from core.calculations import calculate_xirr
from core.financial_input_validation import FinancialInputError


MAX_AMOUNT = 10**12
MIN_RATE = Decimal("0.000000000001")
MAX_SAFE_INTEGER = (1 << 53) - 1


def _iso_date(value: Any) -> date:
    if isinstance(value, str):
        parsed = date.fromisoformat(value)
        if value == parsed.isoformat():
            return parsed
    raise ValueError("Use YYYY-MM-DD.")


def _decimal(value: Any) -> Decimal:
    if type(value) not in (float, int, Decimal):
        raise ValueError("A number is required.")
    return Decimal(str(value))


Day = Annotated[date, BeforeValidator(_iso_date)]
Won = Annotated[int, Field(strict=True, ge=0, le=MAX_AMOUNT)]
Ratio = Annotated[Decimal, BeforeValidator(_decimal), Field(ge=0, le=1)]
PositiveRatio = Annotated[Decimal, BeforeValidator(_decimal), Field(gt=0, le=1)]
Coverage = Annotated[Decimal, BeforeValidator(_decimal), Field(gt=0, le=100)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sourceLocator: dict[str, Any] | None = None


class _Period(_Input):
    date: Day
    potentialRent: Won
    occupancyRate: Ratio
    otherOperatingIncome: Won
    operatingExpense: Won
    capitalExpense: Won
    depositReceived: Won
    depositRefunded: Won
    debtService: Won
    refinancePrincipalRepayment: Won = 0


class _Maturity(_Input):
    date: Day
    principal: Won
    interest: Won
    fees: Won


class _Ltv(_Input):
    valuation: Won
    limit: PositiveRatio


class _Dscr(_Input):
    annualCfads: Won
    minimum: Coverage
    annualInterestRate: Ratio
    amortizationMonths: Annotated[int, Field(strict=True, ge=12, le=600)] | None


class _DebtYield(_Input):
    annualNoi: Won
    minimum: PositiveRatio


class _Refinance(_Input):
    fundingDate: Day
    requestedPrincipal: Annotated[int, Field(strict=True, gt=0, le=MAX_AMOUNT)]
    fees: Won
    reserve: Won
    ltv: _Ltv | None
    dscr: _Dscr | None
    debtYield: _DebtYield | None
    commitmentLimit: Won | None


class _Exit(_Input):
    date: Day
    grossSalePrice: Won
    transactionCosts: Won
    taxes: Won
    depositTreatment: Literal["refund", "transfer"]
    refinancePayoffInterest: Won
    refinancePayoffFees: Won
    refinanceReserveReleased: Won = 0
    overdueDebtCharges: Won | None = None


class _Investor(_Input):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    investedAt: Day
    capital: Annotated[int, Field(strict=True, gt=0, le=MAX_AMOUNT)]
    principalPriority: Annotated[int, Field(strict=True, ge=1, le=100)]
    remainderBps: Annotated[int, Field(strict=True, ge=0, le=10_000)]


class _Request(_Input):
    currency: Literal["KRW"]
    asOfDate: Day
    openingUnrestrictedCash: Won
    openingDepositBalance: Won
    periods: Annotated[list[_Period], Field(min_length=1, max_length=600)]
    maturity: _Maturity
    refinance: _Refinance | None
    exit: _Exit
    investors: Annotated[list[_Investor], Field(min_length=1, max_length=100)]


def _error(message: str, code: str = "invalid_rental_exit_input") -> None:
    raise FinancialInputError(message, code=code)


def _validate(spec: dict) -> _Request:
    try:
        request = _Request.model_validate(spec)
    except ValidationError as exc:
        issue = exc.errors(include_input=False, include_context=False, include_url=False)[0]
        path = ".".join(map(str, issue["loc"]))
        _error(f"임대·회수 입력의 날짜·금액·필수 항목을 확인하세요 ({path or '입력'}).")

    maturity, exit_terms = request.maturity, request.exit
    if not request.asOfDate <= maturity.date <= exit_terms.date:
        _error("기준일, 만기일, 매각일의 순서를 확인하세요.")
    dates = [period.date for period in request.periods]
    if len(dates) != len(set(dates)) or any(
        not request.asOfDate < date <= exit_terms.date for date in dates
    ):
        _error("운영 기간의 기준일 이후·매각일 이내 날짜를 중복 없이 입력하세요.")
    refinance = request.refinance
    if refinance and not maturity.date <= refinance.fundingDate < exit_terms.date:
        _error("차환일은 기존 만기일부터 매각일 전까지로 입력하세요. 만기 당일 매각은 별도 시나리오로 계산하세요.")
    if any(row.investedAt > request.asOfDate for row in request.investors):
        _error("이 모형의 출자원금은 기준일 현재 이미 납입한 원금입니다.")
    if len({row.id for row in request.investors}) != len(request.investors):
        _error("투자자 식별자가 중복됩니다.")
    if sum(row.remainderBps for row in request.investors) != 10_000:
        _error("잔여이익 배분율 합계는 10,000bp여야 합니다.")
    return request


def _annual_service_per_won(terms: _Dscr) -> Decimal:
    if terms.amortizationMonths is None:
        if terms.annualInterestRate == 0:
            _error("무이자 이자 지급형 대출의 DSCR 한도는 산정할 수 없습니다.")
        return terms.annualInterestRate
    if terms.annualInterestRate == 0:
        return Decimal(12) / terms.amortizationMonths
    if terms.annualInterestRate < MIN_RATE:
        _error("원리금 균등상환 차환 금리가 지원하는 계산 정밀도보다 작습니다.")
    monthly_rate = terms.annualInterestRate / 12
    return 12 * monthly_rate / (1 - (1 + monthly_rate) ** -terms.amortizationMonths)


def _whole_won_limit(value: Decimal) -> int:
    if value > MAX_SAFE_INTEGER:
        _error("차환 한도가 지원 금액 범위를 초과합니다. 금액·비율의 단위를 확인하세요.")
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _refinance_capacity(terms: _Refinance) -> dict:
    caps = {"ltv": None, "dscr": None, "debtYield": None, "commitment": terms.commitmentLimit}
    if terms.ltv:
        caps["ltv"] = _whole_won_limit(terms.ltv.valuation * terms.ltv.limit)
    if terms.dscr:
        service = _annual_service_per_won(terms.dscr)
        caps["dscr"] = _whole_won_limit(_bounded_division(terms.dscr.annualCfads, terms.dscr.minimum * service))
    if terms.debtYield:
        caps["debtYield"] = _whole_won_limit(_bounded_division(terms.debtYield.annualNoi, terms.debtYield.minimum))
    applicable = {key: value for key, value in caps.items() if value is not None}
    if not applicable:
        _error("차환에 적용하는 한도 조건을 하나 이상 입력하세요.")
    maximum = min(applicable.values())
    return {
        "date": terms.fundingDate.isoformat(),
        "requestedPrincipal": terms.requestedPrincipal,
        "caps": caps,
        "sizingLimit": maximum,
        "bindingCaps": [key for key, value in applicable.items() if value == maximum],
        "requestedNetProceeds": terms.requestedPrincipal - terms.fees - terms.reserve,
        "drawnPrincipal": 0,
        "netProceeds": 0,
        "sourceLocator": terms.sourceLocator,
    }


def _bounded_division(numerator: int, denominator: Decimal) -> Decimal:
    if denominator == 0 or numerator > MAX_SAFE_INTEGER * denominator:
        _error("차환 한도가 지원 금액 범위를 초과합니다. 금액·비율의 단위를 확인하세요.")
    return numerator / denominator


def _allocate(amount: int, weights: dict[str, int]) -> dict[str, int]:
    """Allocate whole won with no residual. Equal remainders use investor ID order."""
    denominator = sum(weights.values())
    if not denominator:
        return {key: 0 for key in weights}
    quotas = {key: divmod(amount * weight, denominator) for key, weight in weights.items()}
    result = {key: whole for key, (whole, _) in quotas.items()}
    leftover = amount - sum(result.values())
    order = sorted(quotas, key=lambda key: (-quotas[key][1], key))
    for key in order[:leftover]:
        result[key] += 1
    return result


def _investor_recovery(investors: list[_Investor], distributable: int, exit_date: date) -> list[dict]:
    capital_paid = {}
    remaining = distributable
    for priority in sorted({row.principalPriority for row in investors}):
        weights = {row.id: row.capital for row in investors if row.principalPriority == priority}
        principal = min(remaining, sum(weights.values()))
        capital_paid.update(_allocate(principal, weights))
        remaining -= principal
    profit = _allocate(remaining, {row.id: row.remainderBps for row in investors})
    results = []
    for row in investors:
        total = capital_paid[row.id] + profit[row.id]
        cash_flows = [{"date": row.investedAt.isoformat(), "amount": -row.capital},
                      {"date": exit_date.isoformat(), "amount": total}]
        xirr = None
        if total and row.investedAt < exit_date:
            try:
                xirr = calculate_xirr(cash_flows)
            except (OverflowError, ValueError):
                pass
        results.append({
            "id": row.id, "investedCapital": row.capital, "principalPriority": row.principalPriority,
            "remainderBps": row.remainderBps, "returnedCapital": capital_paid[row.id],
            "profitDistribution": profit[row.id], "totalDistribution": total,
            "unpaidCapital": row.capital - capital_paid[row.id], "moic": total / row.capital,
            "xirr": xirr, "cashFlows": cash_flows, "sourceLocator": row.sourceLocator,
        })
    return results


def _operating_flow(period: _Period) -> dict:
    rent = int((period.potentialRent * period.occupancyRate).to_integral_value(rounding=ROUND_HALF_UP))
    noi = rent + period.otherOperatingIncome - period.operatingExpense
    before_debt = noi - period.capitalExpense
    return {
        "date": period.date.isoformat(), "kind": "operations", "effectiveRent": rent, "noi": noi,
        "cashBeforeDebt": before_debt, "scheduledDebtService": period.debtService,
        "scheduledRefinancePrincipalRepayment": period.refinancePrincipalRepayment,
        "depositReceived": period.depositReceived, "depositRefunded": period.depositRefunded,
        "sourceLocator": period.sourceLocator,
    }


def run_rental_exit(spec: dict) -> dict:
    """Run a bounded, analysis-only projection with explicit loan and sale terms.

    Operations dated on a settlement date are processed first. The unrestricted
    projection can be negative to expose outstanding funding needs. At maturity
    and exit, payments cannot exceed available unrestricted cash. A refinance
    that cannot close produces zero liquidity. Later cash is tested at exit
    against the remaining original debt. The original maturity shortfall stays
    visible even when the remaining debt is covered at exit.
    """
    request = _validate(spec)
    maturity, exit_terms = request.maturity, request.exit
    refinance = _refinance_capacity(request.refinance) if request.refinance else None
    operations = {period.date: _operating_flow(period) for period in request.periods}
    timeline = sorted({*operations, maturity.date, exit_terms.date})
    cash, deposits = request.openingUnrestrictedCash, request.openingDepositBalance
    debt_due = maturity.principal + maturity.interest + maturity.fees
    old_debt_left, new_debt, refinance_reserve = debt_due, 0, 0
    unpaid_scheduled_refinance_principal = 0
    peak_operating_shortfall, total_noi = 0, 0
    first_operating_shortfall_date = None
    ledger = []
    maturity_result, sale = {}, {}

    def record(event: dict, *, cash_delta: int, deposit_delta: int = 0,
               reserve_delta: int = 0) -> None:
        nonlocal cash, deposits, refinance_reserve
        previous_cash, previous_deposits, previous_reserve = cash, deposits, refinance_reserve
        cash += cash_delta
        deposits += deposit_delta
        refinance_reserve += reserve_delta
        ledger.append({**event, "beginningProjectedCash": previous_cash,
                       "netUnrestrictedFlow": cash_delta,
                       "endingProjectedCash": cash, "endingAvailableCash": max(0, cash),
                       "unfundedProjectedCash": max(0, -cash),
                       "beginningRestrictedDeposits": previous_deposits,
                       "netRestrictedDepositFlow": deposit_delta, "endingRestrictedDeposits": deposits,
                       "beginningRefinanceReserve": previous_reserve,
                       "netRefinanceReserveFlow": reserve_delta, "endingRefinanceReserve": refinance_reserve,
                       })

    for day in timeline:
        flow = operations.get(day)
        if flow:
            if flow["depositRefunded"] > deposits + flow["depositReceived"]:
                _error("보증금 반환액이 보유한 제한계정 잔액을 초과합니다.")
            scheduled_principal = flow["scheduledRefinancePrincipalRepayment"]
            scheduled_service = flow["scheduledDebtService"]
            if scheduled_principal and day <= maturity.date:
                _error("차환 원금 상환일은 기존 만기 정산일 이후여야 합니다.")
            refinance_pending_or_failed = day > maturity.date and not (refinance and refinance["drawnPrincipal"])
            service = scheduled_service if not refinance_pending_or_failed else 0
            principal_due = scheduled_principal if not refinance_pending_or_failed else 0
            if principal_due > new_debt:
                _error("차환 원금 상환액이 해당 시점의 차환 원금 잔액을 초과합니다.")
            before_principal = flow["cashBeforeDebt"] - service
            principal_paid = min(principal_due, max(0, cash + before_principal))
            unpaid_principal = principal_due - principal_paid
            unpaid_scheduled_refinance_principal += unpaid_principal
            new_debt -= principal_paid
            total_noi += flow["noi"]
            flow.update(debtService=service, refinancePrincipalRepayment=principal_paid,
                        unpaidRefinancePrincipalRepayment=unpaid_principal,
                        excludedUnfundedRefinanceService=scheduled_service - service,
                        excludedUnfundedRefinancePrincipal=scheduled_principal - principal_due)
            record(flow, cash_delta=before_principal - principal_paid,
                   deposit_delta=flow["depositReceived"] - flow["depositRefunded"])
            if (cash < 0 or unpaid_principal) and first_operating_shortfall_date is None:
                first_operating_shortfall_date = day.isoformat()
            peak_operating_shortfall = max(peak_operating_shortfall, -cash, unpaid_principal)

        if day == exit_terms.date:
            # Gross sale price is before tenant settlement. Either refund the
            # tenant or credit the buyer once, releasing the associated reserve.
            requested_release = exit_terms.refinanceReserveReleased
            release = requested_release if refinance and refinance["drawnPrincipal"] else 0
            if release > refinance_reserve:
                _error("매각 시 해제할 차환 유보금이 제한계정의 잔액을 초과합니다.")
            net_sale = exit_terms.grossSalePrice - exit_terms.transactionCosts - exit_terms.taxes - deposits
            sale = {
                "date": day.isoformat(), "kind": "sale", "grossSalePrice": exit_terms.grossSalePrice,
                "transactionCosts": exit_terms.transactionCosts, "taxes": exit_terms.taxes,
                "depositTreatment": exit_terms.depositTreatment,
                "depositSettlement": deposits, "netSaleCash": net_sale,
                "restrictedDepositRelease": deposits, "refinanceReserveRelease": release,
                "excludedUnfundedRefinanceReserveRelease": requested_release - release,
                "sourceLocator": exit_terms.sourceLocator,
            }
            record(sale, cash_delta=net_sale + deposits + release, deposit_delta=-deposits,
                   reserve_delta=-release)

        if day == maturity.date:
            before_refinance = cash
            if request.refinance and refinance is not None:
                terms = request.refinance
                if terms.fundingDate != day:
                    status = "funding_after_maturity"
                elif terms.requestedPrincipal > refinance["sizingLimit"]:
                    status = "limit_exceeded"
                elif cash + refinance["requestedNetProceeds"] < debt_due:
                    status = "insufficient_closing_funds"
                else:
                    status = "funded"
                    new_debt = terms.requestedPrincipal
                    refinance["drawnPrincipal"] = new_debt
                    refinance["netProceeds"] = refinance["requestedNetProceeds"]
                    record({"date": day.isoformat(), "kind": "refinance",
                            "drawnPrincipal": new_debt, "fees": terms.fees,
                            "sourceLocator": terms.sourceLocator},
                           cash_delta=refinance["netProceeds"], reserve_delta=terms.reserve)
                refinance["status"] = status
                refinance["closingShortfallAtRequestedAmount"] = (
                    max(0, debt_due - before_refinance - refinance["requestedNetProceeds"])
                    if terms.fundingDate == day and terms.requestedPrincipal <= refinance["sizingLimit"] else None
                )
            available = max(0, cash)
            paid = min(debt_due, available)
            old_debt_left -= paid
            maturity_result = {
                "date": day.isoformat(), "kind": "maturity", "debtDue": debt_due, "cashBeforeRefinance": before_refinance,
                "availableBeforeSettlement": available, "debtPaid": paid, "shortfall": old_debt_left,
                "sourceLocator": maturity.sourceLocator,
            }
            record(maturity_result, cash_delta=-paid)

    late_charges = exit_terms.overdueDebtCharges
    needs_late_charges = bool(old_debt_left and maturity.date < exit_terms.date and late_charges is None)
    if late_charges and (not old_debt_left or maturity.date == exit_terms.date):
        _error("만기 미상환 원금이 매각일까지 남은 경우에만 연체 정산 비용을 적용하세요.")
    quoted_refinance_charges = exit_terms.refinancePayoffInterest + exit_terms.refinancePayoffFees
    refinance_charges = quoted_refinance_charges if refinance and refinance["drawnPrincipal"] else 0
    payoff_due = None if needs_late_charges else old_debt_left + (late_charges or 0) + new_debt + refinance_charges
    payoff_paid = min(max(0, cash), payoff_due) if payoff_due is not None else None
    settlement = {
        "date": exit_terms.date.isoformat(), "kind": "exit_settlement",
        "status": "needs_overdue_debt_charges" if needs_late_charges else "calculated",
        "debtDue": payoff_due, "debtPaid": payoff_paid,
        "refinancePrincipalDue": new_debt, "previousDebtDue": old_debt_left,
        "overdueDebtCharges": late_charges, "refinancePayoffCharges": refinance_charges,
        "excludedUnfundedRefinancePayoffCharges": quoted_refinance_charges - refinance_charges,
    }
    unpaid_debt, exit_cash_shortfall, distributable, distributed, unpaid_investor_capital = (None,) * 5
    recovery = []
    if payoff_paid is not None:
        record(settlement, cash_delta=-payoff_paid)
        unpaid_debt = payoff_due - payoff_paid
        exit_cash_shortfall = max(0, -cash) + unpaid_debt
        distributable = max(0, cash)
        recovery = _investor_recovery(request.investors, distributable, exit_terms.date)
        distributed = sum(row["totalDistribution"] for row in recovery)
        unpaid_investor_capital = sum(row["unpaidCapital"] for row in recovery)
        record({"date": exit_terms.date.isoformat(), "kind": "investor_distribution",
                "amount": distributed}, cash_delta=-distributed)
    if needs_late_charges:
        decision = "exit_terms_required"
    elif maturity_result["shortfall"] or exit_cash_shortfall or peak_operating_shortfall:
        decision = "shortfall"
    elif refinance_reserve:
        decision = "restricted_reserve_remaining"
    else:
        decision = "settled"
    return {
        "analysisType": "realytics_rental_exit_v1", "engineVersion": "rental_exit-v1",
        "status": "partial" if needs_late_charges else "completed",
        "analysisOnly": True, "reportEligible": False, "currency": "KRW",
        "summary": {
            "decision": decision,
            "totalNoi": total_noi, "peakOperatingShortfall": peak_operating_shortfall,
            "firstOperatingShortfallDate": first_operating_shortfall_date,
            "maturityShortfall": maturity_result["shortfall"], "netSaleCash": sale["netSaleCash"],
            "exitDebtShortfall": unpaid_debt, "exitCashShortfall": exit_cash_shortfall,
            "scheduledRefinancePrincipalUnderpayment": unpaid_scheduled_refinance_principal,
            "remainingRefinanceReserve": refinance_reserve,
            "distributableCash": distributable, "distributedCash": distributed,
            "unpaidInvestorCapital": unpaid_investor_capital,
            "distributionReconciliationDifference": distributable - distributed if distributed is not None else None,
        },
        "maturity": maturity_result, "refinance": refinance, "sale": sale, "exitSettlement": settlement,
        "ledger": ledger, "investorRecovery": recovery,
        "sourceLocator": request.sourceLocator,
    }
