# Feasibility Study v2 Schema

Updated: 2026-05-13

## Goal

Feasibility Study v2 is the standard data contract for developer-side real estate 사업수지분석.

The final target is automatic calculation, but the first production workflow must support Excel import/export with formulas preserved. The platform should treat Excel as a controlled workbook view over the same structured schema, not as an unrelated ad hoc file.

Schema constants:

- Data schema: `realytics-feasibility-study-v2`
- Workbook template: `realytics-fs-xlsx-v1`
- Backend schema: `services/api-server/schemas/feasibility_study.py`
- Frontend type mirror: `apps/web/lib/feasibility-study-types.ts`

## Design Principles

1. JSON is the source contract.
2. Excel is the editable analyst workbook.
3. Excel formulas must remain formulas on export.
4. Import should read from stable named ranges and tables, not fragile visual coordinates only.
5. Every imported field should eventually carry provenance: source file, page or sheet, cell or named range, raw text, and confidence.
6. Calculated metrics can be produced by Excel first, then mirrored by API calculation later.

## Top-Level Shape

```json
{
  "version": "realytics-feasibility-study-v2",
  "project_id": "optional",
  "project_name": "Sample Mixed-use Project",
  "asset_type": "mixed_use",
  "currency": "KRW",
  "site": {},
  "program": {},
  "product_mix": [],
  "revenue_items": [],
  "cost_items": [],
  "financing": [],
  "schedule": [],
  "scenarios": [],
  "workbook": {},
  "metrics": {},
  "raw_import": {}
}
```

## Core Sections

### site

Purpose: land and regulation assumptions.

Key fields:

- `address`
- `land_area_sqm`
- `zoning`
- `legal_far`
- `legal_bcr`
- `acquired_land_ratio`

### program

Purpose: architectural and product-area assumptions.

Key fields:

- `gross_floor_area_sqm`
- `saleable_area_sqm`
- `contract_area_sqm`
- `floors_above_ground`
- `floors_below_ground`
- `parking_count`
- `unit_count`

### product_mix

Purpose: apartment, officetel, knowledge industry center, retail, and other saleable products.

Recommended columns:

- `key`
- `asset_type`
- `product_name`
- `unit_count`
- `exclusive_area_sqm`
- `saleable_area_sqm`
- `contract_area_sqm`
- `avg_sale_price_per_sqm`
- `avg_sale_price_per_py`
- `revenue`
- `excel_row_id`

Formula policy:

- `revenue` should usually be an Excel formula first.
- Example formula: `=unit_count * saleable_area_sqm * avg_sale_price_per_sqm`
- API calculation should later reproduce the same logic for automated runs.

### revenue_items

Purpose: structured revenue build-up.

Typical categories:

- sales_revenue
- rental_revenue
- vat_refund
- other_revenue

### cost_items

Purpose: structured cost build-up.

Typical categories:

- land
- acquisition_tax
- construction
- design_supervision
- permit_fee
- trust_fee
- sales_commission
- finance_cost
- contingency
- other

### financing

Purpose: equity, bridge loan, PF tranches, fees, and interest assumptions.

Recommended columns:

- `tranche_id`
- `name`
- `priority`
- `amount`
- `interest_rate`
- `fee_rate`
- `term_months`
- `repayment_source`

### schedule

Purpose: project timeline and monthly cash-flow alignment.

Recommended milestones:

- land_closing
- permit_approval
- construction_start
- sales_start
- pf_drawdown
- completion
- occupancy
- pf_repayment

### scenarios

Purpose: formula-linked sensitivity analysis.

Default scenario drivers:

- `sales_price_factor`
- `construction_cost_factor`
- `interest_rate_addition`
- `sales_rate`
- `delay_months`

## Excel Workbook Template

Recommended sheets:

| Sheet | Role | Purpose |
|---|---|---|
| `00_Control` | cover | Version, project metadata, import status |
| `01_Assumptions` | inputs | Site, program, high-level schedule assumptions |
| `02_ProductMix` | product_mix | Saleable product mix and price assumptions |
| `03_Costs` | costs | Land, construction, indirect costs, finance costs |
| `04_Financing` | financing | Equity, bridge, PF tranches, rates and fees |
| `05_MonthlyCF` | monthly_cf | Monthly cash-in/out and cumulative cash |
| `06_Sensitivity` | sensitivity | Downside/base/upside scenario table |
| `07_Summary` | summary | Developer and PF review KPIs |
| `99_Checks` | checks | Formula checks, balance checks, import validation |
| `_ExportMap` | export_map | Hidden schema-path to named-range mapping |

## Named Range Strategy

The import/export layer should prefer named ranges over raw cell addresses.

Required named ranges:

| Schema Path | Named Range |
|---|---|
| `project_name` | `fs_project_name` |
| `asset_type` | `fs_asset_type` |
| `site.land_area_sqm.value` | `fs_land_area_sqm` |
| `site.legal_far.value` | `fs_legal_far` |
| `site.legal_bcr.value` | `fs_legal_bcr` |
| `program.gross_floor_area_sqm.value` | `fs_gross_floor_area_sqm` |
| `program.saleable_area_sqm.value` | `fs_saleable_area_sqm` |
| `metrics.total_revenue` | `fs_total_revenue` |
| `metrics.total_cost` | `fs_total_cost` |
| `metrics.development_profit` | `fs_development_profit` |
| `metrics.profit_margin` | `fs_profit_margin` |
| `metrics.profit_on_cost` | `fs_profit_on_cost` |
| `metrics.break_even_sale_rate` | `fs_break_even_sale_rate` |
| `metrics.max_minus` | `fs_max_minus` |
| `metrics.ltv` | `fs_ltv` |
| `metrics.ltc` | `fs_ltc` |
| `metrics.exit_sale_rate` | `fs_exit_sale_rate` |

Repeated data should use Excel tables:

- `tbl_product_mix`
- `tbl_revenue_items`
- `tbl_cost_items`
- `tbl_financing`
- `tbl_schedule`
- `tbl_scenarios`
- `tbl_monthly_cf`

## Formula Preservation Rules

Export:

- Input cells are writable.
- Derived cells should be formulas.
- Summary and check cells should reference workbook formulas, not hard-coded API results.
- API-calculated metrics can be written to a comparison/check section, not over the workbook formula cells.

Import:

- Read both displayed values and formulas.
- Store formula text in `FormulaBinding.formula`.
- Store editable status in `FormulaBinding.editable`.
- Store source address in `ExcelCellRef`.
- Store original workbook metadata in `raw_import`.

Checks:

- `99_Checks` should flag missing required named ranges.
- `99_Checks` should reconcile `total_revenue`, `total_cost`, `development_profit`, and monthly cash-flow totals.
- Import should reject or warn when critical formulas are missing from required cells.

## Implementation Order

1. Generate blank Excel template from `FeasibilityStudyV2`.
2. Export project data into the template while preserving formulas.
3. Import completed Excel workbook back into `FeasibilityStudyV2`.
4. Add formula/error scan and named-range validation.
5. Add API calculation parity checks against Excel outputs.
6. Connect the imported `FeasibilityStudyV2` object to Development Feasibility Agent v2.

## Open Decisions

- Whether the first workbook generator should live in the Next.js API route using `exceljs`, or in the Python API using `openpyxl`/`xlsxwriter`.
- Whether Excel remains the calculation authority for v1 import/export, or whether API formulas become the authority with Excel used as an editable presentation layer.
- Whether workbook files are stored in SQLite blob metadata only, filesystem runtime storage, or object storage in production.
