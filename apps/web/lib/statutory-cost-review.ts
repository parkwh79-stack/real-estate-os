import type { SchoolSiteLawBasis } from "./school-site-law-basis";

export const STATUTORY_AMOUNT_KIND = { estimate: "검토 추정액", notified: "고지액", paid: "실납부액" } as const;
export const STATUTORY_COST_INCLUSION = {
  included: "기존 사업비 포함", additional: "추가 반영", not_applicable: "비적용 검토",
} as const;

export type StatutoryAmountKind = keyof typeof STATUTORY_AMOUNT_KIND;
export type StatutoryCostInclusion = keyof typeof STATUTORY_COST_INCLUSION;

type CostSource = { archiveId: string; fileName: string; location: string };
type CostAdoption = {
  amountKind: StatutoryAmountKind | null;
  amountKrw: number | null;
  paymentMonthIndex: number | null;
  inclusion: StatutoryCostInclusion;
  sourceLocator: CostSource;
};
type AmountRecord = {
  amountKrw: number;
  paymentMonthIndex: number | null;
  paidOn?: string;
  sourceLocator: CostSource;
};
export type SavedStatutoryCostItem = CostAdoption & {
  key: string;
  label: string;
  reason: string;
  legalBasis?: SchoolSiteLawBasis;
  amountRecords?: Partial<Record<StatutoryAmountKind, AmountRecord>>;
  previousAdoption?: CostAdoption;
};
export type SavedStatutoryCostSelection = {
  schemaVersion: string;
  baseCalculationRunId: string;
  baseFinancialInputHash: string;
  previousReviewCalculationRunId?: string;
  items: SavedStatutoryCostItem[];
  additionalCostKrw: number;
};
