from __future__ import annotations

from typing import Any, Callable, Dict, List

from db.repositories.market_evidence_repository import MarketEvidenceRepository
from db.repositories.project_snapshot_repository import ProjectSnapshotRepository
from db.repositories.report_repository import ReportRepository
from storage import upsert_evidence_graph_record, upsert_risk_register_record


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
        runs = self.orchestration_store.get(project_id, {}).values()
        metadata = [
            {
                "run_id": run["run_id"],
                "project_id": run["project_id"],
                "workflow": run["workflow"],
                "status": run["status"],
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
        return self.orchestration_store.get(project_id, {}).get(run_id)

    def save(self) -> None:
        self.persist()
