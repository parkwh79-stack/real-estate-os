from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from db.repositories.legal_governance_repository import (
    PostgresLegalGovernanceRepository,
)
from legal_intelligence.governance_models import (
    CounselOpinionVersionInput,
    LegalAnalysisReviewInput,
    LegalAnalysisVersionInput,
    LegalApprovalDelegationInput,
    LegalApprovalDelegationRevokeInput,
    LegalMatterVersionInput,
    LegalRequiredClaimSetInput,
    LegalWaiverCureInput,
    LegalWaiverVersionInput,
    TenantLegalTermAliasReviewInput,
)


NOW = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
SHA = "a" * 64


class _Cursor:
    def __init__(
        self,
        *,
        source_row=None,
        alias_source_row=None,
        delegation_source_row=None,
        delegation_authority_row=None,
        evidence_snapshot_row=None,
        required_claim_source_row=None,
        opinion_predecessor_row=None,
        analysis_predecessor_row=None,
        matter_leaf_row=None,
        waiver_binding_row=None,
        evidence_item_binding_row=None,
    ):
        self.calls = []
        self.row = None
        self.source_row = source_row
        self.alias_source_row = alias_source_row
        self.delegation_source_row = delegation_source_row
        self.delegation_authority_row = delegation_authority_row
        self.evidence_snapshot_row = evidence_snapshot_row
        self.required_claim_source_row = required_claim_source_row
        self.opinion_predecessor_row = opinion_predecessor_row
        self.analysis_predecessor_row = analysis_predecessor_row
        self.matter_leaf_row = matter_leaf_row
        self.waiver_binding_row = waiver_binding_row
        self.evidence_item_binding_row = evidence_item_binding_row

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        if params is not None:
            assert normalized.count("%s") == len(params), normalized
        self.calls.append((normalized, params))
        if normalized.startswith("SELECT matter.project_legal_matter_id"):
            self.row = self.matter_leaf_row
        elif normalized.startswith("SELECT waiver.legal_waiver_id"):
            self.row = self.waiver_binding_row
        elif normalized.startswith(
            "SELECT COUNT(DISTINCT requested.legal_evidence_item_id)"
        ):
            self.row = self.evidence_item_binding_row
        elif normalized.startswith("SELECT project_legal_matter_id"):
            self.row = self.source_row
        elif normalized.startswith("SELECT term_id"):
            self.row = self.alias_source_row
        elif normalized.startswith("SELECT delegation_key"):
            self.row = self.delegation_source_row
        elif normalized.startswith(
            "SELECT delegation.legal_approval_delegation_id"
        ):
            self.row = self.delegation_authority_row
        elif normalized.startswith(
            "SELECT snapshot.legal_evidence_snapshot_id"
        ):
            self.row = self.evidence_snapshot_row
        elif normalized.startswith("SELECT claim_set.claim_set_key"):
            self.row = self.required_claim_source_row
        elif normalized.startswith("SELECT source.opinion_version"):
            self.row = self.opinion_predecessor_row
        elif normalized.startswith("SELECT source.analysis_version"):
            self.row = self.analysis_predecessor_row
        elif "RETURNING project_legal_matter_id" in normalized:
            self.row = ("legal-matter:new",)
        elif "RETURNING legal_waiver_id" in normalized:
            self.row = ("legal-waiver:new",)
        elif "RETURNING counsel_opinion_id" in normalized:
            self.row = ("counsel-opinion:new",)
        elif "RETURNING legal_analysis_version_id" in normalized:
            self.row = ("legal-analysis:new",)
        elif "RETURNING tenant_legal_term_alias_review_event_id" in normalized:
            self.row = ("tenant-legal-term-alias-review:new",)
        elif "RETURNING tenant_legal_term_alias_id" in normalized:
            self.row = ("tenant-legal-term-alias:new",)
        elif "RETURNING legal_approval_delegation_id" in normalized:
            self.row = ("legal-approval-delegation:new",)
        elif "RETURNING legal_required_claim_set_id" in normalized:
            self.row = ("legal-required-claim-set:new",)
        else:
            self.row = None

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

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


def _repository(cursor):
    return PostgresLegalGovernanceRepository(
        tenant_id="tenant-1",
        project_id="project-1",
        connection_factory=lambda: _Connection(cursor),
        schema_name="realytics",
        now=lambda: NOW,
    )


def test_matter_write_is_project_bound_and_sets_fail_closed_tenant_guc():
    cursor = _Cursor()
    result = _repository(cursor).append_matter(
        LegalMatterVersionInput(
            matterKey="permit-risk",
            title="Permit review",
            matterType="permit",
        ),
        actor_id="legal-maker",
        actor_role="tenant_admin",
    )
    assert result == "legal-matter:new"
    assert cursor.calls[0][0] == (
        'SET LOCAL search_path TO "realytics", pg_catalog'
    )
    assert cursor.calls[1] == (
        "SELECT set_config('realytics.tenant_id', %s, true)",
        ("tenant-1",),
    )
    insert = next(
        call for call in cursor.calls if "INSERT INTO project_legal_matters" in call[0]
    )
    assert insert[1][1:3] == ("tenant-1", "project-1")
    assert insert[1][14] == "legal-maker"
    assert insert[1][4] == 1
    assert insert[1][13] is None
    assert "statement_timestamp()" in insert[0]
    leaf_query = next(
        sql
        for sql, _params in cursor.calls
        if "FROM project_legal_matters AS matter" in sql
    )
    assert "NOT EXISTS" in leaf_query
    assert "FOR UPDATE OF matter" in leaf_query


