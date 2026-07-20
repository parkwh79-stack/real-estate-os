from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from db.repositories.legal_intelligence_repository import (
    PostgresLegalTenantOverlayRepository,
)


SNAPSHOT_HASH = "a" * 64
DECIDED_AT = datetime(2026, 7, 20, 2, 30, tzinfo=timezone.utc)


def _ready_row(**overrides):
    values = {
        "snapshot_id": "snapshot-1",
        "snapshot_version": 3,
        "snapshot_hash": SNAPSHOT_HASH,
        "as_of_date": date(2026, 7, 20),
        "citation_status": "verified",
        "has_open_conflicts": False,
        "is_stale": False,
        "gate_eligible": True,
        "snapshot_metadata": {
            "legalReviewId": "legal-review-1",
            "coverageComplete": True,
            "expectedCitationCount": 2,
            "sealedCitationCount": 2,
            "claimCoverageComplete": True,
            "expectedClaimCount": 2,
            "verifiedClaimCount": 2,
        },
        "stored_legal_review_id": "legal-review-1",
        "snapshot_created_by": "snapshot-maker-1",
        "snapshot_created_at": datetime(
            2026,
            7,
            20,
            1,
            30,
            tzinfo=timezone.utc,
        ),
        "snapshot_superseded": False,
        "item_count": 2,
        "citation_count": 2,
        "claim_count": 2,
        "invalid_lineage_count": 0,
        "required_claim_set_id": "required-claims-1",
        "snapshot_policy_sha256": "c" * 64,
        "required_claim_set_version": 2,
        "required_claim_set_sha256": "c" * 64,
        "computed_claim_set_sha256": "c" * 64,
        "required_claim_count": 2,
        "covered_claim_count": 2,
        "required_claim_policy_active": True,
        "projection_version_id": "legal-projection-1",
        "projection_review_id": "legal-review-1",
        "projection_review_version": 1,
        "projection_snapshot_id": "snapshot-1",
        "projection_snapshot_version": 3,
        "projection_snapshot_sha256": SNAPSHOT_HASH,
        "projection_snapshot_gate_eligible": True,
        "projection_required_claim_set_id": "required-claims-1",
        "projection_required_claim_set_sha256": "c" * 64,
        "projection_hash_contract": "pg-jsonb-row-v1",
        "projection_sha256": "d" * 64,
        "computed_projection_sha256": "d" * 64,
        "projection_analysis_only": True,
        "projection_report_eligible": False,
        "projection_superseded": False,
        "projection_created_at": datetime(
            2026,
            7,
            20,
            2,
            0,
            tzinfo=timezone.utc,
        ),
        "decision_id": "decision-1",
        "decision_chain_version": 1,
        "supersedes_decision_id": None,
        "expected_predecessor_decision_id": None,
        "decision_snapshot_id": "snapshot-1",
        "decision_type": "approve",
        "decision_projection_id": "legal-projection-1",
        "decision_projection_version": 1,
        "decision_projection_sha256": "d" * 64,
        "reviewer_id": "counsel-reviewer-1",
        "reviewer_role": "external_counsel",
        "legal_approval_delegation_id": "delegation-1",
        "delegation_valid_at_decision": True,
        "decided_at": DECIDED_AT,
        "decision_recorded_at": DECIDED_AT,
        "readiness_evaluated_at": datetime(
            2026,
            7,
            20,
            3,
            0,
            tzinfo=timezone.utc,
        ),
        "decision_metadata": {
            "legalReviewId": "legal-review-1",
            "evidenceSnapshotVersion": 3,
            "evidenceSnapshotSha256": f"sha256:{SNAPSHOT_HASH}",
        },
        "active_blocking_matter_count": 0,
        "waived_matter_count": 1,
        "invalid_waiver_count": 0,
        "latest_matter_event_at": datetime(
            2026,
            7,
            20,
            2,
            10,
            tzinfo=timezone.utc,
        ),
        "latest_waiver_event_at": datetime(
            2026,
            7,
            20,
            2,
            20,
            tzinfo=timezone.utc,
        ),
        "governance_changed_after_decision": False,
    }
    values.update(overrides)
    return (
        values["snapshot_id"],
        values["snapshot_version"],
        values["snapshot_hash"],
        values["as_of_date"],
        values["citation_status"],
        values["has_open_conflicts"],
        values["is_stale"],
        values["gate_eligible"],
        values["snapshot_metadata"],
        values["stored_legal_review_id"],
        values["snapshot_created_by"],
        values["snapshot_created_at"],
        values["snapshot_superseded"],
        values["item_count"],
        values["citation_count"],
        values["claim_count"],
        values["invalid_lineage_count"],
        values["required_claim_set_id"],
        values["snapshot_policy_sha256"],
        values["required_claim_set_version"],
        values["required_claim_set_sha256"],
        values["computed_claim_set_sha256"],
        values["required_claim_count"],
        values["covered_claim_count"],
        values["required_claim_policy_active"],
        values["projection_version_id"],
        values["projection_review_id"],
        values["projection_review_version"],
        values["projection_snapshot_id"],
        values["projection_snapshot_version"],
        values["projection_snapshot_sha256"],
        values["projection_snapshot_gate_eligible"],
        values["projection_required_claim_set_id"],
        values["projection_required_claim_set_sha256"],
        values["projection_hash_contract"],
        values["projection_sha256"],
        values["computed_projection_sha256"],
        values["projection_analysis_only"],
        values["projection_report_eligible"],
        values["projection_superseded"],
        values["projection_created_at"],
        values["decision_id"],
        values["decision_chain_version"],
        values["supersedes_decision_id"],
        values["expected_predecessor_decision_id"],
        values["decision_snapshot_id"],
        values["decision_type"],
        values["decision_projection_id"],
        values["decision_projection_version"],
        values["decision_projection_sha256"],
        values["reviewer_id"],
        values["reviewer_role"],
        values["legal_approval_delegation_id"],
        values["delegation_valid_at_decision"],
        values["decided_at"],
        values["decision_recorded_at"],
        values["readiness_evaluated_at"],
        values["decision_metadata"],
        values["active_blocking_matter_count"],
        values["waived_matter_count"],
        values["invalid_waiver_count"],
        values["latest_matter_event_at"],
        values["latest_waiver_event_at"],
        values["governance_changed_after_decision"],
    )


