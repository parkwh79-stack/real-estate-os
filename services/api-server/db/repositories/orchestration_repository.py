from __future__ import annotations

from typing import Any, Callable, Dict, List

from db.repositories.market_evidence_repository import MarketEvidenceRepository
from db.repositories.project_snapshot_repository import ProjectSnapshotRepository
from db.repositories.report_repository import ReportRepository


class OrchestrationRepository:
    """Repository wrapper for orchestration runs and related saved artifacts."""

    def __init__(
        self,
        orchestration_store: Dict[str, Dict[str, Any]],
        report_repository: ReportRepository,
        market_evidence_repository: MarketEvidenceRepository,
        project_snapshot_repository: ProjectSnapshotRepository,
        persist: Callable[[], None],
    ):
        self.orchestration_store = orchestration_store
        self.report_repository = report_repository
        self.market_evidence_repository = market_evidence_repository
        self.project_snapshot_repository = project_snapshot_repository
        self.persist = persist

    def save_run(
        self,
        project_id: str,
        run: Dict[str, Any],
        project_data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        self.orchestration_store.setdefault(project_id, {})[run["run_id"]] = run
        self.market_evidence_repository.add_from_run(project_id, run)
        self.persist()

        if "phase11Report" in run.get("artifacts", {}):
            report_artifact = run["artifacts"]["phase11Report"]
            report_record = self.report_repository.create(
                project_id,
                report_artifact["report"],
                report_artifact["report_markdown"],
                run["run_id"],
            )
            run["artifacts"]["reportRecord"] = self.report_repository.metadata(report_record)
            self.persist()

        if project_data is not None:
            snapshot = self.project_snapshot_repository.create_from_run(project_id, project_data, run)
            run["artifacts"]["projectSnapshot"] = self.project_snapshot_repository.metadata(snapshot)
            self.persist()

        return run

    def list_metadata(self, project_id: str) -> List[Dict[str, Any]]:
        runs = self.orchestration_store.get(project_id, {}).values()
        return [
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

    def get(self, project_id: str, run_id: str) -> Dict[str, Any] | None:
        return self.orchestration_store.get(project_id, {}).get(run_id)

    def save(self) -> None:
        self.persist()
