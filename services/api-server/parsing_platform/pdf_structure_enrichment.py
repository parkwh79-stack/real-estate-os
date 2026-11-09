from __future__ import annotations

"""Deterministic PDF table and semantic-section enrichment.

The outputs in this module are structural evidence only. They never promote
document text into an approved fact, even when the native PDF library reports
a high-confidence table boundary.
"""

import hashlib
import inspect
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

import fitz

from parsing_platform.adapters import (
    MAX_CHUNK_TEXT_CHARS,
    MAX_EXTRACTION_CHUNKS,
    MAX_PDF_PAGES,
    ArchivedDocument,
    ExtractionArtifact,
    ExtractionChunk,
)
from parsing_platform.document_taxonomy import (
    DOCUMENT_TAXONOMY_VERSION,
    DOCUMENT_TYPE_SCOPE_ALL,
)
from parsing_platform.registry import ParserModuleManifest


PDF_NATIVE_TABLE_SCHEMA_VERSION = "realytics-pdf-native-table-v2"
SEMANTIC_SECTION_SCHEMA_VERSION = "realytics-semantic-section-index-v2"
_DEFAULT_PAGE_TABLE_FINDER = object()


PDF_NATIVE_TABLE_MANIFEST = ParserModuleManifest(
    module_id="pdf_native_table_extraction",
    version="2.0.1",
    capabilities=("document.pdf_table_structure",),
    document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
    document_type_scope=DOCUMENT_TYPE_SCOPE_ALL,
    requires=("document.safe",),
    conditional_provides=("document.pdf_tables",),
    execution_class="deterministic",
    input_schema="realytics-pdf-native-table-input-v2",
    output_schema=PDF_NATIVE_TABLE_SCHEMA_VERSION,
    required_locator_precision="exact",
    data_classification="restricted",
    max_output_bytes=20 * 1024 * 1024,
    metadata={
        "deterministic": True,
        "authoritative": False,
        "factPromotionAllowed": False,
        "capabilityPolicy": "explicit_abstention_when_unavailable",
    },
)


SEMANTIC_SECTION_MANIFEST = ParserModuleManifest(
    module_id="semantic_section_indexing",
    version="2.0.0",
    capabilities=("document.semantic_structure",),
    document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
    document_type_scope=DOCUMENT_TYPE_SCOPE_ALL,
    requires=("document.structure",),
    conditional_provides=("document.semantic_sections",),
    execution_class="deterministic",
    input_schema="realytics-semantic-section-input-v2",
    output_schema=SEMANTIC_SECTION_SCHEMA_VERSION,
    required_locator_precision="exact",
    data_classification="restricted",
    max_output_bytes=10 * 1024 * 1024,
    metadata={
        "deterministic": True,
        "authoritative": False,
        "storesOriginalText": False,
        "factPromotionAllowed": False,
    },
)


class PdfTableExtractionError(ValueError):
    """Native table extraction could not produce exact cell locators."""


