from __future__ import annotations

"""Bounded local PDF reading. Originals and financial facts are never rewritten."""

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Sequence

import fitz

from parsing_platform.adapters import ArchivedDocument, MAX_SOURCE_BYTES
from parsing_platform.pdf_ocr import assess_pdf_ocr_requirement, PDF_OCR_ASSESSMENT_POLICY_VERSION
from parsing_platform.ocr_language_data import (
    OcrLanguageDataUnavailable, verified_tessdata_dir,
    reading_engine_metadata, reading_engine_fingerprint, TESSDATA_ENV,
)

SCHEMA_VERSION = "realytics-local-pdf-reading-v1"
READER_POLICY_VERSION = "local-pdf-reading-300dpi-v1"
MAX_SELECTED_PAGES = 3
OCR_DPI = 300
MAX_OCR_PIXELS = 12_000_000
MAX_PAGE_TEXT_CHARS = 100_000
MAX_PAGE_BLOCKS = 5_000
MAX_WORKER_RESPONSE_BYTES = 900_000
MAX_WORKER_REQUEST_HEADER_BYTES = 16_384
MAX_PAGE_TIMEOUT_SECONDS = 20.0
_OCR_SLOTS = threading.BoundedSemaphore(1)


class LocalPdfReadingError(ValueError):
    pass


class _OcrFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def get_local_pdf_reading_fingerprint(*, tessdata_dir: str | Path | None = None) -> str:
    """Stable policy/model identity; no rendering, model reads, or network I/O."""
    return _json_hash({
        "engineFingerprint": reading_engine_fingerprint(),
        "policyVersion": READER_POLICY_VERSION,
        "assessmentPolicyVersion": PDF_OCR_ASSESSMENT_POLICY_VERSION,
        "dpi": OCR_DPI, "maxPixels": MAX_OCR_PIXELS,
        "maxTextChars": MAX_PAGE_TEXT_CHARS, "maxBlocks": MAX_PAGE_BLOCKS,
        "maxPageResponseBytes": MAX_WORKER_RESPONSE_BYTES,
    })


def _runtime(tessdata_dir: str | Path | None) -> tuple[Path | None, dict[str, Any]]:
    directory = None
    error = None
    try:
        directory = verified_tessdata_dir(tessdata_dir)
    except (OcrLanguageDataUnavailable, OSError, ValueError) as exc:
        error = str(exc) if isinstance(exc, OcrLanguageDataUnavailable) else "OCR_LANGUAGE_DATA_UNAVAILABLE"
    metadata = {
        **reading_engine_metadata(), "policyVersion": READER_POLICY_VERSION,
        "assessmentPolicyVersion": PDF_OCR_ASSESSMENT_POLICY_VERSION,
        "dpi": OCR_DPI, "maxPixels": MAX_OCR_PIXELS,
        "available": directory is not None, "unavailableReason": error,
        "fingerprint": get_local_pdf_reading_fingerprint(),
    }
    return directory, metadata


def _validate_source(document: ArchivedDocument) -> None:
    if not isinstance(document, ArchivedDocument):
        raise LocalPdfReadingError("an archived PDF source is required")
    if any(not isinstance(value, str) or not value.strip() for value in (document.tenant_id, document.project_id, document.object_key, document.object_version)):
        raise LocalPdfReadingError("tenant and immutable object version are required")
    if not 0 < len(document.content) <= MAX_SOURCE_BYTES:
        raise LocalPdfReadingError("PDF source size is outside the supported bound")
    if document.archive_id.lower() != document.sha256:
        raise LocalPdfReadingError("PDF source hash does not match the archived version")
    if not document.content.startswith(b"%PDF"):
        raise LocalPdfReadingError("source is not a PDF")


def _selected_pages(page_numbers: Sequence[int]) -> list[int]:
    if not isinstance(page_numbers, (list, tuple)) or not 1 <= len(page_numbers) <= MAX_SELECTED_PAGES:
        raise LocalPdfReadingError("select between one and three PDF pages")
    if any(type(number) is not int or number < 1 for number in page_numbers):
        raise LocalPdfReadingError("page numbers must be positive one-based integers")
    if len(set(page_numbers)) != len(page_numbers):
        raise LocalPdfReadingError("duplicate page numbers are not allowed")
    return sorted(page_numbers)


