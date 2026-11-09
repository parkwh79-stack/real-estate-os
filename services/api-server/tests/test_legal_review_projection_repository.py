from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from db.repositories.legal_review_projection_repository import (
    PostgresLegalReviewProjectionRepository,
)
from schemas.legal_review_projection import (
    LegalReviewProjectionInput,
    PermitReviewProjectionInput,
)
from use_cases.legal_review_projection import (
    build_legal_pod_projection_inputs,
    mark_projection_persistence_failure,
)


NOW = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, *, previous=None, rows=None):
        self.previous = dict(previous or {})
        self.rows = dict(rows or {})
        self.calls = []
        self.one = None
        self.many = []

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        values = tuple(params or ())
        assert normalized.count("%s") == len(values), normalized
        self.calls.append((normalized, values))
        self.one = None
        self.many = []
        for kind, table in (
            ("legal", "project_legal_review_versions"),
            ("permit", "project_permit_review_versions"),
        ):
            if (
                normalized.startswith(
                    "SELECT review_version_id, review_id"
                )
                and f"FROM {table}" in normalized
            ):
                self.many = list(self.rows.get(kind) or [])
                return
            if (
                normalized.startswith("SELECT review_version_id")
                and f"FROM {table}" in normalized
            ):
                self.one = self.previous.get(kind)
                return
            if normalized.startswith(f"INSERT INTO {table}"):
                self.one = (
                    values[0],
                    values[4],
                    "pg-jsonb-row-v1",
                    "a" * 64,
                    NOW,
                )
                return

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.exit_type = None

    def cursor(self):
        return self.cursor_value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_args):
        self.exit_type = exc_type
        return False


def _repository(cursor):
    return PostgresLegalReviewProjectionRepository(
        tenant_id="tenant-1",
        project_id="project-1",
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        now=lambda: NOW,
    )


def _legal(**overrides):
    value = {
        "reviewId": "legal-review-1",
        "status": "caution",
        "evidence": {
            "status": "partial",
            "evidenceLocators": [
                {
                    "sourceType": "document_page",
                    "sourceDocumentId": "document-1",
                    "pageNumber": 3,
                }
            ],
            "stale": True,
        },
        "parser": {"status": "not_applicable"},
        "recordLocator": {
            "sourceType": "legal_review_record",
            "reviewId": "legal-review-1",
        },
        "display": {"checks": [{"key": "permit", "severity": "caution"}]},
    }
    value.update(overrides)
    return LegalReviewProjectionInput.model_validate(value)


def _permit(**overrides):
    value = {
        "reviewId": "permit-review-1",
        "status": "pending_evidence",
        "evidence": {
            "status": "missing",
            "evidenceLocators": [],
            "stale": True,
        },
        "parser": {"status": "not_available"},
        "recordLocator": {
            "sourceType": "permit_review_record",
            "reviewId": "permit-review-1",
        },
    }
    value.update(overrides)
    return PermitReviewProjectionInput.model_validate(value)


def test_legal_and_permit_projection_insert_is_atomic_tenant_scoped_and_hashed():
    cursor = _Cursor()
    saved = _repository(cursor).append_legal_pod_run(
        legal=_legal(),
        permit=_permit(),
        actor_id="legal-pod-runner",
    )

    assert cursor.calls[0] == (
        'SET LOCAL search_path TO "realytics", pg_catalog',
        (),
    )
    assert cursor.calls[1] == (
        "SELECT set_config('realytics.tenant_id', %s, true)",
        ("tenant-1",),
    )
    inserts = [
        call
        for call in cursor.calls
        if call[0].startswith("INSERT INTO project_")
    ]
    assert len(inserts) == 2
    assert all(call[1][1:3] == ("tenant-1", "project-1") for call in inserts)
    assert all(
        "projection_sha256" not in call[0].split(") VALUES", 1)[0]
        for call in inserts
    )
    assert saved["legalReview"]["reviewVersion"] == 1
    assert (
        saved["legalReview"]["projectionHashContract"]
        == "pg-jsonb-row-v1"
    )
    assert saved["legalReview"]["projectionSha256"].startswith("sha256:")
    assert saved["legalReview"]["checks"][0]["key"] == "permit"
    assert saved["permitReview"]["analysisOnly"] is True
    assert saved["permitReview"]["reportEligible"] is False