def test_matter_successor_locks_leaf_and_derives_version_on_server():
    opened_at = NOW - timedelta(days=2)
    cursor = _Cursor(
        matter_leaf_row=(
            "matter-1",
            4,
            opened_at,
            "permit",
            "matter-editor",
            "legal-maker",
        ),
        evidence_snapshot_row=("snapshot-1",),
    )

    result = _repository(cursor).append_matter(
        LegalMatterVersionInput(
            matterKey="permit-risk",
            matterVersion=5,
            title="Permit review resolved",
            matterType="permit",
            status="resolved",
            openedAt=NOW,
            closedAt=NOW,
            supersedesMatterId="matter-1",
            legalEvidenceSnapshotId="snapshot-1",
            resolutionReason="Permit conditions verified.",
        ),
        actor_id="legal-checker",
        actor_role="legal_counsel_approver",
    )

    assert result == "legal-matter:new"
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO project_legal_matters" in call[0]
    )
    assert insert[1][4] == 5
    assert insert[1][11] == opened_at
    assert insert[1][13] == "matter-1"
    assert insert[1][14] == "legal-checker"
    advisory = next(
        call
        for call in cursor.calls
        if "pg_advisory_xact_lock" in call[0]
    )
    assert advisory[1] == (
        "project-legal-matter:tenant-1:project-1:permit-risk",
    )


def test_matter_successor_rejects_a_non_leaf_or_client_version_race():
    source = (
        "matter-current",
        4,
        NOW - timedelta(days=2),
        "permit",
        "matter-editor",
        "matter-root-maker",
    )
    with pytest.raises(LookupError, match="current leaf"):
        _repository(_Cursor(matter_leaf_row=source)).append_matter(
            LegalMatterVersionInput(
                matterKey="permit-risk",
                title="stale update",
                matterType="permit",
                supersedesMatterId="matter-old",
            ),
            actor_id="legal-maker",
            actor_role="tenant_admin",
        )

    with pytest.raises(ValueError, match="server-derived"):
        _repository(_Cursor(matter_leaf_row=source)).append_matter(
            LegalMatterVersionInput(
                matterKey="permit-risk",
                matterVersion=6,
                title="wrong version",
                matterType="permit",
                supersedesMatterId="matter-current",
            ),
            actor_id="legal-maker",
            actor_role="tenant_admin",
        )

    with pytest.raises(ValueError, match="root version"):
        _repository(_Cursor()).append_matter(
            LegalMatterVersionInput(
                matterKey="new-risk",
                matterVersion=2,
                title="forged root version",
                matterType="permit",
            ),
            actor_id="legal-maker",
            actor_role="tenant_admin",
        )


def test_terminal_matter_requires_counsel_evidence_and_maker_checker():
    value = LegalMatterVersionInput(
        matterKey="permit-risk",
        title="Permit review resolved",
        matterType="permit",
        status="resolved",
        closedAt=NOW,
        supersedesMatterId="matter-1",
        legalEvidenceSnapshotId="snapshot-1",
        resolutionReason="Verified permit evidence closes the issue.",
    )
    leaf = (
        "matter-1",
        1,
        NOW - timedelta(days=2),
        "permit",
        "matter-editor",
        "matter-maker",
    )
    with pytest.raises(PermissionError):
        _repository(_Cursor(matter_leaf_row=leaf)).append_matter(
            value,
            actor_id="tenant-admin",
            actor_role="tenant_admin",
        )
    with pytest.raises(ValueError, match="maker-checker"):
        _repository(_Cursor(matter_leaf_row=leaf)).append_matter(
            value,
            actor_id="matter-maker",
            actor_role="legal_counsel_approver",
        )


def test_waived_matter_rebinds_current_approved_waiver_to_new_leaf():
    cursor = _Cursor(
        matter_leaf_row=(
            "matter-1",
            2,
            NOW - timedelta(days=2),
            "permit",
            "matter-editor",
            "matter-maker",
        ),
        evidence_snapshot_row=("snapshot-1",),
        waiver_binding_row=("waiver-approved-1",),
    )
    result = _repository(cursor).append_matter(
        LegalMatterVersionInput(
            matterKey="permit-risk",
            title="Permit risk conditionally waived",
            matterType="permit",
            status="waived",
            supersedesMatterId="matter-1",
            legalEvidenceSnapshotId="snapshot-1",
            resolutionReason="Time-bound counsel waiver approved.",
            legalWaiverId="waiver-approved-1",
        ),
        actor_id="legal-checker",
        actor_role="legal_counsel_approver",
    )
    assert result == "legal-matter:new"
    waiver_lookup = next(
        call
        for call in cursor.calls
        if call[0].startswith("SELECT waiver.legal_waiver_id")
    )
    assert waiver_lookup[1][:5] == (
        "waiver-approved-1",
        "tenant-1",
        "project-1",
        "matter-1",
        "snapshot-1",
    )
    matter_insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO project_legal_matters" in call[0]
    )
    assert matter_insert[1][15:22] == (
        "snapshot-1",
        "Time-bound counsel waiver approved.",
        "legal-checker",
        "legal_counsel_approver",
        None,
        NOW,
        matter_insert[1][21],
    )
    binding_insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_waivers" in call[0]
    )
    assert binding_insert[1][1:4] == (
        "legal-matter:new",
        "legal-matter:new",
        "waiver-approved-1",
    )
    assert "'matter_binding'" in binding_insert[0]


