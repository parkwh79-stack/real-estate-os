from __future__ import annotations

import hashlib
import json

import fitz
import pytest

from parsing_platform.adapters import (
    ArchivedDocument,
    ExtractionArtifact,
    ExtractionChunk,
    bind_artifact_to_document_version,
)
from parsing_platform.pdf_structure_enrichment import (
    PDF_NATIVE_TABLE_MANIFEST,
    SEMANTIC_SECTION_MANIFEST,
    DeterministicSemanticSectionIndexer,
    NativePdfTableExtractor,
)


def _document(content: bytes, filename: str = "table.pdf") -> ArchivedDocument:
    return ArchivedDocument(
        project_id="project-pdf",
        tenant_id="tenant-pdf",
        archive_id=hashlib.sha256(content).hexdigest(),
        filename=filename,
        content_type="application/pdf",
        content=content,
        object_key=f"project-pdf/{filename}",
        object_version="version-1",
    )


def _table_pdf_bytes() -> bytes:
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    for x in (50, 150, 250):
        page.draw_line((x, 50), (x, 150), color=(0, 0, 0), width=1)
    for y in (50, 100, 150):
        page.draw_line((50, y), (250, y), color=(0, 0, 0), width=1)
    for text, x, y in (
        ("Product", 65, 80),
        ("Revenue", 165, 80),
        ("Apartment", 65, 130),
        ("100", 165, 130),
    ):
        page.insert_text((x, y), text)
    content = pdf.tobytes()
    pdf.close()
    return content


@pytest.mark.skipif(
    not hasattr(fitz.Page, "find_tables"),
    reason="PyMuPDF native table capability is unavailable",
)
def test_native_pdf_table_cells_preserve_exact_structural_locator() -> None:
    document = _document(_table_pdf_bytes())

    artifact = NativePdfTableExtractor().extract(document)

    assert artifact.complete is True
    assert artifact.metadata["status"] == "completed"
    assert artifact.metadata["capability"]["status"] == "available"
    assert artifact.metadata["tableCount"] == 1
    assert len(artifact.chunks) == 4
    revenue = artifact.chunks[1]
    assert revenue.text == "Revenue"
    assert revenue.kind == "pdf_table_cell"
    assert revenue.source_locator["pageNumber"] == 1
    assert revenue.source_locator["tableId"] == "page-1-table-1"
    assert revenue.source_locator["rowIndex"] == 1
    assert revenue.source_locator["columnIndex"] == 2
    assert len(revenue.source_locator["metadata"]["bbox"]) == 4
    assert (
        revenue.source_locator["metadata"]["bboxCoordinateSystem"]
        == "pdf_points_top_left"
    )
    assert revenue.metadata["approvalEligible"] is False
    assert revenue.metadata["factPromotionAllowed"] is False

    bound = bind_artifact_to_document_version(
        artifact,
        document=document,
        document_version_id="document-version:table-1",
    )
    bound_locator = bound.chunks[1].source_locator
    assert bound_locator["pageNumber"] == 1
    assert bound_locator["tableId"] == "page-1-table-1"
    assert bound_locator["rowIndex"] == 1
    assert bound_locator["columnIndex"] == 2
    assert len(bound_locator["bbox"]) == 4
    assert bound_locator["coordinateSpace"] == "pdf_points_top_left"


def test_native_table_capability_unavailable_explicitly_abstains() -> None:
    document = _document(_table_pdf_bytes())

    artifact = NativePdfTableExtractor(
        page_table_finder=None
    ).extract(document)

    assert artifact.complete is False
    assert artifact.chunks == ()
    assert artifact.warnings == ("PDF_TABLE_CAPABILITY_UNAVAILABLE",)
    assert artifact.metadata["status"] == "abstained"
    assert artifact.metadata["abstained"] is True
    assert artifact.metadata["capability"]["status"] == "unavailable"
    assert artifact.metadata["diagnostics"][0]["blocking"] is True
    assert artifact.metadata["approvalEligible"] is False


def _structural_artifact() -> ExtractionArtifact:
    phrases = (
        ("intro", "Confidential preamble", "pdf_text_block", None),
        (
            "heading-1",
            "1. Project Summary",
            "pdf_text_block",
            None,
        ),
        (
            "body-1",
            "The project has 112 parcels.",
            "pdf_text_block",
            None,
        ),
        (
            "heading-2",
            "Financing",
            "pdf_text_block",
            "section_heading",
        ),
        (
            "body-2",
            "Senior facility evidence.",
            "pdf_text_block",
            None,
        ),
    )
    chunks = []
    for index, (chunk_id, text, kind, semantic_role) in enumerate(
        phrases,
        start=1,
    ):
        metadata = (
            {"semanticRole": semantic_role}
            if semantic_role
            else {}
        )
        chunks.append(
            ExtractionChunk(
                chunk_id=chunk_id,
                kind=kind,
                text=text,
                source_locator={
                    "documentId": "document-semantic",
                    "pageNumber": index,
                    "sourceType": "document_page_bbox",
                    "metadata": {
                        "bbox": [10, 10, 100, 30],
                        "bboxCoordinateSystem": (
                            "pdf_points_top_left"
                        ),
                    },
                },
                metadata=metadata,
            )
        )
    return ExtractionArtifact(
        adapter_id="test_structure",
        adapter_version="1.0.0",
        document_id="document-semantic",
        chunks=tuple(chunks),
    )


def test_semantic_sections_are_deterministic_text_free_indexes() -> None:
    source = _structural_artifact()
    indexer = DeterministicSemanticSectionIndexer()

    first = indexer.index(source).to_dict()
    second = indexer.index(source).to_dict()

    assert first == second
    assert first["sectionCount"] == 3
    assert [
        section["semanticRole"] for section in first["sections"]
    ] == ["preamble", "numbered_heading", "explicit_heading"]
    assert first["sections"][1]["headingChunkId"] == "heading-1"
    assert first["sections"][2]["headingChunkId"] == "heading-2"
    assert first["metadata"]["storesOriginalText"] is False
    assert first["metadata"]["factPromotionAllowed"] is False
    serialized = json.dumps(first, ensure_ascii=False, sort_keys=True)
    for phrase in (
        "Confidential preamble",
        "Project Summary",
        "112 parcels",
        "Senior facility evidence",
    ):
        assert phrase not in serialized
    for section in first["sections"]:
        assert section["approvalEligible"] is False
        assert section["reportEligible"] is False
        for reference in section["chunkReferences"]:
            assert set(reference) == {
                "chunkId",
                "sourceLocator",
                "rawTextSha256",
            }
            assert len(reference["rawTextSha256"]) == 64


def test_pdf_enrichment_manifests_are_independent_and_non_authoritative() -> None:
    table_manifest = PDF_NATIVE_TABLE_MANIFEST.to_dict()
    section_manifest = SEMANTIC_SECTION_MANIFEST.to_dict()

    assert table_manifest["conditional_provides"] == [
        "document.pdf_tables"
    ]
    assert section_manifest["provides"] == []
    assert section_manifest["conditional_provides"] == [
        "document.semantic_sections"
    ]
    assert table_manifest["metadata"]["authoritative"] is False
    assert section_manifest["metadata"]["storesOriginalText"] is False
