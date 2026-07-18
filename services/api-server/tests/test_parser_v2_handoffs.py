from __future__ import annotations

from datetime import datetime, timezone

import pytest

from parsing_platform.canonical_fields import (
    CANONICAL_FIELD_NAMESPACE_VERSION,
    resolve_canonical_field,
)
from parsing_platform.field_registry import FIELD_REGISTRY_VERSION
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


def _snapshot(*, field_key="financing.pf_amount", active=True, locator=None):
    canonical_field = resolve_canonical_field(field_key).canonical_field_key
    metadata = {
        "canonicalFieldNamespaceVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
    }
    if canonical_field.startswith("financing."):
        unit = "KRW"
        value = 49_000_000_000
        metadata["basis"] = {
            "vatTreatment": "not_applicable",
            "amountScope": "not_applicable",
            "allocationBasis": "not_applicable",
            "currency": "KRW",
            "financingBalanceBasis": "commitment",
        }
    elif canonical_field.startswith(("revenue.", "cost.")):
        unit = "KRW"
        value = 49_000_000_000
        metadata["basis"] = {
            "vatTreatment": "included",
            "amountScope": "gross",
            "allocationBasis": "direct",
            "currency": "KRW",
        }
    else:
        unit = "SQM"
        value = 1_000
    entity_id = "parcel-1" if canonical_field.startswith("land.") else None
    version_context = {
        "parserVersion": "2.0.0",
        "ontologyVersion": "realytics-real-estate-domain-standard-v1",
        "promptVersion": "1.0.0",
        "modelVersion": "deterministic",
    }
    selection = {
        "selectionId": "selection-1",
        "tenantId": "tenant-1",
        "projectId": "project-1",
        "fieldKey": field_key,
        "entityId": entity_id,
        "candidateId": "candidate-1",
        "reviewDecisionId": "decision-1",
        "approvalStatus": "approved",
        "conflictStatus": "none",
        "normalizedValue": value,
        "unit": unit,
        "selectedBy": "reviewer-1",
        "selectedAt": "2026-07-15T00:00:00+00:00",
        "metadata": metadata,
    }
    return {
        "tenantId": "tenant-1",
        "projectId": "project-1",
        "snapshotId": "snapshot-1",
        "snapshotVersion": 1,
        "purpose": "pf_review",
        "inputSchemaVersion": "realytics-parser-approved-inputs-v1",
        "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
        "canonicalFieldNamespaceVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
        "approvalStatus": "approved",
        "validityStatus": "active" if active else "invalidated",
        "approvedAt": "2026-07-15T00:00:00+00:00",
        "approvedBy": "reviewer-1",
        "selectionIds": ["selection-1"],
        "selections": [selection],
        "ontologyVersion": "realytics-real-estate-domain-standard-v1",
        "documentVersionIds": ["doc-v1"],
        "sourceHashes": [SOURCE_HASH],
        "sourceParsingRunIds": ["run-1"],
        "versionContexts": [version_context],
        "sourceDocuments": [
            {
                "documentId": "doc-1",
                "documentVersionId": "doc-v1",
                "archiveId": "archive-1",
                "objectVersion": "object-1",
                "sourceHash": SOURCE_HASH,
                "sha256": SOURCE_HASH,
            }
        ],
        "inputItems": [
            {
                "fieldKey": field_key,
                "canonicalField": field_key,
                "entityId": entity_id,
                "normalizedValue": value,
                "unit": unit,
                "authority": "submitted_document",
                "candidateId": "candidate-1",
                "selectionId": "selection-1",
                "reviewDecisionId": "decision-1",
                "parsingRunId": "run-1",
                "documentVersionId": "doc-v1",
                "sourceHash": SOURCE_HASH,
                "sha256": SOURCE_HASH,
                "versionContext": version_context,
                "sourceLocator": locator
                or {
                    "documentVersionId": "doc-v1",
                    "fileSha256": SOURCE_HASH,
                    "sheetName": "자금조달",
                    "cellReference": "D12",
                    "parserMethod": "formula",
                },
                "originType": "extracted",
                "inputFactIds": [],
                "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
                "metadata": metadata,
            }
        ],
        "inputs": {} if entity_id else {field_key: value},
        "inputsByEntity": (
            {entity_id: {field_key: value}} if entity_id else {}
        ),
    }


