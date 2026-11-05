from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import fitz
import pytest

from parsing_platform.adapters import ArchivedDocument
from parsing_platform import local_pdf_reading as reading
from parsing_platform import ocr_language_data


def _document(content: bytes) -> ArchivedDocument:
    return ArchivedDocument(project_id="reading-project", tenant_id="reading-tenant", archive_id=hashlib.sha256(content).hexdigest(), filename="reading.pdf", content_type="application/pdf", content=content, object_key="original/reading.pdf", object_version="generation-17")


def _pdf(*kinds: str, rotation: int = 0) -> bytes:
    with fitz.open() as pdf:
        for kind in kinds:
            page = pdf.new_page(width=500, height=220)
            if kind == "native":
                page.insert_text((30, 45), "Native project amount 123,456 with enough readable context", fontsize=13)
            elif kind == "scan":
                with fitz.open() as source:
                    source_page = source.new_page(width=500, height=220)
                    source_page.insert_text((30, 55), "PROJECT TOTAL 123,456", fontsize=22)
                    source_page.insert_text((30, 100), "총사업비 123,456원", fontname="korea", fontsize=22)
                    image = source_page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
                page.insert_image(page.rect, stream=image)
            page.set_rotation(rotation)
        return pdf.tobytes()


@pytest.fixture
def language_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    hashes = {}
    for name in ("kor.traineddata", "eng.traineddata"):
        content = f"controlled fake model {name}".encode()
        (tmp_path / name).write_bytes(content)
        hashes[name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    monkeypatch.setattr(ocr_language_data, "MODEL_FILES", hashes)
    return tmp_path


def _result(content: bytes, page_number: int, text: str = "OCR amount 123,456") -> dict:
    return {"fileSha256": hashlib.sha256(content).hexdigest(), "pageNumber": page_number, "text": text, "textSha256": hashlib.sha256(text.encode()).hexdigest(), "blocks": [{"text": text, "bbox": [10, 10, 220, 35]}]}


def test_native_pages_do_not_invoke_ocr_and_keep_original_text_and_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _document(_pdf("native"))
    monkeypatch.setattr(reading, "_run_ocr_page", lambda *_: pytest.fail("native page invoked OCR"))
    result = reading.read_pdf_pages(source, page_numbers=[1])
    with fitz.open(stream=source.content, filetype="pdf") as pdf:
        expected = pdf[0].get_text("text", sort=True)
    assert result["pages"][0]["text"] == expected
    assert result["pages"][0]["sourceKind"] == "native"
    assert result["pages"][0]["status"] == "completed"
    assert result["ocrRequiredPages"] == result["pendingOcrPages"] == []
    assert result["fileSha256"] == hashlib.sha256(source.content).hexdigest()
    assert result["objectVersion"] == "generation-17"
    assert result["reportEligible"] is result["factPromotionAllowed"] is False


def test_selected_scans_only_are_executed_and_unselected_pages_stay_pending(language_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _document(_pdf("scan", "native", "scan"))
    called = []
    def execute(content, page_number, directory, timeout):
        called.append((page_number, directory, timeout))
        return _result(content, page_number)
    monkeypatch.setattr(reading, "_run_ocr_page", execute)
    result = reading.read_pdf_pages(source, page_numbers=[2, 1], tessdata_dir=language_data)
    assert called == [(1, language_data, 20.0)]
    assert result["pageCount"] == 3
    assert result["ocrRequiredPages"] == [1, 3]
    assert result["completedOcrPages"] == [1]
    assert result["pendingOcrPages"] == [3]
    assert result["pages"][0]["confidence"] is None
    assert result["pages"][0]["confidenceAvailable"] is False
    assert result["pages"][1]["sourceKind"] == "native"


@pytest.mark.parametrize("pages", [[], [1, 2, 3, 4], [1, 1], [0], [-1], [True], ["1"]])
def test_invalid_page_selection_is_rejected_before_ocr(pages) -> None:
    with pytest.raises(reading.LocalPdfReadingError):
        reading.read_pdf_pages(_document(_pdf("native")), page_numbers=pages)


@pytest.mark.parametrize("change", [{"tenant_id": None}, {"object_version": None}, {"object_key": None}, {"archive_id": "0" * 64}])
def test_unbound_or_changed_original_is_rejected(change) -> None:
    with pytest.raises(reading.LocalPdfReadingError):
        reading.read_pdf_pages(replace(_document(_pdf("native")), **change), page_numbers=[1])


def test_missing_language_data_is_explicit_and_native_page_remains_usable(tmp_path: Path, language_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reading, "_run_ocr_page", lambda *_: pytest.fail("unavailable model invoked OCR"))
    result = reading.read_pdf_pages(_document(_pdf("scan", "native")), page_numbers=[1, 2], tessdata_dir=tmp_path / "missing")
    assert result["pages"][0]["status"] == "unavailable"
    assert result["pages"][0]["text"] == ""
    assert result["pages"][0]["blocks"] == []
    assert result["failedPages"] == result["pendingOcrPages"] == [1]
    assert result["pages"][1]["status"] == "completed"


def test_model_integrity_is_checked_again_before_execution(language_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = reading.get_local_pdf_reading_fingerprint(tessdata_dir=language_data)
    (language_data / "kor.traineddata").write_bytes(b"changed")
    after = reading.get_local_pdf_reading_fingerprint(tessdata_dir=language_data)
    assert before == after  # Lookup identity remains stable; corrupt data cannot execute.
    monkeypatch.setattr(reading, "_run_ocr_page", lambda *_: pytest.fail("changed model invoked OCR"))
    result = reading.read_pdf_pages(_document(_pdf("scan")), page_numbers=[1], tessdata_dir=language_data)
    assert result["pages"][0]["errorCode"] == "OCR_LANGUAGE_DATA_INTEGRITY_FAILED"


@pytest.mark.parametrize("failure", ["binding", "hash", "bbox", "empty"])
def test_ocr_output_requires_original_binding_text_hash_and_page_geometry(language_data: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    source = _document(_pdf("scan"))
    result = _result(source.content, 1)
    if failure == "binding": result["fileSha256"] = "0" * 64
    if failure == "hash": result["textSha256"] = "0" * 64
    if failure == "bbox": result["blocks"][0]["bbox"] = [0, 0, 999, 999]
    if failure == "empty": result = {**result, "text": "", "textSha256": hashlib.sha256(b"").hexdigest(), "blocks": []}
    monkeypatch.setattr(reading, "_run_ocr_page", lambda *_: result)
    output = reading.read_pdf_pages(source, page_numbers=[1], tessdata_dir=language_data)
    assert output["failedPages"] == output["pendingOcrPages"] == [1]
    assert output["completedOcrPages"] == []
    assert output["pages"][0]["status"] == "failed"
    assert output["pages"][0]["text"] == ""


def test_native_rotation_bboxes_match_rendered_page_geometry() -> None:
    output = reading.read_pdf_pages(_document(_pdf("native", rotation=90)), page_numbers=[1])
    page = output["pages"][0]
    assert page["width"] == 220 and page["height"] == 500 and page["rotation"] == 90
    for block in page["blocks"]:
        x0, y0, x1, y1 = block["bbox"]
        assert 0 <= x0 < x1 <= page["width"] and 0 <= y0 < y1 <= page["height"]


def test_oversized_scan_is_rejected_before_render(language_data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _document(_pdf("scan"))
    monkeypatch.setattr(reading, "MAX_OCR_PIXELS", 100)
    monkeypatch.setattr(reading, "_run_ocr_page", lambda *_: pytest.fail("oversized page invoked OCR"))
    output = reading.read_pdf_pages(source, page_numbers=[1], tessdata_dir=language_data)
    assert output["pages"][0]["errorCode"] == "OCR_PAGE_PIXEL_LIMIT_EXCEEDED"


def test_timeout_kills_and_reaps_the_actual_isolated_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_popen = subprocess.Popen
    children = []
    def sleeping_process(command, **kwargs):
        process = real_popen([sys.executable, "-B", "-c", "import time; time.sleep(10)"], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(reading.subprocess, "Popen", sleeping_process)
    with pytest.raises(reading._OcrFailure, match="OCR_TIMEOUT"):
        reading._run_ocr_page(b"", 1, tmp_path, 0.2)
    assert children[0].poll() is not None


def test_real_local_korean_english_ocr_with_pinned_language_data() -> None:
    configured = os.environ.get("OCR_TEST_TESSDATA_DIR")
    if not configured:
        pytest.skip("explicit pinned OCR test-data directory is not configured")
    _, runtime = reading._runtime(configured)
    assert runtime["available"], runtime
    source = _document(_pdf("scan"))
    output = reading.read_pdf_pages(source, page_numbers=[1], tessdata_dir=configured)
    assert output["completedOcrPages"] == [1], output
    assert output["pendingOcrPages"] == []
    text = output["pages"][0]["text"]
    assert "123,456" in text
    assert "PROJECT" in text
    assert "사업비" in text
    assert source.sha256 == output["fileSha256"]


def test_busy_ocr_slot_does_not_block_native_page_reading(tmp_path: Path) -> None:
    assert reading._OCR_SLOTS.acquire(blocking=False)
    try:
        with pytest.raises(reading._OcrFailure, match="OCR_BUSY"):
            reading._run_ocr_page(b"", 1, tmp_path, 1)
        result = reading.read_pdf_pages(_document(_pdf("native")), page_numbers=[1])
        assert result["pages"][0]["status"] == "completed"
    finally:
        reading._OCR_SLOTS.release()


def test_ocr_subprocess_uses_only_static_arguments_and_frames_data_on_stdin(monkeypatch):
    requests = []

    class Process:
        returncode = 0

        def communicate(self, request, timeout):
            header, content = request.split(b"\n", 1)
            requests.append((json.loads(header), content, timeout))
            return b'{"pageNumber":2}', None

    def launch(command, **kwargs):
        assert command == [reading.sys.executable, "-B", "-m", "parsing_platform.local_pdf_reading", "--ocr-worker"]
        assert kwargs["shell"] is False
        return Process()

    monkeypatch.setattr(reading.subprocess, "Popen", launch)
    directory = Path("models with spaces; --untrusted-option")
    content = b"%PDF-1.7\nunchanged source"
    assert reading._run_isolated_ocr(content, 2, directory, 1.0) == {"pageNumber": 2}
    assert requests == [({"pageNumber": 2, "tessdataDir": str(directory)}, content, 1.0)]


def test_ocr_worker_rejects_unbounded_or_malformed_stdin_before_loading_models(monkeypatch):
    import io
    from types import SimpleNamespace

    def reject_models(*_args):
        pytest.fail("invalid worker request reached language data")

    monkeypatch.setattr(reading, "verified_tessdata_dir", reject_models)
    for request in (b"x" * (reading.MAX_WORKER_REQUEST_HEADER_BYTES + 1),
                    b'{"pageNumber":"--eval","tessdataDir":"models"}\n%PDF'):
        output = io.BytesIO()
        monkeypatch.setattr(reading, "sys", SimpleNamespace(
            platform="worker-contract-test",
            stdin=SimpleNamespace(buffer=io.BytesIO(request)),
            stdout=SimpleNamespace(buffer=output),
        ))
        assert reading._worker_main() == 0
        assert json.loads(output.getvalue())["errorCode"] == "OCR_REQUEST_INVALID"
