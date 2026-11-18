from __future__ import annotations

from datetime import datetime, timezone

from use_cases.document_review_workspace import (
    build_document_review_workspace_read_model,
)


def _package() -> dict:
    locator = {
        "evidenceId": "evidence-1",
        "documentVersionId": "document-version-1",
        "pageNumber": 7,
        "tableId": "cost-table",
        "rowIndex": 3,
        "rawTextSha256": "b" * 64,
    }
    selection_1 = {
        "selectionId": "selection-1",
        "fieldKey": "cost.total_project_cost",
        "candidateId": "candidate-1",
        "normalizedValue": 100,
        "unit": "KRW",
    }
    selection_2 = {
        **selection_1,
        "selectionId": "selection-2",
        "candidateId": "candidate-2",
        "normalizedValue": 120,
    }
    return {
        "schemaVersion": "realytics-parser-v2-run-package-v2",
        "parsingRun": {
            "parsingRunId": "run-1",
            "documentVersionId": "document-version-1",
            "executionStatus": "completed",
            "completedAt": "2026-07-27T01:00:00+00:00",
        },
        "documentVersion": {
            "documentVersionId": "document-version-1",
            "archiveId": "a" * 64,
        },
        "candidates": [
            {
                "candidateId": "candidate-1",
                "parsingRunId": "run-1",
                "documentVersionId": "document-version-1",
                "fieldKey": "cost.total_project_cost",
                "presenceStatus": "present",
                "reviewStatus": "approved",
                "authority": "submitted_document",
                "validationStatus": "passed",
                "evidence": [locator],
            },
            {
                "candidateId": "candidate-2",
                "parsingRunId": "run-1",
                "documentVersionId": "document-version-1",
                "fieldKey": "revenue.total_sales",
                "presenceStatus": "present",
                "reviewStatus": "pending",
                "authority": "draft_document",
                "validationStatus": "failed",
                "validationCodes": ["UNIT_MISMATCH"],
                "evidence": [],
            },
        ],
        "conflicts": [
            {
                "conflictId": "conflict-1",
                "candidateIds": ["candidate-2", "candidate-3"],
                "conflictStatus": "open",
            }
        ],
        "reviewDecisions": [],
        "canonicalSelections": [],
        "approvedInputSnapshots": [
            {
                "snapshotId": "snapshot-1",
                "snapshotVersion": 1,
                "approvedAt": "2026-07-26T01:00:00+00:00",
                "selectionIds": ["selection-1"],
                "inputItems": [selection_1],
            },
            {
                "snapshotId": "snapshot-2",
                "snapshotVersion": 2,
                "supersedesSnapshotId": "snapshot-1",
                "approvedAt": "2026-07-27T01:00:00+00:00",
                "selectionIds": ["selection-2"],
                "inputItems": [selection_2],
            },
        ],
    }


def test_builds_stable_versioned_read_model_and_explicit_review_queue() -> None:
    first = build_document_review_workspace_read_model(
        project_id="project-1",
        source_documents=[
            {
                "archiveId": "a" * 64,
                "uploadedAt": "2026-07-26T00:00:00+00:00",
            }
        ],
        run_packages=[_package()],
        glossary_candidates=[],
        observed_at=datetime(2026, 7, 27, 2, tzinfo=timezone.utc),
    )
    replay = build_document_review_workspace_read_model(
        project_id="project-1",
        source_documents=[
            {
                "archiveId": "a" * 64,
                "uploadedAt": "2026-07-26T00:00:00+00:00",
            }
        ],
        run_packages=[_package()],
        glossary_candidates=[],
        observed_at=datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
    )

    assert first["schemaVersion"] == "realytics-document-review-workspace-v1"
    assert first["snapshotHash"] == replay["snapshotHash"]
    assert first["snapshotId"] == replay["snapshotId"]
    assert first["observedAt"] != replay["observedAt"]
    assert first["asOfDate"] == "2026-07-27T01:00:00+00:00"
    assert first["authority"] == {
        "candidateOnly": True,
        "authoritativeProjectionMutated": False,
        "approvalAuthority": "approved_input_snapshot_only",
        "assembly": "server_versioned_read_model",
    }

    queue = {item["candidateId"]: item for item in first["reviewQueue"]}
    assert "candidate-1" not in queue
    assert queue["candidate-2"]["severity"] == "blocking"
    assert queue["candidate-2"]["validationOutcome"] == "failed"
    assert queue["candidate-2"]["issueCodes"] == [
        "VALIDATION_NOT_PASSED",
        "VALIDATION:UNIT_MISMATCH",
        "MISSING_EVIDENCE_LOCATOR",
        "OPEN_SOURCE_CONFLICT",
        "REVIEW_PENDING",
        "WEAK_SOURCE_AUTHORITY",
    ]


def test_compares_approved_parser_snapshots_by_field_and_entity() -> None:
    result = build_document_review_workspace_read_model(
        project_id="project-1",
        source_documents=[],
        run_packages=[_package()],
        glossary_candidates=[],
    )

    assert len(result["snapshotDiffs"]) == 1
    diff = result["snapshotDiffs"][0]
    assert diff["comparable"] is True
    assert diff["changeCount"] == 1
    assert len(diff["fromSnapshotHash"]) == 64
    assert len(diff["toSnapshotHash"]) == 64
    assert diff["changes"] == [
        {
            "changeType": "changed",
            "fieldKey": "cost.total_project_cost",
            "entityId": None,
            "before": {
                "normalizedValue": 100,
                "unit": "KRW",
                "candidateId": "candidate-1",
                "selectionId": "selection-1",
                "documentVersionId": None,
                "sourceHash": None,
                "sourceLocator": None,
                "authority": None,
                "effectiveDate": None,
                "asOfDate": None,
            },
            "after": {
                "normalizedValue": 120,
                "unit": "KRW",
                "candidateId": "candidate-2",
                "selectionId": "selection-2",
                "documentVersionId": None,
                "sourceHash": None,
                "sourceLocator": None,
                "authority": None,
                "effectiveDate": None,
                "asOfDate": None,
            },
        }
    ]


def test_snapshot_diff_fails_closed_without_materialized_items() -> None:
    package = _package()
    package["approvedInputSnapshots"] = [
        {
            "snapshotId": "snapshot-1",
            "snapshotVersion": 1,
            "approvedAt": "2026-07-26T01:00:00+00:00",
        },
        {
            "snapshotId": "snapshot-2",
            "snapshotVersion": 2,
            "supersedesSnapshotId": "snapshot-1",
            "approvedAt": "2026-07-27T01:00:00+00:00",
        },
    ]

    result = build_document_review_workspace_read_model(
        project_id="project-1",
        source_documents=[],
        run_packages=[package],
        glossary_candidates=[],
    )

    assert result["snapshotDiffs"][0]["comparable"] is False
    assert result["snapshotDiffs"][0]["changes"] == []
    assert result["snapshotDiffs"][0]["issueCodes"] == [
        "SNAPSHOT_INPUT_ITEMS_UNAVAILABLE"
    ]


def test_official_api_row_locator_is_precise_evidence() -> None:
    package = _package()
    package["candidates"][0]["evidence"] = [
        {
            "apiEndpoint": "https://example.test/official-transactions",
            "apiRowId": "transaction-2026-1",
            "rawTextSha256": "f" * 64,
        }
    ]

    result = build_document_review_workspace_read_model(
        project_id="project-1",
        source_documents=[],
        run_packages=[package],
        glossary_candidates=[],
    )

    assert all(
        item["candidateId"] != "candidate-1" for item in result["reviewQueue"]
    )
