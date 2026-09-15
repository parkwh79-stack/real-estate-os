from __future__ import annotations

import tempfile
import time
from pathlib import Path
from agents.visual_qa_agent import VisualQAAgent
from agents.report_export_agent import ReportExportAgent


def test_visual_qa_agent_pdf_structure_validation():
    # 1. Generate a mock report record
    report_record = {
        "report_id": "test_report_123",
        "title": "Hana PF Review Sample Report",
        "version": 1,
        "report_markdown": "# 1. Cover\nThis is a sample cover page text.",
        "report": {
            "title": "Hana PF Review Sample Report",
            "sectionOrder": [
                {"key": "cover", "index": "1", "title": "Cover Page"},
                {"key": "terms", "index": "2", "title": "Financial Terms"}
            ],
            "sections": {
                "cover": {"Summary": "This is a basic developer PF feasibility summary. 결재 항목 포함."},
                "terms": {"LTV": 0.58, "LTC": 0.72, "DSCR": 1.25, "Note": "Strict compliance required."}
            }
        }
    }

    # 2. Render report to PDF bytes using ReportExportAgent
    exporter = ReportExportAgent()
    export_result = exporter.export(report_record, "pdf")
    assert export_result["format"] == "pdf"
    
    # The content is base64 encoded in binary export, we need the raw bytes
    import base64
    pdf_bytes = base64.b64decode(export_result["content"])

    # 3. Instantiate VisualQAAgent in a temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        qa_agent = VisualQAAgent(baseline_dir=Path(temp_dir))

        # Check structural layout details (A4 width, overflow, blank pages)
        structure_report = qa_agent.analyze_pdf_structure(pdf_bytes)
        
        assert "status" in structure_report
        assert structure_report["total_pages"] > 0
        assert "page_details" in structure_report
        
        # Verify page Details conform to A4 standard
        for page in structure_report["page_details"]:
            assert page["width"] > 0
            assert page["height"] > 0
            assert page["is_a4"] is True  # Should match our A4 width and height tolerance
            assert isinstance(page["overflow_warnings"], list)
            assert isinstance(page["overlap_warnings"], list)


def test_visual_regression_baseline_generation():
    report_record = {
        "report_id": "regression_test_id",
        "title": "Hana PF Review Sample Report",
        "version": 1,
        "report_markdown": "# Cover\n결재 내용 확인 완료.",
        "report": {
            "title": "Hana PF Review Sample Report",
            "sectionOrder": [
                {"key": "cover", "index": "1", "title": "Cover"}
            ],
            "sections": {
                "cover": "결재용 보고서 바디."
            }
        }
    }

    exporter = ReportExportAgent()
    export_result = exporter.export(report_record, "pdf")
    import base64
    pdf_bytes = base64.b64decode(export_result["content"])

    with tempfile.TemporaryDirectory() as temp_dir:
        qa_agent = VisualQAAgent(baseline_dir=Path(temp_dir))

        # First run creates baseline image
        reg_report_1 = qa_agent.run_visual_regression(pdf_bytes, report_id="regression_test_id")
        assert reg_report_1["status"] == "baseline_created"
        assert reg_report_1["pages_compared"] == 0
        assert reg_report_1["matched_pages"] == 0
        assert reg_report_1["results"][0]["status"] == "baseline_created"
        
        # Baseline file should be saved in the directory
        baseline_file = Path(temp_dir) / "baseline_regression_test_id_page_1.png"
        assert baseline_file.exists()

        # Second run with same PDF should match the baseline perfectly.
        # This run exercises the per-pixel comparison loop (a baseline now exists).
        # Performance guard: `Pixmap.samples` re-copies the whole pixel buffer on
        # every access, so indexing it inside the loop made a single A4 page take
        # tens of minutes (O(pixels^2)). Reading samples once keeps it O(pixels)
        # (<1s). 30s is a generous, non-flaky ceiling that still fails loudly if
        # the quadratic pattern is ever reintroduced.
        start = time.perf_counter()
        reg_report_2 = qa_agent.run_visual_regression(pdf_bytes, report_id="regression_test_id")
        elapsed = time.perf_counter() - start
        assert elapsed < 30, (
            f"Visual regression comparison took {elapsed:.1f}s — pix.samples is likely "
            "being re-materialized inside the pixel loop (O(pixels^2) regression)."
        )
        assert reg_report_2["status"] == "passed"
        assert reg_report_2["matched_pages"] == 1
        assert reg_report_2["regression_pages"] == 0
        assert reg_report_2["results"][0]["status"] == "matched"
        assert reg_report_2["results"][0]["diff_percentage"] == 0.0
