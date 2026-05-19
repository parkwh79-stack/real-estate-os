from __future__ import annotations

from typing import Any, Dict

from db.repositories.orchestration_repository import OrchestrationRepository


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


def approve_orchestration_run_by_id_use_case(
    orchestration_repository: OrchestrationRepository,
    project_id: str,
    run_id: str,
    input_data: Dict[str, Any] | None,
    approved_at: str,
) -> Dict[str, Any]:
    """Find, approve, and persist an orchestration run through the repository."""
    run = orchestration_repository.get(project_id, run_id)
    if not run:
        raise LookupError("Orchestration run not found")
    approved = approve_orchestration_run_use_case(run, input_data, approved_at)
    orchestration_repository.save()
    return approved
