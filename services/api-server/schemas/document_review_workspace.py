"""Typed HTTP contract for the evidence-first document review workspace."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from parsing_platform.contracts import ParsingContract


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DocumentReviewFreshness(ParsingContract):
    status: Literal["observed", "unknown"]
    latest_source_at: str | None = None
    observed_at: str
    warning_codes: list[str] = Field(default_factory=list)


class DocumentReviewAuthority(ParsingContract):
    candidate_only: Literal[True]
    authoritative_projection_mutated: Literal[False]
    approval_authority: Literal["approved_input_snapshot_only"]
    assembly: Literal["server_versioned_read_model"]


class DocumentReviewQueueItem(ParsingContract):
    queue_item_id: str = Field(..., min_length=1)
    candidate_id: str = Field(..., min_length=1)
    parsing_run_id: str = Field(..., min_length=1)
    document_version_id: str = Field(..., min_length=1)
    field_key: str = Field(..., min_length=1)
    entity_id: str | None = None
    validation_outcome: Literal["passed", "warning", "failed", "unknown"]
    issue_codes: list[str] = Field(..., min_length=1)
    open_conflict_ids: list[str] = Field(default_factory=list)
    evidence_locator_count: int = Field(..., ge=0)
    severity: Literal["blocking", "high", "medium", "low"]

    @field_validator("issue_codes")
    @classmethod
    def require_supported_issue_codes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("issueCodes must not contain duplicates")
        supported = {
            "VALIDATION_NOT_PASSED",
            "MISSING_EVIDENCE_LOCATOR",
            "WEAK_EVIDENCE_LOCATOR",
            "OPEN_SOURCE_CONFLICT",
            "REVIEW_PENDING",
            "REVIEW_NEEDS_REVISION",
            "WEAK_SOURCE_AUTHORITY",
            "MISSING_VALUE",
        }
        unsupported = [
            code
            for code in value
            if code not in supported and not code.startswith("VALIDATION:")
        ]
        if unsupported:
            raise ValueError(
                "unsupported document review issueCodes: " + ", ".join(unsupported)
            )
        return value


class SnapshotDiffValue(ParsingContract):
    normalized_value: JsonValue
    unit: str | None = None
    candidate_id: str | None = None
    selection_id: str | None = None
    document_version_id: str | None = None
    source_hash: Sha256 | None = None
    source_locator: dict[str, JsonValue] | None = None
    authority: str | None = None
    effective_date: str | None = None
    as_of_date: str | None = None


class ApprovedSnapshotChange(ParsingContract):
    change_type: Literal["added", "removed", "changed"]
    field_key: str = Field(..., min_length=1)
    entity_id: str | None = None
    before: SnapshotDiffValue | None = None
    after: SnapshotDiffValue | None = None

    @model_validator(mode="after")
    def require_change_sides(self) -> "ApprovedSnapshotChange":
        if self.change_type == "added" and (self.before is not None or self.after is None):
            raise ValueError("added snapshot change requires only an after value")
        if self.change_type == "removed" and (self.before is None or self.after is not None):
            raise ValueError("removed snapshot change requires only a before value")
        if self.change_type == "changed" and (self.before is None or self.after is None):
            raise ValueError("changed snapshot change requires before and after values")
        return self


class ApprovedSnapshotDiff(ParsingContract):
    from_snapshot_id: str = Field(..., min_length=1)
    from_snapshot_hash: Sha256
    to_snapshot_id: str = Field(..., min_length=1)
    to_snapshot_hash: Sha256
    to_snapshot_version: int | None = Field(None, ge=1)
    approved_at: str | None = None
    comparable: bool
    issue_codes: list[Literal["SNAPSHOT_INPUT_ITEMS_UNAVAILABLE"]] = Field(
        default_factory=list
    )
    change_count: int = Field(..., ge=0)
    changes: list[ApprovedSnapshotChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_comparison_consistency(self) -> "ApprovedSnapshotDiff":
        if self.change_count != len(self.changes):
            raise ValueError("snapshot diff changeCount must equal changes length")
        if self.comparable and self.issue_codes:
            raise ValueError("a comparable snapshot diff cannot carry issueCodes")
        if not self.comparable and self.issue_codes != [
            "SNAPSHOT_INPUT_ITEMS_UNAVAILABLE"
        ]:
            raise ValueError(
                "an incomparable snapshot diff requires SNAPSHOT_INPUT_ITEMS_UNAVAILABLE"
            )
        return self


class DocumentReviewIssueSummary(ParsingContract):
    count: int = Field(..., ge=0)
    by_code: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)


class DocumentReviewWorkspaceViewModel(ParsingContract):
    schema_version: Literal["realytics-document-review-workspace-v1"]
    view_model_version: Literal["document-review-workspace-2026-07-27"]
    project_id: str = Field(..., min_length=1)
    as_of_date: str | None = None
    source_documents: list[dict[str, JsonValue]] = Field(default_factory=list)
    runs: list[dict[str, JsonValue]] = Field(default_factory=list)
    packages: list[dict[str, JsonValue]] = Field(default_factory=list)
    glossary_candidates: list[dict[str, JsonValue]] = Field(default_factory=list)
    review_queue: list[DocumentReviewQueueItem] = Field(default_factory=list)
    snapshot_diffs: list[ApprovedSnapshotDiff] = Field(default_factory=list)
    snapshot_id: str = Field(..., min_length=1)
    snapshot_hash: Sha256
    observed_at: str
    freshness: DocumentReviewFreshness
    issue_summary: DocumentReviewIssueSummary
    warnings: list[str] = Field(default_factory=list)
    authority: DocumentReviewAuthority

    @model_validator(mode="after")
    def validate_read_model_identity(self) -> "DocumentReviewWorkspaceViewModel":
        expected_id = f"document-review:{self.project_id}:{self.snapshot_hash[:20]}"
        if self.snapshot_id != expected_id:
            raise ValueError("snapshotId must be derived from projectId and snapshotHash")
        if self.issue_summary.count != len(self.review_queue):
            raise ValueError("issueSummary.count must equal reviewQueue length")
        return self


__all__ = [
    "ApprovedSnapshotDiff",
    "DocumentReviewQueueItem",
    "DocumentReviewWorkspaceViewModel",
]
