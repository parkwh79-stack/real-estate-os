from __future__ import annotations

"""Human decisions for Legal Intelligence reviews.

The HTTP layer must derive ``reviewer_id`` from the authenticated principal and
pass it separately.  Identity-looking fields in ``input_data`` are deliberately
ignored so a caller cannot approve a review on somebody else's behalf.

The legacy legal-review document is retained for compatibility, but decisions
are appended to ``reviewHistory`` as immutable events.  A normalized
``decision_writer`` can be supplied by the PostgreSQL Legal Intelligence
repository to dual-write the same event to ``legal_review_decisions``.
"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from storage import (
    list_legal_review_records,
    record_user_activity_log,
    upsert_legal_review_record,
)


LEGAL_REVIEW_DECISION_STATUSES = {
    "approved",
    "needs_revision",
    "rejected",
    "waived",
}
VERIFIED_EVIDENCE_STATUSES = {
    "verified",
    "citation_verified",
    "human_reviewed",
}
LEGAL_REVIEWER_ROLES = {
    "legal_reviewer",
    "external_counsel",
    "compliance_officer",
    "tenant_admin",
}
NORMALIZED_DECISION_TYPES = {
    "approved": "approve",
    "needs_revision": "request_changes",
    "rejected": "reject",
    "waived": "waive_with_conditions",
}
def decide_legal_review_use_case(
    project_id: str,
    review_id: str,
    input_data: Mapping[str, Any] | None,
    *,
    reviewer_id: str,
    reviewer_role: str = "legal_reviewer",
    reviewed_at: str | None = None,
    review_loader: Callable[[str, int], list[dict[str, Any]]] = list_legal_review_records,
    review_writer: Callable[[str, dict[str, Any]], dict[str, Any]] = upsert_legal_review_record,
    decision_writer: Callable[[dict[str, Any]], Any] | None = None,
    audit_recorder: Callable[[dict[str, Any]], Any] | None = record_user_activity_log,
) -> dict[str, Any]:
    """Append a trusted human decision and update the legacy review projection.

    ``approved`` is fail-closed: the referenced Legal Intelligence evidence must
    be verified, non-stale, contain at least one verified citation, and have no
    open conflicts.  ``waived`` is a distinct disposition and always requires
    an explicit reason; it is not treated as counsel approval by the readiness
    gate.
    """

    project_key = str(project_id).strip()
    review_key = str(review_id).strip()
    trusted_reviewer = str(reviewer_id).strip()
    trusted_reviewer_role = str(reviewer_role).strip().lower()
    if not project_key:
        raise ValueError("project_id is required")
    if not review_key:
        raise ValueError("review_id is required")
    if (
        not trusted_reviewer
        or trusted_reviewer.lower() in {"anonymous", "unknown", "system"}
    ):
        raise ValueError("reviewer_id must come from the authenticated principal")
    if trusted_reviewer_role not in LEGAL_REVIEWER_ROLES:
        allowed_roles = ", ".join(sorted(LEGAL_REVIEWER_ROLES))
        raise ValueError(
            "reviewer_role must come from trusted authorization context and be "
            f"one of: {allowed_roles}"
        )

    payload = dict(input_data or {})
    decision_status = _decision_status(payload)
    waiver_reason = _clean_text(payload.get("waiverReason") or payload.get("waiver_reason"))
    comment = _clean_text(payload.get("comment") or payload.get("reviewComment"))
    if decision_status == "waived" and not waiver_reason:
        raise ValueError("waiverReason is required when decisionStatus is waived")

    records = review_loader(project_key, 500)
    review = next(
        (
            item
            for item in records
            if isinstance(item, dict) and str(item.get("reviewId") or "") == review_key
        ),
        None,
    )
    if review is None:
        raise LookupError("Legal review not found")

    evidence = legal_evidence_readiness(review)
    if decision_status == "approved" and not evidence["pass"]:
        raise ValueError(
            "Legal review approval requires a gate-eligible sealed evidence "
            "snapshot with at least one exact verified citation, current "
            "evidence, complete required-claim coverage, and no open conflicts"
        )

    timestamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    intelligence = review.get("legalIntelligence")
    intelligence = intelligence if isinstance(intelligence, Mapping) else {}
    evidence_snapshot_id = _clean_text(
        intelligence.get("evidenceSnapshotId")
        or review.get("legalEvidenceSnapshotId")
    )
    legal_finding_id = _clean_text(
        review.get("legalFindingId")
        or intelligence.get("legalFindingId")
    )
    evidence_snapshot_version = intelligence.get("snapshotVersion")
    evidence_snapshot_sha256 = _clean_text(
        intelligence.get("snapshotSha256")
    )
    decision_reason = (
        waiver_reason
        or comment
        or _default_decision_reason(decision_status)
    )
    decision = {
        "schemaVersion": "realytics-legal-review-decision-v1",
        "decisionId": f"legal-review-decision:{uuid4()}",
        "projectId": project_key,
        "legalReviewId": review_key,
        "decisionStatus": decision_status,
        "reviewStatus": decision_status,
        "decisionType": NORMALIZED_DECISION_TYPES[decision_status],
        # Review-level decisions always address the approval gate.  A payload
        # cannot relabel itself as a counsel opinion or broader eligibility
        # decision.
        "decisionScope": "approval_gate",
        "reason": decision_reason,
        "legalFindingId": legal_finding_id,
        "legalEvidenceSnapshotId": evidence_snapshot_id,
        "reviewerId": trusted_reviewer,
        "reviewerRole": trusted_reviewer_role,
        "decidedAt": timestamp,
        "reviewedBy": trusted_reviewer,
        "reviewedAt": timestamp,
        "comment": comment,
        "waiverReason": waiver_reason,
        "evidenceAssessment": {
            "evidenceStatus": evidence["evidenceStatus"],
            "verifiedReferenceCount": evidence["verifiedReferenceCount"],
            "verifiedCitationCount": evidence["verifiedCitationCount"],
            "requiredClaimCount": evidence["requiredClaimCount"],
            "verifiedClaimCount": evidence["verifiedClaimCount"],
            "claimCoverageComplete": evidence["claimCoverageComplete"],
            "evidenceSnapshotId": evidence["evidenceSnapshotId"],
            "snapshotGateEligible": evidence["snapshotGateEligible"],
            "evidenceSnapshotVersion": evidence_snapshot_version,
            "evidenceSnapshotSha256": evidence_snapshot_sha256,
            "stale": evidence["stale"],
            "openConflictCount": evidence["openConflictCount"],
            "eligibleForApproval": evidence["pass"],
        },
        "metadata": {
            "legalReviewId": review_key,
            "decisionStatus": decision_status,
            "evidenceSnapshotVersion": evidence_snapshot_version,
            "evidenceSnapshotSha256": evidence_snapshot_sha256,
            "comment": comment,
            "waiverReason": waiver_reason,
            "evidenceAssessment": {
                "evidenceStatus": evidence["evidenceStatus"],
                "verifiedReferenceCount": evidence["verifiedReferenceCount"],
                "verifiedCitationCount": evidence["verifiedCitationCount"],
                "requiredClaimCount": evidence["requiredClaimCount"],
                "verifiedClaimCount": evidence["verifiedClaimCount"],
                "claimCoverageComplete": evidence["claimCoverageComplete"],
                "evidenceSnapshotId": evidence["evidenceSnapshotId"],
                "snapshotGateEligible": evidence["snapshotGateEligible"],
                "stale": evidence["stale"],
                "openConflictCount": evidence["openConflictCount"],
                "eligibleForApproval": evidence["pass"],
            },
        },
    }

    # Deep-copy before adding the projection so callers holding the record that
    # came from an in-memory store cannot observe a half-written decision.
    updated = deepcopy(review)
    history = updated.get("reviewHistory")
    if not isinstance(history, list):
        history = []
    updated["reviewHistory"] = [*history, decision]
    updated["reviewStatus"] = decision_status
    updated["humanReviewStatus"] = (
        "counsel_approved"
        if decision_status == "approved" and trusted_reviewer_role == "external_counsel"
        else decision_status
    )
    updated["reviewedBy"] = trusted_reviewer
    updated["reviewedAt"] = timestamp
    updated["reviewComment"] = comment
    if waiver_reason:
        updated["waiverReason"] = waiver_reason
    updated["requiresHumanReview"] = decision_status not in {"approved", "waived"}

    # A normalized repository implements an INSERT-only writer.  Write that
    # durable event before refreshing the mutable legacy projection.
    normalized_decision_persisted = False
    if decision_writer is not None and evidence_snapshot_id:
        decision_writer(deepcopy(decision))
        normalized_decision_persisted = True
    saved = review_writer(project_key, updated)

    if audit_recorder is not None:
        audit_recorder(
            {
                "eventType": "legal_review_decision_recorded",
                "projectId": project_key,
                "userId": trusted_reviewer,
                "createdAt": timestamp,
                "metadata": {
                    "decisionId": decision["decisionId"],
                    "legalReviewId": review_key,
                    "decisionStatus": decision_status,
                    "reviewerRole": trusted_reviewer_role,
                    "evidenceEligibleForApproval": evidence["pass"],
                    "normalizedDecisionPersisted": normalized_decision_persisted,
                },
            }
        )

    return {
        "schemaVersion": "realytics-legal-review-decision-result-v1",
        "projectId": project_key,
        "legalReviewId": review_key,
        "decision": decision,
        "legalReview": saved,
        "normalizedDecisionPersistence": {
            "persisted": normalized_decision_persisted,
            "reason": (
                None
                if normalized_decision_persisted
                else (
                    "decision_writer_not_configured"
                    if decision_writer is None
                    else "legal_evidence_snapshot_id_missing"
                )
            ),
        },
    }


# Route-friendly semantic alias.
review_legal_review_use_case = decide_legal_review_use_case


def legal_evidence_readiness(review: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the fail-closed evidence assessment shared with approval logic."""

    review = review if isinstance(review, Mapping) else {}
    intelligence = review.get("legalIntelligence")
    intelligence = intelligence if isinstance(intelligence, Mapping) else {}

    evidence_status = str(
        intelligence.get("evidenceStatus")
        or intelligence.get("verificationStatus")
        or ""
    ).strip().lower()
    verified_count = _non_negative_int(
        intelligence.get("verifiedReferenceCount")
        if intelligence.get("verifiedReferenceCount") is not None
        else intelligence.get("verifiedCitationCount")
    )
    verified_citation_count = _non_negative_int(
        intelligence.get("verifiedCitationCount")
    )
    required_claim_count = _non_negative_int(
        intelligence.get("requiredClaimCount")
    )
    verified_claim_count = _non_negative_int(
        intelligence.get("verifiedClaimCount")
    )
    claim_coverage_complete = (
        intelligence.get("claimCoverageComplete") is True
        and required_claim_count > 0
        and verified_claim_count == required_claim_count
    )
    evidence_snapshot_id = _clean_text(
        intelligence.get("evidenceSnapshotId")
        or review.get("legalEvidenceSnapshotId")
    )
    snapshot_gate_eligible = (
        intelligence.get("snapshotGateEligible") is True
        or intelligence.get("gateEligible") is True
    )
    stale = bool(intelligence.get("stale", True))
    open_conflict_count = _open_conflict_count(intelligence)
    passed = (
        evidence_status in VERIFIED_EVIDENCE_STATUSES
        and verified_count > 0
        and verified_citation_count > 0
        and claim_coverage_complete
        and bool(evidence_snapshot_id)
        and snapshot_gate_eligible
        and not stale
        and open_conflict_count == 0
    )
    return {
        "pass": passed,
        "evidenceStatus": evidence_status or "missing",
        "verifiedReferenceCount": verified_count,
        "verifiedCitationCount": verified_citation_count,
        "requiredClaimCount": required_claim_count,
        "verifiedClaimCount": verified_claim_count,
        "claimCoverageComplete": claim_coverage_complete,
        "evidenceSnapshotId": evidence_snapshot_id,
        "snapshotGateEligible": snapshot_gate_eligible,
        "stale": stale,
        "openConflictCount": open_conflict_count,
    }


