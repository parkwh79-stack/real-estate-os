export type ProjectStatus = "검토중" | "조건부 승인" | "보류";

export interface ProjectSummary {
  id: string;
  name: string;
  address: string;
  pnu: string;
  assetType: string;
  status: ProjectStatus;
  risk: "Low" | "Medium" | "High";
  pfScore: number;
  totalRevenue: number;
  totalCost: number;
  totalLoan: number;
  updatedAt: string;
}

export interface Transaction {
  id: string;
  address: string;
  transaction_date: string;
  total_price_krw: string;
  unit_price_krw: string;
  property_type: string;
  lat: number;
  lng: number;
}

export interface CashFlowRow {
  category: string;
  subCategory: string;
  total: number;
  values: number[];
  isTotal?: boolean;
  isNegative?: boolean;
}

export interface Tranche {
  name: string;
  amount: number;
  rate: number;
  fee: number;
  priority: string;
  lenders: string;
}

export interface CalculationTraceStep {
  id: string;
  label: string;
  workbookSheet: string;
  sampleSource: string;
  webOutput: string;
  formula: string;
  explanation: string;
}

export interface ExcelMirrorSheet {
  sheetName: string;
  role: string;
  sourceRange: string;
  webPurpose: string;
  status: "반영" | "부분 반영" | "다음 단계";
}

export const demoProject: ProjectSummary = {
  id: "P9-2026-05A",
  name: "대구 달서구 본동 주상복합 PF",
  address: "대구광역시 달서구 본동 743 일원",
  pnu: "2729012400107430000",
  assetType: "주상복합 개발",
  status: "조건부 승인",
  risk: "Medium",
  pfScore: 82,
  totalRevenue: 1656.1,
  totalCost: 1453.1,
  totalLoan: 900,
  updatedAt: "2026.05.08",
};

export const projects: ProjectSummary[] = [
  demoProject,
  {
    id: "P9-2026-04B",
    name: "성수동2가 복합개발",
    address: "서울 성동구 성수동2가",
    pnu: "1120011500102770000",
    assetType: "업무/근린생활",
    status: "검토중",
    risk: "Low",
    pfScore: 88,
    totalRevenue: 1200,
    totalCost: 970,
    totalLoan: 825,
    updatedAt: "2026.05.06",
  },
  {
    id: "P9-2026-03C",
    name: "강남역 오피스 리포지셔닝",
    address: "서울 강남구 역삼동 825-2",
    pnu: "1168010100108250002",
    assetType: "상업/업무",
    status: "검토중",
    risk: "Medium",
    pfScore: 76,
    totalRevenue: 980,
    totalCost: 840,
    totalLoan: 620,
    updatedAt: "2026.05.03",
  },
  {
    id: "P9-2026-02D",
    name: "용산 후암동 고급주거",
    address: "서울 용산구 후암동",
    pnu: "1117013100100010000",
    assetType: "공동주택",
    status: "보류",
    risk: "High",
    pfScore: 62,
    totalRevenue: 740,
    totalCost: 705,
    totalLoan: 470,
    updatedAt: "2026.04.28",
  },
];

export const transactions: Transaction[] = [
  {
    id: "tx-1",
    address: "역삼동 826-1",
    transaction_date: "2026.02",
    total_price_krw: "350억",
    unit_price_krw: "1.60억/평",
    property_type: "상업업무",
    lat: 37.4985,
    lng: 127.025,
  },
  {
    id: "tx-2",
    address: "역삼동 825-9",
    transaction_date: "2025.11",
    total_price_krw: "280억",
    unit_price_krw: "1.45억/평",
    property_type: "상업업무",
    lat: 37.4975,
    lng: 127.0265,
  },
  {
    id: "tx-3",
    address: "역삼동 824-3",
    transaction_date: "2025.08",
    total_price_krw: "195억",
    unit_price_krw: "1.30억/평",
    property_type: "근린상가",
    lat: 37.4965,
    lng: 127.029,
  },
  {
    id: "tx-4",
    address: "역삼동 830-2",
    transaction_date: "2025.05",
    total_price_krw: "420억",
    unit_price_krw: "1.55억/평",
    property_type: "상업업무",
    lat: 37.499,
    lng: 127.03,
  },
];