def test_waiver_contract_requires_conditions_and_a_future_period():
    with pytest.raises(ValidationError):
        LegalWaiverVersionInput(
            projectLegalMatterId="matter-1",
            legalEvidenceSnapshotId="snapshot-1",
            reason="temporary exception",
            conditions=[],
            validUntil=NOW + timedelta(days=10),
        )

    cursor = _Cursor()
    waiver = LegalWaiverVersionInput(
        projectLegalMatterId="matter-1",
        legalEvidenceSnapshotId="snapshot-1",
        reason="temporary exception",
        conditions=[
            {
                "code": "CURE-1",
                "description": "Attach a verified permit opinion",
            }
        ],
        validUntil=NOW + timedelta(days=10),
    )
    assert (
        _repository(cursor).propose_waiver(
            waiver,
            actor_id="tenant-admin",
        )
        == "legal-waiver:new"
    )
    with pytest.raises(ValueError, match="cureStatus=not_started"):
        _repository(_Cursor()).propose_waiver(
            waiver.model_copy(update={"cure_status": "in_progress"}),
            actor_id="tenant-admin",
        )
    for forged_proposal in (
        waiver.model_copy(update={"waiver_version": 2}),
        waiver.model_copy(
            update={"supersedes_waiver_id": "legal-waiver:predecessor"}
        ),
    ):
        with pytest.raises(ValueError, match="version 1"):
            _repository(_Cursor()).propose_waiver(
                forged_proposal,
                actor_id="tenant-admin",
            )


@pytest.mark.parametrize(
    ("status", "cure_status"),
    (
        ("proposed", "in_progress"),
        ("approved", "verified"),
        ("cured", "failed"),
        ("revoked", "verified"),
        ("rejected", "not_started"),
        ("expired", "in_progress"),
        ("superseded", "verified"),
    ),
)
def test_waiver_contract_rejects_noncanonical_status_cure_pairs(
    status: str,
    cure_status: str,
) -> None:
    with pytest.raises(
        ValidationError,
        match="status and cureStatus",
    ):
        LegalWaiverVersionInput(
            projectLegalMatterId="matter-1",
            legalEvidenceSnapshotId="snapshot-1",
            status=status,
            reason="temporary exception",
            conditions=[
                {
                    "code": "CURE-1",
                    "description": "Attach verified evidence",
                }
            ],
            validFrom=(
                NOW
                if status in {"approved", "cured", "revoked"}
                else None
            ),
            validUntil=NOW + timedelta(days=10),
            cureStatus=cure_status,
            cureEvidenceSnapshotId=(
                "snapshot-cure-1"
                if cure_status in {"evidence_submitted", "verified"}
                else None
            ),
        )


def test_delegation_is_explicit_versioned_and_revocable_only_by_grantor():
    repository = _repository(_Cursor())
    delegation = LegalApprovalDelegationInput(
        granteeId="external-counsel-1",
        granteeRole="external_counsel",
        scope="approval_gate",
        validUntil=NOW + timedelta(days=10),
        reason="Deal-specific external counsel substitution",
    )
    with pytest.raises(PermissionError):
        repository.grant_approval_delegation(
            delegation,
            actor_id="tenant-admin",
            actor_role="tenant_admin",
        )
    assert repository.grant_approval_delegation(
        delegation,
        actor_id="legal-approver-1",
        actor_role="legal_counsel_approver",
    ) == "legal-approval-delegation:new"


def _required_claim_policy() -> LegalRequiredClaimSetInput:
    return LegalRequiredClaimSetInput(
        claimSetKey="approval-gate-v1",
        claims=[
            {
                "claimKey": "permit.zoning",
                "claimTextSha256": "a" * 64,
                "description": "Verify zoning eligibility.",
            },
            {
                "claimKey": "permit.far",
                "claimTextSha256": "b" * 64,
                "description": "Verify applicable FAR.",
            },
        ],
    )


