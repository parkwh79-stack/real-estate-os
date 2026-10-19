"""Keep workbook sequence coordinates separate from elapsed calendar months."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.financial_input_validation import FinancialInputError


PERIOD_BASIS_VERSION = "cash-flow-period-basis-v1"


def period_basis(data: dict) -> dict | None:
    """Validate an explicitly supplied basis without creating one for old runs."""
    basis = data.get("periodBasis")
    if basis is None:
        return None
    if (not isinstance(basis, dict) or basis.get("schemaVersion") != PERIOD_BASIS_VERSION
            or basis.get("kind") not in {"source_sequence", "calendar_monthly"}):
        raise FinancialInputError("현금흐름 기간 기준을 확인해 주세요.", code="invalid_cash_flow_period_basis")
    if basis["kind"] == "source_sequence":
        periods = basis.get("periods")
        if not isinstance(periods, list) or not periods:
            raise FinancialInputError("원본 기간 목록과 출처가 필요합니다.", code="invalid_cash_flow_period_basis")
        seen = set()
        for item in periods:
            if not isinstance(item, dict):
                raise FinancialInputError("원본 기간 항목을 확인해 주세요.", code="invalid_cash_flow_period_basis")
            index, month = item.get("sourcePeriodIndex"), item.get("sourceMonthNumber")
            if (isinstance(index, bool) or not isinstance(index, int) or index < 0 or index in seen
                    or not isinstance(item.get("label"), str)
                    or item.get("periodKind") not in {"event", "monthly", "unknown"}
                    or (month is not None and (isinstance(month, bool) or not isinstance(month, int) or month < 1))):
                raise FinancialInputError("원본 기간의 순번·표기·월번호를 확인해 주세요.", code="invalid_cash_flow_period_basis")
            seen.add(index)
    return basis


def is_source_sequence(data: dict) -> bool:
    basis = period_basis(data)
    return basis is not None and basis["kind"] == "source_sequence"


def _source_location(locator: Any) -> str:
    if not isinstance(locator, dict):
        return "출처 미기록"
    document = locator.get("sourceDocument") or locator.get("sourceName") or locator.get("sourceWorkbook")
    sheet = locator.get("sheetName") or locator.get("sheet_name") or locator.get("sheet")
    cell = locator.get("cell") or locator.get("range")
    location = "!".join(str(value) for value in (sheet, cell) if value not in (None, ""))
    return " / ".join(str(value) for value in (document, location) if value not in (None, "")) or "출처 미기록"


def require_monthly_financing_period(request: dict) -> None:
    basis = period_basis(request)
    if basis is not None and basis["kind"] != "source_sequence":
        return
    if basis is None:
        # Old mapper drafts can be resubmitted to execute the new monthly mode.
        # Inspect only this execution boundary: never synthesize or amend the
        # period basis of historical snapshots when they are read or compared.
        unresolved = _legacy_workbook_events(request)
        if not unresolved:
            return
    else:
        unresolved = [item for item in basis["periods"] if item["periodKind"] != "monthly"]
    details = "; ".join(f"{item['label']} ({_source_location(item.get('sourceLocator'))})" for item in unresolved)
    message = (
        "원본 사건 순서는 실제 경과 개월이 아니므로 월별 이자를 계산할 수 없습니다. "
        "사건 날짜와 계산 월차의 연결을 먼저 확인해 주세요. "
        + (f"확인이 필요한 사건: {details}." if details else "원본 기간과 실제 달력의 연결이 미확인입니다.")
        + " 예산 현금흐름 검토는 계속 사용할 수 있습니다."
    )
    raise FinancialInputError(message, code="monthly_financing_period_unconfirmed")


def _legacy_workbook_events(request: dict) -> list[dict]:
    schedule = request.get("costSchedule") or request.get("cost_schedule") or {}
    rows = schedule.get("rows") if isinstance(schedule, dict) else schedule
    if not isinstance(rows, list):
        return []
    section = schedule if isinstance(schedule, dict) else {}
    found = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("period") or "").strip() not in {
            "토지계약", "토지잔금", "교평등", "사업승인", "분양",
        }:
            continue
        locator = row.get("sourceLocator") or row.get("source_locator") or section.get("sourceLocator") or {}
        locator = locator if isinstance(locator, dict) else {}
        source_types = [row.get("sourceType"), locator.get("sourceType"), section.get("sourceType")]
        workbook_type = any(
            any(token in str(value or "").casefold() for token in ("excel", "xlsx", "workbook"))
            for value in source_types
        )
        located_cell = bool((locator.get("sheetName") or locator.get("sheet_name") or locator.get("sheet"))
                            and (locator.get("cell") or locator.get("range")))
        if workbook_type or located_cell:
            found.append({"label": str(row["period"]).strip(), "sourceLocator": locator})
    return found


def calculation_period_label(request: dict, index: int) -> str:
    basis = period_basis(request)
    if basis is None or basis["kind"] != "source_sequence":
        return f"M{index}"
    item = next((item for item in basis["periods"] if item["sourcePeriodIndex"] == index), None)
    return f"원본 {index + 1}: {item['label']}" if item else f"원본 대응 미확인: 계산 순서 {index + 1}"


def captured_period_basis(request: dict, rows: list[dict], *, monthly_financing: bool) -> dict | None:
    basis = period_basis(request)
    if basis is None:
        if not monthly_financing:
            return None
        basis = {"schemaVersion": PERIOD_BASIS_VERSION, "kind": "calendar_monthly",
                 "monthIndexOrigin": "project_month_zero"}
    captured = deepcopy(basis)
    captured.update(calculationPeriodCount=len(rows),
                    firstCalculationPeriodIndex=rows[0]["monthIndex"] if rows else None,
                    lastCalculationPeriodIndex=rows[-1]["monthIndex"] if rows else None)
    return captured


def interest_calculation(rows: list[dict], *, monthly_financing: bool) -> dict:
    if not monthly_financing:
        return {"status": "not_calculated", "reason": "source_period_duration_unconfirmed",
                "firstChargedMonthIndex": None, "lastChargedMonthIndex": None, "chargedPeriodCount": 0}
    charged = [row["monthIndex"] for row in rows if row["monthlyFinancing"]["interest"] > 0]
    return {"status": "calculated", "method": "opening_balance_monthly_12",
            "firstChargedMonthIndex": charged[0] if charged else None,
            "lastChargedMonthIndex": charged[-1] if charged else None,
            "chargedPeriodCount": len(charged)}


def require_stress_calendar_period(stress_input: dict) -> None:
    base = stress_input.get("baseCashFlowRequest")
    if is_source_sequence(stress_input) or (isinstance(base, dict) and is_source_sequence(base)):
        raise FinancialInputError(
            "원본 사건과 달력 월차의 연결이 미확인되어 기간을 사용하는 스트레스 계산은 진행하지 않습니다. "
            "원본 행 수를 사업 개월로 대신 넣지 않습니다. 독립 PF 시나리오와 예산 현금흐름은 계속 검토할 수 있습니다.",
            code="cash_flow_stress_period_unconfirmed",
        )
