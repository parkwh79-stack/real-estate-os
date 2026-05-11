import { demoProject } from "./platform-data";

export const coverData = {
  projectName: demoProject.name,
  reviewDate: "2026.05.08",
  financialInstitution: "하나금융투자 부동산금융본부",
  summary: "대구 달서구 본동 일원 주상복합 개발사업 PF (조건부 승인)",
};

export const financeConditions = {
  totalAmount: 900,
  tranches: [
    { name: "Tranche A (Senior)", amount: 630, rate: 4.5, fee: 1.5, allInCost: 5.25, priority: "1순위" },
    { name: "Tranche B (Mezzanine)", amount: 120, rate: 6.5, fee: 5.0, allInCost: 8.75, priority: "2순위" },
    { name: "Tranche C (Junior)", amount: 150, rate: 8.0, fee: 10.0, allInCost: 12.5, priority: "3순위" },
  ]
};

export const bondPreservation = [
  { category: "신탁구조", desc: "관리형 토지신탁 (우선수익권 설정)" },
  { category: "책임준공", desc: "신세계건설 책임준공 (미이행시 채무인수)" },
  { category: "연대보증", desc: "시행사 대표이사 개인 연대보증" },
  { category: "Covenant", desc: "LTV 75% 이하 유지, 분양률 6개월 내 40% 달성" }
];

export const discountTrigger = [
  { period: "분양개시 후 6개월", targetRate: 40, discountRate: "해당없음" },
  { period: "분양개시 후 12개월", targetRate: 60, discountRate: "분양가의 5% 할인" },
  { period: "분양개시 후 18개월", targetRate: 80, discountRate: "분양가의 10% 할인" }
];

export const fundingStructure = {
  cashIn: [
    { item: "Equity (자기자본)", amount: 17440 },
    { item: "PF 대출", amount: 90000 },
    { item: "분양수입", amount: 165610 }
  ],
  cashOut: [
    { item: "토지비", amount: 22570 },
    { item: "공사비", amount: 86450 },
    { item: "금융비용", amount: 11690 },
    { item: "기타사업비", amount: 24590 }
  ]
};

export const landSecuring = [
  { parcel: "대구 달서구 본동 743", owner: "(주)리얼코어", share: "100%", area: "3,500㎡", mortgage: "없음 (신탁등기 완료)" }
];

export const projectSchedule = [
  { step: "토지매입", planned: "2025.12", completed: "2025.12", status: "완료" },
  { step: "건축허가", planned: "2026.03", completed: "2026.03", status: "완료" },
  { step: "PF 기표", planned: "2026.08", completed: "-", status: "예정" },
  { step: "착공/분양", planned: "2026.09", completed: "-", status: "예정" },
  { step: "준공", planned: "2028.09", completed: "-", status: "예정" }
];

export const financialStructureText = `
[차주] (주)리얼코어개발은 본 사업의 시행사로서 인허가 및 분양을 총괄합니다.
[신탁사] 하나자산신탁이 관리형 토지신탁을 수탁하여 자금관리 및 사업부지 소유권을 보전합니다.
[시공사] 신세계건설이 단순도급 및 책임준공 의무를 부담합니다.
[대주단] 하나금융투자를 주관사로 하여 Tr.A~C 대주단이 900억원을 한도로 대출을 실행합니다.
`;

export const salesPaymentSchedule = [
  { type: "계약금", ratio: 10, time: "계약시" },
  { type: "중도금", ratio: 60, time: "공정률에 따라 6회 분할" },
  { type: "잔금", ratio: 30, time: "입주지정기간" }
];

export const salesRevenueDistribution = [
  { rank: 1, item: "제세공과금 및 신탁보수" },
  { rank: 2, item: "필수사업비 (공사비 일부 등)" },
  { rank: 3, item: "Tr.A 대출원리금 상환" },
  { rank: 4, item: "Tr.B 대출원리금 상환" },
  { rank: 5, item: "Tr.C 대출원리금 상환" },
  { rank: 6, item: "시공사 잔여 공사비" },
  { rank: 7, item: "시행사 이익" }
];

export const summaryCashFlow = [
  { month: "M1", in: 900, out: 800, net: 100, cum: 100 },
  { month: "M12", in: 1500, out: 1200, net: 300, cum: -400 },
  { month: "M24", in: 4000, out: 1000, net: 3000, cum: 2000 }
];

export const borrowerAnalysis = {
  name: "(주)리얼코어개발",
  established: "2018.05",
  creditRating: "BB+",
  financials: "자산 500억 / 부채 300억 / 자본 200억 (2025년말 기준)"
};

export const contractorAnalysis = {
  name: "신세계건설(주)",
  creditRating: "A-",
  orderBacklog: "3.5조원",
  financials: "부채비율 250%, 유동비율 120%"
};

export const marketAnalysis = {
  location: "달서구 중심상업지구 인접, 교통 및 편의시설 우수",
  competitors: "인근 A단지 (분양률 95%), B단지 (분양률 80%)",
  priceReasonableness: "주변 시세 대비 105% 수준이나, 신축 프리미엄 감안 시 적정"
};

export const feasibilityAnalysis = {
  ltv: 54.3,
  exitSalesRate: 67.0,
  priceLtv: 60.5
};

export const exitStrategy = {
  scenario1: "준공 시점 미분양 물량에 대해 할인분양 (최대 15%) 실시하여 LTV 70% 이내 회수",
  scenario2: "대물변제 및 임대 후 매각 (시공사 책임준공 불이행 시 신용보강 활용)"
};

export const drawdownConditionsFull = [
  "본건 PF 대출약정 및 부속계약 체결 완료",
  "사업부지 소유권 신탁등기 및 1순위 우선수익권 설정 완료",
  "건축허가 (사업승인) 완료",
  "시공사 책임준공 확약서 및 도급계약서 징구",
  "시행사 대표이사 연대보증서 징구",
  "자기자본(Equity) 전액 투입 증빙"
];

export const eodTriggers = {
  automatic: ["차주의 파산, 회생절차 개시 신청", "사업부지 가압류/가처분 등 권리침해 발생"],
  optional: ["원리금 연체 5영업일 이상", "LTV, 분양률 등 주요 Covenant 위반 시", "시공사 부도 또는 워크아웃 개시"]
};

export const fundExecutionOrder = [
  "1순위: 필수 제세공과금",
  "2순위: 신탁보수 및 대리사무보수",
  "3순위: PF대출 이자",
  "4순위: 필수 사업비 (공사비 기성 포함)",
  "5순위: PF대출 원금 (Tr.A -> Tr.B -> Tr.C)",
  "6순위: 기타 사업비 및 시행사 이익"
];
