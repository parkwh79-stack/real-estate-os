from __future__ import annotations

from typing import Any, Dict, Iterable, List


ALLOWED_EVIDENCE_REVIEW_STATUSES = {
    "pending_review",
    "approved",
    "rejected",
    "needs_revision",
    "auto_validated",
}


def list_market_evidence_use_case(
    records: Iterable[Dict[str, Any]],
    run_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Filter and sort persisted market evidence records."""
    result = list(records)
    if run_id:
        result = [record for record in result if record.get("runId") == run_id]
    return sorted(
        result,
        key=lambda item: (
            item.get("createdAt") or "",
            item.get("evidenceType") or "",
            item.get("evidenceId") or "",
        ),
    )


def review_market_evidence_use_case(
    record: Dict[str, Any],
    input_data: Dict[str, Any],
    reviewed_at: str,
) -> Dict[str, Any]:
    """Apply analyst review status to a market evidence record."""
    review_status = input_data.get("review_status") or input_data.get("reviewStatus")
    if review_status not in ALLOWED_EVIDENCE_REVIEW_STATUSES:
        allowed = sorted(ALLOWED_EVIDENCE_REVIEW_STATUSES)
        raise ValueError(f"review_status must be one of {allowed}")

    review_entry = {
        "reviewStatus": review_status,
        "reviewedAt": reviewed_at,
        "reviewedBy": input_data.get("reviewed_by") or input_data.get("reviewedBy") or "system",
        "reviewComment": input_data.get("comment", ""),
    }
    record["reviewStatus"] = review_status
    record["reviewedAt"] = reviewed_at
    record["reviewedBy"] = review_entry["reviewedBy"]
    record["reviewComment"] = review_entry["reviewComment"]
    record.setdefault("reviewHistory", []).append(review_entry)
    return record
