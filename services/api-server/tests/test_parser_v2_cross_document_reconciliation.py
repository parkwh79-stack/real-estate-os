from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from parsing_platform.domain_standards import DOMAIN_STANDARD_VERSION
from use_cases.parser_v2_cross_document_reconciliation import (
    RECONCILIATION_REPORT_SCHEMA_VERSION,
    ParserV2CrossDocumentReconciliationError,
    ParserV2CrossDocumentReconciliationPreconditionError,
    reconcile_parser_v2_project_sources,
)
from use_cases.parser_v2_records import ParserV2RecordStore


TENANT_ID = "tenant-reconciliation"
PROJECT_ID = "project-reconciliation"


class _TickClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


class _Backend:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict]] = {}
        self.upsert_count = 0

    def upsert(self, project_id: str, record: dict) -> dict:
        self.upsert_count += 1
        saved = copy.deepcopy(record)
        saved.setdefault("projectId", project_id)
        self.rows.setdefault(project_id, {})[saved["recordId"]] = saved
        return copy.deepcopy(saved)

    def list(self, project_id: str, **filters) -> list[dict]:
        records = list(self.rows.get(project_id, {}).values())
        record_type = filters.get("record_type")
        if record_type:
            records = [
                item for item in records if item.get("recordType") == record_type
            ]
        return copy.deepcopy(records[: int(filters.get("limit") or 200)])


def _store() -> tuple[ParserV2RecordStore, _Backend]:
    backend = _Backend()
    return (
        ParserV2RecordStore(
            upsert_fn=backend.upsert,
            list_fn=backend.list,
            clock=_TickClock(),
        ),
        backend,
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _document(
    store: ParserV2RecordStore,
    *,
    document_id: str,
    version_id: str,
    version_number: int = 1,
    predecessor_version_id: str | None = None,
    source_authority: str = "submitted_document",
) -> dict:
    return store.create_document_version(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        document_id=document_id,
        document_version_id=version_id,
        actor_id="ingestion-worker",
        archive_id=f"archive:{version_id}",
        object_version=f"object:{version_id}",
        source_hash=_sha(version_id),
        media_type="application/pdf",
        original_file_name=f"{version_id}.pdf",
        size_bytes=100,
        version_number=version_number,
        source_authority=source_authority,
        predecessor_version_id=predecessor_version_id,
    )


def _run(
    store: ParserV2RecordStore,
    *,
    run_id: str,
    document_version_id: str,
    complete: bool = True,
) -> dict:
    run = store.create_parsing_run(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id=run_id,
        document_version_id=document_version_id,
        actor_id="parser-worker",
        parser_version="parser-v2-test",
        ontology_version=DOMAIN_STANDARD_VERSION,
        prompt_version="prompt-v2-test",
        model_version="model-v2-test",
        profile_id="feasibility.test",
        profile_version="1.0.0",
        plan_hash=_sha(f"plan:{run_id}"),
        input_hash=_sha(f"input:{run_id}"),
        requested_modules=["structural_extraction", "cost_parser"],
    )
    if complete:
        store.append_parsing_run_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            run_id=run_id,
            actor_id="parser-worker",
            execution_status="running",
        )
        run = store.append_parsing_run_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            run_id=run_id,
            actor_id="parser-worker",
            execution_status="completed",
        )
    return run


def _basis(*, amount_scope: str = "gross") -> dict:
    return {
        "vatTreatment": "included",
        "amountScope": amount_scope,
        "allocationBasis": "direct",
        "currency": "KRW",
    }


def _candidate(
    store: ParserV2RecordStore,
    *,
    candidate_id: str,
    run_id: str,
    document_version_id: str,
    value: int,
    field_key: str = "revenue.total_sales_revenue",
    unit: str = "krw",
    basis: dict | None = None,
    authority: str = "submitted_document",
    effective_date: str = "2026-06-30",
    as_of_date: str = "2026-07-01",
    supersedes_candidate_id: str | None = None,
) -> dict:
    return store.create_candidate(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id=run_id,
        document_version_id=document_version_id,
        candidate_id=candidate_id,
        actor_id="parser-worker",
        canonical_field=field_key,
        value=value,
        raw_value=f"{value:,}",
        unit=unit,
        confidence=0.95,
        authority=authority,
        evidence=[
            {
                "documentVersionId": document_version_id,
                "fileSha256": _sha(document_version_id),
                "pageNumber": 1,
                "tableId": "revenue-summary",
                "rowIndex": 1,
                "columnIndex": 2,
                "rawTextSha256": _sha(f"evidence:{candidate_id}"),
                "parserMethod": "native_pdf_table",
            }
        ],
        effective_date=effective_date,
        as_of_date=as_of_date,
        supersedes_candidate_id=supersedes_candidate_id,
        metadata={"basis": copy.deepcopy(basis or _basis())},
    )


