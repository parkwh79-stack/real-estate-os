from __future__ import annotations

from typing import Any, Dict

from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from use_cases.common import deep_merge


def analyze_development_feasibility_use_case(
    project_data: Dict[str, Any],
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Analyze developer/PF feasibility from project state plus request overrides."""
    source_data = deep_merge(project_data, overrides)
    return DevelopmentFeasibilityAgent().analyze(source_data)
