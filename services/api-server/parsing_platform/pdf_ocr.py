from __future__ import annotations

"""Fail-closed OCR assessment and external executor contracts for PDFs."""

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

import fitz

from parsing_platform.adapters import (
    MAX_CHUNK_TEXT_CHARS,
    MAX_EXTRACTION_CHUNKS,
    MAX_PDF_PAGES,
    ArchivedDocument,
    ExtractionArtifact,
    ExtractionChunk,
    bind_artifact_to_document_version,
)
from parsing_platform.contracts import SourceLocator
from parsing_platform.document_taxonomy import (
    DOCUMENT_TAXONOMY_VERSION,
    DOCUMENT_TYPE_SCOPE_ALL,
)
from parsing_platform.registry import ParserModuleManifest


PDF_OCR_ASSESSMENT_SCHEMA_VERSION = "realytics-pdf-ocr-assessment-v2"
PDF_OCR_ASSESSMENT_POLICY_VERSION = (
    "realytics-pdf-ocr-page-signals-v1"
)
OCR_MIN_VISIBLE_NATIVE_TEXT_CHARACTERS = 32
OCR_MIN_VISIBLE_NATIVE_TEXT_DENSITY = 0.00008
OCR_LARGE_RASTER_IMAGE_COVERAGE_THRESHOLD = 0.50
EXTERNAL_OCR_REQUEST_SCHEMA_VERSION = "realytics-external-ocr-request-v2"
EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION = "realytics-external-ocr-response-v2"
EXTERNAL_OCR_CONTRACT_VERSION = "2.0.0"
OCR_EXECUTOR_ENV_VAR = "REALYTICS_OCR_EXECUTOR_URL"
OCR_ALLOW_INSECURE_HTTP_ENV_VAR = (
    "REALYTICS_OCR_ALLOW_INSECURE_HTTP"
)
_EXTERNAL_OCR_REQUEST_FIELDS = frozenset(
    {
        "schemaVersion",
        "contractVersion",
        "requestId",
        "requestHash",
        "tenantId",
        "projectId",
        "archiveId",
        "objectKey",
        "objectVersion",
        "documentVersionId",
        "fileSha256",
        "originalFileName",
        "mediaType",
        "pageNumbers",
        "pageGeometry",
        "config",
        "executorPolicy",
        "authorityPolicy",
    }
)
_EXTERNAL_OCR_RESPONSE_FIELDS = frozenset(
    {
        "schemaVersion",
        "requestId",
        "requestHash",
        "status",
        "tenantId",
        "projectId",
        "archiveId",
        "objectKey",
        "objectVersion",
        "documentVersionId",
        "fileSha256",
        "completedPages",
        "executorId",
        "executorVersion",
        "chunks",
    }
)
_EXTERNAL_OCR_CHUNK_FIELDS = frozenset(
    {
        "pageNumber",
        "readingOrderIndex",
        "text",
        "textSha256",
        "bbox",
        "confidence",
    }
)


EXTERNAL_PDF_OCR_MANIFEST = ParserModuleManifest(
    module_id="external_pdf_ocr",
    version=EXTERNAL_OCR_CONTRACT_VERSION,
    capabilities=("document.ocr",),
    document_taxonomy_version=DOCUMENT_TAXONOMY_VERSION,
    document_type_scope=DOCUMENT_TYPE_SCOPE_ALL,
    requires=("document.safe",),
    conditional_provides=("document.ocr_structure",),
    execution_class="external_service",
    input_schema=EXTERNAL_OCR_REQUEST_SCHEMA_VERSION,
    output_schema=EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION,
    required_locator_precision="exact",
    data_classification="restricted",
    max_output_bytes=20 * 1024 * 1024,
    metadata={
        "authoritative": False,
        "factPromotionAllowed": False,
        "failClosed": True,
        "executorEnvironmentVariable": OCR_EXECUTOR_ENV_VAR,
        "sourceTransferMode": "immutable_object_reference_only",
        "normalizedCoordinateSpace": "pdf_points_top_left",
    },
)


class ExternalOcrExecutor(Protocol):
    """Transport-neutral executor boundary.

    Implementations may use HTTP, a queue, or a private worker. The response
    must still pass ``validate_external_ocr_response`` before it becomes
    structural evidence.
    """

    def execute(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


class OcrResponseValidationError(ValueError):
    """An external OCR response violated the immutable request contract."""


class OcrRequiredDispatchBlocked(RuntimeError):
    """Typed fail-closed error for an undispatched required OCR job."""

    code = "OCR_REQUIRED_DISPATCH_BLOCKED"
    public_message = (
        "Required PDF OCR could not be dispatched under the configured "
        "executor policy."
    )

    def __init__(self, plan: "ExternalOcrExecutionPlan") -> None:
        self.plan = plan
        self.diagnostics = tuple(plan.diagnostics)
        super().__init__(self.public_message)


class OcrRequiredExecutionPending(RuntimeError):
    """Typed gate when a plan exists but no OCR result has been verified."""

    code = "OCR_REQUIRED_EXECUTION_NOT_COMPLETED"
    public_message = (
        "Required PDF OCR has not completed through a verified external "
        "executor; downstream parsing remains blocked."
    )

    def __init__(self, plan: "ExternalOcrExecutionPlan") -> None:
        self.plan = plan
        self.diagnostics = tuple(plan.diagnostics)
        super().__init__(self.public_message)


@dataclass(frozen=True)
class PdfPageGeometry:
    page_number: int
    page_bbox: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "pageNumber": self.page_number,
            "pageBBox": list(self.page_bbox),
        }


