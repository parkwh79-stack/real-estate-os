from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from use_cases.common import deep_merge


_DEVELOPMENT_FEASIBILITY_INPUT_KEYS = (
    "project", "land", "regulation", "architectural_plan", "feasibility_input",
    "pf_structure", "sales_assumptions", "cost_assumptions", "cashflow_input",
    "schedule", "project_schedule", "sales_schedule", "salesCollectionSchedule",
    "product_mix", "productMix", "feasibilityStudyV2", "canonicalFeasibilityStudyV2",
    "logistics", "version",
)


def development_feasibility_input_basis(
    project_data: Dict[str, Any],
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Capture the Cost-Schedule Pod's consumed inputs, without shared outputs.

    Both cache boundaries and the Agent receive this same effective input.
    Canonical source/approval data is retained verbatim; it is not promoted.
    """
    source = deep_merge(project_data, overrides)
    basis = {key: deepcopy(source[key]) for key in _DEVELOPMENT_FEASIBILITY_INPUT_KEYS if key in source}
    metadata = source.get("_meta")
    if isinstance(metadata, dict):
        if "parsing_source" in metadata:
            basis["_meta"] = {"parsing_source": deepcopy(metadata["parsing_source"])}
    elif "_meta" in source:
        basis["_meta"] = deepcopy(metadata)
    return basis


def analyze_development_feasibility_use_case(
    project_data: Dict[str, Any],
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Analyze developer/PF feasibility from project state plus request overrides."""
    source_data = deep_merge(project_data, overrides)
    return DevelopmentFeasibilityAgent().analyze(source_data)
