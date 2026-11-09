from __future__ import annotations

import pytest

import use_cases.approval_readiness_gate as gate_module


def _gate(result, code):
    return next(item for item in result["gates"] if item["code"] == code)


def _legal_review(**intelligence_overrides):
    intelligence = {
        "evidenceStatus": "verified",
        "verifiedReferenceCount": 2,
        "verifiedCitationCount": 2,
        "requiredClaimCount": 2,
        "verifiedClaimCount": 2,
        "claimCoverageComplete": True,
        "evidenceSnapshotId": "legal-evidence-snapshot-1",
        "snapshotGateEligible": True,
        "stale": False,
        "openConflictCount": 0,
    }
    intelligence.update(intelligence_overrides)
    return {
        "reviewId": "legal-1",
        "status": "clear",
        "humanReviewStatus": "approved",
        "legalIntelligence": intelligence,
        # A contradictory legacy transport flag must not affect the gate.
        "mcp": {"connected": False, "status": "not_connected"},
    }


def _normalized_readiness(**overrides):
    value = {
        "schemaVersion": "realytics-normalized-legal-readiness-v1",
        "source": "normalized_postgres",
        "readable": True,
        "pass": True,
        "reason": "ready",
        "legalReviewProjectionId": "legal-projection-1",
        "legalReviewProjectionReviewId": "legal-1",
        "legalReviewProjectionVersion": 1,
        "legalReviewProjectionSha256": "sha256:" + ("d" * 64),
        "legalReviewProjectionValid": True,
        "legalReviewProjectionSuperseded": False,
        "evidenceStatus": "verified",
        "verifiedReferenceCount": 2,
        "verifiedCitationCount": 2,
        "requiredClaimCount": 2,
        "verifiedClaimCount": 2,
        "claimCoverageComplete": True,
        "evidenceSnapshotId": "legal-evidence-snapshot-1",
        "snapshotVersion": 3,
        "snapshotSha256": "sha256:" + ("a" * 64),
        "snapshotGateEligible": True,
        "snapshotSuperseded": False,
        "stale": False,
        "openConflictCount": 0,
        "lineageValid": True,
        "latestDecisionId": "legal-decision-1",
        "latestDecisionType": "approve",
        "latestDecisionLegalReviewProjectionId": "legal-projection-1",
        "latestDecisionLegalReviewProjectionVersion": 1,
        "latestDecisionLegalReviewProjectionSha256": (
            "sha256:" + ("d" * 64)
        ),
        "latestDecisionProjectionBindingValid": True,
        "latestDecisionTimeValid": True,
        "activeBlockingMatterCount": 0,
        "waivedMatterCount": 1,
        "invalidWaiverCount": 0,
        "matterWaiverStateValid": True,
        "matterWaiverGovernanceValid": True,
        "governanceChangedAfterDecision": False,
        "latestMatterGovernanceAt": "2026-07-20T02:10:00+00:00",
        "latestWaiverGovernanceAt": "2026-07-20T02:20:00+00:00",
        "humanReviewStatus": "approved",
    }
    value.update(overrides)
    return value


@pytest.fixture
def base_gate_state(monkeypatch):
    # Legacy parsing remains testable, but runtime code always requires the
    # normalized PostgreSQL legal authority path.
    monkeypatch.setattr(
        gate_module,
        "_normalized_legal_readiness_required",
        lambda: gate_module.postgres_configured(),
    )
    monkeypatch.setattr(gate_module, "list_deal_state_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(gate_module, "list_calculation_run_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(gate_module, "list_assumption_override_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(gate_module, "list_pod_ai_review_records", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        gate_module,
        "list_permit_review_records",
        lambda *_args, **_kwargs: [
            {
                "reviewId": "permit-1",
                "status": "clear",
                "parserStatus": "verified",
                "evidenceStatus": "verified",
                "verifiedEvidenceCount": 1,
                "sourceLocators": [
                    {
                        "sourceType": "document_page",
                        "sourceDocumentId": "permit-document-1",
                        "pageNumber": 3,
                    }
                ],
            }
        ],
    )


def test_verified_legal_intelligence_passes_even_when_legacy_mcp_flag_is_false(
    monkeypatch,
    base_gate_state,
):
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [_legal_review()],
    )
    result = gate_module.build_deal_state_approval_readiness("p1")

    assert _gate(result, "KOREAN_LAW_EVIDENCE_AVAILABLE")["pass"] is True
    assert _gate(
        result,
        "LEGAL_NORMALIZED_MATTERS_AND_WAIVERS_CLEAR",
    )["pass"] is True
    assert _gate(result, "LEGAL_HUMAN_REVIEW_COMPLETE")["pass"] is True
    assert result["legalEvidenceStatus"] == "verified"
    assert result["legalEvidenceVerifiedReferenceCount"] == 2
    assert "koreanLawMcpConnected" not in result


