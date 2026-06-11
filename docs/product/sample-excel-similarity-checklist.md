# Sample Excel Similarity Checklist

Updated: 2026-06-05

Compared files:

- Source sample: Bonri-dong mixed-use PF feasibility workbook in the local `sample/` folder.
- Current export: `GET /api/export-cf`.
- Latest QA artifact: `.runtime/formula-cashflow-export-template-repository.xlsx`.
- Latest parity artifact: `.runtime/template-repository-parity-check.json`.

## 1. Executive Assessment

The current export is now **sample-package equivalent** when the local sample workbook is available. The export first copies the full source sample workbook sheet package, then appends the REALYTICS canonical calculation, validation, formula-map, sample trace, import-contract, and template-contract sheets.

This is a material upgrade from the previous cash-flow-only parity mode.

Current state:

- All worksheets from the local sample workbook are copied when available.
- The copied package preserves the original input, sales, expense, business-plan, summary, overview, cash-flow, sale-price-cap, progress, infrastructure-charge, and review sheets.
- If the local sample workbook is not available, the export falls back to simplified REALYTICS mirror sheets.
- The copied cash-flow sheet matches the sample dimension, row/column count, non-empty cell count, formula count, merged ranges, freeze pane, and key cash-flow cell values.
- `services/api-server/scripts/verify_cashflow_parity.py` provides repeatable regression QA for the cash-flow package and can compare all sample sheets with `--all-sample-sheets`.
- Latest local QA generated `.runtime/formula-cashflow-export-template-repository.xlsx` and `.runtime/template-repository-parity-check.json`; full sample-sheet parity returned `status=passed`.
- Workbook validator evaluates formula integrity on the REALYTICS canonical sheet scope for formula-export workbooks; the generated workbook returned `status=passed`, `directV2ImportReady=true`, and no canonical formula errors.
- Generated workbooks include `08_Template계약`, with template mode, template ID, version, source type, source file, source path, SHA-256, copied sheet count, copied sheet names, and resolved timestamp.
- Template named ranges are available for import and audit: `realytics_template_mode`, `realytics_template_id`, `realytics_template_version`, and `realytics_template_sha256`.
- Template resolution is now behind `apps/web/lib/excel-template-repository.ts`, so the export route no longer owns source path discovery or SHA-256 calculation directly.

Estimated similarity by layer:

| Layer | Similarity | Assessment |
|---|---:|---|
| Core project values | 95%+ when sample workbook is available | Original sample values are preserved in copied sheets |
| Sheet flow | 95%+ when sample workbook is available | Original sample sheets are copied first, then REALYTICS sheets are appended |
| Business-plan sheet | 95%+ when sample workbook is available | Original business-plan sheet is copied; canonical REALYTICS model remains separate |
| Sales sheet | 95%+ when sample workbook is available | Original sales sheet structure is copied; canonical product-mix model remains separate |
| Monthly cash flow | 100% when sample workbook is available | Original summary and cash-flow sheets are copied from the sample workbook |
| Construction progress | 95%+ when sample workbook is available | Original progress sheet is copied; canonical progress model remains separate |
| Formatting / print layout | 90%+ when sample workbook is available | Copied sheets preserve dimensions, row heights, column widths, merged cells, fills, freeze panes, and page setup where ExcelJS supports them |
| Audit / import readiness | 80%+ | REALYTICS named ranges, formula map, validation, import contract, and template contract are stronger than the sample workbook |

Overall current level:

- **90%+ for local sample-package similarity**
- **100% for copied cash-flow package parity**
- **100% full sample-sheet structural parity in the latest local QA run**
- **Still not production-template complete**, because the copied sample sheets are static template replicas rather than fully parameterized REALYTICS-driven sheets.

## 2. Structural Comparison

| Item | Sample | Current Export | Status |
|---|---:|---:|---|
| Sample sheet package | 12 sheets | Copied when sample workbook is available | Done |
| REALYTICS canonical sheets | N/A | Appended after sample sheets | Done |
| Template metadata sheet | N/A | `08_Template계약` | Done |
| Fallback mode | N/A | Simplified mirror sheets when sample workbook is unavailable | Done |

Sample-package mode now preserves the original workbook front/support package instead of trying to manually rebuild each support tab.

