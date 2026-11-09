from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from parsing_platform.canonical_fields import CANONICAL_FIELD_NAMESPACE_VERSION
from parsing_platform.field_registry import FIELD_REGISTRY_VERSION
from parsing_platform import (
    CANDIDATE_SET_SEAL_SCHEMA_VERSION,
    ApprovalStatus,
    ApprovedInputSnapshot,
    CandidateSetSeal,
    CanonicalSelection,
    ConflictStatus,
    DocumentVersion,
    ExecutionStatus,
    OriginType,
    ParsedFactCandidate,
    ParsingRun,
    PresenceStatus,
    ReviewDecision,
    ReviewStatus,
    SourceLocator,
    generate_contract_json_schemas,
)


NOW = "2026-07-15T01:00:00Z"
LATER = "2026-07-15T01:05:00Z"
SHA256 = "a" * 64
FINANCING_METADATA = {
    "canonicalFieldNamespaceVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
    "basis": {
        "vatTreatment": "not_applicable",
        "amountScope": "not_applicable",
        "allocationBasis": "not_applicable",
        "currency": "KRW",
        "financingBalanceBasis": "commitment",
    },
}


def test_candidate_set_seal_counts_are_part_of_the_public_contract():
    payload = {
        "sealId": "seal-1",
        "tenantId": "tenant-001",
        "projectId": "project-001",
        "parsingRunId": "run-001",
        "documentVersionId": "document-version-001",
        "candidateIds": ["candidate-1", "candidate-2"],
        "candidateCount": 2,
        "conflictIds": ["conflict-1"],
        "conflictCount": 1,
        "collectionManifestHash": SHA256,
        "candidateSetHash": SHA256,
        "sealedAt": NOW,
        "sealedBy": "parser-worker-1",
    }
    parsed = CandidateSetSeal.model_validate(payload)
    assert parsed.candidate_count == 2
    assert parsed.schema_version == CANDIDATE_SET_SEAL_SCHEMA_VERSION
    with pytest.raises(ValidationError, match="candidate-set-seal-v2"):
        CandidateSetSeal.model_validate(
            {
                **payload,
                "schemaVersion": "realytics-candidate-set-seal-v1",
            }
        )
    with pytest.raises(ValidationError, match="candidateCount must match"):
        CandidateSetSeal.model_validate({**payload, "candidateCount": 1})


def test_candidate_set_seal_v2_supports_empty_immutable_collection():
    parsed = CandidateSetSeal.model_validate(
        {
            "sealId": "seal-empty",
            "tenantId": "tenant-001",
            "projectId": "project-001",
            "parsingRunId": "run-empty",
            "documentVersionId": "document-version-empty",
            "candidateIds": [],
            "candidateCount": 0,
            "expectedModuleIds": ["feasibility_reconciliation"],
            "completedModuleIds": ["feasibility_reconciliation"],
            "unresolvedModuleIds": [],
            "reconciliationSourceCandidateIds": [],
            "reconciliationBound": True,
            "collectionManifestHash": SHA256,
            "candidateSetHash": SHA256,
            "sealedAt": NOW,
            "sealedBy": "parser-worker-1",
        }
    )
    assert parsed.candidate_ids == []
    assert parsed.reconciliation_bound is True


def _candidate_payload(**overrides):
    payload = {
        "candidateId": "fact-001",
        "tenantId": "tenant-001",
        "projectId": "project-001",
        "parsingRunId": "parse-001",
        "documentVersionId": "docv-001",
        "fieldKey": "financing.senior.loan_amount",
        "rawValue": "1,000,000,000원",
        "normalizedValue": 1_000_000_000,
        "unit": "KRW",
        "originType": "extracted",
        "presenceStatus": "present",
        "reviewStatus": "pending",
        "approvalStatus": "not_requested",
        "conflictStatus": "none",
        "confidence": 0.99,
        "authority": "executed_contract",
        "effectiveDate": "2026-07-01",
        "asOfDate": "2026-07-15",
        "evidence": [
            {
                "documentVersionId": "docv-001",
                "pageNumber": 12,
                "rawTextExcerpt": "선순위 대출금 1,000,000,000원",
                "rawTextSha256": SHA256,
                "parserMethod": "native_pdf_text",
            },
            {
                "documentVersionId": "docv-001",
                "pageNumber": 13,
                "tableId": "capital-stack",
                "rowIndex": 2,
                "columnIndex": 3,
                "parserMethod": "pdf_table",
            },
        ],
        "validationCodes": [],
        "parserVersion": "parser-core@2.0.0",
        "ontologyVersion": "real-estate-finance@2.0.0",
        "promptVersion": "pf-term-sheet@2.0.0",
        "modelVersion": "gpt-5.0-2026-07-01",
        "createdAt": NOW,
    }
    payload.update(overrides)
    return payload