def test_required_claim_policy_is_versioned_hashed_and_maker_checker():
    cursor = _Cursor()
    policy = _required_claim_policy()

    inserted = _repository(cursor).propose_required_claim_set(
        policy,
        actor_id="policy-maker",
    )

    assert inserted == "legal-required-claim-set:new"
    claim_inserts = [
        call
        for call in cursor.calls
        if "INSERT INTO legal_required_claims" in call[0]
    ]
    assert len(claim_inserts) == 2
    source_hash = next(
        call[1][6]
        for call in cursor.calls
        if "INSERT INTO legal_required_claim_sets" in call[0]
    )
    source = (
        "approval-gate-v1",
        1,
        "approval_gate",
        source_hash,
        "policy-maker",
        NOW,
        {"legalApprovalDelegationId": "caller-spoofed"},
        ["permit.zoning", "permit.far"],
        ["a" * 64, "b" * 64],
        ["Verify zoning eligibility.", "Verify applicable FAR."],
        [{}, {}],
    )
    with pytest.raises(ValueError, match="self-approval"):
        _repository(
            _Cursor(required_claim_source_row=source)
        ).approve_required_claim_set(
            "required-claim-draft-1",
            actor_id="policy-maker",
            actor_role="legal_counsel_approver",
        )

    approval_cursor = _Cursor(required_claim_source_row=source)
    approved = _repository(
        approval_cursor
    ).approve_required_claim_set(
        "required-claim-draft-1",
        actor_id="policy-checker",
        actor_role="legal_counsel_approver",
    )
    assert approved == "legal-required-claim-set:new"
    approved_insert = [
        call
        for call in approval_cursor.calls
        if "INSERT INTO legal_required_claim_sets" in call[0]
    ][0]
    assert approved_insert[1][4] == 2
    assert approved_insert[1][9] == "policy-checker"
    assert "caller-spoofed" not in approved_insert[1][-1]

    with pytest.raises(
        PermissionError,
        match="legalApprovalDelegationId",
    ):
        _repository(
            _Cursor(required_claim_source_row=source)
        ).approve_required_claim_set(
            "required-claim-draft-1",
            actor_id="external-policy-checker",
            actor_role="external_counsel",
        )
    external_cursor = _Cursor(
        required_claim_source_row=source,
        delegation_authority_row=("delegation-policy-1",),
    )
    assert _repository(external_cursor).approve_required_claim_set(
        "required-claim-draft-1",
        actor_id="external-policy-checker",
        actor_role="external_counsel",
        legal_approval_delegation_id="delegation-policy-1",
    ) == "legal-required-claim-set:new"
    delegation_query = next(
        call
        for call in external_cursor.calls
        if "FROM legal_approval_delegations AS delegation" in call[0]
    )
    assert delegation_query[1][5] == "approval_gate"
    external_insert = next(
        call
        for call in external_cursor.calls
        if "INSERT INTO legal_required_claim_sets" in call[0]
    )
    assert external_insert[1][10:12] == (
        "external_counsel",
        "delegation-policy-1",
    )
    assert '"legalApprovalDelegationId":"delegation-policy-1"' in (
        external_insert[1][-1]
    )

    source = (
        "delegation-key-1",
        1,
        "legal-approver-1",
        "legal_counsel_approver",
        "external-counsel-1",
        "external_counsel",
        "approval_gate",
        NOW,
        NOW + timedelta(days=10),
        {},
    )
    with pytest.raises(PermissionError, match="grantor"):
        _repository(
            _Cursor(delegation_source_row=source)
        ).revoke_approval_delegation(
            "delegation-key-1:v1",
            LegalApprovalDelegationRevokeInput(
                reason="Engagement ended",
            ),
            actor_id="another-approver",
            actor_role="legal_counsel_approver",
        )
    assert _repository(
        _Cursor(delegation_source_row=source)
    ).revoke_approval_delegation(
        "delegation-key-1:v1",
        LegalApprovalDelegationRevokeInput(
            reason="Engagement ended",
        ),
        actor_id="legal-approver-1",
        actor_role="legal_counsel_approver",
    ) == "legal-approval-delegation:new"


def test_waiver_approval_requires_counsel_and_rejects_self_approval():
    source = (
        "matter-1",
        "snapshot-1",
        1,
        "temporary exception",
        [{"code": "CURE-1"}],
        NOW + timedelta(days=5),
        "not_started",
        None,
        "waiver-maker",
        NOW - timedelta(hours=1),
        {},
    )
    with pytest.raises(PermissionError):
        _repository(_Cursor(source_row=source)).approve_waiver(
            "waiver-1",
            actor_id="reviewer",
            actor_role="legal_reviewer",
        )

    with pytest.raises(ValueError, match="self-approval"):
        _repository(_Cursor(source_row=source)).approve_waiver(
            "waiver-1",
            actor_id="waiver-maker",
            actor_role="legal_counsel_approver",
        )


def test_external_counsel_waiver_approval_requires_active_delegation():
    source = (
        "matter-1",
        "snapshot-1",
        1,
        "temporary exception",
        [{"code": "CURE-1"}],
        NOW + timedelta(days=5),
        "not_started",
        None,
        "waiver-maker",
        NOW - timedelta(hours=1),
        {},
    )
    with pytest.raises(
        PermissionError,
        match="legalApprovalDelegationId",
    ):
        _repository(_Cursor(source_row=source)).approve_waiver(
            "waiver-1",
            actor_id="external-checker",
            actor_role="external_counsel",
        )
    with pytest.raises(
        PermissionError,
        match="active project-scoped",
    ):
        _repository(_Cursor(source_row=source)).approve_waiver(
            "waiver-1",
            actor_id="external-checker",
            actor_role="external_counsel",
            legal_approval_delegation_id="expired-delegation",
        )

    cursor = _Cursor(
        source_row=source,
        delegation_authority_row=("delegation-1",),
    )
    assert _repository(cursor).approve_waiver(
        "waiver-1",
        actor_id="external-checker",
        actor_role="external_counsel",
        legal_approval_delegation_id="delegation-1",
    ) == "legal-waiver:new"
    delegation_query = next(
        call
        for call in cursor.calls
        if "FROM legal_approval_delegations AS delegation" in call[0]
    )
    assert delegation_query[1][1:6] == (
        "tenant-1",
        "project-1",
        "external-checker",
        "external_counsel",
        "waiver_approval",
    )
    waiver_insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_waivers" in call[0]
    )
    assert waiver_insert[1][12:15] == (
        "external-checker",
        "external_counsel",
        "delegation-1",
    )
    assert '"legalApprovalDelegationId":"delegation-1"' in (
        waiver_insert[1][-1]
    )


def test_waiver_effective_date_does_not_rewrite_approval_timestamp():
    source = (
        "matter-1",
        "snapshot-1",
        1,
        "temporary exception",
        [{"code": "CURE-1"}],
        NOW + timedelta(days=5),
        "not_started",
        None,
        "waiver-maker",
        NOW - timedelta(hours=1),
        {},
    )
    cursor = _Cursor(source_row=source)
    effective_from = NOW + timedelta(days=1)
    assert _repository(cursor).approve_waiver(
        "waiver-1",
        actor_id="legal-checker",
        actor_role="legal_counsel_approver",
        valid_from=effective_from,
    ) == "legal-waiver:new"
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_waivers" in call[0]
    )
    assert insert[1][8] == effective_from
    assert insert[1][15] == NOW