export const tranches: Tranche[] = [
  {
    name: "Tr.A Senior",
    amount: 630,
    rate: 4.5,
    fee: 1.5,
    priority: "1순위 관리형 토지신탁 수익권",
    lenders: "시중은행, 하나캐피탈",
  },
  {
    name: "Tr.B Mezzanine",
    amount: 120,
    rate: 6.5,
    fee: 5,
    priority: "2순위 관리형 토지신탁 수익권",
    lenders: "전문투자형 사모펀드",
  },
  {
    name: "Tr.C Junior",
    amount: 150,
    rate: 8,
    fee: 10,
    priority: "3순위 관리형 토지신탁 수익권",
    lenders: "증권사, 대체투자 펀드",
  },
];

export const cashFlowAssumptions = {
  revenue: 1656.1,
  landCost: 460.3,
  constructionCost: 720.4,
  financeCost: 163.0,
  otherCost: 174.7,
};

export const excelMirrorSheets: ExcelMirrorSheet[] = [
  {
    sheetName: "입력",
    role: "사업개요와 건축개요",
    sourceRange: "샘플 입력!D2:E26, M5:M8",
    webPurpose: "대지면적, 연면적, 용적률, 사업기간, 상품별 전용면적을 계산 전제값으로 사용",
    status: "반영",
  },
  {
    sheetName: "분양",
    role: "상품별 분양수입",
    sourceRange: "샘플 분양!3:42",
    webPurpose: "아파트, 오피스텔, 근린생활시설의 세대수, 계약면적, 분양수입을 합산",
    status: "반영",
  },
  {
    sheetName: "사업수지",
    role: "수입·비용·시행이익",
    sourceRange: "샘플 사업수지!D8:D74",
    webPurpose: "총분양수입, 총사업비, 시행이익, 이익률, LTC/LTV 계산의 기준표",
    status: "반영",
  },
  {
    sheetName: "2.현금흐름",
    role: "월별 Cash Flow",
    sourceRange: "샘플 2.현금흐름!31:130",
    webPurpose: "분양수입, 사업비, PF 인출·상환, 누적 현금흐름과 Max Minus 산정",
    status: "부분 반영",
  },
  {
    sheetName: "05_수식지도 / 07_Import계약",
    role: "계산 감사와 서버 재수입",
    sourceRange: "REALYTICS 확장 시트",
    webPurpose: "각 숫자의 수식, 원천 셀, named range, 서버 import 계약을 추적",
    status: "반영",
  },
];

export const calculationTraceSteps: CalculationTraceStep[] = [
  {
    id: "revenue",
    label: "총분양수입",
    workbookSheet: "분양 → 사업수지",
    sampleSource: "분양!J21 + J30 + J40 = 사업수지!D8",
    webOutput: "02A_분양수입!C9, 02_사업수지!C4",
    formula: "=SUM(아파트 분양수입, 오피스텔 분양수입, 근린생활시설 분양수입)",
    explanation: "상품별 공급가액을 먼저 산정하고, 부가세 제외 기준 총분양수입으로 사업수지에 연결합니다.",
  },
  {
    id: "cost",
    label: "총사업비",
    workbookSheet: "사업수지",
    sampleSource: "사업수지!D18,D26,D33,D41,D52,D62,D71",
    webOutput: "02_사업수지!C13",
    formula: "=대지비+공사비+용역비+분양제경비+부담금+금융비용+기타사업비",
    explanation: "실제 샘플의 비용 소계 행을 그대로 비용 카테고리로 가져와 총사업비를 계산합니다.",
  },
  {
    id: "profit",
    label: "시행이익",
    workbookSheet: "사업수지",
    sampleSource: "사업수지!D74",
    webOutput: "02_사업수지!C15",
    formula: "=총분양수입-총사업비",
    explanation: "개발사업성의 1차 판단값입니다. 이후 매출이익률과 원가대비 이익률로 전환합니다.",
  },
  {
    id: "monthly-cf",
    label: "월별 Cash Flow",
    workbookSheet: "2.현금흐름",
    sampleSource: "2.현금흐름!31:130",
    webOutput: "03_월별현금흐름!D:AV",
    formula: "=월별 수입합계-월별 지출합계",
    explanation: "분양대금 수납시점, 공정률 기반 공사비, 금융비용을 월별로 배분해 순현금흐름을 계산합니다.",
  },
  {
    id: "max-minus",
    label: "Max Minus",
    workbookSheet: "2.현금흐름 → 03_월별현금흐름",
    sampleSource: "누계 현금흐름 최저점",
    webOutput: "03_월별현금흐름!C22",
    formula: "=MIN(월별 누적현금흐름)",
    explanation: "프로젝트 기간 중 가장 큰 자금부족 지점입니다. PF 한도와 Equity 투입 필요성을 판단합니다.",
  },
  {
    id: "pf-repayment",
    label: "PF 인출·상환",
    workbookSheet: "2.현금흐름 → 03B/03C",
    sampleSource: "2.현금흐름!48:55, 93:109",
    webOutput: "03B_PF인출상환, 03C_Tranche상환",
    formula: "=상환적립 가능액을 이자와 원금에 우선 배분",
    explanation: "분양수입금 Waterfall을 반영해 선순위, 중순위, 후순위 회수 가능성을 검토합니다.",
  },
];

