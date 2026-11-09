from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from db.repositories.legal_citator_repository import (
    PostgresLegalCitatorRepository,
)
from legal_intelligence.citator_models import (
    CitatorRecordKind,
    CitatorReviewInput,
    CitatorReviewOutcome,
)
from routers import legal_citator
from use_cases.legal_citator_review import LegalCitatorReviewService


NOW = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
EXCERPT = "건축법 제11조에 따른 허가를 받아야 한다."
EXCERPT_SHA = hashlib.sha256(EXCERPT.encode("utf-8")).hexdigest()
PROVISION_BODY = f"공식 조문 시작. {EXCERPT} 공식 조문 끝."
PROVISION_SHA = hashlib.sha256(
    PROVISION_BODY.encode("utf-8")
).hexdigest()
SNAPSHOT_ID = "snapshot-current"
SNAPSHOT_SHA = "1" * 64
EVIDENCE_ITEM_ID = "evidence-item-current"
OBJECT_EVIDENCE_ITEM_ID = "evidence-item-object-current"
SOURCE_FETCH_ID = "fetch-official-current"
OBJECT_SOURCE_FETCH_ID = "fetch-official-object-current"
PROVIDER_ID = "law-go-kr"
EXACT_LOCATOR = "law:article:11"
OBJECT_EXACT_LOCATOR = "case:holding:42"


def _binding_source_values(
    *,
    resource_version_id: str = "version-source",
    provision_id: str = "provision-source",
    evidence_item_id: str = EVIDENCE_ITEM_ID,
    exact_locator: str = EXACT_LOCATOR,
    source_fetch_id: str = SOURCE_FETCH_ID,
) -> tuple:
    return (
        SNAPSHOT_ID,
        3,
        SNAPSHOT_SHA,
        date(2026, 7, 20),
        evidence_item_id,
        resource_version_id,
        provision_id,
        exact_locator,
        source_fetch_id,
        PROVIDER_ID,
        EXCERPT_SHA,
        PROVISION_SHA,
        PROVISION_BODY,
        NOW,
        "MINISTRY_OF_GOVERNMENT_LEGISLATION",
        "primary_official",
        "legal-freshness-v1",
    )


def _queue_binding_values(
    *,
    resource_version_id: str = "version-source",
    provision_id: str = "provision-source",
) -> tuple:
    values = _binding_source_values(
        resource_version_id=resource_version_id,
        provision_id=provision_id,
    )
    return (*values[:12], *values[13:])