@pytest.mark.parametrize(
    "legal_review",
    [
        _legal_review(evidenceStatus="candidate"),
        _legal_review(verifiedReferenceCount=0),
        _legal_review(verifiedCitationCount=0),
        _legal_review(verifiedClaimCount=1),
        _legal_review(claimCoverageComplete=False),
        _legal_review(evidenceSnapshotId=None),
        _legal_review(snapshotGateEligible=False),
        _legal_review(stale=True),
        _legal_review(openConflictCount=1),
    ],
)
def test_legal_evidence_gate_fails_closed_for_unverified_stale_or_conflicted_data(
    monkeypatch,
    base_gate_state,
    legal_review,
):
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [legal_review],
    )
    result = gate_module.build_deal_state_approval_readiness("p1")

    evidence_gate = _gate(result, "KOREAN_LAW_EVIDENCE_AVAILABLE")
    assert evidence_gate["pass"] is False
    assert "MCP" not in evidence_gate["message"]
    assert "verified" in evidence_gate["message"]


@pytest.mark.parametrize(
    ("status", "passes"),
    [
        ("approved", True),
        ("counsel_approved", True),
        ("pending", False),
        ("needs_revision", False),
        ("rejected", False),
        ("waived", False),
    ],
)
def test_human_review_is_an_independent_approval_gate(
    monkeypatch,
    base_gate_state,
    status,
    passes,
):
    review = _legal_review()
    review["humanReviewStatus"] = status
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [review],
    )
    result = gate_module.build_deal_state_approval_readiness("p1")

    assert _gate(result, "KOREAN_LAW_EVIDENCE_AVAILABLE")["pass"] is True
    assert _gate(result, "LEGAL_HUMAN_REVIEW_COMPLETE")["pass"] is passes
    if not passes:
        assert any(
            "authorized legal reviewer" in action
            for action in result["requiredActions"]
        )


def test_legacy_mcp_connected_cannot_substitute_for_verified_evidence(
    monkeypatch,
    base_gate_state,
):
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [
            {
                "reviewId": "legacy-only",
                "status": "clear",
                "humanReviewStatus": "approved",
                "mcp": {"connected": True, "status": "connected"},
            }
        ],
    )
    result = gate_module.build_deal_state_approval_readiness("p1")

    assert _gate(result, "KOREAN_LAW_EVIDENCE_AVAILABLE")["pass"] is False
    assert result["legalEvidenceStatus"] == "missing"
    assert result["legalEvidenceStale"] is True


@pytest.mark.parametrize(
    "status",
    ["not_run", "pending", "failed", "blocking_issue"],
)
def test_legal_pod_completion_gate_rejects_incomplete_or_failed_status(
    monkeypatch,
    base_gate_state,
    status,
):
    review = _legal_review()
    review["status"] = status
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [review],
    )

    result = gate_module.build_deal_state_approval_readiness("p1")

    assert _gate(result, "LEGAL_POD_REVIEW_COMPLETE")["pass"] is False


@pytest.mark.parametrize(
    "permit_review",
    [
        {
            "status": "clear",
            "parserStatus": "not_available",
            "evidenceStatus": "missing",
            "verifiedEvidenceCount": 0,
            "sourceLocators": [],
        },
        {
            "status": "clear",
            "parserStatus": "verified",
            "evidenceStatus": "verified",
            "verifiedEvidenceCount": 0,
            "sourceLocators": [],
        },
        {
            "status": "pending_evidence",
            "parserStatus": "verified",
            "evidenceStatus": "verified",
            "verifiedEvidenceCount": 1,
            "sourceLocators": [{"sourceDocumentId": "permit-1"}],
        },
    ],
)
def test_permit_gate_requires_verified_parser_evidence_and_locator(
    monkeypatch,
    base_gate_state,
    permit_review,
):
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [_legal_review()],
    )
    monkeypatch.setattr(
        gate_module,
        "list_permit_review_records",
        lambda *_args, **_kwargs: [permit_review],
    )

    result = gate_module.build_deal_state_approval_readiness("p1")

    assert _gate(result, "PERMIT_REVIEW_AVAILABLE")["pass"] is False


