"""Typed tenant Legal Intelligence governance contracts.

These payloads are intentionally append-only.  A status transition creates a
new version/event; callers cannot rewrite an issued legal record.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class GovernanceModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class LegalMatterType(str, Enum):
    STATUTORY = "statutory"
    PERMIT = "permit"
    LAND_TITLE = "land_title"
    CONTRACT = "contract"
    SECURITY = "security"
    TRUST = "trust"
    DISPUTE = "dispute"
    COMPLIANCE = "compliance"
    OTHER = "other"


class LegalSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LegalMatterStatus(str, Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    BLOCKED = "blocked"
    RESOLVED = "resolved"
    WAIVED = "waived"
    CLOSED = "closed"
    SUPERSEDED = "superseded"


class LegalMatterVersionInput(GovernanceModel):
    matter_key: str = Field(min_length=1, max_length=200)
    matter_version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=500)
    matter_type: LegalMatterType
    status: LegalMatterStatus = LegalMatterStatus.OPEN
    severity: LegalSeverity = LegalSeverity.MEDIUM
    owner_user_id: str | None = Field(default=None, max_length=300)
    due_at: datetime | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    supersedes_matter_id: str | None = Field(default=None, max_length=300)
    legal_evidence_snapshot_id: str | None = Field(
        default=None,
        max_length=300,
    )
    resolution_reason: str | None = Field(default=None, max_length=5_000)
    legal_approval_delegation_id: str | None = Field(
        default=None,
        max_length=300,
    )
    legal_waiver_id: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_terminal_authority(self) -> "LegalMatterVersionInput":
        terminal = self.status in {
            LegalMatterStatus.RESOLVED,
            LegalMatterStatus.WAIVED,
            LegalMatterStatus.CLOSED,
            LegalMatterStatus.SUPERSEDED,
        }
        if (
            self.status
            in {
                LegalMatterStatus.RESOLVED,
                LegalMatterStatus.CLOSED,
                LegalMatterStatus.SUPERSEDED,
            }
            and self.closed_at is None
        ):
            raise ValueError("closedAt is required for a closed matter status")
        if terminal:
            if not self.legal_evidence_snapshot_id:
                raise ValueError(
                    "legalEvidenceSnapshotId is required for a terminal "
                    "matter status"
                )
            if not self.resolution_reason:
                raise ValueError(
                    "resolutionReason is required for a terminal matter status"
                )
            if (
                self.status == LegalMatterStatus.WAIVED
                and not self.legal_waiver_id
            ):
                raise ValueError(
                    "legalWaiverId is required for a waived matter status"
                )
            if (
                self.status != LegalMatterStatus.WAIVED
                and self.legal_waiver_id is not None
            ):
                raise ValueError(
                    "legalWaiverId is only valid for a waived matter status"
                )
        elif any(
            value is not None
            for value in (
                self.legal_evidence_snapshot_id,
                self.resolution_reason,
                self.legal_approval_delegation_id,
                self.legal_waiver_id,
            )
        ):
            raise ValueError(
                "terminal authority fields are only valid for a terminal "
                "matter status"
            )
        return self


class WaiverCondition(GovernanceModel):
    code: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    due_at: datetime | None = None
    required_evidence_type: str | None = Field(default=None, max_length=200)


class LegalWaiverVersionInput(GovernanceModel):
    project_legal_matter_id: str = Field(min_length=1, max_length=300)
    legal_evidence_snapshot_id: str = Field(min_length=1, max_length=300)
    waiver_version: int = Field(default=1, ge=1)
    status: str = Field(
        default="proposed",
        pattern=r"^(proposed|approved|rejected|expired|cured|revoked|superseded)$",
    )
    reason: str = Field(min_length=1, max_length=5_000)
    conditions: list[WaiverCondition] = Field(min_length=1)
    valid_from: datetime | None = None
    valid_until: datetime
    cure_status: str = Field(
        default="not_started",
        pattern=(
            r"^(not_started|in_progress|evidence_submitted|verified|failed|"
            r"not_applicable)$"
        ),
    )
    cure_evidence_snapshot_id: str | None = Field(default=None, max_length=300)
    supersedes_waiver_id: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_waiver_lifecycle(self) -> "LegalWaiverVersionInput":
        allowed_cure_statuses = {
            "proposed": {"not_started"},
            "approved": {
                "not_started",
                "in_progress",
                "evidence_submitted",
            },
            "cured": {"verified"},
            "revoked": {"failed"},
            "rejected": {"not_applicable"},
            "expired": {"not_applicable"},
            "superseded": {"not_applicable"},
        }
        if self.cure_status not in allowed_cure_statuses[self.status]:
            raise ValueError(
                "status and cureStatus do not form a valid waiver state"
            )
        if self.status == "proposed" and (
            self.waiver_version != 1
            or self.supersedes_waiver_id is not None
        ):
            raise ValueError(
                "an initial waiver proposal must be version 1 without "
                "supersedesWaiverId"
            )
        if self.valid_until.tzinfo is None:
            raise ValueError("validUntil must include a timezone")
        if self.valid_from is not None and self.valid_from.tzinfo is None:
            raise ValueError("validFrom must include a timezone")
        if self.status == "approved" and self.valid_from is None:
            raise ValueError("validFrom is required for an approved waiver")
        if (
            self.cure_status in {"evidence_submitted", "verified"}
            and not self.cure_evidence_snapshot_id
        ):
            raise ValueError(
                "cureEvidenceSnapshotId is required once cure evidence is submitted"
            )
        if self.valid_from is not None and self.valid_until <= self.valid_from:
            raise ValueError("validUntil must be later than validFrom")
        condition_codes = [condition.code for condition in self.conditions]
        if len(condition_codes) != len(set(condition_codes)):
            raise ValueError("waiver conditions must use unique codes")
        for condition in self.conditions:
            if (
                condition.due_at is not None
                and condition.due_at.tzinfo is None
            ):
                raise ValueError(
                    "waiver condition dueAt must include a timezone"
                )
            if (
                condition.due_at is not None
                and condition.due_at > self.valid_until
            ):
                raise ValueError(
                    "waiver condition dueAt must not be later than validUntil"
                )
        return self


class WaiverCureConditionOutcome(GovernanceModel):
    code: str = Field(min_length=1, max_length=200)
    status: str = Field(pattern=r"^(in_progress|satisfied|failed)$")
    note: str | None = Field(default=None, max_length=2_000)
    evidence_item_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_evidence_binding(self) -> "WaiverCureConditionOutcome":
        identifiers = [
            str(identifier or "").strip()
            for identifier in self.evidence_item_ids
        ]
        if any(not identifier for identifier in identifiers):
            raise ValueError("evidenceItemIds must not contain blank values")
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("evidenceItemIds must be unique")
        if self.status == "satisfied" and not identifiers:
            raise ValueError(
                "a satisfied condition requires non-empty evidenceItemIds"
            )
        self.evidence_item_ids = identifiers
        return self


class LegalWaiverCureInput(GovernanceModel):
    cure_status: str = Field(
        pattern=r"^(in_progress|evidence_submitted|cured|failed)$"
    )
    reason: str = Field(min_length=1, max_length=5_000)
    cure_evidence_snapshot_id: str | None = Field(
        default=None,
        max_length=300,
    )
    legal_approval_delegation_id: str | None = Field(
        default=None,
        max_length=300,
    )
    condition_outcomes: list[WaiverCureConditionOutcome] = Field(
        default_factory=list,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_terminal_cure_evidence(self) -> "LegalWaiverCureInput":
        if (
            self.cure_status in {"evidence_submitted", "cured", "failed"}
            and not self.cure_evidence_snapshot_id
        ):
            raise ValueError(
                "cureEvidenceSnapshotId is required once cure evidence "
                "is submitted"
            )
        codes = [outcome.code for outcome in self.condition_outcomes]
        if len(codes) != len(set(codes)):
            raise ValueError("conditionOutcomes must use unique codes")
        if (
            self.cure_status in {"evidence_submitted", "cured", "failed"}
            and not codes
        ):
            raise ValueError(
                "conditionOutcomes are required once cure evidence is "
                "submitted"
            )
        if self.cure_status == "cured" and any(
            outcome.status != "satisfied"
            for outcome in self.condition_outcomes
        ):
            raise ValueError(
                "a cured waiver requires every condition outcome "
                "to be satisfied"
            )
        if self.cure_status == "failed" and not any(
            outcome.status == "failed"
            for outcome in self.condition_outcomes
        ):
            raise ValueError(
                "a failed waiver cure requires at least one failed condition"
            )
        if self.cure_status == "evidence_submitted" and any(
            outcome.status == "failed"
            for outcome in self.condition_outcomes
        ):
            raise ValueError(
                "a failed condition must use the failed terminal transition"
            )
        return self


class CounselOpinionVersionInput(GovernanceModel):
    project_legal_matter_id: str = Field(min_length=1, max_length=300)
    legal_evidence_snapshot_id: str = Field(min_length=1, max_length=300)
    opinion_version: int = Field(default=1, ge=1)
    status: str = Field(
        default="draft",
        pattern=r"^(draft|issued|approved|withdrawn|superseded)$",
    )
    object_locator: str = Field(min_length=1, max_length=2_000)
    content_sha256: str
    privileged: bool = True
    authored_at: datetime | None = None
    supersedes_opinion_id: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_hash_and_locator(self) -> "CounselOpinionVersionInput":
        if not _SHA256.fullmatch(self.content_sha256.casefold()):
            raise ValueError("contentSha256 must be a SHA-256 digest")
        lowered = self.object_locator.casefold()
        if any(
            marker in lowered
            for marker in ("token=", "secret=", "password=", "key=")
        ):
            raise ValueError("objectLocator must not contain credentials")
        return self


class LegalAnalysisVersionInput(GovernanceModel):
    project_legal_matter_id: str = Field(min_length=1, max_length=300)
    legal_evidence_snapshot_id: str = Field(min_length=1, max_length=300)
    analysis_kind: str = Field(
        pattern=r"^(ai_summary|deterministic_analysis|counsel_summary)$"
    )
    analysis_version: int = Field(default=1, ge=1)
    object_locator: str = Field(min_length=1, max_length=2_000)
    content_sha256: str
    input_snapshot_sha256: str
    model_id: str | None = Field(default=None, max_length=300)
    prompt_sha256: str | None = None
    analysis_only: bool = True
    report_eligible: bool = False
    human_review_status: str = Field(
        default="pending",
        pattern=r"^(pending|approved|rejected)$",
    )
    supersedes_analysis_version_id: str | None = Field(
        default=None,
        max_length=300,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_analysis_contract(self) -> "LegalAnalysisVersionInput":
        for field_name in ("content_sha256", "input_snapshot_sha256"):
            value = str(getattr(self, field_name)).casefold()
            if not _SHA256.fullmatch(value):
                raise ValueError(
                    f"{_to_camel(field_name)} must be a SHA-256 digest"
                )
        if self.prompt_sha256 and not _SHA256.fullmatch(
            self.prompt_sha256.casefold()
        ):
            raise ValueError("promptSha256 must be a SHA-256 digest")
        if self.report_eligible and (
            self.analysis_only or self.human_review_status != "approved"
        ):
            raise ValueError(
                "reportEligible requires approved, non-analysis-only content"
            )
        return self


class LegalAnalysisReviewInput(GovernanceModel):
    review_status: str = Field(pattern=r"^(approved|rejected)$")
    reason: str = Field(min_length=1, max_length=5_000)
    report_eligible: bool = False
    legal_approval_delegation_id: str | None = Field(
        default=None,
        max_length=300,
    )

    @model_validator(mode="after")
    def _validate_review_outcome(self) -> "LegalAnalysisReviewInput":
        if self.review_status != "approved" and self.report_eligible:
            raise ValueError(
                "reportEligible is only available for an approved analysis"
            )
        return self


class LegalRequiredClaimInput(GovernanceModel):
    claim_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$"
    )
    claim_text_sha256: str
    description: str | None = Field(default=None, max_length=2_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_claim_hash(self) -> "LegalRequiredClaimInput":
        if not _SHA256.fullmatch(self.claim_text_sha256.casefold()):
            raise ValueError("claimTextSha256 must be a SHA-256 digest")
        return self


class LegalRequiredClaimSetInput(GovernanceModel):
    claim_set_key: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$"
    )
    claim_set_version: int = Field(default=1, ge=1)
    review_scope: str = Field(
        default="approval_gate",
        pattern=r"^(approval_gate|report_eligibility|counsel_opinion)$",
    )
    claims: list[LegalRequiredClaimInput] = Field(min_length=1)
    supersedes_claim_set_id: str | None = Field(
        default=None,
        max_length=300,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_unique_claims(self) -> "LegalRequiredClaimSetInput":
        keys = [item.claim_key for item in self.claims]
        if len(keys) != len(set(keys)):
            raise ValueError("claims must use unique claimKey values")
        hashes = [
            item.claim_text_sha256.casefold().removeprefix("sha256:")
            for item in self.claims
        ]
        if len(hashes) != len(set(hashes)):
            raise ValueError("claims must use unique claimTextSha256 values")
        return self


class LegalAnnotationInput(GovernanceModel):
    project_legal_matter_id: str = Field(min_length=1, max_length=300)
    legal_resource_id: str = Field(min_length=1, max_length=300)
    legal_resource_version_id: str = Field(min_length=1, max_length=300)
    legal_provision_id: str | None = Field(default=None, max_length=300)
    exact_locator: str = Field(min_length=1, max_length=2_000)
    annotation_text_sha256: str
    annotation_type: str = Field(
        pattern=(
            r"^(issue|interpretation|counsel_note|cure_evidence|"
            r"cross_reference)$"
        )
    )
    status: str = Field(
        default="active",
        pattern=r"^(active|resolved|superseded)$",
    )
    supersedes_annotation_id: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_annotation_hash(self) -> "LegalAnnotationInput":
        if not _SHA256.fullmatch(self.annotation_text_sha256.casefold()):
            raise ValueError("annotationTextSha256 must be a SHA-256 digest")
        return self


class TenantLegalTermAliasInput(GovernanceModel):
    term_id: str = Field(min_length=1, max_length=300)
    alias_version: int = Field(default=1, ge=1)
    alias_text: str = Field(min_length=1, max_length=500)
    normalized_alias: str = Field(min_length=1, max_length=500)
    status: str = Field(
        default="candidate",
        pattern=r"^(candidate|approved|rejected|superseded)$",
    )
    supersedes_alias_id: str | None = Field(default=None, max_length=300)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TenantLegalTermAliasReviewInput(GovernanceModel):
    review_status: str = Field(
        pattern=r"^(approved|rejected|needs_revision)$"
    )
    reason: str = Field(min_length=1, max_length=5_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LegalApprovalDelegationInput(GovernanceModel):
    grantee_id: str = Field(min_length=1, max_length=300)
    grantee_role: str = Field(
        pattern=r"^(legal_counsel_approver|external_counsel)$"
    )
    scope: str = Field(
        pattern=(
            r"^(approval_gate|citator_review|counsel_opinion|"
            r"waiver_approval|matter_resolution)$"
        )
    )
    valid_from: datetime | None = None
    valid_until: datetime
    reason: str = Field(min_length=1, max_length=5_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_delegation_period(
        self,
    ) -> "LegalApprovalDelegationInput":
        if (
            self.valid_from is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("validUntil must be later than validFrom")
        return self


class LegalApprovalDelegationRevokeInput(GovernanceModel):
    reason: str = Field(min_length=1, max_length=5_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "CounselOpinionVersionInput",
    "LegalApprovalDelegationInput",
    "LegalApprovalDelegationRevokeInput",
    "LegalAnalysisReviewInput",
    "LegalAnalysisVersionInput",
    "LegalAnnotationInput",
    "LegalMatterStatus",
    "LegalMatterType",
    "LegalMatterVersionInput",
    "LegalRequiredClaimInput",
    "LegalRequiredClaimSetInput",
    "LegalSeverity",
    "LegalWaiverCureInput",
    "LegalWaiverVersionInput",
    "TenantLegalTermAliasInput",
    "TenantLegalTermAliasReviewInput",
    "WaiverCureConditionOutcome",
    "WaiverCondition",
]
