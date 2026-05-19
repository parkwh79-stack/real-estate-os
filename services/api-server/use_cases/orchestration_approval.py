from __future__ import annotations

from typing import Any, Dict


def approve_orchestration_run_use_case(
    run: Dict[str, Any],
    input_data: Dict[str, Any] | None,
    approved_at: str,
) -> Dict[str, Any]:
    """Approve a completed orchestration run."""
    if run["status"] not in {"completed", "completed_with_warnings"}:
        raise ValueError("Only completed runs can be approved")

    input_data = input_data or {}
    run["status"] = "approved"
    run["approval"] = {
        "approved_at": approved_at,
        "approved_by": input_data.get("approved_by", "system"),
        "comment": input_data.get("comment", ""),
    }
    return run
