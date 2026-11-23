"""A valid large original must not produce an oversized BFF preview."""
import hashlib
import random
import struct
import zlib

import fitz

from use_cases.source_document_viewer import MAX_PDF_PREVIEW_BYTES, render_pdf_page_preview


def test_high_entropy_page_preview_stays_below_function_response_limit():
    width = height = 1200
    rgb = random.Random(17).randbytes(width * height * 3)
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xffffffff)
    scanlines = b"".join(b"\x00" + rgb[row * width * 3:(row + 1) * width * 3] for row in range(height))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=600)
        page.insert_image(page.rect, stream=png)
        original = pdf.tobytes()
    digest = hashlib.sha256(original).hexdigest()
    result = render_pdf_page_preview(original, page_number=1, scale=2)
    assert 0 < len(result["content"]) <= MAX_PDF_PREVIEW_BYTES
    assert result["content"].startswith(b"\x89PNG")
    assert result["previewAdjusted"] is True
    assert result["pixelWidth"] < 1200 and result["pixelHeight"] < 1200
    assert result["pageWidth"] == 600 and result["pageHeight"] == 600
    assert result["coordinateSpace"] == "pdf_points_top_left"
    assert hashlib.sha256(original).hexdigest() == digest


def test_small_text_preview_keeps_requested_resolution():
    with fitz.open() as pdf:
        page = pdf.new_page(width=400, height=200)
        page.insert_text((20, 50), "Native source remains unchanged")
        original = pdf.tobytes()
    result = render_pdf_page_preview(original, page_number=1, scale=1.5)
    assert result["pixelWidth"] == 600 and result["pixelHeight"] == 300
    assert result["pixelScale"] == 1.5 and result["previewAdjusted"] is False
