from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from legal_intelligence.governance_models import (
    LegalAnalysisReviewInput,
    LegalMatterVersionInput,
    LegalWaiverCureInput,
    LegalWaiverVersionInput,
)
from routers import legal_governance


NOW = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/projects/project-1/legal/matters",
            "headers": [],
            "query_string": b"",
        }
    )


class _Repository:
    def __init__(self):
        self.calls = []

    def append_matter(self, payload, *, actor_id, actor_role):
        self.calls.append(
            ("matter", payload.matter_key, actor_id, actor_role)
        )
        return "matter-1"

    def propose_waiver(self, payload, *, actor_id):
        self.calls.append(("waiver", payload.reason, actor_id))
        return "waiver-1"

    def approve_required_claim_set(
        self,
        claim_set_id,
        *,
        actor_id,
        actor_role,
        legal_approval_delegation_id,
    ):
        self.calls.append(
            (
                "required-claim-approval",
                claim_set_id,
                actor_id,
                actor_role,
                legal_approval_delegation_id,
            )
        )
        return "required-claim-set-approved-1"

    def approve_waiver(self, *_args, **_kwargs):
        raise PermissionError("counsel required")

    def advance_waiver_cure(
        self,
        waiver_id,
        payload,
        *,
        actor_id,
        actor_role,
    ):
        self.calls.append(
            (
                "waiver-cure",
                waiver_id,
                payload.cure_status,
                payload.cure_evidence_snapshot_id,
                actor_id,
                actor_role,
            )
        )
        return "waiver-cure-1"

    def review_analysis(
        self,
        analysis_version_id,
        payload,
        *,
        actor_id,
        actor_role,
    ):
        self.calls.append(
            (
                "analysis-review",
                analysis_version_id,
                payload.review_status,
                actor_id,
                actor_role,
                payload.legal_approval_delegation_id,
            )
        )
        return "analysis-reviewed-1"