class _Cursor:
    def __init__(
        self,
        *,
        citation_source: tuple | None = None,
        relation_source: tuple | None = None,
        object_binding_source: tuple | None = None,
        valid_delegation_id: str | None = None,
        latest_citation_event_id: str | None = None,
        latest_relation_event_id: str | None = None,
        citation_queue_rows: list[tuple] | None = None,
        relation_queue_rows: list[tuple] | None = None,
    ):
        self.calls: list[tuple[str, tuple | None]] = []
        self.row = None
        self.rows: list[tuple] = []
        self.citation_source = citation_source
        self.relation_source = relation_source
        self.object_binding_source = object_binding_source
        self.valid_delegation_id = valid_delegation_id
        self.latest_citation_event_id = latest_citation_event_id
        self.latest_relation_event_id = latest_relation_event_id
        self.citation_queue_rows = citation_queue_rows or []
        self.relation_queue_rows = relation_queue_rows or []

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("INSERT INTO project_legal_"):
            assert params is not None
            assert normalized.count("%s") == len(params)
        self.calls.append((normalized, params))
        self.rows = []
        if (
            "FROM legal_citations AS citation" in normalized
            and "JOIN current_snapshot AS snapshot" in normalized
            and "WHERE citation.legal_citation_id = %s" in normalized
        ):
            self.row = self.citation_source
        elif (
            "FROM legal_relation_assertions AS relation" in normalized
            and "JOIN current_snapshot AS snapshot" in normalized
            and "WHERE relation.legal_relation_assertion_id = %s"
            in normalized
        ):
            self.row = self.relation_source
        elif (
            normalized.startswith(
                "SELECT snapshot.legal_evidence_snapshot_id"
            )
            and "item.legal_evidence_item_id = %s" in normalized
            and "item.legal_resource_id = %s" in normalized
        ):
            self.row = self.object_binding_source
        elif normalized.startswith("SELECT 1 FROM legal_citations AS citation"):
            self.row = (1,)
        elif normalized.startswith(
            "SELECT 1 FROM legal_relation_assertions AS relation"
        ):
            self.row = (1,)
        elif normalized.startswith(
            "SELECT delegation.legal_approval_delegation_id"
        ):
            self.row = (
                (self.valid_delegation_id,)
                if self.valid_delegation_id == params[0]
                else None
            )
        elif normalized.startswith(
            "SELECT review.project_legal_citation_review_version_id"
        ) and "project_legal_citation_review_versions" in normalized:
            self.row = (
                (
                    "project-citation-review-version-latest",
                    1,
                    self.latest_citation_event_id,
                )
                if self.latest_citation_event_id
                else None
            )
        elif normalized.startswith(
            "SELECT review.project_legal_relation_review_version_id"
        ) and "project_legal_relation_review_versions" in normalized:
            self.row = (
                (
                    "project-relation-review-version-latest",
                    1,
                    self.latest_relation_event_id,
                )
                if self.latest_relation_event_id
                else None
            )
        elif normalized.startswith(
            "SELECT legal_citation_review_event_id "
            "FROM project_legal_citation_review_events"
        ):
            self.row = (
                (self.latest_citation_event_id,)
                if self.latest_citation_event_id
                else None
            )
        elif normalized.startswith(
            "SELECT legal_relation_review_event_id "
            "FROM project_legal_relation_review_events"
        ):
            self.row = (
                (self.latest_relation_event_id,)
                if self.latest_relation_event_id
                else None
            )
        elif normalized.startswith("SELECT citation.legal_citation_id"):
            self.row = None
            self.rows = self.citation_queue_rows
        elif normalized.startswith(
            "SELECT relation.legal_relation_assertion_id"
        ):
            self.row = None
            self.rows = self.relation_queue_rows
        elif (
            "RETURNING project_legal_citation_review_version_id"
            in normalized
        ):
            self.row = (params[0],)
        elif (
            "RETURNING project_legal_citation_review_event_id"
            in normalized
        ):
            self.row = (params[0],)
        elif (
            "RETURNING project_legal_relation_review_version_id"
            in normalized
        ):
            self.row = (params[0],)
        elif (
            "RETURNING project_legal_relation_review_event_id"
            in normalized
        ):
            self.row = (params[0],)
        else:
            self.row = None

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _repository(cursor: _Cursor) -> PostgresLegalCitatorRepository:
    return PostgresLegalCitatorRepository(
        tenant_id="tenant-1",
        project_id="project-1",
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        now=lambda: NOW,
    )


def _citation_source() -> tuple:
    return (
        "resource-source",
        "version-source",
        "provision-source",
        "건축법 제11조",
        "resource-target",
        "version-target",
        "provision-target",
        "machine_inferred",
        "citator-v1",
        0.82,
        {"origin": "deterministic-candidate"},
        NOW,
        *_binding_source_values(),
    )


def _relation_source() -> tuple:
    return (
        "resource-subject",
        "resource-object",
        "limits",
        "version-subject",
        "provision-subject",
        "machine_inferred",
        "citator-v1",
        0.71,
        date(2026, 7, 20),
        NOW,
        "pending",
        {"origin": "deterministic-candidate"},
        *_binding_source_values(
            resource_version_id="version-subject",
            provision_id="provision-subject",
        ),
    )


def test_approved_review_requires_exact_excerpt_and_matching_hash():
    with pytest.raises(ValidationError, match="exact evidenceExcerpt"):
        CitatorReviewInput(
            reviewStatus="approved",
            reason="citation confirmed",
        )
    with pytest.raises(ValidationError, match="cannot override"):
        CitatorReviewInput(
            reviewStatus="needs_revision",
            reason="recheck",
            metadata={"tenantId": "tenant-2"},
        )
    with pytest.raises(ValidationError, match="cannot override"):
        CitatorReviewInput(
            reviewStatus="needs_revision",
            reason="recheck",
            metadata={
                "snapshotId": "caller-selected",
                "sourceAuthority": "caller-claimed",
            },
        )


