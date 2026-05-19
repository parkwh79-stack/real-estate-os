from __future__ import annotations

from typing import Any, Dict, Iterable, List

from db.repositories.market_evidence_repository import MarketEvidenceRepository


ALLOWED_EVIDENCE_REVIEW_STATUSES = {
    "pending_review",
    "approved",
    "rejected",
    "needs_revision",
    "auto_validated",
}


def list_market_evidence_use_case(
    records_or_repository: Iterable[Dict[str, Any]] | MarketEvidenceRepository,
    project_id: str | None = None,
    run_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Filter and sort persisted market evidence records."""
    if isinstance(records_or_repository, MarketEvidenceRepository):
        if not project_id:
            raise ValueError("project_id is required when using MarketEvidenceRepository")
        records = records_or_repository.values(project_id)
    else:
        records = records_or_repository
    return MarketEvidenceRepository.list_sorted(records, run_id)


def review_market_evidence_use_case(
    record_or_repository: Dict[str, Any] | MarketEvidenceRepository,
    input_data: Dict[str, Any],
    reviewed_at: str,
    project_id: str | None = None,
) -> Dict[str, Any]:
    """Apply analyst review status to a market evidence record."""
    if isinstance(record_or_repository, MarketEvidenceRepository):
        if not project_id:
            raise ValueError("project_id is required when using MarketEvidenceRepository")
        record_key = input_data.get("record_key") or input_data.get("recordKey")
        if not record_key:
            raise KeyError("record_key is required")
        record = record_or_repository.get(project_id, record_key)
        if not record:
            raise LookupError("Market evidence record not found")
    else:
        record = record_or_repository

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
    if isinstance(record_or_repository, MarketEvidenceRepository):
        record_or_repository.save()
    return record
