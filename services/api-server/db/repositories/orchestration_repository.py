from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

from db.repositories.market_evidence_repository import MarketEvidenceRepository
from db.postgres_runtime_pool import api_tenant_transaction
from db.repositories.project_snapshot_repository import (
    PostgresProjectSnapshotRepository,
    ProjectSnapshotRepository,
)
from db.repositories.report_repository import (
    PostgresReportRepository,
    ReportRepository,
)
from storage import (
    get_orchestration_run_record,
    list_orchestration_run_records,
    upsert_assumption_proposal_record,
    upsert_evidence_graph_record,
    upsert_risk_register_record,
)


OrchestrationTransactionFactory = Callable[
    [str],
    AbstractContextManager[Any],
]


class OrchestrationRepository:
    """Repository wrapper for orchestration runs and related saved artifacts."""

    def __init__(
        self,
        orchestration_store: Dict[str, Dict[str, Any]],
        report_repository: ReportRepository,
        market_evidence_repository: MarketEvidenceRepository,
        project_snapshot_repository: ProjectSnapshotRepository,
        persist: Callable[[], None],
        persist_run: Callable[[str, str], None] | None = None,
    ):
        self.orchestration_store = orchestration_store
        self.report_repository = report_repository
        self.market_evidence_repository = market_evidence_repository
        self.project_snapshot_repository = project_snapshot_repository
        self.persist = persist
        self.persist_run = persist_run

    def _persist_run(self, project_id: str, run_id: str) -> None:
        if self.persist_run is not None:
            self.persist_run(project_id, run_id)
        else:
            self.persist()

    def save_run(
        self,
        project_id: str,
        run: Dict[str, Any],
        project_data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        self.orchestration_store.setdefault(project_id, {})[run["run_id"]] = run
        self.market_evidence_repository.add_from_run(project_id, run)
        if isinstance(run.get("artifacts", {}).get("riskRegister"), dict):
            upsert_risk_register_record(project_id, run["run_id"], run["artifacts"]["riskRegister"])
        if isinstance(run.get("artifacts", {}).get("evidenceGraph"), dict):
            upsert_evidence_graph_record(project_id, run["run_id"], run["artifacts"]["evidenceGraph"])
        proposal_artifact = run.get("artifacts", {}).get("assumptionProposals") or {}
        for proposal in proposal_artifact.get("proposals") or []:
            if isinstance(proposal, dict):
                upsert_assumption_proposal_record(proposal)
        self._persist_run(project_id, run["run_id"])

        if "phase11Report" in run.get("artifacts", {}):
            report_artifact = run["artifacts"]["phase11Report"]
            report_record = self.report_repository.create(
                project_id,
                report_artifact["report"],
                report_artifact["report_markdown"],
                run["run_id"],
            )
            run["artifacts"]["reportRecord"] = self.report_repository.metadata(report_record)
            self._persist_run(project_id, run["run_id"])

        if project_data is not None:
            snapshot = self.project_snapshot_repository.create_from_run(project_id, project_data, run)
            run["artifacts"]["projectSnapshot"] = self.project_snapshot_repository.metadata(snapshot)
            self._persist_run(project_id, run["run_id"])

        return run

    def list_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        project_runs = self.orchestration_store.setdefault(project_id, {})
        for persisted_run in list_orchestration_run_records(project_id):
            run_id = persisted_run.get("run_id")
            if run_id:
                project_runs[str(run_id)] = persisted_run
        runs = project_runs.values()
        metadata = [
            {
                "run_id": run["run_id"],
                "project_id": run["project_id"],
                "workflow": run["workflow"],
                "status": run["status"],
                "executionStatus": run.get("executionStatus") or run.get("status"),
                "approvalStatus": run.get("approvalStatus") or "not_requested",
                "reportStatus": run.get("reportStatus") or "not_started",
                "started_at": run["started_at"],
                "completed_at": run["completed_at"],
                "step_count": len(run["steps"]),
                "errors": run["errors"],
                "approval": run.get("approval"),
            }
            for run in runs
        ]
        # Newest first so clients that read [0] get the latest run.
        metadata.sort(key=lambda r: r.get("started_at") or "", reverse=True)
        return metadata

    def get(self, project_id: str, run_id: str) -> Dict[str, Any] | None:
        persisted_run = get_orchestration_run_record(project_id, run_id)
        if persisted_run is not None:
            self.orchestration_store.setdefault(project_id, {})[run_id] = persisted_run
            return persisted_run
        return self.orchestration_store.get(project_id, {}).get(run_id)

    def save(self) -> None:
        self.persist()


class PostgresOrchestrationRepository:
    """Tenant-bound normalized PF run coordinator.

    The run row is inserted before its report and Snapshot, then both artifacts
    and the final run metadata are written on the same tenant transaction. This
    makes the later publication foreign keys ready without an ``app_state``
    projection or a cross-transaction repair step.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        report_repository: PostgresReportRepository,
        project_snapshot_repository: PostgresProjectSnapshotRepository,
        transaction_factory: OrchestrationTransactionFactory = api_tenant_transaction,
    ) -> None:
        normalized_tenant_id = str(tenant_id or "").strip()
        if not normalized_tenant_id:
            raise ValueError("tenant_id is required")
        if report_repository.tenant_id != normalized_tenant_id:
            raise ValueError("Report repository tenant does not match")
        if project_snapshot_repository.tenant_id != normalized_tenant_id:
            raise ValueError("Snapshot repository tenant does not match")
        self.tenant_id = normalized_tenant_id
        self.report_repository = report_repository
        self.project_snapshot_repository = project_snapshot_repository
        self._transaction = transaction_factory
        self.orchestration_store: Dict[str, Dict[str, Any]] = {}

    def save_run(
        self,
        project_id: str,
        run: Dict[str, Any],
        project_data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        normalized_project_id = _required_text(project_id, "project_id")
        working = deepcopy(run)
        run_id = _required_text(working.get("run_id"), "run.run_id")
        declared_project_id = str(working.get("project_id") or "").strip()
        if declared_project_id and declared_project_id != normalized_project_id:
            raise ValueError("Orchestration project lineage does not match")
        working["project_id"] = normalized_project_id
        options = working.get("options")
        if isinstance(options, Mapping):
            options = deepcopy(dict(options))
            declared_tenant = str(
                options.get("tenantId") or options.get("tenant_id") or ""
            ).strip()
            if declared_tenant and declared_tenant != self.tenant_id:
                raise ValueError("Orchestration tenant lineage does not match")
            options["tenantId"] = self.tenant_id
            options.pop("tenant_id", None)
            working["options"] = options

        with self._transaction(self.tenant_id) as connection:
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

            existing = self._get_in_transaction(
                connection,
                normalized_project_id,
                run_id,
            )
            if existing is not None:
                existing_artifacts = existing.get("artifacts") or {}
                working_artifacts = working.setdefault("artifacts", {})
                for key in ("reportRecord", "projectSnapshot"):
                    if key not in working_artifacts and key in existing_artifacts:
                        working_artifacts[key] = deepcopy(existing_artifacts[key])

            self._upsert_run(connection, normalized_project_id, working)
            artifacts = working.setdefault("artifacts", {})
            report_artifact = artifacts.get("phase11Report")
            report_record_meta = artifacts.get("reportRecord")
            if isinstance(report_artifact, Mapping) and not isinstance(
                report_record_meta,
                Mapping,
            ):
                report_record = self.report_repository.create_in_transaction(
                    connection,
                    normalized_project_id,
                    dict(report_artifact)["report"],
                    str(dict(report_artifact)["report_markdown"]),
                    run_id=run_id,
                )
                artifacts["reportRecord"] = self.report_repository.metadata(
                    report_record
                )
                self._upsert_run(connection, normalized_project_id, working)

            if project_data is not None and not isinstance(
                artifacts.get("projectSnapshot"),
                Mapping,
            ):
                snapshot = (
                    self.project_snapshot_repository.create_from_run_in_transaction(
                        connection,
                        normalized_project_id,
                        project_data,
                        working,
                    )
                )
                artifacts["projectSnapshot"] = (
                    self.project_snapshot_repository.metadata(snapshot)
                )
                self._upsert_run(connection, normalized_project_id, working)

            self._persist_side_artifacts(
                connection,
                normalized_project_id,
                working,
            )

        run.clear()
        run.update(deepcopy(working))
        self.orchestration_store.setdefault(normalized_project_id, {})[
            run_id
        ] = run
        return run

    def list_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        normalized_project_id = _required_text(project_id, "project_id")
        with self._transaction(self.tenant_id) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM realytics.orchestrations
                WHERE tenant_id = %s AND project_id = %s
                ORDER BY started_at DESC NULLS LAST, run_id DESC
                """,
                (self.tenant_id, normalized_project_id),
            ).fetchall()
        return [_run_metadata(_run_payload(row[0])) for row in rows]

    def get(self, project_id: str, run_id: str) -> Dict[str, Any] | None:
        normalized_project_id = _required_text(project_id, "project_id")
        normalized_run_id = _required_text(run_id, "run_id")
        with self._transaction(self.tenant_id) as connection:
            record = self._get_in_transaction(
                connection,
                normalized_project_id,
                normalized_run_id,
            )
        if record is not None:
            self.orchestration_store.setdefault(normalized_project_id, {})[
                normalized_run_id
            ] = record
        return record

    def save(self) -> None:
        records = [
            (project_id, run)
            for project_id, project_runs in self.orchestration_store.items()
            for run in project_runs.values()
        ]
        if not records:
            return
        with self._transaction(self.tenant_id) as connection:
            for project_id, run in records:
                self._upsert_run(connection, project_id, run)

    def _get_in_transaction(
        self,
        connection: Any,
        project_id: str,
        run_id: str,
    ) -> Dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT project_id, run_id, status, started_at, completed_at,
                   payload_json
            FROM realytics.orchestrations
            WHERE tenant_id = %s AND project_id = %s AND run_id = %s
            """,
            (self.tenant_id, project_id, run_id),
        ).fetchone()
        if row is None:
            return None
        payload = _run_payload(row[5])
        payload["project_id"] = str(row[0])
        payload["run_id"] = str(row[1])
        payload["status"] = row[2]
        if row[3] is not None:
            payload["started_at"] = _iso_value(row[3])
        if row[4] is not None:
            payload["completed_at"] = _iso_value(row[4])
        return payload

    def _upsert_run(
        self,
        connection: Any,
        project_id: str,
        run: Mapping[str, Any],
    ) -> None:
        run_id = _required_text(run.get("run_id"), "run.run_id")
        if str(run.get("project_id") or "") != project_id:
            raise ValueError("Orchestration project lineage does not match")
        written = connection.execute(
            """
            INSERT INTO realytics.orchestrations (
                tenant_id,
                project_id,
                run_id,
                status,
                started_at,
                completed_at,
                payload_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (project_id, run_id) DO UPDATE
            SET status = EXCLUDED.status,
                started_at = EXCLUDED.started_at,
                completed_at = EXCLUDED.completed_at,
                payload_json = EXCLUDED.payload_json
            WHERE orchestrations.tenant_id = EXCLUDED.tenant_id
            RETURNING run_id
            """,
            (
                self.tenant_id,
                project_id,
                run_id,
                run.get("status"),
                run.get("started_at"),
                run.get("completed_at"),
                _run_json(run),
            ),
        ).fetchone()
        if written is None:
            raise ValueError("Orchestration identity conflicts across tenants")

    def _persist_side_artifacts(
        self,
        connection: Any,
        project_id: str,
        run: Mapping[str, Any],
    ) -> None:
        """Preserve normalized side artifacts on the core transaction."""
        run_id = _required_text(run.get("run_id"), "run.run_id")
        artifacts = run.get("artifacts") or {}
        now = datetime.now(timezone.utc).isoformat()

        evidence_records = (
            (artifacts.get("marketEvidenceReview") or {}).get(
                "evidenceRecords"
            )
            or []
        )
        for index, raw in enumerate(evidence_records):
            if not isinstance(raw, Mapping):
                continue
            evidence_id = str(
                raw.get("evidenceId") or f"market-evidence-{index + 1:03d}"
            )
            record_key = f"{run_id}:{evidence_id}"
            validation_status = str(
                raw.get("validationStatus") or "not_checked"
            )
            record = {
                **deepcopy(dict(raw)),
                "recordKey": record_key,
                "projectId": project_id,
                "runId": run_id,
                "workflow": run.get("workflow"),
                "createdAt": now,
                "reviewStatus": (
                    "auto_validated"
                    if validation_status == "pass"
                    else "pending_review"
                ),
            }
            written = connection.execute(
                """
                INSERT INTO realytics.market_evidence (
                    tenant_id, project_id, record_key, run_id, evidence_id,
                    evidence_type, validation_status, created_at, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (project_id, record_key) DO UPDATE
                SET run_id = EXCLUDED.run_id,
                    evidence_id = EXCLUDED.evidence_id,
                    evidence_type = EXCLUDED.evidence_type,
                    validation_status = EXCLUDED.validation_status,
                    created_at = EXCLUDED.created_at,
                    payload_json = EXCLUDED.payload_json
                WHERE market_evidence.tenant_id = EXCLUDED.tenant_id
                RETURNING record_key
                """,
                (
                    self.tenant_id,
                    project_id,
                    record_key,
                    run_id,
                    evidence_id,
                    raw.get("evidenceType"),
                    validation_status,
                    now,
                    _run_json(record),
                ),
            ).fetchone()
            if written is None:
                raise ValueError("Market evidence identity conflicts across tenants")

        risk_register = artifacts.get("riskRegister")
        if isinstance(risk_register, Mapping):
            risks = list(risk_register.get("risks") or [])
            normalized_risk = {
                **deepcopy(dict(risk_register)),
                "schemaVersion": risk_register.get("schemaVersion")
                or "realytics-risk-register-v1",
                "projectId": project_id,
                "runId": run_id,
                "registerId": risk_register.get("registerId")
                or f"{run_id}:risk-register",
                "status": risk_register.get("status") or "unknown",
                "updatedAt": now,
                "riskCount": len(risks),
            }
            normalized_risk.setdefault("risks", risks)
            normalized_risk.setdefault("reviewSummary", {})
            written = connection.execute(
                """
                INSERT INTO realytics.risk_registers (
                    tenant_id, project_id, run_id, register_id, status,
                    risk_count, updated_at, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (project_id, run_id) DO UPDATE
                SET register_id = EXCLUDED.register_id,
                    status = EXCLUDED.status,
                    risk_count = EXCLUDED.risk_count,
                    updated_at = EXCLUDED.updated_at,
                    payload_json = EXCLUDED.payload_json
                WHERE risk_registers.tenant_id = EXCLUDED.tenant_id
                RETURNING run_id
                """,
                (
                    self.tenant_id,
                    project_id,
                    run_id,
                    normalized_risk["registerId"],
                    normalized_risk["status"],
                    normalized_risk["riskCount"],
                    now,
                    _run_json(normalized_risk),
                ),
            ).fetchone()
            if written is None:
                raise ValueError("Risk Register identity conflicts across tenants")

        evidence_graph = artifacts.get("evidenceGraph")
        if isinstance(evidence_graph, Mapping):
            nodes = list(evidence_graph.get("nodes") or [])
            edges = list(evidence_graph.get("edges") or [])
            summary = evidence_graph.get("summary") or {}
            normalized_graph = {
                **deepcopy(dict(evidence_graph)),
                "schemaVersion": evidence_graph.get("schemaVersion")
                or "realytics-evidence-graph-v1",
                "projectId": project_id,
                "runId": run_id,
                "graphId": evidence_graph.get("graphId")
                or evidence_graph.get("graph_id")
                or f"{run_id}:evidence-graph",
                "updatedAt": now,
                "nodeCount": int(summary.get("nodeCount") or len(nodes)),
                "edgeCount": int(summary.get("edgeCount") or len(edges)),
                "riskNodeCount": int(
                    summary.get("riskNodeCount")
                    or len([node for node in nodes if node.get("type") == "risk"])
                ),
                "sourceLocatorCount": int(
                    summary.get("sourceLocatorCount")
                    or len(
                        [
                            node
                            for node in nodes
                            if node.get("type") == "source_locator"
                        ]
                    )
                ),
            }
            normalized_graph.setdefault("nodes", nodes)
            normalized_graph.setdefault("edges", edges)
            normalized_graph.setdefault("summary", summary)
            written = connection.execute(
                """
                INSERT INTO realytics.evidence_graphs (
                    tenant_id, project_id, run_id, graph_id, node_count,
                    edge_count, risk_node_count, source_locator_count,
                    updated_at, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (project_id, run_id) DO UPDATE
                SET graph_id = EXCLUDED.graph_id,
                    node_count = EXCLUDED.node_count,
                    edge_count = EXCLUDED.edge_count,
                    risk_node_count = EXCLUDED.risk_node_count,
                    source_locator_count = EXCLUDED.source_locator_count,
                    updated_at = EXCLUDED.updated_at,
                    payload_json = EXCLUDED.payload_json
                WHERE evidence_graphs.tenant_id = EXCLUDED.tenant_id
                RETURNING run_id
                """,
                (
                    self.tenant_id,
                    project_id,
                    run_id,
                    normalized_graph["graphId"],
                    normalized_graph["nodeCount"],
                    normalized_graph["edgeCount"],
                    normalized_graph["riskNodeCount"],
                    normalized_graph["sourceLocatorCount"],
                    now,
                    _run_json(normalized_graph),
                ),
            ).fetchone()
            if written is None:
                raise ValueError("Evidence Graph identity conflicts across tenants")

        proposal_artifact = artifacts.get("assumptionProposals") or {}
        for proposal in proposal_artifact.get("proposals") or []:
            if not isinstance(proposal, Mapping):
                continue
            proposal_id = _required_text(
                proposal.get("proposalId") or proposal.get("proposal_id"),
                "proposal.proposalId",
            )
            field_path = _required_text(
                proposal.get("fieldPath") or proposal.get("field_path"),
                "proposal.fieldPath",
            )
            status = _required_text(
                proposal.get("status"),
                "proposal.status",
            )
            created_at = proposal.get("createdAt") or proposal.get(
                "created_at"
            ) or now
            normalized_proposal = {
                **deepcopy(dict(proposal)),
                "proposalId": proposal_id,
                "projectId": project_id,
                "runId": run_id,
                "fieldPath": field_path,
                "createdAt": created_at,
            }
            written = connection.execute(
                """
                INSERT INTO realytics.assumption_proposals (
                    tenant_id, proposal_id, project_id, run_id, field_path,
                    source_agent, status, created_at, reviewed_at, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (proposal_id) DO UPDATE
                SET project_id = EXCLUDED.project_id,
                    run_id = EXCLUDED.run_id,
                    field_path = EXCLUDED.field_path,
                    source_agent = EXCLUDED.source_agent,
                    status = EXCLUDED.status,
                    created_at = EXCLUDED.created_at,
                    reviewed_at = EXCLUDED.reviewed_at,
                    payload_json = EXCLUDED.payload_json
                WHERE assumption_proposals.tenant_id = EXCLUDED.tenant_id
                RETURNING proposal_id
                """,
                (
                    self.tenant_id,
                    proposal_id,
                    project_id,
                    run_id,
                    field_path,
                    proposal.get("sourceAgent") or proposal.get("source_agent"),
                    status,
                    created_at,
                    proposal.get("reviewedAt") or proposal.get("reviewed_at"),
                    _run_json(normalized_proposal),
                ),
            ).fetchone()
            if written is None:
                raise ValueError(
                    "Assumption proposal identity conflicts across tenants"
                )


def _run_metadata(run: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "project_id": run["project_id"],
        "workflow": run.get("workflow"),
        "status": run.get("status"),
        "executionStatus": run.get("executionStatus") or run.get("status"),
        "approvalStatus": run.get("approvalStatus") or "not_requested",
        "reportStatus": run.get("reportStatus") or "not_started",
        "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"),
        "step_count": len(run.get("steps") or []),
        "errors": run.get("errors") or [],
        "approval": run.get("approval"),
    }


def _run_payload(value: Any) -> Dict[str, Any]:
    decoded = value
    if isinstance(value, (str, bytes, bytearray)):
        decoded = json.loads(value)
    if not isinstance(decoded, Mapping):
        raise ValueError("Orchestration payload must be a JSON object")
    return deepcopy(dict(decoded))


def _run_json(value: Mapping[str, Any]) -> str:
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


def _iso_value(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