@dataclass(frozen=True)
class PdfOcrDiagnostic:
    code: str
    severity: str
    message: str
    blocking: bool
    page_numbers: tuple[int, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "blocking": self.blocking,
            "pageNumbers": list(self.page_numbers),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PdfOcrAssessment:
    document_id: str
    page_count: int
    status: str
    ocr_required_pages: tuple[int, ...]
    native_text_pages: tuple[int, ...]
    image_only_document: bool
    page_geometries: tuple[PdfPageGeometry, ...]
    diagnostics: tuple[PdfOcrDiagnostic, ...] = ()

    @property
    def ocr_required(self) -> bool:
        return bool(self.ocr_required_pages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": PDF_OCR_ASSESSMENT_SCHEMA_VERSION,
            "documentId": self.document_id,
            "pageCount": self.page_count,
            "status": self.status,
            "ocrRequired": self.ocr_required,
            "ocrRequiredPages": list(self.ocr_required_pages),
            "nativeTextPages": list(self.native_text_pages),
            "imageOnlyDocument": self.image_only_document,
            "pageGeometry": [
                geometry.to_dict()
                for geometry in self.page_geometries
            ],
            "diagnostics": [
                diagnostic.to_dict() for diagnostic in self.diagnostics
            ],
            "authorityStatus": "structural_diagnostic_only",
            "approvalEligible": False,
            "reportEligible": False,
            "factPromotionAllowed": False,
        }


@dataclass(frozen=True)
class ExternalOcrExecutionPlan:
    status: str
    assessment: PdfOcrAssessment
    diagnostics: tuple[PdfOcrDiagnostic, ...]
    executor_endpoint: str | None = None
    request: dict[str, Any] | None = None

    @property
    def blocked(self) -> bool:
        return any(item.blocking for item in self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "realytics-external-ocr-plan-v2",
            "status": self.status,
            "blocked": self.blocked,
            "executorConfigured": bool(self.executor_endpoint),
            "request": dict(self.request) if self.request else None,
            "assessment": self.assessment.to_dict(),
            "diagnostics": [
                diagnostic.to_dict() for diagnostic in self.diagnostics
            ],
            "executorManifest": external_ocr_executor_contract(),
            "authorityStatus": "analysis_only",
            "approvalEligible": False,
            "reportEligible": False,
            "factPromotionAllowed": False,
        }


@dataclass(frozen=True)
class _PdfPageOcrSignals:
    extracted_native_text_characters: int
    visible_native_text_characters: int
    visible_native_text_density: float
    raster_image_count: int
    raster_image_coverage: float

    @property
    def scan_dominant(self) -> bool:
        return (
            self.raster_image_coverage
            >= OCR_LARGE_RASTER_IMAGE_COVERAGE_THRESHOLD
            and (
                self.visible_native_text_characters
                < OCR_MIN_VISIBLE_NATIVE_TEXT_CHARACTERS
                or self.visible_native_text_density
                < OCR_MIN_VISIBLE_NATIVE_TEXT_DENSITY
            )
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "extractedNativeTextCharacters": (
                self.extracted_native_text_characters
            ),
            "visibleNativeTextCharacters": (
                self.visible_native_text_characters
            ),
            "visibleNativeTextDensity": round(
                self.visible_native_text_density,
                8,
            ),
            "rasterImageCount": self.raster_image_count,
            "rasterImageCoverage": round(
                self.raster_image_coverage,
                6,
            ),
            "scanDominant": self.scan_dominant,
        }


def _pdf_page_ocr_signals(
    page: fitz.Page,
    *,
    page_bbox: list[float],
) -> _PdfPageOcrSignals:
    """Collect only validated native-text and raster geometry signals."""

    normalized_page_bbox = _normalized_bbox(page_bbox)
    if normalized_page_bbox is None:
        raise ValueError("PDF page bounds are invalid")
    page_area = _bbox_area(normalized_page_bbox)
    if page_area <= 0:
        raise ValueError("PDF page area is invalid")

    native_text_value = page.get_text("text")
    if native_text_value is None:
        native_text = ""
    elif isinstance(native_text_value, str):
        native_text = native_text_value
    else:
        raise ValueError("PDF native text metadata is invalid")
    extracted_characters = _non_whitespace_character_count(native_text)

    visible_trace_characters = _visible_text_trace_characters(
        page.get_texttrace(),
        page_bbox=normalized_page_bbox,
    )
    # A trace alone is not enough: extracted text and visible traces must
    # corroborate one another before native text can suppress OCR.
    visible_characters = min(
        extracted_characters,
        visible_trace_characters,
    )

    image_metadata = page.get_image_info(
        hashes=False,
        xrefs=False,
    )
    image_rectangles = _validated_visible_image_rectangles(
        image_metadata,
        page_bbox=normalized_page_bbox,
    )
    image_coverage = min(
        1.0,
        _rectangle_union_area(image_rectangles) / page_area,
    )
    return _PdfPageOcrSignals(
        extracted_native_text_characters=extracted_characters,
        visible_native_text_characters=visible_characters,
        visible_native_text_density=(
            visible_characters / page_area
        ),
        raster_image_count=len(image_rectangles),
        raster_image_coverage=image_coverage,
    )


def _visible_text_trace_characters(
    traces: Any,
    *,
    page_bbox: list[float],
) -> int:
    if not isinstance(traces, (list, tuple)):
        raise ValueError("PDF text trace metadata must be a sequence")
    visible_characters = 0
    for trace in traces:
        if not isinstance(trace, Mapping):
            raise ValueError("PDF text trace entry must be an object")
        render_type = trace.get("type")
        if (
            isinstance(render_type, bool)
            or not isinstance(render_type, int)
            or render_type not in {0, 1, 2, 3}
        ):
            raise ValueError("PDF text trace render type is invalid")
        opacity = _normalized_opacity(trace.get("opacity"))
        trace_bbox = _normalized_bbox(trace.get("bbox"))
        if trace_bbox is None:
            raise ValueError("PDF text trace bounds are invalid")
        characters = trace.get("chars")
        if not isinstance(characters, (list, tuple)):
            raise ValueError("PDF text trace characters are invalid")
        codepoints: list[int] = []
        for character in characters:
            if (
                not isinstance(character, (list, tuple))
                or not character
            ):
                raise ValueError("PDF text trace character is invalid")
            codepoint = character[0]
            if (
                isinstance(codepoint, bool)
                or not isinstance(codepoint, int)
                or not 0 <= codepoint <= 0x10FFFF
            ):
                raise ValueError(
                    "PDF text trace character codepoint is invalid"
                )
            codepoints.append(codepoint)
        if (
            render_type == 3
            or opacity <= 0.01
            or _bbox_intersection(trace_bbox, page_bbox) is None
        ):
            continue
        visible_characters += sum(
            1
            for codepoint in codepoints
            if not chr(codepoint).isspace()
        )
    return visible_characters


def _validated_visible_image_rectangles(
    image_metadata: Any,
    *,
    page_bbox: list[float],
) -> list[list[float]]:
    if not isinstance(image_metadata, (list, tuple)):
        raise ValueError("PDF image metadata must be a sequence")
    rectangles: list[list[float]] = []
    for image in image_metadata:
        if not isinstance(image, Mapping):
            raise ValueError("PDF image metadata entry must be an object")
        width = image.get("width")
        height = image.get("height")
        if (
            isinstance(width, bool)
            or not isinstance(width, int)
            or width <= 0
            or isinstance(height, bool)
            or not isinstance(height, int)
            or height <= 0
        ):
            raise ValueError("PDF image dimensions are invalid")
        image_bbox = _normalized_bbox(image.get("bbox"))
        if image_bbox is None:
            raise ValueError("PDF image bounds are invalid")
        clipped_bbox = _bbox_intersection(image_bbox, page_bbox)
        if clipped_bbox is not None:
            rectangles.append(clipped_bbox)
    return rectangles


def _normalized_opacity(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("PDF text opacity is invalid")
    try:
        opacity = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("PDF text opacity is invalid") from exc
    if not math.isfinite(opacity) or not 0 <= opacity <= 1:
        raise ValueError("PDF text opacity is invalid")
    return opacity


def _non_whitespace_character_count(value: str) -> int:
    return sum(1 for character in value if not character.isspace())


def _bbox_area(bbox: list[float]) -> float:
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])


