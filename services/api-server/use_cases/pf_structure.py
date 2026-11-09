"""Prepare and execute the same PF request for API and Pod callers."""
from copy import deepcopy
from typing import Any

from core.calculations import calculate_pf_metrics


def build_pf_structure_input(project: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    feasibility = project.get("feasibility_input") or {}
    finance_cost = overrides.get("finance_cost", feasibility.get("finance_cost", 0))
    # Legacy projects used this alias for rows; current Pod projections also
    # use it for a result object, which must never be treated as loan inputs.
    legacy_tranches = project.get("pfStructure")
    project_tranches = project.get("pf_structure", legacy_tranches if isinstance(legacy_tranches, list) else [])
    return deepcopy({
        "tranches": overrides.get("tranches", overrides.get("pfStructure", project_tranches)),
        "total_revenue": overrides.get("total_revenue", feasibility.get("total_revenue", 0)),
        "total_cost": overrides.get("total_cost", feasibility.get("total_cost", 0)),
        "finance_cost": finance_cost,
        "other_priority_costs": overrides.get("other_priority_costs", finance_cost),
        "net_operating_income": overrides.get("net_operating_income", overrides.get("noi", 0)),
        "debt_service": overrides.get("debt_service", 0),
    })


def execute_pf_structure_input(source: dict[str, Any]) -> dict[str, Any]:
    result = calculate_pf_metrics(source)
    return {**result, **{key: result["metrics"][key]["value"] for key in ("ltv", "ltc", "dscr", "exit_sale_rate")}}