def test_approval_excerpt_must_match_current_item_and_provision_body():
    wrong_item = list(_citation_source())
    wrong_item[22] = "a" * 64
    with pytest.raises(ValueError, match="current evidence item"):
        _repository(_Cursor(citation_source=tuple(wrong_item))).review_citation(
            "citation-candidate-1",
            CitatorReviewInput(
                reviewStatus="approved",
                reason="official provision checked",
                evidenceExcerpt=EXCERPT,
                evidenceExcerptSha256=EXCERPT_SHA,
            ),
            reviewer_id="counsel-1",
            reviewer_role="legal_counsel_approver",
        )

    wrong_body = list(_citation_source())
    wrong_body[24] = "Another official provision without the excerpt."
    wrong_body[23] = hashlib.sha256(
        wrong_body[24].encode("utf-8")
    ).hexdigest()
    with pytest.raises(ValueError, match="exact substring"):
        _repository(_Cursor(citation_source=tuple(wrong_body))).review_citation(
            "citation-candidate-1",
            CitatorReviewInput(
                reviewStatus="approved",
                reason="official provision checked",
                evidenceExcerpt=EXCERPT,
                evidenceExcerptSha256=EXCERPT_SHA,
            ),
            reviewer_id="counsel-1",
            reviewer_role="legal_counsel_approver",
        )


def test_nonfinal_review_still_requires_current_intact_binding():
    bad_body_hash = list(_relation_source())
    bad_body_hash[23] = "b" * 64
    with pytest.raises(ValueError, match="official provision hash"):
        _repository(
            _Cursor(relation_source=tuple(bad_body_hash))
        ).review_relation(
            "relation-candidate-1",
            CitatorReviewInput(
                reviewStatus="needs_revision",
                reason="official evidence needs correction",
            ),
            reviewer_id="reviewer-1",
            reviewer_role="legal_reviewer",
        )


def test_rejection_may_omit_excerpt_but_keeps_current_binding():
    cursor = _Cursor(citation_source=_citation_source())
    outcome = _repository(cursor).review_citation(
        "citation-candidate-1",
        CitatorReviewInput(
            reviewStatus="rejected",
            reason="candidate cites the wrong legal proposition",
        ),
        reviewer_id="counsel-1",
        reviewer_role="legal_counsel_approver",
    )
    assert outcome.final_status == "rejected"
    assert outcome.evidence_excerpt_sha256 is None
    assert outcome.evidence_binding is not None
    assert outcome.evidence_binding.snapshot_id == SNAPSHOT_ID
    version_insert = next(
        call
        for call in cursor.calls
        if call[0].startswith(
            "INSERT INTO project_legal_citation_review_versions"
        )
    )
    assert version_insert[1][22] is None
    assert version_insert[1][23] is None
    assert version_insert[1][4] == SNAPSHOT_ID


def test_human_asserted_candidate_enforces_maker_checker_separation():
    source = list(_citation_source())
    source[7] = "human_asserted"
    source[10] = {"assertedBy": "counsel-1"}
    cursor = _Cursor(citation_source=tuple(source))
    with pytest.raises(PermissionError, match="maker and checker"):
        _repository(cursor).review_citation(
            "citation-candidate-1",
            CitatorReviewInput(
                reviewStatus="approved",
                reason="confirmed",
                evidenceExcerpt=EXCERPT,
                evidenceExcerptSha256=EXCERPT_SHA,
            ),
            reviewer_id="counsel-1",
            reviewer_role="legal_counsel_approver",
        )
    assert not any(
        sql.startswith("INSERT INTO")
        for sql, _params in cursor.calls
    )
    with pytest.raises(ValidationError, match="does not match"):
        CitatorReviewInput(
            reviewStatus="approved",
            reason="citation confirmed",
            evidenceExcerpt=EXCERPT,
            evidenceExcerptSha256="a" * 64,
        )


