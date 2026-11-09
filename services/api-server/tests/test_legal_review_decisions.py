from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import routers.projects as project_router
from db.repositories.legal_intelligence_repository import (
    LegalReviewDecisionConflict,
)
from use_cases.legal_review_decisions import (
    decide_legal_review_use_case,
    legal_evidence_readiness,
)


def _review(**overrides):
    value = {
        "schemaVersion": "realytics-legal-review-v2",
        "projectId": "p1",
        "reviewId": "legal-review-1",
        "reviewVersionId": "legal-review-version-1",
        "reviewVersion": 1,
        "projectionSha256": "sha256:" + ("d" * 64),
        "status": "caution",
        "humanReviewStatus": "pending",
        "reviewHistory": [
            {
                "decisionId": "legal-review-decision:old",
                "decisionStatus": "needs_revision",
                "reviewedBy": "reviewer-old",
            }
        ],
        "legalIntelligence": {
            "evidenceStatus": "verified",
            "verifiedReferenceCount": 2,
            "verifiedCitationCount": 2,
            "requiredClaimCount": 2,
            "verifiedClaimCount": 2,
            "claimCoverageComplete": True,
            "stale": False,
            "openConflicts": [],
            "evidenceSnapshotId": "legal-evidence-snapshot-1",
            "snapshotVersion": 1,
            "snapshotSha256": "sha256:" + ("a" * 64),
            "snapshotGateEligible": True,
            "snapshotCreatedBy": "snapshot-maker",
        },
    }
    value.update(overrides)
    return value


def _run(
    review,
    payload,
    *,
    reviewer_id="trusted-user",
    reviewer_role="legal_counsel_approver",
):
    writes = []
    decisions = []
    audits = []

    def load(project_id, limit):
        assert (project_id, limit) == ("p1", 500)
        return [review]

    def save(project_id, value):
        writes.append(deepcopy(value))
        return value

    result = decide_legal_review_use_case(
        "p1",
        "legal-review-1",
        payload,
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        reviewed_at="2026-07-18T10:00:00+00:00",
        review_loader=load,
        review_writer=save,
        decision_writer=lambda value: decisions.append(deepcopy(value)),
        audit_recorder=lambda value: audits.append(deepcopy(value)),
    )
    return result, writes, decisions, audits


def test_approve_appends_decision_and_uses_only_trusted_reviewer_identity():
    original = _review()
    result, writes, decisions, audits = _run(
        original,
        {
            "decisionStatus": "approved",
            "reviewedBy": "spoofed-user",
            "reviewed_by": "also-spoofed",
            "comment": "법령 인용 및 적용 기준 확인",
        },
        reviewer_id="principal-123",
    )

    decision = result["decision"]
    assert decision["decisionStatus"] == "approved"
    assert decision["reviewedBy"] == "principal-123"
    assert (
        decision["legalReviewProjectionId"]
        == "legal-review-version-1"
    )
    assert decision["legalReviewProjectionVersion"] == 1
    assert decision["legalReviewProjectionSha256"] == (
        "sha256:" + ("d" * 64)
    )
    assert decision["evidenceAssessment"]["eligibleForApproval"] is True
    assert decisions == [decision]
    assert len(writes) == 1
    assert writes[0]["humanReviewStatus"] == "counsel_approved"
    assert writes[0]["reviewHistory"][-1] == decision
    assert writes[0]["reviewHistory"][0]["decisionId"] == "legal-review-decision:old"
    assert original["humanReviewStatus"] == "pending"
    assert len(original["reviewHistory"]) == 1
    assert audits[0]["userId"] == "principal-123"
    assert audits[0]["metadata"]["decisionId"] == decision["decisionId"]