def test_configured_postgres_overrides_passing_legacy_projection(
    monkeypatch,
    base_gate_state,
):
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [_legal_review()],
    )
    monkeypatch.setattr(gate_module, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        gate_module,
        "_load_normalized_legal_readiness",
        lambda *_args, **_kwargs: _normalized_readiness(**{
            "pass": False,
            "reason": "snapshot_superseded",
            "snapshotSuperseded": True,
            "humanReviewStatus": "pending",
        }),
    )

    result = gate_module.build_deal_state_approval_readiness(
        "p1",
        tenant_id="tenant-1",
    )

    assert _gate(result, "LEGAL_NORMALIZED_POSTGRES_READABLE")["pass"] is True
    assert _gate(result, "KOREAN_LAW_EVIDENCE_AVAILABLE")["pass"] is False
    assert _gate(result, "LEGAL_HUMAN_REVIEW_COMPLETE")["pass"] is False
    assert result["legalEvidenceSource"] == "normalized_postgres"
    assert result["legalEvidenceNormalizedReason"] == "snapshot_superseded"
    assert result["legalEvidenceSnapshotSuperseded"] is True


def test_configured_postgres_uses_normalized_lineage_and_decision_not_legacy_flags(
    monkeypatch,
    base_gate_state,
):
    legacy = _legal_review(
        evidenceStatus="candidate",
        snapshotGateEligible=False,
        stale=True,
    )
    legacy["humanReviewStatus"] = "pending"
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [legacy],
    )
    monkeypatch.setattr(gate_module, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        gate_module,
        "_load_normalized_legal_readiness",
        lambda *_args, **_kwargs: _normalized_readiness(),
    )

    result = gate_module.build_deal_state_approval_readiness(
        "p1",
        tenant_id="tenant-1",
    )

    assert _gate(result, "KOREAN_LAW_EVIDENCE_AVAILABLE")["pass"] is True
    assert _gate(result, "LEGAL_HUMAN_REVIEW_COMPLETE")["pass"] is True
    assert result["legalEvidenceLineageValid"] is True
    assert result["legalEvidenceLatestDecisionType"] == "approve"
    assert result["legalReviewProjectionId"] == "legal-projection-1"
    assert result["legalReviewProjectionVersion"] == 1
    assert result["legalReviewProjectionValid"] is True
    assert (
        result["legalEvidenceLatestDecisionProjectionId"]
        == "legal-projection-1"
    )
    assert (
        result["legalEvidenceLatestDecisionProjectionBindingValid"]
        is True
    )
    assert result["legalEvidenceLatestDecisionTimeValid"] is True
    assert result["legalActiveBlockingMatterCount"] == 0
    assert result["legalInvalidWaiverCount"] == 0
    assert result["legalMatterWaiverStateValid"] is True
    assert result["legalMatterWaiverGovernanceValid"] is True


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        (
            {
                "pass": False,
                "reason": "active_blocking_legal_matter",
                "activeBlockingMatterCount": 1,
                "matterWaiverStateValid": False,
                "matterWaiverGovernanceValid": False,
            },
            "active_blocking_legal_matter",
        ),
        (
            {
                "pass": False,
                "reason": "invalid_or_expired_legal_waiver",
                "invalidWaiverCount": 1,
                "matterWaiverStateValid": False,
                "matterWaiverGovernanceValid": False,
            },
            "invalid_or_expired_legal_waiver",
        ),
        (
            {
                "pass": False,
                "reason": "legal_governance_changed_after_decision",
                "governanceChangedAfterDecision": True,
                "matterWaiverGovernanceValid": False,
            },
            "legal_governance_changed_after_decision",
        ),
    ],
)
def test_normalized_matter_and_waiver_authority_fails_closed(
    monkeypatch,
    base_gate_state,
    overrides,
    expected_reason,
):
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [_legal_review()],
    )
    monkeypatch.setattr(gate_module, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        gate_module,
        "_load_normalized_legal_readiness",
        lambda *_args, **_kwargs: _normalized_readiness(**overrides),
    )

    result = gate_module.build_deal_state_approval_readiness(
        "p1",
        tenant_id="tenant-1",
    )

    assert result["legalEvidenceNormalizedReason"] == expected_reason
    assert _gate(
        result,
        "LEGAL_NORMALIZED_MATTERS_AND_WAIVERS_CLEAR",
    )["pass"] is False
    assert any(
        "normalized legal matters" in action.lower()
        for action in result["requiredActions"]
    )