def test_citation_approval_appends_tenant_review_version_and_event():
    cursor = _Cursor(citation_source=_citation_source())
    outcome = _repository(cursor).review_citation(
        "citation-candidate-1",
        CitatorReviewInput(
            reviewStatus="approved",
            reason="official provision checked",
            evidenceExcerpt=EXCERPT,
            evidenceExcerptSha256=EXCERPT_SHA,
        ),
        reviewer_id="counsel-1",
        reviewer_role="legal_counsel_approver",
    )
    assert outcome.final_status == "verified"
    assert outcome.source_record_id == "citation-candidate-1"
    assert outcome.review_version_id.startswith(
        "project-legal-citation-review:"
    )
    assert outcome.review_version == 1
    assert outcome.reviewed_at == NOW
    assert outcome.evidence_excerpt_sha256 == EXCERPT_SHA
    assert outcome.evidence_binding is not None
    assert outcome.evidence_binding.snapshot_id == SNAPSHOT_ID
    assert outcome.evidence_binding.evidence_item_id == EVIDENCE_ITEM_ID
    assert outcome.evidence_binding.provision_id == "provision-source"
    assert outcome.evidence_binding.exact_locator == EXACT_LOCATOR
    citation_insert = next(
        call
        for call in cursor.calls
        if call[0].startswith(
            "INSERT INTO project_legal_citation_review_versions"
        )
    )
    assert citation_insert[1][1:4] == (
        "tenant-1",
        "project-1",
        "citation-candidate-1",
    )
    assert citation_insert[1][4:13] == (
        SNAPSHOT_ID,
        EVIDENCE_ITEM_ID,
        "version-source",
        "provision-source",
        SOURCE_FETCH_ID,
        PROVIDER_ID,
        EXACT_LOCATOR,
        EXCERPT_SHA,
        PROVISION_SHA,
    )
    assert citation_insert[1][14] == "approved"
    assert citation_insert[1][15] == "verified"
    assert citation_insert[1][16] == "approved"
    assert citation_insert[1][18] == "counsel-1"
    assert citation_insert[1][22] == EXCERPT
    assert citation_insert[1][23] == EXCERPT_SHA
    event_insert = next(
        call
        for call in cursor.calls
        if call[0].startswith(
            "INSERT INTO project_legal_citation_review_events"
        )
    )
    assert event_insert[1][3] == outcome.review_version_id
    assert event_insert[1][4] == "citation-candidate-1"
    assert event_insert[1][5:14] == citation_insert[1][4:13]
    assert event_insert[1][15] == "approved"
    assert not any(
        sql.startswith("INSERT INTO legal_citations")
        for sql, _params in cursor.calls
    )


def test_scope_check_and_overlay_write_are_atomic_on_review_principal():
    review_cursor = _Cursor(citation_source=_citation_source())
    repository = PostgresLegalCitatorRepository(
        tenant_id="tenant-1",
        project_id="project-1",
        review_connection_factory=lambda: _Connection(review_cursor),
        schema_name="realytics",
        now=lambda: NOW,
    )
    repository.review_citation(
        "citation-candidate-1",
        CitatorReviewInput(
            reviewStatus="needs_revision",
            reason="target requires another official-source check",
        ),
        reviewer_id="reviewer-1",
        reviewer_role="legal_reviewer",
    )
    assert any(
        "legal_evidence_snapshot_items" in sql
        for sql, _params in review_cursor.calls
    )
    assert any(
        sql.startswith(
            "INSERT INTO project_legal_citation_review_versions"
        )
        for sql, _params in review_cursor.calls
    )
    assert not any(
        sql.startswith("INSERT INTO legal_citations")
        for sql, _params in review_cursor.calls
    )


def test_machine_or_non_counsel_actor_cannot_finalize_citation():
    cursor = _Cursor(citation_source=_citation_source())
    review = CitatorReviewInput(
        reviewStatus="approved",
        reason="official provision checked",
        evidenceExcerpt=EXCERPT,
        evidenceExcerptSha256=EXCERPT_SHA,
    )
    with pytest.raises(ValueError, match="human legal reviewer"):
        _repository(cursor).review_citation(
            "citation-candidate-1",
            review,
            reviewer_id="system",
            reviewer_role="legal_counsel_approver",
        )
    with pytest.raises(PermissionError, match="final Citator decision"):
        _repository(cursor).review_citation(
            "citation-candidate-1",
            review,
            reviewer_id="legal-reviewer-1",
            reviewer_role="legal_reviewer",
        )
    with pytest.raises(ValueError, match="human legal reviewer"):
        _repository(cursor).review_citation(
            "citation-candidate-1",
            CitatorReviewInput(
                reviewStatus="rejected",
                reason="wrong target",
            ),
            reviewer_id="tenant-admin-1",
            reviewer_role="tenant_admin",
        )
    assert not any(
        call[0].startswith(
            "INSERT INTO project_legal_citation_review_versions"
        )
        for call in cursor.calls
    )


