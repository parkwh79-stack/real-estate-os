from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from parsing_platform.contracts import (
    ApprovedInputSnapshot,
    CanonicalSelection,
    DocumentVersion,
    ParsedFactCandidate,
    ParsingRun,
    ReviewDecision,
)
from use_cases.parser_v2_records import (
    APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
    CANONICAL_SELECTION_RECORD_TYPE,
    DOCUMENT_VERSION_RECORD_TYPE,
    PARSED_FACT_CANDIDATE_RECORD_TYPE,
    PARSING_RUN_RECORD_TYPE,
    REVIEW_DECISION_RECORD_TYPE,
    SOURCE_CONFLICT_RECORD_TYPE,
    ImmutableRecordConflict,
    ParserV2ApprovalError,
    ParserV2RecordError,
    ParserV2RecordStore,
    ParserV2ReferenceError,
    approved_input_snapshot_wire_payload,
)


TENANT = "tenant-a"
PROJECT = "project-a"
ACTOR = "reviewer-a"
SNAPSHOT_APPROVER = "approver-b"
HASH_A = "a" * 64
HASH_B = "b" * 64


class TickClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 15, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


class InMemoryDealStateStub:
    """A deliberately overwriting backend; the store must guard immutability."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict]] = {}
        self.upsert_count = 0

    def upsert(self, project_id: str, record: dict) -> dict:
        self.upsert_count += 1
        saved = copy.deepcopy(record)
        saved.setdefault("projectId", project_id)
        saved.setdefault("updatedAt", saved.get("createdAt"))
        self.rows.setdefault(project_id, {})[saved["recordId"]] = saved
        return copy.deepcopy(saved)

    def list(self, project_id: str, **filters) -> list[dict]:
        records = list(self.rows.get(project_id, {}).values())
        record_type = filters.get("record_type")
        if record_type:
            records = [item for item in records if item.get("recordType") == record_type]
        return copy.deepcopy(records[: int(filters.get("limit") or 200)])


def _store() -> tuple[ParserV2RecordStore, InMemoryDealStateStub]:
    backend = InMemoryDealStateStub()
    return (
        ParserV2RecordStore(
            upsert_fn=backend.upsert,
            list_fn=backend.list,
            clock=TickClock(),
        ),
        backend,
    )


def _json_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _document(
    store: ParserV2RecordStore,
    *,
    version_id: str = "docv-1",
    source_hash: str = HASH_A,
    predecessor: str | None = None,
    version_number: int = 1,
) -> dict:
    return store.create_document_version(
        tenant_id=TENANT,
        project_id=PROJECT,
        document_id="document-1",
        document_version_id=version_id,
        actor_id=ACTOR,
        archive_id=f"archive-{version_id}",
        object_version=f"object-{version_id}",
        source_hash=source_hash,
        media_type="application/pdf",
        original_file_name="사업계획서.pdf",
        size_bytes=1234,
        version_number=version_number,
        predecessor_version_id=predecessor,
    )


def _run(
    store: ParserV2RecordStore,
    *,
    run_id: str = "run-1",
    document_version_id: str = "docv-1",
    parser_version: str = "parser-2.0.0",
) -> dict:
    return store.create_parsing_run(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=run_id,
        document_version_id=document_version_id,
        actor_id=ACTOR,
        parser_version=parser_version,
        ontology_version="ontology-2.0.0",
        prompt_version="prompt-business-plan-2.0.0",
        model_version="gpt-test-1",
        profile_id="test.profile",
        profile_version="1.0.0",
        plan_hash="e" * 64,
        input_hash="f" * 64,
        requested_modules=["document_intake", "financial_table"],
    )


def _candidate(
    store: ParserV2RecordStore,
    *,
    candidate_id: str = "candidate-1",
    value: int = 100_000_000_000,
    run_id: str = "run-1",
    document_version_id: str = "docv-1",
) -> dict:
    return store.create_candidate(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=run_id,
        document_version_id=document_version_id,
        candidate_id=candidate_id,
        actor_id="parser-worker-1",
        canonical_field="feasibility.total_revenue",
        value=value,
        raw_value=f"{value:,}원",
        unit="KRW",
        confidence=0.98,
        evidence=[
            {
                "documentVersionId": document_version_id,
                "pageNumber": 12,
                "tableId": "sales-summary",
                "rowIndex": 3,
                "columnIndex": 2,
                "rawTextSha256": "c" * 64,
                "parserMethod": "native_pdf_table",
            },
            {
                "documentVersionId": document_version_id,
                "pageNumber": 13,
                "paragraphIndex": 4,
                "rawTextSha256": "d" * 64,
                "parserMethod": "native_pdf_text",
            },
        ],
        target_modules=["feasibility_cashflow", "pf_credit"],
        effective_date="2026-06-30",
        as_of_date="2026-07-01",
        metadata={"extractionProfile": "business_plan_v2"},
    )


def _ready_candidate_store() -> tuple[ParserV2RecordStore, InMemoryDealStateStub]:
    store, backend = _store()
    _document(store)
    _run(store)
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="running",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="completed",
    )
    return store, backend


def _seal_run(store: ParserV2RecordStore, run_id: str = "run-1") -> dict:
    candidate_ids = sorted(
        item["candidateId"]
        for item in store.list_candidates(TENANT, PROJECT, run_id=run_id)
    )
    conflict_ids = sorted(
        item["conflictId"]
        for item in store.list_source_conflicts(TENANT, PROJECT)
        if run_id in (item.get("sourceParsingRunIds") or [])
        or item.get("parsingRunId") == run_id
    )
    return store.seal_candidate_set(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=run_id,
        actor_id="parser-worker-1",
        expected_candidate_ids=candidate_ids,
        expected_conflict_ids=conflict_ids,
    )


def test_parsing_run_lifecycle_is_append_only_and_materialized() -> None:
    store, backend = _store()
    document = _document(store)
    run = _run(store)

    running = store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="running",
    )
    completed = store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="completed",
    )
    current = store.get_parsing_run(TENANT, PROJECT, "run-1")

    assert run["executionStatus"] == "pending"
    assert running["fromExecutionStatus"] == "pending"
    assert completed["fromExecutionStatus"] == "running"
    assert current["executionStatus"] == "completed"
    assert current["approvalStatus"] == "not_requested"
    assert current["startedAt"] and current["completedAt"]
    assert len(current["transitionEvents"]) == 2

    raw = backend.list(PROJECT, record_type=PARSING_RUN_RECORD_TYPE, limit=100)
    original = next(item for item in raw if item.get("eventKind") == "created")
    assert original["executionStatus"] == "pending"
    assert len(raw) == 3

    before_retry = backend.upsert_count
    assert store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="completed",
    )["recordId"] == completed["recordId"]
    assert backend.upsert_count == before_retry

    with pytest.raises(ParserV2RecordError, match="illegal parsing run transition"):
        store.append_parsing_run_event(
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id="run-1",
            actor_id="worker-1",
            execution_status="running",
        )

    DocumentVersion.model_validate(
        {
            key: document[key]
            for key in (
                "documentVersionId",
                "tenantId",
                "projectId",
                "archiveId",
                "objectVersion",
                "sha256",
                "fileName",
                "mediaType",
                "sizeBytes",
                "versionNumber",
                "sourceAuthority",
                "ingestedAt",
                "ingestedBy",
            )
        }
    )
    ParsingRun.model_validate(
        {
            key: current[key]
            for key in (
                "parsingRunId",
                "tenantId",
                "projectId",
                "documentVersionId",
                "executionStatus",
                "approvalStatus",
                "parserVersion",
                "ontologyVersion",
                "promptVersion",
                "modelVersion",
                "profileId",
                "profileVersion",
                "planHash",
                "inputHash",
                "idempotencyKey",
                "attemptNumber",
                "requestedAt",
                "startedAt",
                "completedAt",
                "errorCodes",
            )
        }
    )


def test_candidate_is_immutable_idempotent_multilocator_and_reparse_uses_new_ids() -> None:
    store, backend = _ready_candidate_store()
    candidate = _candidate(store)
    assert len(candidate["evidence"]) == 2
    assert candidate["sourceLocator"] == candidate["evidence"][0]
    assert candidate["parsingRunId"] == "run-1"
    assert candidate["approvalStatus"] == "not_requested"
    assert candidate["rawValue"].endswith("원")
    assert candidate["normalizedValue"] == 100_000_000_000
    assert candidate["effectiveDate"] == "2026-06-30"
    assert candidate["metadata"]["extractionProfile"] == "business_plan_v2"

    before_retry = backend.upsert_count
    same = _candidate(store)
    assert same["recordId"] == candidate["recordId"]
    assert backend.upsert_count == before_retry

    with pytest.raises(ImmutableRecordConflict, match="immutable Parser V2 ID"):
        _candidate(store, value=101_000_000_000)

    _document(
        store,
        version_id="docv-2",
        source_hash=HASH_B,
        predecessor="docv-1",
        version_number=2,
    )
    _run(
        store,
        run_id="run-2",
        document_version_id="docv-2",
        parser_version="parser-2.1.0",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-2",
        actor_id="worker-2",
        execution_status="running",
    )
    reparsed = _candidate(
        store,
        candidate_id="candidate-2",
        value=101_000_000_000,
        run_id="run-2",
        document_version_id="docv-2",
    )
    assert reparsed["recordId"] != candidate["recordId"]
    assert reparsed["sourceHash"] == HASH_B
    assert reparsed["parserVersion"] == "parser-2.1.0"

    ParsedFactCandidate.model_validate(
        {
            key: candidate[key]
            for key in (
                "candidateId",
                "tenantId",
                "projectId",
                "parsingRunId",
                "documentVersionId",
                "fieldKey",
                "rawValue",
                "normalizedValue",
                "unit",
                "originType",
                "presenceStatus",
                "reviewStatus",
                "approvalStatus",
                "conflictStatus",
                "confidence",
                "authority",
                "effectiveDate",
                "asOfDate",
                "evidence",
                "validationCodes",
                "parserVersion",
                "ontologyVersion",
                "promptVersion",
                "modelVersion",
                "formulaId",
                "formulaVersion",
                "inputFactIds",
                "calculationRunId",
                "supersedesCandidateId",
                "createdAt",
                "metadata",
            )
        }
    )


def test_open_conflict_blocks_selection_until_explicit_resolution() -> None:
    store, _ = _ready_candidate_store()
    first = _candidate(store)
    second = _candidate(store, candidate_id="candidate-2", value=102_000_000_000)
    conflict = store.create_source_conflict(
        tenant_id=TENANT,
        project_id=PROJECT,
        conflict_id="conflict-1",
        actor_id="reconciler-1",
        canonical_field="feasibility.total_revenue",
        candidate_ids=[first["candidateId"], second["candidateId"]],
        conflict_code="SOURCE_VALUE_MISMATCH",
    )
    _seal_run(store)
    decision = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=first["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="체결본 및 원본 셀 대조 완료",
        conflict_ids=[conflict["conflictId"]],
    )

    with pytest.raises(ParserV2ApprovalError, match="open source conflicts"):
        store.create_canonical_selection(
            tenant_id=TENANT,
            project_id=PROJECT,
            selection_id="selection-1",
            actor_id=ACTOR,
            candidate_id=first["candidateId"],
            review_decision_id=decision["reviewDecisionId"],
        )

    resolution = store.append_source_conflict_resolution(
        tenant_id=TENANT,
        project_id=PROJECT,
        conflict_id="conflict-1",
        actor_id=ACTOR,
        review_decision_id=decision["reviewDecisionId"],
        resolution_status="resolved",
        notes="candidate-1을 canonical source로 선택",
    )
    assert resolution["eventKind"] == "conflict_resolution"
    assert store.get_source_conflict(TENANT, PROJECT, "conflict-1")[
        "conflictStatus"
    ] == "resolved"

    selection = store.create_canonical_selection(
        tenant_id=TENANT,
        project_id=PROJECT,
        selection_id="selection-1",
        actor_id=ACTOR,
        candidate_id=first["candidateId"],
        review_decision_id=decision["reviewDecisionId"],
    )
    assert selection["approvalStatus"] == "approved"

    losing_decision = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=second["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Reviewed but not selected as the conflict winner.",
        conflict_ids=[conflict["conflictId"]],
    )
    with pytest.raises(ParserV2ApprovalError, match="not the approved winner"):
        store.create_canonical_selection(
            tenant_id=TENANT,
            project_id=PROJECT,
            selection_id="selection-loser",
            actor_id=ACTOR,
            candidate_id=second["candidateId"],
            review_decision_id=losing_decision["reviewDecisionId"],
        )


def test_review_selection_and_snapshot_require_current_explicit_approval() -> None:
    store, backend = _ready_candidate_store()
    candidate = _candidate(store)
    _seal_run(store)
    rejected = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=candidate["candidateId"],
        actor_id=ACTOR,
        action="reject",
        reason="근거 불충분",
        decision_id="decision-reject",
    )
    with pytest.raises(ParserV2ApprovalError, match="approved review decision"):
        store.create_canonical_selection(
            tenant_id=TENANT,
            project_id=PROJECT,
            selection_id="selection-rejected",
            actor_id=ACTOR,
            candidate_id=candidate["candidateId"],
            review_decision_id=rejected["reviewDecisionId"],
        )

    approved = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=candidate["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="원문과 수치 일치",
        decision_id="decision-approved",
    )
    before_retry = backend.upsert_count
    same = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=candidate["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="원문과 수치 일치",
        decision_id="decision-approved",
    )
    assert same["recordId"] == approved["recordId"]
    assert backend.upsert_count == before_retry

    selection = store.create_canonical_selection(
        tenant_id=TENANT,
        project_id=PROJECT,
        selection_id="selection-1",
        actor_id=ACTOR,
        candidate_id=candidate["candidateId"],
        review_decision_id=approved["reviewDecisionId"],
    )
    snapshot = store.create_approved_input_snapshot(
        tenant_id=TENANT,
        project_id=PROJECT,
        snapshot_id="snapshot-1",
        actor_id=SNAPSHOT_APPROVER,
        selection_ids=[selection["selectionId"]],
        purpose="monthly_cash_flow",
    )

    assert snapshot["inputs"] == {
        "feasibility.total_revenue": 100_000_000_000
    }
    assert snapshot["sourceHashes"] == [HASH_A]
    assert snapshot["documentVersionIds"] == ["docv-1"]
    assert snapshot["sourceParsingRunIds"] == ["run-1"]

    ReviewDecision.model_validate(
        {
            key: approved[key]
            for key in (
                "reviewDecisionId",
                "tenantId",
                "projectId",
                "candidateId",
                "conflictIds",
                "action",
                "resultingReviewStatus",
                "resultingApprovalStatus",
                "reviewerId",
                "decidedAt",
                "reason",
            )
        }
    )
    CanonicalSelection.model_validate(snapshot["selections"][0])
    ApprovedInputSnapshot.model_validate(
        approved_input_snapshot_wire_payload(snapshot)
    )


def test_document_change_appends_invalidation_events_and_preserves_originals() -> None:
    store, backend = _ready_candidate_store()
    candidate = _candidate(store)
    _seal_run(store)
    approved = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=candidate["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="원문 대조 완료",
    )
    selection = store.create_canonical_selection(
        tenant_id=TENANT,
        project_id=PROJECT,
        selection_id="selection-1",
        actor_id=ACTOR,
        candidate_id=candidate["candidateId"],
        review_decision_id=approved["reviewDecisionId"],
    )
    snapshot = store.create_approved_input_snapshot(
        tenant_id=TENANT,
        project_id=PROJECT,
        snapshot_id="snapshot-1",
        actor_id=SNAPSHOT_APPROVER,
        selection_ids=[selection["selectionId"]],
        purpose="pf_review",
    )
    _document(
        store,
        version_id="docv-2",
        source_hash=HASH_B,
        predecessor="docv-1",
        version_number=2,
    )

    result = store.invalidate_approvals_for_document_change(
        tenant_id=TENANT,
        project_id=PROJECT,
        superseded_document_version_id="docv-1",
        replacement_document_version_id="docv-2",
        actor_id=ACTOR,
        reason="개정 사업계획서 접수",
    )

    assert result["selectionInvalidationCount"] == 1
    assert result["snapshotInvalidationCount"] == 1
    assert store.list_canonical_selections(TENANT, PROJECT, active_only=True) == []
    assert store.list_approved_input_snapshots(TENANT, PROJECT, active_only=True) == []
    assert store.list_canonical_selections(TENANT, PROJECT)[0][
        "approvalStatus"
    ] == "superseded"
    assert store.list_approved_input_snapshots(TENANT, PROJECT)[0][
        "validityStatus"
    ] == "invalidated"

    raw_selections = backend.list(
        PROJECT, record_type=CANONICAL_SELECTION_RECORD_TYPE, limit=100
    )
    raw_snapshots = backend.list(
        PROJECT, record_type=APPROVED_INPUT_SNAPSHOT_RECORD_TYPE, limit=100
    )
    assert next(item for item in raw_selections if item["recordId"] == selection["recordId"])[
        "approvalStatus"
    ] == "approved"
    assert next(item for item in raw_snapshots if item["recordId"] == snapshot["recordId"])[
        "approvalStatus"
    ] == "approved"
    assert any(item.get("eventKind") == "invalidated" for item in raw_selections)
    assert any(item.get("eventKind") == "invalidated" for item in raw_snapshots)

    with pytest.raises(ParserV2ApprovalError, match="absent or invalidated"):
        store.create_approved_input_snapshot(
            tenant_id=TENANT,
            project_id=PROJECT,
            snapshot_id="snapshot-2",
            actor_id=SNAPSHOT_APPROVER,
            selection_ids=["selection-1"],
            purpose="pf_review",
        )

    with pytest.raises(ParserV2ReferenceError):
        store.get_candidate("another-tenant", PROJECT, candidate["candidateId"])

    assert {
        row["recordType"]
        for row in backend.rows[PROJECT].values()
    }.issuperset(
        {
            PARSING_RUN_RECORD_TYPE,
            DOCUMENT_VERSION_RECORD_TYPE,
            PARSED_FACT_CANDIDATE_RECORD_TYPE,
            REVIEW_DECISION_RECORD_TYPE,
            CANONICAL_SELECTION_RECORD_TYPE,
            APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
        }
    )
    assert all("projectData" not in row for row in backend.rows[PROJECT].values())


def test_conflict_resolution_requires_explicit_referencing_review() -> None:
    store, _ = _ready_candidate_store()
    first = _candidate(store)
    second = _candidate(store, candidate_id="candidate-2", value=99_000_000_000)
    store.create_source_conflict(
        tenant_id=TENANT,
        project_id=PROJECT,
        conflict_id="conflict-1",
        actor_id="reconciler",
        canonical_field="feasibility.total_revenue",
        candidate_ids=[first["candidateId"], second["candidateId"]],
        conflict_code="SOURCE_VALUE_MISMATCH",
    )
    _seal_run(store)
    unrelated = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=first["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="단일 후보만 검토",
    )
    with pytest.raises(ParserV2ReferenceError, match="explicitly reference"):
        store.append_source_conflict_resolution(
            tenant_id=TENANT,
            project_id=PROJECT,
            conflict_id="conflict-1",
            actor_id=ACTOR,
            review_decision_id=unrelated["reviewDecisionId"],
        )

    record_types = {
        PARSING_RUN_RECORD_TYPE,
        DOCUMENT_VERSION_RECORD_TYPE,
        PARSED_FACT_CANDIDATE_RECORD_TYPE,
        SOURCE_CONFLICT_RECORD_TYPE,
        REVIEW_DECISION_RECORD_TYPE,
        CANONICAL_SELECTION_RECORD_TYPE,
        APPROVED_INPUT_SNAPSHOT_RECORD_TYPE,
    }
    assert len(record_types) == 7


def test_candidate_rejects_noncanonical_camel_case_field_key() -> None:
    store, _ = _store()
    _document(store)
    _run(store)

    with pytest.raises(ParserV2RecordError, match="snake_case"):
        store.create_candidate(
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id="run-1",
            document_version_id="docv-1",
            candidate_id="candidate-camel-case",
            actor_id=ACTOR,
            canonical_field="cashFlow.maxMinus",
            value=1,
            evidence=[{"documentVersionId": "docv-1", "pageNumber": 1}],
        )


def test_candidate_does_not_persist_raw_source_excerpt() -> None:
    store, _ = _store()
    _document(store)
    _run(store)

    candidate = store.create_candidate(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        document_version_id="docv-1",
        candidate_id="candidate-redacted",
        actor_id=ACTOR,
        canonical_field="financing.senior.loan_amount",
        value=1,
        evidence=[
            {
                "documentVersionId": "docv-1",
                "pageNumber": 1,
                "rawTextExcerpt": "대출금 계좌 123-456-789",
            }
        ],
    )

    locator = candidate["sourceLocator"]
    assert "rawTextExcerpt" not in locator
    assert locator["rawTextSha256"]
    assert locator["evidenceId"].startswith("parser-evidence:")
    assert locator["metadata"]["rawTextPersisted"] is False


def test_store_rejects_locator_not_attested_by_persisted_run_artifact() -> None:
    store, _ = _store()
    _document(store)
    _run(store)
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="running",
    )
    locator = {
        "evidenceId": "parser-evidence:attested-1",
        "documentVersionId": "docv-1",
        "fileSha256": HASH_A,
        "pageNumber": 1,
        "paragraphIndex": 1,
        "rawTextSha256": "c" * 64,
        "parserMethod": "pdf_native@1.0.0",
    }
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="completed",
        orchestration_artifact={
            "results": {
                "structural_extraction": {
                    "output": {"artifact": {"chunks": [{"sourceLocator": locator}]}}
                }
            }
        },
    )

    candidate = store.create_candidate(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        document_version_id="docv-1",
        candidate_id="candidate-attested",
        actor_id="worker-1",
        canonical_field="financing.senior.loan_amount",
        value=1,
        unit="KRW",
        evidence=[locator],
    )
    assert candidate["evidence"][0]["evidenceId"] == locator["evidenceId"]

    forged = {**locator, "evidenceId": "parser-evidence:forged"}
    with pytest.raises(ParserV2ReferenceError, match="absent from the parsing run"):
        store.create_candidate(
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id="run-1",
            document_version_id="docv-1",
            candidate_id="candidate-forged",
            actor_id="worker-1",
            canonical_field="financing.senior.loan_amount",
            value=2,
            unit="KRW",
            evidence=[forged],
        )


def test_source_locator_rejects_a_supplied_hash_that_does_not_match_excerpt() -> None:
    store, _ = _store()
    _document(store)
    _run(store)

    with pytest.raises(ParserV2RecordError, match="does not match its excerpt"):
        store.create_candidate(
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id="run-1",
            document_version_id="docv-1",
            candidate_id="candidate-bad-excerpt-hash",
            actor_id=ACTOR,
            canonical_field="financing.senior.loan_amount",
            value=1,
            unit="KRW",
            evidence=[
                {
                    "documentVersionId": "docv-1",
                    "pageNumber": 1,
                    "paragraphIndex": 1,
                    "rawTextExcerpt": "금 일억원",
                    "rawTextSha256": "0" * 64,
                }
            ],
        )


def test_unregistered_field_remains_candidate_but_cannot_be_selected() -> None:
    store, _ = _ready_candidate_store()
    candidate = store.create_candidate(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        document_version_id="docv-1",
        candidate_id="candidate-unknown-field",
        actor_id="parser-worker-1",
        canonical_field="custom.unapproved_metric",
        value=1,
        evidence=[
            {
                "documentVersionId": "docv-1",
                "pageNumber": 1,
                "paragraphIndex": 1,
                "rawTextSha256": "a" * 64,
            }
        ],
    )
    _seal_run(store)
    decision = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=candidate["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Candidate may be reviewed, but field governance is still missing.",
    )

    assert candidate["validationStatus"] == "blocked"
    assert "CANONICAL_FIELD_UNREGISTERED" in candidate["validationCodes"]
    with pytest.raises(ParserV2ApprovalError, match="blocking validation"):
        store.create_canonical_selection(
            tenant_id=TENANT,
            project_id=PROJECT,
            selection_id="selection-unknown-field",
            actor_id=ACTOR,
            candidate_id=candidate["candidateId"],
            review_decision_id=decision["reviewDecisionId"],
        )


def test_candidate_payload_size_is_bounded_before_persistence() -> None:
    store, _ = _ready_candidate_store()
    with pytest.raises(ParserV2RecordError, match="storage limit"):
        store.create_candidate(
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id="run-1",
            document_version_id="docv-1",
            candidate_id="candidate-too-large",
            actor_id=ACTOR,
            canonical_field="project.name",
            value="x" * (300 * 1024),
            evidence=[
                {
                    "documentVersionId": "docv-1",
                    "pageNumber": 1,
                    "paragraphIndex": 1,
                    "rawTextSha256": "a" * 64,
                }
            ],
        )


def test_derived_candidate_binds_exact_calculation_output_and_snapshot_closure() -> None:
    backend = InMemoryDealStateStub()
    calculation_runs: list[dict] = []
    store = ParserV2RecordStore(
        upsert_fn=backend.upsert,
        list_fn=backend.list,
        list_calculation_runs_fn=lambda _project_id, **_kwargs: copy.deepcopy(
            calculation_runs
        ),
        clock=TickClock(),
    )
    _document(store)
    _run(store)
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="running",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        actor_id="worker-1",
        execution_status="completed",
    )
    source = _candidate(store, candidate_id="candidate-source")
    input_snapshot = {"inputFactIds": [source["candidateId"]], "scenarioId": "base"}
    result_snapshot = {"summary": {"totalProjectCost": 50_000_000_000}}
    output_value = result_snapshot["summary"]["totalProjectCost"]
    calculation_runs.append(
        {
            "tenantId": TENANT,
            "projectId": PROJECT,
            "calculationRunId": "calc-cost-1",
            "engineKey": "total_project_cost",
            "engineVersion": "total_project_cost-v2",
            "status": "completed",
            "inputFactIds": [source["candidateId"]],
            "inputSnapshot": input_snapshot,
            "resultSnapshot": result_snapshot,
        }
    )
    binding = {
        "origin_type": "derived",
        "formula_id": "total_project_cost",
        "formula_version": "total_project_cost-v2",
        "input_fact_ids": [source["candidateId"]],
        "calculation_run_id": "calc-cost-1",
        "calculation_engine_version": "total_project_cost-v2",
        "calculation_output_path": "/summary/totalProjectCost",
        "calculation_output_hash": _json_hash(output_value),
        "input_snapshot_hash": _json_hash(input_snapshot),
    }
    evidence = [
        {
            "documentVersionId": "docv-1",
            "pageNumber": 1,
            "paragraphIndex": 1,
            "rawTextSha256": "9" * 64,
        }
    ]
    derived = store.create_candidate(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-1",
        document_version_id="docv-1",
        candidate_id="candidate-derived-cost",
        actor_id="calculation-worker-1",
        canonical_field="cost.total_project_cost",
        value=output_value,
        unit="KRW",
        evidence=evidence,
        **binding,
    )
    assert derived["calculationOutputHash"] == _json_hash(output_value)
    assert derived["inputSnapshotHash"] == _json_hash(input_snapshot)
    assert any(
        item.get("artifactKey") == "calc-cost-1"
        and item.get("jsonPath") == "/summary/totalProjectCost"
        and item.get("metadata", {}).get("outputHash") == _json_hash(output_value)
        for item in derived["evidence"]
    )
    assert derived["sourceLocator"]["artifactKey"] == "calc-cost-1"
    assert derived["sourceLocator"]["documentVersionId"] == "docv-1"

    with pytest.raises(ParserV2ApprovalError, match="normalizedValue does not match"):
        store.create_candidate(
            tenant_id=TENANT,
            project_id=PROJECT,
            run_id="run-1",
            document_version_id="docv-1",
            candidate_id="candidate-tampered-cost",
            actor_id="calculation-worker-1",
            canonical_field="cost.total_project_cost",
            value=output_value + 1,
            unit="KRW",
            evidence=evidence,
            **binding,
        )

    _seal_run(store)
    source_decision = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=source["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Source evidence reviewed.",
    )
    derived_decision = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=derived["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Calculation output and lineage reviewed.",
    )
    source_selection = store.create_canonical_selection(
        tenant_id=TENANT,
        project_id=PROJECT,
        selection_id="selection-source",
        actor_id=ACTOR,
        candidate_id=source["candidateId"],
        review_decision_id=source_decision["reviewDecisionId"],
    )
    derived_selection = store.create_canonical_selection(
        tenant_id=TENANT,
        project_id=PROJECT,
        selection_id="selection-derived",
        actor_id=ACTOR,
        candidate_id=derived["candidateId"],
        review_decision_id=derived_decision["reviewDecisionId"],
    )
    assert derived_selection["sourceLocator"]["artifactKey"] == "calc-cost-1"

    with pytest.raises(ParserV2ApprovalError, match="every derived input"):
        store.create_approved_input_snapshot(
            tenant_id=TENANT,
            project_id=PROJECT,
            snapshot_id="snapshot-derived-only",
            actor_id=SNAPSHOT_APPROVER,
            selection_ids=[derived_selection["selectionId"]],
            purpose="pf_review",
        )

    snapshot = store.create_approved_input_snapshot(
        tenant_id=TENANT,
        project_id=PROJECT,
        snapshot_id="snapshot-derived-complete",
        actor_id=SNAPSHOT_APPROVER,
        selection_ids=[source_selection["selectionId"], derived_selection["selectionId"]],
        purpose="pf_review",
    )
    assert snapshot["selectionIds"] == ["selection-source", "selection-derived"]
    derived_input = next(
        item
        for item in snapshot["inputItems"]
        if item["selectionId"] == derived_selection["selectionId"]
    )
    assert derived_input["sourceLocator"]["artifactKey"] == "calc-cost-1"
    assert derived_input["sourceLocator"]["jsonPath"] == "/summary/totalProjectCost"
    assert derived_input["sourceLocator"]["metadata"]["outputHash"] == _json_hash(
        output_value
    )
    ApprovedInputSnapshot.model_validate(
        approved_input_snapshot_wire_payload(snapshot)
    )


def test_same_review_content_can_be_appended_again_at_a_new_sequence() -> None:
    store, _ = _ready_candidate_store()
    candidate = _candidate(store, candidate_id="candidate-review-sequence")
    _seal_run(store)
    first = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=candidate["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Evidence matched.",
        expected_decision_sequence=0,
    )
    store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=candidate["candidateId"],
        actor_id=ACTOR,
        action="reject",
        reason="A newer conflict was discovered.",
        expected_decision_sequence=1,
    )
    third = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=candidate["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Evidence matched.",
        expected_decision_sequence=2,
    )

    assert first["reviewDecisionId"] != third["reviewDecisionId"]
    assert third["decisionSequence"] == 3


def test_new_conflict_invalidates_active_approvals_and_stales_the_original_seal() -> None:
    store, _ = _ready_candidate_store()
    winner = _candidate(store, candidate_id="candidate-conflict-winner", value=100)
    _seal_run(store)
    first_decision = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=winner["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Initial approval.",
    )
    first_selection = store.create_canonical_selection(
        tenant_id=TENANT,
        project_id=PROJECT,
        selection_id="selection-before-conflict",
        actor_id=ACTOR,
        candidate_id=winner["candidateId"],
        review_decision_id=first_decision["reviewDecisionId"],
    )
    first_snapshot = store.create_approved_input_snapshot(
        tenant_id=TENANT,
        project_id=PROJECT,
        snapshot_id="snapshot-before-conflict",
        actor_id=SNAPSHOT_APPROVER,
        selection_ids=[first_selection["selectionId"]],
        purpose="pf_review",
    )
    _run(
        store,
        run_id="run-2",
        document_version_id="docv-1",
        parser_version="parser-2.0.1",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-2",
        actor_id="worker-2",
        execution_status="running",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-2",
        actor_id="worker-2",
        execution_status="completed",
    )
    loser = _candidate(
        store,
        candidate_id="candidate-conflict-loser",
        value=90,
        run_id="run-2",
    )

    store.create_source_conflict(
        tenant_id=TENANT,
        project_id=PROJECT,
        conflict_id="conflict-after-approval",
        actor_id=ACTOR,
        canonical_field="feasibility.total_revenue",
        candidate_ids=[winner["candidateId"], loser["candidateId"]],
        conflict_code="SOURCE_VALUE_MISMATCH",
    )

    assert store.list_canonical_selections(TENANT, PROJECT, active_only=True) == []
    assert store.list_approved_input_snapshots(TENANT, PROJECT, active_only=True) == []

    with pytest.raises(ParserV2ApprovalError, match="changed after sealing"):
        store.append_review_decision(
            tenant_id=TENANT,
            project_id=PROJECT,
            candidate_id=winner["candidateId"],
            actor_id=ACTOR,
            action="approve",
            reason="A new reconciliation run is required.",
            conflict_ids=["conflict-after-approval"],
            expected_decision_sequence=1,
        )

    assert first_snapshot["snapshotId"] == "snapshot-before-conflict"


def test_selection_and_snapshot_supersession_leave_only_new_versions_active() -> None:
    store, _ = _ready_candidate_store()
    first = _candidate(store, candidate_id="candidate-version-1")
    _seal_run(store)
    first_decision = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=first["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Initial approved source.",
    )
    first_selection = store.create_canonical_selection(
        tenant_id=TENANT,
        project_id=PROJECT,
        selection_id="selection-version-1",
        actor_id=ACTOR,
        candidate_id=first["candidateId"],
        review_decision_id=first_decision["reviewDecisionId"],
    )
    first_snapshot = store.create_approved_input_snapshot(
        tenant_id=TENANT,
        project_id=PROJECT,
        snapshot_id="snapshot-version-1",
        actor_id=SNAPSHOT_APPROVER,
        selection_ids=[first_selection["selectionId"]],
        purpose="pf_review",
    )

    _run(
        store,
        run_id="run-2",
        document_version_id="docv-1",
        parser_version="parser-2.0.1",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-2",
        actor_id="worker-2",
        execution_status="running",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id="run-2",
        actor_id="worker-2",
        execution_status="completed",
    )
    second = _candidate(
        store,
        candidate_id="candidate-version-2",
        value=101_000_000_000,
        run_id="run-2",
    )
    _seal_run(store, "run-2")
    second_decision = store.append_review_decision(
        tenant_id=TENANT,
        project_id=PROJECT,
        candidate_id=second["candidateId"],
        actor_id=ACTOR,
        action="approve",
        reason="Approved replacement source.",
    )
    second_selection = store.create_canonical_selection(
        tenant_id=TENANT,
        project_id=PROJECT,
        selection_id="selection-version-2",
        actor_id=ACTOR,
        candidate_id=second["candidateId"],
        review_decision_id=second_decision["reviewDecisionId"],
        selection_version=2,
        supersedes_selection_id=first_selection["selectionId"],
    )
    second_snapshot = store.create_approved_input_snapshot(
        tenant_id=TENANT,
        project_id=PROJECT,
        snapshot_id="snapshot-version-2",
        actor_id=SNAPSHOT_APPROVER,
        selection_ids=[second_selection["selectionId"]],
        purpose="pf_review",
        supersedes_snapshot_id=first_snapshot["snapshotId"],
    )

    assert [
        item["selectionId"]
        for item in store.list_canonical_selections(
            TENANT, PROJECT, active_only=True
        )
    ] == ["selection-version-2"]
    assert [
        item["snapshotId"]
        for item in store.list_approved_input_snapshots(
            TENANT, PROJECT, active_only=True
        )
    ] == ["snapshot-version-2"]
    assert second_snapshot["snapshotVersion"] == 2
