from __future__ import annotations

from typing import Any, Dict

from agents.orchestration_agent import OrchestrationAgent
from db.repositories.orchestration_repository import OrchestrationRepository


def run_pf_review_use_case(
    project_id: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run the PF review workflow from API, MCP, or future background jobs."""
    return OrchestrationAgent().run_pf_review(project_id, project_data, options or {})


def run_and_save_pf_review_use_case(
    orchestration_repository: OrchestrationRepository,
    project_id: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run PF review and persist the run plus derived artifacts through repositories."""
    run = run_pf_review_use_case(project_id, project_data, options)
    return orchestration_repository.save_run(project_id, run)