class NativePdfTableExtractor:
    """Extract native PDF table cells when PyMuPDF exposes ``find_tables``.

    ``page_table_finder=None`` intentionally disables the optional capability
    and is useful for deployments where the native table implementation is not
    installed. In that case extraction returns an explicit abstention artifact.
    """

    adapter_id = "pdf_native_table_structure"
    adapter_version = "2.0.1"
    manifest = PDF_NATIVE_TABLE_MANIFEST

    def __init__(
        self,
        *,
        page_table_finder: Callable[[Any], Any] | None | object = (
            _DEFAULT_PAGE_TABLE_FINDER
        ),
    ) -> None:
        self._page_table_finder = page_table_finder

    def supports(self, document: ArchivedDocument) -> bool:
        return (
            document.extension == ".pdf"
            or str(document.content_type or "").casefold()
            == "application/pdf"
        )

    def extract(self, document: ArchivedDocument) -> ExtractionArtifact:
        if not self.supports(document):
            raise ValueError("native PDF table extraction requires a PDF source")

        pdf = fitz.open(stream=document.content, filetype="pdf")
        page_count = pdf.page_count
        try:
            if pdf.needs_pass:
                raise ValueError("Encrypted PDF cannot be parsed")
            if page_count > MAX_PDF_PAGES:
                raise ValueError(
                    "PDF page count exceeds the native table extraction limit"
                )
            if self._page_table_finder is None:
                return _table_abstention_artifact(
                    document=document,
                    page_count=page_count,
                    code="PDF_TABLE_CAPABILITY_UNAVAILABLE",
                    message=(
                        "The native PDF library does not expose an approved "
                        "table extraction capability."
                    ),
                    capability_status="unavailable",
                )

            chunks: list[ExtractionChunk] = []
            table_count = 0
            for page_number, page in enumerate(pdf, start=1):
                page_bbox = _normalized_bbox(tuple(page.rect))
                if page_bbox is None:
                    return _table_abstention_artifact(
                        document=document,
                        page_count=page_count,
                        code="PDF_PAGE_GEOMETRY_INVALID",
                        message=(
                            "The PDF page geometry is invalid; no table cells "
                            "were emitted."
                        ),
                        capability_status="available",
                    )
                finder = self._finder_for_page(page)
                if finder is None:
                    return _table_abstention_artifact(
                        document=document,
                        page_count=page_count,
                        code="PDF_TABLE_CAPABILITY_UNAVAILABLE",
                        message=(
                            "The native PDF library does not expose "
                            "Page.find_tables in this runtime."
                        ),
                        capability_status="unavailable",
                    )
                try:
                    result = finder()
                except (AttributeError, NotImplementedError) as exc:
                    return _table_abstention_artifact(
                        document=document,
                        page_count=page_count,
                        code="PDF_TABLE_CAPABILITY_UNAVAILABLE",
                        message=(
                            "The native PDF table capability is unavailable "
                            "for this runtime."
                        ),
                        capability_status="unavailable",
                        exception_type=type(exc).__name__,
                    )
                except Exception as exc:
                    return _table_abstention_artifact(
                        document=document,
                        page_count=page_count,
                        code="PDF_TABLE_EXTRACTION_FAILED",
                        message=(
                            "Native PDF table extraction failed and abstained "
                            "without returning partial table evidence."
                        ),
                        capability_status="available",
                        exception_type=type(exc).__name__,
                    )

                tables = getattr(result, "tables", None)
                if not isinstance(tables, (list, tuple)):
                    return _table_abstention_artifact(
                        document=document,
                        page_count=page_count,
                        code="PDF_TABLE_CAPABILITY_PROTOCOL_UNSUPPORTED",
                        message=(
                            "The native PDF table result does not expose the "
                            "expected table collection."
                        ),
                        capability_status="unavailable",
                    )
                for page_table_index, table in enumerate(tables, start=1):
                    table_count += 1
                    try:
                        chunks.extend(
                            self._table_chunks(
                                document=document,
                                page_number=page_number,
                                page_bbox=page_bbox,
                                page_table_index=page_table_index,
                                table=table,
                                existing_chunk_count=len(chunks),
                            )
                        )
                    except PdfTableExtractionError as exc:
                        return _table_abstention_artifact(
                            document=document,
                            page_count=page_count,
                            code="PDF_TABLE_LOCATOR_INCOMPLETE",
                            message=str(exc),
                            capability_status="available",
                        )
        finally:
            pdf.close()

        status = "completed" if table_count else "available_no_tables"
        return ExtractionArtifact(
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            document_id=document.archive_id,
            chunks=tuple(chunks),
            complete=True,
            metadata={
                "schemaVersion": PDF_NATIVE_TABLE_SCHEMA_VERSION,
                "status": status,
                "abstained": False,
                "capability": _native_table_capability("available"),
                "pageCount": page_count,
                "tableCount": table_count,
                "cellCount": len(chunks),
                "authorityStatus": "structural_evidence_only",
                "approvalEligible": False,
                "reportEligible": False,
                "factPromotionAllowed": False,
            },
        )

    def _finder_for_page(self, page: Any) -> Callable[[], Any] | None:
        if self._page_table_finder is _DEFAULT_PAGE_TABLE_FINDER:
            native = getattr(page, "find_tables", None)
            if not callable(native):
                return None
            # PyMuPDF 2.x defaults to layout-gated detection. This adapter's
            # contract is deterministic native grid geometry, including small
            # tables that the layout classifier can reject. Preserve that
            # contract on both old and new supported library signatures.
            parameters = inspect.signature(native).parameters
            if any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            ):
                # Both 1.27 and 2.x expose **kwargs on Page. Check the actual
                # delegated implementation, not the wrapper's acceptance alone.
                implementation = getattr(getattr(fitz, "table", None), "find_tables", None)
                parameters = inspect.signature(implementation).parameters if callable(implementation) else {}
            if "use_layout" in parameters:
                return lambda: native(use_layout=False)
            return native
        configured = self._page_table_finder
        if callable(configured):
            return lambda: configured(page)
        return None

    def _table_chunks(
        self,
        *,
        document: ArchivedDocument,
        page_number: int,
        page_bbox: list[float],
        page_table_index: int,
        table: Any,
        existing_chunk_count: int,
    ) -> list[ExtractionChunk]:
        extracted = table.extract()
        rows = getattr(table, "rows", None)
        row_count = getattr(table, "row_count", None)
        column_count = getattr(table, "col_count", None)
        if (
            not isinstance(extracted, list)
            or not isinstance(rows, list)
            or not isinstance(row_count, int)
            or not isinstance(column_count, int)
            or row_count < 1
            or column_count < 1
            or isinstance(row_count, bool)
            or isinstance(column_count, bool)
            or len(rows) != row_count
            or len(extracted) != row_count
            or any(
                not isinstance(row, (list, tuple))
                or len(row) != column_count
                for row in extracted
            )
        ):
            raise PdfTableExtractionError(
                "Native PDF table structure is incomplete; no cells were emitted."
            )
        table_bbox = _normalized_bbox(getattr(table, "bbox", None))
        if table_bbox is None:
            raise PdfTableExtractionError(
                "Native PDF table bbox is missing; no cells were emitted."
            )
        if not _bbox_contains(page_bbox, table_bbox):
            raise PdfTableExtractionError(
                "Native PDF table bbox falls outside the page geometry; "
                "no cells were emitted."
            )

        table_id = f"page-{page_number}-table-{page_table_index}"
        chunks: list[ExtractionChunk] = []
        for row_offset in range(row_count):
            values = extracted[row_offset]
            native_cells = getattr(rows[row_offset], "cells", None)
            if (
                not isinstance(native_cells, (list, tuple))
                or len(native_cells) != column_count
            ):
                raise PdfTableExtractionError(
                    f"Native cell locators are missing for {table_id}."
                )
            for column_offset in range(column_count):
                value = values[column_offset]
                text = "" if value is None else str(value)
                bbox = _normalized_bbox(native_cells[column_offset])
                # A None bbox is a normal placeholder for an empty merged cell.
                # A populated value without a bbox cannot be exact evidence.
                if bbox is None and not text:
                    continue
                if bbox is None:
                    raise PdfTableExtractionError(
                        f"Native bbox is missing for {table_id}, "
                        f"row {row_offset + 1}, column {column_offset + 1}."
                    )
                if (
                    not _bbox_contains(page_bbox, bbox)
                    or not _bbox_contains(table_bbox, bbox)
                ):
                    raise PdfTableExtractionError(
                        f"Native bbox falls outside its table or page at "
                        f"{table_id}, row {row_offset + 1}, "
                        f"column {column_offset + 1}."
                    )
                if len(text) > MAX_CHUNK_TEXT_CHARS:
                    raise PdfTableExtractionError(
                        f"Native table cell exceeds the text limit at "
                        f"{table_id}, row {row_offset + 1}, "
                        f"column {column_offset + 1}."
                    )
                if (
                    existing_chunk_count + len(chunks)
                    >= MAX_EXTRACTION_CHUNKS
                ):
                    raise PdfTableExtractionError(
                        "Native PDF table cell count exceeds the extraction limit."
                    )
                raw_text_hash = hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest()
                chunks.append(
                    ExtractionChunk(
                        chunk_id=(
                            f"{table_id}-row-{row_offset + 1}"
                            f"-column-{column_offset + 1}"
                        ),
                        kind="pdf_table_cell",
                        text=text,
                        source_locator={
                            "documentId": document.archive_id,
                            "documentName": document.filename,
                            "pageNumber": page_number,
                            "tableId": table_id,
                            "rowIndex": row_offset + 1,
                            "columnIndex": column_offset + 1,
                            "sourceType": "document_table_cell_bbox",
                            "metadata": {
                                "bbox": bbox,
                                "tableBBox": table_bbox,
                                "pageBBox": page_bbox,
                                "bboxCoordinateSystem": (
                                    "pdf_points_top_left"
                                ),
                            },
                        },
                        metadata={
                            "rawTextSha256": raw_text_hash,
                            "authorityStatus": "candidate_evidence",
                            "approvalEligible": False,
                            "reportEligible": False,
                            "factPromotionAllowed": False,
                        },
                    )
                )
        return chunks