def _selection_payload(**overrides):
    payload = {
        "selectionId": "selection-001",
        "tenantId": "tenant-001",
        "projectId": "project-001",
        "fieldKey": "financing.senior.loan_amount",
        "candidateId": "fact-001",
        "reviewDecisionId": "review-001",
        "approvalStatus": "approved",
        "conflictStatus": "none",
        "normalizedValue": 1_000_000_000,
        "unit": "KRW",
        "selectedBy": "user-001",
        "selectedAt": LATER,
        "metadata": deepcopy(FINANCING_METADATA),
    }
    payload.update(overrides)
    return payload


def test_candidate_uses_camel_case_and_preserves_multiple_precise_locators():
    candidate = ParsedFactCandidate.model_validate(_candidate_payload())

    assert candidate.origin_type == OriginType.EXTRACTED
    assert candidate.presence_status == PresenceStatus.PRESENT
    assert len(candidate.evidence) == 2
    assert candidate.evidence[1].table_id == "capital-stack"

    wire = candidate.model_dump(mode="json", by_alias=True)
    assert wire["parserVersion"] == "parser-core@2.0.0"
    assert wire["ontologyVersion"] == "real-estate-finance@2.0.0"
    assert wire["promptVersion"] == "pf-term-sheet@2.0.0"
    assert wire["modelVersion"] == "gpt-5.0-2026-07-01"
    assert "parser_version" not in wire
    assert wire["evidence"][1]["rowIndex"] == 2


@pytest.mark.parametrize(
    "missing_key",
    [
        "formulaId",
        "formulaVersion",
        "inputFactIds",
        "calculationRunId",
        "calculationEngineVersion",
        "calculationOutputPath",
        "calculationOutputHash",
        "inputSnapshotHash",
    ],
)
def test_derived_candidate_requires_complete_calculation_lineage(missing_key):
    payload = _candidate_payload(
        originType="derived",
        formulaId="ltv",
        formulaVersion="2.1.0",
        inputFactIds=["fact-loan", "fact-value"],
        calculationRunId="calc-001",
        calculationEngineVersion="deterministic-pf-metrics@2.1.0",
        calculationOutputPath="$.metrics.ltv",
        calculationOutputHash="b" * 64,
        inputSnapshotHash="c" * 64,
    )
    payload.pop(missing_key)

    with pytest.raises(ValidationError, match="derived fact requires"):
        ParsedFactCandidate.model_validate(payload)


def test_derived_candidate_binds_deterministic_output_and_input_snapshot_hashes():
    candidate = ParsedFactCandidate.model_validate(
        _candidate_payload(
            originType="derived",
            formulaId="ltv",
            formulaVersion="2.1.0",
            inputFactIds=["fact-loan", "fact-value"],
            calculationRunId="calc-001",
            calculationEngineVersion="deterministic-pf-metrics@2.1.0",
            calculationOutputPath="$.metrics.ltv",
            calculationOutputHash="b" * 64,
            inputSnapshotHash="c" * 64,
        )
    )

    assert candidate.calculation_engine_version == "deterministic-pf-metrics@2.1.0"
    assert candidate.calculation_output_path == "$.metrics.ltv"
    assert candidate.calculation_output_hash == "b" * 64
    assert candidate.input_snapshot_hash == "c" * 64

    with pytest.raises(ValidationError, match="originType=derived"):
        ParsedFactCandidate.model_validate(
            _candidate_payload(calculationOutputHash="b" * 64)
        )


