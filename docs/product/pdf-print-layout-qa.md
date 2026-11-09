# PDF Print Layout QA

Updated: 2026-10-14

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
The harness compares PDF pages with a reference independent of each execution's
report ID. PF orchestration derives `baseline_id` from tenant, project and template;
the current report ID identifies only the current result/difference directory.

1. **Candidate capture:** The first run creates immutable images and a manifest
   containing source report/PDF hash, image hashes, DPI and capture time. It returns
   `baseline_created`, zero compared pages, and `baseline_reviewed=false`.
2. **Explicit layout review:** A trusted local maintainer inspects every image,
   then calls `VisualQAAgent.review_baseline(baseline_id, expected_manifest_hash=...,
   reviewer=..., reason=...)`. This is an administrative Python operation, not an
   API payload or automatic PF-run action. It records a separate immutable review
   receipt bound to the manifest. An existing receipt is not overwritten; use a new
   template revision for a replacement. This is layout review, not credit approval.
3. **Comparison:** Later report IDs use the same reference. Matching an unreviewed
   reference returns `baseline_unreviewed`. Only matching a reviewed, hash-valid
   reference returns `passed`; orchestration also checks `baseline_reviewed=true`.
   Added/removed pages and differences above 0.05% produce `regression_detected`.
   Changed images, missing manifests or different DPI produce `baseline_invalid`.
   Old images without a manifest cannot be silently promoted. Competing captures
   cannot overwrite reference images; an incomplete capture requires inspection.
4. **Difference artifacts:** `diff_page_*.png` contains the current changed page,
   not a red-highlight overlay. Numeric/content changes and visible report metadata
   can legitimately differ; this comparison does not mask them or auto-approve them.

No operational reference has been approved by this development change. Tests review
only synthetic fixtures. Missing layout approval affects final report quality, not
independent feasibility calculations or analysis-only comparison drafts.

The PDF writer now uses the correctly named `HYGothic-Medium` CID font and a
FontDescriptor. A raw UTF-16 text-operator check had not caught the previous Korean
glyph fallback; regression coverage now also extracts and renders text with PyMuPDF.
The actual-source six-page comparison draft was visually checked after the fix.

### 3. Execution in Test Suite
Automated regression tests are fully integrated into our testing suite under `tests/test_visual_regression.py` and are checked alongside compiler and structural assertions.

---

## Residual Risk

- Browser print engines differ slightly. Final institution templates should still be sampled in Chrome and Edge before external delivery.
- Real report data can contain longer legal text than the anonymized fixture; long covenant clauses should be reviewed with representative PDFs before production export.
- Structural warnings (such as margin overflows or missing approval blocks) should be actively monitored on the Orchestration Agent's dashboard.
