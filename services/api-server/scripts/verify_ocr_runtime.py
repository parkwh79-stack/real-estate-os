"""Exercise the real offline OCR engine on a synthetic Korean/English scan."""
from __future__ import annotations

import hashlib
import json
import os

import fitz

from agents.parser_agent import ParserAgent
from parsing_platform.adapters import ArchivedDocument


def verify() -> dict:
    with fitz.open() as original:
        page = original.new_page(width=500, height=220)
        page.insert_text((30, 65), "PROJECT COST 123,456", fontsize=25)
        page.insert_text((30, 125), "사업비 123,456", fontname="korea", fontsize=25)
        raster = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png")
    with fitz.open() as scan:
        page = scan.new_page(width=500, height=220)
        page.insert_image(page.rect, stream=raster)
        content = scan.tobytes()
    source = ArchivedDocument(
        project_id="synthetic-ocr-build-check", tenant_id="synthetic-ocr-build-check",
        archive_id=hashlib.sha256(content).hexdigest(), filename="synthetic-ocr-build-check.pdf",
        content_type="application/pdf", content=content,
        object_key="synthetic/ocr-build-check.pdf", object_version="synthetic-build-v1",
    )
    result = ParserAgent.read_evidence_pages(source, page_numbers=[1],
        tessdata_dir=os.environ.get("REALYTICS_OCR_TESSDATA_DIR"))
    if result.get("completedOcrPages") != [1]:
        raise RuntimeError(f"Real OCR runtime check failed: {result.get('pages')}")
    text = result["pages"][0]["text"]
    if not all(value in text for value in ("PROJECT", "사업비", "123,456")):
        raise RuntimeError("Real OCR runtime did not recognize the synthetic check words")
    if result.get("fileSha256") != source.sha256:
        raise RuntimeError("OCR runtime changed the source binding")
    return {"verified": True, "pymupdfVersion": fitz.VersionBind, "languages": ["kor", "eng"],
            "originalUnchanged": True, "paidCalls": 0}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=True))
