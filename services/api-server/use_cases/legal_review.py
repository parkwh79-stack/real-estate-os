from __future__ import annotations

from typing import Any, Dict

from agents.legal_review_agent import LegalReviewAgent
from use_cases.common import deep_merge


def review_legal_and_permits_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run legal and permit review from project state, overrides, and law context."""
    input_data = input_data or {}
    source_data = deep_merge(project_data, input_data.get("project_data", {}))
    return LegalReviewAgent().review(source_data, input_data.get("law_context"))
