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


@pytest.fixture
def base_gate_state(monkeypatch):
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
