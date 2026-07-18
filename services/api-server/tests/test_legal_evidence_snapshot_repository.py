from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib

import pytest

from db.repositories.legal_intelligence_repository import (
    PostgresLegalTenantOverlayRepository,
)


NOW = datetime(2026, 7, 18, 6, 30, tzinfo=timezone.utc)
RESOURCE_ID = "legal-resource:building-act"
VERSION_ID = "legal-version:building-act:2026"
SOURCE_FETCH_ID = "legal-fetch:building-act:2026"
LOCATOR = "https://www.law.go.kr/법령/건축법/제11조"
NORMALIZED_HASH = "a" * 64
PROVISION_TEXT = (
    "A person who intends to construct a building must obtain the permit "
    "required by the cited provision."
)


class _SnapshotCursor:
    def __init__(self, connection):
        self.connection = connection
        self.one = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        normalized = " ".join(str(statement).split())
        values = tuple(params or ())
        self.connection.statements.append((normalized, values))
        self.one = None
        if "FROM legal_resource_versions AS competing" in normalized:
            self.one = (self.connection.open_issue,)
        elif "snapshot.gate_eligible" in normalized:
            self.one = self.connection.gate_snapshot
        elif "FROM legal_evidence_snapshots" in normalized:
            self.one = self.connection.latest
        elif "FROM legal_resource_versions AS version" in normalized:
            key = (str(values[1]), str(values[2]), values[0])
            self.one = self.connection.lineages.get(key)
        elif normalized.startswith("INSERT INTO legal_evidence_snapshots"):
            self.connection.snapshot_inserts.append(values)
            self.one = (values[0],)
        elif normalized.startswith("INSERT INTO legal_evidence_snapshot_items"):
            self.connection.item_inserts.append(values)
            self.one = (values[0],)
        elif normalized.startswith("INSERT INTO legal_review_decisions"):
            self.connection.decision_inserts.append(values)
            self.one = (values[0],)

    def fetchone(self):
        return self.one


class _SnapshotConnection:
    def __init__(
        self,
        *,
        latest=None,
        lineages=None,
        open_issue=False,
        gate_snapshot=None,
    ):
        self.latest = latest
        self.lineages = dict(lineages or {})
        self.open_issue = open_issue
        self.gate_snapshot = gate_snapshot
        self.statements = []
        self.snapshot_inserts = []
        self.item_inserts = []
        self.decision_inserts = []
        self.exit_exception = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _traceback):
        self.exit_exception = exc_type
        return False

    def cursor(self):
        return _SnapshotCursor(self)


def _lineage(
    *,
    resource_id=RESOURCE_ID,
    version_id=VERSION_ID,
    source_fetch_id=SOURCE_FETCH_ID,
    normalized_hash=NORMALIZED_HASH,
    verified_at=NOW,
    verification_status="schema_verified",
    fetch_status="succeeded",
    locator=LOCATOR,
    body_text=PROVISION_TEXT,
):
    return (
        resource_id,
        version_id,
        source_fetch_id,
        normalized_hash,
        verified_at,
        verification_status,
        fetch_status,
        locator,
        body_text,
        hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
    )


def _reference(**overrides):
    value = {
        "transport": "local_verified_snapshot",
        "verificationStatus": "verified_snapshot",
        "resourceId": RESOURCE_ID,
        "resourceVersionId": VERSION_ID,
        "sourceFetchId": SOURCE_FETCH_ID,
        "legalProvisionId": "legal-provision:building-act:11",
        "exactLocator": LOCATOR,
        "normalizedContentSha256": f"sha256:{NORMALIZED_HASH}",
        "verifiedAt": NOW.isoformat(),
        "claimText": "본 사업은 건축법 제11조의 건축허가 대상이다.",
        "excerpt": "건축물을 건축하려는 자는 허가를 받아야 한다.",
        "metadata": {"citation": "건축법 제11조"},
    }
    value["excerpt"] = PROVISION_TEXT
    value.update(overrides)
    return value


def _snapshot(references=None, **overrides):
    value = {
        "asOfDate": "2026-07-18",
        "freshnessPolicyVersion": "legal-freshness-v1",
        "hasOpenConflicts": False,
        "stale": False,
        "expectedCitationCount": 1,
        "expectedClaimCount": 1,
        "verifiedClaimCount": 1,
        "references": references or [_reference()],
        "metadata": {"purpose": "approval_gate"},
    }
    value.update(overrides)
    return value