def test_matter_and_waiver_routes_bind_authenticated_actor(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(
        legal_governance,
        "_repository",
        lambda *_args, **_kwargs: (
            repository,
            "authenticated-maker",
            "tenant_admin",
        ),
    )
    matter = legal_governance.append_legal_matter(
        "project-1",
        _request(),
        LegalMatterVersionInput(
            matterKey="permit",
            title="Permit matter",
            matterType="permit",
        ),
    )
    waiver = legal_governance.propose_legal_waiver(
        "project-1",
        _request(),
        LegalWaiverVersionInput(
            projectLegalMatterId="matter-1",
            legalEvidenceSnapshotId="snapshot-1",
            reason="Time-bound cure",
            conditions=[
                {
                    "code": "CURE-1",
                    "description": "Attach counsel evidence",
                }
            ],
            validUntil=NOW + timedelta(days=30),
        ),
    )
    assert matter["projectLegalMatterId"] == "matter-1"
    assert waiver["legalWaiverId"] == "waiver-1"
    assert repository.calls == [
        (
            "matter",
            "permit",
            "authenticated-maker",
            "tenant_admin",
        ),
        ("waiver", "Time-bound cure", "authenticated-maker"),
    ]


def test_final_approval_permission_error_is_not_hidden_as_validation(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(
        legal_governance,
        "_repository",
        lambda *_args, **_kwargs: (
            repository,
            "tenant-admin",
            "admin",
        ),
    )
    with pytest.raises(HTTPException) as error:
        legal_governance.approve_legal_waiver(
            "project-1",
            "waiver-1",
            _request(),
            legal_governance._ApprovalPayload(),
        )
    assert error.value.status_code == 403
    assert error.value.detail["error"] == "legal_counsel_approver_required"


def test_analysis_review_route_binds_authenticated_counsel(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(
        legal_governance,
        "_repository",
        lambda *_args, **_kwargs: (
            repository,
            "legal-checker",
            "legal_counsel_approver",
        ),
    )
    result = legal_governance.review_legal_analysis(
        "project-1",
        "analysis-1",
        _request(),
        LegalAnalysisReviewInput(
            reviewStatus="approved",
            reason="Verified against the frozen evidence snapshot.",
            reportEligible=True,
        ),
    )
    assert result["legalAnalysisVersionId"] == "analysis-reviewed-1"
    assert repository.calls == [
        (
            "analysis-review",
            "analysis-1",
            "approved",
            "legal-checker",
            "legal_counsel_approver",
            None,
        )
    ]


def test_waiver_cure_route_binds_evidence_and_authenticated_checker(
    monkeypatch,
):
    repository = _Repository()
    monkeypatch.setattr(
        legal_governance,
        "_repository",
        lambda *_args, **_kwargs: (
            repository,
            "legal-checker",
            "legal_counsel_approver",
        ),
    )
    result = legal_governance.advance_legal_waiver_cure(
        "project-1",
        "waiver-1",
        _request(),
        LegalWaiverCureInput(
            cureStatus="cured",
            reason="Verified against the attached evidence.",
            cureEvidenceSnapshotId="snapshot-cure-1",
            conditionOutcomes=[
                {
                    "code": "CURE-1",
                    "status": "satisfied",
                    "evidenceItemIds": ["evidence-item-1"],
                }
            ],
        ),
    )
    assert result["legalWaiverId"] == "waiver-cure-1"
    assert repository.calls == [
        (
            "waiver-cure",
            "waiver-1",
            "cured",
            "snapshot-cure-1",
            "legal-checker",
            "legal_counsel_approver",
        )
    ]


def test_required_claim_approval_route_forwards_external_delegation(
    monkeypatch,
):
    repository = _Repository()
    monkeypatch.setattr(
        legal_governance,
        "_repository",
        lambda *_args, **_kwargs: (
            repository,
            "external-checker",
            "external_counsel",
        ),
    )
    result = legal_governance.approve_legal_required_claim_set(
        "project-1",
        "claim-set-1",
        _request(),
        legal_governance._DelegatedApprovalPayload(
            legalApprovalDelegationId="delegation-policy-1"
        ),
    )
    assert (
        result["legalRequiredClaimSetId"]
        == "required-claim-set-approved-1"
    )
    assert repository.calls == [
        (
            "required-claim-approval",
            "claim-set-1",
            "external-checker",
            "external_counsel",
            "delegation-policy-1",
        )
    ]


def test_waiver_and_opinion_approval_routes_forward_delegation(monkeypatch):
    class _ApprovalRepository(_Repository):
        def approve_waiver(
            self,
            waiver_id,
            *,
            actor_id,
            actor_role,
            valid_from,
            legal_approval_delegation_id,
        ):
            self.calls.append(
                (
                    "waiver-approval",
                    waiver_id,
                    actor_id,
                    actor_role,
                    valid_from,
                    legal_approval_delegation_id,
                )
            )
            return "waiver-approved-1"

        def approve_counsel_opinion(
            self,
            opinion_id,
            *,
            actor_id,
            actor_role,
            legal_approval_delegation_id,
        ):
            self.calls.append(
                (
                    "opinion-approval",
                    opinion_id,
                    actor_id,
                    actor_role,
                    legal_approval_delegation_id,
                )
            )
            return "opinion-approved-1"

    repository = _ApprovalRepository()
    monkeypatch.setattr(
        legal_governance,
        "_repository",
        lambda *_args, **_kwargs: (
            repository,
            "external-checker",
            "external_counsel",
        ),
    )
    waiver = legal_governance.approve_legal_waiver(
        "project-1",
        "waiver-1",
        _request(),
        legal_governance._ApprovalPayload(
            legalApprovalDelegationId="delegation-waiver-1"
        ),
    )
    opinion = legal_governance.approve_counsel_opinion(
        "project-1",
        "opinion-1",
        _request(),
        legal_governance._DelegatedApprovalPayload(
            legalApprovalDelegationId="delegation-opinion-1"
        ),
    )
    assert waiver["legalWaiverId"] == "waiver-approved-1"
    assert opinion["counselOpinionId"] == "opinion-approved-1"
    assert repository.calls == [
        (
            "waiver-approval",
            "waiver-1",
            "external-checker",
            "external_counsel",
            None,
            "delegation-waiver-1",
        ),
        (
            "opinion-approval",
            "opinion-1",
            "external-checker",
            "external_counsel",
            "delegation-opinion-1",
        ),
    ]


def test_analysis_route_forwards_external_delegation(monkeypatch):
    repository = _Repository()
    monkeypatch.setattr(
        legal_governance,
        "_repository",
        lambda *_args, **_kwargs: (
            repository,
            "external-checker",
            "external_counsel",
        ),
    )
    result = legal_governance.review_legal_analysis(
        "project-1",
        "analysis-1",
        _request(),
        LegalAnalysisReviewInput(
            reviewStatus="approved",
            reason="Delegated review completed.",
            reportEligible=True,
            legalApprovalDelegationId="delegation-analysis-1",
        ),
    )
    assert result["legalAnalysisVersionId"] == "analysis-reviewed-1"
    assert repository.calls[0][3:] == (
        "external-checker",
        "external_counsel",
        "delegation-analysis-1",
    )


def test_governance_routes_have_no_legacy_json_authority_fallback(
    monkeypatch,
):
    monkeypatch.setattr(
        legal_governance,
        "get_project_record",
        lambda _project_id: {
            "project": {"id": "project-1"},
            "_meta": {"tenantId": "tenant-1"},
        },
    )
    monkeypatch.setattr(
        legal_governance,
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
        legal_governance,
        "enforce_project_tenant_scope",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(legal_governance, "postgres_configured", lambda: False)

    with pytest.raises(HTTPException) as error:
        legal_governance._repository(
            "project-1",
            _request(),
            permission="legal:review",
        )
    assert error.value.status_code == 503
    assert error.value.detail["error"] == "normalized_legal_store_required"
