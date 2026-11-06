from __future__ import annotations

import hashlib
import json

import fitz
import pytest

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.contracts import SourceLocator
from parsing_platform.default_modules import (
    sanitize_parsing_orchestration_result,
)
from parsing_platform.pdf_enrichment_modules import (
    PDF_OCR_PLANNING_MANIFEST,
    SemanticSectionModule,
    build_pdf_enrichment_module_registry,
    build_pdf_enrichment_orchestrator,
    pdf_enrichment_profile,
)
from parsing_platform.pdf_structure_enrichment import (
    PDF_NATIVE_TABLE_MANIFEST,
    SEMANTIC_SECTION_MANIFEST,
)


def _document(content: bytes, filename: str = "plan.pdf") -> ArchivedDocument:
    return ArchivedDocument(
        project_id="project-pdf-e2e",
        tenant_id="tenant-pdf-e2e",
        archive_id=hashlib.sha256(content).hexdigest(),
        filename=filename,
        content_type="application/pdf",
        content=content,
        object_key=f"project-pdf-e2e/{filename}",
        object_version="version-1",
    )


def _table_pdf_bytes() -> bytes:
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    page.insert_text((50, 30), "1. Feasibility Summary")
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


def _image_only_pdf_bytes() -> bytes:
    pixmap = fitz.Pixmap(
        fitz.csRGB,
        fitz.IRect(0, 0, 20, 20),
        False,
    )
    pixmap.clear_with(160)
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    page.insert_image(
        fitz.Rect(20, 20, 280, 280),
        stream=pixmap.tobytes("png"),
    )
    content = pdf.tobytes()
    pdf.close()
    return content


def _nested_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _nested_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _nested_keys(item)


def test_pdf_enrichment_modules_are_independently_registered() -> None:
    registry = build_pdf_enrichment_module_registry(ocr_environ={})

    refs = {manifest.ref for manifest in registry.list_active()}

    assert PDF_NATIVE_TABLE_MANIFEST.ref in refs
    assert SEMANTIC_SECTION_MANIFEST.ref in refs
    assert PDF_OCR_PLANNING_MANIFEST.ref in refs
    assert "document_inspection@1.0.0" in refs
    assert "structural_extraction@1.0.0" in refs