def _land_snapshot(*, expected_count: int, covered_count: int):
    snapshot = _snapshot(field_key="land.pnu")
    expected_entity_ids = [
        f"parcel-{index:03d}"
        for index in range(1, expected_count + 1)
    ]
    version_context = snapshot["versionContexts"][0]
    selections = []
    input_items = []
    inputs_by_entity = {}
    field_specs = (
        ("land.pnu", lambda index: f"{index:019d}", None),
        ("land.registered_area_sqm", lambda index: 1_000 + index, "SQM"),
        (
            "land.zoning.primary_use_district_code",
            lambda index: f"ZONING-{index:03d}",
            None,
        ),
    )
    sequence = 0
    for index, entity_id in enumerate(
        expected_entity_ids[:covered_count],
        start=1,
    ):
        inputs_by_entity[entity_id] = {}
        for field_key, value_factory, unit in field_specs:
            sequence += 1
            value = value_factory(index)
            selection_id = f"selection-{sequence}"
            candidate_id = f"candidate-{sequence}"
            review_decision_id = f"decision-{sequence}"
            metadata = {
                "canonicalFieldNamespaceVersion": (
                    CANONICAL_FIELD_NAMESPACE_VERSION
                ),
            }
            selections.append(
                {
                    "selectionId": selection_id,
                    "tenantId": "tenant-1",
                    "projectId": "project-1",
                    "fieldKey": field_key,
                    "entityId": entity_id,
                    "candidateId": candidate_id,
                    "reviewDecisionId": review_decision_id,
                    "approvalStatus": "approved",
                    "conflictStatus": "none",
                    "normalizedValue": value,
                    "unit": unit,
                    "selectedBy": "reviewer-1",
                    "selectedAt": "2026-07-15T00:00:00+00:00",
                    "metadata": metadata,
                }
            )
            input_items.append(
                {
                    "fieldKey": field_key,
                    "canonicalField": field_key,
                    "entityId": entity_id,
                    "normalizedValue": value,
                    "unit": unit,
                    "authority": "submitted_document",
                    "candidateId": candidate_id,
                    "selectionId": selection_id,
                    "reviewDecisionId": review_decision_id,
                    "parsingRunId": "run-1",
                    "documentVersionId": "doc-v1",
                    "sourceHash": SOURCE_HASH,
                    "sha256": SOURCE_HASH,
                    "versionContext": version_context,
                    "sourceLocator": {
                        "documentVersionId": "doc-v1",
                        "fileSha256": SOURCE_HASH,
                        "sheetName": "Land",
                        "cellReference": f"A{sequence + 1}",
                        "parserMethod": "native_cell",
                    },
                    "originType": "extracted",
                    "inputFactIds": [],
                    "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
                    "metadata": metadata,
                }
            )
            inputs_by_entity[entity_id][field_key] = value

    snapshot["selectionIds"] = [
        item["selectionId"] for item in selections
    ]
    snapshot["selections"] = selections
    snapshot["inputItems"] = input_items
    snapshot["inputs"] = {}
    snapshot["inputsByEntity"] = inputs_by_entity
    snapshot["metadata"] = {
        "expectedEntityIds": expected_entity_ids,
        "expectedEntityCount": expected_count,
    }
    return snapshot


def test_handoff_executes_only_after_approved_domain_policy_is_complete():
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

    assert envelope.status == "created"
    assert envelope.to_agent == "pf_credit_pod"
    assert envelope.payload["approvedSnapshotId"] == "snapshot-1"
    assert envelope.payload["authoritativeInputBoundary"] == "approved_snapshot_only"
    assert envelope.payload["approvedInputs"][0]["selectionId"] == "selection-1"
    assert envelope.payload["approvedInputs"][0]["metadata"]["basis"][
        "financingBalanceBasis"
    ] == "commitment"
    assert envelope.payload["fieldRegistryVersion"] == FIELD_REGISTRY_VERSION
    assert envelope.payload["handoffPolicyStatus"] == "approved"
    assert envelope.payload["handoffPolicyVersion"] == (
        "realytics-domain-handoff-completeness-v2"
    )
    assert envelope.payload["missingApprovedFields"] == []
    assert (
        envelope.payload["canonicalFieldNamespaceVersion"]
        == CANONICAL_FIELD_NAMESPACE_VERSION
    )
    assert envelope.blocking_issues == []
    assert envelope.progress_impact["canExecute"] is True


def test_handoff_uses_the_snapshot_namespace_version_without_relabeling():
    snapshot = _snapshot()
    actual_namespace_version = "realytics-canonical-fact-namespace-v9"
    snapshot["canonicalFieldNamespaceVersion"] = actual_namespace_version
    snapshot["selections"][0]["metadata"][
        "canonicalFieldNamespaceVersion"
    ] = actual_namespace_version
    snapshot["inputItems"][0]["metadata"][
        "canonicalFieldNamespaceVersion"
    ] = actual_namespace_version

    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore([snapshot]),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="pf_credit",
        actor_id="reviewer-1",
    )

    assert (
        envelope.payload["canonicalFieldNamespaceVersion"]
        == actual_namespace_version
    )
    assert (
        envelope.payload["approvedInputs"][0]["metadata"][
            "canonicalFieldNamespaceVersion"
        ]
        == actual_namespace_version
    )


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
        "DOMAIN_HANDOFF_REQUIRED_APPROVED_FIELDS_MISSING",
    ]
    assert envelope.payload["approvedInputs"] == []