def _approved_waiver_source(
    *,
    cure_status: str,
    metadata=None,
    valid_until: datetime | None = None,
    cure_evidence_snapshot_id: str | None = None,
):
    metadata_value = metadata or {}
    transition = metadata_value.get("cureTransition")
    cure_actor = (
        str(transition.get("actorId"))
        if cure_status != "not_started"
        and isinstance(transition, dict)
        and transition.get("actorId")
        else None
    )
    return (
        "matter-1",
        "snapshot-1",
        2,
        "temporary exception",
        [{"code": "CURE-1", "description": "Attach permit evidence"}],
        NOW - timedelta(days=1),
        valid_until or NOW + timedelta(days=5),
        cure_status,
        cure_evidence_snapshot_id,
        "waiver-maker",
        NOW - timedelta(days=2),
        "waiver-approver",
        "legal_counsel_approver",
        None,
        NOW - timedelta(days=1),
        cure_actor,
        "legal_reviewer" if cure_actor else None,
        NOW - timedelta(hours=12) if cure_actor else None,
        "Cure work started." if cure_actor else None,
        [],
        None,
        metadata_value,
    )


def test_waiver_cure_is_append_only_and_enforces_transition_order():
    invalid = _approved_waiver_source(cure_status="not_started")
    with pytest.raises(ValueError, match="invalid waiver cure transition"):
        _repository(_Cursor(source_row=invalid)).advance_waiver_cure(
            "waiver-2",
            LegalWaiverCureInput(
                cureStatus="cured",
                reason="Cure verified.",
                cureEvidenceSnapshotId="snapshot-cure-1",
                conditionOutcomes=[
                    {
                        "code": "CURE-1",
                        "status": "satisfied",
                        "evidenceItemIds": ["evidence-item-1"],
                    }
                ],
            ),
            actor_id="legal-checker",
            actor_role="legal_counsel_approver",
        )
    with pytest.raises(
        PermissionError,
        match="legalApprovalDelegationId",
    ):
        _repository(_Cursor(source_row=invalid)).advance_waiver_cure(
            "waiver-2",
            LegalWaiverCureInput(
                cureStatus="in_progress",
                reason="External counsel started the cure review.",
            ),
            actor_id="external-cure-reviewer",
            actor_role="external_counsel",
        )

    cursor = _Cursor(source_row=invalid)
    result = _repository(cursor).advance_waiver_cure(
        "waiver-2",
        LegalWaiverCureInput(
            cureStatus="in_progress",
            reason="Permit evidence collection started.",
        ),
        actor_id="cure-maker",
        actor_role="legal_reviewer",
    )
    assert result == "legal-waiver:new"
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_waivers" in call[0]
    )
    assert insert[1][5:8] == (3, "approved", "temporary exception")
    assert insert[1][11] == "in_progress"
    assert insert[1][19:23] == (
        "cure-maker",
        "legal_reviewer",
        NOW,
        "Permit evidence collection started.",
    )
    assert insert[1][25] == "waiver-2"
    assert '"from":"not_started"' in insert[1][26]
    assert '"to":"in_progress"' in insert[1][26]


def test_waiver_evidence_submission_binds_each_satisfied_condition_item():
    with pytest.raises(ValidationError, match="evidenceItemIds"):
        LegalWaiverCureInput(
            cureStatus="evidence_submitted",
            reason="Evidence package submitted.",
            cureEvidenceSnapshotId="snapshot-cure-1",
            conditionOutcomes=[
                {"code": "CURE-1", "status": "satisfied"}
            ],
        )

    source = _approved_waiver_source(
        cure_status="in_progress",
        metadata={
            "cureTransition": {
                "actorId": "cure-maker",
                "to": "in_progress",
            }
        },
    )
    cursor = _Cursor(
        source_row=source,
        evidence_snapshot_row=("snapshot-cure-1",),
        evidence_item_binding_row=(1,),
    )
    result = _repository(cursor).advance_waiver_cure(
        "waiver-3",
        LegalWaiverCureInput(
            cureStatus="evidence_submitted",
            reason="Verified evidence package submitted.",
            cureEvidenceSnapshotId="snapshot-cure-1",
            conditionOutcomes=[
                {
                    "code": "CURE-1",
                    "status": "satisfied",
                    "evidenceItemIds": ["evidence-item-1"],
                }
            ],
        ),
        actor_id="evidence-submitter",
        actor_role="legal_reviewer",
    )
    assert result == "legal-waiver:new"
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_waivers" in call[0]
    )
    assert insert[1][6] == "approved"
    assert insert[1][11:13] == (
        "evidence_submitted",
        "snapshot-cure-1",
    )
    evidence_query = next(
        call
        for call in cursor.calls
        if call[0].startswith(
            "SELECT COUNT(DISTINCT requested.legal_evidence_item_id)"
        )
    )
    assert evidence_query[1] == (
        ["evidence-item-1"],
        "snapshot-cure-1",
        "tenant-1",
        "project-1",
    )