class _ReadinessCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        values = tuple(params or ())
        self.connection.statements.append((sql, values))
        if sql.startswith("WITH selected_snapshot AS"):
            self.row = self.connection.row
        elif sql.startswith("SELECT snapshot.legal_evidence_snapshot_id"):
            self.row = self.connection.identity_row
        else:
            self.row = None

    def fetchone(self):
        return self.row


class _ReadinessConnection:
    def __init__(self, row, *, identity_row=None):
        self.row = row
        self.identity_row = identity_row
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return _ReadinessCursor(self)


def _repository(connection):
    return PostgresLegalTenantOverlayRepository(
        tenant_id="tenant-1",
        project_id="project-1",
        connection_factory=lambda: connection,
    )


def _evaluate(connection):
    return _repository(connection).evaluate_approval_readiness(
        legal_review_id="legal-review-1",
        evidence_snapshot_id="snapshot-1",
        expected_snapshot_version=3,
        expected_snapshot_sha256=f"sha256:{SNAPSHOT_HASH}",
    )


def test_normalized_readiness_rechecks_scope_coverage_lineage_and_latest_decision():
    connection = _ReadinessConnection(_ready_row())

    result = _evaluate(connection)

    assert result["pass"] is True
    assert result["reason"] == "ready"
    assert result["source"] == "normalized_postgres"
    assert result["verifiedCitationCount"] == 2
    assert result["requiredClaimCount"] == 2
    assert result["claimCoverageComplete"] is True
    assert result["requiredClaimPolicyValid"] is True
    assert result["requiredClaimSetId"] == "required-claims-1"
    assert result["lineageValid"] is True
    assert result["legalReviewProjectionId"] == "legal-projection-1"
    assert result["legalReviewProjectionValid"] is True
    assert result["legalReviewProjectionHashContract"] == (
        "pg-jsonb-row-v1"
    )
    assert result["legalReviewProjectionHashRecomputed"] is True
    assert result["legalReviewProjectionSha256"] == (
        "sha256:" + ("d" * 64)
    )
    assert result["humanReviewStatus"] == "counsel_approved"
    assert result["latestDecisionDelegationValid"] is True
    assert result["latestDecisionProjectionBindingValid"] is True
    assert result["latestDecisionTimeValid"] is True
    assert result["latestDecisionVersion"] == 1
    assert result["latestDecisionSupersedesId"] is None
    assert result["latestDecisionChainValid"] is True
    assert result["activeBlockingMatterCount"] == 0
    assert result["waivedMatterCount"] == 1
    assert result["invalidWaiverCount"] == 0
    assert result["matterWaiverStateValid"] is True
    assert result["matterWaiverGovernanceValid"] is True
    assert result["governanceChangedAfterDecision"] is False
    assert (
        result["latestDecisionLegalReviewProjectionId"]
        == "legal-projection-1"
    )
    readiness_sql, params = next(
        item
        for item in connection.statements
        if item[0].startswith("WITH selected_snapshot AS")
    )
    assert params == (
        "snapshot-1",
        "tenant-1",
        "project-1",
        "tenant-1",
        "project-1",
        "legal-review-1",
    )
    assert "legal_evidence_snapshot_items" in readiness_sql
    assert "legal_resource_versions" in readiness_sql
    assert "legal_resources" in readiness_sql
    assert "legal_resource_version_lineages" in readiness_sql
    assert "legal_source_fetches" in readiness_sql
    assert "legal_provisions" in readiness_sql
    assert "source_fetch.fetched_at > statement_timestamp()" in readiness_sql
    assert "lineage.validated_at > statement_timestamp()" in readiness_sql
    assert "provision.provision_sha256 IS DISTINCT FROM encode" in (
        readiness_sql
    )
    assert "legal_review_decisions" in readiness_sql
    assert "decision.decision_version IS NOT NULL" in readiness_sql
    assert "successor.supersedes_legal_review_decision_id" in readiness_sql
    assert "ORDER BY decision.decision_version DESC" in readiness_sql
    assert "projection.projection_hash_contract" in readiness_sql
    assert "to_jsonb(projection)" in readiness_sql
    assert "extensions.digest" in readiness_sql
    assert "legal_required_claim_sets" in readiness_sql
    assert "legal_required_claims" in readiness_sql
    assert "project_legal_review_versions" in readiness_sql
    assert "project_legal_matters" in readiness_sql
    assert "legal_waivers" in readiness_sql
    assert "candidate.recorded_at DESC" in readiness_sql
    assert "legal_try_timestamptz" in readiness_sql
    assert "DISTINCT condition_entry ->> 'code'" in readiness_sql
    assert "outcome_entry ->> 'status' = 'satisfied'" in readiness_sql
    assert "failed_outcome ->> 'status' = 'failed'" in readiness_sql
    assert "satisfied_outcome -> 'evidenceItemIds'" in readiness_sql
    assert "AS evidence_item(evidence_item_id)" in readiness_sql
    assert "cure_item.legal_evidence_item_id =" in readiness_sql
    assert "waiver.cure_evidence_snapshot_id" in readiness_sql
    assert "cure_item" in readiness_sql
    assert ".citation_verification_status =" in readiness_sql
    assert "waiver.valid_until > statement_timestamp()" in readiness_sql
    assert "waiver.legal_evidence_snapshot_id IS DISTINCT FROM" in (
        readiness_sql
    )
    assert "invalid_terminal_cure_count" in readiness_sql
    assert "resolved_successor.status = 'resolved'" in readiness_sql
    assert "resolved_successor.created_at >= waiver.recorded_at" in (
        readiness_sql
    )
    assert "governance.latest_matter_event_at > decision.decided_at" in (
        readiness_sql
    )
    assert "successor.supersedes_review_version_id" in readiness_sql
    assert "replacement.supersedes_version_id" in readiness_sql
    assert "replacement_fetch.fetch_status IN" in readiness_sql
    assert "replacement_provider.provider_status = 'active'" in readiness_sql
    assert "item.verified_at > statement_timestamp()" in readiness_sql
    assert "item.verified_at + INTERVAL '7 days'" in readiness_sql
    assert "snapshot.freshness_policy_version <>" in readiness_sql
    assert "version.verified_at > statement_timestamp()" in readiness_sql
    assert "decision.legal_review_id = %s" in readiness_sql
    assert "delegation.scope = decision.decision_scope" in readiness_sql
    assert "delegation.created_at <= decision.decided_at" in readiness_sql
    assert "delegation.scope IN" not in readiness_sql