def test_non_derived_candidate_rejects_formula_metadata():
    with pytest.raises(ValidationError, match="only for originType=derived"):
        ParsedFactCandidate.model_validate(_candidate_payload(formulaId="ltv"))


def test_candidate_enforces_presence_evidence_and_independent_approval_state():
    with pytest.raises(ValidationError, match="source locator"):
        ParsedFactCandidate.model_validate(_candidate_payload(evidence=[]))

    with pytest.raises(ValidationError, match="normalizedValue=null"):
        ParsedFactCandidate.model_validate(
            _candidate_payload(presenceStatus="not_disclosed", evidence=[])
        )

    approved = ParsedFactCandidate.model_validate(
        _candidate_payload(
            reviewStatus="approved",
            approvalStatus="approved",
            reviewDecisionId="review-001",
        )
    )
    assert approved.review_status == ReviewStatus.APPROVED
    assert approved.approval_status == ApprovalStatus.APPROVED

    with pytest.raises(ValidationError, match="approved review"):
        ParsedFactCandidate.model_validate(
            _candidate_payload(approvalStatus="approved", reviewStatus="pending")
        )


def test_approved_snapshot_rejects_pending_or_duplicate_selections():
    pending = CanonicalSelection.model_validate(
        _selection_payload(
            selectionId="selection-pending",
            approvalStatus="pending",
            reviewDecisionId=None,
        )
    )
    snapshot_payload = {
        "snapshotId": "snapshot-001",
        "tenantId": "tenant-001",
        "projectId": "project-001",
        "approvalStatus": "approved",
        "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
        "canonicalFieldNamespaceVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
        "selections": [pending.model_dump(mode="json", by_alias=True)],
        "ontologyVersion": "realytics-real-estate-domain-standard-v1",
        "sourceParsingRunIds": ["parse-001"],
        "approvedBy": "user-001",
        "approvedAt": LATER,
    }

    with pytest.raises(ValidationError, match="approved selections only"):
        ApprovedInputSnapshot.model_validate(snapshot_payload)

    first = _selection_payload()
    duplicate = deepcopy(first)
    duplicate["selectionId"] = "selection-002"
    duplicate["candidateId"] = "fact-002"
    snapshot_payload["selections"] = [first, duplicate]
    with pytest.raises(ValidationError, match="same fieldKey/entityId"):
        ApprovedInputSnapshot.model_validate(snapshot_payload)

    snapshot_payload["selections"] = [first]
    snapshot = ApprovedInputSnapshot.model_validate(snapshot_payload)
    assert snapshot.approval_status == ApprovalStatus.APPROVED
    assert snapshot.selections[0].approval_status == ApprovalStatus.APPROVED


def test_run_lifecycle_keeps_execution_and_approval_independent():
    base = {
        "parsingRunId": "parse-001",
        "tenantId": "tenant-001",
        "projectId": "project-001",
        "documentVersionId": "docv-001",
        "executionStatus": "running",
        "approvalStatus": "not_requested",
        "parserVersion": "parser-core@2.0.0",
        "ontologyVersion": "realytics-real-estate-domain-standard-v1",
        "promptVersion": "pf-term-sheet@2.0.0",
        "modelVersion": "gpt-5.0-2026-07-01",
        "profileId": "pf-review.standard",
        "profileVersion": "2.0.0",
        "planHash": "a" * 64,
        "inputHash": "b" * 64,
        "idempotencyKey": "parse:docv-001:v2",
        "requestedAt": NOW,
        "startedAt": NOW,
    }
    running = ParsingRun.model_validate(base)
    assert running.execution_status == ExecutionStatus.RUNNING
    assert running.approval_status == ApprovalStatus.NOT_REQUESTED

    with pytest.raises(ValidationError, match="only after successful"):
        ParsingRun.model_validate({**base, "approvalStatus": "pending"})

    completed = ParsingRun.model_validate(
        {
            **base,
            "executionStatus": "completed",
            "approvalStatus": "pending",
            "completedAt": LATER,
        }
    )
    assert completed.execution_status == ExecutionStatus.COMPLETED
    assert completed.approval_status == ApprovalStatus.PENDING


