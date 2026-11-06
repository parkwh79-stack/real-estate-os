from __future__ import annotations

from fastapi import HTTPException
from starlette.requests import Request

import routers.projects as project_router


def _request(method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/projects/project-1/legal-reviews",
            "headers": [],
            "query_string": b"",
        }
    )


def _authorized(*_args, **_kwargs):
    return {
        "authenticated": True,
        "role": "legal_reviewer",
        "user": {
            "userId": "legal-runner",
            "tenantId": "tenant-1",
            "role": "legal_reviewer",
        },
        "authorization": {"allowed": True},
    }


def _project():
    return {
        "project": {"id": "project-1"},
        "_meta": {"tenantId": "tenant-1"},
    }


def test_review_lists_require_normalized_repository_when_postgres_is_configured(
    monkeypatch,
):
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "project-1",
        _project(),
    )
    monkeypatch.setattr(project_router, "authorize_request", _authorized)
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(project_router, "postgres_configured", lambda: True)
    calls = []

    class _Repository:
        def __init__(self, *, tenant_id, project_id):
            calls.append((tenant_id, project_id))

        def list_legal_reviews(self, limit):
            return [{"reviewId": "normalized-legal", "limit": limit}]

        def list_permit_reviews(self, limit):
            return [{"reviewId": "normalized-permit", "limit": limit}]

    monkeypatch.setattr(
        project_router,
        "PostgresLegalReviewProjectionRepository",
        _Repository,
    )

    legal = project_router.list_project_legal_reviews(
        "project-1",
        _request(),
        7,
    )
    permit = project_router.list_project_permit_reviews(
        "project-1",
        _request(),
        9,
    )

    assert legal["source"] == "normalized_postgres"
    assert legal["items"][0]["reviewId"] == "normalized-legal"
    assert permit["source"] == "normalized_postgres"
    assert permit["items"][0]["reviewId"] == "normalized-permit"
    assert calls == [
        ("tenant-1", "project-1"),
        ("tenant-1", "project-1"),
    ]


def test_normalized_list_failure_does_not_fall_back_to_legacy(monkeypatch):
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "project-1",
        _project(),
    )
    monkeypatch.setattr(project_router, "authorize_request", _authorized)
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(project_router, "postgres_configured", lambda: True)

    class _UnavailableRepository:
        def __init__(self, **_kwargs):
            pass

        def list_legal_reviews(self, _limit):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        project_router,
        "PostgresLegalReviewProjectionRepository",
        _UnavailableRepository,
    )

    try:
        project_router.list_project_legal_reviews(
            "project-1",
            _request(),
            20,
        )
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail["error"] == "normalized_legal_store_unavailable"
    else:  # pragma: no cover - assertion aid
        raise AssertionError("normalized read failure must be fail-closed")


def test_legal_pod_projection_write_failure_stays_analysis_only(monkeypatch):
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "project-1",
        _project(),
    )
    monkeypatch.setattr(project_router, "authorize_request", _authorized)
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(project_router, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        project_router,
        "list_land_evidence_records",
        lambda _project_id: [],
    )
    monkeypatch.setattr(
        project_router,
        "run_legal_pod_use_case",
        lambda *_args, **_kwargs: {
            "legalReview": {
                "reviewId": "legal-review-1",
                "status": "caution",
                "analysisOnly": True,
                "reportEligible": False,
                "legalIntelligence": {
                    "evidenceStatus": "missing",
                    "stale": True,
                },
            },
            "permitReview": {
                "reviewId": "permit-review-1",
                "status": "pending_evidence",
                "parserStatus": "not_available",
                "analysisOnly": True,
                "reportEligible": False,
            },
            "riskIssues": [],
            "handoff": {},
        },
    )
    monkeypatch.setattr(
        project_router,
        "PostgresLegalTenantOverlayRepository",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        project_router,
        "seal_legal_review_evidence_snapshot",
        lambda legal_review, **_kwargs: {
            "sealed": False,
            "reason": "no_verified_snapshot",
            "legalReview": legal_review,
        },
    )

    class _UnavailableProjectionRepository:
        def __init__(self, **_kwargs):
            pass

        def append_legal_pod_run(self, **_kwargs):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        project_router,
        "PostgresLegalReviewProjectionRepository",
        _UnavailableProjectionRepository,
    )
    monkeypatch.setattr(
        project_router,
        "upsert_legal_review_record",
        lambda _project_id, value: value,
    )
    monkeypatch.setattr(
        project_router,
        "upsert_project_record",
        lambda _project_id, value: value,
    )
    monkeypatch.setattr(
        project_router,
        "record_sensitive_project_event",
        lambda *_args, **_kwargs: None,
    )

    response = project_router.run_legal_pod(
        "project-1",
        _request("POST"),
        {},
    )

    assert response["normalizedProjectionPersistence"] == {
        "persisted": False,
        "reason": "normalized_projection_store_unavailable",
        "errorType": "RuntimeError",
    }
    assert response["legalReview"]["analysisOnly"] is True
    assert response["legalReview"]["reportEligible"] is False
    assert response["legalReview"]["normalizedProjectionEligible"] is False


