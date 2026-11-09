from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any, Callable, Dict, Iterable, List


class MarketEvidenceRepository:
    """Repository wrapper for persisted market evidence records."""

    def __init__(self, market_evidence_store: Dict[str, Dict[str, Any]], persist: Callable[[], None],
                 persist_records: Callable[[str, List[Dict[str, Any]]], None] | None = None):
        self.market_evidence_store = market_evidence_store
        self.persist = persist
        self.persist_records = persist_records

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
        changed = []
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
            changed.append(stored[record_key])
        if self.persist_records is not None:
            self.persist_records(project_id, changed)

    def save(self, project_id: str | None = None, record: Dict[str, Any] | None = None) -> None:
        if self.persist_records is not None and project_id is not None and record is not None:
            self.persist_records(project_id, [record])
        else:
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


class MarketEvidenceConflict(RuntimeError):
    """Another reviewer or run changed the selected market evidence."""


class PostgresMarketEvidenceRepository(MarketEvidenceRepository):
    """Read and review existing run evidence through the tenant's API pool."""

    def __init__(self, *, tenant_id: str, transaction_factory=None):
        from db.postgres_runtime_pool import api_tenant_transaction

        self.tenant_id = str(tenant_id or "").strip()
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        self._transaction = transaction_factory or api_tenant_transaction
        self._expected_payloads: dict[tuple[str, str], dict[str, Any]] = {}

    def _record(self, row, project_id: str) -> Dict[str, Any]:
        record_key, payload = row
        record = json.loads(payload) if isinstance(payload, str) else deepcopy(payload)
        if (
            not isinstance(record, dict)
            or record.get("projectId") != project_id
            or record.get("recordKey") != record_key
            or record.get("tenantId") not in (None, self.tenant_id)
        ):
            raise ValueError("Stored market evidence lineage does not match")
        return record

    def values(self, project_id: str) -> Iterable[Dict[str, Any]]:
        with self._transaction(self.tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT record_key, payload_json FROM realytics.market_evidence
                WHERE tenant_id = %s AND project_id = %s
                ORDER BY created_at, record_key
                """,
                (self.tenant_id, project_id),
            ).fetchall()
        return [self._record(row, project_id) for row in rows]

    def get(self, project_id: str, record_key: str) -> Dict[str, Any] | None:
        with self._transaction(self.tenant_id) as connection:
            row = connection.execute(
                """
                SELECT record_key, payload_json FROM realytics.market_evidence
                WHERE tenant_id = %s AND project_id = %s AND record_key = %s
                """,
                (self.tenant_id, project_id, record_key),
            ).fetchone()
        if row is None:
            return None
        record = self._record(row, project_id)
        self._expected_payloads[(project_id, record_key)] = deepcopy(record)
        return record

    def save(self, project_id: str | None = None, record: Dict[str, Any] | None = None) -> None:
        if not project_id or not isinstance(record, dict):
            raise ValueError("An existing market evidence record is required")
        record_key = record.get("recordKey")
        expected = self._expected_payloads.get((project_id, record_key))
        if expected is None:
            raise ValueError("Read the market evidence before reviewing it")
        self._record((record_key, record), project_id)
        with self._transaction(self.tenant_id) as connection:
            written = connection.execute(
                """
                UPDATE realytics.market_evidence SET payload_json = %s::jsonb
                WHERE tenant_id = %s AND project_id = %s AND record_key = %s
                    AND payload_json = %s::jsonb
                RETURNING record_key
                """,
                (
                    json.dumps(record, ensure_ascii=False),
                    self.tenant_id, project_id, record_key,
                    json.dumps(expected, ensure_ascii=False),
                ),
            ).fetchone()
            if written is None:
                raise MarketEvidenceConflict("Market evidence changed; reload before reviewing")
        self._expected_payloads[(project_id, record_key)] = deepcopy(record)
