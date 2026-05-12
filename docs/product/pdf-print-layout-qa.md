# PDF Print Layout QA

Updated: 2026-05-12

## Scope

This checklist covers the `/report` Phase 11 PF review report page.

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

## Residual Risk

- Browser print engines differ slightly. Final institution templates should still be sampled in Chrome and Edge before external delivery.
- Real report data can contain longer legal text than the anonymized fixture; long covenant clauses should be reviewed with representative PDFs before production export.