@dataclass(frozen=True)
class SemanticChunkReference:
    chunk_id: str
    source_locator: dict[str, Any]
    raw_text_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunkId": self.chunk_id,
            "sourceLocator": deepcopy(self.source_locator),
            "rawTextSha256": self.raw_text_sha256,
        }


@dataclass(frozen=True)
class SemanticSection:
    section_id: str
    ordinal: int
    semantic_role: str
    heading_chunk_id: str | None
    content_hash: str
    chunk_references: tuple[SemanticChunkReference, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sectionId": self.section_id,
            "ordinal": self.ordinal,
            "semanticRole": self.semantic_role,
            "headingChunkId": self.heading_chunk_id,
            "contentHash": self.content_hash,
            "chunkCount": len(self.chunk_references),
            "chunkReferences": [
                reference.to_dict() for reference in self.chunk_references
            ],
            "authorityStatus": "structural_index_only",
            "approvalEligible": False,
            "reportEligible": False,
            "factPromotionAllowed": False,
        }


@dataclass(frozen=True)
class SemanticSectionArtifact:
    document_id: str
    sections: tuple[SemanticSection, ...]
    source_artifact_hash: str
    complete: bool
    diagnostics: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": SEMANTIC_SECTION_SCHEMA_VERSION,
            "documentId": self.document_id,
            "complete": self.complete,
            "sectionCount": len(self.sections),
            "sourceArtifactHash": self.source_artifact_hash,
            "sections": [section.to_dict() for section in self.sections],
            "diagnostics": [dict(item) for item in self.diagnostics],
            "metadata": {
                **dict(self.metadata),
                "storesOriginalText": False,
                "authorityStatus": "structural_index_only",
                "approvalEligible": False,
                "reportEligible": False,
                "factPromotionAllowed": False,
            },
        }


