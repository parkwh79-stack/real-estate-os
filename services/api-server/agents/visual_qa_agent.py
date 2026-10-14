from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from core.runtime_paths import runtime_data_dir


class VisualQAAgent:
    """
    VisualQAAgent performs structural and visual checks on generated PDF report
    artifacts for A4 layout, margins, text collisions, and regression drift.
    """

    A4_WIDTH = 595.27
    A4_HEIGHT = 841.89
    A4_TOLERANCE = 2.0

    def __init__(self, baseline_dir: Optional[Path] = None):
        self.baseline_dir = Path(baseline_dir) if baseline_dir else runtime_data_dir() / "baselines"
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

    @staticmethod
    def baseline_key(tenant_id: str, project_id: str, template: str) -> str:
        """Keep a project/template reference independent of each execution ID."""
        return sha256(json.dumps([tenant_id, project_id, template], ensure_ascii=False).encode()).hexdigest()

    def review_baseline(self, baseline_id: str, *, expected_manifest_hash: str, reviewer: str, reason: str) -> Dict[str, Any]:
        """Trusted local-maintainer action after inspecting the candidate images.

        Not exposed to project payloads or automatic execution. Layout review
        never confers financial/report publication approval.
        """
        self._validate_identifier(baseline_id, "baseline_id")
        path = self.baseline_dir / f"baseline_{baseline_id}.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if sha256(path.read_bytes()).hexdigest() != expected_manifest_hash or not self._valid_baseline(manifest, baseline_id):
            raise ValueError("Baseline changed; inspect the current candidate before review")
        if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("A reviewer and review reason are required")
        review = {"manifestHash": expected_manifest_hash, "reviewedBy": reviewer.strip(), "reason": reason.strip(),
                  "reviewedAt": datetime.now(timezone.utc).isoformat()}
        review_path = self.baseline_dir / f"review_{baseline_id}.json"
        # A reviewed revision is immutable; a replacement uses a new template revision.
        with review_path.open("x", encoding="utf-8") as stream:
            json.dump(review, stream, ensure_ascii=False, sort_keys=True)
        return review

    @staticmethod
    def _validate_identifier(value: str, name: str) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[\w.-]+", value):
            raise ValueError(f"{name} must be a non-empty identifier without path separators")

    def _valid_baseline(self, manifest: dict, baseline_id: str) -> bool:
        pages = manifest.get("pages")
        if manifest.get("baselineId") != baseline_id or not isinstance(pages, list) or not pages:
            return False
        try:
            return all(sha256((self.baseline_dir / f"baseline_{baseline_id}_page_{index}.png").read_bytes()).hexdigest() == digest
                       for index, digest in enumerate(pages, 1))
        except OSError:
            return False

    def run_visual_regression(self, pdf_bytes: bytes, report_id: str, dpi: int = 150, *, baseline_id: str | None = None) -> Dict[str, Any]:
        if not isinstance(report_id, str) or not re.fullmatch(r"[\w.-]+", report_id):
            raise ValueError("report_id must be a non-empty identifier without path separators")
        if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
            raise ValueError("dpi must be a positive integer")
        baseline_id = report_id if baseline_id is None else baseline_id
        self._validate_identifier(baseline_id, "baseline_id")
        pattern = re.compile(rf"baseline_{re.escape(baseline_id)}_page_([1-9][0-9]*)\.png")
        baseline_pages = {
            int(match.group(1)): path
            for path in self.baseline_dir.iterdir()
            if path.is_file() and (match := pattern.fullmatch(path.name))
        }
        creating_baseline = not baseline_pages
        manifest_path = self.baseline_dir / f"baseline_{baseline_id}.json"
        review_path = self.baseline_dir / f"review_{baseline_id}.json"
        manifest_hash = None
        reviewed = False
        if not creating_baseline or manifest_path.exists() or review_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_hash = sha256(manifest_path.read_bytes()).hexdigest()
                valid = self._valid_baseline(manifest, baseline_id) and manifest.get("dpi") == dpi and len(manifest.get("pages", [])) == len(baseline_pages)
                review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
                reviewed = valid and review.get("manifestHash") == manifest_hash and bool(review.get("reviewedBy") and review.get("reason"))
            except (OSError, ValueError, TypeError, AttributeError):
                valid = False
            if not valid:
                return {"status": "baseline_invalid", "report_id": report_id, "baseline_id": baseline_id,
                        "baseline_reviewed": False, "pages_compared": 0, "matched_pages": 0,
                        "reason": "Reference files, render settings or manifest do not match; inspect a new baseline revision."}
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        run_diff_dir = self.baseline_dir / f"diffs_{report_id}"
        run_diff_dir.mkdir(parents=True, exist_ok=True)

        matches = 0
        differences = 0
        compared = 0
        results: List[Dict[str, Any]] = []

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            zoom = dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
            baseline_file = self.baseline_dir / f"baseline_{baseline_id}_page_{page_idx + 1}.png"

            if creating_baseline:
                # Competing runs must never replace a reference another run
                # has just captured or a maintainer has reviewed.
                with baseline_file.open("xb") as stream:
                    stream.write(pix.tobytes("png"))
                results.append(
                    {
                        "page_index": page_idx + 1,
                        "status": "baseline_created",
                        "diff_percentage": 0.0,
                        "comment": "No baseline found. Saved current layout as reference.",
                    }
                )
                continue

            if page_idx + 1 not in baseline_pages:
                differences += 1
                results.append({"page_index": page_idx + 1, "status": "added_page", "diff_percentage": 100.0, "comment": "Current page is absent from the established baseline."})
                continue

            baseline_pix = fitz.Pixmap(str(baseline_file))
            if baseline_pix.colorspace != fitz.csRGB:
                baseline_pix = fitz.Pixmap(fitz.csRGB, baseline_pix)
            if baseline_pix.alpha:
                baseline_pix = fitz.Pixmap(baseline_pix, 0)
            compared += 1
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

        total_pages = len(doc)
        doc.close()
        if creating_baseline:
            manifest = {"baselineId": baseline_id, "sourceReportId": report_id, "pdfHash": sha256(pdf_bytes).hexdigest(),
                        "dpi": dpi, "createdAt": datetime.now(timezone.utc).isoformat(),
                        "pages": [sha256((self.baseline_dir / f"baseline_{baseline_id}_page_{index}.png").read_bytes()).hexdigest()
                                  for index in range(1, total_pages + 1)]}
            with manifest_path.open("x", encoding="utf-8") as stream:
                json.dump(manifest, stream, ensure_ascii=False, sort_keys=True)
            manifest_hash = sha256(manifest_path.read_bytes()).hexdigest()
        for page_number in sorted(set(baseline_pages) - set(range(1, total_pages + 1))):
            differences += 1
            results.append({"page_index": page_number, "status": "removed_page", "diff_percentage": 100.0, "comment": "Baseline page is absent from the current report."})
        return {
            "status": "baseline_created" if creating_baseline else "regression_detected" if differences else "passed" if reviewed else "baseline_unreviewed",
            "report_id": report_id,
            "baseline_id": baseline_id,
            "baseline_manifest_hash": manifest_hash,
            "baseline_reviewed": reviewed,
            "total_pages": total_pages,
            "baseline_pages": total_pages if creating_baseline else len(baseline_pages),
            "pages_compared": compared,
            "matched_pages": matches,
            "regression_pages": differences,
            "diff_output_directory": str(run_diff_dir),
            "results": results,
        }
