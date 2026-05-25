from __future__ import annotations

from typing import Any, Dict

from db.repositories.orchestration_repository import OrchestrationRepository


def approve_orchestration_run_use_case(
    run: Dict[str, Any],
    input_data: Dict[str, Any] | None,
    approved_at: str,
) -> Dict[str, Any]:
    """Approve a completed orchestration run."""
    if run["status"] not in {"completed", "completed_with_warnings"}:
        raise ValueError("Only completed runs can be approved")

    unresolved_handoffs = unresolved_handoff_reviews(run)
    if unresolved_handoffs:
        labels = ", ".join(
            f"{handoff.get('handoffType')}:{handoff.get('reviewStatus')}"
            for handoff in unresolved_handoffs
        )
        raise ValueError(f"Unresolved agent handoff reviews must be cleared before approval: {labels}")

    unresolved_risks = unresolved_risk_reviews(run)
    if unresolved_risks:
        labels = ", ".join(
            f"{risk.get('riskId')}:{risk.get('reviewStatus', risk.get('status'))}"
            for risk in unresolved_risks[:10]
        )
        raise ValueError(f"Unresolved risk register reviews must be cleared before approval: {labels}")

    input_data = input_data or {}
    run["status"] = "approved"
    run["approval"] = {
        "approved_at": approved_at,
        "approved_by": input_data.get("approved_by", "system"),
        "comment": input_data.get("comment", ""),
    }
    return run


