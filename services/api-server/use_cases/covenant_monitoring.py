from __future__ import annotations

from typing import Any, Dict

from agents.covenant_monitoring_agent import CovenantMonitoringAgent
from core.calculations import calculate_pf_metrics


def _sales_rate(project_data: Dict[str, Any]) -> Any:
    for source in (project_data.get("marketability"), project_data.get("expected_results"), project_data.get("monitoring")):
        if isinstance(source, dict):
            for key in ("salesRate", "sales_rate", "currentSalesRate"):
                if isinstance(source.get(key), (int, float)):
                    return source[key]
    return None


def monitor_covenants_use_case(project_data: Dict[str, Any]) -> Dict[str, Any]:
    """Compute live PF covenant monitoring from the project's PF metrics."""
    feasibility = project_data.get("feasibility_input", {}) or {}
    pf = calculate_pf_metrics({
        "tranches": project_data.get("pf_structure", []),
        "total_revenue": feasibility.get("total_revenue", 0),
        "total_cost": feasibility.get("total_cost", 0),
        "finance_cost": feasibility.get("finance_cost", 0),
        "net_operating_income": feasibility.get("net_operating_income", feasibility.get("noi", 0)),
        "debt_service": feasibility.get("debt_service", 0),
    })
    m = pf.get("metrics", {})
    metrics = {
        "ltv": (m.get("ltv") or {}).get("value"),
        "ltc": (m.get("ltc") or {}).get("value"),
        "dscr": (m.get("dscr") or {}).get("value"),
        "exit_sale_rate": (m.get("exit_sale_rate") or {}).get("value"),
        "sales_rate": _sales_rate(project_data),
    }
    thresholds = (project_data.get("covenants") or {}).get("thresholds")
    stress = project_data.get("stressTest")
    return CovenantMonitoringAgent().monitor(metrics, thresholds=thresholds, stress_test=stress)