def _seal_run(
    store: ParserV2RecordStore,
    *,
    run_id: str,
    candidate_ids: list[str],
    conflict_ids: list[str] | None = None,
) -> dict:
    return store.seal_candidate_set(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id=run_id,
        actor_id="parser-worker",
        expected_candidate_ids=candidate_ids,
        expected_conflict_ids=conflict_ids or [],
    )


def test_cross_document_conflict_is_deterministic_and_idempotent() -> None:
    store, backend = _store()
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v1",
        source_authority="signed_report",
    )
    _document(
        store,
        document_id="document-b",
        version_id="document-b:v1",
        source_authority="draft_document",
    )
    _run(store, run_id="run-a", document_version_id="document-a:v1")
    _run(store, run_id="run-b", document_version_id="document-b:v1")
    _candidate(
        store,
        candidate_id="candidate-a",
        run_id="run-a",
        document_version_id="document-a:v1",
        value=100,
        authority="signed_report",
    )
    _candidate(
        store,
        candidate_id="candidate-b",
        run_id="run-b",
        document_version_id="document-b:v1",
        value=120,
        authority="draft_document",
    )

    first = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )
    writes_after_first = backend.upsert_count
    second = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="another-reconciliation-worker",
    )

    assert first == second
    assert first["schemaVersion"] == RECONCILIATION_REPORT_SCHEMA_VERSION
    assert backend.upsert_count == writes_after_first
    assert len(first["conflictIds"]) == 1
    assert len(store.list_source_conflicts(TENANT_ID, PROJECT_ID)) == 1
    assert store.list_canonical_selections(TENANT_ID, PROJECT_ID) == []
    group = first["groups"][0]
    assert group["status"] == "conflict"
    assert group["crossDocument"] is True
    assert group["crossRun"] is True
    assert group["conflictStatus"] == "open"
    authorities = {
        context["authority"]
        for variant in group["semanticVariants"]
        for context in variant["candidateContexts"]
    }
    assert authorities == {"signed_report", "draft_document"}
    assert first["automaticSelectionPerformed"] is False
    assert first["authorityRankingPerformed"] is False


def test_new_conflicts_touching_sealed_runs_fail_before_any_write() -> None:
    store, backend = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _document(store, document_id="document-b", version_id="document-b:v1")
    _run(store, run_id="run-a", document_version_id="document-a:v1")
    _run(store, run_id="run-b", document_version_id="document-b:v1")
    for field_key, suffix, first_value, second_value in (
        ("revenue.total_sales_revenue", "revenue", 100, 120),
        ("cost.total_project_cost", "cost", 70, 80),
    ):
        _candidate(
            store,
            candidate_id=f"candidate-a-{suffix}",
            run_id="run-a",
            document_version_id="document-a:v1",
            field_key=field_key,
            value=first_value,
        )
        _candidate(
            store,
            candidate_id=f"candidate-b-{suffix}",
            run_id="run-b",
            document_version_id="document-b:v1",
            field_key=field_key,
            value=second_value,
        )
    _seal_run(
        store,
        run_id="run-a",
        candidate_ids=["candidate-a-cost", "candidate-a-revenue"],
    )
    _seal_run(
        store,
        run_id="run-b",
        candidate_ids=["candidate-b-cost", "candidate-b-revenue"],
    )
    seals_before = store.list_candidate_set_seals(TENANT_ID, PROJECT_ID)
    writes_before = backend.upsert_count

    with pytest.raises(
        ParserV2CrossDocumentReconciliationPreconditionError
    ) as caught:
        reconcile_parser_v2_project_sources(
            record_store=store,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_id="reconciliation-worker",
        )

    assert caught.value.code == (
        "PARSER_V2_CROSS_DOCUMENT_RECONCILIATION_REQUIRES_PRE_SEAL"
    )
    assert caught.value.sealed_run_ids == ("run-a", "run-b")
    assert len(caught.value.conflict_ids) == 2
    assert backend.upsert_count == writes_before
    assert store.list_source_conflicts(TENANT_ID, PROJECT_ID) == []
    assert store.list_candidate_set_seals(TENANT_ID, PROJECT_ID) == seals_before

    # The rejected reconciliation did not stale the seal or deadlock review.
    review = store.append_review_decision(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        candidate_id="candidate-a-revenue",
        actor_id="independent-reviewer",
        action="approve",
        reason="sealed package remained unchanged",
        expected_decision_sequence=0,
    )
    assert review["approvalStatus"] == "approved"