def test_external_counsel_review_requires_active_project_delegation():
    review = CitatorReviewInput(
        reviewStatus="approved",
        reason="official provision checked",
        evidenceExcerpt=EXCERPT,
        evidenceExcerptSha256=EXCERPT_SHA,
    )
    missing_cursor = _Cursor(citation_source=_citation_source())
    with pytest.raises(PermissionError, match="legalApprovalDelegationId"):
        _repository(missing_cursor).review_citation(
            "citation-candidate-1",
            review,
            reviewer_id="outside-counsel-1",
            reviewer_role="external_counsel",
        )

    invalid_cursor = _Cursor(citation_source=_citation_source())
    with pytest.raises(PermissionError, match="delegation not found"):
        _repository(invalid_cursor).review_citation(
            "citation-candidate-1",
            review.model_copy(
                update={
                    "legal_approval_delegation_id": "delegation-expired"
                }
            ),
            reviewer_id="outside-counsel-1",
            reviewer_role="external_counsel",
        )
    assert not any(
        sql.startswith(
            "INSERT INTO project_legal_citation_review_versions"
        )
        for sql, _params in invalid_cursor.calls
    )

    valid_cursor = _Cursor(
        citation_source=_citation_source(),
        valid_delegation_id="delegation-active",
    )
    outcome = _repository(valid_cursor).review_citation(
        "citation-candidate-1",
        review.model_copy(
            update={"legal_approval_delegation_id": "delegation-active"}
        ),
        reviewer_id="outside-counsel-1",
        reviewer_role="external_counsel",
    )
    assert outcome.legal_approval_delegation_id == "delegation-active"
    delegation_check = next(
        call
        for call in valid_cursor.calls
        if call[0].startswith(
            "SELECT delegation.legal_approval_delegation_id"
        )
    )
    assert delegation_check[1][1:5] == (
        "tenant-1",
        "project-1",
        "outside-counsel-1",
        "external_counsel",
    )
    event_insert = next(
        call
        for call in valid_cursor.calls
        if call[0].startswith(
            "INSERT INTO project_legal_citation_review_events"
        )
    )
    assert event_insert[1][21] == "delegation-active"


def test_advisory_lock_and_latest_successor_check_prevent_review_fork():
    cursor = _Cursor(citation_source=None)
    with pytest.raises(LookupError, match="project-scoped immutable"):
        _repository(cursor).review_citation(
            "citation-already-reviewed",
            CitatorReviewInput(
                reviewStatus="needs_revision",
                reason="recheck requested",
            ),
            reviewer_id="reviewer-1",
            reviewer_role="legal_reviewer",
        )
    lock_index = next(
        index
        for index, (sql, _params) in enumerate(cursor.calls)
        if "pg_advisory_xact_lock" in sql
    )
    latest_index = next(
        index
        for index, (sql, _params) in enumerate(cursor.calls)
        if (
            "FROM legal_citations AS citation" in sql
            and "JOIN current_snapshot AS snapshot" in sql
        )
    )
    assert lock_index < latest_index
    latest_sql = cursor.calls[latest_index][0]
    assert "legal_evidence_snapshot_items" in latest_sql
    assert "legal_resource_version_lineages" in latest_sql
    assert "newer.snapshot_version > snapshot.snapshot_version" in latest_sql
    assert "replacement.version_sequence > version.version_sequence" in (
        latest_sql
    )
    assert "item.verified_at + INTERVAL '7 days'" in latest_sql
    assert "provision.source_locator = item.exact_locator" in latest_sql
    assert "provision.provision_sha256 = encode(" in latest_sql
    assert "metadata_json ->> 'reviewStatus'" in latest_sql
    assert not any(
        sql.startswith(
            "INSERT INTO project_legal_citation_review_versions"
        )
        for sql, _params in cursor.calls
    )


def test_review_chain_must_supersede_exact_latest_event():
    cursor = _Cursor(
        citation_source=_citation_source(),
        latest_citation_event_id="review-event-latest",
    )
    with pytest.raises(ValueError, match="latest review event"):
        _repository(cursor).review_citation(
            "citation-candidate-1",
            CitatorReviewInput(
                reviewStatus="needs_revision",
                reason="recheck requested",
                supersedesReviewEventId="review-event-stale",
            ),
            reviewer_id="reviewer-1",
            reviewer_role="legal_reviewer",
        )
    assert not any(
        sql.startswith(
            "INSERT INTO project_legal_citation_review_versions"
        )
        for sql, _params in cursor.calls
    )


def test_relation_needs_revision_is_append_only_and_not_finalized():
    cursor = _Cursor(relation_source=_relation_source())
    outcome = _repository(cursor).review_relation(
        "relation-candidate-1",
        CitatorReviewInput(
            reviewStatus="needs_revision",
            reason="exact treatment passage is missing",
        ),
        reviewer_id="reviewer-1",
        reviewer_role="legal_reviewer",
    )
    assert outcome.final_status == "unknown"
    relation_insert = next(
        call
        for call in cursor.calls
        if call[0].startswith(
            "INSERT INTO project_legal_relation_review_versions"
        )
    )
    assert relation_insert[1][3] == "relation-candidate-1"
    assert relation_insert[1][4] == SNAPSHOT_ID
    assert relation_insert[1][13:21] == (None,) * 8
    assert relation_insert[1][22] == "needs_revision"
    assert relation_insert[1][23] == "unknown"
    assert relation_insert[1][24] == "pending"
    assert relation_insert[1][26] == "reviewer-1"
    assert relation_insert[1][28] == NOW
    assert not any(
        call[0].startswith("UPDATE ")
        or call[0].startswith("DELETE ")
        for call in cursor.calls
    )


