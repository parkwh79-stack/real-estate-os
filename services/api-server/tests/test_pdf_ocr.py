from __future__ import annotations

import hashlib

import fitz
import pytest

from parsing_platform.adapters import ArchivedDocument
from parsing_platform.contracts import SourceLocator
from parsing_platform.pdf_ocr import (
    EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION,
    EXTERNAL_PDF_OCR_MANIFEST,
    OCR_EXECUTOR_ENV_VAR,
    OcrResponseValidationError,
    assess_pdf_ocr_requirement,
    bind_external_ocr_response_to_document_version,
    external_ocr_executor_contract,
    plan_external_pdf_ocr,
    validate_external_ocr_response,
)


def _document(
    content: bytes,
    *,
    object_key: str | None = "project-pdf/scan.pdf",
) -> ArchivedDocument:
    return ArchivedDocument(
        project_id="project-pdf",
        tenant_id="tenant-pdf",
        archive_id=hashlib.sha256(content).hexdigest(),
        filename="scan.pdf",
        content_type="application/pdf",
        content=content,
        object_key=object_key,
        object_version="version-1" if object_key else None,
    )


def _image_only_pdf_bytes() -> bytes:
    pixmap = fitz.Pixmap(
        fitz.csRGB,
        fitz.IRect(0, 0, 20, 20),
        False,
    )
    pixmap.clear_with(180)
    image = pixmap.tobytes("png")
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    page.insert_image(fitz.Rect(20, 20, 280, 280), stream=image)
    content = pdf.tobytes()
    pdf.close()
    return content


def _native_text_pdf_bytes() -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Native project text")
    content = pdf.tobytes()
    pdf.close()
    return content


def _mixed_raster_text_pdf_bytes(
    text_lines: list[str],
    *,
    image_rect: fitz.Rect = fitz.Rect(20, 20, 280, 280),
    render_mode: int = 0,
) -> bytes:
    pixmap = fitz.Pixmap(
        fitz.csRGB,
        fitz.IRect(0, 0, 20, 20),
        False,
    )
    pixmap.clear_with(180)
    image = pixmap.tobytes("png")
    pdf = fitz.open()
    page = pdf.new_page(width=300, height=300)
    page.insert_image(image_rect, stream=image)
    for index, text in enumerate(text_lines):
        page.insert_text(
            (30, 45 + index * 22),
            text,
            fontsize=10,
            render_mode=render_mode,
        )
    content = pdf.tobytes()
    pdf.close()
    return content


def test_image_only_pdf_requires_ocr_and_unconfigured_executor_blocks() -> None:
    document = _document(_image_only_pdf_bytes())

    assessment = assess_pdf_ocr_requirement(document)
    plan = plan_external_pdf_ocr(
        document,
        assessment=assessment,
        document_version_id="document-version:scan",
        environ={},
    )

    assert assessment.status == "ocr_required"
    assert assessment.ocr_required_pages == (1,)
    assert assessment.image_only_document is True
    assert assessment.diagnostics[0].code == "OCR_REQUIRED"
    assert plan.status == "blocked"
    assert plan.request is None
    assert any(
        item.code == "OCR_EXECUTOR_UNAVAILABLE"
        and item.blocking
        for item in plan.diagnostics
    )
    serialized = plan.to_dict()
    assert serialized["blocked"] is True
    assert (
        serialized["executorManifest"]["configuration"][
            "endpointEnvironmentVariable"
        ]
        == OCR_EXECUTOR_ENV_VAR
    )
    assert serialized["reportEligible"] is False


def test_configured_executor_still_requires_immutable_object_binding() -> None:
    document = _document(
        _image_only_pdf_bytes(),
        object_key=None,
    )

    plan = plan_external_pdf_ocr(
        document,
        document_version_id="document-version:scan",
        environ={OCR_EXECUTOR_ENV_VAR: "https://ocr.internal"},
    )

    assert plan.status == "blocked"
    assert plan.request is None
    assert any(
        item.code == "OCR_SOURCE_BINDING_INCOMPLETE"
        for item in plan.diagnostics
    )


def test_ready_ocr_request_contains_reference_not_inline_bytes() -> None:
    document = _document(_image_only_pdf_bytes())

    plan = plan_external_pdf_ocr(
        document,
        document_version_id="document-version:scan",
        environ={OCR_EXECUTOR_ENV_VAR: "https://ocr.internal/v1"},
    )

    assert plan.status == "ready"
    assert plan.request is not None
    assert plan.request["archiveId"] == document.archive_id
    assert plan.request["objectKey"] == document.object_key
    assert plan.request["objectVersion"] == document.object_version
    assert plan.request["documentVersionId"] == "document-version:scan"
    assert plan.request["fileSha256"] == document.sha256
    assert plan.request["pageNumbers"] == [1]
    assert len(plan.request["requestHash"]) == 64
    assert plan.request["requestId"].endswith(
        plan.request["requestHash"][:24]
    )
    assert plan.request["pageGeometry"] == [
        {"pageNumber": 1, "pageBBox": [0.0, 0.0, 300.0, 300.0]}
    ]
    assert "inlineBytes" not in plan.request
    assert plan.request["authorityPolicy"]["approvalEligible"] is False