@pytest.mark.skipif(
    not hasattr(fitz.Page, "find_tables"),
    reason="PyMuPDF native table capability is unavailable",
)
def test_pdf_enrichment_profile_binds_strict_table_and_section_locators() -> None:
    document = _document(_table_pdf_bytes())
    orchestrator = build_pdf_enrichment_orchestrator(ocr_environ={})
    profile = pdf_enrichment_profile(
        project_id=document.project_id,
        document_types=("business_plan",),
    )

    result = orchestrator.execute_sync(
        profile,
        {
            "document": document,
            "documentType": "business_plan",
        },
        context={
            "project_id": document.project_id,
            "document_id": "document:pdf-e2e",
            "document_version_id": "document-version:pdf-e2e",
        },
    )

    assert result.execution_status == "completed"
    assert result.failed_module_ids == ()
    table_output = result.outputs["pdf_native_table_extraction"]
    assert table_output["status"] == "completed"
    assert table_output["providedCapabilities"] == [
        "document.pdf_tables"
    ]
    table_chunks = table_output["artifact"]["chunks"]
    assert len(table_chunks) == 4
    for chunk in table_chunks:
        locator = SourceLocator.model_validate(
            chunk["sourceLocator"]
        )
        assert locator.document_id == "document:pdf-e2e"
        assert (
            locator.document_version_id
            == "document-version:pdf-e2e"
        )
        assert locator.table_id == "page-1-table-1"
        assert locator.row_index is not None
        assert locator.column_index is not None
        assert locator.bbox is not None

    section_output = result.outputs["semantic_section_indexing"]
    section_index = section_output["sectionIndex"]
    serialized = json.dumps(
        section_index,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "Feasibility Summary" not in serialized
    assert "Apartment" not in serialized
    assert section_index["metadata"]["storesOriginalText"] is False
    for section in section_index["sections"]:
        for reference in section["chunkReferences"]:
            SourceLocator.model_validate(reference["sourceLocator"])
            assert set(reference) == {
                "chunkId",
                "sourceLocator",
                "rawTextSha256",
            }

    ocr_output = result.outputs["pdf_ocr_assessment_dispatch_plan"]
    assert ocr_output["status"] == "not_required"
    assert ocr_output["dispatchPlan"]["request"] is None
    assert ocr_output["providedCapabilities"] == [
        "document.ocr_assessed"
    ]

    sanitized = sanitize_parsing_orchestration_result(
        result.to_dict()
    )
    serialized_sanitized = json.dumps(
        sanitized,
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "Feasibility Summary" not in serialized_sanitized
    assert "Apartment" not in serialized_sanitized
    assert "text" not in set(_nested_keys(sanitized))


def test_semantic_module_abstains_on_forged_bound_locator() -> None:
    document = _document(_table_pdf_bytes())
    orchestrator = build_pdf_enrichment_orchestrator(ocr_environ={})
    result = orchestrator.execute_sync(
        pdf_enrichment_profile(
            project_id=document.project_id,
            document_types=("business_plan",),
        ),
        {
            "document": document,
            "documentType": "business_plan",
        },
        context={
            "project_id": document.project_id,
            "document_id": "document:pdf-forged",
            "document_version_id": "document-version:pdf-forged",
        },
    )
    structural_result = result.results["structural_extraction"].to_dict()
    structural_result["output"]["artifact"]["chunks"][0][
        "sourceLocator"
    ]["fileSha256"] = "0" * 64

    output = SemanticSectionModule().execute(
        {
            "input": {
                "document": document,
                "documentType": "business_plan",
            },
            "module_results": {
                "structural_extraction": structural_result,
            },
            "available_capabilities": ["document.structure"],
        },
        {
            "project_id": document.project_id,
            "document_id": "document:pdf-forged",
            "document_version_id": "document-version:pdf-forged",
        },
    )

    assert output["status"] == "abstained"
    assert output["providedCapabilities"] == []
    assert output["sectionIndex"]["complete"] is False
    assert output["sectionIndex"]["diagnostics"][0]["blocking"] is True


def test_image_only_pdf_profile_stops_at_fail_closed_ocr_plan() -> None:
    document = _document(_image_only_pdf_bytes(), "scan.pdf")
    orchestrator = build_pdf_enrichment_orchestrator(ocr_environ={})
    profile = pdf_enrichment_profile(
        project_id=document.project_id,
        document_types=("business_plan",),
    )

    result = orchestrator.execute_sync(
        profile,
        {
            "document": document,
            "documentType": "business_plan",
        },
        context={
            "project_id": document.project_id,
            "document_id": "document:scan",
            "document_version_id": "document-version:scan",
        },
    )

    assert result.execution_status == "failed"
    ocr_result = result.results[
        "pdf_ocr_assessment_dispatch_plan"
    ]
    assert ocr_result.execution_status == "failed"
    assert ocr_result.error_code == "OCR_REQUIRED_DISPATCH_BLOCKED"
    assert result.results[
        "pdf_native_table_extraction"
    ].execution_status == "skipped"
    assert result.results[
        "semantic_section_indexing"
    ].execution_status == "skipped"
    assert result.results[
        "document_classification"
    ].execution_status == "skipped"


def test_ready_ocr_plan_without_executor_result_still_fails_closed() -> None:
    document = _document(_image_only_pdf_bytes(), "scan.pdf")
    orchestrator = build_pdf_enrichment_orchestrator(
        ocr_environ={
            "REALYTICS_OCR_EXECUTOR_URL": (
                "https://ocr.internal/v2"
            )
        }
    )
    result = orchestrator.execute_sync(
        pdf_enrichment_profile(
            project_id=document.project_id,
            document_types=("business_plan",),
        ),
        {
            "document": document,
            "documentType": "business_plan",
        },
        context={
            "project_id": document.project_id,
            "document_id": "document:scan",
            "document_version_id": "document-version:scan",
        },
    )

    assert result.execution_status == "failed"
    ocr_result = result.results[
        "pdf_ocr_assessment_dispatch_plan"
    ]
    assert ocr_result.error_code == (
        "OCR_REQUIRED_EXECUTION_NOT_COMPLETED"
    )
    assert result.results[
        "semantic_section_indexing"
    ].execution_status == "skipped"
    assert result.results[
        "document_classification"
    ].execution_status == "skipped"
