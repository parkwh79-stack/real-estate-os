from __future__ import annotations

from typing import Any, Dict

from agents.orchestration_agent import OrchestrationAgent


def run_pf_review_use_case(
    project_id: str,
    project_data: Dict[str, Any],
    options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run the PF review workflow from API, MCP, or future background jobs."""
    return OrchestrationAgent().run_pf_review(project_id, project_data, options or {})