def test_review_queue_exposes_latest_event_for_exact_chain_continuation():
    cursor = _Cursor(
        citation_queue_rows=[
            (
                "citation-revision-1",
                "건축법 제1조",
                "unknown",
                "machine_inferred",
                0.82,
                NOW,
                "pending",
                "reviewer-1",
                NOW,
                "citation-review-event-1",
                "citation-review-version-1",
                1,
                "needs_revision",
                "unresolved",
                *_queue_binding_values(),
                True,
            )
        ],
        relation_queue_rows=[
            (
                "relation-revision-1",
                "resource-subject",
                "resource-object",
                "limits",
                "unknown",
                "machine_inferred",
                0.71,
                NOW,
                "pending",
                "reviewer-1",
                NOW,
                "relation-review-event-1",
                "relation-review-version-1",
                1,
                "needs_revision",
                "candidate",
                *_queue_binding_values(
                    resource_version_id="version-subject",
                    provision_id="provision-subject",
                ),
                True,
            )
        ],
    )
    queue = _repository(cursor).list_review_queue(limit=10)
    assert queue["citations"][0]["latestReviewEventId"] == (
        "citation-review-event-1"
    )
    assert queue["citations"][0]["reviewedBy"] == "reviewer-1"
    assert queue["relations"][0]["latestReviewEventId"] == (
        "relation-review-event-1"
    )
    assert queue["citations"][0]["currentEvidenceBinding"] == {
        "snapshotId": SNAPSHOT_ID,
        "snapshotVersion": 3,
        "snapshotSha256": SNAPSHOT_SHA,
        "snapshotAsOfDate": date(2026, 7, 20),
        "evidenceItemId": EVIDENCE_ITEM_ID,
        "resourceVersionId": "version-source",
        "provisionId": "provision-source",
        "exactLocator": EXACT_LOCATOR,
        "sourceFetchId": SOURCE_FETCH_ID,
        "providerId": PROVIDER_ID,
        "excerptSha256": EXCERPT_SHA,
        "provisionSha256": PROVISION_SHA,
        "verifiedAt": NOW,
        "sourceAuthority": "MINISTRY_OF_GOVERNMENT_LEGISLATION",
        "authorityTier": "primary_official",
        "freshnessPolicyVersion": "legal-freshness-v1",
        "current": True,
    }
    assert queue["citations"][0]["reviewBindingCurrent"] is True
    assert "reviewerRole" not in (
        queue["citations"][0]["currentEvidenceBinding"]
    )
    assert queue["relations"][0]["reviewedAt"] == NOW


