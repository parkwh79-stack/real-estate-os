from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List


PROJECT_SNAPSHOT_SCHEMA_VERSION = "realytics-project-snapshot-v1"


class ProjectSnapshotRepository:
    """Repository for immutable project review snapshots."""

    def __init__(
        self,
        snapshot_store: Dict[str, Dict[str, Any]],
        persist: Callable[[], None],
    ):
        self.snapshot_store = snapshot_store
        self.persist = persist

    def create_from_run(
        self,
        project_id: str,
        project_data: Dict[str, Any],
        run: Dict[str, Any],
    ) -> Dict[str, Any]:
        snapshot = build_project_snapshot(project_id, project_data, run)
        self.snapshot_store.setdefault(project_id, {})[snapshot["snapshotId"]] = snapshot
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
        }


def build_project_snapshot(
    project_id: str,
    project_data: Dict[str, Any],
    run: Dict[str, Any],
) -> Dict[str, Any]:
    """Build an auditable snapshot of the project state used by one orchestration run."""
    created_at = datetime.now(timezone.utc).isoformat()
    run_id = run.get("run_id")
    artifacts = run.get("artifacts", {})
    return {
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
    }


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