class DeterministicSemanticSectionIndexer:
    """Group structural chunks without copying source text into the index."""

    manifest = SEMANTIC_SECTION_MANIFEST
    indexer_id = "deterministic_semantic_section_indexer"
    indexer_version = "2.0.0"

    def index(
        self,
        artifact: ExtractionArtifact,
    ) -> SemanticSectionArtifact:
        source_artifact_hash = _stable_hash(
            artifact.to_dict(include_text=False)
        )
        chunks = list(artifact.chunks)
        chunk_ids = [str(chunk.chunk_id or "").strip() for chunk in chunks]
        if any(not chunk_id for chunk_id in chunk_ids):
            raise ValueError("semantic section indexing requires chunk IDs")
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError(
                "semantic section indexing requires unique chunk IDs"
            )
        if not chunks:
            return SemanticSectionArtifact(
                document_id=artifact.document_id,
                sections=(),
                source_artifact_hash=source_artifact_hash,
                complete=artifact.complete,
                diagnostics=(
                    {
                        "code": "SEMANTIC_SECTION_INPUT_EMPTY",
                        "severity": "info",
                        "blocking": False,
                        "message": (
                            "No structural chunks were available for semantic "
                            "section indexing."
                        ),
                    },
                ),
                metadata={
                    "indexerId": self.indexer_id,
                    "indexerVersion": self.indexer_version,
                    "abstained": True,
                },
            )

        heading_roles = [_heading_role(chunk) for chunk in chunks]
        grouped: list[tuple[str, str | None, list[ExtractionChunk]]] = []
        if not any(heading_roles):
            grouped.append(("unclassified", None, chunks))
        else:
            current_role = "preamble"
            heading_chunk_id: str | None = None
            current: list[ExtractionChunk] = []
            for chunk, heading_role in zip(chunks, heading_roles):
                if heading_role is not None:
                    if current:
                        grouped.append(
                            (current_role, heading_chunk_id, current)
                        )
                    current_role = heading_role
                    heading_chunk_id = chunk.chunk_id
                    current = [chunk]
                else:
                    current.append(chunk)
            if current:
                grouped.append((current_role, heading_chunk_id, current))

        sections: list[SemanticSection] = []
        for ordinal, (
            semantic_role,
            heading_chunk_id,
            section_chunks,
        ) in enumerate(grouped, start=1):
            references = tuple(
                SemanticChunkReference(
                    chunk_id=chunk.chunk_id,
                    source_locator=_locator_reference(
                        chunk.source_locator
                    ),
                    raw_text_sha256=hashlib.sha256(
                        chunk.text.encode("utf-8")
                    ).hexdigest(),
                )
                for chunk in section_chunks
            )
            content_hash = _stable_hash(
                [reference.to_dict() for reference in references]
            )
            section_id = "semantic-section-" + _stable_hash(
                {
                    "documentId": artifact.document_id,
                    "ordinal": ordinal,
                    "semanticRole": semantic_role,
                    "headingChunkId": heading_chunk_id,
                    "contentHash": content_hash,
                }
            )[:24]
            sections.append(
                SemanticSection(
                    section_id=section_id,
                    ordinal=ordinal,
                    semantic_role=semantic_role,
                    heading_chunk_id=heading_chunk_id,
                    content_hash=content_hash,
                    chunk_references=references,
                )
            )

        return SemanticSectionArtifact(
            document_id=artifact.document_id,
            sections=tuple(sections),
            source_artifact_hash=source_artifact_hash,
            complete=artifact.complete,
            metadata={
                "indexerId": self.indexer_id,
                "indexerVersion": self.indexer_version,
                "abstained": False,
                "sourceExtractionComplete": artifact.complete,
            },
        )


