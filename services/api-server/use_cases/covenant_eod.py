"""Covenant / EOD evaluation use case.

Runs the deterministic feasibility analysis for the project, then derives covenant
thresholds + EOD conditions from the resulting numbers (see CovenantEodAgent).
Read-only / on-demand, like development-feasibility analyze — no persistence here.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agents.covenant_eod_agent import CovenantEodAgent
from agents.development_feasibility_agent import DevelopmentFeasibilityAgent
from use_cases.common import deep_merge


def _resolve_asset_type(project_data: Dict[str, Any]) -> str:
    agent = DevelopmentFeasibilityAgent()
    product_mix = project_data.get("product_mix") or project_data.get("productMix") or []
    if product_mix and isinstance(product_mix[0], dict):
        item = product_mix[0]
        return agent._asset_type(item.get("productType") or item.get("asset_type") or item.get("type"))
    return agent._asset_type((project_data.get("project", {}) or {}).get("type"))


def evaluate_covenant_eod_use_case(
    project_data: Dict[str, Any],
    overrides: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Derive covenant + EOD definitions from the project's deterministic feasibility."""
    source_data = deep_merge(project_data, overrides)
    analysis = DevelopmentFeasibilityAgent().analyze(source_data)
    asset_type = _resolve_asset_type(source_data)
    pf_structure: List[Dict[str, Any]] = source_data.get("pf_structure") or []
    project_name = (source_data.get("project", {}) or {}).get("name")
    return CovenantEodAgent().build(
        analysis,
        asset_type=asset_type,
        pf_structure=pf_structure,
        policy=policy or (overrides or {}).get("covenantPolicy"),
        project_name=project_name,
    )