def test_terminal_waiver_cure_requires_evidence_and_maker_checker():
    with pytest.raises(ValidationError, match="cureEvidenceSnapshotId"):
        LegalWaiverCureInput(
            cureStatus="cured",
            reason="Cure verified.",
        )

    source = _approved_waiver_source(
        cure_status="evidence_submitted",
        cure_evidence_snapshot_id="snapshot-cure-1",
        metadata={
            "cureTransition": {
                "actorId": "cure-maker",
                "to": "in_progress",
            }
        },
    )
    payload = LegalWaiverCureInput(
        cureStatus="cured",
        reason="The verified permit evidence satisfies every condition.",
        cureEvidenceSnapshotId="snapshot-cure-1",
        conditionOutcomes=[
            {
                "code": "CURE-1",
                "status": "satisfied",
                "evidenceItemIds": ["evidence-item-1"],
            }
        ],
    )
    with pytest.raises(ValueError, match="unknown waiver conditions"):
        _repository(_Cursor(source_row=source)).advance_waiver_cure(
            "waiver-3",
            payload.model_copy(
                update={
                    "condition_outcomes": [
                        payload.condition_outcomes[0].model_copy(
                            update={"code": "UNKNOWN"}
                        )
                    ]
                }
            ),
            actor_id="cure-checker",
            actor_role="legal_counsel_approver",
        )
    with pytest.raises(ValueError, match="self-verification"):
        _repository(_Cursor(source_row=source)).advance_waiver_cure(
            "waiver-3",
            payload,
            actor_id="cure-maker",
            actor_role="legal_counsel_approver",
        )

    cursor = _Cursor(
        source_row=source,
        evidence_snapshot_row=("snapshot-cure-1",),
        evidence_item_binding_row=(1,),
    )
    assert _repository(cursor).advance_waiver_cure(
        "waiver-3",
        payload,
        actor_id="cure-checker",
        actor_role="legal_counsel_approver",
    ) == "legal-waiver:new"
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_waivers" in call[0]
    )
    assert insert[1][6] == "cured"
    assert insert[1][11:13] == ("verified", "snapshot-cure-1")
    assert insert[1][19:23] == (
        "cure-checker",
        "legal_counsel_approver",
        NOW,
        "The verified permit evidence satisfies every condition.",
    )
    assert insert[1][25] == "waiver-3"


def test_failed_waiver_cure_revokes_waiver_and_expired_waiver_is_rejected():
    source = _approved_waiver_source(
        cure_status="evidence_submitted",
        cure_evidence_snapshot_id="snapshot-failure-1",
        metadata={"cureTransition": {"actorId": "cure-maker"}},
    )
    cursor = _Cursor(
        source_row=source,
        evidence_snapshot_row=("snapshot-failure-1",),
    )
    assert _repository(cursor).advance_waiver_cure(
        "waiver-4",
        LegalWaiverCureInput(
            cureStatus="failed",
            reason="The authority rejected the cure application.",
            cureEvidenceSnapshotId="snapshot-failure-1",
            conditionOutcomes=[
                {
                    "code": "CURE-1",
                    "status": "failed",
                    "note": "Permit application rejected.",
                }
            ],
        ),
        actor_id="cure-checker",
        actor_role="legal_counsel_approver",
    ) == "legal-waiver:new"
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_waivers" in call[0]
    )
    assert insert[1][6] == "revoked"
    assert insert[1][11] == "failed"

    with pytest.raises(ValueError, match="expired"):
        _repository(
            _Cursor(
                source_row=_approved_waiver_source(
                    cure_status="not_started",
                    valid_until=NOW - timedelta(seconds=1),
                )
            )
        ).advance_waiver_cure(
            "waiver-expired",
            LegalWaiverCureInput(
                cureStatus="in_progress",
                reason="Late cure attempt.",
            ),
            actor_id="legal-reviewer",
            actor_role="legal_reviewer",
        )


def test_external_counsel_terminal_cure_binds_typed_delegation():
    source = _approved_waiver_source(
        cure_status="evidence_submitted",
        cure_evidence_snapshot_id="snapshot-cure-2",
        metadata={"cureTransition": {"actorId": "cure-maker"}},
    )
    cursor = _Cursor(
        source_row=source,
        evidence_snapshot_row=("snapshot-cure-2",),
        evidence_item_binding_row=(1,),
        delegation_authority_row=("delegation-cure-1",),
    )
    assert _repository(cursor).advance_waiver_cure(
        "waiver-5",
        LegalWaiverCureInput(
            cureStatus="cured",
            reason="Delegated external counsel verified the cure.",
            cureEvidenceSnapshotId="snapshot-cure-2",
            legalApprovalDelegationId="delegation-cure-1",
        conditionOutcomes=[
                {
                    "code": "CURE-1",
                    "status": "satisfied",
                    "evidenceItemIds": ["evidence-item-2"],
                }
            ],
        ),
        actor_id="external-cure-checker",
        actor_role="external_counsel",
    ) == "legal-waiver:new"
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_waivers" in call[0]
    )
    assert insert[1][19:22] == (
        "external-cure-checker",
        "external_counsel",
        NOW,
    )
    assert insert[1][24] == "delegation-cure-1"


def test_counsel_opinion_and_ai_analysis_contracts_are_hash_bound():
    with pytest.raises(ValidationError, match="contentSha256"):
        CounselOpinionVersionInput(
            projectLegalMatterId="matter-1",
            legalEvidenceSnapshotId="snapshot-1",
            objectLocator="s3://legal/opinion.pdf",
            contentSha256="not-a-hash",
        )