def _repository(connection, **kwargs):
    return PostgresLegalTenantOverlayRepository(
        tenant_id="tenant-1",
        project_id="project-1",
        connection_factory=lambda: connection,
        **kwargs,
    )


def _default_lineages():
    return {
        (
            RESOURCE_ID,
            VERSION_ID,
            "legal-provision:building-act:11",
        ): _lineage()
    }


def test_append_snapshot_and_exact_items_are_atomic_rls_scoped_and_gate_eligible():
    connection = _SnapshotConnection(lineages=_default_lineages())

    result = _repository(connection).append_verified_evidence_snapshot(
        _snapshot(),
        created_by="legal-reviewer-1",
        created_at=NOW,
    )

    assert result["snapshotVersion"] == 1
    assert result["supersedesSnapshotId"] is None
    assert result["citationVerificationStatus"] == "verified"
    assert result["verifiedReferenceCount"] == 1
    assert result["gateEligible"] is True
    assert result["snapshotSha256"].startswith("sha256:")
    assert len(connection.snapshot_inserts) == 1
    assert len(connection.item_inserts) == 1
    snapshot_insert = connection.snapshot_inserts[0]
    item_insert = connection.item_inserts[0]
    assert snapshot_insert[1:3] == ("tenant-1", "project-1")
    assert snapshot_insert[7] == result["snapshotSha256"].removeprefix("sha256:")
    assert snapshot_insert[8:13] == (
        "verified",
        False,
        False,
        True,
        None,
    )
    assert item_insert[1:4] == (
        "tenant-1",
        "project-1",
        result["legalEvidenceSnapshotId"],
    )
    assert item_insert[5:10] == (
        RESOURCE_ID,
        VERSION_ID,
        "legal-provision:building-act:11",
        SOURCE_FETCH_ID,
        LOCATOR,
    )
    normalized_statements = [statement for statement, _ in connection.statements]
    tenant_context_index = next(
        index
        for index, statement in enumerate(normalized_statements)
        if "realytics.tenant_id" in statement
    )
    advisory_lock_index = next(
        index
        for index, statement in enumerate(normalized_statements)
        if "pg_advisory_xact_lock" in statement
    )
    snapshot_insert_index = next(
        index
        for index, statement in enumerate(normalized_statements)
        if statement.startswith("INSERT INTO legal_evidence_snapshots")
    )
    assert tenant_context_index < advisory_lock_index < snapshot_insert_index
    assert connection.exit_exception is None


def test_snapshot_hash_and_ids_are_deterministic_for_same_state_and_content():
    first_connection = _SnapshotConnection(lineages=_default_lineages())
    second_connection = _SnapshotConnection(lineages=_default_lineages())

    first = _repository(first_connection).append_verified_evidence_snapshot(
        _snapshot(),
        created_by="reviewer-a",
        created_at=NOW,
    )
    second = _repository(second_connection).append_verified_evidence_snapshot(
        _snapshot(),
        created_by="reviewer-b",
        created_at=NOW,
    )

    assert first["snapshotSha256"] == second["snapshotSha256"]
    assert first["legalEvidenceSnapshotId"] == second["legalEvidenceSnapshotId"]
    assert (
        first["items"][0]["legalEvidenceItemId"]
        == second["items"][0]["legalEvidenceItemId"]
    )


def test_next_snapshot_is_monotonic_and_supersedes_latest_under_lock():
    connection = _SnapshotConnection(
        latest=("legal-evidence-snapshot:old", 4, "f" * 64),
        lineages=_default_lineages(),
    )
    payload = _snapshot(
        expectedSnapshotVersion=5,
        expectedSupersedesSnapshotId="legal-evidence-snapshot:old",
    )

    result = _repository(connection).append_verified_evidence_snapshot(
        payload,
        created_by="legal-reviewer-1",
        created_at=NOW,
    )

    assert result["snapshotVersion"] == 5
    assert result["supersedesSnapshotId"] == "legal-evidence-snapshot:old"
    assert connection.snapshot_inserts[0][4] == 5
    assert connection.snapshot_inserts[0][12] == "legal-evidence-snapshot:old"