def _table_abstention_artifact(
    *,
    document: ArchivedDocument,
    page_count: int,
    code: str,
    message: str,
    capability_status: str,
    exception_type: str | None = None,
) -> ExtractionArtifact:
    diagnostic: dict[str, Any] = {
        "code": code,
        "severity": "high",
        "blocking": True,
        "message": message,
    }
    if exception_type:
        diagnostic["exceptionType"] = exception_type
    return ExtractionArtifact(
        adapter_id=NativePdfTableExtractor.adapter_id,
        adapter_version=NativePdfTableExtractor.adapter_version,
        document_id=document.archive_id,
        chunks=(),
        complete=False,
        warnings=(code,),
        metadata={
            "schemaVersion": PDF_NATIVE_TABLE_SCHEMA_VERSION,
            "status": "abstained",
            "abstained": True,
            "capability": _native_table_capability(capability_status),
            "pageCount": page_count,
            "tableCount": 0,
            "cellCount": 0,
            "diagnostics": [diagnostic],
            "authorityStatus": "structural_evidence_only",
            "approvalEligible": False,
            "reportEligible": False,
            "factPromotionAllowed": False,
        },
    )


def _native_table_capability(status: str) -> dict[str, Any]:
    return {
        "name": "pymupdf.page.find_tables",
        "status": status,
        "library": "PyMuPDF",
        "libraryVersion": str(
            getattr(fitz, "VersionBind", None)
            or getattr(fitz, "__version__", "unknown")
        ),
    }