## 3. Completed Items

- [x] Copy all sample workbook sheets when the local sample workbook is available.
- [x] Preserve original cash-flow package.
- [x] Preserve original business-plan, sales, input, progress, and review support sheets in sample mode.
- [x] Keep REALYTICS canonical calculation sheets after the copied sample package.
- [x] Keep named ranges and import contract for REALYTICS canonical import/export.
- [x] Add template contract metadata sheet.
- [x] Add template source path, source file, version, and SHA-256.
- [x] Move template discovery, source-type resolution, and SHA-256 calculation behind a repository wrapper.
- [x] Keep fallback simplified mirror mode when no sample workbook is configured.
- [x] Add parity regression script for cash-flow package.
- [x] Extend parity script with `--all-sample-sheets`.
- [x] Full sample-sheet parity regression passed locally for all 12 source sample sheets.
- [x] Validator passed on REALYTICS canonical formula scope after full sample package copy.

## 4. Remaining Gaps

### P0 - Required For Production Template Packaging

- [x] Move local sample discovery behind a controlled repository wrapper.
- [ ] Move the actual template binary from local sample discovery to a secured object-storage or server-managed template location.
- [ ] Keep `REALYTICS_SAMPLE_WORKBOOK_PATH` as a local pilot override only.
- [x] Add template version metadata to the generated workbook.
- [x] Add checksum/hash for the copied sample template package.
- [x] Support `REALYTICS_EXCEL_TEMPLATE_PATH`, `REALYTICS_EXCEL_TEMPLATE_DIR`, `REALYTICS_EXCEL_TEMPLATE_ID`, and `REALYTICS_EXCEL_TEMPLATE_VERSION` for controlled template deployment.
- [x] Run full sample-sheet parity with `--all-sample-sheets` after export generation.

### P1 - Required For Parameterized Template Use

- [ ] Convert copied input sheet cells into links from REALYTICS canonical assumptions where safe.
- [ ] Convert copied sales sheet core product rows into parameterized formulas from product mix.
- [ ] Convert copied business-plan subtotal rows into parameterized links from REALYTICS cost/revenue assumptions.
- [ ] Convert copied progress assumptions into a reusable schedule template.
- [ ] Add visible reconciliation rows explaining which copied sample cells are static and which are REALYTICS-driven.

### P2 - Required For Full Production-Grade Excel QA

- [ ] Add visual QA screenshot comparison for copied sample sheets.
- [ ] Add A4 print area/page break QA for copied sample sheets.
- [ ] Add regression test that checks dimensions, formulas, merged ranges, freeze panes, comments, and key cell values across all sample sheets.
- [ ] Add LLM-first workbook learning feedback so new uploaded Excel patterns can propose improved template mappings.

## 5. Recommended Next Coding Order

1. **Controlled template repository packaging**
   - Package the sample workbook into a controlled server/object-storage template location.
   - Keep template version, hash, and source metadata in the generated workbook.

2. **Parameterized copied sheets**
   - Start with the business-plan sheet, then sales, input, and progress sheets.
   - Link only high-confidence cells to REALYTICS canonical assumptions.
   - Leave ambiguous cells static until LLM judgement and regression pass.

3. **Visual and print QA**
   - Verify copied sample pages render correctly for A4 output.
   - Add row/column/page setup checks.

4. **Comment and shape parity**
   - ExcelJS/openpyxl can warn when comments exist inside merged ranges.
   - Track comment/shape parity separately from value/formula parity.

## 6. Acceptance Criteria

Treat the export as sample-equivalent only when:

- [x] Source sample sheets are copied when the sample workbook is available.
- [x] Cash-flow package matches the sample workbook.
- [x] Full sample-sheet parity regression passes with `--all-sample-sheets`.
- [ ] Template source is packaged outside the developer-local sample folder.
- [x] Template version and hash are visible in the workbook.
- [x] User can distinguish copied sample sheets from REALYTICS canonical calculation sheets through the template contract sheet.
- [ ] Key business values reconcile within agreed tolerance:
  - total revenue
  - total project cost
  - developer profit
  - product subtotal revenue
  - land cost
  - construction cost
  - financial cost
- [x] Exported workbook passes workbook validator on canonical formula scope.
- [ ] Exported workbook passes visual QA.
