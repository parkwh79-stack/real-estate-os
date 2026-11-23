"""Preserve domain execution outcomes at the durable adapter boundary."""

from collections.abc import Mapping

from agent_platform.contracts import AgentRunStatus


def domain_result_status(output: Mapping, *, empty: bool = False) -> AgentRunStatus:
    normalized = {
        key: str(output.get(key) or "").strip().casefold().replace("-", "_")
        for key in ("executionStatus", "status", "analysisStatus", "reviewStatus")
    }
    states = set(normalized.values())
    if empty or output.get("success") is False or states & {"failed", "error", "failure", "timed_out", "timeout", "expired"}:
        return AgentRunStatus.FAILED
    if states & {"cancelled", "canceled", "interrupted"}:
        return AgentRunStatus.CANCELLED
    # A completed risk/legal analysis can find a business blocker. Only an
    # execution-level block stops the runtime; its findings still need review.
    if normalized["executionStatus"] in {"blocked", "blocking", "blocking_issue"} or normalized["status"] == "blocked":
        return AgentRunStatus.BLOCKED
    if states & {"waiting_for_human", "awaiting_approval"}:
        return AgentRunStatus.WAITING_FOR_HUMAN
    if states & {"blocked", "blocking", "blocking_issue", "completed_with_review", "completed_with_warnings", "warning", "needs_review", "needs_human_review", "needs_evidence", "partial", "caution", "rejected", "not_ready"}:
        return AgentRunStatus.COMPLETED_WITH_REVIEW
    if not any(states) and output.get("error"):
        return AgentRunStatus.FAILED
    return AgentRunStatus.COMPLETED
