from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from db.postgres_runtime_pool import api_tenant_transaction


SnapshotTransactionFactory = Callable[
    [str],
    AbstractContextManager[Any],
]


PROJECT_SNAPSHOT_SCHEMA_VERSION = "realytics-project-snapshot-v1"
PROJECT_SNAPSHOT_HASH_PREFIX = "sha256:"
_MUTABLE_PROJECT_SNAPSHOT_FIELDS = frozenset(
    {
        "snapshotHash",
        "historicalOutcome",
        "historicalOutcomeHistory",
        "updatedAt",
    }
)


class ProjectSnapshotRepository:
    """Repository for immutable project review snapshots."""

    def __init__(
        self,
        snapshot_store: Dict[str, Dict[str, Any]],
        persist: Callable[[], None],
        persist_snapshot: Callable[[str, Dict[str, Any]], None] | None = None,
    ):
        self.snapshot_store = snapshot_store
        self.persist = persist
        self.persist_snapshot = persist_snapshot

    def create_from_run(
        self,
        project_id: str,
        project_data: Dict[str, Any],
        run: Dict[str, Any],
    ) -> Dict[str, Any]:
        snapshot = build_project_snapshot(project_id, project_data, run)
        self.snapshot_store.setdefault(project_id, {})[snapshot["snapshotId"]] = snapshot
        if self.persist_snapshot is not None:
            self.persist_snapshot(project_id, snapshot)
        else:
            self.persist()
        return snapshot

    def list_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        return [
            self.metadata(snapshot)
            for snapshot in self.snapshot_store.get(project_id, {}).values()
        ]

    def list_all(self) -> List[Dict[str, Any]]:
        snapshots: List[Dict[str, Any]] = []
        for project_snapshots in self.snapshot_store.values():
            snapshots.extend(project_snapshots.values())
        return sorted(
            snapshots,
            key=lambda item: item.get("createdAt") or "",
            reverse=True,
        )

    def get(self, project_id: str, snapshot_id: str) -> Dict[str, Any] | None:
        return self.snapshot_store.get(project_id, {}).get(snapshot_id)

    def upsert_historical_outcome(
        self,
        project_id: str,
        snapshot_id: str,
        outcome_data: Dict[str, Any],
        *,
        updated_by: str = "system",
    ) -> Dict[str, Any] | None:
        snapshot = self.get(project_id, snapshot_id)
        if not snapshot:
            return None
        outcome = normalize_historical_outcome(outcome_data, updated_by=updated_by)
        previous_outcome = snapshot.get("historicalOutcome")
        history = list(snapshot.get("historicalOutcomeHistory") or [])
        if previous_outcome:
            history.append(previous_outcome)
        snapshot["historicalOutcome"] = outcome
        snapshot["historicalOutcomeHistory"] = history[-20:]
        snapshot["updatedAt"] = outcome["updatedAt"]
        self.persist()
        return snapshot

    def latest(self, project_id: str) -> Dict[str, Any] | None:
        snapshots = list(self.snapshot_store.get(project_id, {}).values())
        if not snapshots:
            return None
        return sorted(snapshots, key=lambda item: item.get("createdAt") or "", reverse=True)[0]

    def delete_project(self, project_id: str) -> None:
        self.snapshot_store.pop(project_id, None)
        self.persist()

    def metadata(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "schemaVersion": snapshot.get("schemaVersion"),
            "snapshotHash": snapshot.get("snapshotHash"),
            "snapshotId": snapshot.get("snapshotId"),
            "projectId": snapshot.get("projectId"),
            "runId": snapshot.get("runId"),
            "workflow": snapshot.get("workflow"),
            "status": snapshot.get("status"),
            "createdAt": snapshot.get("createdAt"),
            "source": snapshot.get("source"),
            "artifactKeys": sorted(snapshot.get("agentArtifacts", {}).keys()),
            "handoffCount": len(snapshot.get("handoffs", [])),
            "reconciliationStatus": snapshot.get("reconciliation", {}).get("status"),
            "qualityPass": snapshot.get("quality", {}).get("pass"),
            "outcomeStatus": (snapshot.get("historicalOutcome") or {}).get("outcomeStatus"),
            "repaymentCompleted": (snapshot.get("historicalOutcome") or {}).get("repaymentCompleted"),
            "eodOccurred": (snapshot.get("historicalOutcome") or {}).get("eodOccurred"),
            "lossOccurred": (snapshot.get("historicalOutcome") or {}).get("lossOccurred"),
            "actualSalesRate": (snapshot.get("historicalOutcome") or {}).get("actualSalesRate"),
            "recoveryRate": (snapshot.get("historicalOutcome") or {}).get("recoveryRate"),
        }


