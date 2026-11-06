from __future__ import annotations

from datetime import date
import math
import re
from typing import Any, Dict

from core.financial_input_validation import FinancialInputError


_EVENT_KEY_FIELDS = ("key", "type", "event", "eventKey", "event_key", "name")
_EVENT_VALUE_FIELDS = ("planned_date", "plannedDate", "date", "value")


def schedule_event_map(schedule: Any) -> Dict[str, Any]:
    """Adapt canonical event lists and legacy schedule objects to one lookup map.

    The canonical feasibility schema stores schedule rows as a list so each row
    can retain provenance. Older deterministic agents use a key/value mapping.
    This adapter lets those agents read dates without mutating or flattening the
    source record kept on the project.
    """
    if isinstance(schedule, dict):
        return {
            str(key): _event_value(value)
            for key, value in schedule.items()
        }
    if not isinstance(schedule, list):
        return {}

    result: Dict[str, Any] = {}
    for item in schedule:
        if not isinstance(item, dict):
            continue
        key = next((item.get(field) for field in _EVENT_KEY_FIELDS if item.get(field)), None)
        if key is None:
            continue
        value = _event_value(item)
        if value not in (None, ""):
            result[str(key)] = value
    return result


def _event_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for field in _EVENT_VALUE_FIELDS:
        if field in value:
            return _planned_schedule_value(value[field])
    return value


def schedule_month_indices(schedule: Any) -> Dict[str, int]:
    """Resolve supplied dates or M-month offsets without inventing an epoch.

    Calendar-only schedules keep construction start as their calculation origin
    (or the earliest supplied date when construction start is absent). Mixing
    calendar dates and offsets requires a row containing both a date and an
    explicit month index, so that the caller supplies their relationship.
    """
    if schedule in (None, {}, []):
        return {}
    if isinstance(schedule, dict):
        rows = [(str(key), value) for key, value in schedule.items()]
    elif isinstance(schedule, list):
        rows = []
        for item in schedule:
            if not isinstance(item, dict):
                continue
            key = next((item.get(field) for field in _EVENT_KEY_FIELDS if item.get(field) not in (None, "")), None)
            if key is not None:
                if not isinstance(key, str):
                    raise _schedule_error("일정 항목 key는 문자열이어야 합니다.")
                rows.append((key, item))
    else:
        raise _schedule_error("일정은 날짜 또는 월차를 가진 항목 목록이어야 합니다.")

    points: Dict[str, tuple[int | None, int | None]] = {}
    exact_dates: Dict[str, date] = {}
    for key, value in rows:
        planned = _planned_schedule_value(value)
        month = None
        if isinstance(value, dict):
            for alias in ("month_index", "monthIndex"):
                if alias in value:
                    if value[alias] is not None:
                        month = _month_offset(value[alias], key)
                    break
        calendar_month = None
        if planned not in (None, ""):
            if isinstance(planned, str) and re.fullmatch(r"M\d+", planned, re.IGNORECASE):
                offset = _month_offset(planned[1:], key)
            elif isinstance(planned, (int, float)) and not isinstance(planned, bool):
                offset = _month_offset(planned, key)
            else:
                offset = None
                calendar_month = _calendar_month(planned, key)
                if len(planned) == 10:
                    exact_dates[key] = date.fromisoformat(planned)
            if offset is not None:
                if month is not None and month != offset:
                    raise _schedule_error(f"{key}: 날짜의 월차와 month_index가 일치하지 않습니다.")
                month = offset
        if calendar_month is not None or month is not None:
            point = (calendar_month, month)
            if key in points and points[key] != point:
                raise _schedule_error(f"{key}: 서로 다른 일정 값이 중복되어 있습니다.")
            points[key] = point

    calendar_points = {key: day for key, (day, _) in points.items() if day is not None}
    offsets = {key: month for key, (_, month) in points.items() if month is not None}
    if not calendar_points:
        result = offsets
    else:
        anchors = {day - month for day, month in points.values() if day is not None and month is not None}
        if len(anchors) > 1:
            raise _schedule_error("일정 날짜와 월차의 기준월이 서로 일치하지 않습니다.")
        if offsets and not anchors:
            raise FinancialInputError(
                "달력 날짜와 M월차를 함께 사용하려면 같은 일정에 날짜와 month_index로 기준을 지정해 주세요.",
                code="schedule_reference_required",
            )
        origin = next(iter(anchors)) if anchors else calendar_points.get("construction_start", min(calendar_points.values()))
        result = {key: day - origin if day is not None else month for key, (day, month) in points.items()}
    if (
        "construction_start" in result and "completion" in result
        and result["completion"] < result["construction_start"]
    ) or (
        "construction_start" in exact_dates and "completion" in exact_dates
        and exact_dates["completion"] < exact_dates["construction_start"]
    ):
        raise _schedule_error("준공 일정은 착공 일정 이전일 수 없습니다.")
    return result


def _planned_schedule_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    # A canonical clear wins over old aliases, as in the reviewed input writer.
    for key in _EVENT_VALUE_FIELDS:
        if key in value:
            return _planned_schedule_value(value[key])
    return None


def _month_offset(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise _schedule_error(f"{key}: 월차는 0 이상의 정수여야 합니다.")
    if isinstance(value, str) and re.fullmatch(r"\d+", value):
        return int(value)
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and math.isfinite(value) and value >= 0 and int(value) == value:
        return int(value)
    raise _schedule_error(f"{key}: 월차는 0 이상의 정수여야 합니다.")


def _calendar_month(value: Any, key: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", value):
        raise _schedule_error(f"{key}: 유효한 ISO 날짜(YYYY-MM-DD 또는 YYYY-MM)나 M월차를 입력해 주세요.")
    try:
        parsed = date.fromisoformat(value if len(value) == 10 else f"{value}-01")
    except ValueError as exc:
        raise _schedule_error(f"{key}: 존재하지 않는 달력 날짜입니다.") from exc
    return parsed.year * 12 + parsed.month - 1


def _schedule_error(message: str) -> FinancialInputError:
    return FinancialInputError(message, code="invalid_schedule")
