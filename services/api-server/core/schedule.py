from __future__ import annotations

from typing import Any, Dict


_EVENT_KEY_FIELDS = ("key", "type", "event", "eventKey", "event_key")
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
        resolved = value.get(field)
        if resolved not in (None, ""):
            return resolved
    return value