def _bbox_intersection(
    first: list[float],
    second: list[float],
) -> list[float] | None:
    intersection = [
        max(first[0], second[0]),
        max(first[1], second[1]),
        min(first[2], second[2]),
        min(first[3], second[3]),
    ]
    if (
        intersection[2] <= intersection[0]
        or intersection[3] <= intersection[1]
    ):
        return None
    return intersection


def _rectangle_union_area(rectangles: list[list[float]]) -> float:
    """Return deterministic union area for axis-aligned rectangles."""

    if not rectangles:
        return 0.0
    x_coordinates = sorted(
        {
            coordinate
            for rectangle in rectangles
            for coordinate in (rectangle[0], rectangle[2])
        }
    )
    area = 0.0
    for left, right in zip(x_coordinates, x_coordinates[1:]):
        if right <= left:
            continue
        y_intervals = sorted(
            (rectangle[1], rectangle[3])
            for rectangle in rectangles
            if rectangle[0] < right and rectangle[2] > left
        )
        if not y_intervals:
            continue
        covered_height = 0.0
        current_bottom, current_top = y_intervals[0]
        for bottom, top in y_intervals[1:]:
            if bottom <= current_top:
                current_top = max(current_top, top)
                continue
            covered_height += current_top - current_bottom
            current_bottom, current_top = bottom, top
        covered_height += current_top - current_bottom
        area += (right - left) * covered_height
    return area