def test_normalized_readiness_ignores_legacy_projection_counts_and_booleans():
    row = _ready_row(
        snapshot_metadata={
            "legalReviewId": "forged-legacy-review",
            "coverageComplete": False,
            "expectedCitationCount": 999,
            "sealedCitationCount": 0,
            "expectedClaimCount": 999,
            "verifiedClaimCount": 0,
            "claimCoverageComplete": False,
        }
    )

    result = _evaluate(_ReadinessConnection(row))

    assert result["pass"] is True
    assert result["requiredClaimCount"] == 2
    assert result["verifiedClaimCount"] == 2


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (_ready_row(snapshot_version=4), "snapshot_identity_mismatch"),
        (
            _ready_row(projection_version_id=None),
            "normalized_legal_review_projection_missing",
        ),
        (
            _ready_row(projection_sha256="not-a-hash"),
            "normalized_legal_review_projection_hash_invalid",
        ),
        (
            _ready_row(projection_hash_contract=None),
            "normalized_legal_review_projection_hash_invalid",
        ),
        (
            _ready_row(computed_projection_sha256="e" * 64),
            "normalized_legal_review_projection_hash_invalid",
        ),
        (
            _ready_row(projection_snapshot_version=2),
            "normalized_legal_review_projection_snapshot_mismatch",
        ),
        (
            _ready_row(projection_superseded=True),
            "normalized_legal_review_projection_snapshot_mismatch",
        ),
        (_ready_row(snapshot_superseded=True), "snapshot_superseded"),
        (_ready_row(invalid_lineage_count=1), "corpus_lineage_invalid"),
        (
            _ready_row(covered_claim_count=1),
            "required_claim_coverage_incomplete",
        ),
        (
            _ready_row(required_claim_policy_active=False),
            "required_claim_policy_invalid",
        ),
        (
            _ready_row(computed_claim_set_sha256="d" * 64),
            "required_claim_policy_invalid",
        ),
        (
            _ready_row(decision_type="request_changes"),
            "latest_review_decision_not_approved",
        ),
        (
            _ready_row(decision_chain_version=None),
            "latest_review_decision_time_invalid",
        ),
        (
            _ready_row(
                decision_chain_version=2,
                supersedes_decision_id=None,
            ),
            "latest_review_decision_time_invalid",
        ),
        (
            _ready_row(reviewer_role="legal_reviewer"),
            "latest_review_decision_not_approved",
        ),
        (
            _ready_row(delegation_valid_at_decision=False),
            "latest_review_decision_not_approved",
        ),
        (
            _ready_row(decision_projection_id="legal-projection-old"),
            "latest_review_decision_projection_mismatch",
        ),
        (
            _ready_row(decision_projection_version=0),
            "latest_review_decision_projection_mismatch",
        ),
        (
            _ready_row(decision_projection_sha256="e" * 64),
            "latest_review_decision_projection_mismatch",
        ),
        (
            _ready_row(
                projection_created_at=datetime(
                    2026,
                    7,
                    20,
                    3,
                    0,
                    tzinfo=timezone.utc,
                )
            ),
            "latest_review_decision_time_invalid",
        ),
        (
            _ready_row(
                snapshot_created_at=datetime(
                    2026,
                    7,
                    20,
                    3,
                    0,
                    tzinfo=timezone.utc,
                )
            ),
            "latest_review_decision_time_invalid",
        ),
        (
            _ready_row(
                decided_at=datetime(
                    2026,
                    7,
                    20,
                    3,
                    1,
                    tzinfo=timezone.utc,
                ),
                readiness_evaluated_at=datetime(
                    2026,
                    7,
                    20,
                    3,
                    0,
                    tzinfo=timezone.utc,
                ),
            ),
            "latest_review_decision_time_invalid",
        ),
        (
            _ready_row(reviewer_id="snapshot-maker-1"),
            "latest_review_decision_not_approved",
        ),
        (
            _ready_row(active_blocking_matter_count=1),
            "active_blocking_legal_matter",
        ),
        (
            _ready_row(invalid_waiver_count=1),
            "invalid_or_expired_legal_waiver",
        ),
        (
            _ready_row(governance_changed_after_decision=True),
            "legal_governance_changed_after_decision",
        ),
        (
            _ready_row(
                decision_metadata={
                    "legalReviewId": "legal-review-1",
                    "evidenceSnapshotVersion": 3,
                    "evidenceSnapshotSha256": "sha256:" + ("b" * 64),
                }
            ),
            "latest_review_decision_not_approved",
        ),
    ],
)
def test_normalized_readiness_fails_closed_when_authoritative_state_changes(
    row,
    reason,
):
    result = _evaluate(_ReadinessConnection(row))

    assert result["pass"] is False
    assert result["reason"] == reason


