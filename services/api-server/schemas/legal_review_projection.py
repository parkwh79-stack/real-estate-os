"""Tenant-native contracts for immutable Legal Pod review projections.

The normalized PostgreSQL rows are the durable review-run ledger.  Fields used
by approval/readiness are first-class columns; ``display`` is a hash-bound,
non-authoritative compatibility view for existing Legal Pod screens.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
_INTERNAL_RECORD_LOCATOR_TYPES = {
    "legal_review",
    "legal_review_record",
    "permit_review",
    "permit_review_record",
}


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class LegalReviewProjectionModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class ReviewEvidenceProjection(LegalReviewProjectionModel):
    status: Literal[
        "missing",
        "weak",
        "partial",
        "verified",
        "complete",
        "unverified",
        "failed",
        "unavailable",
    ]
    evidence_locators: list[dict[str, Any]] = Field(default_factory=list)
    verified_evidence_count: int = Field(default=0, ge=0)
    verified_reference_count: int = Field(default=0, ge=0)
    verified_citation_count: int = Field(default=0, ge=0)
    required_claim_count: int = Field(default=0, ge=0)
    verified_claim_count: int = Field(default=0, ge=0)
    claim_coverage_complete: bool = False
    open_conflict_count: int = Field(default=0, ge=0)
    stale: bool = True
    citation_verification_status: Literal[
        "not_verified",
        "not_requested",
        "unverified",
        "conflicted",
        "partial",
        "verified",
        "failed",
        "stale",
    ] = "not_verified"
    legal_evidence_snapshot_id: str | None = Field(
        default=None,
        max_length=300,
    )
    snapshot_version: int | None = Field(default=None, ge=1)
    snapshot_sha256: str | None = None
    snapshot_gate_eligible: bool = False
    required_claim_set_id: str | None = Field(default=None, max_length=300)
    required_claim_set_sha256: str | None = None

    @model_validator(mode="after")
    def _validate_evidence_contract(self) -> "ReviewEvidenceProjection":
        if self.snapshot_sha256 and not _SHA256.fullmatch(
            self.snapshot_sha256.casefold()
        ):
            raise ValueError("snapshotSha256 must be a SHA-256 digest")
        if self.required_claim_set_sha256 and not _SHA256.fullmatch(
            self.required_claim_set_sha256.casefold()
        ):
            raise ValueError(
                "requiredClaimSetSha256 must be a SHA-256 digest"
            )
        if self.snapshot_gate_eligible and (
            not self.legal_evidence_snapshot_id
            or self.snapshot_version is None
            or not self.snapshot_sha256
            or not self.required_claim_set_id
            or not self.required_claim_set_sha256
            or self.verified_reference_count < 1
            or self.verified_citation_count < 1
            or self.required_claim_count < 1
            or self.verified_claim_count != self.required_claim_count
            or not self.claim_coverage_complete
            or self.open_conflict_count != 0
            or self.stale
            or self.citation_verification_status != "verified"
        ):
            raise ValueError(
                "snapshotGateEligible requires the normalized snapshot and "
                "required-claim policy identity, complete citation/claim "
                "coverage, current evidence, and no open conflicts"
            )
        for locator in self.evidence_locators:
            source_type = str(
                locator.get("sourceType")
                or locator.get("source_type")
                or ""
            ).strip().casefold()
            if source_type in _INTERNAL_RECORD_LOCATOR_TYPES:
                raise ValueError(
                    "an internal review record locator cannot be evidence"
                )
            if not any(
                locator.get(key) not in (None, "", [], {})
                for key in (
                    "sourceDocumentId",
                    "evidenceId",
                    "sourceUrl",
                    "pageNumber",
                    "sheetName",
                    "cell",
                    "apiRowId",
                    "artifactKey",
                    "resourceId",
                    "resourceVersionId",
                    "officialId",
                    "contentHash",
                )
            ):
                raise ValueError(
                    "each evidence locator requires an external source identity"
                )
        return self


class ReviewParserProjection(LegalReviewProjectionModel):
    status: Literal[
        "not_applicable",
        "not_available",
        "unverified",
        "verified",
        "failed",
    ]
    module_key: str | None = Field(default=None, max_length=300)
    module_version: str | None = Field(default=None, max_length=100)
    input_sha256: str | None = None
    output_sha256: str | None = None
    failure_reason: str | None = Field(default=None, max_length=5_000)

    @model_validator(mode="after")
    def _validate_parser_contract(self) -> "ReviewParserProjection":
        for field_name in ("input_sha256", "output_sha256"):
            value = getattr(self, field_name)
            if value and not _SHA256.fullmatch(value.casefold()):
                raise ValueError(
                    f"{_to_camel(field_name)} must be a SHA-256 digest"
                )
        if self.status == "verified" and not self.module_key:
            raise ValueError("a verified parser requires moduleKey")
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("a failed parser requires failureReason")
        return self


class ReviewRecordLocator(LegalReviewProjectionModel):
    source_type: Literal["legal_review_record", "permit_review_record"]
    review_id: str = Field(min_length=1, max_length=300)


class LegalReviewDecisionProjectionBinding(LegalReviewProjectionModel):
    """Immutable normalized projection identity reviewed by human counsel."""

    review_version_id: str = Field(min_length=1, max_length=300)
    review_version: int = Field(ge=1)
    projection_sha256: str

    @model_validator(mode="after")
    def _validate_projection_hash(
        self,
    ) -> "LegalReviewDecisionProjectionBinding":
        if not _SHA256.fullmatch(self.projection_sha256.casefold()):
            raise ValueError("projectionSha256 must be a SHA-256 digest")
        return self


class _ReviewProjectionInput(LegalReviewProjectionModel):
    review_id: str = Field(min_length=1, max_length=300)
    status: str = Field(min_length=1, max_length=100)
    review_level: Literal["preliminary", "full", "counsel"] = "preliminary"
    evidence: ReviewEvidenceProjection
    parser: ReviewParserProjection
    analysis_only: bool = True
    report_eligible: bool = False
    human_review_status: Literal[
        "pending",
        "approved",
        "counsel_approved",
        "rejected",
        "needs_revision",
        "waived",
    ] = "pending"
    record_locator: ReviewRecordLocator
    display: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_authority_boundary(self) -> "_ReviewProjectionInput":
        if self.report_eligible and (
            self.analysis_only
            or self.human_review_status
            not in {"approved", "counsel_approved"}
        ):
            raise ValueError(
                "reportEligible requires an approved, non-analysis-only "
                "projection"
            )
        if self.record_locator.review_id != self.review_id:
            raise ValueError("recordLocator.reviewId must match reviewId")
        return self


class LegalReviewProjectionInput(_ReviewProjectionInput):
    projection_kind: Literal["legal"] = "legal"
    status: Literal[
        "clear",
        "caution",
        "blocking_issue",
        "failed",
        "not_assessable",
    ]

    @model_validator(mode="after")
    def _legal_locator(self) -> "LegalReviewProjectionInput":
        if self.record_locator.source_type != "legal_review_record":
            raise ValueError(
                "a legal projection requires a legal_review_record locator"
            )
        return self


class PermitReviewProjectionInput(_ReviewProjectionInput):
    projection_kind: Literal["permit"] = "permit"
    status: Literal[
        "clear",
        "caution",
        "blocking",
        "pending_evidence",
        "failed",
    ]

    @model_validator(mode="after")
    def _permit_locator(self) -> "PermitReviewProjectionInput":
        if self.record_locator.source_type != "permit_review_record":
            raise ValueError(
                "a permit projection requires a permit_review_record locator"
            )
        return self


__all__ = [
    "LegalReviewProjectionInput",
    "PermitReviewProjectionInput",
    "ReviewEvidenceProjection",
    "ReviewParserProjection",
    "ReviewRecordLocator",
]
