# PDF Print Layout QA

Updated: 2026-09-28

## Scope

This checklist covers report layout. `/report` is the sample entry; actual
project review uses `/project/[id]/report` and its saved report/export context.

## Implemented Controls

- A4 print page rule: `@page { size: A4; margin: 12mm; }`.
- Report body constrained to printable A4 width through `.report-print-page`.
- Table QA controls:
  - `.report-table { table-layout: fixed; }`
  - repeated table headers via `thead { display: table-header-group; }`
  - row splitting prevention via `tr { break-inside: avoid; }`
  - long-cell wrapping via `overflow-wrap: anywhere`.
- Section QA controls:
  - `.report-section` uses `break-inside: avoid`.
  - approval block uses `.approval-block` and is protected from page splitting.
- The table of contents is forced to a separate printed page through `print:break-after-page`.

## Manual QA Steps

1. Open `/report` in the web app.
2. Click `PDF 저장 / 인쇄`.
3. Set paper to A4.
4. Confirm the cover and table of contents are readable.
5. Confirm wide financial terms and cash flow tables do not clip at the right edge.
6. Confirm table headers repeat if a table moves across pages.
7. Confirm rows are not split mid-line.
8. Confirm the approval block is visible and not split across pages.

## Automated Visual QA & Regression Testing

To verify layout integrity programmatically, the platform includes a **`VisualQAAgent`** (`services/api-server/agents/visual_qa_agent.py`) backed by the high-performance **PyMuPDF (fitz)** rendering engine.

### 1. Structural Verification Rules
The visual agent automatically scans every generated PDF document for layout anomalies:
*   **Page Dimension Validation:** Ensures every page conforms exactly to the **A4 standard** (595.27 x 841.89 points) within a strict rounding tolerance.
*   **Content Overflow Detection:** Scans horizontal and vertical boundaries to verify no text renders outside printable margins (minimum 12mm / 34pt).
*   **Text Collision / Overlap:** Checks Y-coordinate bounding box overlaps to identify colliding lines or crushed paragraphs.
*   **Approval Block Integrity:** Checks for the presence of the signature block (`결재` / `approval`) on the final page, raising a warning if it's missing or detached.
*   **Empty Page Identification:** Counts block elements to detect stray blank pages or page-break policy failures.

### 2. Pixel-Level Visual Regression
The harness compares PDF pages with stored reference images. Stored images are
not automatically human-approved or certified. Current PF runs use new report
IDs; linking an approved template/report baseline across runs remains work to do.
1.  **Baseline Generation:** The first execution for an identifier creates images (`baseline_*.png`) and returns `baseline_created`. It is not a successful comparison or an approval.
2.  **Visual Comparer:** On subsequent runs (such as after system or style modifications), pages are re-rasterized and matched pixel-by-pixel against the references.
3.  **Visual Layout Diff:** If changes exceed a 0.05% pixel-count threshold (protecting against sub-pixel anti-aliasing), a visual diff layout is generated (`diff_page_*.png`) highlighting modifications in bright red to aid the manual review.

### 3. Execution in Test Suite
Automated regression tests are fully integrated into our testing suite under `tests/test_visual_regression.py` and are checked alongside compiler and structural assertions.

---

## Residual Risk

- Browser print engines differ slightly. Final institution templates should still be sampled in Chrome and Edge before external delivery.
- Real report data can contain longer legal text than the anonymized fixture; long covenant clauses should be reviewed with representative PDFs before production export.
- Structural warnings (such as margin overflows or missing approval blocks) should be actively monitored on the Orchestration Agent's dashboard.