def test_handoff_blocks_when_approved_snapshot_is_incomplete_for_domain_policy():
    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore(
            [_snapshot(field_key="feasibility.total_revenue")]
        ),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="development_feasibility",
        actor_id="reviewer-1",
    )

    assert envelope.status == "blocked"
    assert envelope.payload["handoffPolicyStatus"] == "approved"
    assert envelope.blocking_issues == [
        "DOMAIN_HANDOFF_REQUIRED_APPROVED_FIELDS_MISSING"
    ]
    assert envelope.payload["missingApprovedFields"] == [
        "program.gross_floor_area_sqm",
        "cost.total_project_cost",
    ]
    approved_input = envelope.payload["approvedInputs"][0]
    assert approved_input["fieldKey"] == "revenue.total_sales_revenue"
    assert approved_input["canonicalField"] == "revenue.total_sales_revenue"
    assert approved_input["metadata"]["canonicalFieldLineage"]["inputFieldKey"] == (
        "feasibility.total_revenue"
    )
    assert envelope.progress_impact["canExecute"] is False


def test_land_cost_alone_cannot_satisfy_feasibility_exact_fields():
    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore(
            [_snapshot(field_key="cost.land_cost")]
        ),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="development_feasibility",
        actor_id="reviewer-1",
    )

    assert envelope.status == "blocked"
    assert envelope.progress_impact["canExecute"] is False
    assert envelope.payload["missingApprovedFields"] == [
        "program.gross_floor_area_sqm",
        "revenue.total_sales_revenue",
        "cost.total_project_cost",
    ]


def test_arbitrary_financing_fact_cannot_satisfy_pf_credit_exact_field():
    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore(
            [_snapshot(field_key="financing.senior.loan_amount")]
        ),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="pf_credit",
        actor_id="reviewer-1",
    )

    assert envelope.status == "blocked"
    assert envelope.progress_impact["canExecute"] is False
    assert envelope.payload["missingApprovedFields"] == [
        "financing.pf_amount"
    ]


def test_prefix_scoped_input_cannot_execute_without_an_exact_approved_policy():
    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore(
            [_snapshot(field_key="cost.total_project_cost")]
        ),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="cost_schedule",
        actor_id="reviewer-1",
    )

    assert envelope.status == "blocked"
    assert envelope.progress_impact["canExecute"] is False
    assert envelope.payload["approvedInputs"]
    assert envelope.payload["handoffPolicyStatus"] == "needs_domain_approval"
    assert envelope.blocking_issues == [
        "DOMAIN_HANDOFF_COMPLETENESS_POLICY_NOT_APPROVED"
    ]


def test_land_handoff_blocks_when_one_of_112_expected_parcels_is_missing():
    envelope = build_approved_snapshot_handoff(
        record_store=SnapshotStore(
            [_land_snapshot(expected_count=112, covered_count=111)]
        ),
        tenant_id="tenant-1",
        project_id="project-1",
        snapshot_id="snapshot-1",
        target_domain="land_legal",
        actor_id="reviewer-1",
    )

    assert envelope.status == "blocked"
    assert envelope.progress_impact["canExecute"] is False
    assert envelope.blocking_issues == [
        "DOMAIN_HANDOFF_ENTITY_COVERAGE_INCOMPLETE"
    ]
    coverage = envelope.payload["entityScopedCoverage"][0]
    assert coverage["groupId"] == "parcel_core"
    assert coverage["expectedEntityCount"] == 112
    assert coverage["completeEntityCount"] == 111
    assert coverage["missingExpectedEntityIds"] == ["parcel-112"]
    assert coverage["missingFieldsByEntity"]["parcel-112"] == [
        "land.pnu",
        "land.registered_area_sqm",
        "land.zoning.primary_use_district_code",
    ]


def test_handoff_rejects_missing_or_mismatched_evidence():
    snapshot = _snapshot(locator={"artifactKey": "parser-output"})
    with pytest.raises(ParserV2ApprovalError, match="strict ApprovedInputSnapshot"):
        build_approved_snapshot_handoff(
            record_store=SnapshotStore([snapshot]),
            tenant_id="tenant-1",
            project_id="project-1",
            snapshot_id="snapshot-1",
            target_domain="pf_credit",
            actor_id="reviewer-1",
        )


def test_handoff_rejects_locator_bound_to_another_source_hash():
    snapshot = _snapshot(
        locator={
            "documentVersionId": "doc-v1",
            "fileSha256": "b" * 64,
            "sheetName": "Funding",
            "cellReference": "D12",
            "parserMethod": "formula",
        }
    )
    with pytest.raises(ParserV2ApprovalError, match="source hash"):
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
