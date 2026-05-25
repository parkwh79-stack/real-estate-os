from db.repositories.project_snapshot_repository import (
    PROJECT_SNAPSHOT_SCHEMA_VERSION,
    ProjectSnapshotRepository,
)


def test_project_snapshot_repository_builds_case_comparison_seed():
    store = {}
    persisted = {"called": False}
    repo = ProjectSnapshotRepository(store, lambda: persisted.__setitem__("called", True))

    snapshot = repo.create_from_run(
        "p-1",
        {
            "project": {"id": "p-1", "name": "Snapshot QA", "type": "mixed_use"},
            "land": {"address": "Seoul", "area_sqm": 1000},
            "feasibility_input": {
                "total_revenue": 100_000,
                "total_cost": 80_000,
                "land_cost": 20_000,
            },
            "pf_structure": [{"tranche": "Tr.A", "amount": 50_000}],
        },
        {
            "run_id": "run-1",
            "workflow": "pf-review",
            "status": "completed",
            "started_at": "2026-05-21T00:00:00Z",
            "completed_at": "2026-05-21T00:01:00Z",
            "steps": [{"key": "project_load"}, {"key": "quality_check"}],
            "errors": [],
            "handoffs": [
                {
                    "handoffId": "h-1",
                    "handoffType": "land_to_feasibility_legal_pf",
                    "fromAgent": "LandAgent",
                    "toAgent": "DevelopmentFeasibilityAgent",
                    "payload": {"sitePatch": {"landAreaSqm": 1000}},
                }
            ],
            "agentProgress": {},
            "artifacts": {
                "landAgentReview": {"status": "basic_site_data_only"},
                "pfStructure": {"ltv": 0.5, "ltc": 0.625, "exit_sale_rate": 0.5},
                "marketabilityAnalysis": {"score": 77, "grade": "B"},
                "reconciliationGate": {"status": "clear"},
                "qualityCheck": {"pass": True},
                "phase11Report": {
                    "report": {
                        "template": "phase-11-pf-review",
                        "sections": {"cover": {}, "risk": {}},
                        "qualityChecks": {"isComplete": True},
                    }
                },
                "reportRecord": {"report_id": "r-1", "version": 1},
            },
        },
    )

    assert persisted["called"] is True
    assert snapshot["schemaVersion"] == PROJECT_SNAPSHOT_SCHEMA_VERSION
    assert snapshot["snapshotId"] == "snapshot-run-1"
    assert snapshot["caseComparisonSeed"]["pfAmount"] == 50_000
    assert snapshot["caseComparisonSeed"]["marketabilityGrade"] == "B"
    assert snapshot["report"]["sectionKeys"] == ["cover", "risk"]
    assert repo.get("p-1", "snapshot-run-1") == snapshot
    assert repo.list_metadata("p-1")[0]["reconciliationStatus"] == "clear"