def unresolved_handoff_reviews(run: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Return handoffs that block final run approval."""
    unresolved = []
    for handoff in run.get("handoffs", []):
        review_status = handoff.get("reviewStatus", "not_required")
        status = handoff.get("status")
        requires_review = bool(handoff.get("requiresHumanReview"))
        if review_status in {"rejected", "needs_revision"}:
            unresolved.append(handoff)
        elif requires_review and review_status != "approved":
            unresolved.append(handoff)
        elif status in {"blocked", "needs_review"} and review_status != "approved":
            unresolved.append(handoff)
    return unresolved


def unresolved_risk_reviews(run: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Return risk register rows that block final run approval."""
    risk_register = run.get("artifacts", {}).get("riskRegister") or {}
    unresolved = []
    for risk in risk_register.get("risks", []):
        if not risk.get("requiresHumanReview"):
            continue
        review_status = risk.get("reviewStatus") or risk.get("status") or "open"
        if review_status not in {"approved", "waived", "resolved"}:
            unresolved.append(risk)
    return unresolved


def approve_orchestration_run_by_id_use_case(
    orchestration_repository: OrchestrationRepository,
    project_id: str,
    run_id: str,
    input_data: Dict[str, Any] | None,
    approved_at: str,
) -> Dict[str, Any]:
    """Find, approve, and persist an orchestration run through the repository."""
    run = orchestration_repository.get(project_id, run_id)
    if not run:
        raise LookupError("Orchestration run not found")
    approved = approve_orchestration_run_use_case(run, input_data, approved_at)
    orchestration_repository.save()
    return approved


def review_orchestration_handoff_use_case(
    run: Dict[str, Any],
    handoff_id: str,
    input_data: Dict[str, Any] | None,
    reviewed_at: str,
) -> Dict[str, Any]:
    """Review one typed agent handoff and update related progress review flags."""
    input_data = input_data or {}
    review_status = input_data.get("review_status") or input_data.get("reviewStatus")
    if review_status not in {"approved", "rejected", "needs_revision"}:
        raise ValueError("review_status must be approved, rejected, or needs_revision")

    handoff = next(
        (item for item in run.get("handoffs", []) if item.get("handoffId") == handoff_id),
        None,
    )
    if not handoff:
        raise LookupError("Agent handoff not found")

    reviewer = input_data.get("reviewed_by") or input_data.get("reviewedBy") or "system"
    comment = input_data.get("comment") or input_data.get("reviewComment") or ""
    status_by_review = {
        "approved": "accepted",
        "rejected": "blocked",
        "needs_revision": "needs_review",
    }

    history_entry = {
        "reviewStatus": review_status,
        "reviewedBy": reviewer,
        "reviewedAt": reviewed_at,
        "reviewComment": comment,
    }
    handoff.setdefault("reviewHistory", []).append(history_entry)
    handoff["reviewStatus"] = review_status
    handoff["reviewedBy"] = reviewer
    handoff["reviewedAt"] = reviewed_at
    handoff["reviewComment"] = comment
    handoff["status"] = status_by_review[review_status]
    handoff["requiresHumanReview"] = review_status != "approved"

    _refresh_progress_review_state(run, handoff_id, review_status, reviewed_at)
    return run


def review_orchestration_handoff_by_id_use_case(
    orchestration_repository: OrchestrationRepository,
    project_id: str,
    run_id: str,
    handoff_id: str,
    input_data: Dict[str, Any] | None,
    reviewed_at: str,
) -> Dict[str, Any]:
    """Find, review, and persist one typed handoff in an orchestration run."""
    run = orchestration_repository.get(project_id, run_id)
    if not run:
        raise LookupError("Orchestration run not found")
    reviewed = review_orchestration_handoff_use_case(run, handoff_id, input_data, reviewed_at)
    orchestration_repository.save()
    return reviewed


def review_risk_register_row_use_case(
    run: Dict[str, Any],
    risk_id: str,
    input_data: Dict[str, Any] | None,
    reviewed_at: str,
) -> Dict[str, Any]:
    """Review one risk register row and preserve audit history."""
    input_data = input_data or {}
    review_status = input_data.get("review_status") or input_data.get("reviewStatus")
    if review_status not in {"approved", "waived", "needs_revision", "resolved"}:
        raise ValueError("review_status must be approved, waived, needs_revision, or resolved")

    risk_register = run.get("artifacts", {}).get("riskRegister")
    if not risk_register:
        raise LookupError("Risk register artifact not found")

    risk = next(
        (item for item in risk_register.get("risks", []) if item.get("riskId") == risk_id),
        None,
    )
    if not risk:
        raise LookupError("Risk register row not found")

    reviewer = input_data.get("reviewed_by") or input_data.get("reviewedBy") or "system"
    comment = input_data.get("comment") or input_data.get("reviewComment") or ""
    waiver_reason = input_data.get("waiver_reason") or input_data.get("waiverReason")
    history_entry = {
        "reviewStatus": review_status,
        "reviewedBy": reviewer,
        "reviewedAt": reviewed_at,
        "reviewComment": comment,
    }
    if waiver_reason:
        history_entry["waiverReason"] = waiver_reason

    risk.setdefault("reviewHistory", []).append(history_entry)
    risk["reviewStatus"] = review_status
    risk["reviewedBy"] = reviewer
    risk["reviewedAt"] = reviewed_at
    risk["reviewComment"] = comment
    if waiver_reason:
        risk["waiverReason"] = waiver_reason
    risk["status"] = review_status
    risk["requiresHumanReview"] = review_status not in {"approved", "waived", "resolved"}

    _sync_risk_row_references(run, risk_id, risk)
    _refresh_risk_register_review_summary(risk_register)
    _refresh_credit_review_from_risk_reviews(run)
    return run


def review_risk_register_row_by_id_use_case(
    orchestration_repository: OrchestrationRepository,
    project_id: str,
    run_id: str,
    risk_id: str,
    input_data: Dict[str, Any] | None,
    reviewed_at: str,
) -> Dict[str, Any]:
    """Find, review, and persist one risk register row in an orchestration run."""
    run = orchestration_repository.get(project_id, run_id)
    if not run:
        raise LookupError("Orchestration run not found")
    reviewed = review_risk_register_row_use_case(run, risk_id, input_data, reviewed_at)
    orchestration_repository.save()
    return reviewed


def _refresh_progress_review_state(run: Dict[str, Any], handoff_id: str, review_status: str, reviewed_at: str) -> None:
    for progress in run.get("agentProgress", {}).values():
        if handoff_id not in progress.get("handoffIds", []):
            continue

        related_handoffs = [
            handoff
            for handoff in run.get("handoffs", [])
            if handoff.get("handoffId") in progress.get("handoffIds", [])
        ]
        pending_review = any(
            handoff.get("requiresHumanReview") or handoff.get("reviewStatus") in {"pending", "rejected", "needs_revision"}
            for handoff in related_handoffs
        )
        progress["reviewRequired"] = pending_review

        if review_status == "rejected":
            progress["status"] = "blocked"
            progress.setdefault("blockedTasks", [])
            message = f"Handoff {handoff_id} was rejected."
            if message not in progress["blockedTasks"]:
                progress["blockedTasks"].append(message)
            progress["nextAction"] = "Resolve rejected handoff before final approval."
        elif review_status == "needs_revision":
            progress["status"] = "completed_with_warnings"
            progress.setdefault("blockedTasks", [])
            message = f"Handoff {handoff_id} needs revision."
            if message not in progress["blockedTasks"]:
                progress["blockedTasks"].append(message)
            progress["nextAction"] = "Revise or rerun the upstream agent output."
        elif not pending_review and progress.get("status") in {"blocked", "completed_with_warnings"}:
            progress["status"] = "completed"
            progress["blockedTasks"] = [
                task
                for task in progress.get("blockedTasks", [])
                if handoff_id not in task
            ]
            progress["nextAction"] = "Handoff review completed."
        progress["updatedAt"] = reviewed_at


def _sync_risk_row_references(run: Dict[str, Any], risk_id: str, reviewed_risk: Dict[str, Any]) -> None:
    artifacts = run.get("artifacts", {})
    risk_register = artifacts.get("riskRegister") or {}
    for collection_key in ["topRisks"]:
        for index, risk in enumerate(risk_register.get(collection_key, [])):
            if risk.get("riskId") == risk_id:
                risk_register[collection_key][index] = dict(reviewed_risk)

    credit_review = artifacts.get("creditReviewDecision") or {}
    for risk in credit_review.get("keyRisks", []):
        if risk.get("riskId") == risk_id:
            risk["reviewStatus"] = reviewed_risk.get("reviewStatus")
            risk["reviewedBy"] = reviewed_risk.get("reviewedBy")
            risk["reviewedAt"] = reviewed_risk.get("reviewedAt")
            risk["reviewComment"] = reviewed_risk.get("reviewComment")


def _refresh_risk_register_review_summary(risk_register: Dict[str, Any]) -> None:
    risks = risk_register.get("risks", [])
    status_counts: Dict[str, int] = {}
    unresolved_count = 0
    for risk in risks:
        status = risk.get("reviewStatus") or risk.get("status") or "open"
        status_counts[status] = status_counts.get(status, 0) + 1
        if risk.get("requiresHumanReview") and status not in {"approved", "waived", "resolved"}:
            unresolved_count += 1
    risk_register.setdefault("reviewSummary", {})
    risk_register["reviewSummary"].update({
        "reviewedCount": sum(count for status, count in status_counts.items() if status in {"approved", "waived", "resolved", "needs_revision"}),
        "unresolvedReviewCount": unresolved_count,
        "byReviewStatus": status_counts,
    })


def _refresh_credit_review_from_risk_reviews(run: Dict[str, Any]) -> None:
    credit_review = run.get("artifacts", {}).get("creditReviewDecision")
    if not isinstance(credit_review, dict):
        return
    unresolved = unresolved_risk_reviews(run)
    credit_review.setdefault("humanReview", {})
    credit_review["humanReview"]["unresolvedRiskReviewCount"] = len(unresolved)
    if unresolved:
        credit_review["humanReview"]["required"] = True
        credit_review["humanReview"]["reason"] = f"{len(unresolved)} risk register row(s) still require review."
    elif credit_review.get("decision") == "approve":
        credit_review["humanReview"]["required"] = False
        credit_review["humanReview"]["reason"] = "Risk register reviews are cleared."
