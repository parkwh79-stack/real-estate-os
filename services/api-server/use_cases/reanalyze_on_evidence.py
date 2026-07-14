"""Smart re-analysis after new evidence (upload) — P0 wiring.

When additional material is uploaded to a project that was **already analyzed**,
this re-runs the pods that still need work — the blocked / not-yet-completed pods
plus the pods the new document type feeds — so the new evidence can unblock them.

Each pod re-run goes through the normal `run_chief_deal_pod_use_case`, which
re-validates parsed facts, attempts auto-supplement (VWorld / 국토부 실거래가),
regenerates the Pod AI Review (AI judgement), and propagates the Event-Bus
recalculation cascade. So this trigger reuses all existing logic — it only adds
the "on new evidence, re-run the still-open pods" wiring that was missing.

Non-fabricating: a pod that still lacks evidence stays ``blocked`` honestly.
"""

from __future__ import annotations

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
}


def affected_pods_for_document(document_type: Optional[str]) -> List[str]:
    return list(_DOC_TYPE_PODS.get(str(document_type or "").strip().lower(), []))


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

    # A brand-new project with no prior analysis and no doc-affected pods → don't
    # auto-run the whole graph; the user starts it via "전체 자동 실행".
    if not completed and not affected:
        return {
            "schemaVersion": "realytics-reanalyze-on-evidence-v1",
            "triggered": False,
            "reason": "no_prior_analysis",
            "targets": [],
            "rows": [],
        }

    rows: List[Dict[str, Any]] = []
    for pod in targets:
        was_completed = pod in completed
        try:
            run_pod(
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
            rows.append({"pod": pod, "before": "completed" if was_completed else "blocked", "after": "completed"})
        except Exception as exc:  # blocked pods raise ValueError with the missing-evidence reason
            rows.append({
                "pod": pod,
                "before": "completed" if was_completed else "blocked",
                "after": "blocked",
                "reason": str(exc)[:200],
            })

    newly_unblocked = [r["pod"] for r in rows if r["before"] == "blocked" and r["after"] == "completed"]
    still_blocked = [r["pod"] for r in rows if r["after"] == "blocked"]
    return {
        "schemaVersion": "realytics-reanalyze-on-evidence-v1",
        "triggered": True,
        "targets": targets,
        "rows": rows,
        "rerunCount": len(rows),
        "newlyUnblocked": newly_unblocked,
        "stillBlocked": still_blocked,
    }