@pytest.mark.parametrize(
    "intelligence",
    [
        {
                "evidenceStatus": "verified",
                "verifiedReferenceCount": 0,
                "stale": False,
                "openConflicts": [],
        },
        {
                "evidenceStatus": "verified",
                "verifiedReferenceCount": 1,
                "stale": True,
                "openConflicts": [],
        },
        {
                "evidenceStatus": "verified",
                "verifiedReferenceCount": 1,
                "stale": False,
                "openConflicts": [{"field": "effectiveDate"}],
        },
        {
                "evidenceStatus": "candidate",
                "verifiedReferenceCount": 4,
                "stale": False,
                "openConflictCount": 0,
        },
    ],
)
def test_approve_fails_closed_when_evidence_is_not_approval_grade(intelligence):
    with pytest.raises(ValueError, match="gate-eligible sealed evidence snapshot"):
        decide_legal_review_use_case(
            "p1",
            "legal-review-1",
            {"decisionStatus": "approved"},
            reviewer_id="trusted-user",
            reviewer_role="legal_counsel_approver",
            review_loader=lambda *_args: [
                _review(legalIntelligence=intelligence)
            ],
            review_writer=lambda _project_id, value: value,
            decision_writer=None,
            audit_recorder=None,
        )


def test_normalized_writer_not_legacy_projection_is_approval_authority():
    forged_legacy = _review(
        legalIntelligence={
            "evidenceStatus": "candidate",
            "verifiedReferenceCount": 0,
            "verifiedCitationCount": 0,
            "requiredClaimCount": 0,
            "verifiedClaimCount": 0,
            "claimCoverageComplete": False,
            "stale": True,
            "openConflictCount": 99,
            "evidenceSnapshotId": "normalized-snapshot-1",
        }
    )
    normalized = []

    result = decide_legal_review_use_case(
        "p1",
        "legal-review-1",
        {"decisionStatus": "approved"},
        reviewer_id="trusted-user",
        reviewer_role="legal_counsel_approver",
        review_loader=lambda *_args: [forged_legacy],
        review_writer=lambda _project_id, value: value,
        decision_writer=lambda decision: normalized.append(decision),
        audit_recorder=None,
    )

    assert result["normalizedDecisionPersistence"]["persisted"] is True
    assert normalized[0]["legalEvidenceSnapshotId"] == "normalized-snapshot-1"


def test_waiver_requires_reason_but_does_not_misrepresent_itself_as_approval():
    weak_review = _review(
        legalIntelligence={
            "evidenceStatus": "candidate",
            "verifiedReferenceCount": 0,
            "stale": True,
            "openConflictCount": 2,
            "evidenceSnapshotId": "legal-evidence-snapshot-weak",
            "snapshotCreatedBy": "snapshot-maker",
        }
    )
    with pytest.raises(ValueError, match="waiverReason"):
        _run(weak_review, {"decisionStatus": "waived"})

    result, writes, _, _ = _run(
        weak_review,
        {
            "decisionStatus": "waived",
            "waiverValidUntil": "2026-08-31T00:00:00+00:00",
            "waiverConditions": [
                {
                    "code": "CURE-1",
                    "description": "Attach verified legal evidence.",
                }
            ],
            "waiverReason": "외부 법률의견서 제출을 조건으로 위원회가 예외 승인",
        },
    )
    assert result["decision"]["decisionStatus"] == "waived"
    assert result["decision"]["evidenceAssessment"]["eligibleForApproval"] is False
    assert writes[0]["humanReviewStatus"] == "waived"
    assert writes[0]["requiresHumanReview"] is False


@pytest.mark.parametrize("status", ["needs_revision", "rejected"])
def test_non_approval_dispositions_are_append_only_even_with_weak_evidence(status):
    result, writes, decisions, _ = _run(
        _review(legalIntelligence={"evidenceSnapshotId": "legal-evidence-snapshot-weak"}),
        {"reviewStatus": status, "comment": "보완 필요"},
    )
    assert result["decision"]["decisionStatus"] == status
    assert writes[0]["reviewHistory"][-1]["decisionStatus"] == status
    assert decisions[0]["decisionId"] == result["decision"]["decisionId"]


def test_decision_validation_and_lookup_are_fail_closed():
    with pytest.raises(ValueError, match="decisionStatus"):
        _run(_review(), {"decisionStatus": "accepted"})

    for invalid_reviewer in (" ", "anonymous", "system"):
        with pytest.raises(ValueError, match="authenticated principal"):
            _run(
                _review(),
                {"decisionStatus": "rejected"},
                reviewer_id=invalid_reviewer,
            )

    with pytest.raises(ValueError, match="trusted authorization context"):
        _run(
            _review(),
            {"decisionStatus": "rejected", "reviewerRole": "tenant_admin"},
            reviewer_role="untrusted_role",
        )
    with pytest.raises(ValueError, match="trusted authorization context"):
        _run(
            _review(),
            {"decisionStatus": "needs_revision"},
            reviewer_role="tenant_admin",
        )

    with pytest.raises(LookupError, match="Legal review not found"):
        decide_legal_review_use_case(
            "p1",
            "missing",
            {"decisionStatus": "rejected"},
            reviewer_id="principal",
            reviewer_role="legal_counsel_approver",
            review_loader=lambda _project_id, _limit: [],
            review_writer=lambda _project_id, value: value,
            audit_recorder=None,
        )