def test_legal_pod_persists_preliminary_unverified_source_statuses(
    monkeypatch,
):
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "project-1",
        _project(),
    )
    monkeypatch.setattr(project_router, "authorize_request", _authorized)
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(project_router, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        project_router,
        "list_land_evidence_records",
        lambda _project_id: [],
    )
    legal_review = {
        "reviewId": "legal-review-preliminary",
        "status": "caution",
        "reviewLevel": "preliminary",
        "analysisOnly": True,
        "reportEligible": False,
        "humanReviewStatus": "pending",
        "evidenceLocators": [],
        "legalIntelligence": {
            "evidenceStatus": "unverified",
            "stale": True,
            "citationVerification": {"status": "not_requested"},
        },
    }
    permit_review = {
        "reviewId": "permit-review-preliminary",
        "status": "pending_evidence",
        "reviewLevel": "preliminary",
        "parserStatus": "not_available",
        "evidenceStatus": "missing",
        "sourceLocators": [],
        "analysisOnly": True,
        "reportEligible": False,
    }
    monkeypatch.setattr(
        project_router,
        "run_legal_pod_use_case",
        lambda *_args, **_kwargs: {
            "legalReview": legal_review,
            "permitReview": permit_review,
            "riskIssues": [],
            "handoff": {},
        },
    )
    monkeypatch.setattr(
        project_router,
        "PostgresLegalTenantOverlayRepository",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        project_router,
        "seal_legal_review_evidence_snapshot",
        lambda review, **_kwargs: {
            "sealed": False,
            "reason": "exact_verified_local_citations_missing",
            "legalReview": review,
        },
    )
    captured = {}

    class _ProjectionRepository:
        def __init__(self, **_kwargs):
            pass

        def append_legal_pod_run(self, *, legal, permit, actor_id):
            captured.update(
                {"legal": legal, "permit": permit, "actorId": actor_id}
            )
            return {
                "legalReview": {
                    "reviewVersionId": "legal-version-1",
                    "reviewVersion": 1,
                    "projectionSha256": "sha256:" + ("a" * 64),
                },
                "permitReview": {
                    "reviewVersionId": "permit-version-1",
                    "reviewVersion": 1,
                    "projectionSha256": "sha256:" + ("b" * 64),
                },
            }

    monkeypatch.setattr(
        project_router,
        "PostgresLegalReviewProjectionRepository",
        _ProjectionRepository,
    )
    monkeypatch.setattr(
        project_router,
        "upsert_legal_review_record",
        lambda _project_id, value: value,
    )
    monkeypatch.setattr(
        project_router,
        "upsert_project_record",
        lambda _project_id, value: value,
    )
    monkeypatch.setattr(
        project_router,
        "record_sensitive_project_event",
        lambda *_args, **_kwargs: None,
    )

    response = project_router.run_legal_pod(
        "project-1",
        _request("POST"),
        {},
    )

    assert response["normalizedProjectionPersistence"]["persisted"] is True
    assert captured["actorId"] == "legal-runner"
    assert captured["legal"].evidence.status == "unverified"
    assert (
        captured["legal"].evidence.citation_verification_status
        == "not_requested"
    )
    assert captured["permit"].parser.status == "not_available"