def test_interpretive_relation_approval_records_exact_human_review():
    object_binding = _binding_source_values(
        resource_version_id="version-object",
        provision_id="provision-object",
        evidence_item_id=OBJECT_EVIDENCE_ITEM_ID,
        exact_locator=OBJECT_EXACT_LOCATOR,
        source_fetch_id=OBJECT_SOURCE_FETCH_ID,
    )
    cursor = _Cursor(
        relation_source=_relation_source(),
        object_binding_source=object_binding,
    )
    outcome = _repository(cursor).review_relation(
        "relation-candidate-1",
        CitatorReviewInput(
            reviewStatus="approved",
            reason="limiting passage confirmed against official judgment",
            evidenceExcerpt=EXCERPT,
            evidenceExcerptSha256=EXCERPT_SHA,
            objectEvidenceItemId=OBJECT_EVIDENCE_ITEM_ID,
        ),
        reviewer_id="counsel-1",
        reviewer_role="legal_counsel_approver",
    )
    assert outcome.final_status == "verified"
    assert outcome.reviewed_at == NOW
    assert outcome.evidence_excerpt_sha256 == EXCERPT_SHA
    assert outcome.object_evidence_binding is not None
    assert (
        outcome.object_evidence_binding.evidence_item_id
        == OBJECT_EVIDENCE_ITEM_ID
    )
    assert (
        outcome.object_evidence_binding.resource_version_id
        == "version-object"
    )
    assert outcome.object_evidence_binding.provision_id == "provision-object"
    assert (
        outcome.object_evidence_binding.exact_locator
        == OBJECT_EXACT_LOCATOR
    )
    relation_insert = next(
        call
        for call in cursor.calls
        if call[0].startswith(
            "INSERT INTO project_legal_relation_review_versions"
        )
    )
    assert relation_insert[1][3] == "relation-candidate-1"
    assert relation_insert[1][4:13] == (
        SNAPSHOT_ID,
        EVIDENCE_ITEM_ID,
        "version-subject",
        "provision-subject",
        SOURCE_FETCH_ID,
        PROVIDER_ID,
        EXACT_LOCATOR,
        EXCERPT_SHA,
        PROVISION_SHA,
    )
    assert relation_insert[1][13:21] == (
        OBJECT_EVIDENCE_ITEM_ID,
        "version-object",
        "provision-object",
        OBJECT_SOURCE_FETCH_ID,
        PROVIDER_ID,
        OBJECT_EXACT_LOCATOR,
        EXCERPT_SHA,
        PROVISION_SHA,
    )
    assert relation_insert[1][22] == "approved"
    assert relation_insert[1][23] == "verified"
    assert relation_insert[1][24] == "approved"
    assert relation_insert[1][26] == "counsel-1"
    assert relation_insert[1][28] == NOW
    assert relation_insert[1][30] == EXCERPT
    assert relation_insert[1][31] == EXCERPT_SHA
    event_insert = next(
        call
        for call in cursor.calls
        if call[0].startswith(
            "INSERT INTO project_legal_relation_review_events"
        )
    )
    assert event_insert[1][3] == outcome.review_version_id
    assert event_insert[1][5:14] == relation_insert[1][4:13]
    assert event_insert[1][14:22] == relation_insert[1][13:21]
    assert event_insert[1][23] == "approved"
    assert event_insert[1][24] == "counsel-1"
    assert event_insert[1][26] == NOW
    assert event_insert[1][28] == EXCERPT_SHA


def test_verified_relation_requires_an_exact_current_object_binding():
    review = CitatorReviewInput(
        reviewStatus="approved",
        reason="interpretive relation checked",
        evidenceExcerpt=EXCERPT,
        evidenceExcerptSha256=EXCERPT_SHA,
    )
    missing_id_cursor = _Cursor(relation_source=_relation_source())
    with pytest.raises(ValueError, match="objectEvidenceItemId"):
        _repository(missing_id_cursor).review_relation(
            "relation-candidate-1",
            review,
            reviewer_id="counsel-1",
            reviewer_role="legal_counsel_approver",
        )
    assert not any(
        sql.startswith(
            "INSERT INTO project_legal_relation_review_versions"
        )
        for sql, _params in missing_id_cursor.calls
    )

    stale_target_cursor = _Cursor(relation_source=_relation_source())
    with pytest.raises(LookupError, match="exact current binding"):
        _repository(stale_target_cursor).review_relation(
            "relation-candidate-1",
            review.model_copy(
                update={
                    "object_evidence_item_id": OBJECT_EVIDENCE_ITEM_ID,
                }
            ),
            reviewer_id="counsel-1",
            reviewer_role="legal_counsel_approver",
        )
    target_query = next(
        call
        for call in stale_target_cursor.calls
        if call[0].startswith(
            "SELECT snapshot.legal_evidence_snapshot_id"
        )
    )
    assert target_query[1] == (
        OBJECT_EVIDENCE_ITEM_ID,
        "resource-object",
        SNAPSHOT_ID,
        "tenant-1",
        "project-1",
    )
    assert "item.legal_resource_id = %s" in target_query[0]
    assert "lineage.validated_at <= statement_timestamp()" in target_query[0]
    assert "provision.source_locator = item.exact_locator" in target_query[0]
    assert "replacement.version_sequence > version.version_sequence" in (
        target_query[0]
    )


def test_citation_review_rejects_relation_object_binding_input():
    cursor = _Cursor(citation_source=_citation_source())
    with pytest.raises(ValueError, match="only valid for a relation"):
        _repository(cursor).review_citation(
            "citation-candidate-1",
            CitatorReviewInput(
                reviewStatus="needs_revision",
                reason="recheck",
                objectEvidenceItemId=OBJECT_EVIDENCE_ITEM_ID,
            ),
            reviewer_id="reviewer-1",
            reviewer_role="legal_reviewer",
        )
    assert cursor.calls == []