def test_missing_snapshot_blocks_approval_before_any_legacy_or_normalized_write():
    review = _review(
        legalIntelligence={
            "evidenceStatus": "verified",
            "verifiedReferenceCount": 1,
            "verifiedCitationCount": 1,
            "stale": False,
            "openConflictCount": 0,
        }
    )
    writes = []
    decisions = []
    with pytest.raises(ValueError, match="normalized evidence snapshot"):
        decide_legal_review_use_case(
            "p1",
            "legal-review-1",
            {"decisionStatus": "approved"},
            reviewer_id="trusted-user",
            reviewer_role="legal_counsel_approver",
            review_loader=lambda *_args: [review],
            review_writer=lambda _project_id, value: writes.append(value),
            decision_writer=lambda value: decisions.append(value),
            audit_recorder=None,
        )
    assert writes == []
    assert decisions == []


def test_final_decision_fails_closed_without_normalized_projection_binding():
    review = _review()
    review.pop("reviewVersionId")

    with pytest.raises(ValueError, match="normalized legal review projection"):
        _run(review, {"decisionStatus": "approved"})


def test_normalized_decision_shape_uses_migration_enums_and_trusted_role():
    result, _, decisions, _ = _run(
        _review(),
        {
            "decisionStatus": "approved",
            "decisionScope": "counsel_opinion",
            "reviewerRole": "tenant_admin",
            "expectedLatestDecisionId": "decision-previous",
        },
        reviewer_id="counsel-user",
    )
    decision = decisions[0]
    assert decision["decisionType"] == "approve"
    # Payload scope is ignored; this endpoint records only approval-gate scope.
    assert decision["decisionScope"] == "approval_gate"
    assert decision["reviewerId"] == "counsel-user"
    # Payload role is ignored; only the trusted kwarg may set it.
    assert decision["reviewerRole"] == "legal_counsel_approver"
    assert decision["legalEvidenceSnapshotId"] == "legal-evidence-snapshot-1"
    assert decision["expectedLatestDecisionId"] == "decision-previous"
    assert (
        decision["metadata"]["expectedLatestDecisionId"]
        == "decision-previous"
    )
    assert result["normalizedDecisionPersistence"]["persisted"] is True


def test_external_counsel_approval_projects_counsel_approved_status():
    result, writes, _, _ = _run(
        _review(),
        {
            "decisionStatus": "approved",
            "legalApprovalDelegationId": "legal-delegation-1",
        },
        reviewer_id="external-counsel-77",
        reviewer_role="external_counsel",
    )
    assert result["decision"]["reviewerRole"] == "external_counsel"
    assert (
        result["decision"]["legalApprovalDelegationId"]
        == "legal-delegation-1"
    )
    assert writes[0]["humanReviewStatus"] == "counsel_approved"


def test_final_decision_requires_dedicated_counsel_and_maker_checker():
    with pytest.raises(ValueError, match="dedicated counsel approver"):
        _run(
            _review(),
            {"decisionStatus": "approved"},
            reviewer_role="legal_reviewer",
        )

    with pytest.raises(ValueError, match="maker-checker"):
        decide_legal_review_use_case(
            "p1",
            "legal-review-1",
            {"decisionStatus": "approved"},
            reviewer_id="snapshot-maker",
            reviewer_role="legal_counsel_approver",
            review_loader=lambda *_args: [_review()],
            review_writer=lambda _project_id, value: value,
            decision_writer=None,
            audit_recorder=None,
        )

    with pytest.raises(ValueError, match="legalApprovalDelegationId"):
        _run(
            _review(),
            {"decisionStatus": "approved"},
            reviewer_id="external-counsel-1",
            reviewer_role="external_counsel",
        )


