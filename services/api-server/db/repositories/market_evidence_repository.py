from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List


class MarketEvidenceRepository:
    """Repository wrapper for persisted market evidence records."""

    def __init__(self, market_evidence_store: Dict[str, Dict[str, Any]], persist: Callable[[], None]):
        self.market_evidence_store = market_evidence_store
        self.persist = persist

    def values(self, project_id: str) -> Iterable[Dict[str, Any]]:
        return self.market_evidence_store.get(project_id, {}).values()

    def get(self, project_id: str, record_key: str) -> Dict[str, Any] | None:
        return self.market_evidence_store.get(project_id, {}).get(record_key)

    def add_from_run(self, project_id: str, run: Dict[str, Any]) -> None:
        evidence_records = (
            run.get("artifacts", {})
            .get("marketEvidenceReview", {})
            .get("evidenceRecords", [])
        )
        if not evidence_records:
            return

        created_at = datetime.now(timezone.utc).isoformat()
        stored = self.market_evidence_store.setdefault(project_id, {})
        for index, record in enumerate(evidence_records):
            evidence_id = record.get("evidenceId") or f"market-evidence-{index + 1:03d}"
            record_key = f"{run['run_id']}:{evidence_id}"
            validation_status = record.get("validationStatus", "not_checked")
            stored[record_key] = {
                **record,
                "recordKey": record_key,
                "projectId": project_id,
                "runId": run["run_id"],
                "workflow": run.get("workflow"),
                "createdAt": created_at,
                "reviewStatus": "pending_review" if validation_status != "pass" else "auto_validated",
            }

    def save(self) -> None:
        self.persist()

    @staticmethod
    def list_sorted(records: Iterable[Dict[str, Any]], run_id: str | None = None) -> List[Dict[str, Any]]:
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