def test_counsel_opinion_revision_uses_the_locked_immediate_version():
    revision = CounselOpinionVersionInput(
        projectLegalMatterId="matter-1",
        legalEvidenceSnapshotId="snapshot-2",
        opinionVersion=2,
        status="draft",
        objectLocator="s3://legal/opinion-v2.pdf",
        contentSha256=SHA,
        authoredAt=NOW,
        supersedesOpinionId="opinion-1",
    )
    cursor = _Cursor(
        opinion_predecessor_row=(1, NOW - timedelta(hours=1))
    )
    assert _repository(cursor).append_counsel_opinion(
        revision,
        actor_id="opinion-author",
        actor_role="legal_counsel_approver",
    ) == "counsel-opinion:new"
    predecessor_query = next(
        call
        for call in cursor.calls
        if "SELECT source.opinion_version" in call[0]
    )
    assert "NOT EXISTS" in predecessor_query[0]
    assert "FOR UPDATE" in predecessor_query[0]
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO counsel_opinions" in call[0]
    )
    assert insert[1][5] == 2
    assert insert[1][-2] == "opinion-1"

    with pytest.raises(ValueError, match="server-derived"):
        _repository(
            _Cursor(
                opinion_predecessor_row=(1, NOW - timedelta(hours=1))
            )
        ).append_counsel_opinion(
            revision.model_copy(update={"opinion_version": 3}),
            actor_id="opinion-author",
            actor_role="legal_counsel_approver",
        )
    with pytest.raises(ValueError, match="dedicated transition"):
        _repository(_Cursor()).append_counsel_opinion(
            revision.model_copy(
                update={
                    "status": "withdrawn",
                    "supersedes_opinion_id": None,
                    "opinion_version": 1,
                }
            ),
            actor_id="opinion-author",
            actor_role="legal_counsel_approver",
        )


def test_analysis_revision_uses_the_locked_immediate_version():
    revision = LegalAnalysisVersionInput(
        projectLegalMatterId="matter-1",
        legalEvidenceSnapshotId="snapshot-2",
        analysisKind="ai_summary",
        analysisVersion=2,
        objectLocator="s3://legal/analysis-v2.json",
        contentSha256=SHA,
        inputSnapshotSha256=SHA,
        supersedesAnalysisVersionId="analysis-1",
    )
    cursor = _Cursor(analysis_predecessor_row=(1, NOW - timedelta(hours=1)))
    assert _repository(cursor).append_analysis(
        revision,
        actor_id="analysis-maker",
    ) == "legal-analysis:new"
    predecessor_query = next(
        call
        for call in cursor.calls
        if "SELECT source.analysis_version" in call[0]
    )
    assert "NOT EXISTS" in predecessor_query[0]
    assert "FOR UPDATE" in predecessor_query[0]
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_analysis_versions" in call[0]
    )
    assert insert[1][6] == 2
    assert insert[1][-2] == "analysis-1"

    with pytest.raises(ValueError, match="server-derived"):
        _repository(
            _Cursor(
                analysis_predecessor_row=(
                    1,
                    NOW - timedelta(hours=1),
                )
            )
        ).append_analysis(
            revision.model_copy(update={"analysis_version": 3}),
            actor_id="analysis-maker",
        )


def test_ai_analysis_review_is_maker_checker_and_counsel_gated_for_reports():
    source = (
        "matter-1",
        "snapshot-1",
        "ai_summary",
        1,
        "s3://legal/summary.json",
        SHA,
        SHA,
        "legal-model-1",
        SHA,
        "analysis-maker",
        NOW - timedelta(hours=1),
        {},
    )
    review = LegalAnalysisReviewInput(
        reviewStatus="approved",
        reason="Checked against the cited corpus snapshot.",
        reportEligible=True,
    )
    with pytest.raises(PermissionError, match="final counsel"):
        _repository(_Cursor(source_row=source)).review_analysis(
            "analysis-1",
            review,
            actor_id="legal-reviewer",
            actor_role="legal_reviewer",
        )
    with pytest.raises(ValueError, match="self-review"):
        _repository(_Cursor(source_row=source)).review_analysis(
            "analysis-1",
            review,
            actor_id="analysis-maker",
            actor_role="legal_counsel_approver",
        )
    cursor = _Cursor(source_row=source)
    assert _repository(cursor).review_analysis(
        "analysis-1",
        review,
        actor_id="legal-checker",
        actor_role="legal_counsel_approver",
    ) == "legal-analysis:new"
    insert = next(
        call
        for call in cursor.calls
        if "INSERT INTO legal_analysis_versions" in call[0]
    )
    assert insert[1][12:19] == (
        False,
        True,
        "approved",
        "legal-checker",
        "legal_counsel_approver",
        None,
        NOW,
    )