def test_existing_identical_conflict_remains_idempotent_after_sealing() -> None:
    store, backend = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _document(store, document_id="document-b", version_id="document-b:v1")
    _run(store, run_id="run-a", document_version_id="document-a:v1")
    _run(store, run_id="run-b", document_version_id="document-b:v1")
    _candidate(
        store,
        candidate_id="candidate-a",
        run_id="run-a",
        document_version_id="document-a:v1",
        value=100,
    )
    _candidate(
        store,
        candidate_id="candidate-b",
        run_id="run-b",
        document_version_id="document-b:v1",
        value=120,
    )
    first = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )
    conflict_id = first["conflictIds"][0]
    _seal_run(
        store,
        run_id="run-a",
        candidate_ids=["candidate-a"],
        conflict_ids=[conflict_id],
    )
    _seal_run(
        store,
        run_id="run-b",
        candidate_ids=["candidate-b"],
        conflict_ids=[conflict_id],
    )
    writes_before = backend.upsert_count

    second = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="another-reconciliation-worker",
    )

    assert second == first
    assert backend.upsert_count == writes_before
    assert second["groups"][0]["conflictStatus"] == "open"


def test_semantic_duplicates_ignore_authority_and_temporal_context_but_report_them() -> None:
    store, _ = _store()
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v1",
        source_authority="signed_report",
    )
    _document(
        store,
        document_id="document-b",
        version_id="document-b:v1",
        source_authority="draft_document",
    )
    _run(store, run_id="run-a", document_version_id="document-a:v1")
    _run(store, run_id="run-b", document_version_id="document-b:v1")
    _candidate(
        store,
        candidate_id="candidate-a",
        run_id="run-a",
        document_version_id="document-a:v1",
        value=100,
        unit="KRW",
        authority="signed_report",
        effective_date="2026-01-01",
        as_of_date="2026-01-31",
    )
    _candidate(
        store,
        candidate_id="candidate-b",
        run_id="run-b",
        document_version_id="document-b:v1",
        value=100,
        unit="krw",
        authority="draft_document",
        effective_date="2026-02-01",
        as_of_date="2026-02-28",
    )

    report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert report["conflictIds"] == []
    group = report["groups"][0]
    assert group["status"] == "semantic_duplicate"
    assert len(group["semanticVariants"]) == 1
    assert group["semanticVariants"][0]["normalizedUnit"] == "krw"
    contexts = group["semanticVariants"][0]["candidateContexts"]
    assert [item["effectiveDate"] for item in contexts] == [
        "2026-01-01",
        "2026-02-01",
    ]
    assert [item["asOfDate"] for item in contexts] == [
        "2026-01-31",
        "2026-02-28",
    ]
    assert [item["authority"] for item in contexts] == [
        "signed_report",
        "draft_document",
    ]


def test_same_value_with_different_explicit_basis_is_a_conflict() -> None:
    store, _ = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _document(store, document_id="document-b", version_id="document-b:v1")
    _run(store, run_id="run-a", document_version_id="document-a:v1")
    _run(store, run_id="run-b", document_version_id="document-b:v1")
    _candidate(
        store,
        candidate_id="candidate-gross",
        run_id="run-a",
        document_version_id="document-a:v1",
        value=100,
        basis=_basis(amount_scope="gross"),
    )
    _candidate(
        store,
        candidate_id="candidate-net",
        run_id="run-b",
        document_version_id="document-b:v1",
        value=100,
        basis=_basis(amount_scope="net"),
    )

    report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    group = report["groups"][0]
    assert group["status"] == "conflict"
    assert {
        variant["basis"]["amountScope"]
        for variant in group["semanticVariants"]
    } == {"gross", "net"}


