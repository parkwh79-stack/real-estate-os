from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF


class VisualQAAgent:
    """
    VisualQAAgent performs structural and visual checks on generated PDF report
    artifacts for A4 layout, margins, text collisions, and regression drift.
    """

    A4_WIDTH = 595.27
    A4_HEIGHT = 841.89
    A4_TOLERANCE = 2.0

    def __init__(self, baseline_dir: Optional[Path] = None):
        self.baseline_dir = Path(baseline_dir) if baseline_dir else Path(__file__).resolve().parent.parent / "data" / "runtime" / "baselines"
        self.baseline_dir.mkdir(parents=True, exist_ok=True)

    def analyze_pdf_structure(self, pdf_bytes: bytes) -> Dict[str, Any]:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_checks: List[Dict[str, Any]] = []
        global_issues: List[str] = []
        total_overflow_count = 0
        total_overlap_count = 0
        total_empty_pages = 0

        left_margin_limit = 34.0
        right_margin_limit = self.A4_WIDTH - 34.0
        top_margin_limit = 34.0
        bottom_margin_limit = self.A4_HEIGHT - 34.0

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            rect = page.rect
            size_issues = []
            if abs(rect.width - self.A4_WIDTH) > self.A4_TOLERANCE or abs(rect.height - self.A4_HEIGHT) > self.A4_TOLERANCE:
                size_issues.append(
                    f"Page size {rect.width:.2f}x{rect.height:.2f} does not match A4 standard "
                    f"({self.A4_WIDTH:.2f}x{self.A4_HEIGHT:.2f})"
                )

            overflow_issues = []
            overlap_issues = []
            previous_rects: List[Tuple[float, float, float, float]] = []
            text_blocks = page.get_text("blocks")

            for block in text_blocks:
                x0, y0, x1, y1, text, block_no, _block_type = block
                text_preview = text.strip()[:20]
                if x0 < left_margin_limit or x1 > right_margin_limit:
                    overflow_issues.append(
                        f"Horizontal text overflow detected at X ({x0:.1f}, {x1:.1f}). Text: '{text_preview}...'"
                    )
                    total_overflow_count += 1
                if y0 < top_margin_limit or y1 > bottom_margin_limit:
                    overflow_issues.append(
                        f"Vertical text overflow detected outside printable margins at Y ({y0:.1f}, {y1:.1f}). Text: '{text_preview}...'"
                    )
                    total_overflow_count += 1

                for px0, py0, px1, py1 in previous_rects:
                    if x1 <= px0 or x0 >= px1 or y1 <= py0 or y0 >= py1:
                        continue
                    overlap_area = (min(x1, px1) - max(x0, px0)) * (min(y1, py1) - max(y0, py0))
                    if overlap_area > 50.0:
                        overlap_issues.append(
                            f"Possible text collision detected around block {block_no}. Overlap area: {overlap_area:.1f}pt"
                        )
                        total_overlap_count += 1

                previous_rects.append((x0, y0, x1, y1))

            is_empty = len(text_blocks) == 0
            if is_empty:
                total_empty_pages += 1

            page_checks.append(
                {
                    "page_index": page_idx + 1,
                    "width": rect.width,
                    "height": rect.height,
                    "is_a4": len(size_issues) == 0,
                    "size_errors": size_issues,
                    "overflow_warnings": overflow_issues,
                    "overlap_warnings": overlap_issues,
                    "is_empty": is_empty,
                    "block_count": len(text_blocks),
                }
            )

        if len(doc) > 0:
            final_page_text = doc[len(doc) - 1].get_text()
            if "결재" not in final_page_text and "approval" not in final_page_text.lower():
                global_issues.append("Approval block ('결재' or 'approval') not detected on the final page of the report.")

        status = "passed"
        if total_overflow_count or total_overlap_count or total_empty_pages or global_issues:
            status = "warnings"
        if any(not page["is_a4"] for page in page_checks):
            status = "failed"

        return {
            "status": status,
            "total_pages": len(doc),
            "total_overflow_warnings": total_overflow_count,
            "total_overlap_warnings": total_overlap_count,
            "total_empty_pages": total_empty_pages,
            "global_issues": global_issues,
            "page_details": page_checks,
        }

    def run_visual_regression(self, pdf_bytes: bytes, report_id: str, dpi: int = 150) -> Dict[str, Any]:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        run_diff_dir = self.baseline_dir / f"diffs_{report_id}"
        run_diff_dir.mkdir(parents=True, exist_ok=True)

        matches = 0
        differences = 0
        results: List[Dict[str, Any]] = []

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            zoom = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            baseline_file = self.baseline_dir / f"baseline_{report_id}_page_{page_idx + 1}.png"

            if not baseline_file.exists():
                pix.save(str(baseline_file))
                matches += 1
                results.append(
                    {
                        "page_index": page_idx + 1,
                        "status": "baseline_created",
                        "diff_percentage": 0.0,
                        "comment": "No baseline found. Saved current layout as reference.",
                    }
                )
                continue

            baseline_pix = fitz.Pixmap(str(baseline_file))
            if pix.width != baseline_pix.width or pix.height != baseline_pix.height:
                diff_file_path = run_diff_dir / f"diff_page_{page_idx + 1}.png"
                pix.save(str(diff_file_path))
                differences += 1
                results.append(
                    {
                        "page_index": page_idx + 1,
                        "status": "size_mismatch",
                        "diff_percentage": 100.0,
                        "diff_image_path": str(diff_file_path),
                        "comment": f"Dimension mismatch: baseline {baseline_pix.width}x{baseline_pix.height} vs current {pix.width}x{pix.height}",
                    }
                )
                continue

            # `Pixmap.samples` materializes a fresh bytes copy of the whole pixel
            # buffer on every access. Reading it once into locals keeps this loop
            # O(pixels); indexing it inside the loop made it O(pixels^2) and could
            # take tens of minutes per A4 page once a baseline existed.
            current_samples = pix.samples
            baseline_samples = baseline_pix.samples
            sample_limit = min(len(current_samples), len(baseline_samples))
            diff_pixels = 0
            for idx in range(0, sample_limit - 2, 3):
                if (
                    abs(current_samples[idx] - baseline_samples[idx]) > 15
                    or abs(current_samples[idx + 1] - baseline_samples[idx + 1]) > 15
                    or abs(current_samples[idx + 2] - baseline_samples[idx + 2]) > 15
                ):
                    diff_pixels += 1

            diff_percentage = (diff_pixels / max(1, pix.width * pix.height)) * 100.0
            if diff_percentage > 0.05:
                diff_file_path = run_diff_dir / f"diff_page_{page_idx + 1}.png"
                pix.save(str(diff_file_path))
                differences += 1
                results.append(
                    {
                        "page_index": page_idx + 1,
                        "status": "regression_detected",
                        "diff_percentage": round(diff_percentage, 3),
                        "diff_image_path": str(diff_file_path),
                        "comment": f"Visual differences detected on {diff_percentage:.2f}% of page pixels.",
                    }
                )
            else:
                matches += 1
                results.append(
                    {
                        "page_index": page_idx + 1,
                        "status": "matched",
                        "diff_percentage": 0.0,
                        "comment": "Page layouts match baseline within tolerance.",
                    }
                )

        return {
            "status": "regression_detected" if differences else "passed",
            "report_id": report_id,
            "total_pages": len(doc),
            "pages_compared": len(doc),
            "matched_pages": matches,
            "regression_pages": differences,
            "diff_output_directory": str(run_diff_dir),
            "results": results,
        }