def test_external_counsel_final_opinion_and_analysis_require_delegation():
    opinion_source = (
        "matter-1",
        "snapshot-1",
        1,
        "s3://legal/opinion.pdf",
        SHA,
        True,
        "opinion-maker",
        NOW - timedelta(hours=1),
        {},
    )
    with pytest.raises(
        PermissionError,
        match="legalApprovalDelegationId",
    ):
        _repository(
            _Cursor(source_row=opinion_source)
        ).approve_counsel_opinion(
            "opinion-1",
            actor_id="external-checker",
            actor_role="external_counsel",
        )
    opinion_cursor = _Cursor(
        source_row=opinion_source,
        delegation_authority_row=("delegation-opinion-1",),
    )
    assert _repository(opinion_cursor).approve_counsel_opinion(
        "opinion-1",
        actor_id="external-checker",
        actor_role="external_counsel",
        legal_approval_delegation_id="delegation-opinion-1",
    ) == "counsel-opinion:new"
    opinion_check = next(
        call
        for call in opinion_cursor.calls
        if "FROM legal_approval_delegations AS delegation" in call[0]
    )
    assert opinion_check[1][5] == "counsel_opinion"
    opinion_insert = next(
        call
        for call in opinion_cursor.calls
        if "INSERT INTO counsel_opinions" in call[0]
    )
    assert opinion_insert[1][11:14] == (
        "external-checker",
        "external_counsel",
        "delegation-opinion-1",
    )

    analysis_source = (
        "matter-1",
        "snapshot-1",
        "ai_summary",
        1,
        "s3://legal/summary.json",
        SHA,
        SHA,
        "legal-model-1",
        SHA,
        "analysis-maker",
        NOW - timedelta(hours=1),
        {},
    )
    review = LegalAnalysisReviewInput(
        reviewStatus="approved",
        reason="Verified against the frozen evidence snapshot.",
        reportEligible=True,
        legalApprovalDelegationId="delegation-analysis-1",
    )
    analysis_cursor = _Cursor(
        source_row=analysis_source,
        delegation_authority_row=("delegation-analysis-1",),
    )
    assert _repository(analysis_cursor).review_analysis(
        "analysis-1",
        review,
        actor_id="external-checker",
        actor_role="external_counsel",
    ) == "legal-analysis:new"
    analysis_check = next(
        call
        for call in analysis_cursor.calls
        if "FROM legal_approval_delegations AS delegation" in call[0]
    )
    assert analysis_check[1][5] == "approval_gate"
    analysis_insert = next(
        call
        for call in analysis_cursor.calls
        if "INSERT INTO legal_analysis_versions" in call[0]
    )
    assert analysis_insert[1][17] == "delegation-analysis-1"
    assert '"legalApprovalDelegationId":"delegation-analysis-1"' in (
        analysis_insert[1][-1]
    )


def test_rejected_ai_analysis_cannot_be_report_eligible():
    with pytest.raises(ValidationError, match="reportEligible"):
        LegalAnalysisReviewInput(
            reviewStatus="rejected",
            reason="The citations do not support the conclusion.",
            reportEligible=True,
        )


def test_tenant_alias_review_is_append_only_and_maker_checker_enforced():
    source = (
        "term-1",
        1,
        "브릿지론",
        "브릿지론",
        "alias-maker",
        NOW - timedelta(hours=1),
        {},
    )
    with pytest.raises(ValueError, match="self-review"):
        _repository(_Cursor(alias_source_row=source)).review_tenant_term_alias(
            "alias-1",
            TenantLegalTermAliasReviewInput(
                reviewStatus="approved",
                reason="Verified against the tenant glossary policy.",
            ),
            actor_id="alias-maker",
            actor_role="legal_reviewer",
        )

    result = _repository(
        _Cursor(alias_source_row=source)
    ).review_tenant_term_alias(
        "alias-1",
        TenantLegalTermAliasReviewInput(
            reviewStatus="approved",
            reason="Verified against the tenant glossary policy.",
        ),
        actor_id="alias-checker",
        actor_role="legal_reviewer",
    )
    assert result["status"] == "approved"
    assert result["reviewEventId"].startswith(
        "tenant-legal-term-alias-review:"
    )


def test_workspace_never_returns_privileged_object_locators():
    cursor = _Cursor()
    workspace = _repository(cursor).list_workspace()
    assert workspace["counselOpinions"] == []
    opinion_query = next(
        sql
        for sql, _params in cursor.calls
        if "FROM counsel_opinions" in sql
    )
    analysis_query = next(
        sql
        for sql, _params in cursor.calls
        if "FROM legal_analysis_versions" in sql
    )
    assert "object_locator" not in opinion_query
    assert "object_locator" not in analysis_query
    assert "protected_object_present" in opinion_query
    assert "protected_object_present" in analysis_query
    assert "legal_approval_delegation_id" in opinion_query
    assert "legal_approval_delegation_id" in analysis_query
    waiver_query = next(
        sql
        for sql, _params in cursor.calls
        if "FROM legal_waivers" in sql
    )
    assert "cure_condition_outcomes_json" in waiver_query
    assert "cure_approval_delegation_id" in waiver_query
    required_claim_query = next(
        sql
        for sql, _params in cursor.calls
        if "FROM legal_required_claim_sets AS claim_set" in sql
    )
    assert "'approverRole', claim_set.approver_role" in required_claim_query
    assert "claim_set.legal_approval_delegation_id" in (
        required_claim_query
    )

    opinion = CounselOpinionVersionInput(
        projectLegalMatterId="matter-1",
        legalEvidenceSnapshotId="snapshot-1",
        objectLocator="s3://legal/opinion.pdf",
        contentSha256=f"sha256:{SHA}",
        status="issued",
    )
    cursor = _Cursor()
    assert (
        _repository(cursor).append_counsel_opinion(
            opinion,
            actor_id="outside-counsel",
            actor_role="external_counsel",
        )
        == "counsel-opinion:new"
    )

    with pytest.raises(ValidationError, match="reportEligible"):
        LegalAnalysisVersionInput(
            projectLegalMatterId="matter-1",
            legalEvidenceSnapshotId="snapshot-1",
            analysisKind="ai_summary",
            objectLocator="s3://legal/summary.json",
            contentSha256=SHA,
            inputSnapshotSha256=SHA,
            reportEligible=True,
        )
