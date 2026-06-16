# Feasibility Workbook Validation

Updated: 2026-05-13

## Purpose

Feasibility Study Agent handles financial model numbers, so every Excel-driven run must pass validation before the agent treats workbook outputs as authoritative.

This process was designed after reviewing the sample workbook:

`19.12.11 본리동 주상복합 사업수지 (PF검토용).xlsx`

## Sample Workbook Findings

Workbook profile:

- 12 worksheets.
- Core sheets: `입력`, `분양`, `제경비`, `사업수지`, `1.수지총괄(대외용)`, `2.현금흐름`, `검토서`.
- Formula-heavy model with more than 11,000 formulas.
- No REALYTICS `fs_*` named ranges.
- No structured Excel tables.

Key verified values from `사업수지`:

| Metric | Cell | Value |
|---|---:|---:|
| Total revenue | `사업수지!D8` | 165,596,965.012 |
| Total revenue after VAT adjustment | `사업수지!D11` | 165,596,965.012 |
| Construction subtotal | `사업수지!D18` | 72,036,344.512 |
| Land subtotal | `사업수지!D26` | 46,032,000.000 |
| Service subtotal | `사업수지!D33` | 3,561,168.000 |
| Sales expense subtotal | `사업수지!D41` | 6,318,199.466 |
| Levy subtotal | `사업수지!D52` | 2,653,950.462 |
| Finance cost subtotal | `사업수지!D62` | 16,298,073.468 |
| Other cost subtotal | `사업수지!D71` | 4,941,829.406 |
| Total cost | `사업수지!D72` | 151,841,565.314 |
| Development profit | `사업수지!D74` | 13,755,399.698 |

Passed reconciliations:

- `사업수지!D74 = 사업수지!D11 - 사업수지!D72`
- `사업수지!D72 = D18 + D26 + D33 + D41 + D52 + D62 + D71`
- `1.수지총괄(대외용)!E51 = 사업수지!D8`
- `검토서!E35 = 사업수지!D74`

Formula integrity issues found:

- `#REF!`: 25 cached error cells.
- `#DIV/0!`: 12 cached error cells.
- The `검토서` sheet has broken scenario formulas in WORST/BEST columns.
- The NORMAL case links correctly to the core `사업수지` sheet.

Conclusion:

- The sample workbook is conceptually well covered by `FeasibilityStudyV2`.
- It is not directly import-ready under `realytics-fs-xlsx-v1` because it lacks REALYTICS named ranges and tables.
- The correct path is to create a legacy-cell-map adapter first, then export future workbooks using the REALYTICS template.

## Validation Gates

### Gate 1. Workbook Structure

Required for the legacy sample profile:

- `입력`
- `분양`
- `제경비`
- `사업수지`
- `1.수지총괄(대외용)`
- `2.현금흐름`
- `검토서`

Fail if any required sheet is missing.

### Gate 2. Key Cell Extraction

Read formula and cached value for:

- Project name, location, land area.
- Total revenue.
- Cost subtotals.
- Total cost.
- Development profit.
- External summary values.
- Review-sheet profit values.

Each extracted cell must store:

- Sheet name.
- Cell address.
- Cached value.
- Formula text.

### Gate 3. Numeric Reconciliation

Required checks:

- Profit equals revenue minus total cost.
- Total cost equals cost subtotals.
- External summary revenue equals business-plan revenue.
- Review NORMAL profit equals business-plan profit.

Default tolerance:

- `1.0` in workbook unit.

### Gate 4. Formula Integrity

Scan all workbook formulas and cached values.

Blocking or warning indicators:

- Formula text containing `#REF!`.
- Cached value `#REF!`.
- Cached value `#DIV/0!`.
- Missing cached values in key formula cells.
- Formula count unexpectedly low for a known template.

### Gate 5. Schema Readiness

Check whether the workbook can be imported directly into `FeasibilityStudyV2`.

Direct import requires:

- REALYTICS `fs_*` named ranges.
- Structured tables such as `tbl_product_mix`, `tbl_cost_items`, `tbl_financing`, and `tbl_monthly_cf`.

If these are missing, use a legacy adapter.

## Code

Validator module:

`services/api-server/agents/feasibility_workbook_validator.py`

Test module:

`services/api-server/tests/test_feasibility_workbook_validator.py`

Core function:

```python
validate_feasibility_workbook(workbook_path)
```

Result shape:

```json
{
  "status": "passed|warning|failed",
  "profile": "legacy-bonri-mixed-use-pf-feasibility",
  "sheets": [],
  "requiredSheetChecks": [],
  "keyCells": {},
  "reconciliationChecks": [],
  "formulaIntegrity": {},
  "schemaReadiness": {},
  "recommendations": []
}
```

## Agent Rule

Feasibility Study Agent should not use Excel outputs as final numbers unless:

1. Required sheets are present.
2. Core reconciliations pass.
3. Formula integrity has no unresolved `#REF!` in authoritative cells.
4. Schema readiness is either direct v2 import or a known legacy adapter.
5. All imported metrics carry workbook cell provenance.
6. Formula export imports preserve product-level monthly collection and sales-rate scenarios under `raw_import.productMonthlySalesRateScenarios`.
7. A reviewer must approve the returned FeasibilityStudyV2 draft through `/projects/{project_id}/feasibility-workbook/approve` before the workbook becomes canonical project data.
