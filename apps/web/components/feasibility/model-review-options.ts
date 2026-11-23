export type Model = "dated_financing" | "rental_exit";
type ModelOption = {
  label: string;
  description: string;
  example: Record<string, unknown>;
  metrics: ReadonlyArray<readonly [string, string]>;
  detailKey: string;
  detailColumns: ReadonlyArray<readonly [string, string, boolean]>;
};

export const modelReviewOptions: Record<Model, ModelOption> = {
  dated_financing: {
    label: "날짜별 대출과 만기 상환",
    description: "약정한도, 인출 조건, 적용 금리와 상환계정의 입출금을 입력합니다. 금액은 원, 금리는 소수 비율(5%는 0.05)입니다.",
    metrics: [
      ["totalDrawn", "실행 인출액"], ["totalInterestAccrued", "발생 이자"],
      ["peakMaturityShortfall", "최대 만기 부족액"], ["endingRepaymentCash", "기말 상환계정"],
    ],
    detailKey: "maturities",
    detailColumns: [
      ["date", "만기일", false], ["trancheId", "대출", false], ["totalDue", "상환 의무", true],
      ["availableBeforePayment", "가용 상환재원", true], ["totalPaid", "상환액", true], ["shortfall", "부족액", true],
    ],
    example: {
      currency: "KRW",
      asOfDate: "2027-01-01",
      openingRepaymentCash: 0,
      repaymentCashMovements: [{ date: "2027-01-01", amount: 90_000_000 }],
      tranches: [{
        trancheId: "선순위", commitment: 100_000_000, drawAvailableFrom: "2026-01-01",
        drawAvailableThrough: "2026-12-31", maturityDate: "2027-01-01", dayCount: "ACT/365F", repaymentPriority: 1,
        rateSchedule: [{ date: "2026-01-01", annualRate: 0.05 }],
        draws: [{ date: "2026-01-01", amount: 100_000_000, conditionsMet: true }],
        scheduledPrincipal: [], interestPaymentDates: [], fees: [],
      }],
    },
  },
  rental_exit: {
    label: "임대 운영·차환·매각·투자자 회수",
    description: "기간별 임대수입·비용·보증금, 만기 상환, 차환 한도와 매각 정산 조건을 입력합니다. 미적용 차환 기준은 null로 지정합니다.",
    metrics: [
      ["maturityShortfall", "만기 상환부족액"], ["netSaleCash", "순매각 현금"],
      ["distributableCash", "투자자 배분가능 현금"], ["unpaidInvestorCapital", "투자자 미회수 원금"],
    ],
    detailKey: "investorRecovery",
    detailColumns: [["id", "투자자", false], ["investedCapital", "출자금", true],
      ["returnedCapital", "원금 회수", true], ["profitDistribution", "잔여현금 배분", true],
      ["unpaidCapital", "미회수 원금", true]],
    example: {
      currency: "KRW", asOfDate: "2026-01-01", openingUnrestrictedCash: 0, openingDepositBalance: 0,
      periods: [{ date: "2026-12-31", potentialRent: 20_000_000, occupancyRate: 0.9, otherOperatingIncome: 0,
        operatingExpense: 5_000_000, capitalExpense: 0, depositReceived: 0, depositRefunded: 0, debtService: 0 }],
      maturity: { date: "2027-01-01", principal: 100_000_000, interest: 5_000_000, fees: 0 },
      refinance: { fundingDate: "2027-01-01", requestedPrincipal: 100_000_000, fees: 1_000_000, reserve: 0,
        ltv: { valuation: 200_000_000, limit: 0.6 }, dscr: null, debtYield: null, commitmentLimit: null },
      exit: { date: "2028-01-01", grossSalePrice: 220_000_000, transactionCosts: 5_000_000, taxes: 0,
        depositTreatment: "refund", refinancePayoffInterest: 5_000_000, refinancePayoffFees: 0, overdueDebtCharges: 0 },
      investors: [{ id: "LP", investedAt: "2026-01-01", capital: 60_000_000, principalPriority: 1, remainderBps: 8000 },
        { id: "GP", investedAt: "2026-01-01", capital: 20_000_000, principalPriority: 2, remainderBps: 2000 }],
    },
  },
};