def test_optimistic_supersession_mismatch_fails_before_any_insert():
    connection = _SnapshotConnection(
        latest=("legal-evidence-snapshot:newer", 2, "e" * 64),
        lineages=_default_lineages(),
    )

    with pytest.raises(ValueError, match="expectedSnapshotVersion"):
        _repository(connection).append_verified_evidence_snapshot(
            _snapshot(
                expectedSnapshotVersion=2,
                expectedSupersedesSnapshotId="legal-evidence-snapshot:old",
            ),
            created_by="reviewer",
        )

    assert connection.snapshot_inserts == []
    assert connection.item_inserts == []
    assert connection.exit_exception is ValueError


def test_direct_official_result_cannot_be_sealed_before_local_corpus_ingestion():
    connection = _SnapshotConnection(lineages=_default_lineages())
    direct_reference = _reference(
        transport="law_go_kr_direct_api",
        verificationStatus="source_attested",
    )

    with pytest.raises(ValueError, match="not a verified local corpus reference"):
        _repository(connection).append_verified_evidence_snapshot(
            _snapshot([direct_reference]),
            created_by="reviewer",
        )

    assert connection.snapshot_inserts == []
    assert connection.item_inserts == []


@pytest.mark.parametrize(
    ("missing_key", "expected"),
    [
        ("resourceId", "resourceId"),
        ("resourceVersionId", "resourceVersionId"),
        ("sourceFetchId", "sourceFetchId"),
        ("exactLocator", "exactLocator"),
        ("normalizedContentSha256", "normalizedContentSha256"),
        ("verifiedAt", "verifiedAt"),
    ],
)
def test_required_corpus_lineage_fields_fail_closed(missing_key, expected):
    connection = _SnapshotConnection(lineages=_default_lineages())
    reference = _reference()
    reference.pop(missing_key)

    with pytest.raises(ValueError, match=expected):
        _repository(connection).append_verified_evidence_snapshot(
            _snapshot([reference]),
            created_by="reviewer",
        )

    assert connection.snapshot_inserts == []
    assert connection.item_inserts == []


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            {"normalizedContentSha256": "sha256:" + ("b" * 64)},
            "normalizedContentSha256 lineage mismatch",
        ),
        (
            {"verifiedAt": "2026-07-18T06:31:00+00:00"},
            "verifiedAt lineage mismatch",
        ),
        (
            {"exactLocator": "https://www.law.go.kr/법령/건축법"},
            "exactLocator lineage mismatch",
        ),
        (
            {"sourceFetchId": "different-fetch"},
            "sourceFetchId lineage mismatch",
        ),
    ],
)
def test_corpus_lineage_mismatch_rolls_back_before_snapshot_insert(
    override,
    expected,
):
    connection = _SnapshotConnection(lineages=_default_lineages())

    with pytest.raises(ValueError, match=expected):
        _repository(connection).append_verified_evidence_snapshot(
            _snapshot([_reference(**override)]),
            created_by="reviewer",
        )

    assert connection.snapshot_inserts == []
    assert connection.item_inserts == []
    assert connection.exit_exception is ValueError


def test_missing_freshness_or_conflict_assessment_is_sealed_but_not_gate_eligible():
    connection = _SnapshotConnection(lineages=_default_lineages())
    payload = _snapshot()
    payload.pop("hasOpenConflicts")
    payload.pop("stale")

    result = _repository(connection).append_verified_evidence_snapshot(
        payload,
        created_by="reviewer",
        created_at=NOW,
    )

    assert result["hasOpenConflicts"] is True
    assert result["stale"] is True
    assert result["gateEligible"] is False


def test_all_references_are_validated_before_snapshot_or_item_insert():
    second = _reference(
        resourceId="legal-resource:missing",
        resourceVersionId="legal-version:missing",
        sourceFetchId="legal-fetch:missing",
        legalProvisionId="legal-provision:missing",
        exactLocator="https://www.law.go.kr/법령/없는법",
    )
    connection = _SnapshotConnection(lineages=_default_lineages())

    with pytest.raises(ValueError, match="no matching local corpus lineage"):
        _repository(connection).append_verified_evidence_snapshot(
            _snapshot([deepcopy(_reference()), second]),
            created_by="reviewer",
        )

    assert connection.snapshot_inserts == []
    assert connection.item_inserts == []


def test_duplicate_exact_items_cannot_inflate_verified_reference_count():
    reference = _reference()
    connection = _SnapshotConnection(lineages=_default_lineages())

    with pytest.raises(ValueError, match="duplicate exact evidence items"):
        _repository(connection).append_verified_evidence_snapshot(
            _snapshot([reference, deepcopy(reference)]),
            created_by="reviewer",
        )

    assert connection.snapshot_inserts == []
    assert connection.item_inserts == []