class _ServiceRepository:
    def __init__(self):
        self.calls = []

    def list_review_queue(self, *, record_kind, limit):
        self.calls.append(("list", record_kind, limit))
        return {"citations": [], "relations": [], "totalCount": 0}

    def review_citation(
        self,
        citation_id,
        review,
        *,
        reviewer_id,
        reviewer_role,
    ):
        self.calls.append(
            (
                "citation",
                citation_id,
                review.review_status.value,
                reviewer_id,
                reviewer_role,
            )
        )
        return CitatorReviewOutcome(
            recordKind="citation",
            sourceRecordId=citation_id,
            reviewVersionId="citation-review-version",
            reviewVersion=1,
            reviewEventId="citation-review-event",
            reviewStatus=review.review_status,
            finalStatus="rejected",
            reviewerId=reviewer_id,
            reviewerRole=reviewer_role,
            reviewedAt=NOW,
        )


def test_use_case_exposes_queue_and_explicit_human_review_only():
    repository = _ServiceRepository()
    service = LegalCitatorReviewService(repository)
    queue = service.list_queue(
        record_kind=CitatorRecordKind.CITATION,
        limit=25,
    )
    result = service.review_citation(
        "citation-1",
        CitatorReviewInput(
            reviewStatus="rejected",
            reason="wrong target",
        ),
        reviewer_id="reviewer-1",
        reviewer_role="legal_reviewer",
    )
    assert queue["schemaVersion"].endswith("queue-v2")
    assert queue["corpusRecordsImmutable"] is True
    assert result["appendOnly"] is True
    assert repository.calls == [
        ("list", CitatorRecordKind.CITATION, 25),
        (
            "citation",
            "citation-1",
            "rejected",
            "reviewer-1",
            "legal_reviewer",
        ),
    ]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": (
                "/projects/project-1/legal/citator/citations/"
                "citation-1/reviews"
            ),
            "headers": [],
            "query_string": b"",
        }
    )


class _RouterService:
    def review_citation(self, *_args, **_kwargs):
        raise PermissionError("final counsel required")


def test_router_maps_final_approval_role_failure_to_403(monkeypatch):
    monkeypatch.setattr(
        legal_citator,
        "_service",
        lambda *_args, **_kwargs: (
            _RouterService(),
            "tenant-admin-1",
            "tenant_admin",
        ),
    )
    with pytest.raises(HTTPException) as error:
        legal_citator.review_citator_citation(
            "project-1",
            "citation-1",
            _request(),
            CitatorReviewInput(
                reviewStatus="approved",
                reason="checked",
                evidenceExcerpt=EXCERPT,
                evidenceExcerptSha256=EXCERPT_SHA,
            ),
        )
    assert error.value.status_code == 403
    assert error.value.detail["error"] == "legal_counsel_approver_required"


def test_router_has_no_legacy_json_or_machine_finalization_fallback(
    monkeypatch,
):
    monkeypatch.setattr(
        legal_citator,
        "get_project_record",
        lambda _project_id: {
            "project": {"id": "project-1"},
            "_meta": {"tenantId": "tenant-1"},
        },
    )
    monkeypatch.setattr(
        legal_citator,
        "authorize_request",
        lambda *_args, **_kwargs: {
            "authenticated": True,
            "role": "legal_reviewer",
            "user": {
                "userId": "reviewer-1",
                "tenantId": "tenant-1",
                "role": "legal_reviewer",
                "metadata": {"principalType": "human"},
            },
        },
    )
    monkeypatch.setattr(
        legal_citator,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(legal_citator, "postgres_configured", lambda: False)
    with pytest.raises(HTTPException) as error:
        legal_citator._service("project-1", _request())
    assert error.value.status_code == 503
    assert error.value.detail["error"] == "normalized_legal_store_required"


def test_router_write_rejects_service_principal_with_legal_role(monkeypatch):
    monkeypatch.setattr(
        legal_citator,
        "get_project_record",
        lambda _project_id: {
            "project": {"id": "project-1"},
            "_meta": {"tenantId": "tenant-1"},
        },
    )
    monkeypatch.setattr(
        legal_citator,
        "authorize_request",
        lambda *_args, **_kwargs: {
            "authenticated": True,
            "role": "legal_counsel_approver",
            "user": {
                "userId": "legal-review-worker",
                "tenantId": "tenant-1",
                "role": "legal_counsel_approver",
                "metadata": {"principalType": "service"},
            },
        },
    )
    monkeypatch.setattr(
        legal_citator,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    with pytest.raises(HTTPException) as error:
        legal_citator._service(
            "project-1",
            _request(),
            require_human=True,
        )
    assert error.value.status_code == 403
    assert error.value.detail["error"] == "human_legal_reviewer_required"