def test_external_ocr_response_hash_and_locator_are_validated() -> None:
    document = _document(_image_only_pdf_bytes())
    plan = plan_external_pdf_ocr(
        document,
        document_version_id="document-version:scan",
        environ={OCR_EXECUTOR_ENV_VAR: "https://ocr.internal/v1"},
    )
    text = "Scanned feasibility heading"
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    response = {
        "schemaVersion": EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION,
        "requestId": plan.request["requestId"],
        "requestHash": plan.request["requestHash"],
        "status": "completed",
        "tenantId": plan.request["tenantId"],
        "projectId": plan.request["projectId"],
        "archiveId": plan.request["archiveId"],
        "objectKey": plan.request["objectKey"],
        "objectVersion": plan.request["objectVersion"],
        "documentVersionId": plan.request["documentVersionId"],
        "fileSha256": document.sha256,
        "completedPages": [1],
        "executorId": "private-ocr",
        "executorVersion": "2026.07",
        "chunks": [
            {
                "pageNumber": 1,
                "readingOrderIndex": 1,
                "text": text,
                "textSha256": text_hash,
                "bbox": [10, 20, 250, 50],
                "confidence": 0.98,
            }
        ],
    }

    artifact = validate_external_ocr_response(response, plan=plan)

    assert artifact.complete is True
    assert artifact.chunks[0].text == text
    locator = artifact.chunks[0].source_locator
    assert locator["pageNumber"] == 1
    assert locator["metadata"]["bbox"] == [10.0, 20.0, 250.0, 50.0]
    assert artifact.chunks[0].metadata["approvalEligible"] is False
    assert artifact.metadata["factPromotionAllowed"] is False

    bound = bind_external_ocr_response_to_document_version(
        response,
        plan=plan,
        document=document,
        document_version_id="document-version:scan",
        document_id="document:scan",
    )
    bound_locator = SourceLocator.model_validate(
        bound.chunks[0].source_locator
    )
    assert bound_locator.document_id == "document:scan"
    assert (
        bound_locator.document_version_id
        == "document-version:scan"
    )
    assert bound_locator.file_sha256 == document.sha256
    assert bound_locator.raw_text_sha256 == text_hash
    assert bound_locator.bbox == (10.0, 20.0, 250.0, 50.0)
    assert bound_locator.coordinate_space == "pdf_points_top_left"

    response["chunks"][0]["textSha256"] = "0" * 64
    with pytest.raises(
        OcrResponseValidationError,
        match="text hash",
    ):
        validate_external_ocr_response(response, plan=plan)


def test_external_ocr_response_rejects_extra_fields_and_out_of_page_bbox() -> None:
    document = _document(_image_only_pdf_bytes())
    plan = plan_external_pdf_ocr(
        document,
        document_version_id="document-version:scan",
        environ={OCR_EXECUTOR_ENV_VAR: "https://ocr.internal/v1"},
    )
    assert plan.request is not None
    text = "Bounded OCR text"
    response = {
        "schemaVersion": EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION,
        "requestId": plan.request["requestId"],
        "requestHash": plan.request["requestHash"],
        "status": "completed",
        "tenantId": plan.request["tenantId"],
        "projectId": plan.request["projectId"],
        "archiveId": plan.request["archiveId"],
        "objectKey": plan.request["objectKey"],
        "objectVersion": plan.request["objectVersion"],
        "documentVersionId": plan.request["documentVersionId"],
        "fileSha256": plan.request["fileSha256"],
        "completedPages": [1],
        "executorId": "private-ocr",
        "executorVersion": "2026.07",
        "chunks": [
            {
                "pageNumber": 1,
                "readingOrderIndex": 1,
                "text": text,
                "textSha256": hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest(),
                "bbox": [10, 20, 250, 50],
                "confidence": 0.98,
            }
        ],
    }

    extra = {**response, "untrusted": "field"}
    with pytest.raises(OcrResponseValidationError, match="fields"):
        validate_external_ocr_response(extra, plan=plan)

    response["chunks"][0]["bbox"] = [10, 20, 350, 50]
    with pytest.raises(OcrResponseValidationError, match="outside"):
        validate_external_ocr_response(response, plan=plan)


