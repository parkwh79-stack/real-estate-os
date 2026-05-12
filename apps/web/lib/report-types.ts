export const phase11SectionOrder = [
  { index: 1, key: "cover", title: "표지" },
  { index: 2, key: "financialTerms", title: "주요 금융조건 요약표" },
  { index: 3, key: "claimPreservation", title: "주요 채권보전방안" },
  { index: 4, key: "discountTrigger", title: "할인분양 Trigger" },
  { index: 5, key: "fundingStructure", title: "자금조달 구조" },
  { index: 6, key: "useOfFunds", title: "자금 사용 용도" },
  { index: 7, key: "landSecuring", title: "사업부지 확보" },
  { index: 8, key: "projectSchedule", title: "사업추진 일정" },
  { index: 9, key: "financialStructureDiagram", title: "금융구조도" },
  { index: 10, key: "salesPaymentSchedule", title: "분양대금 납입 일정" },
  { index: 11, key: "salesRevenueDistribution", title: "분양수입금 배분" },
  { index: 12, key: "summaryCashFlow", title: "요약 현금흐름표" },
  { index: 13, key: "borrowerAnalysis", title: "차주 분석" },
  { index: 14, key: "contractorAnalysis", title: "시공사 분석" },
  { index: 15, key: "marketAnalysis", title: "분양성 분석" },
  { index: 16, key: "businessFeasibility", title: "사업성 분석" },
  { index: 17, key: "exitStrategy", title: "Exit 전략 및 리스크" },
  { index: 18, key: "drawdownConditions", title: "인출 선행/후행조건" },
  { index: 19, key: "eodTriggers", title: "채무불이행 사유(EOD)" },
  { index: 20, key: "fundExecutionOrder", title: "자금집행 순서 및 변제 충당 순위" },
] as const;

export type Phase11SectionKey = (typeof phase11SectionOrder)[number]["key"];

export interface Phase11SectionMeta {
  index: number;
  key: Phase11SectionKey;
  title: string;
}

export interface Phase11QualityChecks {
  expectedSectionCount: number;
  actualSectionCount: number;
  missingSections: string[];
  isComplete: boolean;
}

export type Phase11Sections = Record<Phase11SectionKey, unknown>;

export interface Phase11ReportPayload {
  template: "phase-11-pf-review";
  title: string;
  status: "draft" | "approved" | string;
  unit: "KRW" | string;
  displayUnit: string;
  sectionOrder: Phase11SectionMeta[];
  sections: Phase11Sections;
  qualityChecks: Phase11QualityChecks;
}

export interface Phase11ReportApiResponse {
  project_id: string;
  report_id: string;
  template: "phase-11-pf-review";
  report: Phase11ReportPayload;
  report_markdown: string;
  metadata: {
    section_count: number;
    expected_section_count: number;
    complete: boolean;
  };
}