def test_database_detected_open_issue_overrides_caller_conflict_flag():
    connection = _SnapshotConnection(
        lineages=_default_lineages(),
        open_issue=True,
    )

    result = _repository(connection).append_verified_evidence_snapshot(
        _snapshot(hasOpenConflicts=False),
        created_by="reviewer",
        created_at=NOW,
    )

    assert result["hasOpenConflicts"] is True
    assert result["gateEligible"] is False


def test_database_verified_at_derives_staleness_even_when_caller_says_fresh():
    connection = _SnapshotConnection(lineages=_default_lineages())
    evaluated_at = NOW + timedelta(days=8)

    result = _repository(
        connection,
        freshness_days=7,
        now=lambda: evaluated_at,
    ).append_verified_evidence_snapshot(
        _snapshot(stale=False),
        created_by="reviewer",
        created_at=evaluated_at,
    )

    assert result["stale"] is True
    assert result["gateEligible"] is False


def test_excerpt_must_be_verbatim_from_the_exact_official_provision():
    connection = _SnapshotConnection(lineages=_default_lineages())

    with pytest.raises(ValueError, match="does not match the exact official"):
        _repository(connection).append_verified_evidence_snapshot(
            _snapshot([_reference(excerpt="invented legal conclusion")]),
            created_by="reviewer",
            created_at=NOW,
        )

    assert connection.snapshot_inserts == []
    assert connection.item_inserts == []


def test_partial_citation_coverage_can_be_sealed_but_never_gate_eligible():
    connection = _SnapshotConnection(lineages=_default_lineages())

    result = _repository(connection).append_verified_evidence_snapshot(
        _snapshot(expectedCitationCount=2),
        created_by="reviewer",
        created_at=NOW,
    )

    assert result["verifiedCitationCount"] == 1
    assert result["expectedCitationCount"] == 2
    assert result["coverageComplete"] is False
    assert result["gateEligible"] is False


def test_uncovered_required_claim_can_never_be_gate_eligible():
    connection = _SnapshotConnection(lineages=_default_lineages())

    result = _repository(connection).append_verified_evidence_snapshot(
        _snapshot(expectedClaimCount=2, verifiedClaimCount=1),
        created_by="reviewer",
        created_at=NOW,
    )

    assert result["expectedClaimCount"] == 2
    assert result["verifiedClaimCount"] == 1
    assert result["claimCoverageComplete"] is False
    assert result["coverageComplete"] is False
    assert result["gateEligible"] is False


def _approve_decision(**overrides):
    decision = {
        "decisionId": "decision-1",
        "decisionType": "approve",
        "decisionScope": "approval_gate",
        "reason": "Verified by counsel.",
        "reviewerId": "counsel-1",
        "reviewerRole": "external_counsel",
        "decidedAt": NOW.isoformat(),
        "legalEvidenceSnapshotId": "snapshot-1",
        "metadata": {
            "legalReviewId": "review-1",
            "evidenceSnapshotVersion": 1,
            "evidenceSnapshotSha256": "sha256:" + ("d" * 64),
        },
    }
    decision.update(overrides)
    return decision


def test_approve_decision_rechecks_snapshot_hash_version_and_review_lineage():
    connection = _SnapshotConnection(
        gate_snapshot=(
            1,
            "d" * 64,
            {"legalReviewId": "review-1"},
        )
    )

    inserted = _repository(connection).append_review_decision(
        _approve_decision()
    )

    assert inserted == "decision-1"
    assert len(connection.decision_inserts) == 1


@pytest.mark.parametrize(
    ("gate_snapshot", "decision", "expected"),
    [
        (None, _approve_decision(), "gate-eligible"),
        (
            (1, "d" * 64, {"legalReviewId": "different-review"}),
            _approve_decision(),
            "review lineage",
        ),
        (
            (2, "d" * 64, {"legalReviewId": "review-1"}),
            _approve_decision(),
            "version changed",
        ),
        (
            (1, "e" * 64, {"legalReviewId": "review-1"}),
            _approve_decision(),
            "hash changed",
        ),
    ],
)
def test_approve_decision_fails_closed_on_snapshot_mismatch(
    gate_snapshot,
    decision,
    expected,
):
    connection = _SnapshotConnection(gate_snapshot=gate_snapshot)

    with pytest.raises(ValueError, match=expected):
        _repository(connection).append_review_decision(decision)

    assert connection.decision_inserts == []
