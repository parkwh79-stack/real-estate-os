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


def plan_orchestration_use_case(
    project_id: str,
    project_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Return the intent menu (available workflows, missing data, recommendation)."""
    from agents.orchestration_planner_agent import OrchestrationPlannerAgent

    return OrchestrationPlannerAgent().plan(project_id, project_data)


def run_workflow_use_case(
    project_id: str,
    project_data: Dict[str, Any],
    workflow: str,
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run only the agents required by a user-selected workflow."""
    return OrchestrationAgent().run_workflow(project_id, project_data, workflow, options or {})


def run_and_save_pf_review_use_case(
    orchestration_repository: OrchestrationRepository,
    project_id: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run PF review and persist the run plus derived artifacts through repositories."""
    run = run_pf_review_use_case(project_id, project_data, options)
    return orchestration_repository.save_run(project_id, run, project_data)
