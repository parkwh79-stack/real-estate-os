from __future__ import annotations

from typing import Any, Dict

from agents.covenant_monitoring_agent import (
    DEFAULT_THRESHOLDS,
    CovenantMonitoringAgent,
)
from core.calculations import calculate_pf_metrics

ALLOWED_THRESHOLD_KEYS = set(DEFAULT_THRESHOLDS)


def normalize_thresholds(raw: Dict[str, Any] | None) -> Dict[str, float]:
    """Keep only the known covenant keys with positive numeric overrides.

    Unknown keys and non-positive / non-numeric values are dropped (not
    fabricated into the covenant model). Returns the overrides only — the agent
    merges them over DEFAULT_THRESHOLDS at evaluation time.
    """
    cleaned: Dict[str, float] = {}
    for key, value in (raw or {}).items():
        if key not in ALLOWED_THRESHOLD_KEYS:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value <= 0:
            continue
        cleaned[key] = float(value)
    return cleaned


def set_monitoring_thresholds_use_case(
    project_repository,
    project_id: str,
    raw_thresholds: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Persist project-specific covenant thresholds under covenants.thresholds.

    Returns the effective thresholds (defaults merged with the saved overrides)
    so the caller can echo what monitoring will use on the next run.
    """
    overrides = normalize_thresholds(raw_thresholds)
    project = project_repository.update(project_id, {"covenants": {"thresholds": overrides}})
    if project is None:
        raise LookupError("Project not found")
    saved = ((project.get("covenants") or {}).get("thresholds")) or {}
    return {
        "schemaVersion": "realytics-monitoring-thresholds-v1",
        "projectId": project_id,
        "defaults": dict(DEFAULT_THRESHOLDS),
        "overrides": overrides,
        "effective": {**DEFAULT_THRESHOLDS, **{k: float(v) for k, v in saved.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}},
    }


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