def _page_content(page: fitz.Page, *, textpage: Any = None) -> dict[str, Any]:
    text = page.get_text("text", textpage=textpage, sort=True)
    if len(text) > MAX_PAGE_TEXT_CHARS:
        raise _OcrFailure("PDF_PAGE_TEXT_LIMIT_EXCEEDED")
    blocks: list[dict[str, Any]] = []
    for raw in page.get_text("blocks", textpage=textpage, sort=True):
        if len(raw) < 7 or raw[6] != 0 or not str(raw[4]).strip():
            continue
        rect = fitz.Rect(raw[:4]) * page.rotation_matrix
        rect = rect & page.rect
        bbox = [round(float(value), 3) for value in rect]
        if not all(math.isfinite(value) for value in bbox) or rect.is_empty:
            raise _OcrFailure("PDF_PAGE_GEOMETRY_INVALID")
        blocks.append({"text": str(raw[4]), "bbox": bbox})
        if len(blocks) > MAX_PAGE_BLOCKS:
            raise _OcrFailure("PDF_PAGE_BLOCK_LIMIT_EXCEEDED")
    result = {"text": text, "textSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "blocks": blocks}
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_WORKER_RESPONSE_BYTES - 512:
        raise _OcrFailure("PDF_PAGE_RESPONSE_SIZE_LIMIT_EXCEEDED")
    return result


def _empty_page(page: fitz.Page, page_number: int, source_kind: str) -> dict[str, Any]:
    return {
        "pageNumber": page_number, "status": "empty", "sourceKind": source_kind,
        "text": "", "textSha256": hashlib.sha256(b"").hexdigest(), "blocks": [],
        "width": float(page.rect.width), "height": float(page.rect.height),
        "rotation": int(page.rotation), "coordinateSpace": "pdf_points_top_left",
        "confidence": None, "confidenceAvailable": False,
    }


def _pixel_count(page: fitz.Page) -> int:
    return math.ceil(page.rect.width * OCR_DPI / 72) * math.ceil(page.rect.height * OCR_DPI / 72)


def _run_ocr_page(content: bytes, page_number: int, directory: Path, timeout_seconds: float) -> dict[str, Any]:
    if not _OCR_SLOTS.acquire(blocking=False):
        raise _OcrFailure("OCR_BUSY")
    try:
        return _run_isolated_ocr(content, page_number, directory, timeout_seconds)
    finally:
        _OCR_SLOTS.release()


def _run_isolated_ocr(content: bytes, page_number: int, directory: Path, timeout_seconds: float) -> dict[str, Any]:
    """The native OCR call runs only in a disposable process with a hard timeout."""
    command = [sys.executable, "-B", "-m", "parsing_platform.local_pdf_reading", "--ocr-worker"]
    header = json.dumps({"pageNumber": page_number, "tessdataDir": str(directory)}, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(header) > MAX_WORKER_REQUEST_HEADER_BYTES:
        raise _OcrFailure("OCR_REQUEST_INVALID")
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False,
            cwd=Path(__file__).resolve().parents[1], close_fds=True,
            env={**os.environ, "OMP_THREAD_LIMIT": "1", "OMP_NUM_THREADS": "1"},
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as exc:
        raise _OcrFailure("OCR_EXECUTOR_UNAVAILABLE") from exc
    try:
        output, _ = process.communicate(header + content, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise _OcrFailure("OCR_TIMEOUT") from exc
    if process.returncode != 0 or len(output) > MAX_WORKER_RESPONSE_BYTES:
        raise _OcrFailure("OCR_EXECUTION_FAILED")
    try:
        result = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _OcrFailure("OCR_RESPONSE_INVALID") from exc
    if not isinstance(result, dict):
        raise _OcrFailure("OCR_RESPONSE_INVALID")
    if result.get("errorCode"):
        raise _OcrFailure(str(result["errorCode"]))
    return result


def _validate_ocr_result(result: dict[str, Any], *, content: bytes, page_number: int, page: fitz.Page) -> dict[str, Any]:
    if result.get("pageNumber") != page_number or result.get("fileSha256") != hashlib.sha256(content).hexdigest():
        raise _OcrFailure("OCR_SOURCE_BINDING_MISMATCH")
    text = result.get("text")
    blocks = result.get("blocks")
    if not isinstance(text, str) or len(text) > MAX_PAGE_TEXT_CHARS or not isinstance(blocks, list) or len(blocks) > MAX_PAGE_BLOCKS:
        raise _OcrFailure("OCR_RESPONSE_INVALID")
    if result.get("textSha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
        raise _OcrFailure("OCR_TEXT_INTEGRITY_MISMATCH")
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            raise _OcrFailure("OCR_RESPONSE_INVALID")
        bbox = block.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4 or any(type(value) not in (int, float) or not math.isfinite(value) for value in bbox):
            raise _OcrFailure("OCR_PAGE_GEOMETRY_INVALID")
        if not 0 <= bbox[0] < bbox[2] <= page.rect.width + 0.001 or not 0 <= bbox[1] < bbox[3] <= page.rect.height + 0.001:
            raise _OcrFailure("OCR_PAGE_GEOMETRY_INVALID")
    if text.strip() and not blocks:
        raise _OcrFailure("OCR_LOCATORS_MISSING")
    return {"text": text, "textSha256": result["textSha256"], "blocks": blocks}


def read_pdf_pages(
    document: ArchivedDocument, *, page_numbers: Sequence[int],
    tessdata_dir: str | Path | None = None, timeout_seconds: float = MAX_PAGE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Read at most three selected pages; unselected required OCR stays pending."""
    _validate_source(document)
    selected = _selected_pages(page_numbers)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= MAX_PAGE_TIMEOUT_SECONDS:
        raise LocalPdfReadingError("OCR page timeout must be above zero and at most twenty seconds")
    assessment = assess_pdf_ocr_requirement(document)
    if any(number > assessment.page_count for number in selected):
        raise LocalPdfReadingError("selected page is outside the PDF")
    directory, runtime = _runtime(tessdata_dir)
    required = set(assessment.ocr_required_pages)
    pages: list[dict[str, Any]] = []
    completed: list[int] = []
    failed: list[int] = []
    with fitz.open(stream=document.content, filetype="pdf") as pdf:
        for number in selected:
            page = pdf.load_page(number - 1)
            is_ocr = number in required
            output = _empty_page(page, number, "ocr" if is_ocr else "native")
            try:
                if is_ocr:
                    if _pixel_count(page) > MAX_OCR_PIXELS:
                        raise _OcrFailure("OCR_PAGE_PIXEL_LIMIT_EXCEEDED")
                    if directory is None:
                        raise _OcrFailure(str(runtime["unavailableReason"] or "OCR_LANGUAGE_DATA_UNAVAILABLE"))
                    result = _run_ocr_page(document.content, number, directory, float(timeout_seconds))
                    output.update(_validate_ocr_result(result, content=document.content, page_number=number, page=page))
                    if not output["text"].strip():
                        raise _OcrFailure("OCR_TEXT_EMPTY")
                    completed.append(number)
                else:
                    output.update(_page_content(page))
                output["status"] = "completed" if output["text"].strip() else "empty"
            except _OcrFailure as exc:
                output.update({"text": "", "textSha256": hashlib.sha256(b"").hexdigest(), "blocks": []})
                output.update({"status": "timeout" if exc.code == "OCR_TIMEOUT" else "unavailable" if exc.code in {"OCR_LANGUAGE_DATA_UNAVAILABLE", "OCR_EXECUTOR_UNAVAILABLE", "OCR_BUSY"} else "failed", "errorCode": exc.code})
                failed.append(number)
            except Exception:
                output.update({"text": "", "textSha256": hashlib.sha256(b"").hexdigest(), "blocks": []})
                output.update({"status": "failed", "errorCode": "PDF_PAGE_READING_FAILED"})
                failed.append(number)
            pages.append(output)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "tenantId": document.tenant_id, "projectId": document.project_id,
        "archiveId": document.archive_id, "objectKey": document.object_key,
        "objectVersion": document.object_version, "fileSha256": document.sha256,
        "readerFingerprint": runtime["fingerprint"], "reader": runtime,
        "pageCount": assessment.page_count, "pages": pages,
        "ocrRequiredPages": sorted(required), "completedOcrPages": completed,
        "pendingOcrPages": sorted(required.difference(completed)), "failedPages": failed,
        "analysisOnly": True, "reportEligible": False, "factPromotionAllowed": False,
    }


def _worker_main() -> int:
    """No original file writes, network requests, image response, or model calls."""
    try:
        if sys.platform.startswith("linux"):
            import resource
            memory_limit = 512 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        header = sys.stdin.buffer.readline(MAX_WORKER_REQUEST_HEADER_BYTES + 1)
        if not header.endswith(b"\n") or len(header) > MAX_WORKER_REQUEST_HEADER_BYTES:
            raise _OcrFailure("OCR_REQUEST_INVALID")
        request = json.loads(header)
        if (
            not isinstance(request, dict) or set(request) != {"pageNumber", "tessdataDir"}
            or type(request["pageNumber"]) is not int or request["pageNumber"] < 1
            or not isinstance(request["tessdataDir"], str) or not request["tessdataDir"]
        ):
            raise _OcrFailure("OCR_REQUEST_INVALID")
        number = request["pageNumber"]
        try:
            directory = verified_tessdata_dir(request["tessdataDir"])
        except OcrLanguageDataUnavailable as exc:
            raise _OcrFailure(str(exc)) from exc
        content = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
        if not 0 < len(content) <= MAX_SOURCE_BYTES:
            raise _OcrFailure("PDF_SOURCE_SIZE_LIMIT_EXCEEDED")
        with fitz.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass or not 1 <= number <= pdf.page_count:
                raise _OcrFailure("PDF_SOURCE_INVALID")
            page = pdf[number - 1]
            if _pixel_count(page) > MAX_OCR_PIXELS:
                raise _OcrFailure("OCR_PAGE_PIXEL_LIMIT_EXCEEDED")
            textpage = page.get_textpage_ocr(language="kor+eng", dpi=OCR_DPI, full=True, tessdata=str(directory))
            result = {"pageNumber": number, "fileSha256": hashlib.sha256(content).hexdigest(), **_page_content(page, textpage=textpage)}
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_WORKER_RESPONSE_BYTES:
            raise _OcrFailure("OCR_RESPONSE_SIZE_LIMIT_EXCEEDED")
    except _OcrFailure as exc:
        encoded = json.dumps({"errorCode": exc.code}).encode("utf-8")
    except Exception:
        encoded = b'{"errorCode":"OCR_EXECUTION_FAILED"}'
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "--ocr-worker":
        raise SystemExit(2)
    raise SystemExit(_worker_main())
