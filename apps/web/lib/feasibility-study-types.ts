export const FEASIBILITY_STUDY_SCHEMA_VERSION = "realytics-feasibility-study-v2" as const;
export const FEASIBILITY_WORKBOOK_VERSION = "realytics-fs-xlsx-v1" as const;

export type AssetType =
  | "apartment"
  | "officetel"
  | "knowledge_industry_center"
  | "mixed_use"
  | "commercial"
  | "logistics"
  | "lodging"
  | "residential_land"
  | "other";

export type AmountUnit = "KRW" | "KRW_MILLION" | "SQM" | "PY" | "PERCENT" | "COUNT" | "MONTH";

export type ExcelCellRef = {
  sheet_name: string;
  cell?: string;
  named_range?: string;
  table_name?: string;
  column_name?: string;
};

export type FieldProvenance = {
  source_document?: string;
  source_type?: "pdf" | "docx" | "xlsx" | "manual" | "api" | "mcp";
  page?: number;
  sheet_name?: string;
  cell?: string;
  named_range?: string;
  raw_text?: string;
  confidence?: number;
};

export type FormulaBinding = {
  formula?: string;
  formula_type: "input" | "excel_formula" | "api_formula" | "derived";
  depends_on: string[];
  editable: boolean;
  locked_in_template: boolean;
};

export type FeasibilityInputReferences = {
  approved_input_snapshot_id?: string;
  approved_input_snapshot_hash?: string;
  source_snapshot_id?: string;
  site_parcel_snapshot_id?: string;
  parcel_set_hash?: string;
  regulatory_constraint_snapshot_id?: string;
  buildable_envelope_id?: string;
  constraint_as_of_date?: string;
  formula_versions: Record<string, string>;
};

export type FormulaTraceRecord = {
  formula_id: string;
  formula_version: string;
  output_path: string;
  input_paths: string[];
  intermediate_values: Record<string, number | string | boolean | null>;
  rounding_policy?: string;
  source_locator?: Record<string, unknown>;
};

export type FeasibilityValue = {
  key: string;
  label: string;
  value?: number | string | boolean | null;
  unit?: AmountUnit;
  excel?: ExcelCellRef;
  formula?: FormulaBinding;
  provenance?: FieldProvenance;
  notes?: string;
};

export type SiteInputs = {
  address?: string;
  land_area_sqm?: FeasibilityValue;
  zoning?: string;
  legal_far?: FeasibilityValue;
  legal_bcr?: FeasibilityValue;
  acquired_land_ratio?: FeasibilityValue;
};

export type ProgramInputs = {
  gross_floor_area_sqm?: FeasibilityValue;
  saleable_area_sqm?: FeasibilityValue;
  contract_area_sqm?: FeasibilityValue;
  floors_above_ground?: FeasibilityValue;
  floors_below_ground?: FeasibilityValue;
  parking_count?: FeasibilityValue;
  unit_count?: FeasibilityValue;
};

export type ProductMixRow = {
  key: string;
  asset_type: AssetType;
  product_name: string;
  unit_count?: number;
  exclusive_area_sqm?: number;
  saleable_area_sqm?: number;
  contract_area_sqm?: number;
  avg_sale_price_per_sqm?: number;
  avg_sale_price_per_py?: number;
  revenue?: number;
  excel_row_id?: string;
  provenance?: FieldProvenance;
};

export type LineItem = {
  key: string;
  label: string;
  category: string;
  amount?: number;
  quantity?: number;
  unit_price?: number;
  unit: AmountUnit;
  excel?: ExcelCellRef;
  formula?: FormulaBinding;
  provenance?: FieldProvenance;
};

export type FinancingTranche = {
  tranche_id: string;
  name: string;
  priority: "senior" | "mezzanine" | "junior" | "equity" | "bridge" | "other";
  amount?: number;
  interest_rate?: number;
  fee_rate?: number;
  term_months?: number;
  repayment_source?: string;
  excel_row_id?: string;
};

export type ScheduleMilestone = {
  key: string;
  label: string;
  planned_date?: string;
  actual_date?: string;
  month_index?: number;
  excel?: ExcelCellRef;
  provenance?: FieldProvenance;
};

export type ScenarioAssumption = {
  scenario_id: string;
  label: string;
  sales_price_factor: number;
  construction_cost_factor: number;
  interest_rate_addition: number;
  sales_rate?: number;
  delay_months: number;
  scenario_class: "current_rights" | "approved_permit" | "conditional_incentive" | "speculative_rezoning";
  constraint_snapshot_id?: string;
  program_candidate_id?: string;
  approval_decision_id?: string;
  eligibility?: "eligible" | "conditional" | "ineligible" | "not_assessable";
  blocking_constraint_ids: string[];
  condition_ids: string[];
  report_grade: boolean;
};

export type MonthlyCashFlowRow = {
  month_index: number;
  period: string;
  cash_in: Record<string, number>;
  cash_out: Record<string, number>;
  pf_draw: Record<string, number>;
  loan_repayment: Record<string, number>;
  equity_contribution: Record<string, number>;
  equity_recovery: Record<string, number>;
  other_financing_inflow: Record<string, number>;
  other_financing_outflow: Record<string, number>;
  operating_account_inflow?: number;
  operating_account_outflow?: number;
  repayment_reserve_inflow?: number;
  repayment_reserve_outflow?: number;
  net_cash_flow?: number;
  cumulative_cash_flow?: number;
  source_locator?: Record<string, unknown>;
};

export type WorkbookSheetSpec = {
  sheet_name: string;
  role:
    | "cover"
    | "inputs"
    | "product_mix"
    | "costs"
    | "financing"
    | "monthly_cf"
    | "sensitivity"
    | "summary"
    | "checks"
    | "export_map";
  required: boolean;
  description?: string;
};

export type WorkbookMapping = {
  workbook_version: typeof FEASIBILITY_WORKBOOK_VERSION;
  template_name: string;
  formula_policy: "preserve_excel_formulas" | "api_calculates" | "hybrid";
  unit_scale: AmountUnit;
  sheets: WorkbookSheetSpec[];
  named_ranges: Record<string, string>;
};

export type FeasibilityMetrics = {
  total_revenue?: number;
  total_cost?: number;
  development_profit?: number;
  profit_margin?: number;
  profit_on_cost?: number;
  break_even_sale_rate?: number;
  required_equity?: number;
  max_minus?: number;
  ltv?: number;
  ltc?: number;
  exit_sale_rate?: number;
};

export type FeasibilityStudyV2 = {
  version: typeof FEASIBILITY_STUDY_SCHEMA_VERSION;
  project_id?: string;
  project_name?: string;
  asset_type: AssetType;
  currency: "KRW";
  calculation_date?: string;
  input_references: FeasibilityInputReferences;
  site: SiteInputs;
  program: ProgramInputs;
  product_mix: ProductMixRow[];
  revenue_items: LineItem[];
  cost_items: LineItem[];
  financing: FinancingTranche[];
  schedule: ScheduleMilestone[];
  scenarios: ScenarioAssumption[];
  formula_traces: FormulaTraceRecord[];
  monthly_cash_flow: MonthlyCashFlowRow[];
  workbook: WorkbookMapping;
  metrics?: FeasibilityMetrics;
  raw_import: Record<string, unknown>;
};