def test_legal_evidence_readiness_treats_missing_staleness_as_stale():
    assessment = legal_evidence_readiness(
        {
            "legalIntelligence": {
                "evidenceStatus": "citation_verified",
                "verifiedReferenceCount": "3",
                "verifiedCitationCount": "3",
                "openConflictCount": "0",
            }
        }
    )
    assert assessment == {
        "pass": False,
        "evidenceStatus": "citation_verified",
        "verifiedReferenceCount": 3,
        "verifiedCitationCount": 3,
        "requiredClaimCount": 0,
        "verifiedClaimCount": 0,
        "claimCoverageComplete": False,
        "evidenceSnapshotId": None,
        "snapshotGateEligible": False,
        "stale": True,
        "openConflictCount": 0,
    }


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/projects/p-route/legal-reviews/review-route/decision",
            "headers": [],
            "query_string": b"",
        }
    )


def test_router_binds_decision_to_authenticated_principal(monkeypatch):
    captured = {}
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "p-route",
        {"project": {"id": "p-route"}, "_meta": {"tenantId": "tenant-1"}},
    )
    monkeypatch.setattr(
        project_router,
        "authorize_request",
        lambda request, permission: {
            "authenticated": True,
            "role": "legal_counsel_approver",
            "user": {
                "userId": "trusted-reviewer",
                "tenantId": "tenant-1",
                "role": "legal_counsel_approver",
                "metadata": {"principalType": "human"},
            },
            "authorization": {"allowed": True, "permission": permission},
        },
    )
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda request, project: {},
    )
    monkeypatch.setattr(project_router, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        project_router,
        "legal_review_database_configured",
        lambda: True,
    )
    decision_writer = lambda value: value
    monkeypatch.setattr(
        project_router,
        "PostgresLegalTenantOverlayRepository",
        lambda **_kwargs: type(
            "_NormalizedRepository",
            (),
            {"append_review_decision": staticmethod(decision_writer)},
        )(),
    )
    monkeypatch.setattr(
        project_router,
        "decide_legal_review_use_case",
        lambda project_id, review_id, payload, **kwargs: (
            captured.update(
                {
                    "projectId": project_id,
                    "reviewId": review_id,
                    "payload": payload,
                    **kwargs,
                }
            )
            or {
                "decision": {
                    "decisionId": "decision-route",
                    "decisionStatus": "approved",
                },
                "normalizedDecisionPersistence": {"persisted": False},
            }
        ),
    )
    monkeypatch.setattr(
        project_router,
        "record_sensitive_project_event",
        lambda *args, **kwargs: None,
    )

    response = project_router.decide_project_legal_review(
        "p-route",
        "review-route",
        _request(),
        {
            "decisionStatus": "approved",
            "reviewerId": "spoofed-reviewer",
            "reviewerRole": "tenant_admin",
        },
    )

    assert response["decision"]["decisionId"] == "decision-route"
    assert captured["reviewer_id"] == "trusted-reviewer"
    assert captured["reviewer_role"] == "legal_counsel_approver"
    assert captured["decision_writer"] is decision_writer


def test_router_maps_stale_legal_decision_predecessor_to_conflict(monkeypatch):
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "p-route-conflict",
        {
            "project": {"id": "p-route-conflict"},
            "_meta": {"tenantId": "tenant-1"},
        },
    )
    monkeypatch.setattr(
        project_router,
        "authorize_request",
        lambda _request, _permission: {
            "authenticated": True,
            "role": "legal_counsel_approver",
            "user": {
                "userId": "trusted-reviewer",
                "tenantId": "tenant-1",
                "role": "legal_counsel_approver",
                "metadata": {"principalType": "human"},
            },
        },
    )
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(project_router, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        project_router,
        "legal_review_database_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        project_router,
        "PostgresLegalTenantOverlayRepository",
        lambda **_kwargs: type(
            "_Repository",
            (),
            {"append_review_decision": staticmethod(lambda _value: None)},
        )(),
    )
    monkeypatch.setattr(
        project_router,
        "decide_legal_review_use_case",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LegalReviewDecisionConflict("reload approval readiness")
        ),
    )

    with pytest.raises(HTTPException) as error:
        project_router.decide_project_legal_review(
            "p-route-conflict",
            "review-route",
            _request(),
            {
                "decisionStatus": "approved",
                "expectedLatestDecisionId": "stale-decision",
            },
        )

    assert error.value.status_code == 409
    assert (
        error.value.detail["error"]
        == "legal_review_decision_predecessor_changed"
    )