def test_document_hash_and_locator_geometry_are_validated():
    document = DocumentVersion.model_validate(
        {
            "documentVersionId": "docv-001",
            "documentId": "document-001",
            "tenantId": "tenant-001",
            "projectId": "project-001",
            "archiveId": "archive-001",
            "objectVersion": "s3-version-001",
            "sha256": SHA256,
            "sourceHash": SHA256,
            "fileName": "term-sheet.pdf",
            "originalFileName": "term-sheet.pdf",
            "mediaType": "application/pdf",
            "sizeBytes": 1234,
            "versionNumber": 1,
            "sourceAuthority": "executed_contract",
            "ingestedAt": NOW,
            "ingestedBy": "user-001",
        }
    )
    assert document.document_id == "document-001"
    assert document.sha256 == SHA256

    with pytest.raises(ValidationError):
        DocumentVersion.model_validate(
            {**document.model_dump(mode="json", by_alias=True), "sha256": "not-a-hash"}
        )

    with pytest.raises(ValidationError, match="sourceHash"):
        DocumentVersion.model_validate(
            {
                **document.model_dump(mode="json", by_alias=True),
                "sourceHash": "b" * 64,
            }
        )

    with pytest.raises(ValidationError, match="bbox"):
        SourceLocator.model_validate(
            {
                "documentVersionId": "docv-001",
                "pageNumber": 1,
                "bbox": [10, 20, 5, 30],
                "coordinateSpace": "pdf_points_top_left",
            }
        )


def test_review_decision_accepts_materialized_identity_and_sequence_fields():
    payload = {
        "reviewDecisionId": "review-001",
        "tenantId": "tenant-001",
        "projectId": "project-001",
        "candidateId": "fact-001",
        "parsingRunId": "parse-001",
        "documentId": "document-001",
        "documentVersionId": "docv-001",
        "sourceHash": SHA256,
        "fieldKey": "capital_stack.senior.loan_amount",
        "conflictIds": [],
        "action": "approve",
        "resultingReviewStatus": "approved",
        "resultingApprovalStatus": "approved",
        "reviewStatus": "approved",
        "approvalStatus": "approved",
        "reviewerId": "user-001",
        "decidedAt": LATER,
        "decisionSequence": 1,
        "reason": "Evidence and source authority checked.",
    }
    decision = ReviewDecision.model_validate(payload)
    assert decision.document_id == "document-001"
    assert decision.decision_sequence == 1

    with pytest.raises(ValidationError, match="reviewStatus"):
        ReviewDecision.model_validate({**payload, "reviewStatus": "pending"})
    with pytest.raises(ValidationError):
        ReviewDecision.model_validate({**payload, "decisionSequence": 0})


