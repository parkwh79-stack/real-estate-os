"""Usage insights: distil structured user-activity logs into optimization signals.

This is the analysis layer of the safe, human-in-the-loop improvement loop:
  user_activity_logs (structured, metadata only)
    -> build_usage_insights (deterministic aggregation)
    -> render_optimization_log_markdown (curated report)
    -> docs/ops/optimization-log.md -> backlog.md (human-reviewed)

It NEVER reads sensitive PF payloads. Only whitelisted, non-sensitive fields
(event type, normalized path, status code, duration, counts) are used.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# Workflows the Orchestration Planner can offer (used to spot unused workflows).
KNOWN_WORKFLOWS = [
    "land_review",
    "feasibility",
    "marketability",
    "returns_stress",
    "pf_structure_review",
    "full_pf_review",
]

SLOW_MS = 1000.0
VERY_SLOW_MS = 3000.0

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NUM_RE = re.compile(r"^\d+$")


def normalize_path(path: str | None) -> str:
    """Collapse IDs in a request path so endpoints aggregate together.

    /projects/123/orchestration/run-workflow -> /projects/{id}/orchestration/run-workflow
    """
    if not path:
        return "(unknown)"
    cleaned = _UUID_RE.sub("{id}", str(path).split("?")[0])
    segments = []
    for seg in cleaned.split("/"):
        segments.append("{id}" if _NUM_RE.match(seg) else seg)
    return "/".join(segments) or "/"


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_usage_insights(logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    event_counts: Dict[str, int] = {}
    endpoint_counts: Dict[str, int] = {}
    workflow_counts: Dict[str, int] = {}
    error_counts: Dict[str, int] = {}
    blocker_counts: Dict[str, int] = {}
    # endpoint -> {"count", "slow", "verySlow", "maxMs", "sumMs", "durCount"}
    endpoint_perf: Dict[str, Dict[str, float]] = {}
    users: set = set()
    projects: set = set()
    timestamps: List[str] = []

    for log in logs or []:
        event_type = _safe_str(log.get("eventType")) or "unknown"
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

        path = normalize_path(log.get("path"))
        endpoint_counts[path] = endpoint_counts.get(path, 0) + 1

        if (user_id := _safe_str(log.get("userId"))):
            users.add(user_id)
        if (project_id := _safe_str(log.get("projectId"))):
            projects.add(project_id)
        if (created := _safe_str(log.get("createdAt"))):
            timestamps.append(created)

        status = log.get("statusCode")
        if isinstance(status, (int, float)) and int(status) >= 400:
            error_counts[path] = error_counts.get(path, 0) + 1

        duration = log.get("durationMs")
        if isinstance(duration, (int, float)):
            perf = endpoint_perf.setdefault(path, {"slow": 0, "verySlow": 0, "maxMs": 0.0, "sumMs": 0.0, "durCount": 0})
            perf["sumMs"] += float(duration)
            perf["durCount"] += 1
            perf["maxMs"] = max(perf["maxMs"], float(duration))
            if duration >= SLOW_MS:
                perf["slow"] += 1
            if duration >= VERY_SLOW_MS:
                perf["verySlow"] += 1

        # Safe metadata signals only: chosen/recommended workflow + blocker codes.
        meta = log.get("metadata")
        if isinstance(meta, dict):
            for key in ("workflow", "recommendedWorkflow", "recommended_workflow"):
                if (wf := _safe_str(meta.get(key))):
                    workflow_counts[wf] = workflow_counts.get(wf, 0) + 1
            blockers = meta.get("blockerCodes") or meta.get("blockedReasons")
            if isinstance(blockers, list):
                for blocker in blockers:
                    if (code := _safe_str(blocker)):
                        blocker_counts[code] = blocker_counts.get(code, 0) + 1

    slow_endpoints = []
    for path, perf in endpoint_perf.items():
        if perf["slow"] <= 0 and perf["maxMs"] < SLOW_MS:
            continue
        avg = perf["sumMs"] / perf["durCount"] if perf["durCount"] else 0.0
        slow_endpoints.append({
            "endpoint": path,
            "slowCount": int(perf["slow"]),
            "verySlowCount": int(perf["verySlow"]),
            "avgMs": round(avg, 1),
            "maxMs": round(perf["maxMs"], 1),
        })
    slow_endpoints.sort(key=lambda row: (row["verySlowCount"], row["avgMs"]), reverse=True)

    unused_workflows = [wf for wf in KNOWN_WORKFLOWS if wf not in workflow_counts]

    insights = {
        "schemaVersion": "realytics-usage-insights-v1",
        "totals": {
            "events": len(logs or []),
            "distinctUsers": len(users),
            "distinctProjects": len(projects),
            "since": min(timestamps) if timestamps else None,
            "until": max(timestamps) if timestamps else None,
        },
        "eventTypeCounts": _sorted_rows(event_counts, "eventType"),
        "endpointUsage": _sorted_rows(endpoint_counts, "endpoint")[:15],
        "workflowSelections": _sorted_rows(workflow_counts, "workflow"),
        "unusedWorkflows": unused_workflows,
        "errorHotspots": _sorted_rows(error_counts, "endpoint")[:10],
        "recurringBlockers": _sorted_rows(blocker_counts, "blocker")[:10],
        "slowEndpoints": slow_endpoints[:10],
    }
    insights["recommendations"] = _build_recommendations(insights)
    return insights


def _sorted_rows(counts: Dict[str, int], key_name: str) -> List[Dict[str, Any]]:
    return [
        {key_name: key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


def _build_recommendations(insights: Dict[str, Any]) -> List[Dict[str, str]]:
    recs: List[Dict[str, str]] = []

    for row in insights["slowEndpoints"][:5]:
        if row["verySlowCount"] > 0 or row["avgMs"] >= SLOW_MS:
            recs.append({
                "severity": "high" if row["verySlowCount"] > 0 else "medium",
                "signal": f"{row['endpoint']} 평균 {row['avgMs']}ms (최대 {row['maxMs']}ms, 1s+ {row['slowCount']}건)",
                "suggestion": "느린 엔드포인트 — 비동기 잡 전환 또는 쿼리/계산 최적화 검토",
            })

    for row in insights["errorHotspots"][:5]:
        recs.append({
            "severity": "high",
            "signal": f"{row['endpoint']}에서 4xx/5xx {row['count']}건",
            "suggestion": "오류 빈발 — 입력 검증/예외 처리 또는 API 계약 점검",
        })

    for row in insights["recurringBlockers"][:5]:
        recs.append({
            "severity": "medium",
            "signal": f"승인 blocker '{row['blocker']}' {row['count']}회 반복",
            "suggestion": "자주 막히는 blocker — 가이드/자동화 또는 워크플로우 개선 우선순위化",
        })

    if insights["unusedWorkflows"] and insights["totals"]["events"] > 0:
        recs.append({
            "severity": "low",
            "signal": f"미사용 워크플로우: {', '.join(insights['unusedWorkflows'])}",
            "suggestion": "발견성(추천/메뉴 노출) 또는 필요성 재검토",
        })

    if not recs:
        recs.append({
            "severity": "info",
            "signal": "최적화가 필요한 뚜렷한 신호 없음",
            "suggestion": "사용 데이터가 더 쌓이면 재분석",
        })
    return recs


def render_optimization_log_markdown(insights: Dict[str, Any], generated_at: str) -> str:
    """Render a dated optimization-log section from insights (human-reviewed)."""
    totals = insights["totals"]
    lines: List[str] = []
    lines.append(f"## {generated_at} — 사용 기반 최적화 리포트")
    lines.append("")
    lines.append(
        f"- 이벤트 {totals['events']}건 · 사용자 {totals['distinctUsers']}명 · 프로젝트 {totals['distinctProjects']}건"
        + (f" · 기간 {totals['since']} ~ {totals['until']}" if totals.get("since") else "")
    )
    lines.append("")

    lines.append("### 개선 제안 (사람 검토 후 backlog 반영)")
    for rec in insights["recommendations"]:
        lines.append(f"- **[{rec['severity']}]** {rec['signal']} → {rec['suggestion']}")
    lines.append("")

    if insights["slowEndpoints"]:
        lines.append("### 느린 엔드포인트")
        for row in insights["slowEndpoints"]:
            lines.append(f"- `{row['endpoint']}` 평균 {row['avgMs']}ms / 최대 {row['maxMs']}ms / 1s+ {row['slowCount']}건")
        lines.append("")

    if insights["workflowSelections"]:
        lines.append("### 워크플로우 선택 빈도")
        for row in insights["workflowSelections"]:
            lines.append(f"- {row['workflow']}: {row['count']}회")
        if insights["unusedWorkflows"]:
            lines.append(f"- (미사용) {', '.join(insights['unusedWorkflows'])}")
        lines.append("")

    if insights["recurringBlockers"]:
        lines.append("### 반복 승인 blocker")
        for row in insights["recurringBlockers"]:
            lines.append(f"- {row['blocker']}: {row['count']}회")
        lines.append("")

    lines.append("### 상위 엔드포인트 사용량")
    for row in insights["endpointUsage"][:10]:
        lines.append(f"- `{row['endpoint']}`: {row['count']}회")
    lines.append("")
    lines.append("> 이 리포트는 user_activity_logs(메타데이터)에서 자동 생성됩니다. PF 금액·소유주·계좌 등 민감정보는 포함되지 않습니다. 코드 변경은 사람 검토·테스트를 거칩니다.")
    lines.append("")
    return "\n".join(lines)