def test_router_requires_normalized_postgres_for_human_decisions(monkeypatch):
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "p-route-no-postgres",
        {
            "project": {"id": "p-route-no-postgres"},
            "_meta": {"tenantId": "tenant-1"},
        },
    )
    monkeypatch.setattr(
        project_router,
        "authorize_request",
        lambda _request, _permission: {
            "authenticated": True,
            "role": "legal_counsel_approver",
            "user": {
                "userId": "counsel-1",
                "tenantId": "tenant-1",
                "role": "legal_counsel_approver",
                "metadata": {"principalType": "human"},
            },
        },
    )
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(project_router, "postgres_configured", lambda: False)

    with pytest.raises(HTTPException) as error:
        project_router.decide_project_legal_review(
            "p-route-no-postgres",
            "review-1",
            _request(),
            {"decisionStatus": "approved"},
        )

    assert error.value.status_code == 503
    assert error.value.detail["error"] == "normalized_legal_store_required"


def test_router_rejects_permissive_anonymous_human_decision(monkeypatch):
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "p-route-anon",
        {"project": {"id": "p-route-anon"}},
    )
    monkeypatch.setattr(
        project_router,
        "authorize_request",
        lambda request, permission: {
            "authenticated": False,
            "role": "anonymous",
            "user": {"userId": "anonymous", "role": "anonymous"},
            "authorization": {
                "allowed": True,
                "reason": "legacy_identity_permissive_mode",
            },
        },
    )
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda request, project: {},
    )

    with pytest.raises(HTTPException) as error:
        project_router.decide_project_legal_review(
            "p-route-anon",
            "review-route",
            _request(),
            {"decisionStatus": "approved"},
        )
    assert error.value.status_code == 401


def test_router_does_not_promote_a_generic_financial_reviewer_to_legal_reviewer(
    monkeypatch,
):
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        "p-route-generic-reviewer",
        {
            "project": {"id": "p-route-generic-reviewer"},
            "_meta": {"tenantId": "tenant-1"},
        },
    )
    monkeypatch.setattr(
        project_router,
        "authorize_request",
        lambda request, permission: {
            "authenticated": True,
            "role": "reviewer",
            "user": {
                "userId": "financial-reviewer",
                "tenantId": "tenant-1",
                "role": "reviewer",
                "metadata": {"principalType": "human"},
            },
            "authorization": {
                "allowed": True,
                "permission": permission,
            },
        },
    )
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda request, project: {},
    )

    with pytest.raises(HTTPException) as error:
        project_router.decide_project_legal_review(
            "p-route-generic-reviewer",
            "review-route",
            _request(),
            {"decisionStatus": "approved"},
        )

    assert error.value.status_code == 403
    assert error.value.detail["error"] == "legal_reviewer_role_required"


@pytest.mark.parametrize(
    ("route", "expected_scope"),
    [
        (project_router.review_legal_and_permits, "legal_review"),
        (project_router.run_legal_pod, "legal_pod"),
    ],
)
def test_legal_analysis_mutations_reject_permissive_anonymous_callers(
    monkeypatch,
    route,
    expected_scope,
):
    project_id = f"p-anonymous-{expected_scope}"
    monkeypatch.setitem(
        project_router.PROJECTS_DB,
        project_id,
        {"project": {"id": project_id}},
    )
    monkeypatch.setattr(
        project_router,
        "authorize_request",
        lambda request, permission: {
            "authenticated": False,
            "role": "anonymous",
            "user": {"userId": "anonymous", "role": "anonymous"},
            "authorization": {
                "allowed": True,
                "permission": permission,
                "reason": "legacy_identity_permissive_mode",
            },
        },
    )
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda request, project: pytest.fail(
            "tenant scope must not run before authentication is established"
        ),
    )

    with pytest.raises(HTTPException) as error:
        route(project_id, _request(), {})

    assert error.value.status_code == 401
    assert error.value.detail["scope"] == expected_scope
