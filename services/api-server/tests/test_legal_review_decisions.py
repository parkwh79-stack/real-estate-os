from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import routers.projects as project_router
from use_cases.legal_review_decisions import (
    decide_legal_review_use_case,
    legal_evidence_readiness,
)


def _review(**overrides):
    value = {
        "schemaVersion": "realytics-legal-review-v2",
        "projectId": "p1",
        "reviewId": "legal-review-1",
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
            "snapshotGateEligible": True,
        },
    }
    value.update(overrides)
    return value


def _run(
    review,
    payload,
    *,
    reviewer_id="trusted-user",
    reviewer_role="legal_reviewer",
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
    assert decision["evidenceAssessment"]["eligibleForApproval"] is True
    assert decisions == [decision]
    assert len(writes) == 1
    assert writes[0]["humanReviewStatus"] == "approved"
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
        _run(
            _review(legalIntelligence=intelligence),
            {"decisionStatus": "approved"},
        )


def test_waiver_requires_reason_but_does_not_misrepresent_itself_as_approval():
    weak_review = _review(
        legalIntelligence={
            "evidenceStatus": "candidate",
            "verifiedReferenceCount": 0,
            "stale": True,
            "openConflictCount": 2,
            "evidenceSnapshotId": "legal-evidence-snapshot-weak",
        }
    )
    with pytest.raises(ValueError, match="waiverReason"):
        _run(weak_review, {"decisionStatus": "waived"})

    result, writes, _, _ = _run(
        weak_review,
        {
            "decisionStatus": "waived",
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

    with pytest.raises(LookupError, match="Legal review not found"):
        decide_legal_review_use_case(
            "p1",
            "missing",
            {"decisionStatus": "rejected"},
            reviewer_id="principal",
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
    with pytest.raises(ValueError, match="sealed evidence snapshot"):
        decide_legal_review_use_case(
            "p1",
            "legal-review-1",
            {"decisionStatus": "approved"},
            reviewer_id="trusted-user",
            review_loader=lambda *_args: [review],
            review_writer=lambda _project_id, value: writes.append(value),
            decision_writer=lambda value: decisions.append(value),
            audit_recorder=None,
        )
    assert writes == []
    assert decisions == []


def test_normalized_decision_shape_uses_migration_enums_and_trusted_role():
    result, _, decisions, _ = _run(
        _review(),
        {
            "decisionStatus": "approved",
            "decisionScope": "counsel_opinion",
            "reviewerRole": "tenant_admin",
        },
        reviewer_id="counsel-user",
    )
    decision = decisions[0]
    assert decision["decisionType"] == "approve"
    # Payload scope is ignored; this endpoint records only approval-gate scope.
    assert decision["decisionScope"] == "approval_gate"
    assert decision["reviewerId"] == "counsel-user"
    # Payload role is ignored; only the trusted kwarg may set it.
    assert decision["reviewerRole"] == "legal_reviewer"
    assert decision["legalEvidenceSnapshotId"] == "legal-evidence-snapshot-1"
    assert result["normalizedDecisionPersistence"]["persisted"] is True


def test_external_counsel_approval_projects_counsel_approved_status():
    result, writes, _, _ = _run(
        _review(),
        {"decisionStatus": "approved"},
        reviewer_id="external-counsel-77",
        reviewer_role="external_counsel",
    )
    assert result["decision"]["reviewerRole"] == "external_counsel"
    assert writes[0]["humanReviewStatus"] == "counsel_approved"


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
            "role": "legal_reviewer",
            "user": {
                "userId": "trusted-reviewer",
                "tenantId": "tenant-1",
                "role": "legal_reviewer",
            },
            "authorization": {"allowed": True, "permission": permission},
        },
    )
    monkeypatch.setattr(
        project_router,
        "enforce_project_tenant_scope",
        lambda request, project: {},
    )
    monkeypatch.setattr(project_router, "postgres_configured", lambda: False)
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
    assert captured["reviewer_role"] == "legal_reviewer"
    assert captured["decision_writer"] is None


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