def test_declared_candidate_and_document_supersession_remove_stale_facts() -> None:
    store, _ = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _run(store, run_id="run-a-v1", document_version_id="document-a:v1")
    _candidate(
        store,
        candidate_id="candidate-stale-document",
        run_id="run-a-v1",
        document_version_id="document-a:v1",
        value=999,
    )
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v2",
        version_number=2,
        predecessor_version_id="document-a:v1",
    )
    _document(store, document_id="document-b", version_id="document-b:v1")
    _run(store, run_id="run-a-v2", document_version_id="document-a:v2")
    _run(store, run_id="run-b", document_version_id="document-b:v1")
    _candidate(
        store,
        candidate_id="candidate-replaced",
        run_id="run-a-v2",
        document_version_id="document-a:v2",
        value=777,
    )
    _candidate(
        store,
        candidate_id="candidate-current",
        run_id="run-a-v2",
        document_version_id="document-a:v2",
        value=200,
        supersedes_candidate_id="candidate-replaced",
    )
    _candidate(
        store,
        candidate_id="candidate-other-document",
        run_id="run-b",
        document_version_id="document-b:v1",
        value=200,
    )

    report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert report["conflictIds"] == []
    assert report["activeCandidateIds"] == [
        "candidate-current",
        "candidate-other-document",
    ]
    ignored = {
        item["candidateId"]: item["reasons"]
        for item in report["ignoredCandidates"]
    }
    assert ignored["candidate-stale-document"] == [
        "document_version_superseded"
    ]
    assert ignored["candidate-replaced"] == ["candidate_superseded"]
    assert report["supersededDocumentVersionIds"] == ["document-a:v1"]
    assert report["groups"][0]["status"] == "semantic_duplicate"


def test_empty_successful_document_replacement_does_not_hide_predecessor() -> None:
    store, _ = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _run(store, run_id="run-a-v1", document_version_id="document-a:v1")
    _candidate(
        store,
        candidate_id="candidate-a-v1",
        run_id="run-a-v1",
        document_version_id="document-a:v1",
        value=100,
    )
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v2",
        version_number=2,
        predecessor_version_id="document-a:v1",
    )
    _run(store, run_id="run-a-v2", document_version_id="document-a:v2")

    report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert report["activeCandidateIds"] == ["candidate-a-v1"]
    assert report["declaredSupersededDocumentVersionIds"] == [
        "document-a:v1"
    ]
    assert report["effectiveSupersededDocumentVersionIds"] == []
    assert report["supersededDocumentVersionIds"] == []
    assert report["activeDocumentVersionIds"] == [
        "document-a:v1",
        "document-a:v2",
    ]


def test_failed_document_replacement_does_not_hide_predecessor() -> None:
    store, _ = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _run(store, run_id="run-a-v1", document_version_id="document-a:v1")
    _candidate(
        store,
        candidate_id="candidate-a-v1",
        run_id="run-a-v1",
        document_version_id="document-a:v1",
        value=100,
    )
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v2",
        version_number=2,
        predecessor_version_id="document-a:v1",
    )
    _run(
        store,
        run_id="run-a-v2",
        document_version_id="document-a:v2",
        complete=False,
    )
    _candidate(
        store,
        candidate_id="candidate-a-v2",
        run_id="run-a-v2",
        document_version_id="document-a:v2",
        value=999,
    )
    store.append_parsing_run_event(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id="run-a-v2",
        actor_id="parser-worker",
        execution_status="running",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id="run-a-v2",
        actor_id="parser-worker",
        execution_status="failed",
        error_codes=["REPLACEMENT_PARSE_FAILED"],
    )

    report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert report["activeCandidateIds"] == ["candidate-a-v1"]
    assert report["effectiveSupersededDocumentVersionIds"] == []
    assert report["supersededDocumentVersionIds"] == []
    ignored = {
        item["candidateId"]: item["reasons"]
        for item in report["ignoredCandidates"]
    }
    assert ignored["candidate-a-v2"] == ["parsing_run_unsuccessful"]


