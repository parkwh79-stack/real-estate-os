from __future__ import annotations

from typing import Any, Dict

from agents.cash_flow_engine import run_cash_flow_engine
from use_cases.development_feasibility import analyze_development_feasibility_use_case


def run_cash_flow_use_case(
    project_data: Dict[str, Any],
    input_data: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run Cash Flow Engine from a direct request or the canonical feasibility path."""
    input_data = input_data or {}
    request = _cash_flow_request(project_data, input_data)
    return run_cash_flow_engine(request)


def _cash_flow_request(project_data: Dict[str, Any], input_data: Dict[str, Any]) -> Dict[str, Any]:
    if input_data.get("requestType") == "cash_flow_run_request_v1":
        return input_data
    if input_data.get("cashFlowRunRequest", {}).get("requestType") == "cash_flow_run_request_v1":
        return input_data["cashFlowRunRequest"]
    if input_data.get("cashFlowRunRequestDraft", {}).get("requestType") == "cash_flow_run_request_v1":
        return input_data["cashFlowRunRequestDraft"]
    return analyze_development_feasibility_use_case(project_data, input_data)["cashFlowRunRequestDraft"]
