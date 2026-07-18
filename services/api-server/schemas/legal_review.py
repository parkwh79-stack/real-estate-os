"""Contracts for the Legal Pod review ledger and source locators."""

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field


LegalSourceAuthority: TypeAlias = Literal[
    "law_go_kr",
    "supreme_court",
    "national_tax_service",
    "tenant_document",
    "human_counsel",
    "unknown",
]
LegalTransport: TypeAlias = Literal[
    "local_verified_snapshot",
    "law_go_kr_direct_api",
    "self_hosted_mcp",
    "remote_mcp",
    "tenant_object_storage",
    "caller_context",
    "unknown",
]
LegalVerificationStatus: TypeAlias = Literal[
    "verified",
    "candidate",
    "failed",
    "stale",
    "not_verified",
]


class LegalEvidenceLocator(BaseModel):
    sourceType: Literal[
        "document_page", "excel_cell", "api_row", "land_evidence", "artifact", "legal_review", "unknown"
    ] = "unknown"
    sourceDocumentId: str | None = None
    evidenceId: str | None = None
    sourceUrl: str | None = None
    pageNumber: int | None = Field(default=None, ge=1)
    sheetName: str | None = None
    cell: str | None = None
    rowNumber: int | None = Field(default=None, ge=1)
    apiRowId: str | None = None
    artifactKey: str | None = None
    excerpt: str | None = None
    sourceAuthority: LegalSourceAuthority = "unknown"
    transport: LegalTransport = "unknown"
    resourceId: str | None = None
    resourceVersionId: str | None = None
    provisionId: str | None = None
    sourceFetchId: str | None = None
    officialId: str | None = None
    contentHash: str | None = Field(
        default=None,
        pattern=r"^(?:sha256:)?[0-9a-f]{64}$",
    )
    asOfDate: str | None = None
    effectiveDate: str | None = None
    fetchedAt: str | None = None
    verifiedAt: str | None = None
    verificationStatus: LegalVerificationStatus = "not_verified"


class LegalPodRunRequest(BaseModel):
    # Caller-supplied legal context is an untrusted discovery candidate.  The
    # server must resolve it through the Legal Intelligence Core before it can
    # become approval-grade evidence.
    lawContext: dict[str, Any] | None = None
    parsedDocuments: list[dict[str, Any]] = Field(default_factory=list)
    reviewId: str | None = None
