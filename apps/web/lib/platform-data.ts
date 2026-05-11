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
  landCost: 225.7,
  constructionCost: 864.5,
  financeCost: 116.9,
  otherCost: 245.9,
};

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