def legal_human_review_status(review: Mapping[str, Any] | None) -> str:
    """Resolve the latest human disposition without using transport metadata."""

    review = review if isinstance(review, Mapping) else {}
    direct = review.get("humanReviewStatus") or review.get("reviewStatus")
    if direct:
        return str(direct).strip().lower()
    human_review = review.get("humanReview")
    if isinstance(human_review, Mapping):
        return str(
            human_review.get("status") or human_review.get("reviewStatus") or "pending"
        ).strip().lower()
    return "pending"


def _decision_status(payload: Mapping[str, Any]) -> str:
    status = str(
        payload.get("decisionStatus")
        or payload.get("reviewStatus")
        or payload.get("decision_status")
        or payload.get("review_status")
        or ""
    ).strip().lower()
    if status not in LEGAL_REVIEW_DECISION_STATUSES:
        allowed = ", ".join(sorted(LEGAL_REVIEW_DECISION_STATUSES))
        raise ValueError(f"decisionStatus must be one of: {allowed}")
    return status


def _open_conflict_count(intelligence: Mapping[str, Any]) -> int:
    explicit = intelligence.get("openConflictCount")
    if explicit is not None:
        return _non_negative_int(explicit)

    conflicts = intelligence.get("openConflicts")
    if isinstance(conflicts, Sequence) and not isinstance(conflicts, (str, bytes, bytearray)):
        return len(conflicts)
    if isinstance(conflicts, Mapping):
        return len(conflicts)
    if isinstance(conflicts, bool):
        return int(conflicts)
    return 0


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _default_decision_reason(status: str) -> str:
    return {
        "approved": "Verified Legal Intelligence evidence approved by an authorized reviewer.",
        "needs_revision": "Legal review returned for revision by an authorized reviewer.",
        "rejected": "Legal review rejected by an authorized reviewer.",
        "waived": "Legal review requirement waived with documented conditions.",
    }[status]
