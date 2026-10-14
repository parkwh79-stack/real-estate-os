from base64 import b64decode
import json
from pathlib import Path
import re

import pytest

from agents.report_agent import ReportAgent
from agents.report_export_agent import ReportExportAgent


def _pdf_text_lines(pdf: bytes) -> list[str]:
    """Read the emitted text operators and check their declared PDF geometry."""
    page_width = float(re.search(rb"/MediaBox \[0 0 ([\d.]+) [\d.]+\]", pdf)[1])
    font_size = float(re.search(rb"/F1 ([\d.]+) Tf", pdf)[1])
    cid_width = float(re.search(rb"/DW ([\d.]+)", pdf)[1])
    # This exporter declares a uniform advance for every UCS-2 CID (/DW only).
    assert b"/W [" not in pdf
    lines = []
    for x, y, encoded in re.findall(rb"1 0 0 1 ([\d.]+) ([\d.]+) Tm <([0-9A-F]*)> Tj", pdf):
        raw = bytes.fromhex(encoded.decode("ascii"))
        advance = len(raw) / 2 * cid_width / 1000 * font_size
        assert float(x) >= 42
        assert float(x) + advance <= page_width - 42
        assert 42 <= float(y) <= 802
        lines.append(raw.decode("utf-16-be"))
    assert lines
    return lines


@pytest.mark.parametrize("text", [
    "월별 검토 계산은 비회전 대출과 만기 일시상환만 지원합니다. "
    "자기자본 투입과 이자 자본화 및 연체가산은 검토 대상에 포함하지 않습니다.",
    "원본 검증값: sha256:" + "0123456789abcdef" * 12,
    "검토 이유: " + "𠮷지명🏢" * 25,
    "검토 이유:\t금융조건 미확인\n두 번째 줄: " + "원본 확인\t" * 20,
])
def test_pdf_wraps_long_review_text_and_source_hash_inside_page_without_loss(text):
    record = {"title": "검토 초안", "report_id": "synthetic", "report_markdown": text}
    exported = ReportExportAgent().export(record, "pdf")
    lines = _pdf_text_lines(b64decode(exported["content"]))

    # Ignore only line-wrap whitespace; every substantive character must survive.
    assert re.sub(r"\s+", "", text) in re.sub(r"\s+", "", "".join(lines))


def test_pdf_preserves_all_long_review_lines_across_page_breaks():
    source_lines = [f"검토 항목 {i:03d}: " + "미확인 근거는 원본 자료 확인이 필요합니다. " * 5 for i in range(60)]
    record = {"title": "합성 다중 페이지 검토", "report_markdown": "\n".join(source_lines)}
    pdf = b64decode(ReportExportAgent().export(record, "pdf")["content"])
    lines = _pdf_text_lines(pdf)

    assert len(re.findall(rb"/Type /Page\b", pdf)) > 1
    assert re.sub(r"\s+", "", "".join(source_lines)) in re.sub(r"\s+", "", "".join(lines))


def test_standard_pf_report_pdf_keeps_all_sections_inside_page():
    fixture = Path(__file__).parents[1] / "data" / "fixtures" / "hana_pf_review_anonymized.json"
    report = ReportAgent().generate_structured_report(json.loads(fixture.read_text(encoding="utf-8")))
    pdf = b64decode(ReportExportAgent().export({"report": report}, "pdf")["content"])
    rendered = re.sub(r"\s+", "", "".join(_pdf_text_lines(pdf)))

    assert len(report["sectionOrder"]) == 20
    for section in report["sectionOrder"]:
        assert re.sub(r"\s+", "", section["title"]) in rendered