def test_native_text_pdf_does_not_request_ocr() -> None:
    document = _document(_native_text_pdf_bytes())

    assessment = assess_pdf_ocr_requirement(document)
    plan = plan_external_pdf_ocr(
        document,
        assessment=assessment,
        environ={},
    )

    assert assessment.status == "not_required"
    assert assessment.ocr_required is False
    assert assessment.native_text_pages == (1,)
    assert plan.status == "not_required"
    assert plan.blocked is False


def test_large_raster_with_short_native_text_requires_ocr() -> None:
    document = _document(
        _mixed_raster_text_pdf_bytes(["Project title"])
    )

    assessment = assess_pdf_ocr_requirement(document)

    assert assessment.status == "ocr_required"
    assert assessment.ocr_required_pages == (1,)
    assert assessment.native_text_pages == ()
    assert assessment.image_only_document is False
    diagnostic = assessment.diagnostics[0]
    assert diagnostic.code == "OCR_REQUIRED"
    assert diagnostic.metadata["mixedScanPages"] == [1]
    assert diagnostic.metadata["hiddenTextPages"] == []
    signals = diagnostic.metadata["pageSignals"]["1"]
    assert signals["visibleNativeTextCharacters"] > 0
    assert signals["rasterImageCoverage"] > 0.50
    assert signals["scanDominant"] is True


def test_large_raster_with_hidden_text_requires_ocr() -> None:
    document = _document(
        _mixed_raster_text_pdf_bytes(
            [
                "Invisible native overlay text " * 4,
                "must not suppress required OCR",
            ],
            render_mode=3,
        )
    )

    assessment = assess_pdf_ocr_requirement(document)

    assert assessment.status == "ocr_required"
    assert assessment.ocr_required_pages == (1,)
    assert assessment.native_text_pages == ()
    assert assessment.image_only_document is False
    diagnostic = assessment.diagnostics[0]
    assert diagnostic.metadata["hiddenTextPages"] == [1]
    signals = diagnostic.metadata["pageSignals"]["1"]
    assert signals["extractedNativeTextCharacters"] > 32
    assert signals["visibleNativeTextCharacters"] == 0


def test_large_raster_with_text_rich_visible_layer_does_not_require_ocr() -> None:
    document = _document(
        _mixed_raster_text_pdf_bytes(
            [
                "Visible feasibility assumptions revenue cost schedule",
                "Product mix apartment retail financing collection terms",
                "Construction period approval evidence and reconciliation",
            ]
        )
    )

    assessment = assess_pdf_ocr_requirement(document)

    assert assessment.status == "not_required"
    assert assessment.ocr_required is False
    assert assessment.native_text_pages == (1,)
    assert assessment.image_only_document is False


def test_small_logo_does_not_turn_short_native_page_into_scan() -> None:
    document = _document(
        _mixed_raster_text_pdf_bytes(
            ["Project title"],
            image_rect=fitz.Rect(20, 20, 70, 70),
        )
    )

    assessment = assess_pdf_ocr_requirement(document)

    assert assessment.status == "not_required"
    assert assessment.ocr_required is False
    assert assessment.native_text_pages == (1,)


def test_malformed_page_image_metadata_abstains_and_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document(_native_text_pdf_bytes())
    monkeypatch.setattr(
        fitz.Page,
        "get_image_info",
        lambda self, **kwargs: [
            {
                "bbox": ["not-a-coordinate", 0, 100, 100],
                "width": 20,
                "height": 20,
            }
        ],
    )

    assessment = assess_pdf_ocr_requirement(document)
    plan = plan_external_pdf_ocr(
        document,
        assessment=assessment,
        document_version_id="document-version:scan",
        environ={OCR_EXECUTOR_ENV_VAR: "https://ocr.internal/v1"},
    )

    assert assessment.status == "abstained"
    assert assessment.ocr_required is False
    assert assessment.native_text_pages == ()
    assert assessment.diagnostics[0].code == (
        "PDF_OCR_ASSESSMENT_INDETERMINATE"
    )
    assert assessment.diagnostics[0].blocking is True
    assert plan.status == "blocked"
    assert plan.request is None


def test_external_ocr_manifest_declares_fail_closed_boundary() -> None:
    manifest = EXTERNAL_PDF_OCR_MANIFEST.to_dict()
    contract = external_ocr_executor_contract()

    assert manifest["execution_class"] == "external_service"
    assert manifest["conditional_provides"] == [
        "document.ocr_structure"
    ]
    assert manifest["metadata"]["failClosed"] is True
    assert (
        contract["requestContract"]["sourceTransferMode"]
        == "immutable_object_reference_only"
    )
    assert contract["requestContract"]["inlineBytesAllowed"] is False
    assert (
        contract["authorityPolicy"]["factPromotionAllowed"]
        is False
    )
