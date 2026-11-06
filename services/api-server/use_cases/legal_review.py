from __future__ import annotations

from typing import Any, Dict

from agents.legal_review_agent import LegalReviewAgent
from use_cases.common import deep_merge
from use_cases.legal_intelligence import resolve_project_legal_context


def review_legal_and_permits_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run a preliminary review through the Legal Intelligence trust boundary."""
    input_data = input_data or {}
    source_data = deep_merge(project_data, input_data.get("project_data", {}))
    candidate_context = (
        input_data.get("lawContext")
        or input_data.get("law_context")
    )
    resolution = resolve_project_legal_context(
        source_data,
        caller_context=(
            candidate_context
            if isinstance(candidate_context, dict)
            else None
        ),
    )
    result = LegalReviewAgent().review(source_data, resolution["lawContext"])
    result["lawReferences"] = list(
        resolution["legalIntelligence"].get("references") or []
    )
    result["citationVerification"] = resolution["legalIntelligence"].get(
        "citationVerification"
    )
    result["legalIntelligence"] = resolution["legalIntelligence"]
    result["analysisOnly"] = True
    result["reportEligible"] = False
    result["humanReviewStatus"] = "pending"
    return result
