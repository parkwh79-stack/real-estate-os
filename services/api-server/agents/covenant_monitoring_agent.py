from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Dict, List, Optional

from core.financial_input_validation import FinancialInputError

COVENANT_MONITORING_SCHEMA_VERSION = "realytics-covenant-monitoring-v1"

# Default PF covenant thresholds (overridable via project covenants.thresholds).
DEFAULT_THRESHOLDS = {
    "dscr": 1.20,            # floor
    "ltv": 0.70,            # ceiling
    "ltc": 0.85,            # ceiling
    "exit_sale_rate": 0.75,  # ceiling
    "sales_rate": 0.60,     # floor
}

# Each covenant: (metric key, Korean label, kind, recommended action on breach)
_COVENANTS = [
    ("dscr", "DSCR", "floor", "상환재원 보강 또는 Tranche 축소로 부채상환비율 회복"),
    ("ltv", "LTV", "ceiling", "담보 보강·PF 감액·Equity 확충으로 LTV 인하"),
    ("ltc", "LTC", "ceiling", "사업비 통제 또는 자기자본 확충으로 LTC 인하"),
    ("exit_sale_rate", "Exit 분양률", "ceiling", "원금회수 필요 분양률 과다 — 분양가/구조 재검토"),
    ("sales_rate", "분양률", "floor", "분양 촉진·할인분양 트리거 검토로 분양률 제고"),
]

_WATCH_MARGIN = 0.05  # within 5% of threshold => watch


class CovenantMonitoringAgent:
    """Deterministic PF covenant / EOD-trigger monitor.

    Compares current PF metrics (DSCR, LTV, LTC, Exit sale rate, sales rate)
    against covenant thresholds and emits per-covenant status (ok / watch /
    breach), headroom, and alerts — the basis for the deck's Covenant Heatmap
    and EOD trigger monitoring on top of the async run/job foundation.
    """

    analysis_type = "covenant_monitoring_v1"

    def monitor(
        self,
        metrics: Dict[str, Any],
        *,
        thresholds: Optional[Dict[str, Any]] = None,
        stress_test: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        for key, value in (thresholds or {}).items():
            if key in DEFAULT_THRESHOLDS and isinstance(value, (int, float)) and not _is_num(value):
                raise FinancialInputError(f"Covenant threshold {key} must be a finite number.")
        merged = {**DEFAULT_THRESHOLDS, **{k: float(v) for k, v in (thresholds or {}).items() if _is_num(v)}}
        checks: List[Dict[str, Any]] = []
        alerts: List[Dict[str, Any]] = []

        not_applicable: List[str] = []
        invalid_metrics: List[str] = []
        for key, label, kind, action in _COVENANTS:
            value = metrics.get(key)
            if not _is_num(value):
                if isinstance(value, (int, float)):
                    invalid_metrics.append(key)
                    alerts.append({
                        "code": f"COVENANT_{key.upper()}_INVALID_INPUT",
                        "severity": "caution",
                        "covenant": label,
                        "message": f"{label} 입력이 유효한 유한 수가 아니므로 평가할 수 없습니다.",
                        "recommendedAction": "원본 지표를 확인하고 유효한 값으로 다시 계산",
                    })
                continue
            value = float(value)
            # DSCR / ICR-type ratios are only meaningful for income-producing
            # assets. A 0 value means no NOI/debt service was computed (typical
            # for a pure sale/development PF), so it is "not applicable" — not a
            # breach. Avoids false covenant alarms on real sale-type projects.
            if key == "dscr" and value <= 0:
                not_applicable.append(key)
                continue
            threshold = merged[key]
            status, headroom = self._evaluate(value, threshold, kind)
            checks.append({
                "key": key,
                "label": label,
                "kind": kind,
                "value": round(value, 4),
                "threshold": threshold,
                "headroom": round(headroom, 4),
                "status": status,
            })
            if status in ("breach", "watch"):
                alerts.append({
                    "code": f"COVENANT_{key.upper()}_{status.upper()}",
                    "severity": "critical" if status == "breach" else "caution",
                    "covenant": label,
                    "message": f"{label} {self._metric_value(value, key)} (기준 {self._fmt(threshold, kind, key)}) — {'위반' if status=='breach' else '근접'}",
                    "recommendedAction": action,
                })

        # Stress-driven EOD signal: senior principal loss possible under stress.
        senior_break = (stress_test or {}).get("seniorBreakScenarioKey")
        if senior_break:
            alerts.append({
                "code": "STRESS_SENIOR_PRINCIPAL_LOSS",
                "severity": "critical",
                "covenant": "Stress",
                "message": f"스트레스 시나리오 '{senior_break}'에서 Senior 원금 손실이 가능합니다.",
                "recommendedAction": "추가 신용보강·담보 확보 또는 PF 구조 재조정",
            })

        overall = self._overall(checks, alerts)
        return {
            "schemaVersion": COVENANT_MONITORING_SCHEMA_VERSION,
            "analysisType": self.analysis_type,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "overallStatus": overall,
            "thresholds": merged,
            "checks": checks,
            "alerts": alerts,
            "notApplicable": not_applicable,
            "invalidMetrics": invalid_metrics,
            "summary": {
                "checkedCount": len(checks),
                "breachCount": sum(1 for c in checks if c["status"] == "breach"),
                "watchCount": sum(1 for c in checks if c["status"] == "watch"),
                "alertCount": len(alerts),
                "notApplicableCount": len(not_applicable),
            },
        }

    @staticmethod
    def _evaluate(value: float, threshold: float, kind: str) -> tuple[str, float]:
        if kind == "floor":
            headroom = value - threshold
            if value < threshold:
                return "breach", headroom
            if value < threshold * (1 + _WATCH_MARGIN):
                return "watch", headroom
            return "ok", headroom
        # ceiling
        headroom = threshold - value
        if value > threshold:
            return "breach", headroom
        if value > threshold * (1 - _WATCH_MARGIN):
            return "watch", headroom
        return "ok", headroom

    @staticmethod
    def _metric_value(value: float, key: str) -> str:
        return f"{value:.2f}x" if key == "dscr" else f"{value:.2%}"

    @staticmethod
    def _fmt(threshold: float, kind: str, key: str = "") -> str:
        arrow = "≥" if kind == "floor" else "≤"
        return arrow + CovenantMonitoringAgent._metric_value(threshold, key)

    @staticmethod
    def _overall(checks: List[Dict[str, Any]], alerts: List[Dict[str, Any]]) -> str:
        if any(a["severity"] == "critical" for a in alerts) or any(c["status"] == "breach" for c in checks):
            return "breach"
        if any(c["status"] == "watch" for c in checks) or alerts:
            return "watch"
        return "ok" if checks else "no_data"


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
