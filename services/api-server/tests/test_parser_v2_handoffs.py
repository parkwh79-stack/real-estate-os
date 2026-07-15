from __future__ import annotations

from datetime import datetime, timezone

import pytest

from use_cases.parser_v2_handoffs import build_approved_snapshot_handoff
from use_cases.parser_v2_records import ParserV2ApprovalError


SOURCE_HASH = "a" * 64


class SnapshotStore:
    def __init__(self, snapshots):
        self.snapshots = snapshots

    def list_approved_input_snapshots(self, tenant_id, project_id, *, active_only=False):
        assert active_only is True
        return [
            item
            for item in self.snapshots
            if item["tenantId"] == tenant_id and item["projectId"] == project_id
        ]


def _snapshot(*, field_key="financing.senior.loan_amount", active=True, locator=None):
    return {
        "tenantId": "tenant-1",
        "projectId": "project-1",
        "snapshotId": "snapshot-1",
        "purpose": "pf_review",
        "approvalStatus": "approved",
        "validityStatus": "active" if active else "invalidated",
        "approvedAt": "2026-07-15T00:00:00+00:00",
        "approvedBy": "reviewer-1",
        "sourceParsingRunIds": ["run-1"],
        "versionContexts": [{"ontologyVersion": "1.0.0"}],
        "sourceDocuments": [{"documentVersionId": "doc-v1", "sha256": SOURCE_HASH}],
        "inputItems": [
            {
                "fieldKey": field_key,
                "normalizedValue": 49_000_000_000,
                "unit": "KRW",
                "candidateId": "candidate-1",
                "selectionId": "selection-1",
                "reviewDecisionId": "decision-1",
                "parsingRunId": "run-1",
                "documentVersionId": "doc-v1",
                "sourceHash": SOURCE_HASH,
                "versionContext": {
                    "parserVersion": "2.0.0",
                    "ontologyVersion": "1.0.0",
                    "promptVersion": "1.0.0",
                    "modelVersion": "deterministic",
                },
                "sourceLocator": locator
                or {
                    "documentVersionId": "doc-v1",
                    "fileSha256": SOURCE_HASH,
                    "sheetName": "자금조달",
                    "cellReference": "D12",
                    "parserMethod": "formula",
                },
            }
        ],
    }


def test_handoff_preserves_approved_inputs_but_blocks_until_domain_policy_is_approved():
    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore([_snapshot()]),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="pf_credit",
        actor_id="reviewer-1",
        run_id="orchestration-1",
        created_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )

    assert envelope.status == "blocked"
    assert envelope.to_agent == "pf_credit_pod"
    assert envelope.payload["approvedSnapshotId"] == "snapshot-1"
    assert envelope.payload["authoritativeInputBoundary"] == "approved_snapshot_only"
    assert envelope.payload["approvedInputs"][0]["selectionId"] == "selection-1"
    assert envelope.payload["handoffPolicyStatus"] == "draft_not_approved"
    assert envelope.blocking_issues == [
        "DOMAIN_HANDOFF_COMPLETENESS_POLICY_NOT_APPROVED"
    ]
    assert envelope.progress_impact["canExecute"] is False


def test_handoff_preserves_derived_calculation_output_locator():
    locator = {
        "documentVersionId": "doc-v1",
        "fileSha256": SOURCE_HASH,
        "artifactKey": "calculation-run-1",
        "jsonPath": "/summary/seniorLoanAmount",
        "metadata": {
            "calculationRunId": "calculation-run-1",
            "outputHash": "b" * 64,
        },
    }
    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore([_snapshot(locator=locator)]),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="pf_credit",
        actor_id="reviewer-1",
    )

    approved_locator = envelope.payload["approvedInputs"][0]["sourceLocator"]
    assert approved_locator["artifactKey"] == "calculation-run-1"
    assert approved_locator["jsonPath"] == "/summary/seniorLoanAmount"
    assert approved_locator["metadata"]["outputHash"] == "b" * 64


def test_handoff_blocks_when_snapshot_has_no_input_for_target_domain():
    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore([_snapshot(field_key="land.registered_area_sqm")]),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="pf_credit",
        actor_id="reviewer-1",
    )

    assert envelope.status == "blocked"
    assert envelope.blocking_issues == [
        "NO_APPROVED_INPUTS_FOR_TARGET_DOMAIN",
        "DOMAIN_HANDOFF_COMPLETENESS_POLICY_NOT_APPROVED",
    ]
    assert envelope.payload["approvedInputs"] == []


def test_handoff_rejects_missing_or_mismatched_evidence():
    snapshot = _snapshot(locator={"artifactKey": "parser-output"})
    with pytest.raises(ParserV2ApprovalError, match="document version"):
        build_approved_snapshot_handoff(
            record_store=SnapshotStore([snapshot]),
            tenant_id="tenant-1",
            project_id="project-1",
            snapshot_id="snapshot-1",
            target_domain="pf_credit",
            actor_id="reviewer-1",
        )


def test_handoff_rejects_invalidated_snapshot():
    with pytest.raises(ParserV2ApprovalError, match="absent or invalidated"):
        build_approved_snapshot_handoff(
            record_store=SnapshotStore([]),
            tenant_id="tenant-1",
            project_id="project-1",
            snapshot_id="snapshot-1",
            target_domain="pf_credit",
            actor_id="reviewer-1",
        )
