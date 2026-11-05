"""Source-review candidates reach human-approved snapshots without implied application."""
from copy import deepcopy

import pytest

from parsing_platform.field_registry import project_approved_inputs
from test_project_parser_v2_router import _client
from test_source_review_candidates import document


def test_generated_source_candidates_require_human_review_and_snapshot_remains_explicit(monkeypatch):
    original = document()
    client, archive_id, projects, _ = _client(
        monkeypatch, orchestrator_factory=None, primary_content=original.content,
        primary_filename="review.xlsx",
        primary_media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    initial_project = deepcopy(projects["p-1"])
    run_id = "source-review-approval-run"
    response = client.post("/projects/p-1/parsing-v2/runs", json={
        "archiveId": archive_id,
        "profileId": "realytics.source_review_candidates", "profileVersion": "1.0.0",
        "parsingRunId": run_id,
    })
    assert response.status_code == 201, response.text
    assert response.json()["candidateSetSeal"]["candidateCount"] >= 4
    collection = response.json()["candidateCollection"]
    assert collection["expectedModuleIds"] == ["source_review_candidates"]
    assert collection["completedModuleIds"] == ["source_review_candidates"]
    assert collection["unresolvedModuleIds"] == []
    assert collection["reviewEligible"] is True
    assert collection["approvalEligible"] is False
    assert collection["blockedCandidateCount"] == 1
    assert "FACT_BASIS_REQUIRED" not in response.json()["candidateSetSeal"]["blockingDiagnosticCodes"]
    package = client.get(f"/projects/p-1/parsing-v2/runs/{run_id}").json()
    candidates = {item["canonicalField"]: item for item in package["candidates"]}
    selected = []
    for index, field in enumerate(("project.name", "land.address", "land.area_sqm")):
        candidate = candidates[field]
        assert candidate["validationStatus"] != "blocked", candidate
        assert candidate["approvalStatus"] == "not_requested"
        candidate_id = candidate["candidateId"]
        decision_id = f"human-review-{index}"
        review = client.post(f"/projects/p-1/parsing-v2/candidates/{candidate_id}/reviews", json={
            "reviewDecisionId": decision_id, "expectedDecisionSequence": 0,
            "action": "approve", "reason": "Checked the exact original workbook cells and project association.",
            "conflictIds": [],
        }, headers={"x-test-user-id": "parser-v2-reviewer"})
        assert review.status_code == 201, review.text
        selection_id = f"human-selection-{index}"
        selection = client.post("/projects/p-1/parsing-v2/canonical-selections", json={
            "selectionId": selection_id, "candidateId": candidate_id,
            "reviewDecisionId": decision_id, "expectedDecisionSequence": 1,
        }, headers={"x-test-user-id": "parser-v2-reviewer"})
        assert selection.status_code == 201, selection.text
        assert selection.json()["canonicalField"] == field
        selected.append(selection_id)

    cost = candidates["cost.total_project_cost"]
    assert cost["validationStatus"] == "blocked"
    assert "FACT_BASIS_REQUIRED" in cost["validationCodes"]
    review = client.post(f"/projects/p-1/parsing-v2/candidates/{cost['candidateId']}/reviews", json={
        "reviewDecisionId": "human-cost-review", "expectedDecisionSequence": 0,
        "action": "approve", "reason": "Amount transcribed; monetary interpretation remains unconfirmed.",
        "conflictIds": [],
    }, headers={"x-test-user-id": "parser-v2-reviewer"})
    assert review.status_code == 201, review.text
    blocked = client.post("/projects/p-1/parsing-v2/canonical-selections", json={
        "selectionId": "blocked-cost-selection", "candidateId": cost["candidateId"],
        "reviewDecisionId": "human-cost-review", "expectedDecisionSequence": 1,
    }, headers={"x-test-user-id": "parser-v2-reviewer"})
    assert blocked.status_code == 409
    assert "FACT_BASIS_REQUIRED" in blocked.text

    response = client.post("/projects/p-1/parsing-v2/approved-snapshots", json={
        "snapshotId": "source-review-snapshot", "selectionIds": selected,
        "purpose": "pf_review_candidate_input",
        "expectedSelectionVersions": {selection_id: 1 for selection_id in selected},
    }, headers={"x-test-user-id": "parser-v2-approver"})
    assert response.status_code == 201, response.text
    snapshot = response.json()
    assert snapshot["approvalStatus"] == "approved"
    items = {item["fieldKey"]: item for item in snapshot["inputItems"]}
    assert set(items) == {"project.name", "land.address", "land.area_sqm"}
    assert items["land.area_sqm"]["normalizedValue"] == 1234
    for item in items.values():
        assert item["sourceHash"] == archive_id
        assert item["sourceLocator"]["fileSha256"] == archive_id
        assert item["sourceLocator"]["cellReference"]
        assert item["reviewDecisionId"].startswith("human-review-")

    # Source facts can be approved independently; approval does not claim a schema adapter exists.
    with pytest.raises(ValueError, match="no registered projection"):
        project_approved_inputs(
            target_schema_id="realytics-land-ledger-import-v1", target_schema_version="1.0",
            approved_snapshot=snapshot,
            requested_selection_ids=[items["land.address"]["selectionId"], items["land.area_sqm"]["selectionId"]],
        )
    projected = project_approved_inputs(
        target_schema_id="realytics-feasibility-study-v2", target_schema_version="2.0",
        approved_snapshot=snapshot,
        requested_selection_ids=[items["project.name"]["selectionId"]],
    )
    assert projected["payload"]["project_name"] == candidates["project.name"]["normalizedValue"]
    assert projects["p-1"] == initial_project