def test_approved_snapshot_accepts_and_cross_checks_materialized_input_package():
    version_context = {
        "parserVersion": "parser-core@2.0.0",
        "ontologyVersion": "realytics-real-estate-domain-standard-v1",
        "promptVersion": "pf-term-sheet@2.0.0",
        "modelVersion": "deterministic-only",
    }
    source_locator = {
        "documentVersionId": "docv-001",
        "fileSha256": SHA256,
        "pageNumber": 12,
        "paragraphIndex": 0,
        "rawTextSha256": SHA256,
        "parserMethod": "native_pdf_text",
    }
    payload = {
        "snapshotId": "snapshot-materialized-001",
        "snapshotVersion": 1,
        "tenantId": "tenant-001",
        "projectId": "project-001",
        "purpose": "monthly_cash_flow",
        "inputSchemaVersion": "realytics-parser-approved-inputs-v1",
        "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
        "canonicalFieldNamespaceVersion": CANONICAL_FIELD_NAMESPACE_VERSION,
        "approvalStatus": "approved",
        "validityStatus": "active",
        "selectionIds": ["selection-001"],
        "selections": [_selection_payload()],
        "ontologyVersion": "realytics-real-estate-domain-standard-v1",
        "documentVersionIds": ["docv-001"],
        "sourceHashes": [SHA256],
        "sourceDocuments": [
            {
                "documentId": "document-001",
                "documentVersionId": "docv-001",
                "archiveId": SHA256,
                "objectVersion": "object-version-001",
                "sourceHash": SHA256,
                "sha256": SHA256,
            }
        ],
        "inputItems": [
            {
                "fieldKey": "financing.senior.loan_amount",
                "canonicalField": "financing.senior.loan_amount",
                "normalizedValue": 1_000_000_000,
                "unit": "KRW",
                "authority": "executed_contract",
                "candidateId": "fact-001",
                "selectionId": "selection-001",
                "reviewDecisionId": "review-001",
                "parsingRunId": "parse-001",
                "documentVersionId": "docv-001",
                "sourceHash": SHA256,
                "sha256": SHA256,
                "sourceLocator": source_locator,
                "originType": "extracted",
                "inputFactIds": [],
                "fieldRegistryVersion": FIELD_REGISTRY_VERSION,
                "versionContext": version_context,
                "metadata": deepcopy(FINANCING_METADATA),
            }
        ],
        "inputs": {"financing.senior.loan_amount": 1_000_000_000},
        "inputsByEntity": {},
        "versionContexts": [version_context],
        "sourceParsingRunIds": ["parse-001"],
        "approvedBy": "user-001",
        "approvedAt": LATER,
    }

    snapshot = ApprovedInputSnapshot.model_validate(payload)
    assert snapshot.snapshot_version == 1
    assert snapshot.purpose == "monthly_cash_flow"
    assert snapshot.input_items and snapshot.input_items[0].source_hash == SHA256
    assert snapshot.input_items[0].metadata == snapshot.selections[0].metadata

    mismatched_metadata = deepcopy(payload)
    mismatched_metadata["inputItems"][0]["metadata"]["basis"][
        "financingBalanceBasis"
    ] = "drawn"
    with pytest.raises(
        ValidationError,
        match="preserve their canonical selection identity and value",
    ):
        ApprovedInputSnapshot.model_validate(mismatched_metadata)

    without_namespace_version = deepcopy(payload)
    without_namespace_version.pop("canonicalFieldNamespaceVersion")
    with pytest.raises(
        ValidationError,
        match="canonicalFieldNamespaceVersion",
    ):
        ApprovedInputSnapshot.model_validate(without_namespace_version)

    with pytest.raises(ValidationError, match="selectionIds must match"):
        ApprovedInputSnapshot.model_validate(
            {**payload, "selectionIds": ["another-selection"]}
        )

    invalidated = ApprovedInputSnapshot.model_validate(
        {**payload, "approvalStatus": "superseded", "validityStatus": "invalidated"}
    )
    assert invalidated.approval_status == ApprovalStatus.SUPERSEDED
    with pytest.raises(ValidationError, match="active ApprovedInputSnapshot"):
        ApprovedInputSnapshot.model_validate(
            {**payload, "approvalStatus": "superseded", "validityStatus": "active"}
        )


def test_all_public_contracts_generate_serializable_camel_case_json_schema():
    schemas = generate_contract_json_schemas()

    expected = {
        "DocumentVersion",
        "ParsingRun",
        "CandidateSetSeal",
        "SourceLocator",
        "ParsedFactCandidate",
        "SourceConflict",
        "ReviewDecision",
        "CanonicalSelection",
        "ApprovedInputSnapshot",
        "ValidationFinding",
        "ValidationFindingEvent",
    }
    assert set(schemas) == expected
    assert "parserVersion" in schemas["ParsedFactCandidate"]["properties"]
    assert "executionStatus" in schemas["ParsingRun"]["properties"]
    assert "approvalStatus" in schemas["ParsingRun"]["properties"]
    assert "reviewStatus" in schemas["ParsedFactCandidate"]["properties"]
    assert "originType" in schemas["ParsedFactCandidate"]["properties"]
    assert "presenceStatus" in schemas["ParsedFactCandidate"]["properties"]
    assert "conflictStatus" in schemas["ParsedFactCandidate"]["properties"]
    assert "calculationOutputHash" in schemas["ParsedFactCandidate"]["properties"]
    assert "documentId" in schemas["DocumentVersion"]["properties"]
    assert "decisionSequence" in schemas["ReviewDecision"]["properties"]
    assert "inputItems" in schemas["ApprovedInputSnapshot"]["properties"]
    json.dumps(schemas, ensure_ascii=False)
