"""Versioned, evidence-first contracts for the document parsing platform.

These models deliberately separate execution, approval, review, provenance,
presence, and conflict state.  They are transport contracts, not persistence
models: repositories may project them into database records, but must not
collapse the independent state machines into a single ``status`` column.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from agent_platform.contracts.models import SourceLocator as PlatformSourceLocator
from parsing_platform.field_registry import (
    FIELD_REGISTRY_VERSION,
    validate_candidate_against_registry,
)


CANONICAL_FIELD_KEY_PATTERN = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
CANDIDATE_SET_SEAL_SCHEMA_VERSION = "realytics-candidate-set-seal-v2"


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


class ParsingContract(BaseModel):
    """Strict API model with the platform's camelCase wire convention."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


class OriginType(str, Enum):
    EXTRACTED = "extracted"
    DERIVED = "derived"
    INFERRED = "inferred"
    OVERRIDDEN = "overridden"


class PresenceStatus(str, Enum):
    PRESENT = "present"
    MISSING = "missing"
    NOT_DISCLOSED = "not_disclosed"
    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"


class ConflictStatus(str, Enum):
    NONE = "none"
    OPEN = "open"
    RESOLVED = "resolved"
    WAIVED = "waived"


class SourceAuthority(str, Enum):
    """Semantic source class; ranking belongs to a domain-specific policy."""

    OFFICIAL_REGISTRY = "official_registry"
    EXECUTED_CONTRACT = "executed_contract"
    FINAL_PERMIT = "final_permit"
    AUDITED_FINANCIAL = "audited_financial"
    APPROVED_SCENARIO = "approved_scenario"
    SIGNED_REPORT = "signed_report"
    SUBMITTED_DOCUMENT = "submitted_document"
    EXTERNAL_MARKET_DATA = "external_market_data"
    DRAFT_DOCUMENT = "draft_document"
    MODEL_INFERENCE = "model_inference"
    UNKNOWN = "unknown"


class FindingSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ReviewAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_REVISION = "request_revision"


class FindingLifecycleAction(str, Enum):
    RESOLVED = "resolved"
    REOPENED = "reopened"
    WAIVED = "waived"


