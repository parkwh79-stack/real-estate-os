"""Contracts for the Legal Pod review ledger and source locators."""

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class LegalPodRunRequest(BaseModel):
    lawContext: dict[str, Any] | None = None
    parsedDocuments: list[dict[str, Any]] = Field(default_factory=list)
    reviewId: str | None = None

