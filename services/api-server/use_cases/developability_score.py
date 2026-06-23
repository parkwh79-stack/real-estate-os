from __future__ import annotations

from typing import Any, Dict

from agents.developability_score_agent import DevelopabilityScoreAgent


def score_developability_use_case(project_data: Dict[str, Any]) -> Dict[str, Any]:
    """Composite developability score (land + regulation + feasibility + market)."""
    return DevelopabilityScoreAgent().score(
        project_data,
        return_metrics=project_data.get("returnMetrics"),
    )
