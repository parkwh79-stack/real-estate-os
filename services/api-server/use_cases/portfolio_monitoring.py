from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PORTFOLIO_MONITORING_SCHEMA_VERSION = "realytics-portfolio-monitoring-v1"

# Worst-first ordering for portfolio rows and overall status roll-up.
_STATUS_RANK = {"breach": 0, "watch": 1, "ok": 2, "no_data": 3}

# Each monitored metric and whether a higher value is "better" for the lender.
# Mirrors the covenant kinds in CovenantMonitoringAgent (floor => higher better).
_METRIC_HIGHER_BETTER = {
    "dscr": True,        # floor
    "sales_rate": True,  # floor
    "ltv": False,        # ceiling
    "ltc": False,        # ceiling
    "exit_sale_rate": False,  # ceiling
}

_TREND_EPSILON = 1e-6


def build_portfolio_monitoring(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-project covenant monitoring into a portfolio dashboard.

    Pure function. ``entries`` is a list of resolved per-project results:
        {
          "projectId": str,
          "name": str,
          "monitoring": <CovenantMonitoringAgent.monitor() result>,
          "history": Optional[List[{"createdAt": str, "metrics": {key: value}}]],
                     # prior runs, any order; used only to derive a trend arrow
        }

    The router owns the IO (project hydration, covenant monitoring, snapshot
    history); this stays deterministic and trivially testable. No LLM, no
    fabrication — projects with no computable metrics surface as ``no_data``.
    """
    rows: List[Dict[str, Any]] = []
    alert_feed: List[Dict[str, Any]] = []
    status_counts = {"breach": 0, "watch": 0, "ok": 0, "no_data": 0}

    for entry in entries:
        monitoring = entry.get("monitoring") or {}
        project_id = entry.get("projectId")
        name = entry.get("name") or project_id
        status = monitoring.get("overallStatus") or "no_data"
        if status not in status_counts:
            status = "no_data"
        status_counts[status] += 1

        checks = monitoring.get("checks") or []
        alerts = monitoring.get("alerts") or []
        current_metrics = {c.get("key"): c.get("value") for c in checks if c.get("key") is not None}
        critical_alert_count = sum(1 for a in alerts if a.get("severity") == "critical")
        top_alert = _top_alert(alerts)
        trend = _trend(current_metrics, entry.get("history"))

        rows.append({
            "projectId": project_id,
            "name": name,
            "overallStatus": status,
            "breachCount": (monitoring.get("summary") or {}).get("breachCount", 0),
            "watchCount": (monitoring.get("summary") or {}).get("watchCount", 0),
            "alertCount": len(alerts),
            "criticalAlertCount": critical_alert_count,
            "checkedCount": (monitoring.get("summary") or {}).get("checkedCount", len(checks)),
            "metrics": current_metrics,
            "checks": checks,
            "topAlert": top_alert,
            "trend": trend,
            "generatedAt": monitoring.get("generatedAt"),
        })

        for alert in alerts:
            alert_feed.append({
                "projectId": project_id,
                "projectName": name,
                "code": alert.get("code"),
                "severity": alert.get("severity"),
                "covenant": alert.get("covenant"),
                "message": alert.get("message"),
                "recommendedAction": alert.get("recommendedAction"),
            })

    rows.sort(key=lambda r: (_STATUS_RANK.get(r["overallStatus"], 9), -r["criticalAlertCount"], -r["alertCount"]))
    alert_feed.sort(key=lambda a: (0 if a.get("severity") == "critical" else 1, str(a.get("projectName"))))

    monitored = status_counts["breach"] + status_counts["watch"] + status_counts["ok"]
    worst_status = _worst_status(status_counts)
    return {
        "schemaVersion": PORTFOLIO_MONITORING_SCHEMA_VERSION,
        "analysisType": "portfolio_covenant_monitoring",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "projectCount": len(entries),
            "monitoredCount": monitored,
            "statusCounts": status_counts,
            "breachProjectCount": status_counts["breach"],
            "watchProjectCount": status_counts["watch"],
            "totalCriticalAlerts": sum(1 for a in alert_feed if a.get("severity") == "critical"),
            "totalCautionAlerts": sum(1 for a in alert_feed if a.get("severity") == "caution"),
            "worstStatus": worst_status,
        },
        "projects": rows,
        "alertFeed": alert_feed,
    }


def _top_alert(alerts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not alerts:
        return None
    critical = [a for a in alerts if a.get("severity") == "critical"]
    return (critical or alerts)[0]


def _worst_status(status_counts: Dict[str, int]) -> str:
    for status in ("breach", "watch", "ok"):
        if status_counts.get(status):
            return status
    return "no_data"


def _trend(
    current_metrics: Dict[str, Any],
    history: Optional[List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """Direction of each metric vs the most recent prior run, lender-aware.

    Returns {metricKey: {direction: "개선"|"악화"|"유지", from, to, delta}}.
    Only metrics present in both the current run and the latest prior run are
    included — no inference from a single data point.
    """
    prior_metrics = _latest_prior_metrics(history)
    if not prior_metrics:
        return {}
    trend: Dict[str, Dict[str, Any]] = {}
    for key, current_value in current_metrics.items():
        if not _is_num(current_value):
            continue
        prior_value = prior_metrics.get(key)
        if not _is_num(prior_value):
            continue
        delta = float(current_value) - float(prior_value)
        higher_better = _METRIC_HIGHER_BETTER.get(key, True)
        if abs(delta) <= _TREND_EPSILON:
            direction = "유지"
        elif (delta > 0) == higher_better:
            direction = "개선"
        else:
            direction = "악화"
        trend[key] = {
            "direction": direction,
            "from": round(float(prior_value), 4),
            "to": round(float(current_value), 4),
            "delta": round(delta, 4),
        }
    return trend


def _latest_prior_metrics(history: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    if not history:
        return {}
    ordered = sorted(
        (h for h in history if isinstance(h, dict)),
        key=lambda h: h.get("createdAt") or "",
        reverse=True,
    )
    for snapshot in ordered:
        metrics = snapshot.get("metrics")
        if isinstance(metrics, dict) and any(_is_num(v) for v in metrics.values()):
            return metrics
    return {}


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
