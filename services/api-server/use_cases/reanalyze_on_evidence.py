"""Smart re-analysis after new evidence (upload) — P0 wiring.

When additional material is uploaded to a project that was **already analyzed**,
this re-runs the pods that still need work — the blocked / not-yet-completed pods
plus the pods the new document type feeds — so the new evidence can unblock them.

The caller supplies the validated runner and persistence sinks. Newly dirtied
downstream Pods are consumed in bounded rounds; unresolved work stays visible.

Non-fabricating: a pod that still lacks evidence stays ``blocked`` honestly.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, List, Optional, Set

ALL_PODS = ["land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"]

# New document type → pods it directly feeds (so they re-run even if once completed).
_DOC_TYPE_PODS: Dict[str, List[str]] = {
    "land_ledger": ["land_legal"],
    "land_ledger_import": ["land_legal"],
    "registry": ["land_legal"],
    "land_evidence": ["land_legal"],
    "feasibility_workbook": ["feasibility_cashflow", "cost_schedule"],
    "feasibility_workbook_validate": ["feasibility_cashflow", "cost_schedule"],
    "market_evidence": ["market_product"],
    "market_evidence_attach": ["market_product"],
    "market": ["market_product"],
    "business_plan": ["land_legal", "market_product", "feasibility_cashflow"],
    "permit": ["land_legal"],
    "permit_document": ["land_legal"],
    "basic_architectural_plan": ["land_legal", "feasibility_cashflow"],
}


def affected_pods_for_document(document_type: Optional[str]) -> List[str]:
    return list(_DOC_TYPE_PODS.get(str(document_type or "").strip().lower(), []))


def _pod_state(project: Dict[str, Any], pod: str) -> Dict[str, Any]:
    return (project.get("chief_orchestration_state") or {}).get("podRuns", {}).get(pod) or {}


def _execution_status(result: Any, current: Dict[str, Any], previous: Dict[str, Any]) -> str:
    """An exception-free call is not proof of a new successful execution."""
    returned = result.get("status") if isinstance(result, dict) else None
    statuses = {returned, current.get("status")} - {None, ""}
    for status in ("failed", "blocked", "needs_recalculation", "awaiting_review", "pending_review", "running"):
        if status in statuses:
            return status
    if "completed" not in statuses:
        return "awaiting_result"
    if current and current == previous:
        # A prior completed artifact must not satisfy a refresh of new evidence.
        return "needs_recalculation"
    return "completed"


def reanalyze_after_evidence_use_case(
    project_id: str,
    project_data: Dict[str, Any],
    *,
    completed_pod_keys: Optional[Set[str]] = None,
    changed_document_types: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None,
    run_pod: Optional[Callable[..., Dict[str, Any]]] = None,
    artifact_sink: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    review_sink: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    event_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    calculation_sink: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    handoff_sink: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    max_recalculation_rounds: int = 2,
) -> Dict[str, Any]:
    """Re-run the still-open + document-affected pods after new evidence arrives."""
    completed = set(completed_pod_keys or set())
    options = options or {}
    if run_pod is None:  # lazy import avoids a circular import at module load
        from use_cases.chief_deal_orchestration import run_chief_deal_pod_use_case as run_pod  # type: ignore

    affected: Set[str] = set()
    for doc_type in changed_document_types or []:
        affected.update(affected_pods_for_document(doc_type))

    # Never-completed pods (blocked / not yet run) are the primary re-run targets;
    # document-affected pods re-run too so fresh evidence is folded in.
    not_completed = [pod for pod in ALL_PODS if pod not in completed]
    targets = [pod for pod in ALL_PODS if pod in (set(not_completed) | affected)]
    current_runs = (project_data.get("chief_orchestration_state") or {}).get("podRuns") or {}
    targets = list(dict.fromkeys([*targets, *[
        pod for pod in ALL_PODS
        if (current_runs.get(pod) or {}).get("status") in {"blocked", "failed", "needs_recalculation"}
    ]]))
    # Explicit manual refresh after prior analysis must not silently do nothing.
    if completed and not changed_document_types and not targets:
        targets = [pod for pod in ALL_PODS if pod in completed]

    # A brand-new project with no prior analysis and no doc-affected pods → don't
    # auto-run the whole graph; the user starts it via "전체 자동 실행".
    if not completed and not affected and not current_runs:
        return {
            "schemaVersion": "realytics-reanalyze-on-evidence-v1",
            "triggered": False,
            "reason": "no_prior_analysis",
            "targets": [],
            "rows": [],
        }

    rows: List[Dict[str, Any]] = []
    queue = list(targets)
    attempted = set()
    for pod in queue:
        attempted.add(pod)
        was_completed = pod in completed
        previous = deepcopy(_pod_state(project_data, pod))
        try:
            result = run_pod(
                project_id,
                pod,
                project_data,
                options,
                artifact_sink=artifact_sink,
                review_sink=review_sink,
                event_sink=event_sink,
                calculation_sink=calculation_sink,
                handoff_sink=handoff_sink,
            )
            rows.append({"pod": pod, "before": previous.get("status") or ("completed" if was_completed else "blocked"),
                         "after": _execution_status(result, _pod_state(project_data, pod), previous)})
        except ValueError as exc:
            rows.append({
                "pod": pod,
                "before": "completed" if was_completed else "blocked",
                "after": "blocked",
                "reason": "pod_input_validation_failed",
            })
        except Exception:
            rows.append({"pod": pod, "before": "completed" if was_completed else "blocked",
                         "after": "failed", "reason": "pod_execution_failed"})

    # Re-read current state after every round; the first run may invalidate Pods
    # that were completed before this upload. Never declare bounded leftovers done.
    for _round in range(max(0, min(int(max_recalculation_rounds), 5))):
        runs = (project_data.get("chief_orchestration_state") or {}).get("podRuns") or {}
        pending = [pod for pod in ALL_PODS if (runs.get(pod) or {}).get("status") == "needs_recalculation"]
        if not pending:
            break
        for pod in pending:
            attempted.add(pod)
            previous = deepcopy(_pod_state(project_data, pod))
            try:
                result = run_pod(project_id, pod, project_data, options,
                        artifact_sink=artifact_sink, review_sink=review_sink, event_sink=event_sink,
                        calculation_sink=calculation_sink, handoff_sink=handoff_sink)
                rows.append({"pod": pod, "before": "needs_recalculation",
                             "after": _execution_status(result, _pod_state(project_data, pod), previous)})
            except ValueError as exc:
                rows.append({"pod": pod, "before": "needs_recalculation", "after": "blocked", "reason": "pod_input_validation_failed"})
            except Exception:
                rows.append({"pod": pod, "before": "needs_recalculation", "after": "failed", "reason": "pod_execution_failed"})

    latest_rows = {row["pod"]: row for row in rows}
    runs = (project_data.get("chief_orchestration_state") or {}).get("podRuns") or {}
    # Later Pods can invalidate an earlier result, even in the last allowed round.
    for pod, row in latest_rows.items():
        status = (runs.get(pod) or {}).get("status")
        if row["after"] == "completed" and status and status != "completed":
            row["after"] = status
    pending = [pod for pod in ALL_PODS if (runs.get(pod) or {}).get("status") == "needs_recalculation"
               or latest_rows.get(pod, {}).get("after") == "needs_recalculation"]
    newly_unblocked = [pod for pod, row in latest_rows.items() if pod not in completed and row["after"] == "completed" and pod not in pending]
    still_blocked = [pod for pod, row in latest_rows.items() if row["after"] == "blocked"]
    return {
        "schemaVersion": "realytics-reanalyze-on-evidence-v1",
        "triggered": True,
        "targets": [pod for pod in ALL_PODS if pod in attempted],
        "rows": rows,
        "rerunCount": len(rows),
        "newlyUnblocked": newly_unblocked,
        "stillBlocked": still_blocked,
        "failedPods": [pod for pod, row in latest_rows.items() if row["after"] == "failed"],
        "pendingRecalculation": pending,
        "awaitingResult": [pod for pod, row in latest_rows.items() if row["after"] in {"awaiting_result", "running"}],
        "awaitingReview": [pod for pod, row in latest_rows.items() if row["after"] in {"awaiting_review", "pending_review"}],
        "cascadeComplete": not pending and bool(latest_rows) and all(row["after"] == "completed" for row in latest_rows.values()),
    }
