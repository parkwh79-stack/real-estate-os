"""Deterministic law-reference snapshot comparison for Legal Pod reruns."""

from __future__ import annotations

from typing import Any, Dict, Iterable


TRACKED_FIELDS = (
    "title", "lawName", "article", "effectiveDate", "promulgationDate",
    "amendmentDate", "legislativeNoticeDate", "proposalDate", "changeType",
    "changeSummary", "status", "url",
)


def _key(reference: Dict[str, Any]) -> str:
    return str(reference.get("lawId") or reference.get("lawName") or reference.get("title") or reference.get("citation") or reference.get("article") or "unknown-law").strip().lower()


def _references(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for record in records:
        reference = record.get("lawReference") if isinstance(record.get("lawReference"), dict) else record
        if isinstance(reference, dict):
            result[_key(reference)] = reference
    return result


def compare_law_references(previous_records: Iterable[Dict[str, Any]], current_references: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare source-backed law references without interpreting legal meaning."""
    previous = _references(previous_records)
    current = _references(current_references)
    added = [{"lawKey": key, "current": current[key]} for key in sorted(current.keys() - previous.keys())]
    removed = [{"lawKey": key, "previous": previous[key]} for key in sorted(previous.keys() - current.keys())]
    changed = []
    for key in sorted(current.keys() & previous.keys()):
        before, after = previous[key], current[key]
        field_changes = [{"field": field, "previous": before.get(field), "current": after.get(field)} for field in TRACKED_FIELDS if before.get(field) != after.get(field)]
        if field_changes:
            changed.append({"lawKey": key, "changes": field_changes, "previous": before, "current": after})
    return {
        "status": "baseline_created" if not previous else "changed" if added or removed or changed else "unchanged",
        "previousCount": len(previous), "currentCount": len(current),
        "added": added, "removed": removed, "changed": changed,
        "changeCount": len(added) + len(removed) + len(changed),
        "trackedFields": list(TRACKED_FIELDS),
    }