class SourceLocator(PlatformSourceLocator):
    """Backward-compatible locator with table, paragraph, and bbox precision.

    Fields inherited from :class:`agent_platform.contracts.models.SourceLocator`
    keep existing handoffs valid.  New parsers should prefer
    ``documentVersionId`` over a mutable filename or filesystem path.
    """

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )

    document_version_id: str | None = Field(None, min_length=1)
    file_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    cell_range: str | None = Field(None, min_length=1)
    table_id: str | None = Field(None, min_length=1)
    row_index: int | None = Field(None, ge=0)
    column_index: int | None = Field(None, ge=0)
    paragraph_index: int | None = Field(None, ge=0)
    bbox: tuple[float, float, float, float] | None = None
    coordinate_space: str | None = Field(None, min_length=1)
    raw_text_excerpt: str | None = Field(None, min_length=1, max_length=2_000)
    raw_text_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    parser_method: str | None = Field(None, min_length=1)
    workbook_formula: str | None = Field(None, min_length=1)
    workbook_cached_value: JsonValue = None

    @property
    def is_explicit(self) -> bool:
        document_anchor = bool(
            (self.document_version_id or self.document_id)
            and (
                self.page_number is not None
                or self.table_id
                or self.paragraph_index is not None
                or self.json_path
                or self.raw_text_sha256
            )
        )
        spreadsheet_anchor = bool(
            self.sheet_name and (self.cell_reference or self.cell_range)
        )
        api_anchor = bool(self.api_endpoint and self.api_row_id)
        return bool(
            self.evidence_id
            or self.source_url
            or self.artifact_key
            or document_anchor
            or spreadsheet_anchor
            or api_anchor
        )

    @model_validator(mode="after")
    def validate_locator_precision(self) -> "SourceLocator":
        if not self.is_explicit:
            raise ValueError("source locator must contain an explicit document, cell, API, URL, evidence, or artifact anchor")
        if self.bbox is not None:
            left, top, right, bottom = self.bbox
            if not all(math.isfinite(value) for value in self.bbox):
                raise ValueError("bbox coordinates must be finite numbers")
            if right < left or bottom < top:
                raise ValueError("bbox must be ordered as left, top, right, bottom")
            if self.page_number is None or not self.coordinate_space:
                raise ValueError("bbox requires pageNumber and coordinateSpace")
        if self.row_index is not None or self.column_index is not None:
            if not self.table_id:
                raise ValueError("rowIndex/columnIndex require tableId")
        payload_size = len(
            json.dumps(
                self.model_dump(by_alias=True, mode="json", exclude_none=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if payload_size > 64 * 1024:
            raise ValueError("source locator exceeds the controlled metadata limit")
        return self

    @classmethod
    def from_platform_locator(cls, locator: PlatformSourceLocator) -> "SourceLocator":
        return cls.model_validate(locator.model_dump(mode="python", by_alias=True))

    def to_platform_locator(self) -> PlatformSourceLocator:
        payload = self.model_dump(mode="python", by_alias=True)
        extended = {
            key: payload[key]
            for key in (
                "documentVersionId",
                "fileSha256",
                "cellRange",
                "tableId",
                "rowIndex",
                "columnIndex",
                "paragraphIndex",
                "bbox",
                "coordinateSpace",
                "rawTextSha256",
                "parserMethod",
            )
            if payload.get(key) is not None
        }
        payload["metadata"] = {**payload.get("metadata", {}), "parsingPlatform": extended}
        return PlatformSourceLocator.from_mapping(payload)


class DocumentVersion(ParsingContract):
    schema_version: str = "realytics-document-version-v2"
    document_version_id: str = Field(..., min_length=1)
    # Optional for backward-compatible decoding of the first Parser V2 draft.
    # Every repository-created version supplies this stable logical document ID.
    document_id: str | None = Field(None, min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = Field(None, min_length=1)
    archive_id: str = Field(..., min_length=1)
    object_version: str = Field(..., min_length=1)
    sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    source_hash: str | None = Field(None, pattern=SHA256_PATTERN)
    file_name: str = Field(..., min_length=1)
    original_file_name: str | None = Field(None, min_length=1)
    media_type: str = Field(..., min_length=1)
    size_bytes: int = Field(..., ge=0)
    version_number: int = Field(..., ge=1)
    source_authority: SourceAuthority = SourceAuthority.UNKNOWN
    supersedes_document_version_id: str | None = Field(None, min_length=1)
    ingested_at: datetime
    ingested_by: str = Field(..., min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_version_lineage(self) -> "DocumentVersion":
        if self.supersedes_document_version_id == self.document_version_id:
            raise ValueError("a document version cannot supersede itself")
        if self.source_hash is not None and self.source_hash != self.sha256:
            raise ValueError("sourceHash must equal sha256 for an immutable document version")
        if self.original_file_name is not None and self.original_file_name != self.file_name:
            raise ValueError("originalFileName must equal fileName")
        return self


class ParsingRun(ParsingContract):
    schema_version: str = "realytics-parsing-run-v2"
    parsing_run_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = Field(None, min_length=1)
    document_version_id: str = Field(..., min_length=1)
    execution_status: ExecutionStatus = ExecutionStatus.PENDING
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUESTED
    parser_version: str = Field(..., min_length=1)
    ontology_version: str = Field(..., min_length=1)
    prompt_version: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)
    profile_id: str = Field(..., min_length=1)
    profile_version: str = Field(..., min_length=1)
    plan_hash: str = Field(..., pattern=SHA256_PATTERN)
    input_hash: str = Field(..., pattern=SHA256_PATTERN)
    idempotency_key: str = Field(..., min_length=1)
    parent_parsing_run_id: str | None = Field(None, min_length=1)
    attempt_number: int = Field(1, ge=1)
    requested_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_codes: list[str] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> "ParsingRun":
        terminal = {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.COMPLETED_WITH_WARNINGS,
            ExecutionStatus.FAILED,
            ExecutionStatus.CANCELLED,
        }
        successful = {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.COMPLETED_WITH_WARNINGS,
        }
        if self.execution_status == ExecutionStatus.RUNNING and self.started_at is None:
            raise ValueError("a running parsing run requires startedAt")
        if self.execution_status in terminal and self.completed_at is None:
            raise ValueError("a terminal parsing run requires completedAt")
        if self.completed_at is not None and self.started_at is None:
            raise ValueError("completedAt requires startedAt")
        if self.started_at and self.completed_at and self.completed_at < self.started_at:
            raise ValueError("completedAt cannot precede startedAt")
        if self.approval_status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
            if self.execution_status not in successful:
                raise ValueError("approval can begin only after successful parsing execution")
        if self.parent_parsing_run_id == self.parsing_run_id:
            raise ValueError("a parsing run cannot be its own parent")
        return self


class CandidateSetSeal(ParsingContract):
    schema_version: Literal[CANDIDATE_SET_SEAL_SCHEMA_VERSION] = (
        CANDIDATE_SET_SEAL_SCHEMA_VERSION
    )
    seal_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    parsing_run_id: str = Field(..., min_length=1)
    document_version_id: str = Field(..., min_length=1)
    candidate_ids: list[str] = Field(...)
    candidate_count: int = Field(..., ge=0)
    conflict_ids: list[str] = Field(default_factory=list)
    conflict_count: int = Field(0, ge=0)
    expected_module_ids: list[str] = Field(default_factory=list)
    completed_module_ids: list[str] = Field(default_factory=list)
    unresolved_module_ids: list[str] = Field(default_factory=list)
    blocking_diagnostic_codes: list[str] = Field(default_factory=list)
    reconciliation_source_candidate_ids: list[str] = Field(
        default_factory=list
    )
    reconciliation_bound: bool = False
    collection_manifest_hash: str = Field(..., pattern=SHA256_PATTERN)
    candidate_set_hash: str = Field(..., pattern=SHA256_PATTERN)
    sealed_at: datetime
    sealed_by: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_membership_counts(self) -> "CandidateSetSeal":
        for name, values in (
            ("candidateIds", self.candidate_ids),
            ("conflictIds", self.conflict_ids),
            ("expectedModuleIds", self.expected_module_ids),
            ("completedModuleIds", self.completed_module_ids),
            ("unresolvedModuleIds", self.unresolved_module_ids),
            ("blockingDiagnosticCodes", self.blocking_diagnostic_codes),
            (
                "reconciliationSourceCandidateIds",
                self.reconciliation_source_candidate_ids,
            ),
        ):
            if any(not item.strip() for item in values):
                raise ValueError(f"{name} must not contain empty values")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        if self.candidate_count != len(self.candidate_ids):
            raise ValueError("candidateCount must match candidateIds")
        if self.conflict_count != len(self.conflict_ids):
            raise ValueError("conflictCount must match conflictIds")
        expected = set(self.expected_module_ids)
        completed = set(self.completed_module_ids)
        unresolved = set(self.unresolved_module_ids)
        if (
            not completed.issubset(expected)
            or not unresolved.issubset(expected)
            or completed.intersection(unresolved)
            or completed.union(unresolved) != expected
        ):
            raise ValueError(
                "completedModuleIds and unresolvedModuleIds must partition "
                "expectedModuleIds"
            )
        if self.reconciliation_bound and (
            sorted(self.reconciliation_source_candidate_ids)
            != sorted(self.candidate_ids)
        ):
            raise ValueError(
                "reconciliationSourceCandidateIds must match candidateIds "
                "when reconciliationBound is true"
            )
        if (
            not self.reconciliation_bound
            and self.reconciliation_source_candidate_ids
        ):
            raise ValueError(
                "reconciliationSourceCandidateIds requires reconciliationBound=true"
            )
        return self


class ParsedFactCandidate(ParsingContract):
    schema_version: str = "realytics-parsed-fact-candidate-v2"
    candidate_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = Field(None, min_length=1)
    parsing_run_id: str = Field(..., min_length=1)
    document_version_id: str = Field(..., min_length=1)
    field_key: str = Field(..., min_length=1, pattern=CANONICAL_FIELD_KEY_PATTERN)
    entity_id: str | None = Field(None, min_length=1)
    raw_value: JsonValue
    normalized_value: JsonValue
    unit: str | None = Field(None, min_length=1)
    origin_type: OriginType
    presence_status: PresenceStatus = PresenceStatus.PRESENT
    review_status: ReviewStatus = ReviewStatus.PENDING
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUESTED
    conflict_status: ConflictStatus = ConflictStatus.NONE
    confidence: float | None = Field(None, ge=0, le=1)
    authority: SourceAuthority = SourceAuthority.UNKNOWN
    effective_date: date | None = None
    as_of_date: date | None = None
    evidence: list[SourceLocator] = Field(default_factory=list)
    validation_codes: list[str] = Field(default_factory=list)
    parser_version: str = Field(..., min_length=1)
    ontology_version: str = Field(..., min_length=1)
    prompt_version: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)
    formula_id: str | None = Field(None, min_length=1)
    formula_version: str | None = Field(None, min_length=1)
    input_fact_ids: list[str] = Field(default_factory=list)
    calculation_run_id: str | None = Field(None, min_length=1)
    calculation_engine_version: str | None = Field(None, min_length=1)
    calculation_output_path: str | None = Field(None, min_length=1)
    calculation_output_hash: str | None = Field(None, pattern=SHA256_PATTERN)
    input_snapshot_hash: str | None = Field(None, pattern=SHA256_PATTERN)
    review_decision_id: str | None = Field(None, min_length=1)
    supersedes_candidate_id: str | None = Field(None, min_length=1)
    created_at: datetime
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    _CALCULATION_FIELDS: ClassVar[tuple[str, ...]] = (
        "formula_id",
        "formula_version",
        "input_fact_ids",
        "calculation_run_id",
        "calculation_engine_version",
        "calculation_output_path",
        "calculation_output_hash",
        "input_snapshot_hash",
    )

    @model_validator(mode="after")
    def validate_fact_invariants(self) -> "ParsedFactCandidate":
        if self.presence_status == PresenceStatus.PRESENT:
            if not self.evidence:
                raise ValueError("a present fact requires at least one source locator")
        elif self.normalized_value is not None:
            raise ValueError("a non-present fact must have normalizedValue=null")

        calculation_values: dict[str, Any] = {
            "formula_id": self.formula_id,
            "formula_version": self.formula_version,
            "input_fact_ids": self.input_fact_ids,
            "calculation_run_id": self.calculation_run_id,
            "calculation_engine_version": self.calculation_engine_version,
            "calculation_output_path": self.calculation_output_path,
            "calculation_output_hash": self.calculation_output_hash,
            "input_snapshot_hash": self.input_snapshot_hash,
        }
        if self.origin_type == OriginType.DERIVED:
            missing = [name for name, value in calculation_values.items() if not value]
            if missing:
                rendered = ", ".join(_to_camel(name) for name in missing)
                raise ValueError(f"a derived fact requires {rendered}")
            if len(set(self.input_fact_ids)) != len(self.input_fact_ids):
                raise ValueError("inputFactIds must not contain duplicates")
            if self.candidate_id in self.input_fact_ids:
                raise ValueError("a derived fact cannot use itself as an input")
        elif any(calculation_values.values()):
            raise ValueError("calculation metadata is allowed only for originType=derived")

        if len(set(self.validation_codes)) != len(self.validation_codes):
            raise ValueError("validationCodes must not contain duplicates")
        if self.approval_status == ApprovalStatus.APPROVED:
            if self.review_status != ReviewStatus.APPROVED or not self.review_decision_id:
                raise ValueError("an approved candidate requires an approved review and reviewDecisionId")
            if self.presence_status != PresenceStatus.PRESENT:
                raise ValueError("only a present fact can be approved")
            if self.conflict_status == ConflictStatus.OPEN:
                raise ValueError("a candidate with an open conflict cannot be approved")
        if self.approval_status == ApprovalStatus.REJECTED and self.review_status != ReviewStatus.REJECTED:
            raise ValueError("a rejected candidate must have reviewStatus=rejected")
        if self.supersedes_candidate_id == self.candidate_id:
            raise ValueError("a candidate cannot supersede itself")
        return self


class SourceConflict(ParsingContract):
    schema_version: str = "realytics-source-conflict-v2"
    conflict_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = Field(None, min_length=1)
    parsing_run_id: str | None = Field(None, min_length=1)
    source_parsing_run_ids: list[str] = Field(default_factory=list)
    field_key: str = Field(..., min_length=1, pattern=CANONICAL_FIELD_KEY_PATTERN)
    entity_id: str | None = Field(None, min_length=1)
    candidate_ids: list[str] = Field(..., min_length=2)
    conflict_type: str = Field(..., min_length=1)
    conflict_status: ConflictStatus = ConflictStatus.OPEN
    detected_at: datetime
    resolution_review_decision_id: str | None = Field(None, min_length=1)
    resolved_at: datetime | None = None
    notes: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_conflict_lifecycle(self) -> "SourceConflict":
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidateIds must contain at least two distinct candidates")
        if not self.source_parsing_run_ids and self.parsing_run_id:
            object.__setattr__(self, "source_parsing_run_ids", [self.parsing_run_id])
        if not self.source_parsing_run_ids:
            raise ValueError("SourceConflict requires sourceParsingRunIds")
        if len(set(self.source_parsing_run_ids)) != len(self.source_parsing_run_ids):
            raise ValueError("sourceParsingRunIds must not contain duplicates")
        if self.parsing_run_id and self.parsing_run_id not in self.source_parsing_run_ids:
            raise ValueError("parsingRunId must be one of sourceParsingRunIds")
        if self.conflict_status == ConflictStatus.NONE:
            raise ValueError("a SourceConflict cannot have conflictStatus=none")
        closed = self.conflict_status in {ConflictStatus.RESOLVED, ConflictStatus.WAIVED}
        if closed and (not self.resolution_review_decision_id or self.resolved_at is None):
            raise ValueError("resolved or waived conflicts require a review decision and resolvedAt")
        if not closed and (self.resolution_review_decision_id or self.resolved_at):
            raise ValueError("an open conflict cannot contain resolution fields")
        return self


class ReviewDecision(ParsingContract):
    schema_version: str = "realytics-review-decision-v2"
    review_decision_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = Field(None, min_length=1)
    candidate_id: str = Field(..., min_length=1)
    parsing_run_id: str | None = Field(None, min_length=1)
    document_id: str | None = Field(None, min_length=1)
    document_version_id: str | None = Field(None, min_length=1)
    source_hash: str | None = Field(None, pattern=SHA256_PATTERN)
    field_key: str | None = Field(None, min_length=1, pattern=CANONICAL_FIELD_KEY_PATTERN)
    conflict_ids: list[str] = Field(default_factory=list)
    action: ReviewAction
    resulting_review_status: ReviewStatus
    resulting_approval_status: ApprovalStatus
    review_status: ReviewStatus | None = None
    approval_status: ApprovalStatus | None = None
    reviewer_id: str = Field(..., min_length=1)
    decided_at: datetime
    decision_sequence: int | None = Field(None, ge=1)
    reason: str = Field(..., min_length=1)
    evidence: list[SourceLocator] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_decision_result(self) -> "ReviewDecision":
        expected = {
            ReviewAction.APPROVE: (ReviewStatus.APPROVED, ApprovalStatus.APPROVED),
            ReviewAction.REJECT: (ReviewStatus.REJECTED, ApprovalStatus.REJECTED),
            ReviewAction.REQUEST_REVISION: (ReviewStatus.NEEDS_REVISION, ApprovalStatus.PENDING),
        }[self.action]
        actual = (self.resulting_review_status, self.resulting_approval_status)
        if actual != expected:
            raise ValueError(
                f"action={self.action.value} requires resultingReviewStatus={expected[0].value} "
                f"and resultingApprovalStatus={expected[1].value}"
            )
        if self.review_status is not None and self.review_status != self.resulting_review_status:
            raise ValueError("reviewStatus must equal resultingReviewStatus")
        if self.approval_status is not None and self.approval_status != self.resulting_approval_status:
            raise ValueError("approvalStatus must equal resultingApprovalStatus")
        if len(set(self.conflict_ids)) != len(self.conflict_ids):
            raise ValueError("conflictIds must not contain duplicates")
        return self


class CanonicalSelection(ParsingContract):
    schema_version: str = "realytics-canonical-selection-v2"
    selection_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    field_key: str = Field(..., min_length=1, pattern=CANONICAL_FIELD_KEY_PATTERN)
    entity_id: str | None = Field(None, min_length=1)
    candidate_id: str = Field(..., min_length=1)
    review_decision_id: str | None = Field(None, min_length=1)
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    conflict_status: ConflictStatus = ConflictStatus.NONE
    normalized_value: JsonValue
    unit: str | None = Field(None, min_length=1)
    selection_version: int = Field(1, ge=1)
    selected_by: str = Field(..., min_length=1)
    selected_at: datetime
    effective_date: date | None = None
    as_of_date: date | None = None
    supersedes_selection_id: str | None = Field(None, min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_selection(self) -> "CanonicalSelection":
        if self.approval_status == ApprovalStatus.APPROVED:
            if not self.review_decision_id:
                raise ValueError("an approved canonical selection requires reviewDecisionId")
            if self.conflict_status == ConflictStatus.OPEN:
                raise ValueError("an approved canonical selection cannot have an open conflict")
        if self.supersedes_selection_id == self.selection_id:
            raise ValueError("a canonical selection cannot supersede itself")
        return self


class ParserVersionContext(ParsingContract):
    parser_version: str = Field(..., min_length=1)
    ontology_version: str = Field(..., min_length=1)
    prompt_version: str = Field(..., min_length=1)
    model_version: str = Field(..., min_length=1)


class SnapshotSourceDocument(ParsingContract):
    document_id: str = Field(..., min_length=1)
    document_version_id: str = Field(..., min_length=1)
    archive_id: str = Field(..., min_length=1)
    object_version: str = Field(..., min_length=1)
    source_hash: str = Field(..., pattern=SHA256_PATTERN)
    sha256: str = Field(..., pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash_alias(self) -> "SnapshotSourceDocument":
        if self.source_hash != self.sha256:
            raise ValueError("snapshot sourceHash must equal sha256")
        return self


class ApprovedInputItem(ParsingContract):
    field_key: str = Field(..., min_length=1, pattern=CANONICAL_FIELD_KEY_PATTERN)
    canonical_field: str = Field(..., min_length=1, pattern=CANONICAL_FIELD_KEY_PATTERN)
    entity_id: str | None = Field(None, min_length=1)
    normalized_value: JsonValue
    unit: str | None = Field(None, min_length=1)
    authority: SourceAuthority | None = None
    candidate_id: str = Field(..., min_length=1)
    selection_id: str = Field(..., min_length=1)
    review_decision_id: str = Field(..., min_length=1)
    parsing_run_id: str = Field(..., min_length=1)
    document_version_id: str = Field(..., min_length=1)
    source_hash: str = Field(..., pattern=SHA256_PATTERN)
    sha256: str = Field(..., pattern=SHA256_PATTERN)
    source_locator: SourceLocator
    origin_type: OriginType
    formula_id: str | None = Field(None, min_length=1)
    formula_version: str | None = Field(None, min_length=1)
    input_fact_ids: list[str] = Field(default_factory=list)
    calculation_run_id: str | None = Field(None, min_length=1)
    calculation_engine_version: str | None = Field(None, min_length=1)
    calculation_output_path: str | None = Field(None, min_length=1, pattern=r"^/")
    calculation_output_hash: str | None = Field(None, pattern=SHA256_PATTERN)
    input_snapshot_hash: str | None = Field(None, pattern=SHA256_PATTERN)
    effective_date: date | None = None
    as_of_date: date | None = None
    field_registry_version: Literal[FIELD_REGISTRY_VERSION]
    version_context: ParserVersionContext
    metadata: dict[str, JsonValue] = Field(...)

    @model_validator(mode="after")
    def validate_item_identity(self) -> "ApprovedInputItem":
        if self.field_key != self.canonical_field:
            raise ValueError("input item canonicalField must equal fieldKey")
        if self.source_hash != self.sha256:
            raise ValueError("input item sourceHash must equal sha256")
        if len(set(self.input_fact_ids)) != len(self.input_fact_ids):
            raise ValueError("input item inputFactIds must not contain duplicates")
        calculation_fields = (
            self.formula_id,
            self.formula_version,
            self.calculation_run_id,
            self.calculation_engine_version,
            self.calculation_output_path,
            self.calculation_output_hash,
            self.input_snapshot_hash,
        )
        if self.origin_type == OriginType.DERIVED:
            if not self.input_fact_ids or not all(calculation_fields):
                raise ValueError(
                    "a derived approved input requires complete calculation lineage"
                )
            locator_metadata = self.source_locator.metadata or {}
            if (
                self.source_locator.artifact_key != self.calculation_run_id
                or self.source_locator.json_path != self.calculation_output_path
                or str(locator_metadata.get("calculationRunId") or "")
                != self.calculation_run_id
                or str(locator_metadata.get("outputHash") or "")
                != self.calculation_output_hash
            ):
                raise ValueError(
                    "a derived approved input sourceLocator must bind the exact calculation output"
                )
        elif any(calculation_fields) or self.input_fact_ids:
            raise ValueError(
                "calculation lineage is allowed only for a derived approved input"
            )
        namespace_version = self.metadata.get(
            "canonicalFieldNamespaceVersion"
        )
        if not isinstance(namespace_version, str) or not namespace_version:
            raise ValueError(
                "approved input metadata requires canonicalFieldNamespaceVersion"
            )
        registry_issues = validate_candidate_against_registry(
            ontology_version=self.version_context.ontology_version,
            field_key=self.field_key,
            normalized_value=self.normalized_value,
            unit=self.unit,
            entity_id=self.entity_id,
            evidence=[
                self.source_locator.model_dump(
                    by_alias=True,
                    mode="json",
                    exclude_none=True,
                )
            ],
            presence_status="present",
            origin_type=self.origin_type.value,
            metadata=self.metadata,
        )
        blocking_codes = [
            item.code for item in registry_issues if item.blocking
        ]
        if blocking_codes:
            raise ValueError(
                "approved input violates the current field registry: "
                + ", ".join(blocking_codes)
            )
        return self


class ApprovedInputSnapshot(ParsingContract):
    schema_version: str = "realytics-approved-input-snapshot-v2"
    snapshot_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED
    validity_status: str | None = Field(None, pattern=r"^(active|invalidated)$")
    snapshot_version: int | None = Field(None, ge=1)
    purpose: str | None = Field(None, min_length=1)
    input_schema_version: str | None = Field(
        None,
        pattern=r"^realytics-parser-approved-inputs-v[1-9][0-9]*$",
    )
    field_registry_version: Literal[FIELD_REGISTRY_VERSION]
    canonical_field_namespace_version: str = Field(
        ...,
        pattern=r"^realytics-canonical-fact-namespace-v[1-9][0-9]*$",
    )
    selection_ids: list[str] | None = None
    selections: list[CanonicalSelection] = Field(..., min_length=1)
    ontology_version: str = Field(..., min_length=1)
    document_version_ids: list[str] | None = None
    source_hashes: list[str] | None = None
    source_documents: list[SnapshotSourceDocument] | None = None
    input_items: list[ApprovedInputItem] | None = None
    inputs: dict[str, JsonValue] | None = None
    inputs_by_entity: dict[str, dict[str, JsonValue]] | None = None
    version_contexts: list[ParserVersionContext] | None = None
    source_parsing_run_ids: list[str] = Field(..., min_length=1)
    approved_by: str = Field(..., min_length=1)
    approved_at: datetime
    supersedes_snapshot_id: str | None = Field(None, min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def allow_only_approved_selections(self) -> "ApprovedInputSnapshot":
        if self.validity_status == "invalidated":
            if self.approval_status != ApprovalStatus.SUPERSEDED:
                raise ValueError(
                    "an invalidated ApprovedInputSnapshot must have approvalStatus=superseded"
                )
        elif self.approval_status != ApprovalStatus.APPROVED:
            raise ValueError(
                "an active ApprovedInputSnapshot must have approvalStatus=approved"
            )
        unapproved = [
            selection.selection_id
            for selection in self.selections
            if selection.approval_status != ApprovalStatus.APPROVED
        ]
        if unapproved:
            raise ValueError(
                "ApprovedInputSnapshot accepts approved selections only: " + ", ".join(unapproved)
            )
        mismatched_selection_namespaces = [
            selection.selection_id
            for selection in self.selections
            if selection.metadata.get("canonicalFieldNamespaceVersion")
            != self.canonical_field_namespace_version
        ]
        if mismatched_selection_namespaces:
            raise ValueError(
                "selections must preserve the snapshot canonicalFieldNamespaceVersion: "
                + ", ".join(mismatched_selection_namespaces)
            )
        semantic_keys = [(item.field_key, item.entity_id) for item in self.selections]
        if len(set(semantic_keys)) != len(semantic_keys):
            raise ValueError("a snapshot cannot select the same fieldKey/entityId more than once")
        if len(set(self.source_parsing_run_ids)) != len(self.source_parsing_run_ids):
            raise ValueError("sourceParsingRunIds must not contain duplicates")
        if self.supersedes_snapshot_id == self.snapshot_id:
            raise ValueError("an approved snapshot cannot supersede itself")
        if self.snapshot_version is not None:
            if self.snapshot_version == 1 and self.supersedes_snapshot_id is not None:
                raise ValueError("snapshotVersion=1 cannot supersede another snapshot")
            if self.snapshot_version > 1 and self.supersedes_snapshot_id is None:
                raise ValueError("snapshotVersion>1 requires supersedesSnapshotId")

        selection_ids = [item.selection_id for item in self.selections]
        if self.selection_ids is not None:
            if len(set(self.selection_ids)) != len(self.selection_ids):
                raise ValueError("selectionIds must not contain duplicates")
            if self.selection_ids != selection_ids:
                raise ValueError("selectionIds must match selections in order")

        if self.source_hashes is not None:
            if len(set(self.source_hashes)) != len(self.source_hashes):
                raise ValueError("sourceHashes must not contain duplicates")
            if any(
                len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                for value in self.source_hashes
            ):
                raise ValueError("sourceHashes must contain SHA-256 hex digests")
        if self.document_version_ids is not None and len(set(self.document_version_ids)) != len(
            self.document_version_ids
        ):
            raise ValueError("documentVersionIds must not contain duplicates")

        if self.source_documents is not None:
            if len({item.document_version_id for item in self.source_documents}) != len(
                self.source_documents
            ):
                raise ValueError("sourceDocuments must not repeat a documentVersionId")
            source_document_version_ids = sorted(
                {item.document_version_id for item in self.source_documents}
            )
            source_document_hashes = sorted({item.source_hash for item in self.source_documents})
            if self.document_version_ids is not None and sorted(self.document_version_ids) != source_document_version_ids:
                raise ValueError("documentVersionIds must match sourceDocuments")
            if self.source_hashes is not None and sorted(self.source_hashes) != source_document_hashes:
                raise ValueError("sourceHashes must match sourceDocuments")

        if self.input_items is not None:
            input_selection_ids = [item.selection_id for item in self.input_items]
            if input_selection_ids != selection_ids:
                raise ValueError("inputItems must match selections in order")
            selections_by_id = {
                selection.selection_id: selection for selection in self.selections
            }
            for item in self.input_items:
                selection = selections_by_id[item.selection_id]
                if (
                    item.field_key != selection.field_key
                    or item.entity_id != selection.entity_id
                    or item.candidate_id != selection.candidate_id
                    or item.review_decision_id != selection.review_decision_id
                    or item.normalized_value != selection.normalized_value
                    or item.unit != selection.unit
                    or item.metadata != selection.metadata
                ):
                    raise ValueError(
                        "inputItems must preserve their canonical selection identity and value"
                    )
            if self.document_version_ids is not None and any(
                item.document_version_id not in self.document_version_ids
                for item in self.input_items
            ):
                raise ValueError("inputItems reference undeclared documentVersionIds")
            if self.source_hashes is not None and any(
                item.source_hash not in self.source_hashes for item in self.input_items
            ):
                raise ValueError("inputItems reference undeclared sourceHashes")
            if any(
                item.field_registry_version != self.field_registry_version
                for item in self.input_items
            ):
                raise ValueError("inputItems must use the snapshot fieldRegistryVersion")
            input_run_ids = sorted({item.parsing_run_id for item in self.input_items})
            if sorted(self.source_parsing_run_ids) != input_run_ids:
                raise ValueError("sourceParsingRunIds must match inputItems")

        expected_inputs = {
            item.field_key: item.normalized_value
            for item in self.selections
            if item.entity_id is None
        }
        if self.inputs is not None and self.inputs != expected_inputs:
            raise ValueError("inputs must match non-entity canonical selections")
        expected_inputs_by_entity: dict[str, dict[str, JsonValue]] = {}
        for item in self.selections:
            if item.entity_id is None:
                continue
            expected_inputs_by_entity.setdefault(item.entity_id, {})[
                item.field_key
            ] = item.normalized_value
        if (
            self.inputs_by_entity is not None
            and self.inputs_by_entity != expected_inputs_by_entity
        ):
            raise ValueError("inputsByEntity must match entity-scoped canonical selections")

        if self.version_contexts is not None:
            if not self.version_contexts:
                raise ValueError("versionContexts cannot be empty when supplied")
            if any(item.ontology_version != self.ontology_version for item in self.version_contexts):
                raise ValueError("versionContexts must use the snapshot ontologyVersion")
            if self.input_items is not None:
                declared_contexts = {
                    json.dumps(
                        item.model_dump(mode="json", by_alias=True),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    for item in self.version_contexts
                }
                input_contexts = {
                    json.dumps(
                        item.version_context.model_dump(mode="json", by_alias=True),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    for item in self.input_items
                }
                if declared_contexts != input_contexts:
                    raise ValueError("versionContexts must match inputItems")
        return self


class ValidationFinding(ParsingContract):
    schema_version: str = "realytics-validation-finding-v2"
    finding_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str | None = Field(None, min_length=1)
    parsing_run_id: str = Field(..., min_length=1)
    candidate_id: str | None = Field(None, min_length=1)
    code: str = Field(..., pattern=r"^[A-Z][A-Z0-9_]*$")
    severity: FindingSeverity
    message: str = Field(..., min_length=1)
    blocking: bool = False
    evidence: list[SourceLocator] = Field(default_factory=list)
    created_at: datetime
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ValidationFindingEvent(ParsingContract):
    schema_version: str = "realytics-validation-finding-event-v1"
    finding_event_id: str = Field(..., min_length=1)
    finding_id: str = Field(..., min_length=1)
    tenant_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    parsing_run_id: str = Field(..., min_length=1)
    sequence_number: int = Field(..., ge=1)
    action: FindingLifecycleAction
    actor_id: str = Field(..., min_length=1)
    occurred_at: datetime
    reason: str = Field(..., min_length=1)
    event_hash: str = Field(..., pattern=SHA256_PATTERN)


CONTRACT_MODELS: tuple[type[BaseModel], ...] = (
    DocumentVersion,
    ParsingRun,
    CandidateSetSeal,
    SourceLocator,
    ParsedFactCandidate,
    SourceConflict,
    ReviewDecision,
    CanonicalSelection,
    ApprovedInputSnapshot,
    ValidationFinding,
    ValidationFindingEvent,
)


def generate_contract_json_schemas() -> dict[str, dict[str, Any]]:
    """Return standalone, camelCase JSON Schemas for registry publication."""

    return {
        model.__name__: model.model_json_schema(by_alias=True, mode="validation")
        for model in CONTRACT_MODELS
    }
