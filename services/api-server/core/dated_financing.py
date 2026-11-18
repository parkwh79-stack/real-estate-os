"""Dated, non-revolving debt and repayment-account analysis."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any

from core.financial_input_validation import FinancialInputError

ZERO = Fraction(0)
DAY_COUNTS = {"ACT/365F": 365, "ACT/360": 360}
MAX_AMOUNT_KRW = 100_000_000_000_000
MAX_TRANCHE_COUNT = 30
MAX_EVENT_COUNT = 2_000
MAX_MODEL_DAYS = 18_263


def _invalid(path: str, message: str) -> None:
    raise FinancialInputError(f"{path}: {message}", code="invalid_dated_financing")


def _record(value: Any, required: set[str], optional: set[str], path: str) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        _invalid(path, "필수 입력 및 지원 항목을 확인해 주세요.")
    return value


def _rows(value: Any, path: str, limit: int = MAX_EVENT_COUNT) -> list:
    if not isinstance(value, list) or len(value) > limit:
        _invalid(path, f"최대 {limit}개까지 입력할 수 있습니다.")
    return value


def _day(value: Any, path: str) -> date:
    try:
        if not isinstance(value, str):
            raise ValueError
        parsed = date.fromisoformat(value)
        if value != parsed.isoformat():
            raise ValueError
        return parsed
    except ValueError as exc:
        raise FinancialInputError(f"{path}: YYYY-MM-DD 날짜를 입력해 주세요.", code="invalid_dated_financing") from exc


def _krw(value: Any, path: str, minimum: int = 0) -> Fraction:
    if type(value) is not int or not minimum <= value <= MAX_AMOUNT_KRW:
        _invalid(path, "허용 범위 안의 원 단위 정수 금액을 입력해 주세요.")
    return Fraction(value)


def _rate(value: Any, path: str) -> Fraction:
    if type(value) not in (int, float) or not 0 <= value <= 1:
        _invalid(path, "연 금리는 0 이상 1 이하의 비율을 입력해 주세요.")
    return Fraction(str(value))


def _locator(record: dict, path: str) -> dict:
    locator = record.get("sourceLocator")
    if locator is None:
        return {"sourceType": "analysis_input", "inputPath": path}
    if not isinstance(locator, dict) or not locator or len(locator) > 12 or any(
        not isinstance(key, str) or len(key) > 60
        or type(value) not in (str, int)
        or (isinstance(value, str) and len(value) > 500)
        or (type(value) is int and abs(value) > MAX_AMOUNT_KRW)
        for key, value in locator.items()
    ):
        _invalid(path, "원본 위치는 짧은 텍스트 또는 정수 항목으로 입력해 주세요.")
    return {"sourceType": "analysis_input", "inputPath": path, "suppliedLocator": dict(locator)}


def _dated_rows(rows: Any, path: str, fields: set[str]) -> dict[date, dict]:
    result = {}
    for index, raw in enumerate(_rows(rows, path)):
        row_path = f"{path}[{index}]"
        row = _record(raw, {"date", *fields}, {"sourceLocator"}, row_path)
        day = _day(row["date"], row_path)
        if day in result:
            _invalid(row_path, "같은 날짜의 동일 거래 유형은 한 행으로 입력해 주세요.")
        result[day] = {**row, "sourceLocator": _locator(row, row_path)}
    return result


@dataclass
class _Tranche:
    identity: str
    priority: int
    convention: str
    start: date
    end: date
    maturity: date
    commitment: Fraction
    rates: dict[date, dict]
    draws: dict[date, dict]
    principal_due: dict[date, dict]
    interest_dates: dict[date, dict]
    fees: dict[date, dict]
    source_locator: dict
    drawn: Fraction = ZERO
    principal: Fraction = ZERO
    principal_arrears: Fraction = ZERO
    accrued_interest: Fraction = ZERO
    interest_arrears: Fraction = ZERO
    fee_arrears: Fraction = ZERO
    total_interest: Fraction = ZERO
    total_fees: Fraction = ZERO
    annual_rate: Fraction | None = None
    previous: date | None = None

    def accrue(self, when: date) -> Fraction:
        start = self.previous or self.start
        days = max(0, (min(when, self.maturity) - start).days)
        interest = ZERO
        if days and self.annual_rate is not None:
            interest = self.principal * self.annual_rate * days / DAY_COUNTS[self.convention]
        self.accrued_interest += interest
        self.total_interest += interest
        self.previous = max(when, start)
        return interest


def _tranche(raw: Any, path: str) -> _Tranche:
    row = _record(raw, {
        "trancheId", "commitment", "drawAvailableFrom", "drawAvailableThrough", "maturityDate",
        "dayCount", "repaymentPriority", "rateSchedule", "draws", "scheduledPrincipal",
        "interestPaymentDates", "fees",
    }, {"sourceLocator"}, path)
    identity, priority, convention = row["trancheId"], row["repaymentPriority"], row["dayCount"]
    if not isinstance(identity, str) or not identity.strip() or len(identity) > 80:
        _invalid(path, "대출 식별자는 비어 있을 수 없고 80자 이하여야 합니다.")
    if type(priority) is not int or not 1 <= priority <= 100:
        _invalid(path, "상환 우선순위는 1부터 100까지의 정수여야 합니다.")
    if not isinstance(convention, str) or convention not in DAY_COUNTS:
        _invalid(path, "이자 일수 기준은 ACT/365F 또는 ACT/360 중 지정해 주세요.")
    start = _day(row["drawAvailableFrom"], f"{path}.drawAvailableFrom")
    end = _day(row["drawAvailableThrough"], f"{path}.drawAvailableThrough")
    maturity = _day(row["maturityDate"], f"{path}.maturityDate")
    if not start <= end < maturity:
        _invalid(path, "인출 시작일과 종료일 사이의 기간은 만기일 전이어야 합니다.")
    rates = _dated_rows(row["rateSchedule"], f"{path}.rateSchedule", {"annualRate"})
    if not rates or min(rates) != start or max(rates) >= maturity:
        _invalid(path, "최초 금리를 인출 시작일에 지정하고 이후 변경일은 만기일 전에 두세요.")
    for rate in rates.values():
        rate["annualRate"] = _rate(rate["annualRate"], f"{path}.rateSchedule")
    draws = _dated_rows(row["draws"], f"{path}.draws", {"amount", "conditionsMet"})
    principal_due = _dated_rows(row["scheduledPrincipal"], f"{path}.scheduledPrincipal", {"amount"})
    fees = _dated_rows(row["fees"], f"{path}.fees", {"amount"})
    for category, entries in (("draws", draws), ("scheduledPrincipal", principal_due), ("fees", fees)):
        for when, entry in entries.items():
            entry["amount"] = _krw(entry["amount"], f"{path}.{category}", 1)
            if category == "draws" and type(entry["conditionsMet"]) is not bool:
                _invalid(path, "각 인출의 선행조건 충족 여부를 지정해 주세요.")
            if category == "scheduledPrincipal" and not start <= when < maturity:
                _invalid(path, "만기 전 원금 약정 지급일을 지정해 주세요. 만기잔액은 만기일에 전액 청구됩니다.")
            if category == "fees" and not start <= when <= maturity:
                _invalid(path, "수수료 납부일을 인출 개시일 이후부터 만기일까지 지정해 주세요.")
    interest_rows = _rows(row["interestPaymentDates"], f"{path}.interestPaymentDates")
    interest_dates = {
        _day(day, f"{path}.interestPaymentDates[{index}]"): _locator({}, f"{path}.interestPaymentDates[{index}]")
        for index, day in enumerate(interest_rows)
    }
    if len(interest_dates) != len(interest_rows) or any(not start <= day < maturity for day in interest_dates):
        _invalid(path, "만기 전 이자 납부일을 중복 없이 지정해 주세요. 만기이자는 만기일에 함께 청구됩니다.")
    return _Tranche(
        identity, priority, convention, start, end, maturity, _krw(row["commitment"], f"{path}.commitment", 1),
        rates, draws, principal_due, interest_dates, fees, _locator(row, path),
    )


def _number(value: Fraction) -> int | float:
    return value.numerator if value.denominator == 1 else float(value)


def _draw(loan: _Tranche, when: date) -> dict | None:
    draw = loan.draws.get(when)
    if draw is None:
        return None
    available = loan.commitment - loan.drawn if loan.start <= when <= loan.end else ZERO
    amount = draw["amount"]
    if not loan.start <= when <= loan.end:
        reason = "outside_draw_window"
    elif not draw["conditionsMet"]:
        reason = "conditions_not_met"
    elif amount > available:
        reason = "commitment_exceeded"
    else:
        reason = None
        loan.drawn += amount
        loan.principal += amount
    drawn = ZERO if reason else amount
    return {
        "date": when.isoformat(), "trancheId": loan.identity, "requested": _number(amount),
        "availableCommitment": _number(available), "drawn": _number(drawn),
        "undrawnRequest": _number(amount - drawn), "reason": reason, "sourceLocator": draw["sourceLocator"],
    }


def _payment(loan: _Tranche, when: date, cash: Fraction) -> tuple[Fraction, dict]:
    scheduled = loan.principal_due.get(when)
    fees = loan.fees.get(when)
    due_at_maturity = when == loan.maturity
    if fees:
        loan.fee_arrears += fees["amount"]
        loan.total_fees += fees["amount"]
    if due_at_maturity or when in loan.interest_dates:
        loan.interest_arrears += loan.accrued_interest
        loan.accrued_interest = ZERO
    skipped = ZERO
    if due_at_maturity:
        loan.principal_arrears = loan.principal
    elif scheduled:
        new_due = min(scheduled["amount"], loan.principal - loan.principal_arrears)
        skipped = scheduled["amount"] - new_due
        loan.principal_arrears += new_due

    fee, interest, principal = loan.fee_arrears, loan.interest_arrears, loan.principal_arrears
    fee_paid = min(cash, fee)
    interest_paid = min(cash - fee_paid, interest)
    principal_paid = min(cash - fee_paid - interest_paid, principal)
    paid = fee_paid + interest_paid + principal_paid
    loan.fee_arrears -= fee_paid
    loan.interest_arrears -= interest_paid
    loan.principal_arrears -= principal_paid
    loan.principal -= principal_paid
    return cash - paid, {
        "date": when.isoformat(), "trancheId": loan.identity, "isMaturity": due_at_maturity,
        "availableBeforePayment": _number(cash),
        "principalDue": _number(principal), "interestDue": _number(interest), "feeDue": _number(fee),
        "principalPaid": _number(principal_paid), "interestPaid": _number(interest_paid), "feePaid": _number(fee_paid),
        "totalDue": _number(principal + interest + fee), "totalPaid": _number(paid),
        "shortfall": _number(loan.principal_arrears + loan.interest_arrears + loan.fee_arrears),
        "scheduledPrincipalWithoutOutstandingBalance": _number(skipped),
        "sourceLocators": [
            loan.source_locator, *(event["sourceLocator"] for event in (scheduled, fees) if event),
            *([loan.interest_dates[when]] if when in loan.interest_dates else []),
        ],
    }


def run_dated_financing(spec: dict) -> dict:
    """Accrue simple interest on [event day, next event day) and settle due dates.

    Each draw is indivisible, non-revolving and external to the repayment
    account. Only supplied account movements make cash available for service.
    On each date funding movements and draws precede servicing in explicit
    priority order. Arrears can be paid at later listed debt-service dates
    through maturity. The projection leaves unpaid maturities outstanding and
    does not accrue contractual or penalty interest after maturity.
    """
    spec = _record(spec, {"currency", "asOfDate", "openingRepaymentCash", "repaymentCashMovements", "tranches"}, set(), "datedFinancing")
    if spec["currency"] != "KRW":
        _invalid("currency", "통화는 KRW를 사용해 주세요.")
    as_of = _day(spec["asOfDate"], "asOfDate")
    opening_cash = _krw(spec["openingRepaymentCash"], "openingRepaymentCash")
    movements = _dated_rows(spec["repaymentCashMovements"], "repaymentCashMovements", {"amount"})
    for entry in movements.values():
        entry["amount"] = _krw(entry["amount"], "repaymentCashMovements", -MAX_AMOUNT_KRW)
    loans = [_tranche(row, f"tranches[{index}]") for index, row in enumerate(
        _rows(spec["tranches"], "tranches", MAX_TRANCHE_COUNT)
    )]
    if not loans or len({loan.identity for loan in loans}) != len(loans) or len({loan.priority for loan in loans}) != len(loans):
        _invalid("tranches", "대출이 한 건 이상 필요하며 식별자와 상환 우선순위는 중복될 수 없습니다.")
    if len(movements) + sum(len(loan.draws) + len(loan.rates) + len(loan.principal_due)
                            + len(loan.interest_dates) + len(loan.fees) for loan in loans) > MAX_EVENT_COUNT:
        _invalid("datedFinancing", f"전체 일자별 입력을 {MAX_EVENT_COUNT}건 이내로 줄여 주세요.")

    loans.sort(key=lambda loan: loan.priority)
    days = {as_of, *movements}
    own_days = {}
    for loan in loans:
        own_days[loan.identity] = {loan.maturity, *loan.rates, *loan.draws, *loan.principal_due, *loan.interest_dates, *loan.fees}
        days.update(own_days[loan.identity])
        days.add(loan.end)
    if (max(days) - min(days)).days > MAX_MODEL_DAYS:
        _invalid("datedFinancing", "전체 분석 기간은 50년 이하여야 합니다.")

    cash = opening_cash
    ledger, payments, draws, maturities = [], [], [], []
    net_movements = ZERO
    maturity_shortfalls: dict[date, Fraction] = {}
    paid_to_date = ZERO
    for when in sorted(day for day in days if day <= as_of):
        movement = movements.get(when)
        if movement:
            cash += movement["amount"]
            net_movements += movement["amount"]
            if cash < 0:
                _invalid("repaymentCashMovements", f"{when.isoformat()} 상환계정 인출액이 사용 가능 현금을 초과합니다.")
        for loan in loans:
            if when not in own_days[loan.identity] and when != as_of:
                continue
            opening_principal = loan.principal
            accrual_start = loan.previous or loan.start
            annual_rate_for_interval = loan.annual_rate
            interest = loan.accrue(when)
            rate = loan.rates.get(when)
            if rate:
                loan.annual_rate = rate["annualRate"]
            draw = _draw(loan, when)
            if draw:
                draws.append(draw)
            if when == loan.maturity or when in loan.interest_dates or when in loan.principal_due or when in loan.fees:
                previous_cash = cash
                cash, payment = _payment(loan, when, cash)
                paid_to_date += previous_cash - cash
                payments.append(payment)
                if payment["isMaturity"]:
                    maturities.append(payment)
                    shortfall = loan.principal_arrears + loan.interest_arrears + loan.fee_arrears
                    maturity_shortfalls[when] = maturity_shortfalls.get(when, ZERO) + shortfall
            interest_days = max(0, (min(when, loan.maturity) - accrual_start).days)
            ledger.append({
                "date": when.isoformat(), "trancheId": loan.identity,
                "interestDays": interest_days,
                "annualRateForInterval": _number(annual_rate_for_interval)
                if interest_days and annual_rate_for_interval is not None else None,
                "effectiveAnnualRate": _number(loan.annual_rate)
                if when < loan.maturity and loan.annual_rate is not None else None,
                "dayCount": loan.convention,
                "openingPrincipal": _number(opening_principal), "closingPrincipal": _number(loan.principal),
                "interestAccruedForInterval": _number(interest),
                "accruedInterestNotDue": _number(loan.accrued_interest),
                "unpaidPrincipalDue": _number(loan.principal_arrears),
                "unpaidInterestDue": _number(loan.interest_arrears),
                "unpaidFeesDue": _number(loan.fee_arrears),
                "sourceLocators": [loan.source_locator, *([rate["sourceLocator"]] if rate else [])],
            })
    return {
        "analysisType": "dated_financing", "engineVersion": "dated-financing-v1",
        "analysisOnly": True, "reportEligible": False, "currency": "KRW", "asOfDate": as_of.isoformat(),
        "status": "shortfall" if any(row["undrawnRequest"] for row in draws) or any(row["shortfall"] for row in payments)
                  else "maturities_settled" if len(maturities) == len(loans) else "maturities_pending",
        "summary": {
            "totalDrawn": _number(sum(loan.drawn for loan in loans)),
            "totalInterestAccrued": _number(sum(loan.total_interest for loan in loans)),
            "totalFeesDue": _number(sum(loan.total_fees for loan in loans)),
            "totalDebtServicePaid": _number(paid_to_date),
            "availableDrawCapacity": _number(sum((loan.commitment - loan.drawn for loan in loans if loan.start <= as_of <= loan.end), ZERO)),
            "endingPrincipal": _number(sum(loan.principal for loan in loans)),
            "unpaidPrincipal": _number(sum(loan.principal_arrears for loan in loans)),
            "unpaidInterestDue": _number(sum(loan.interest_arrears for loan in loans)),
            "accruedInterestNotDue": _number(sum(loan.accrued_interest for loan in loans)),
            "unpaidFeesDue": _number(sum(loan.fee_arrears for loan in loans)),
            "openingRepaymentCash": _number(opening_cash), "netRepaymentCashMovements": _number(net_movements),
            "endingRepaymentCash": _number(cash),
            "peakMaturityShortfall": _number(max(maturity_shortfalls.values())) if maturity_shortfalls else None,
            "totalMaturityShortfall": _number(sum(maturity_shortfalls.values())) if maturity_shortfalls else None,
            "maturitiesEvaluated": len(maturities), "maturitiesPending": len(loans) - len(maturities),
        },
        "draws": draws, "payments": payments, "maturities": maturities, "datedLedger": ledger,
        "maturityShortfallsByDate": [{"date": day.isoformat(), "shortfall": _number(amount)}
                                     for day, amount in maturity_shortfalls.items()],
        "repaymentCashMovements": [
            {"date": day.isoformat(), "amount": _number(movements[day]["amount"]), "sourceLocator": movements[day]["sourceLocator"]}
            for day in sorted(movements) if day <= as_of
        ],
        "policy": {
            "interest": "simple_interest_actual_days_before_maturity",
            "sameDayOrder": "cash_movements_draws_then_debt_service",
            "paymentOrder": "tranche_priority_then_fees_interest_principal",
            "drawPolicy": "indivisible_non_revolving_commitment_with_declared_conditions",
            "postMaturity": "unpaid_obligations_carried_without_further_accrual_or_payments",
            "repaymentCash": "explicit_available_cash_movements_only",
        },
    }
