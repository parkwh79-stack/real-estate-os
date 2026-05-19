from __future__ import annotations

from typing import Any, Dict

from agents.marketability_agent import MarketabilityAgent
from use_cases.common import deep_merge


def analyze_marketability_use_case(
    project_data: Dict[str, Any],
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run marketability review from project state plus optional overrides."""
    source_data = deep_merge(project_data, overrides)
    return MarketabilityAgent().analyze(source_data)