def test_normalized_readiness_does_not_accept_a_snapshot_outside_rls_scope():
    result = _evaluate(_ReadinessConnection(None))

    assert result["pass"] is False
    assert result["reason"] == "snapshot_scope_or_identity_mismatch"
    assert result["evidenceStatus"] == "missing"
    assert result["humanReviewStatus"] == "pending"


def test_latest_normalized_readiness_selects_identity_without_legacy_projection():
    connection = _ReadinessConnection(
        _ready_row(),
        identity_row=(
            "snapshot-1",
            3,
            SNAPSHOT_HASH,
            "legal-review-1",
        ),
    )

    result = _repository(connection).evaluate_latest_approval_readiness()

    assert result["pass"] is True
    identity_sql, params = next(
        item
        for item in connection.statements
        if item[0].startswith(
            "SELECT snapshot.legal_evidence_snapshot_id"
        )
    )
    assert params == ("tenant-1", "project-1")
    assert "ORDER BY snapshot.snapshot_version DESC" in identity_sql


def test_latest_normalized_readiness_rejects_missing_snapshot_metadata_identity():
    connection = _ReadinessConnection(
        _ready_row(),
        identity_row=("snapshot-1", 3, SNAPSHOT_HASH, None),
    )

    result = _repository(connection).evaluate_latest_approval_readiness()

    assert result["pass"] is False
    assert result["reason"] == "normalized_snapshot_contract_invalid"
