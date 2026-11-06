"""Read-only, source-bound transaction map for the two Sangbong cash-flow sheets.

This adapter normalizes captured source values; it does not recalculate Excel or
approve the source assumptions. In particular, paid construction is a different
row from earned construction, and refundable deposits are financing receipts.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING
import re
from typing import Any

from openpyxl.utils import get_column_letter
from domains.feasibility.numeric import excel_round_int, to_decimal


PROFILE_VERSION = "sangbong-cash-flow-map-v1"
SHEET_NAME = "2. 캐쉬_분양률100%"
SCALE = Decimal(1_000_000)
_OPPOSITE = {
    "cash_in": "cash_out", "cash_out": "cash_in",
    "pf_draw": "loan_repayment", "loan_repayment": "pf_draw",
    "equity_contribution": "equity_recovery", "equity_recovery": "equity_contribution",
    "other_financing_inflow": "other_financing_outflow",
    "other_financing_outflow": "other_financing_inflow",
}
_PROFILES = {
    "sale": {
        "unit_row": 92, "last_row": 240, "net_row": 239, "balance_row": 240,
        "receipt_row": 137, "cost_row": 217, "interest_receipt_rows": [132, 133, 134, 135],
        "anchors": {(115, 4): "투입자본금", (116, 4): "PF", (151, 4): "공사비 계"},
        "rows": {
            "pf_draw": [97, 98, 99, 116, 118, 120],
            "loan_repayment": [101, 102, 103, 207, 221, 222, 223],
            "equity_contribution": [115], "equity_recovery": [238],
            "cash_in": [*range(123, 131), *range(132, 136)],
            # Row 151 owns actual construction payments, including retention.
            # Row 150 is earned work, not this period's payment.
            "cash_out": [*range(141, 147), 151, *range(152, 157), *range(158, 165),
                         *range(166, 176), *range(177, 184), *range(185, 193),
                         *range(194, 207), *range(208, 216)],
        },
    },
    "rental": {
        "unit_row": 84, "last_row": 239, "net_row": 238, "balance_row": 239,
        "receipt_row": 128, "cost_row": 216, "interest_receipt_rows": [123, 124, 125, 126],
        "anchors": {(107, 4): "투입자본금", (108, 4): "기금대출", (142, 4): "공사비 계"},
        "rows": {
            "pf_draw": [89, 90, 91, 108, 110, 112],
            "loan_repayment": [93, 94, 95, 220, 221, 222],
            "equity_contribution": [107], "equity_recovery": [237],
            "cash_in": [117, 118, 119, 120, 121, *range(123, 127)],
            "other_financing_inflow": [115, 116],
            "cash_out": [*range(132, 138), 142, *range(143, 149), *range(150, 157),
                         *range(158, 168), *range(169, 176), *range(177, 184),
                         *range(185, 192), *range(193, 215)],
        },
    },
}


def _number(value: Any) -> Decimal | None:
    if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
        return None
    try:
        return to_decimal(value)
    except (TypeError, ValueError):
        return None


def _raw(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, Decimal)):
        return float(value) if _number(value) is not None else str(value)
    text, ref = getattr(value, "text", None), getattr(value, "ref", None)
    if text is not None or ref is not None:
        return {"type": type(value).__name__, "text": text, "ref": ref}
    return str(value)


def _formula(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("="):
        return value
    if getattr(value, "text", None) is not None or getattr(value, "ref", None) is not None:
        return _raw(value)
    return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def _date_chain_formula(raw: Any, previous_column: int) -> bool:
    if not isinstance(raw, str):
        return False
    previous = get_column_letter(previous_column) + "8"
    normalized = re.sub(r"[\s$]", "", raw).upper()
    return normalized in {
        f"=+DATE(YEAR({previous}),MONTH({previous})+1,DAY({previous}))",
        f"=DATE(YEAR({previous}),MONTH({previous})+1,DAY({previous}))",
        f"=EDATE({previous},1)",
    }


def map_sangbong_cash_flow(formula_sheet: Any, value_sheet: Any, variant: str, source_name: str) -> dict:
    """Map the profile's actual transaction cells to sparse whole-KRW buckets.

    Original zeros are counted, blanks stay absent, and unavailable formula
    caches make the affected period incomplete. Calendar verification covers
    only the explicit first-month/next-month header chain, never other formulas.
    """
    if variant not in _PROFILES:
        raise ValueError("Unknown Sangbong cash-flow variant")
    if formula_sheet.title != SHEET_NAME or value_sheet.title != SHEET_NAME:
        raise ValueError("Sangbong cash-flow sheet name does not match the profile")
    spec = _PROFILES[variant]
    max_column = max(int(formula_sheet.max_column or 0), int(value_sheet.max_column or 0))
    if not 10 <= max_column <= 512:
        raise ValueError("Sangbong cash-flow source columns exceed the supported profile range")
    formulas = [[cell.value for cell in row] for row in formula_sheet.iter_rows(
        min_row=1, max_row=spec["last_row"], min_col=1, max_col=max_column)]
    values = [[cell.value for cell in row] for row in value_sheet.iter_rows(
        min_row=1, max_row=spec["last_row"], min_col=1, max_col=max_column)]

    def get(matrix: list, row: int, column: int) -> Any:
        return matrix[row - 1][column - 1] if row <= len(matrix) and column <= len(matrix[row - 1]) else None

    issues: list[dict] = []
    warnings = [
        "현금흐름 원본의 저장 계산값을 매핑했습니다. Excel 전체 수식을 재계산하거나 승인한 결과가 아닙니다.",
        "차입·상환·자본금 및 회수는 사업 매출·사업비와 별도로 구분합니다.",
        "표시된 잔액은 자금조달 후 잔액이며 조달 전 최대 자금 부족액이 아닙니다.",
    ]
    unit = get(values, spec["unit_row"], 2)
    if not isinstance(unit, str) or "백만" not in unit:
        issues.append({"code": "source_unit_unconfirmed", "cell": f"B{spec['unit_row']}", "rawValue": _raw(unit)})
    for (row, column), expected in spec["anchors"].items():
        actual = get(values, row, column)
        if re.sub(r"\s", "", str(actual or "")) != re.sub(r"\s", "", expected):
            issues.append({"code": "cash_flow_layout_mismatch", "cell": f"{get_column_letter(column)}{row}",
                           "expected": expected, "rawValue": _raw(actual)})
    date_columns = [column for column in range(10, max_column + 1) if _date(get(values, 8, column)) is not None]
    review = {
        "schemaVersion": PROFILE_VERSION, "variant": variant, "status": "needs_review",
        "sourceName": source_name, "sourceSheet": SHEET_NAME,
        "sourceUnit": "KRW_MILLION", "normalizedUnit": "KRW", "normalizationFactor": 1_000_000,
        "roundingPolicy": "excel_round_half_away_from_zero_to_whole_krw",
        "roundingReason": "원 단위 반올림을 적용하고 원본값·처리 이유를 함께 기록하는 사용자 정책입니다.",
        "calculationEvidence": {"method": "saved_workbook_cache", "recalculated": False},
        "formulaRetention": "nonzero_transaction_formulas_inline; zero-cell formulas remain in the unchanged archived original",
        "analysisOnly": True, "reportEligible": False, "authoritative": False,
        "warnings": warnings, "issues": issues, "rowDefinitions": [], "sourceTransactions": {},
        "sourceControls": [], "periodChecks": [],
    }
    if issues or not date_columns:
        if not date_columns:
            issues.append({"code": "cash_flow_calendar_headers_missing", "cell": f"J8:{get_column_letter(max_column)}8"})
        review.update(mappingStatus="unsupported", transactionMappingComplete=False)
        return {"monthly_cash_flow": [], "cashFlowPeriodBasis": None, "cashFlowReview": review}

    columns = list(range(10, max(date_columns) + 1))
    calendar_verified = True
    periods = []
    prior_date = None
    for index, column in enumerate(columns):
        raw_date, saved_date = get(formulas, 8, column), _date(get(values, 8, column))
        literal_date = _date(raw_date)
        verified = saved_date is not None and saved_date.day == 1
        if index == 0:
            verified = verified and literal_date == saved_date
        else:
            verified = (verified and prior_date is not None and saved_date == _next_month(prior_date)
                        and (literal_date == saved_date or _date_chain_formula(raw_date, column - 1)))
        if not verified:
            calendar_verified = False
            issues.append({"code": "cash_flow_calendar_header_unverified", "cell": f"{get_column_letter(column)}8",
                           "rawFormula": _raw(raw_date), "savedValue": _raw(get(values, 8, column))})
        label = saved_date.strftime("%Y-%m") if saved_date is not None else f"원본 열 {get_column_letter(column)}"
        periods.append({
            "sourcePeriodIndex": index, "label": label,
            "periodKind": "monthly" if verified else "unknown", "sourceMonthNumber": index + 1 if verified else None,
            "sourcePhaseMonth": _raw(get(values, 7, column)), "sourcePhaseMonthFormula": _formula(get(formulas, 7, column)),
            "sourceEvent": _raw(get(values, 6, column)),
            "sourceLocator": {"sourceName": source_name, "sheet": SHEET_NAME,
                              "cell": f"{get_column_letter(column)}8", "phaseMonthCell": f"{get_column_letter(column)}7"},
        })
        prior_date = saved_date
    if not calendar_verified:
        for period in periods:
            period.update(periodKind="unknown", sourceMonthNumber=None)
        warnings.append("일부 달력 헤더를 확인하지 못해 원본 순서만 보존합니다. 월별 이자 계산 전에 기간을 확인하세요.")
    basis = {
        "schemaVersion": "cash-flow-period-basis-v1",
        "kind": "calendar_monthly" if calendar_verified else "source_sequence",
        "monthIndexOrigin": "source_first_calendar_month" if calendar_verified else "source_column_sequence",
        "calendarStart": periods[0]["label"] if calendar_verified else None,
        "calendarVerification": "literal_first_month_and_explicit_next_month_chain" if calendar_verified else "unverified",
        "sourcePhaseMonthPolicy": "retained_separately; resetting phase counters are not elapsed calendar indices",
        "periods": periods, "warnings": [] if calendar_verified else warnings[-1:],
    }
    row_kinds = {row: kind for kind, row_numbers in spec["rows"].items() for row in row_numbers}
    labels = {}
    for row, kind in sorted(row_kinds.items()):
        label = " · ".join(str(get(values, row, column)).strip().replace("\n", " ")
                           for column in range(2, 6) if isinstance(get(values, row, column), str) and str(get(values, row, column)).strip())
        labels[row] = label or f"원본 {row}행"
        review["rowDefinitions"].append({"row": row, "label": labels[row], "kind": kind,
            "sourceCell": f"B{row}:G{row}", "declaredTotal": {"cell": f"G{row}",
            "rawValue": _raw(get(values, row, 7)), "formula": _formula(get(formulas, row, 7))}})
    counts = {"numericCellCount": 0, "zeroCellCount": 0, "blankCellCount": 0,
              "nonzeroCellCount": 0, "formulaCellCount": 0, "unavailableCellCount": 0, "roundedCellCount": 0}
    opening = _number(get(values, spec["balance_row"], 9))
    cumulative = excel_round_int(opening * SCALE) if opening is not None else None
    cumulative_rounding = abs(Decimal(cumulative) - opening * SCALE) if opening is not None else Decimal(0)
    if opening is None:
        issues.append({"code": "opening_funded_balance_unavailable", "cell": f"I{spec['balance_row']}"})
    monthly_rows = []
    greatest_difference = 0
    greatest_cumulative_difference = 0
    for index, column in enumerate(columns):
        column_name = get_column_letter(column)
        buckets = {kind: {} for kind in _OPPOSITE}
        complete = True
        signed_raw = Decimal(0)
        rounding = Decimal(0)
        period_nonzero = 0
        for row, source_kind in sorted(row_kinds.items()):
            raw_formula, saved = get(formulas, row, column), get(values, row, column)
            formula = _formula(raw_formula)
            if formula is not None:
                counts["formulaCellCount"] += 1
            number = _number(saved)
            if number is None:
                if saved in (None, "") and formula is None:
                    counts["blankCellCount"] += 1
                    continue
                counts["unavailableCellCount"] += 1
                complete = False
                issues.append({"code": "formula_cache_unavailable" if formula is not None else "transaction_value_not_numeric",
                    "cell": f"{column_name}{row}", "rawValue": _raw(saved), "formula": formula})
                continue
            counts["numericCellCount"] += 1
            direction = 1 if source_kind in {"cash_in", "pf_draw", "equity_contribution", "other_financing_inflow"} else -1
            signed_raw += number * direction
            if number == 0:
                counts["zeroCellCount"] += 1
                continue
            amount = excel_round_int(number * SCALE)
            delta = Decimal(amount) - number * SCALE
            rounding += abs(delta)
            counts["nonzeroCellCount"] += 1
            period_nonzero += 1
            if delta:
                counts["roundedCellCount"] += 1
            target_kind = source_kind if number >= 0 else _OPPOSITE[source_kind]
            key = f"row_{row}_{labels[row]}"
            buckets[target_kind][key] = abs(amount)
            review["sourceTransactions"][f"{column_name}{row}"] = {
                "rawValue": _raw(saved), "formula": formula, "sourceKind": source_kind,
                "normalizedKind": target_kind, "normalizedKrw": abs(amount),
                **({"roundingDeltaKrw": float(delta)} if delta else {}),
            }
        total_in = sum(sum(buckets[kind].values()) for kind in ("cash_in", "pf_draw", "equity_contribution", "other_financing_inflow"))
        total_out = sum(sum(buckets[kind].values()) for kind in ("cash_out", "loan_repayment", "equity_recovery", "other_financing_outflow"))
        net = total_in - total_out if complete else None
        if cumulative is not None and net is not None:
            cumulative += net
        else:
            cumulative = None
        cumulative_rounding += rounding
        saved_net = _number(get(values, spec["net_row"], column))
        saved_balance = _number(get(values, spec["balance_row"], column))
        expected_net = excel_round_int(saved_net * SCALE) if saved_net is not None else None
        expected_balance = excel_round_int(saved_balance * SCALE) if saved_balance is not None else None
        net_bound = int((rounding + (abs(Decimal(expected_net) - saved_net * SCALE) if saved_net is not None else Decimal(0))).to_integral_value(rounding=ROUND_CEILING))
        balance_bound = int((cumulative_rounding + (abs(Decimal(expected_balance) - saved_balance * SCALE) if saved_balance is not None else Decimal(0))).to_integral_value(rounding=ROUND_CEILING))
        net_diff = net - expected_net if net is not None and expected_net is not None else None
        balance_diff = cumulative - expected_balance if cumulative is not None and expected_balance is not None else None
        net_pass = net_diff is not None and abs(net_diff) <= net_bound
        balance_pass = balance_diff is not None and abs(balance_diff) <= balance_bound
        if net_diff is not None:
            greatest_difference = max(greatest_difference, abs(net_diff))
        if balance_diff is not None:
            greatest_cumulative_difference = max(greatest_cumulative_difference, abs(balance_diff))
        review["periodChecks"].append({"sourcePeriodIndex": index,
            "netCell": f"{column_name}{spec['net_row']}", "sourceNetRawValue": _raw(get(values, spec["net_row"], column)),
            "mappedNetKrw": net, "sourceNetKrw": expected_net, "differenceKrw": net_diff,
            "roundingBoundKrw": net_bound, "passed": net_pass,
            "balanceCell": f"{column_name}{spec['balance_row']}", "sourceBalanceRawValue": _raw(get(values, spec["balance_row"], column)),
            "mappedCumulativeKrw": cumulative, "sourceBalanceKrw": expected_balance,
            "cumulativeDifferenceKrw": balance_diff, "cumulativeRoundingBoundKrw": balance_bound,
            "cumulativePassed": balance_pass, "rawNetKrw": float(signed_raw * SCALE) if complete else None})
        if not net_pass or not balance_pass:
            issues.append({"code": "cash_flow_period_reconciliation_required", "sourcePeriodIndex": index,
                           "netCell": f"{column_name}{spec['net_row']}", "netDifferenceKrw": net_diff,
                           "balanceDifferenceKrw": balance_diff})
        monthly_rows.append({"month_index": index, "period": periods[index]["label"], **buckets,
            "net_cash_flow": net, "cumulative_cash_flow": cumulative,
            "source_locator": {"sourceType": "excel_range", "sourceDocument": source_name, "sheetName": SHEET_NAME,
                "cell": f"{column_name}{min(row_kinds)}:{column_name}{max(row_kinds)}",
                "sourceUnit": "KRW_MILLION", "normalizedUnit": "KRW", "normalizationFactor": 1_000_000,
                "transactionRowPolicy": PROFILE_VERSION, "sourceDateCell": f"{column_name}8",
                "sourcePhaseMonthCell": f"{column_name}7", "complete": complete,
                "nonzeroTransactionCount": period_nonzero,
                "cashBalanceBasis": "funded_cash_after_debt_and_equity_transfers; not_unfunded_max_minus"}})
    for name, row in (("totalReceiptsIncludingFunding", spec["receipt_row"]), ("totalOperatingOutgoings", spec["cost_row"])):
        declared = _number(get(values, row, 7))
        period_numbers = [_number(get(values, row, column)) for column in columns]
        observed = sum((number for number in period_numbers if number is not None), Decimal(0))
        difference = (observed - declared) * SCALE if declared is not None else None
        review["sourceControls"].append({"key": name, "cell": f"G{row}",
            "rawDeclaredValue": _raw(get(values, row, 7)), "formula": _formula(get(formulas, row, 7)),
            "sourcePeriodRawSum": float(observed), "differenceKrwBeforeRounding": float(difference) if difference is not None else None,
            "passed": difference is not None and abs(difference) <= Decimal(1),
            "requiresReview": difference is None or abs(difference) > Decimal(1),
            "periodsWithValue": sum(number is not None for number in period_numbers),
            "comparisonBasis": "same_source_control_row; declared_total_vs_its_period_cells"})
        if difference is not None and abs(difference) > Decimal(1):
            warnings.append(f"원본 {SHEET_NAME}!G{row} 합계와 기간별 저장값 합계가 다릅니다. 원본 수식을 확인하세요.")
    if any(_number(get(values, row, 7)) not in (None, Decimal(0)) for row in spec["interest_receipt_rows"]):
        warnings.append("중도금 이자 회수 수입이 포함되어 현금수입 합계와 사업수지의 분양 매출 합계는 서로 다른 기준입니다.")
    if variant == "rental":
        warnings.append("임대보증금·증액은 반환 의무가 있는 자금조달로 구분합니다. 전환 매각 잔금의 보증금 차감은 원본 셀에 있는 순액을 유지하며 임의 총액화하지 않습니다.")
    review.update(mappingStatus="complete" if counts["unavailableCellCount"] == 0 and calendar_verified else "partial",
        transactionMappingComplete=counts["unavailableCellCount"] == 0,
        periodCount=len(monthly_rows), calendarVerified=calendar_verified, coverage=counts,
        maximumNetRoundingDifferenceKrw=greatest_difference,
        maximumCumulativeRoundingDifferenceKrw=greatest_cumulative_difference,
        periodControlsMatched=all(item["passed"] and item["cumulativePassed"] for item in review["periodChecks"]),
        sourceDeclaredTotalsMatched=all(item["passed"] for item in review["sourceControls"]),
        sourceFundedEndBalanceKrw=review["periodChecks"][-1]["sourceBalanceKrw"],
        sourceFundedBalanceIsMaxMinus=False)
    return {"monthly_cash_flow": monthly_rows, "cashFlowPeriodBasis": basis, "cashFlowReview": review}
