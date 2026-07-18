from copy import deepcopy

import pytest

from use_cases.assumption_proposals import (
    approve_assumption_proposals,
    build_consensus_assumption_proposals,
)
from use_cases.orchestration_approval import approve_orchestration_run_use_case
from storage import (
    list_assumption_proposal_records,
    list_project_version_records,
    upsert_assumption_proposal_record,
    upsert_project_version_record,
)


def test_consensus_patch_becomes_proposal_without_mutating_project():
    project = {"_meta": {"projectVersion": 2}, "feasibility_input": {"total_revenue": 130}}
    original = deepcopy(project)
    proposals = build_consensus_assumption_proposals(
        "p1",
        "run1",
        project,
        {
            "patches": [{"field": "feasibility_input.total_revenue", "value": 100, "sourceAgent": "MarketabilityAgent"}],
            "resolutions": [{
                "patch": {"field": "feasibility_input.total_revenue"},
                "rationale": "Evidence-backed market revenue is lower.",
            }],
        },
    )

    assert project == original
    assert proposals[0]["currentValue"] == 130
    assert proposals[0]["proposedValue"] == 100
    assert proposals[0]["status"] == "pending"


def test_approved_proposal_creates_new_project_version():
    project = {"_meta": {"projectVersion": 2}, "feasibility_input": {"total_revenue": 130}}
    proposals = build_consensus_assumption_proposals(
        "p1",
        "run1",
        project,
        {"patches": [{"field": "feasibility_input.total_revenue", "value": 100}], "resolutions": []},
    )

    result = approve_assumption_proposals(
        project,
        proposals,
        [proposals[0]["proposalId"]],
        approved_by="reviewer-1",
        comment="Adopt market-supported revenue.",
    )

    assert project["feasibility_input"]["total_revenue"] == 130
    assert result["project"]["feasibility_input"]["total_revenue"] == 100
    assert result["projectVersion"]["baseVersion"] == 2
    assert result["projectVersion"]["version"] == 3
    assert result["projectVersion"]["previousStateHash"] != result["projectVersion"]["stateHash"]
    assert result["proposals"][0]["status"] == "approved"


def test_run_approval_keeps_execution_status_separate():
    run = {
        "status": "completed",
        "executionStatus": "completed",
        "reportStatus": "quality_pass",
        "handoffs": [],
        "artifacts": {"riskRegister": {"risks": []}, "assumptionProposals": {"proposals": []}},
    }
    result = approve_orchestration_run_use_case(run, {"approved_by": "credit"}, "2026-07-14T00:00:00Z")
    assert result["status"] == "completed"
    assert result["executionStatus"] == "completed"
    assert result["approvalStatus"] == "approved"
    assert result["reportStatus"] == "quality_pass"


def test_run_approval_rejects_pending_assumption_proposal():
    run = {
        "status": "completed",
        "executionStatus": "completed",
        "handoffs": [],
        "artifacts": {
            "riskRegister": {"risks": []},
            "assumptionProposals": {"proposals": [{"proposalId": "p1", "status": "pending"}]},
        },
    }
    with pytest.raises(ValueError, match="assumption proposal"):
        approve_orchestration_run_use_case(run, {}, "2026-07-14T00:00:00Z")


def test_assumption_proposal_and_project_version_are_first_class_records(tmp_path, monkeypatch):
    monkeypatch.setenv("REALYTICS_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("REALYTICS_SQLITE_FILE", str(tmp_path / "assumptions.sqlite3"))
    proposal = build_consensus_assumption_proposals(
        "p-typed",
        "run-typed",
        {"feasibility_input": {"total_revenue": 130}},
        {"patches": [{"field": "feasibility_input.total_revenue", "value": 100}], "resolutions": []},
    )[0]
    upsert_assumption_proposal_record(proposal)
    upsert_project_version_record(
        {
            "versionId": "p-typed:v2",
            "projectId": "p-typed",
            "tenantId": "tenant-1",
            "version": 2,
            "baseVersion": 1,
            "createdBy": "reviewer-1",
            "createdAt": "2026-07-15T00:00:00+00:00",
            "stateHash": "hash-2",
            "projectState": {"feasibility_input": {"total_revenue": 100}},
        }
    )

    proposals = list_assumption_proposal_records(project_id="p-typed", run_id="run-typed")
    versions = list_project_version_records("p-typed")
    assert proposals[0]["proposalId"] == proposal["proposalId"]
    assert versions[0]["versionId"] == "p-typed:v2"
    assert versions[0]["projectState"]["feasibility_input"]["total_revenue"] == 100