def test_append_derives_version_and_supersedes_from_normalized_table_only():
    cursor = _Cursor(
        previous={
            "legal": ("legal-version-1", 1, "a" * 64),
            "permit": ("permit-version-1", 4, "b" * 64),
        }
    )

    saved = _repository(cursor).append_legal_pod_run(
        legal=_legal(),
        permit=_permit(),
        actor_id="legal-pod-runner",
    )

    assert saved["legalReview"]["reviewVersion"] == 2
    assert (
        saved["legalReview"]["supersedesReviewVersionId"]
        == "legal-version-1"
    )
    assert saved["permitReview"]["reviewVersion"] == 5
    assert (
        saved["permitReview"]["supersedesReviewVersionId"]
        == "permit-version-1"
    )


def test_normalized_read_overwrites_forged_display_and_json_authority_fields():
    row = (
        "legal-version-1",
        "legal-review-1",
        1,
        "caution",
        "preliminary",
        "partial",
        1,
        0,
        0,
        0,
        0,
        0,
        False,
        0,
        True,
        "not_verified",
        None,
        None,
        None,
        False,
        None,
        None,
        "not_applicable",
        None,
        None,
        None,
        None,
        None,
        True,
        False,
        "pending",
        {
            "sourceType": "legal_review_record",
            "reviewId": "legal-review-1",
        },
        {
            "status": "verified",
            "snapshotGateEligible": True,
            "evidenceLocators": [
                {
                    "sourceType": "document_page",
                    "sourceDocumentId": "document-1",
                    "pageNumber": 3,
                }
            ],
        },
        {"status": "verified", "moduleKey": "forged"},
        {
            "status": "clear",
            "analysisOnly": False,
            "reportEligible": True,
            "checks": [{"key": "permit"}],
            },
            "pg-jsonb-row-v1",
            "a" * 64,
        None,
        "legal-pod-runner",
        NOW,
    )
    repository = _repository(_Cursor(rows={"legal": [row]}))

    item = repository.list_legal_reviews(1)[0]

    assert item["status"] == "caution"
    assert item["analysisOnly"] is True
    assert item["reportEligible"] is False
    assert item["evidence"]["status"] == "partial"
    assert item["evidence"]["snapshotGateEligible"] is False
    assert item["parser"]["status"] == "not_applicable"


def test_evidence_projection_rejects_internal_record_locator_as_evidence():
    with pytest.raises(ValidationError, match="cannot be evidence"):
        _legal(
            evidence={
                "status": "partial",
                "evidenceLocators": [
                    {
                        "sourceType": "legal_review_record",
                        "artifactKey": "review-1",
                    }
                ],
            }
        )


def test_gate_eligible_projection_requires_complete_snapshot_contract():
    with pytest.raises(ValidationError, match="snapshotGateEligible"):
        _legal(
            evidence={
                "status": "verified",
                "snapshotGateEligible": True,
                "legalEvidenceSnapshotId": "snapshot-1",
                "snapshotVersion": 1,
                "snapshotSha256": "a" * 64,
            }
        )


def test_builder_separates_record_and_external_evidence_locators():
    legal, permit = build_legal_pod_projection_inputs(
        {
            "legalReview": {
                "reviewId": "legal-review-1",
                "status": "caution",
                "evidenceLocators": [
                    {
                        "sourceType": "legal_review_record",
                        "artifactKey": "internal",
                    },
                    {
                        "sourceType": "api_row",
                        "resourceId": "resource-1",
                        "resourceVersionId": "version-1",
                    },
                ],
                "legalIntelligence": {
                    "evidenceStatus": "partial",
                    "stale": True,
                },
            },
            "permitReview": {
                "reviewId": "permit-review-1",
                "status": "pending_evidence",
                "parserStatus": "not_available",
                "sourceLocators": [],
            },
        }
    )

    assert legal.record_locator.source_type == "legal_review_record"
    assert len(legal.evidence.evidence_locators) == 1
    assert legal.evidence.evidence_locators[0]["resourceId"] == "resource-1"
    assert permit.record_locator.source_type == "permit_review_record"
    assert legal.analysis_only is True
    assert permit.report_eligible is False


def test_projection_failure_cannot_promote_legal_pod_result():
    failed = mark_projection_persistence_failure(
        {
            "legalReview": {
                "analysisOnly": False,
                "reportEligible": True,
            },
            "permitReview": {
                "analysisOnly": False,
                "reportEligible": True,
            },
        },
        reason="normalized_projection_store_unavailable",
        error_type="DatabaseError",
    )

    assert failed["normalizedProjectionPersistence"] == {
        "persisted": False,
        "reason": "normalized_projection_store_unavailable",
        "errorType": "DatabaseError",
    }
    assert failed["legalReview"]["analysisOnly"] is True
    assert failed["legalReview"]["reportEligible"] is False
    assert failed["permitReview"]["normalizedProjectionEligible"] is False