export function formatEok(value: number, fractionDigits = 1) {
  return `${value.toLocaleString("ko-KR", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })}억`;
}

export function formatPercent(value: number, fractionDigits = 1) {
  return `${value.toLocaleString("ko-KR", {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })}%`;
}

export function generateCashFlowRows(): CashFlowRow[] {
  const revenueValues = Array.from({ length: 24 }, (_, index) =>
    index >= 11 ? 127.4 : 0,
  );
  const landValues = Array.from({ length: 24 }, (_, index) =>
    index === 0 ? cashFlowAssumptions.landCost : 0,
  );
  const constructionValues = Array.from({ length: 24 }, (_, index) =>
    index >= 2 ? 39.3 : 0,
  );
  const financeValues = Array.from({ length: 24 }, (_, index) =>
    index >= 2 ? 5.3 : 0,
  );
  const otherValues = Array.from({ length: 24 }, () => 10.2);

  const expenseValues = revenueValues.map((_, index) =>
    landValues[index]! +
    constructionValues[index]! +
    financeValues[index]! +
    otherValues[index]!,
  );
  const netValues = revenueValues.map((value, index) => value - expenseValues[index]!);

  let cumulative = 0;
  const cumulativeValues = netValues.map((value) => {
    cumulative += value;
    return Number(cumulative.toFixed(1));
  });

  const total = (values: number[]) =>
    Number(values.reduce((sum, value) => sum + value, 0).toFixed(1));

  return [
    {
      category: "Cash In",
      subCategory: "분양 수입",
      total: total(revenueValues),
      values: revenueValues,
    },
    {
      category: "Cash In",
      subCategory: "수입 합계",
      total: total(revenueValues),
      values: revenueValues,
      isTotal: true,
    },
    {
      category: "Cash Out",
      subCategory: "토지비",
      total: total(landValues),
      values: landValues,
      isNegative: true,
    },
    {
      category: "Cash Out",
      subCategory: "공사비",
      total: total(constructionValues),
      values: constructionValues,
      isNegative: true,
    },
    {
      category: "Cash Out",
      subCategory: "PF 금융비용",
      total: total(financeValues),
      values: financeValues,
      isNegative: true,
    },
    {
      category: "Cash Out",
      subCategory: "기타 사업비",
      total: total(otherValues),
      values: otherValues,
      isNegative: true,
    },
    {
      category: "Cash Out",
      subCategory: "지출 합계",
      total: total(expenseValues),
      values: expenseValues,
      isTotal: true,
      isNegative: true,
    },
    {
      category: "Net CF",
      subCategory: "월별 순현금흐름",
      total: total(netValues),
      values: netValues,
      isTotal: true,
    },
    {
      category: "Cumulative CF",
      subCategory: "누적 현금흐름",
      total: cumulativeValues[cumulativeValues.length - 1] ?? 0,
      values: cumulativeValues,
      isTotal: true,
    },
  ];
}

export function getMaxMinusPoint() {
  const cumulative = generateCashFlowRows().find(
    (row) => row.category === "Cumulative CF",
  );
  const values = cumulative?.values ?? [];
  return values.reduce(
    (worst, value, index) =>
      value < worst.value ? { month: `M${index + 1}`, value } : worst,
    { month: "M1", value: values[0] ?? 0 },
  );
}

export function calcIRR(cashFlows: number[]): number | null {
  const npv = (rate: number) =>
    cashFlows.reduce((sum, cashFlow, index) => sum + cashFlow / (1 + rate) ** index, 0);

  let low = -0.999;
  let high = 10;

  if (npv(low) * npv(high) > 0) {
    return null;
  }

  for (let index = 0; index < 200; index += 1) {
    const mid = (low + high) / 2;
    const midValue = npv(mid);

    if (Math.abs(midValue) < 1e-8) {
      return mid;
    }

    if (npv(low) * midValue < 0) {
      high = mid;
    } else {
      low = mid;
    }
  }

  return (low + high) / 2;
}
