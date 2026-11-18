"""Typed, fail-closed contracts for human Citator review.

Citator extraction may enqueue candidates, but it may never promote one to a
final state.  A review request records the human decision and creates an
append-only successor citation/assertion.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_RESERVED_REVIEW_METADATA_KEYS = frozenset(
    {
        "tenantId",
        "projectId",
        "sourceCitationId",
        "sourceAssertionId",
        "reviewerId",
        "reviewerRole",
        "reviewStatus",
        "legalApprovalDelegationId",
        "makerId",
        "reviewVersion",
        "snapshotId",
        "snapshotVersion",
        "snapshotSha256",
        "snapshotAsOfDate",
        "evidenceItemId",
        "resourceVersionId",
        "provisionId",
        "exactLocator",
        "sourceFetchId",
        "providerId",
        "excerptSha256",
        "provisionSha256",
        "verifiedAt",
        "sourceAuthority",
        "authorityTier",
        "freshnessPolicyVersion",
        "currentEvidenceBinding",
        "evidenceBinding",
        "reviewBindingCurrent",
        "objectEvidenceBinding",
        "objectEvidenceItemId",
        "objectResourceVersionId",
        "objectProvisionId",
        "objectExactLocator",
        "objectSourceFetchId",
        "objectProviderId",
        "objectExcerptSha256",
        "objectProvisionSha256",
    }
)


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class CitatorModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class CitatorRecordKind(str, Enum):
    CITATION = "citation"
    RELATION = "relation"


class CitatorReviewStatus(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


class CitatorReviewInput(CitatorModel):
    """One explicit human review decision.

    Exact text is required only for final approval.  Rejections and revision
    requests still require a reason, while avoiding the invention of an
    excerpt that the reviewer did not rely upon.
    """

    review_status: CitatorReviewStatus
    reason: str = Field(min_length=1, max_length=5_000)
    evidence_excerpt: str | None = Field(default=None, max_length=30_000)
    evidence_excerpt_sha256: str | None = None
    object_evidence_item_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
    )
    supersedes_review_event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
    )
    legal_approval_delegation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_exact_review_evidence(self) -> "CitatorReviewInput":
        excerpt = str(self.evidence_excerpt or "").strip()
        digest = str(self.evidence_excerpt_sha256 or "").casefold()
        if digest.startswith("sha256:"):
            digest = digest[7:]
            self.evidence_excerpt_sha256 = digest

        if self.review_status == CitatorReviewStatus.APPROVED:
            if not excerpt or not digest:
                raise ValueError(
                    "approved Citator review requires exact evidenceExcerpt "
                    "and evidenceExcerptSha256"
                )
        if excerpt or digest:
            if not excerpt or not _SHA256.fullmatch(digest):
                raise ValueError(
                    "evidenceExcerpt and a SHA-256 digest must be supplied together"
                )
            expected = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
            if digest != expected:
                raise ValueError(
                    "evidenceExcerptSha256 does not match evidenceExcerpt"
                )
            self.evidence_excerpt = excerpt
            self.evidence_excerpt_sha256 = digest
        reserved = sorted(
            key
            for key in self.metadata
            if key in _RESERVED_REVIEW_METADATA_KEYS
        )
        if reserved:
            raise ValueError(
                "metadata cannot override typed Citator review authority "
                f"fields: {', '.join(reserved)}"
            )
        return self


class CitatorEvidenceBinding(CitatorModel):
    """Sanitized, server-selected binding to current official evidence."""

    snapshot_id: str = Field(min_length=1)
    snapshot_version: int = Field(ge=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_as_of_date: date
    evidence_item_id: str = Field(min_length=1)
    resource_version_id: str = Field(min_length=1)
    provision_id: str = Field(min_length=1)
    exact_locator: str = Field(min_length=1)
    source_fetch_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    excerpt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verified_at: datetime
    source_authority: str = Field(min_length=1)
    authority_tier: str = Field(min_length=1)
    freshness_policy_version: str = Field(min_length=1)
    current: Literal[True] = True


class CitatorReviewOutcome(CitatorModel):
    record_kind: CitatorRecordKind
    source_record_id: str
    review_version_id: str
    review_version: int = Field(ge=1)
    review_event_id: str
    review_status: CitatorReviewStatus
    final_status: str
    reviewer_id: str
    reviewer_role: str
    reviewed_at: datetime
    evidence_excerpt_sha256: str | None = None
    legal_approval_delegation_id: str | None = None
    evidence_binding: CitatorEvidenceBinding | None = None
    object_evidence_binding: CitatorEvidenceBinding | None = None
    append_only: bool = True


__all__ = [
    "CitatorRecordKind",
    "CitatorEvidenceBinding",
    "CitatorReviewInput",
    "CitatorReviewOutcome",
    "CitatorReviewStatus",
]