def assess_pdf_ocr_requirement(
    document: ArchivedDocument,
) -> PdfOcrAssessment:
    """Detect image-only and scan-dominant pages without guessing OCR text."""

    if not _supports_pdf(document):
        raise ValueError("OCR assessment requires a PDF source")
    pdf = fitz.open(stream=document.content, filetype="pdf")
    page_count = pdf.page_count
    native_text_pages: list[int] = []
    ocr_required_pages: list[int] = []
    blank_pages: list[int] = []
    mixed_scan_pages: list[int] = []
    hidden_text_pages: list[int] = []
    indeterminate_pages: list[int] = []
    pages_with_extracted_native_text: list[int] = []
    page_signal_metrics: dict[int, dict[str, Any]] = {}
    page_geometries: list[PdfPageGeometry] = []
    try:
        if pdf.needs_pass:
            raise ValueError("Encrypted PDF cannot be assessed for OCR")
        if page_count > MAX_PDF_PAGES:
            raise ValueError("PDF page count exceeds the OCR assessment limit")
        for page_number, page in enumerate(pdf, start=1):
            page_bbox = _normalized_bbox(tuple(page.rect))
            if (
                page_bbox is None
                or page_bbox[2] <= page_bbox[0]
                or page_bbox[3] <= page_bbox[1]
            ):
                raise ValueError(
                    "PDF page geometry is invalid for OCR assessment"
                )
            page_geometries.append(
                PdfPageGeometry(
                    page_number=page_number,
                    page_bbox=tuple(page_bbox),
                )
            )
            try:
                signals = _pdf_page_ocr_signals(
                    page,
                    page_bbox=page_bbox,
                )
            except Exception:
                # Page signals are structural metadata, not business facts.
                # If PyMuPDF returns malformed or incomplete signal geometry,
                # the assessment must abstain instead of assuming native text
                # is sufficient.
                indeterminate_pages.append(page_number)
                continue

            page_signal_metrics[page_number] = signals.to_metadata()
            if signals.extracted_native_text_characters:
                pages_with_extracted_native_text.append(page_number)

            if signals.raster_image_count:
                if not signals.visible_native_text_characters:
                    ocr_required_pages.append(page_number)
                    if signals.extracted_native_text_characters:
                        hidden_text_pages.append(page_number)
                    continue
                if signals.scan_dominant:
                    ocr_required_pages.append(page_number)
                    mixed_scan_pages.append(page_number)
                    continue

            if signals.visible_native_text_characters:
                native_text_pages.append(page_number)
                continue
            blank_pages.append(page_number)
    finally:
        pdf.close()

    diagnostics: list[PdfOcrDiagnostic] = []
    if ocr_required_pages:
        diagnostics.append(
            PdfOcrDiagnostic(
                code="OCR_REQUIRED",
                severity="high",
                message=(
                    "One or more image-only or scan-dominant PDF pages "
                    "require an approved external OCR executor before text "
                    "extraction is complete."
                ),
                blocking=True,
                page_numbers=tuple(ocr_required_pages),
                metadata={
                    "executorEnvironmentVariable": OCR_EXECUTOR_ENV_VAR,
                    "failClosed": True,
                    "assessmentPolicyVersion": (
                        PDF_OCR_ASSESSMENT_POLICY_VERSION
                    ),
                    "minimumVisibleNativeTextCharacters": (
                        OCR_MIN_VISIBLE_NATIVE_TEXT_CHARACTERS
                    ),
                    "minimumVisibleNativeTextDensity": (
                        OCR_MIN_VISIBLE_NATIVE_TEXT_DENSITY
                    ),
                    "largeRasterImageCoverageThreshold": (
                        OCR_LARGE_RASTER_IMAGE_COVERAGE_THRESHOLD
                    ),
                    "mixedScanPages": list(mixed_scan_pages),
                    "hiddenTextPages": list(hidden_text_pages),
                    "pageSignals": {
                        str(page_number): page_signal_metrics[page_number]
                        for page_number in ocr_required_pages
                    },
                },
            )
        )
    if indeterminate_pages:
        diagnostics.append(
            PdfOcrDiagnostic(
                code="PDF_OCR_ASSESSMENT_INDETERMINATE",
                severity="critical",
                message=(
                    "One or more PDF pages returned malformed or incomplete "
                    "text/image metadata; OCR assessment abstained for those "
                    "pages."
                ),
                blocking=True,
                page_numbers=tuple(indeterminate_pages),
                metadata={
                    "assessmentPolicyVersion": (
                        PDF_OCR_ASSESSMENT_POLICY_VERSION
                    ),
                    "failClosed": True,
                },
            )
        )
    if blank_pages:
        all_content_missing = (
            not native_text_pages and not ocr_required_pages
            and not indeterminate_pages
        )
        diagnostics.append(
            PdfOcrDiagnostic(
                code=(
                    "PDF_NO_EXTRACTABLE_CONTENT"
                    if all_content_missing
                    else "PDF_BLANK_PAGES"
                ),
                severity="high" if all_content_missing else "info",
                message=(
                    "The PDF contains no visible native text or raster image "
                    "content."
                    if all_content_missing
                    else "The PDF contains one or more blank pages."
                ),
                blocking=all_content_missing,
                page_numbers=tuple(blank_pages),
            )
        )

    status = (
        "ocr_required"
        if ocr_required_pages
        else "abstained"
        if any(item.blocking for item in diagnostics)
        else "not_required"
    )
    return PdfOcrAssessment(
        document_id=document.archive_id,
        page_count=page_count,
        status=status,
        ocr_required_pages=tuple(ocr_required_pages),
        native_text_pages=tuple(native_text_pages),
        image_only_document=(
            page_count > 0
            and len(ocr_required_pages) == page_count
            and not pages_with_extracted_native_text
        ),
        page_geometries=tuple(page_geometries),
        diagnostics=tuple(diagnostics),
    )


