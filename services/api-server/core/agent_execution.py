"""Execution success is independent of a financial or human review decision."""
from collections.abc import Mapping
from typing import Any


SUCCESSFUL_RUNTIME_STATUSES = frozenset({"completed", "completed_with_review"})


def require_successful_runtime(envelope: Mapping[str, Any], *, label: str) -> None:
    status = str(envelope.get("status") or "missing").casefold()
    if status not in SUCCESSFUL_RUNTIME_STATUSES:
        # Never embed provider output or exception payloads in persisted errors.
        raise RuntimeError(f"{label} runtime did not complete successfully ({status})")