def test_validation_blocked_document_replacement_does_not_hide_predecessor() -> None:
    store, _ = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _run(store, run_id="run-a-v1", document_version_id="document-a:v1")
    _candidate(
        store,
        candidate_id="candidate-a-v1",
        run_id="run-a-v1",
        document_version_id="document-a:v1",
        value=100,
    )
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v2",
        version_number=2,
        predecessor_version_id="document-a:v1",
    )
    _run(store, run_id="run-a-v2", document_version_id="document-a:v2")
    blocked = _candidate(
        store,
        candidate_id="candidate-a-v2",
        run_id="run-a-v2",
        document_version_id="document-a:v2",
        value=999,
        unit="sqm",
    )
    assert blocked["validationStatus"] == "blocked"

    report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert report["activeCandidateIds"] == ["candidate-a-v1"]
    assert report["effectiveSupersededDocumentVersionIds"] == []
    assert report["supersededDocumentVersionIds"] == []
    ignored = {
        item["candidateId"]: item["reasons"]
        for item in report["ignoredCandidates"]
    }
    assert ignored["candidate-a-v2"] == [
        "candidate_validation_not_passed"
    ]


def test_eligible_replacement_supersedes_complete_document_ancestor_chain() -> None:
    store, _ = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _run(store, run_id="run-a-v1", document_version_id="document-a:v1")
    _candidate(
        store,
        candidate_id="candidate-a-v1",
        run_id="run-a-v1",
        document_version_id="document-a:v1",
        value=100,
    )
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v2",
        version_number=2,
        predecessor_version_id="document-a:v1",
    )
    # v2 intentionally has no usable parse. A successful v3 still replaces
    # the entire immutable lineage, not only its direct v2 predecessor.
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v3",
        version_number=3,
        predecessor_version_id="document-a:v2",
    )
    _run(store, run_id="run-a-v3", document_version_id="document-a:v3")
    _candidate(
        store,
        candidate_id="candidate-a-v3",
        run_id="run-a-v3",
        document_version_id="document-a:v3",
        value=300,
    )

    report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert report["activeCandidateIds"] == ["candidate-a-v3"]
    assert report["declaredSupersededDocumentVersionIds"] == [
        "document-a:v1",
        "document-a:v2",
    ]
    assert report["effectiveSupersededDocumentVersionIds"] == [
        "document-a:v1",
        "document-a:v2",
    ]
    assert report["supersededDocumentVersionIds"] == [
        "document-a:v1",
        "document-a:v2",
    ]
    assert report["activeDocumentVersionIds"] == ["document-a:v3"]
    assert report["ignoredCandidates"] == [
        {
            "candidateId": "candidate-a-v1",
            "documentVersionId": "document-a:v1",
            "parsingRunId": "run-a-v1",
            "reasons": ["document_version_superseded"],
        }
    ]


def test_document_supersession_cycle_fails_closed() -> None:
    store, backend = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v2",
        version_number=2,
        predecessor_version_id="document-a:v1",
    )
    # Simulate corrupt persisted lineage. The use case must not depend on
    # traversal order or silently make either version authoritative.
    v1_record = next(
        item
        for item in backend.rows[PROJECT_ID].values()
        if item.get("documentVersionId") == "document-a:v1"
    )
    v1_record["supersedesDocumentVersionId"] = "document-a:v2"

    with pytest.raises(
        ParserV2CrossDocumentReconciliationError,
        match="supersession lineage contains a cycle",
    ):
        reconcile_parser_v2_project_sources(
            record_store=store,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_id="reconciliation-worker",
        )


def test_document_supersession_version_gap_fails_closed() -> None:
    store, backend = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _document(
        store,
        document_id="document-a",
        version_id="document-a:v2",
        version_number=2,
        predecessor_version_id="document-a:v1",
    )
    v2_record = next(
        item
        for item in backend.rows[PROJECT_ID].values()
        if item.get("documentVersionId") == "document-a:v2"
    )
    v2_record["versionNumber"] = 3

    with pytest.raises(
        ParserV2CrossDocumentReconciliationError,
        match="version numbers must increase by one",
    ):
        reconcile_parser_v2_project_sources(
            record_store=store,
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_id="reconciliation-worker",
        )