class PostgresProjectSnapshotRepository:
    """Tenant-scoped immutable review Snapshot authority."""

    def __init__(
        self,
        *,
        tenant_id: str,
        transaction_factory: SnapshotTransactionFactory = api_tenant_transaction,
    ) -> None:
        normalized_tenant_id = str(tenant_id or "").strip()
        if not normalized_tenant_id:
            raise ValueError("tenant_id is required")
        self.tenant_id = normalized_tenant_id
        self._transaction = transaction_factory

    def create_from_run(
        self,
        project_id: str,
        project_data: Dict[str, Any],
        run: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._transaction(self.tenant_id) as connection:
            return self.create_from_run_in_transaction(
                connection,
                project_id,
                project_data,
                run,
            )

    def create_from_run_in_transaction(
        self,
        connection: Any,
        project_id: str,
        project_data: Dict[str, Any],
        run: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_project_id = _required_text(project_id, "project_id")
        run_id = _required_text(run.get("run_id"), "run.run_id")
        snapshot_id = f"snapshot-{run_id}"
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"project-snapshot:{self.tenant_id}:{normalized_project_id}:{run_id}",),
        )
        existing = self._get_in_transaction(
            connection,
            normalized_project_id,
            snapshot_id,
        )
        if existing is not None:
            if str(existing.get("runId") or "") != run_id:
                raise ValueError("Existing project Snapshot run lineage conflicts")
            if not verify_project_snapshot_hash(existing):
                raise ValueError("Existing project Snapshot hash is invalid")
            return existing

        project_exists = connection.execute(
            """
            SELECT project_id
            FROM realytics.projects
            WHERE tenant_id = %s AND project_id = %s
            """,
            (self.tenant_id, normalized_project_id),
        ).fetchone()
        if project_exists is None:
            raise LookupError("Project not found in normalized PostgreSQL")
        run_exists = connection.execute(
            """
            SELECT run_id
            FROM realytics.orchestrations
            WHERE tenant_id = %s AND project_id = %s AND run_id = %s
            """,
            (self.tenant_id, normalized_project_id, run_id),
        ).fetchone()
        if run_exists is None:
            raise LookupError("Orchestration run not found in normalized PostgreSQL")

        snapshot = build_project_snapshot(
            normalized_project_id,
            project_data,
            run,
        )
        inserted = connection.execute(
            """
            INSERT INTO realytics.project_snapshots (
                tenant_id,
                project_id,
                snapshot_id,
                run_id,
                created_at,
                payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (project_id, snapshot_id) DO NOTHING
            RETURNING snapshot_id
            """,
            (
                self.tenant_id,
                normalized_project_id,
                snapshot["snapshotId"],
                run_id,
                snapshot["createdAt"],
                _snapshot_json(snapshot),
            ),
        ).fetchone()
        if inserted is None:
            existing = self._get_in_transaction(
                connection,
                normalized_project_id,
                snapshot_id,
            )
            if existing is None:
                raise ValueError("Project Snapshot identity conflicts across tenants")
            if existing.get("snapshotHash") != snapshot.get("snapshotHash"):
                raise ValueError("Project Snapshot retry content conflicts")
            return existing
        return deepcopy(snapshot)

    def list_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        normalized_project_id = _required_text(project_id, "project_id")
        with self._transaction(self.tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM realytics.project_snapshots
                WHERE tenant_id = %s AND project_id = %s
                ORDER BY created_at DESC, snapshot_id DESC
                """,
                (self.tenant_id, normalized_project_id),
            ).fetchall()
        return [self.metadata(_snapshot_payload(row[0])) for row in rows]

    def list_all(self) -> List[Dict[str, Any]]:
        with self._transaction(self.tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM realytics.project_snapshots
                WHERE tenant_id = %s
                ORDER BY created_at DESC, snapshot_id DESC
                """,
                (self.tenant_id,),
            ).fetchall()
        return [_snapshot_payload(row[0]) for row in rows]

    def get(self, project_id: str, snapshot_id: str) -> Dict[str, Any] | None:
        with self._transaction(self.tenant_id) as connection:
            return self._get_in_transaction(
                connection,
                _required_text(project_id, "project_id"),
                _required_text(snapshot_id, "snapshot_id"),
            )

    def latest(self, project_id: str) -> Dict[str, Any] | None:
        normalized_project_id = _required_text(project_id, "project_id")
        with self._transaction(self.tenant_id) as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM realytics.project_snapshots
                WHERE tenant_id = %s AND project_id = %s
                ORDER BY created_at DESC, snapshot_id DESC
                LIMIT 1
                """,
                (self.tenant_id, normalized_project_id),
            ).fetchone()
        return _snapshot_payload(row[0]) if row is not None else None

    def upsert_historical_outcome(
        self,
        project_id: str,
        snapshot_id: str,
        outcome_data: Dict[str, Any],
        *,
        updated_by: str = "system",
    ) -> Dict[str, Any] | None:
        normalized_project_id = _required_text(project_id, "project_id")
        normalized_snapshot_id = _required_text(snapshot_id, "snapshot_id")
        with self._transaction(self.tenant_id) as connection:
            snapshot = self._get_in_transaction(
                connection,
                normalized_project_id,
                normalized_snapshot_id,
                for_update=True,
            )
            if snapshot is None:
                return None
            outcome = normalize_historical_outcome(
                outcome_data,
                updated_by=updated_by,
            )
            history = list(snapshot.get("historicalOutcomeHistory") or [])
            if snapshot.get("historicalOutcome"):
                history.append(snapshot["historicalOutcome"])
            snapshot["historicalOutcome"] = outcome
            snapshot["historicalOutcomeHistory"] = history[-20:]
            snapshot["updatedAt"] = outcome["updatedAt"]
            connection.execute(
                """
                UPDATE realytics.project_snapshots
                SET payload_json = %s::jsonb
                WHERE tenant_id = %s AND project_id = %s AND snapshot_id = %s
                """,
                (
                    _snapshot_json(snapshot),
                    self.tenant_id,
                    normalized_project_id,
                    normalized_snapshot_id,
                ),
            )
            return snapshot

    def delete_project(self, project_id: str) -> None:
        raise RuntimeError(
            "Normalized project Snapshots are retained; delete through the governed retention workflow"
        )

    metadata = ProjectSnapshotRepository.metadata

    def _get_in_transaction(
        self,
        connection: Any,
        project_id: str,
        snapshot_id: str,
        *,
        for_update: bool = False,
    ) -> Dict[str, Any] | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = connection.execute(
            """
            SELECT payload_json
            FROM realytics.project_snapshots
            WHERE tenant_id = %s AND project_id = %s AND snapshot_id = %s
            """
            + suffix,
            (self.tenant_id, project_id, snapshot_id),
        ).fetchone()
        return _snapshot_payload(row[0]) if row is not None else None


def _snapshot_payload(value: Any) -> Dict[str, Any]:
    decoded = value
    if isinstance(value, (str, bytes, bytearray)):
        decoded = json.loads(value)
    if not isinstance(decoded, Mapping):
        raise ValueError("Project Snapshot payload must be a JSON object")
    return deepcopy(dict(decoded))


def _snapshot_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def build_project_snapshot(
    project_id: str,
    project_data: Dict[str, Any],
    run: Dict[str, Any],
) -> Dict[str, Any]:
    """Build an auditable snapshot of the project state used by one orchestration run."""
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = run.get("run_id")
    artifacts = run.get("artifacts", {})
    snapshot = {
        "schemaVersion": PROJECT_SNAPSHOT_SCHEMA_VERSION,
        "snapshotId": f"snapshot-{run_id}",
        "projectId": project_id,
        "runId": run_id,
        "workflow": run.get("workflow"),
        "status": run.get("status"),
        "source": "pf_review_orchestration",
        "createdAt": created_at,
        "canonicalProject": _canonical_project(project_data),
        "orchestration": {
            "startedAt": run.get("started_at"),
            "completedAt": run.get("completed_at"),
            "stepKeys": [step.get("key") for step in run.get("steps", [])],
            "errors": run.get("errors", []),
            "approval": run.get("approval"),
        },
        "agentProgress": run.get("agentProgress", {}),
        "handoffs": _handoff_snapshots(run.get("handoffs", [])),
        "agentArtifacts": _artifact_snapshots(artifacts),
        "reconciliation": artifacts.get("reconciliationGate", {}),
        "quality": artifacts.get("qualityCheck", {}),
        "report": _report_snapshot(artifacts.get("phase11Report", {}), artifacts.get("reportRecord", {})),
        "caseComparisonSeed": _case_comparison_seed(project_data, artifacts),
        "historicalOutcome": _historical_outcome_seed(project_data),
    }
    snapshot["snapshotHash"] = project_snapshot_content_hash(snapshot)
    return snapshot


def project_snapshot_content_hash(snapshot: Dict[str, Any]) -> str:
    """Hash only the immutable review content of a project snapshot.

    Historical outcomes are intentionally mutable after review completion, so
    they and their audit-history timestamps are excluded from this seal. Every
    other top-level field is included, making newly added immutable review
    fields fail closed without maintaining a second allowlist.
    """
    immutable_content = {
        key: value
        for key, value in snapshot.items()
        if key not in _MUTABLE_PROJECT_SNAPSHOT_FIELDS
    }
    canonical = json.dumps(
        immutable_content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return PROJECT_SNAPSHOT_HASH_PREFIX + hashlib.sha256(canonical).hexdigest()


def verify_project_snapshot_hash(snapshot: Dict[str, Any]) -> bool:
    """Return whether a snapshot carries a valid seal for its current content."""
    stored_hash = snapshot.get("snapshotHash")
    if not isinstance(stored_hash, str) or not stored_hash.startswith(
        PROJECT_SNAPSHOT_HASH_PREFIX
    ):
        return False
    digest = stored_hash.removeprefix(PROJECT_SNAPSHOT_HASH_PREFIX)
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return False
    try:
        calculated_hash = project_snapshot_content_hash(snapshot)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(stored_hash, calculated_hash)


def normalize_historical_outcome(outcome_data: Dict[str, Any], *, updated_by: str = "system") -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    outcome = {
        "schemaVersion": "realytics-historical-outcome-v1",
        "outcomeStatus": _outcome_status(outcome_data),
        "actualSalesRate": _first(outcome_data, "actualSalesRate", "actual_sales_rate", "realizedSalesRate", "realized_sales_rate"),
        "actualDiscountRate": _first(outcome_data, "actualDiscountRate", "actual_discount_rate", "realizedDiscountRate", "realized_discount_rate"),
        "repaymentCompleted": _first_bool(outcome_data, "repaymentCompleted", "repayment_completed"),
        "repaymentCompletionDate": _first(outcome_data, "repaymentCompletionDate", "repayment_completion_date"),
        "extensionOccurred": _first_bool(outcome_data, "extensionOccurred", "extension_occurred"),
        "extensionCount": _first(outcome_data, "extensionCount", "extension_count"),
        "eodOccurred": _first_bool(outcome_data, "eodOccurred", "eod_occurred"),
        "eodDate": _first(outcome_data, "eodDate", "eod_date"),
        "lossOccurred": _first_bool(outcome_data, "lossOccurred", "loss_occurred"),
        "lossAmount": _first(outcome_data, "lossAmount", "loss_amount"),
        "recoveryRate": _first(outcome_data, "recoveryRate", "recovery_rate"),
        "seniorRecoveryRate": _first(outcome_data, "seniorRecoveryRate", "senior_recovery_rate"),
        "mezzanineRecoveryRate": _first(outcome_data, "mezzanineRecoveryRate", "mezzanine_recovery_rate"),
        "juniorRecoveryRate": _first(outcome_data, "juniorRecoveryRate", "junior_recovery_rate"),
        "actualExitSource": _first(outcome_data, "actualExitSource", "actual_exit_source"),
        "outcomeMemo": _first(outcome_data, "outcomeMemo", "outcome_memo", "memo"),
        "sourceMetadata": outcome_data.get("sourceMetadata") or outcome_data.get("source_metadata") or {},
        "sourceLocators": outcome_data.get("sourceLocators") or outcome_data.get("source_locators") or [],
        "updatedBy": outcome_data.get("updatedBy") or outcome_data.get("updated_by") or updated_by,
        "updatedAt": now,
    }
    outcome["outcomeQuality"] = _outcome_quality(outcome)
    return outcome


def _historical_outcome_seed(project_data: Dict[str, Any]) -> Dict[str, Any] | None:
    raw = project_data.get("historicalOutcome") or project_data.get("historical_outcome")
    if not isinstance(raw, dict):
        return None
    return normalize_historical_outcome(raw, updated_by="project_payload")


def _outcome_status(outcome_data: Dict[str, Any]) -> str:
    explicit = outcome_data.get("outcomeStatus") or outcome_data.get("outcome_status")
    if explicit:
        return str(explicit)
    repayment_completed = _first_bool(outcome_data, "repaymentCompleted", "repayment_completed")
    eod_occurred = _first_bool(outcome_data, "eodOccurred", "eod_occurred")
    loss_occurred = _first_bool(outcome_data, "lossOccurred", "loss_occurred")
    if loss_occurred:
        return "loss_realized"
    if eod_occurred:
        return "eod_occurred"
    if repayment_completed:
        return "repaid"
    return "partial_or_pending"


def _outcome_quality(outcome: Dict[str, Any]) -> Dict[str, Any]:
    required = [
        "actualSalesRate",
        "actualDiscountRate",
        "repaymentCompleted",
        "eodOccurred",
        "lossOccurred",
        "recoveryRate",
    ]
    missing = [key for key in required if outcome.get(key) in {None, ""}]
    return {
        "status": "complete" if not missing else "partial",
        "missingFields": missing,
        "hasSourceMetadata": bool(outcome.get("sourceMetadata")),
        "hasSourceLocators": bool(outcome.get("sourceLocators")),
    }


def _first(data: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data.get(key)
    return None


def _first_bool(data: Dict[str, Any], *keys: str) -> bool | None:
    value = _first(data, *keys)
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "y", "1"}:
            return True
        if lowered in {"false", "no", "n", "0"}:
            return False
    return bool(value)


def _canonical_project(project_data: Dict[str, Any]) -> Dict[str, Any]:
    keys = [
        "project",
        "land",
        "landLedger",
        "regulation",
        "architectural_plan",
        "program",
        "product_mix",
        "productMix",
        "sales_assumptions",
        "cost_assumptions",
        "feasibility_input",
        "pf_structure",
        "cashflow_input",
        "sales_schedule",
        "schedule",
        "covenants",
        "risk_items",
        "_meta",
    ]
    return {
        key: project_data[key]
        for key in keys
        if key in project_data
    }


def _handoff_snapshots(handoffs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "handoffId": handoff.get("handoffId"),
            "handoffType": handoff.get("handoffType"),
            "fromAgent": handoff.get("fromAgent"),
            "toAgent": handoff.get("toAgent"),
            "status": handoff.get("status"),
            "reviewStatus": handoff.get("reviewStatus"),
            "requiresHumanReview": handoff.get("requiresHumanReview"),
            "confidence": handoff.get("confidence"),
            "evidenceIds": handoff.get("evidenceIds", []),
            "blockingIssues": handoff.get("blockingIssues", []),
            "dataLimitations": handoff.get("dataLimitations", []),
            "payload": handoff.get("payload", {}),
        }
        for handoff in handoffs
    ]


def _artifact_snapshots(artifacts: Dict[str, Any]) -> Dict[str, Any]:
    captured_keys = [
        "landAgentReview",
        "developerFeasibility",
        "feasibility",
        "pfStructure",
        "marketEvidenceReview",
        "marketabilityAnalysis",
        "dataQualityReview",
        "reconciliationGate",
        "legalPermitReview",
        "riskAssessment",
        "riskRegister",
        "creditReviewDecision",
        "scenariosEvaluation",
        "evidenceGraph",
        "qualityCheck",
        "approvalReadinessGate",
    ]
    return {
        key: artifacts[key]
        for key in captured_keys
        if key in artifacts
    }


def _report_snapshot(
    phase11_report: Dict[str, Any],
    report_record: Dict[str, Any],
) -> Dict[str, Any]:
    report = phase11_report.get("report", {})
    return {
        "reportId": report_record.get("report_id"),
        "version": report_record.get("version"),
        "template": report.get("template"),
        "sectionKeys": list((report.get("sections") or {}).keys()),
        "qualityChecks": report.get("qualityChecks"),
    }


def _case_comparison_seed(
    project_data: Dict[str, Any],
    artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    feasibility = project_data.get("feasibility_input", {})
    land_review = artifacts.get("landAgentReview", {})
    marketability = artifacts.get("marketabilityAnalysis", {})
    pf_structure = artifacts.get("pfStructure", {})
    return {
        "projectType": (project_data.get("project") or {}).get("type"),
        "location": (project_data.get("land") or {}).get("address"),
        "landAreaSqm": _land_area(project_data),
        "totalRevenue": feasibility.get("total_revenue"),
        "totalCost": feasibility.get("total_cost"),
        "landCost": feasibility.get("land_cost"),
        "pfAmount": sum(
            item.get("amount", 0)
            for item in project_data.get("pf_structure", [])
            if isinstance(item, dict)
        ),
        "ltv": pf_structure.get("ltv"),
        "ltc": pf_structure.get("ltc"),
        "exitSaleRate": pf_structure.get("exit_sale_rate"),
        "marketabilityGrade": marketability.get("grade"),
        "marketabilityScore": marketability.get("score"),
        "landReadinessStatus": land_review.get("status"),
        "reconciliationStatus": artifacts.get("reconciliationGate", {}).get("status"),
    }


def _land_area(project_data: Dict[str, Any]) -> float | None:
    land = project_data.get("land") or {}
    for key in ["area_sqm", "land_area_sqm", "landAreaSqm"]:
        value = land.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None
