"""Explicit working assumptions that connect a source sequence to calendar months.

Only the reviewed-monthly request mapper consumes these inputs. Source workbooks,
approved studies, budget calculations and historical runs remain immutable.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import re
from typing import Any

from core.financial_input_validation import FinancialInputError

VERSION = "cash-flow-timing-assumptions-v1"
MAX_MONTH = 240


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _source_study(project: dict) -> dict:
    return next((project[key] for key in ("feasibilityStudyV2Draft", "canonicalFeasibilityStudyV2", "feasibilityStudyV2")
                 if isinstance(project.get(key), dict)), {})


def timing_source(project: dict) -> dict | None:
    study = _source_study(project)
    raw = study.get("raw_import") or {}
    raw = raw if isinstance(raw, dict) else {}
    basis = raw.get("cashFlowPeriodBasis")
    if not isinstance(basis, dict) or basis.get("kind") != "source_sequence":
        return None
    document = raw.get("sourceDocument") or {}
    document = {key: deepcopy(document[key]) for key in ("archiveId", "originalFileHash", "originalFileName")
                if isinstance(document, dict) and key in document}
    return {"sourceBasisHash": _hash({"basis": basis, "sourceDocument": document}),
            "basis": deepcopy(basis), "sourceDocument": document,
            "analysisOnly": True, "reportEligible": False}


def _integer(value: Any, label: str, high: int, *, nullable: bool = True) -> int | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
        raise ValueError(f"{label}: 0~{high} 범위의 정수를 입력하세요.")
    return value


def _date(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{label}: YYYY-MM-DD 날짜를 입력하세요.")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}: 존재하는 날짜를 입력하세요.") from exc
    return value


def validate_timing(value: Any) -> dict | None:
    if value is None:
        return None
    keys = {"schemaVersion", "sourceBasisHash", "baseDate", "firstMonthlyMonthIndex", "eventBindings", "collections", "unlistedMonthsAreZero"}
    if (not isinstance(value, dict) or value.keys() - keys or value.get("schemaVersion") != VERSION
            or not isinstance(value.get("sourceBasisHash"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["sourceBasisHash"])):
        raise ValueError("분석용 시간 가정과 원본 기간의 연결을 확인하세요.")
    events, collections = value.get("eventBindings", []), value.get("collections", [])
    if value.get("unlistedMonthsAreZero") not in (None, True, False) or (
            value.get("unlistedMonthsAreZero") is not None and not isinstance(value["unlistedMonthsAreZero"], bool)):
        raise ValueError("미기재 월의 수금 0원 가정을 직접 확인하세요.")
    if not isinstance(events, list) or len(events) > 241 or not isinstance(collections, list) or len(collections) > 241:
        raise ValueError("시간·수금 가정은 각각 241개 이하로 입력하세요.")
    clean_events, clean_collections, seen_events, seen_months = [], [], set(), set()
    for row in events:
        if not isinstance(row, dict) or row.keys() - {"sourcePeriodIndex", "plannedDate", "monthIndex"}:
            raise ValueError("사건 날짜·월차 항목을 확인하세요.")
        index = _integer(row.get("sourcePeriodIndex"), "원본 사건 순번", 10000, nullable=False)
        if index in seen_events:
            raise ValueError("같은 원본 사건을 중복 입력할 수 없습니다.")
        seen_events.add(index)
        clean_events.append({"sourcePeriodIndex": index, "plannedDate": _date(row.get("plannedDate"), "사건 날짜"),
                             "monthIndex": _integer(row.get("monthIndex"), "사건 계산 월차", MAX_MONTH)})
    for row in collections:
        if not isinstance(row, dict) or row.keys() - {"monthIndex", "contract", "interim", "balance"}:
            raise ValueError("월별 분양수금 항목을 확인하세요.")
        month = _integer(row.get("monthIndex"), "수금 계산 월차", MAX_MONTH)
        if month is not None and month in seen_months:
            raise ValueError("같은 월차의 수금은 한 행에 합쳐 입력하세요.")
        seen_months.add(month)
        clean_collections.append({"monthIndex": month, **{
            key: _integer(row.get(key), "분양수금 금액(원)", 9_007_199_254_740_991)
            for key in ("contract", "interim", "balance")}})
    return {"schemaVersion": VERSION, "sourceBasisHash": value["sourceBasisHash"],
            "baseDate": _date(value.get("baseDate"), "계산 M0 기준일"),
            "firstMonthlyMonthIndex": _integer(value.get("firstMonthlyMonthIndex"), "원본 첫 월의 계산 월차", MAX_MONTH),
            "eventBindings": clean_events, "collections": clean_collections,
            "unlistedMonthsAreZero": value.get("unlistedMonthsAreZero")}


def _month_difference(day: str, origin: str) -> int:
    current, base = date.fromisoformat(day), date.fromisoformat(origin)
    return (current.year - base.year) * 12 + current.month - base.month


def _month_date(origin: str, month: int) -> str:
    base = date.fromisoformat(origin)
    number = base.year * 12 + base.month - 1 + month
    return date(number // 12, number % 12 + 1, 1).isoformat()


def validate_timing_source(project: dict, timing: dict | None) -> None:
    if timing is None:
        return
    source = timing_source(project)
    if source is None or timing["sourceBasisHash"] != source["sourceBasisHash"]:
        raise ValueError("시간 가정의 원본 기간이 바뀌었습니다. 최신 원본을 다시 불러온 뒤 입력하세요.")
    periods = source["basis"].get("periods") or []
    event_indices = {row.get("sourcePeriodIndex") for row in periods if row.get("periodKind") == "event"}
    previous_day, previous_month = None, None
    for row in sorted(timing["eventBindings"], key=lambda item: item["sourcePeriodIndex"]):
        if row["sourcePeriodIndex"] not in event_indices:
            raise ValueError("현재 원본에 없는 사건을 시간 가정에 연결할 수 없습니다.")
        day, month = row["plannedDate"], row["monthIndex"]
        if day is not None and previous_day is not None and day < previous_day:
            raise ValueError("사건 날짜는 원본 사건 순서보다 앞뒤가 바뀔 수 없습니다.")
        if month is not None and previous_month is not None and month < previous_month:
            raise ValueError("사건 월차는 원본 사건 순서대로 입력하세요.")
        if day is not None and timing["baseDate"] is not None and month is not None:
            if _month_difference(day, timing["baseDate"]) != month:
                raise ValueError("사건 날짜와 계산 월차가 M0 기준월에 맞지 않습니다.")
        previous_day = day if day is not None else previous_day
        previous_month = month if month is not None else previous_month
    anchor = timing["firstMonthlyMonthIndex"]
    if anchor is not None:
        by_event = {row["sourcePeriodIndex"]: row.get("monthIndex") for row in timing["eventBindings"]}
        last_known_month = -1
        for row in periods:
            source_month = row.get("sourceMonthNumber")
            if isinstance(source_month, int) and anchor + source_month - 1 > MAX_MONTH:
                raise ValueError("원본 월별 기간이 지원 범위 M240을 넘습니다.")
            month = anchor + source_month - 1 if row.get("periodKind") == "monthly" and isinstance(source_month, int) else by_event.get(row.get("sourcePeriodIndex"))
            if month is not None:
                if month < last_known_month:
                    raise ValueError("원본 사건·월별 기간 순서와 계산 월차가 일치하지 않습니다.")
                last_known_month = month


def apply_timing_to_monthly_request(project: dict, request: dict) -> dict:
    """Map explicit assumptions into existing engine inputs, never a new formula."""
    timing = validate_timing(project.get("cash_flow_timing"))
    if timing is None:
        return request
    validate_timing_source(project, timing)
    source = timing_source(project)
    basis = source["basis"]
    periods = basis.get("periods") or []
    monthly = [row for row in periods if row.get("periodKind") == "monthly"]
    if (not monthly or any(row.get("periodKind") not in {"monthly", "event"} for row in periods)
            or [row.get("sourceMonthNumber") for row in monthly] != list(range(1, len(monthly) + 1))):
        raise FinancialInputError("원본 월 번호가 누락·중복·불연속되어 시간 가정을 적용할 수 없습니다. 원본을 다시 확인하세요.",
                                  code="cash_flow_timing_source_unconfirmed")
    events = {row["sourcePeriodIndex"]: row for row in timing["eventBindings"]}
    mapping, last_month = {}, -1
    for row in periods:
        index = row["sourcePeriodIndex"]
        if row["periodKind"] == "monthly":
            anchor = timing["firstMonthlyMonthIndex"]
            month = anchor + row["sourceMonthNumber"] - 1 if anchor is not None else None
        else:
            event = events.get(index) or {}
            month = event.get("monthIndex") if event.get("plannedDate") else None
        if timing["baseDate"] is None or month is None:
            raise FinancialInputError("분석용 M0 기준일·원본 월 연결·모든 사건 날짜와 월차를 입력하세요. 예산 현금흐름은 계속 사용할 수 있습니다.",
                                      code="cash_flow_timing_incomplete")
        if month < last_month or month > MAX_MONTH:
            raise FinancialInputError("원본 기간 순서와 계산 월차가 일치하지 않습니다.", code="cash_flow_timing_order")
        mapping[index], last_month = month, month
    collections = timing["collections"]
    if timing["unlistedMonthsAreZero"] is not True:
        raise FinancialInputError("표에 없는 월의 수금을 0원으로 둘지 분석 가정을 직접 확인하세요.",
                                  code="cash_flow_timing_collections_incomplete")
    if not collections or any(row[key] is None for row in collections for key in ("monthIndex", "contract", "interim", "balance")):
        raise FinancialInputError("월별 수금의 월차·계약금·중도금·잔금을 명시하세요. 확인한 0원도 직접 입력하세요.",
                                  code="cash_flow_timing_collections_incomplete")
    if any(row["monthIndex"] > last_month for row in collections):
        raise ValueError("수금 월차가 연결한 원본 기간을 벗어납니다.")
    total = sum(row[key] for row in collections for key in ("contract", "interim", "balance"))
    revenue = (request.get("sourcesAndUses") or {}).get("revenue") or {}
    if not math.isclose(total, revenue.get("totalSalesRevenue") or 0, rel_tol=1e-12, abs_tol=0.01):
        raise ValueError("분석용 수금 합계와 현재 총매출이 일치하지 않습니다. 자동 배분하지 않으므로 수금 가정을 대사하세요.")

    from agents.cash_flow_mapper import build_cash_flow_run_request_from_feasibility_study
    source_request = build_cash_flow_run_request_from_feasibility_study(_source_study(project))
    cost_schedule = deepcopy(source_request.get("costSchedule") or {})
    cost_rows = cost_schedule.get("rows") or []
    costs = (request.get("sourcesAndUses") or {}).get("cost") or {}
    for key, expected_key in (("landCost", "landCost"), ("constructionCost", "directConstructionCost"),
                              ("financialCost", "financialCost"), ("otherCost", "otherCost")):
        if not math.isclose(sum(row.get(key) or 0 for row in cost_rows), costs.get(expected_key) or 0,
                            rel_tol=1e-9, abs_tol=0.01):
            raise ValueError("현재 비용과 원본 기간별 비용이 다릅니다. 비용 배분을 먼저 대사하세요. 예산 현금흐름은 계속 사용할 수 있습니다.")
    for row in cost_rows:
        index = row.get("monthIndex")
        if index not in mapping:
            raise ValueError("원본 비용 행의 기간 연결이 누락되었습니다.")
        row["sourcePeriodIndex"] = index
        row["monthIndex"] = mapping[index]
    cost_schedule.update(approvalStatus="not_requested", sourceType="user_timing_assumption")
    updated = deepcopy(request)
    updated.pop("capturedWorkbookCashFlow", None)
    updated["costSchedule"] = cost_schedule
    updated["schedule"] = {"analysis_origin": {"plannedDate": timing["baseDate"], "monthIndex": 0}}
    for key, binding in (basis.get("scheduleBindings") or {}).items():
        index = binding.get("sourcePeriodIndex")
        if index in mapping:
            month = mapping[index]
            updated["schedule"][key] = {"plannedDate": (events.get(index) or {}).get("plannedDate") or _month_date(timing["baseDate"], month),
                                        "monthIndex": month}
    if not {"construction_start", "completion"} <= updated["schedule"].keys():
        raise ValueError("원본 착공·준공의 기간 연결이 없습니다. 원본을 다시 확인하세요.")
    updated["productMix"] = [{"productId": "reviewed-total-collections", "assetType": "other",
        "name": "사업 전체 분양수금 분석 가정 · 상품별 배분 미확인", "totalSalesRevenue": total,
        "collectionSteps": [{"stage": key, "monthlyCollectionAmounts": [
            next((row[key] for row in collections if row["monthIndex"] == month), 0)
            for month in range(last_month + 1)]} for key in ("contract", "interim", "balance")]}]
    review = deepcopy(((project.get("_meta") or {}).get("feasibilityTermReviews") or {}).get("cashFlowTiming") or {})
    updated["periodBasis"] = {"schemaVersion": "cash-flow-period-basis-v1", "kind": "calendar_monthly",
        "monthIndexOrigin": "explicit_user_analysis_assumption", "baseMonth": timing["baseDate"][:7],
        "sourcePeriodBasis": deepcopy(basis), "sourcePeriodMonthBindings": [
            {"sourcePeriodIndex": row["sourcePeriodIndex"], "monthIndex": mapping[row["sourcePeriodIndex"]]}
            for row in periods], "timingAssumptions": deepcopy(timing), "timingReview": review,
        "analysisOnly": True, "reportEligible": False,
        "warnings": ["사건 날짜·월차·수금은 사용자 분석 가정이며 실제 대출 약정이나 승인된 사실이 아닙니다."]}
    updated.setdefault("sourceMetadata", {})["cashFlowTimingReview"] = {"assumptions": deepcopy(timing), "review": review}
    updated.update(analysisOnly=True, reportEligible=False)
    return updated