def test_incomplete_or_failed_replacement_does_not_hide_completed_predecessor() -> None:
    store, _ = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _document(store, document_id="document-b", version_id="document-b:v1")
    _run(store, run_id="run-good", document_version_id="document-a:v1")
    _run(
        store,
        run_id="run-replacement",
        document_version_id="document-a:v1",
        complete=False,
    )
    _run(store, run_id="run-other", document_version_id="document-b:v1")
    _candidate(
        store,
        candidate_id="candidate-good",
        run_id="run-good",
        document_version_id="document-a:v1",
        value=100,
    )
    _candidate(
        store,
        candidate_id="candidate-incomplete-replacement",
        run_id="run-replacement",
        document_version_id="document-a:v1",
        value=999,
        supersedes_candidate_id="candidate-good",
    )
    _candidate(
        store,
        candidate_id="candidate-other",
        run_id="run-other",
        document_version_id="document-b:v1",
        value=100,
    )

    pending_report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert pending_report["activeCandidateIds"] == [
        "candidate-good",
        "candidate-other",
    ]
    assert pending_report["declaredSupersededCandidateIds"] == [
        "candidate-good"
    ]
    assert pending_report["effectiveSupersededCandidateIds"] == []
    assert pending_report["conflictIds"] == []
    assert pending_report["groups"][0]["status"] == "semantic_duplicate"
    assert pending_report["ignoredCandidates"][0]["reasons"] == [
        "parsing_run_not_completed"
    ]

    store.append_parsing_run_event(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id="run-replacement",
        actor_id="parser-worker",
        execution_status="running",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id="run-replacement",
        actor_id="parser-worker",
        execution_status="failed",
        error_codes=["RETRY_FAILED"],
    )
    failed_report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert failed_report["activeCandidateIds"] == [
        "candidate-good",
        "candidate-other",
    ]
    assert failed_report["effectiveSupersededCandidateIds"] == []
    assert failed_report["ignoredCandidates"][0]["reasons"] == [
        "parsing_run_unsuccessful"
    ]


def test_failed_run_is_excluded_and_same_document_reparse_conflict_is_detected() -> None:
    store, _ = _store()
    _document(store, document_id="document-a", version_id="document-a:v1")
    _run(
        store,
        run_id="run-failed",
        document_version_id="document-a:v1",
        complete=False,
    )
    _candidate(
        store,
        candidate_id="candidate-failed",
        run_id="run-failed",
        document_version_id="document-a:v1",
        value=999,
    )
    store.append_parsing_run_event(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id="run-failed",
        actor_id="parser-worker",
        execution_status="running",
    )
    store.append_parsing_run_event(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        run_id="run-failed",
        actor_id="parser-worker",
        execution_status="failed",
        error_codes=["PARSER_FAILURE"],
    )
    _run(store, run_id="run-a", document_version_id="document-a:v1")
    _run(store, run_id="run-b", document_version_id="document-a:v1")
    _candidate(
        store,
        candidate_id="candidate-a",
        run_id="run-a",
        document_version_id="document-a:v1",
        value=100,
    )
    _candidate(
        store,
        candidate_id="candidate-b",
        run_id="run-b",
        document_version_id="document-a:v1",
        value=120,
    )

    report = reconcile_parser_v2_project_sources(
        record_store=store,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="reconciliation-worker",
    )

    assert report["ignoredCandidates"] == [
        {
            "candidateId": "candidate-failed",
            "documentVersionId": "document-a:v1",
            "parsingRunId": "run-failed",
            "reasons": ["parsing_run_unsuccessful"],
        }
    ]
    group = report["groups"][0]
    assert group["status"] == "conflict"
    assert group["crossDocument"] is False
    assert group["crossRun"] is True
    conflict = store.get_source_conflict(
        TENANT_ID, PROJECT_ID, group["conflictId"]
    )
    assert conflict["conflictCode"] == "PARSER_V2_CROSS_RUN_FACT_CONFLICT"