def plan_external_pdf_ocr(
    document: ArchivedDocument,
    *,
    assessment: PdfOcrAssessment | None = None,
    document_version_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ExternalOcrExecutionPlan:
    """Create an immutable-reference OCR plan or a blocking diagnostic."""

    assessment = assessment or assess_pdf_ocr_requirement(document)
    if assessment.document_id != document.archive_id:
        raise ValueError("OCR assessment does not match the archived document")
    diagnostics = list(assessment.diagnostics)
    if any(
        item.blocking and item.code != "OCR_REQUIRED"
        for item in diagnostics
    ):
        return ExternalOcrExecutionPlan(
            status="blocked",
            assessment=assessment,
            diagnostics=tuple(diagnostics),
        )
    if not assessment.ocr_required:
        return ExternalOcrExecutionPlan(
            status=(
                "blocked"
                if any(item.blocking for item in diagnostics)
                else "not_required"
            ),
            assessment=assessment,
            diagnostics=tuple(diagnostics),
        )

    environment = os.environ if environ is None else environ
    endpoint = str(environment.get(OCR_EXECUTOR_ENV_VAR) or "").strip()
    allow_insecure_http = _environment_flag(
        environment.get(OCR_ALLOW_INSECURE_HTTP_ENV_VAR)
    )
    endpoint_error = _executor_endpoint_error(
        endpoint,
        allow_insecure_http=allow_insecure_http,
    )
    if endpoint_error:
        diagnostics.append(
            PdfOcrDiagnostic(
                code=(
                    "OCR_EXECUTOR_UNAVAILABLE"
                    if not endpoint
                    else "OCR_EXECUTOR_CONFIGURATION_INVALID"
                ),
                severity="critical",
                message=endpoint_error,
                blocking=True,
                page_numbers=assessment.ocr_required_pages,
                metadata={
                    "executorEnvironmentVariable": OCR_EXECUTOR_ENV_VAR,
                    "insecureHttpOptInEnvironmentVariable": (
                        OCR_ALLOW_INSECURE_HTTP_ENV_VAR
                    ),
                    "failClosed": True,
                },
            )
        )
        return ExternalOcrExecutionPlan(
            status="blocked",
            assessment=assessment,
            diagnostics=tuple(diagnostics),
        )

    if (
        not str(document.tenant_id or "").strip()
        or not str(document.object_key or "").strip()
        or not str(document.object_version or "").strip()
        or not str(document_version_id or "").strip()
    ):
        diagnostics.append(
            PdfOcrDiagnostic(
                code="OCR_SOURCE_BINDING_INCOMPLETE",
                severity="critical",
                message=(
                    "External OCR requires a tenant-scoped immutable object "
                    "version and persisted document version; inline document "
                    "bytes are not transferred."
                ),
                blocking=True,
                page_numbers=assessment.ocr_required_pages,
            )
        )
        return ExternalOcrExecutionPlan(
            status="blocked",
            assessment=assessment,
            diagnostics=tuple(diagnostics),
            executor_endpoint=endpoint,
        )

    request = _external_ocr_request(
        document=document,
        assessment=assessment,
        document_version_id=str(document_version_id).strip(),
        executor_endpoint=endpoint,
        allow_insecure_http=allow_insecure_http,
    )
    return ExternalOcrExecutionPlan(
        status="ready",
        assessment=assessment,
        diagnostics=tuple(diagnostics),
        executor_endpoint=endpoint,
        request=request,
    )


def external_ocr_executor_contract() -> dict[str, Any]:
    """Portable executor manifest; no transport client is implied."""

    return {
        "schemaVersion": "realytics-external-ocr-executor-contract-v2",
        "moduleManifest": EXTERNAL_PDF_OCR_MANIFEST.to_dict(),
        "configuration": {
            "endpointEnvironmentVariable": OCR_EXECUTOR_ENV_VAR,
            "allowedUrlSchemesByDefault": ["https"],
            "developmentHttpOptInEnvironmentVariable": (
                OCR_ALLOW_INSECURE_HTTP_ENV_VAR
            ),
            "developmentHttpAllowedHosts": [
                "localhost",
                "127.0.0.1",
                "::1",
            ],
            "credentialsInUrlAllowed": False,
            "failClosedWhenMissing": True,
        },
        "requestContract": {
            "schemaVersion": EXTERNAL_OCR_REQUEST_SCHEMA_VERSION,
            "required": [
                "requestId",
                "requestHash",
                "tenantId",
                "projectId",
                "archiveId",
                "objectKey",
                "objectVersion",
                "documentVersionId",
                "fileSha256",
                "pageNumbers",
                "pageGeometry",
                "config",
                "executorPolicy",
            ],
            "canonicalRequestHashRequired": True,
            "sourceTransferMode": "immutable_object_reference_only",
            "inlineBytesAllowed": False,
        },
        "responseContract": {
            "schemaVersion": EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION,
            "required": [
                "requestId",
                "requestHash",
                "status",
                "tenantId",
                "projectId",
                "archiveId",
                "objectKey",
                "objectVersion",
                "documentVersionId",
                "fileSha256",
                "completedPages",
                "executorId",
                "executorVersion",
                "chunks",
            ],
            "chunkRequired": [
                "pageNumber",
                "readingOrderIndex",
                "text",
                "textSha256",
                "bbox",
                "confidence",
            ],
            "coordinateSpace": "pdf_points_top_left",
            "readingOrderIndexBase": 1,
            "extraFieldsAllowed": False,
            "responseMustBeValidated": True,
        },
        "authorityPolicy": {
            "authorityStatus": "candidate_evidence",
            "approvalEligible": False,
            "reportEligible": False,
            "factPromotionAllowed": False,
        },
    }


def validate_external_ocr_response(
    response: Mapping[str, Any],
    *,
    plan: ExternalOcrExecutionPlan,
) -> ExtractionArtifact:
    """Validate and normalize executor output as non-authoritative chunks."""

    if plan.status != "ready" or not plan.request:
        raise OcrResponseValidationError(
            "OCR response cannot be accepted without a ready execution plan"
        )
    payload = dict(response or {})
    request = dict(plan.request)
    _validate_external_ocr_request(request)
    _require_exact_fields(
        payload,
        _EXTERNAL_OCR_RESPONSE_FIELDS,
        label="OCR response",
    )
    if payload["schemaVersion"] != EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION:
        raise OcrResponseValidationError(
            "OCR response schema version is unsupported"
        )
    if payload["status"] != "completed":
        raise OcrResponseValidationError(
            "OCR executor did not report completed status"
        )
    for field_name in (
        "requestId",
        "requestHash",
        "tenantId",
        "projectId",
        "archiveId",
        "objectKey",
        "objectVersion",
        "documentVersionId",
        "fileSha256",
    ):
        if payload[field_name] != request[field_name]:
            raise OcrResponseValidationError(
                f"OCR response {field_name} does not match the execution plan"
            )
    executor_id = _required_string(
        payload["executorId"],
        label="OCR executorId",
    )
    executor_version = _required_string(
        payload["executorVersion"],
        label="OCR executorVersion",
    )

    requested_pages = tuple(request["pageNumbers"])
    completed_pages = _page_numbers(payload["completedPages"])
    if completed_pages != requested_pages:
        raise OcrResponseValidationError(
            "OCR response must complete every requested page exactly once"
        )
    page_geometry = _request_page_geometry(request)
    raw_chunks = payload["chunks"]
    if not isinstance(raw_chunks, list):
        raise OcrResponseValidationError(
            "OCR response chunks must be a list"
        )
    if len(raw_chunks) > MAX_EXTRACTION_CHUNKS:
        raise OcrResponseValidationError(
            "OCR response exceeds the extraction chunk limit"
        )

    chunks: list[ExtractionChunk] = []
    observed_pages: set[int] = set()
    seen_chunk_ids: set[str] = set()
    next_reading_order = {
        page_number: 1 for page_number in requested_pages
    }
    previous_order: tuple[int, int] | None = None
    for raw_chunk in raw_chunks:
        if not isinstance(raw_chunk, Mapping):
            raise OcrResponseValidationError(
                "Every OCR response chunk must be an object"
            )
        _require_exact_fields(
            raw_chunk,
            _EXTERNAL_OCR_CHUNK_FIELDS,
            label="OCR response chunk",
        )
        page_number = _positive_int(raw_chunk["pageNumber"])
        if page_number not in requested_pages:
            raise OcrResponseValidationError(
                "OCR chunk page is outside the requested page set"
            )
        reading_order_index = _positive_int(
            raw_chunk["readingOrderIndex"]
        )
        if (
            reading_order_index
            != next_reading_order[page_number]
        ):
            raise OcrResponseValidationError(
                "OCR readingOrderIndex must be contiguous and start at 1 "
                "for every page"
            )
        order = (page_number, reading_order_index)
        if previous_order is not None and order <= previous_order:
            raise OcrResponseValidationError(
                "OCR chunks must be sorted by pageNumber and readingOrderIndex"
            )
        previous_order = order
        next_reading_order[page_number] += 1

        text = raw_chunk["text"]
        if not isinstance(text, str) or not text.strip():
            raise OcrResponseValidationError(
                "OCR chunks require non-empty text"
            )
        if len(text) > MAX_CHUNK_TEXT_CHARS:
            raise OcrResponseValidationError(
                "OCR chunk exceeds the text limit"
            )
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if raw_chunk["textSha256"] != text_hash:
            raise OcrResponseValidationError(
                "OCR chunk text hash verification failed"
            )
        bbox = _normalized_bbox(raw_chunk["bbox"])
        if bbox is None:
            raise OcrResponseValidationError(
                "OCR chunk requires a valid PDF-point bbox"
            )
        if not _bbox_contains(
            list(page_geometry[page_number]),
            bbox,
        ):
            raise OcrResponseValidationError(
                "OCR chunk bbox falls outside the requested page geometry"
            )
        confidence = _confidence(raw_chunk["confidence"])
        chunk_id = "ocr-chunk-" + _stable_hash(
            {
                "requestHash": request["requestHash"],
                "pageNumber": page_number,
                "readingOrderIndex": reading_order_index,
                "bbox": bbox,
                "textSha256": text_hash,
            }
        )[:24]
        if chunk_id in seen_chunk_ids:
            raise OcrResponseValidationError(
                "OCR response contains a duplicate normalized chunk"
            )
        seen_chunk_ids.add(chunk_id)
        observed_pages.add(page_number)
        chunks.append(
            ExtractionChunk(
                chunk_id=chunk_id,
                kind="pdf_ocr_text_block",
                text=text,
                source_locator={
                    "documentId": request["archiveId"],
                    "documentName": request["originalFileName"],
                    "pageNumber": page_number,
                    "sourceType": "external_ocr_page_bbox",
                    "metadata": {
                        "bbox": bbox,
                        "pageBBox": list(
                            page_geometry[page_number]
                        ),
                        "bboxCoordinateSystem": (
                            "pdf_points_top_left"
                        ),
                    },
                },
                metadata={
                    "textSha256": text_hash,
                    "confidence": confidence,
                    "executorId": executor_id,
                    "executorVersion": executor_version,
                    "readingOrderIndex": reading_order_index,
                    "authorityStatus": "candidate_evidence",
                    "approvalEligible": False,
                    "reportEligible": False,
                    "factPromotionAllowed": False,
                },
            )
        )
    if observed_pages != set(requested_pages):
        raise OcrResponseValidationError(
            "OCR response returned no structural text for a requested page"
        )

    return ExtractionArtifact(
        adapter_id="external_pdf_ocr",
        adapter_version=EXTERNAL_OCR_CONTRACT_VERSION,
        document_id=request["archiveId"],
        chunks=tuple(chunks),
        complete=True,
        metadata={
            "schemaVersion": EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION,
            "requestId": request["requestId"],
            "requestHash": request["requestHash"],
            "documentVersionId": request["documentVersionId"],
            "objectVersion": request["objectVersion"],
            "completedPages": list(completed_pages),
            "executorId": executor_id,
            "executorVersion": executor_version,
            "authorityStatus": "candidate_evidence",
            "approvalEligible": False,
            "reportEligible": False,
            "factPromotionAllowed": False,
        },
    )


def bind_external_ocr_response_to_document_version(
    response: Mapping[str, Any],
    *,
    plan: ExternalOcrExecutionPlan,
    document: ArchivedDocument,
    document_version_id: str,
    document_id: str | None = None,
) -> ExtractionArtifact:
    """Validate executor output, then bind every chunk to strict evidence."""

    if plan.status != "ready" or not plan.request:
        raise OcrResponseValidationError(
            "OCR evidence binding requires a ready execution plan"
        )
    version_id = str(document_version_id or "").strip()
    if not version_id:
        raise OcrResponseValidationError(
            "OCR evidence binding requires document_version_id"
        )
    request = plan.request
    expected_request_identity = {
        "tenantId": document.tenant_id,
        "projectId": document.project_id,
        "archiveId": document.archive_id,
        "objectKey": document.object_key,
        "objectVersion": document.object_version,
        "documentVersionId": version_id,
        "fileSha256": document.sha256,
        "originalFileName": document.filename,
        "mediaType": document.content_type or "application/pdf",
    }
    if any(
        request.get(key) != expected
        for key, expected in expected_request_identity.items()
    ):
        raise OcrResponseValidationError(
            "OCR execution plan does not match the current document version"
        )

    artifact = validate_external_ocr_response(response, plan=plan)
    bound = bind_artifact_to_document_version(
        artifact,
        document=document,
        document_version_id=version_id,
        document_id=document_id,
    )
    expected_document_id = str(
        document_id or document.archive_id
    )
    parser_method = (
        f"{bound.adapter_id}@{bound.adapter_version}"
    )
    for chunk in bound.chunks:
        locator = SourceLocator.model_validate(
            chunk.source_locator
        )
        expected_raw_hash = hashlib.sha256(
            chunk.text.encode("utf-8")
        ).hexdigest()
        expected_evidence_id = _evidence_id(
            version_id,
            chunk.chunk_id,
        )
        if (
            locator.document_id != expected_document_id
            or locator.document_version_id != version_id
            or locator.file_sha256 != document.sha256
            or locator.raw_text_sha256 != expected_raw_hash
            or locator.evidence_id != expected_evidence_id
            or locator.parser_method != parser_method
            or locator.bbox is None
            or locator.coordinate_space
            != "pdf_points_top_left"
        ):
            raise OcrResponseValidationError(
                "Bound OCR evidence locator failed strict attestation"
            )
    return bound


def _external_ocr_request(
    *,
    document: ArchivedDocument,
    assessment: PdfOcrAssessment,
    document_version_id: str,
    executor_endpoint: str,
    allow_insecure_http: bool,
) -> dict[str, Any]:
    parsed_endpoint = urlsplit(executor_endpoint)
    requested_pages = set(assessment.ocr_required_pages)
    request_body = {
        "schemaVersion": EXTERNAL_OCR_REQUEST_SCHEMA_VERSION,
        "contractVersion": EXTERNAL_OCR_CONTRACT_VERSION,
        "tenantId": document.tenant_id,
        "projectId": document.project_id,
        "archiveId": document.archive_id,
        "objectKey": document.object_key,
        "objectVersion": document.object_version,
        "documentVersionId": document_version_id,
        "fileSha256": document.sha256,
        "originalFileName": document.filename,
        "mediaType": document.content_type or "application/pdf",
        "pageNumbers": list(assessment.ocr_required_pages),
        "pageGeometry": [
            geometry.to_dict()
            for geometry in assessment.page_geometries
            if geometry.page_number in requested_pages
        ],
        "config": {
            "coordinateSpace": "pdf_points_top_left",
            "readingOrderIndexBase": 1,
            "readingOrderPolicy": "page_then_index_contiguous",
            "pageCoveragePolicy": "all_requested_pages",
            "maxChunkTextChars": MAX_CHUNK_TEXT_CHARS,
            "maxExtractionChunks": MAX_EXTRACTION_CHUNKS,
        },
        "executorPolicy": {
            "moduleManifestRef": EXTERNAL_PDF_OCR_MANIFEST.ref,
            "transportScheme": parsed_endpoint.scheme,
            "endpointSha256": hashlib.sha256(
                executor_endpoint.encode("utf-8")
            ).hexdigest(),
            "insecureHttpDevelopmentOptIn": allow_insecure_http,
            "sourceTransferMode": "immutable_object_reference_only",
            "responseValidationRequired": True,
        },
        "authorityPolicy": {
            "approvalEligible": False,
            "reportEligible": False,
            "factPromotionAllowed": False,
        },
    }
    request_hash = _stable_hash(request_body)
    request = {
        **request_body,
        "requestId": "ocr-request-" + request_hash[:24],
        "requestHash": request_hash,
    }
    _validate_external_ocr_request(request)
    return request


def _executor_endpoint_error(
    endpoint: str,
    *,
    allow_insecure_http: bool,
) -> str | None:
    if not endpoint:
        return (
            f"{OCR_EXECUTOR_ENV_VAR} is not configured; image-only PDF "
            "pages remain blocked."
        )
    parsed = urlsplit(endpoint)
    if not parsed.netloc or parsed.username is not None or parsed.password is not None:
        return (
            f"{OCR_EXECUTOR_ENV_VAR} must be an HTTPS URL without "
            "embedded credentials."
        )
    if parsed.scheme == "https":
        return None
    if (
        parsed.scheme == "http"
        and allow_insecure_http
        and (parsed.hostname or "").casefold()
        in {"localhost", "127.0.0.1", "::1"}
    ):
        return None
    return (
        f"{OCR_EXECUTOR_ENV_VAR} must use HTTPS. Local HTTP is allowed only "
        f"with explicit {OCR_ALLOW_INSECURE_HTTP_ENV_VAR}=true opt-in."
    )


def _validate_external_ocr_request(
    request: Mapping[str, Any],
) -> None:
    _require_exact_fields(
        request,
        _EXTERNAL_OCR_REQUEST_FIELDS,
        label="OCR request",
    )
    if request["schemaVersion"] != EXTERNAL_OCR_REQUEST_SCHEMA_VERSION:
        raise OcrResponseValidationError(
            "OCR request schema version is unsupported"
        )
    if request["contractVersion"] != EXTERNAL_OCR_CONTRACT_VERSION:
        raise OcrResponseValidationError(
            "OCR request contract version is unsupported"
        )
    for field_name in (
        "tenantId",
        "projectId",
        "archiveId",
        "objectKey",
        "objectVersion",
        "documentVersionId",
        "fileSha256",
        "originalFileName",
        "mediaType",
    ):
        _required_string(
            request[field_name],
            label=f"OCR request {field_name}",
        )
    if not _is_sha256(request["archiveId"]) or not _is_sha256(
        request["fileSha256"]
    ):
        raise OcrResponseValidationError(
            "OCR request archive and file hashes must be SHA-256"
        )
    if request["archiveId"] != request["fileSha256"]:
        raise OcrResponseValidationError(
            "OCR request archiveId must equal the immutable file hash"
        )

    requested_pages = _page_numbers(request["pageNumbers"])
    page_geometry = _request_page_geometry(request)
    if tuple(page_geometry) != requested_pages:
        raise OcrResponseValidationError(
            "OCR request page geometry must match requested pages"
        )

    config = request["config"]
    _require_exact_fields(
        config,
        {
            "coordinateSpace",
            "readingOrderIndexBase",
            "readingOrderPolicy",
            "pageCoveragePolicy",
            "maxChunkTextChars",
            "maxExtractionChunks",
        },
        label="OCR request config",
    )
    expected_config = {
        "coordinateSpace": "pdf_points_top_left",
        "readingOrderIndexBase": 1,
        "readingOrderPolicy": "page_then_index_contiguous",
        "pageCoveragePolicy": "all_requested_pages",
        "maxChunkTextChars": MAX_CHUNK_TEXT_CHARS,
        "maxExtractionChunks": MAX_EXTRACTION_CHUNKS,
    }
    if dict(config) != expected_config:
        raise OcrResponseValidationError(
            "OCR request config does not match the contract policy"
        )

    executor_policy = request["executorPolicy"]
    _require_exact_fields(
        executor_policy,
        {
            "moduleManifestRef",
            "transportScheme",
            "endpointSha256",
            "insecureHttpDevelopmentOptIn",
            "sourceTransferMode",
            "responseValidationRequired",
        },
        label="OCR request executorPolicy",
    )
    if (
        executor_policy["moduleManifestRef"]
        != EXTERNAL_PDF_OCR_MANIFEST.ref
        or executor_policy["transportScheme"]
        not in {"https", "http"}
        or not _is_sha256(executor_policy["endpointSha256"])
        or not isinstance(
            executor_policy["insecureHttpDevelopmentOptIn"],
            bool,
        )
        or (
            executor_policy["transportScheme"] == "http"
            and executor_policy[
                "insecureHttpDevelopmentOptIn"
            ]
            is not True
        )
        or executor_policy["sourceTransferMode"]
        != "immutable_object_reference_only"
        or executor_policy["responseValidationRequired"] is not True
    ):
        raise OcrResponseValidationError(
            "OCR request executor policy is invalid"
        )

    authority_policy = request["authorityPolicy"]
    _require_exact_fields(
        authority_policy,
        {
            "approvalEligible",
            "reportEligible",
            "factPromotionAllowed",
        },
        label="OCR request authorityPolicy",
    )
    if dict(authority_policy) != {
        "approvalEligible": False,
        "reportEligible": False,
        "factPromotionAllowed": False,
    }:
        raise OcrResponseValidationError(
            "OCR request authority policy is invalid"
        )

    request_body = {
        key: request[key]
        for key in request
        if key not in {"requestId", "requestHash"}
    }
    expected_hash = _stable_hash(request_body)
    if request["requestHash"] != expected_hash:
        raise OcrResponseValidationError(
            "OCR request canonical hash verification failed"
        )
    if request["requestId"] != "ocr-request-" + expected_hash[:24]:
        raise OcrResponseValidationError(
            "OCR requestId does not match the canonical request hash"
        )


def _request_page_geometry(
    request: Mapping[str, Any],
) -> dict[int, tuple[float, float, float, float]]:
    raw_geometry = request.get("pageGeometry")
    if not isinstance(raw_geometry, list):
        raise OcrResponseValidationError(
            "OCR request pageGeometry must be a list"
        )
    geometry: dict[int, tuple[float, float, float, float]] = {}
    prior_page = 0
    for raw_page in raw_geometry:
        if not isinstance(raw_page, Mapping):
            raise OcrResponseValidationError(
                "OCR request page geometry must be an object"
            )
        _require_exact_fields(
            raw_page,
            {"pageNumber", "pageBBox"},
            label="OCR request page geometry",
        )
        page_number = _positive_int(raw_page["pageNumber"])
        page_bbox = _normalized_bbox(raw_page["pageBBox"])
        if (
            not page_number
            or page_number <= prior_page
            or page_bbox is None
        ):
            raise OcrResponseValidationError(
                "OCR request page geometry must be sorted and valid"
            )
        prior_page = page_number
        geometry[page_number] = tuple(page_bbox)
    return geometry


def _require_exact_fields(
    value: Mapping[str, Any] | Any,
    expected: set[str] | frozenset[str],
    *,
    label: str,
) -> None:
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise OcrResponseValidationError(
            f"{label} fields do not match the strict contract"
        )


def _required_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OcrResponseValidationError(
            f"{label} must be a non-empty string"
        )
    return value


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _environment_flag(value: Any) -> bool:
    return str(value or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _bbox_contains(
    outer: list[float],
    inner: list[float],
    *,
    tolerance: float = 0.001,
) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[2] <= outer[2] + tolerance
        and inner[3] <= outer[3] + tolerance
    )


def _evidence_id(document_version_id: str, chunk_id: str) -> str:
    material = f"{document_version_id}|{chunk_id}".encode("utf-8")
    return (
        "parser-evidence:"
        + hashlib.sha256(material).hexdigest()[:32]
    )


def _supports_pdf(document: ArchivedDocument) -> bool:
    return (
        document.extension == ".pdf"
        or str(document.content_type or "").casefold()
        == "application/pdf"
    )


def _page_numbers(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise OcrResponseValidationError(
            "OCR completedPages must be a list"
        )
    pages = tuple(_positive_int(item) for item in value)
    if not all(pages) or len(set(pages)) != len(pages):
        raise OcrResponseValidationError(
            "OCR completedPages must contain unique positive integers"
        )
    if tuple(sorted(pages)) != pages:
        raise OcrResponseValidationError(
            "OCR completedPages must be sorted"
        )
    return pages


def _positive_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if value > 0 else 0


def _normalized_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(item, bool) for item in value):
        return None
    try:
        bbox = [round(float(item), 3) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in bbox):
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _confidence(value: Any) -> float:
    if isinstance(value, bool):
        raise OcrResponseValidationError(
            "OCR confidence must be numeric"
        )
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise OcrResponseValidationError(
            "OCR confidence must be numeric"
        ) from exc
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise OcrResponseValidationError(
            "OCR confidence must be between 0 and 1"
        )
    return confidence


def _stable_hash(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OcrResponseValidationError(
            "OCR contract values must be canonical JSON"
        ) from exc
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "EXTERNAL_OCR_CONTRACT_VERSION",
    "EXTERNAL_OCR_REQUEST_SCHEMA_VERSION",
    "EXTERNAL_OCR_RESPONSE_SCHEMA_VERSION",
    "EXTERNAL_PDF_OCR_MANIFEST",
    "OCR_ALLOW_INSECURE_HTTP_ENV_VAR",
    "OCR_EXECUTOR_ENV_VAR",
    "OCR_LARGE_RASTER_IMAGE_COVERAGE_THRESHOLD",
    "OCR_MIN_VISIBLE_NATIVE_TEXT_CHARACTERS",
    "OCR_MIN_VISIBLE_NATIVE_TEXT_DENSITY",
    "PDF_OCR_ASSESSMENT_POLICY_VERSION",
    "PDF_OCR_ASSESSMENT_SCHEMA_VERSION",
    "ExternalOcrExecutionPlan",
    "ExternalOcrExecutor",
    "OcrRequiredDispatchBlocked",
    "OcrRequiredExecutionPending",
    "OcrResponseValidationError",
    "PdfOcrAssessment",
    "PdfOcrDiagnostic",
    "PdfPageGeometry",
    "assess_pdf_ocr_requirement",
    "bind_external_ocr_response_to_document_version",
    "external_ocr_executor_contract",
    "plan_external_pdf_ocr",
    "validate_external_ocr_response",
]