def _normalized_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = [round(float(item), 3) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in bbox):
        return None
    if bbox[2] < bbox[0] or bbox[3] < bbox[1]:
        return None
    return bbox


def _bbox_contains(
    outer: list[float],
    inner: list[float],
    *,
    tolerance: float = 0.01,
) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


_HEADING_KINDS = frozenset(
    {
        "pdf_text_block",
        "docx_paragraph",
        "text_line",
        "document_heading",
        "pdf_heading",
        "docx_heading",
    }
)
_NUMBERED_HEADING = re.compile(
    r"^(?:"
    r"제\s*\d+\s*[장절편]\b"
    r"|(?:\d+(?:\.\d+){0,5})[.)]?\s+\S"
    r"|[IVXLC]+[.)]\s+\S"
    r"|[A-Z][.)]\s+\S"
    r")",
    flags=re.IGNORECASE,
)


def _heading_role(chunk: ExtractionChunk) -> str | None:
    metadata = (
        chunk.metadata if isinstance(chunk.metadata, Mapping) else {}
    )
    explicit = str(
        metadata.get("semanticRole")
        or metadata.get("semantic_role")
        or ""
    ).strip().casefold()
    if explicit in {"heading", "section_heading", "title"}:
        return "explicit_heading"
    if chunk.kind not in _HEADING_KINDS:
        return None
    normalized = " ".join(str(chunk.text or "").split())
    if (
        not normalized
        or len(normalized) > 120
        or "\n" in str(chunk.text or "").strip()
    ):
        return None
    if chunk.kind.endswith("_heading"):
        return "explicit_heading"
    if _NUMBERED_HEADING.match(normalized):
        return "numbered_heading"
    return None


_LOCATOR_FIELDS = (
    "documentId",
    "documentName",
    "documentVersionId",
    "fileSha256",
    "pageNumber",
    "pageLabel",
    "bbox",
    "coordinateSpace",
    "sourceType",
    "tableId",
    "rowIndex",
    "columnIndex",
    "paragraphIndex",
    "sheetName",
    "cellReference",
    "cellRange",
    "jsonPath",
    "evidenceId",
    "parserMethod",
)
_LOCATOR_METADATA_FIELDS = (
    "bbox",
    "tableBBox",
    "pageBBox",
    "bboxCoordinateSystem",
)


def _locator_reference(locator: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(locator, Mapping):
        raise ValueError(
            "semantic section chunk requires a source locator mapping"
        )
    reference = {
        key: deepcopy(locator[key])
        for key in _LOCATOR_FIELDS
        if locator.get(key) is not None
    }
    metadata = locator.get("metadata")
    if isinstance(metadata, Mapping):
        retained = {
            key: deepcopy(metadata[key])
            for key in _LOCATOR_METADATA_FIELDS
            if metadata.get(key) is not None
        }
        if retained:
            reference["metadata"] = retained
    if not reference:
        raise ValueError(
            "semantic section chunk requires a non-empty source locator"
        )
    return reference


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "PDF_NATIVE_TABLE_MANIFEST",
    "PDF_NATIVE_TABLE_SCHEMA_VERSION",
    "SEMANTIC_SECTION_MANIFEST",
    "SEMANTIC_SECTION_SCHEMA_VERSION",
    "DeterministicSemanticSectionIndexer",
    "NativePdfTableExtractor",
    "PdfTableExtractionError",
    "SemanticChunkReference",
    "SemanticSection",
    "SemanticSectionArtifact",
]