def test_configured_postgres_does_not_require_legacy_legal_projection(
    monkeypatch,
    base_gate_state,
):
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(gate_module, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        gate_module,
        "_load_normalized_legal_readiness",
        lambda *_args, **_kwargs: _normalized_readiness(),
    )

    result = gate_module.build_deal_state_approval_readiness(
        "p1",
        tenant_id="tenant-1",
    )

    assert _gate(result, "LEGAL_POD_REVIEW_COMPLETE")["pass"] is True
    assert _gate(result, "KOREAN_LAW_EVIDENCE_AVAILABLE")["pass"] is True
    assert _gate(result, "LEGAL_HUMAN_REVIEW_COMPLETE")["pass"] is True
    assert result["legalReviewStatus"] == "not_run"
    assert result["legalEvidenceSource"] == "normalized_postgres"


def test_configured_postgres_unavailability_is_an_explicit_blocking_gate(
    monkeypatch,
    base_gate_state,
):
    monkeypatch.setattr(
        gate_module,
        "list_legal_review_records",
        lambda *_args, **_kwargs: [_legal_review()],
    )
    monkeypatch.setattr(gate_module, "postgres_configured", lambda: True)
    monkeypatch.setattr(
        gate_module,
        "_load_normalized_legal_readiness",
        lambda *_args, **_kwargs: _normalized_readiness(**{
            "readable": False,
            "pass": False,
            "reason": "normalized_postgres_unavailable",
            "evidenceStatus": "missing",
            "snapshotGateEligible": False,
            "stale": True,
            "lineageValid": False,
            "latestDecisionId": None,
            "latestDecisionType": None,
            "humanReviewStatus": "pending",
        }),
    )

    result = gate_module.build_deal_state_approval_readiness(
        "p1",
        tenant_id="tenant-1",
    )

    assert _gate(result, "LEGAL_NORMALIZED_POSTGRES_READABLE")["pass"] is False
    assert _gate(result, "KOREAN_LAW_EVIDENCE_AVAILABLE")["pass"] is False
    assert any(
        "Restore normalized PostgreSQL" in action
        for action in result["requiredActions"]
    )


@pytest.mark.parametrize(
    "record",
    [
        {
            "sourceLocator": {
                "sourceType": "legal_review",
                "reviewId": "legal-review-1",
            }
        },
        {
            "sourceLocator": {
                "type": "agent_record",
                "agentRunId": "agent-run-1",
            }
        },
        {
            "recordLocator": {
                "sourceType": "legal_review_record",
                "reviewId": "legal-review-1",
            }
        },
        {"sourceLocator": {"sourceType": "document_page"}},
    ],
)
def test_record_only_or_empty_locators_never_count_as_source_evidence(record):
    assert gate_module._has_source_locator(record) is False


@pytest.mark.parametrize(
    "locator",
    [
        {
            "sourceType": "document_page",
            "sourceDocumentId": "document-1",
            "pageNumber": 8,
        },
        {"sourceType": "excel_cell", "sheet": "Summary", "cell": "B12"},
        {
            "sourceType": "official_api",
            "sourceFetchId": "legal-fetch-1",
            "exactLocator": "official:law:article-11",
        },
    ],
)
def test_explicit_evidence_coordinates_count_as_source_locators(locator):
    assert gate_module._has_source_locator({"sourceLocator": locator}) is True


def test_legal_gate_requires_explicit_evidence_locator_not_legacy_alias():
    locator = {
        "sourceType": "document_page",
        "sourceDocumentId": "legal-evidence-document-1",
        "pageNumber": 7,
    }
    assert gate_module._has_source_locator(
        {
            "recordType": "legal_evidence",
            "recordLocator": {
                "sourceType": "legal_review_record",
                "reviewId": "legal-review-1",
            },
            "sourceLocator": locator,
        }
    ) is False
    assert gate_module._has_source_locator(
        {
            "recordType": "legal_evidence",
            "recordLocator": {
                "sourceType": "legal_review_record",
                "reviewId": "legal-review-1",
            },
            "evidenceLocator": locator,
            "sourceLocator": locator,
        }
    ) is True
