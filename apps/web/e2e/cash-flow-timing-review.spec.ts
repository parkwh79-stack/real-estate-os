import { expect, test, type Page } from "@playwright/test";
import type { CashFlowTiming, CashFlowTimingSource } from "../lib/cash-flow-timing";

test.setTimeout(90_000);
const projectId = "synthetic-timing-review";
const source: CashFlowTimingSource = {
  sourceBasisHash: "b".repeat(64), sourceDocument: { originalFileName: "합성 시간 검토.xlsx" },
  basis: { periods: [
    { sourcePeriodIndex: 0, label: "토지계약", periodKind: "event", sourceMonthNumber: null, sourceLocator: { sheet: "CF", cell: "F4" } },
    { sourcePeriodIndex: 1, label: "1", periodKind: "monthly", sourceMonthNumber: 1, sourceLocator: { sheet: "CF", cell: "G4" } },
    { sourcePeriodIndex: 2, label: "2", periodKind: "monthly", sourceMonthNumber: 2, sourceLocator: { sheet: "CF", cell: "H4" } },
  ], warnings: ["사건 날짜는 미확인입니다."] },
};
const inputUrl = `/project/${projectId}/input-package?view=review`;

async function fixture(page: Page, options: { saveStatus?: number } = {}) {
  // MOCK-DATA: offline UI contract; financial policy and persistence are tested by the API suite.
  const state = {
    reviewHash: "timing-review-0", values: { total_revenue: 30_000_000_000 }, fieldReviews: {}, sourceDocuments: [],
    terms: { schedule: {}, financing: [], cashFlowTiming: null as CashFlowTiming | null },
    termReviews: {} as Record<string, { origin: string; reason: string; sourceCell: string }>,
    cashFlowTimingSource: source,
  };
  const posts: Record<string, unknown>[] = [];
  const calculations: Record<string, unknown>[] = [];
  let runTiming: CashFlowTiming | null = null;
  const storedResult = () => ({
    engineVersion: "cash_flow_engine-v2", analysisOnly: true, reportEligible: false,
    summary: { totalRevenue: 30_000_000_000, totalProjectCost: 10_000_000_000, maxMinus: -10_000_000_000, maxMinusMonth: 1 },
    monthlyCashFlow: [0, 1, 2].map((monthIndex) => ({ monthIndex, period: `M${monthIndex}`,
      cashIn: { totalCashIn: 0 }, cashOut: { totalCashOut: 0 }, netCashFlow: 0, cumulativeCashFlow: 0,
      monthlyFinancing: { interest: 0, fees: 0, principalRepayment: 0, outstandingDebt: 0, overdueDebt: 0, unfundedOperatingCost: 0 } })),
    monthlyFinancing: { calculatedInterest: 0 },
    periodBasis: { schemaVersion: "cash-flow-period-basis-v1", kind: "calendar_monthly",
      timingAssumptions: runTiming, timingReview: state.termReviews.cashFlowTiming,
      sourcePeriodBasis: { schemaVersion: "cash-flow-period-basis-v1", kind: "source_sequence", ...source.basis },
      sourcePeriodMonthBindings: [{ sourcePeriodIndex: 0, monthIndex: 0 }, { sourcePeriodIndex: 1, monthIndex: 1 }, { sourcePeriodIndex: 2, monthIndex: 2 }],
      calculationPeriodCount: 3, firstCalculationPeriodIndex: 0, lastCalculationPeriodIndex: 2 },
  });
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path.endsWith("/auth/me")) return reply({ authenticated: true,
      user: { userId: "synthetic-analyst", tenantId: "synthetic-timing-tenant", role: "admin", status: "active" },
      permissions: ["project:read", "project:analyze", "assumption:override"],
    });
    if (path.endsWith("/workspace/context")) return reply({ projectId, projectMemory: { projectName: "합성 시간 가정" }, pageData: {} });
    if (path.endsWith("/feasibility-workbook/review")) return reply(state);
    if (path.endsWith("/feasibility-workbook/apply-input")) {
      const body = request.postDataJSON(); posts.push(body);
      if (options.saveStatus) return reply({ detail: "저장된 입력이 변경되었습니다. 최신 값을 다시 불러온 뒤 수정하세요." }, options.saveStatus);
      state.terms.cashFlowTiming = body.terms.cashFlowTiming;
      state.termReviews.cashFlowTiming = body.termReviews.cashFlowTiming;
      state.reviewHash = `timing-review-${posts.length}`;
      return reply({ reviewState: state });
    }
    if (path.endsWith("/cash-flow/run")) {
      const body = request.postDataJSON(); calculations.push(body);
      runTiming = structuredClone(state.terms.cashFlowTiming);
      return reply({ ...storedResult(), calculationRunId: "synthetic-timed-run" });
    }
    if (path.endsWith("/calculation-runs")) return reply({ records: runTiming && url.searchParams.get("engine_key") === "cash_flow_engine"
      ? [{ calculationRunId: "synthetic-timed-run", engineKey: "cash_flow_engine",
        inputSnapshot: { monthlyFinancing: {}, periodBasis: storedResult().periodBasis }, resultSnapshot: storedResult() }] : [] });
    if (path.endsWith("/cash-flow/comparison")) return reply({ comparisonHash: "synthetic-comparison", selection: null, comparison: null,
      availableRuns: runTiming ? [{ calculationRunId: "synthetic-timed-run", policyMode: "reviewed_monthly_v1", period: { baseMonth: "2026-01", lastMonthIndex: 2 } }] : [] });
    if (path.endsWith("/orchestrations")) return reply([]);
    return reply({});
  });
  await page.goto(inputUrl, { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("region", { name: "사업수지 입력 검토" });
  await expect(panel.getByLabel("계산 M0 기준일")).toBeEnabled({ timeout: 60_000 });
  return { panel, posts, calculations, state };
}

async function complete(page: Page) {
  const panel = page.getByRole("region", { name: "사업수지 입력 검토" });
  await panel.getByLabel("계산 M0 기준일").fill("2026-01-01");
  await panel.getByLabel("원본 1개월의 계산 월차").fill("1");
  await panel.getByLabel("토지계약 분석용 사건 날짜").fill("2026-01-15");
  await panel.getByLabel("토지계약 계산 월차").fill("0");
  await panel.getByLabel("표에 없는 월은 분양수금 0원으로 가정합니다").check();
  await panel.getByRole("button", { name: "월별 수금 추가", exact: true }).click();
  for (const [label, value] of [["월차", "2"], ["계약금", "0"], ["중도금", "0"], ["잔금", "30000000000"]]) {
    await panel.getByLabel(`수금 1 ${label}`, { exact: true }).fill(value!);
  }
  await panel.getByLabel("시간·수금 가정 이유").fill("실제 날짜와 약정 확인 전의 명시적 합성 분석 가정");
}

test("partial timing assumptions save and reopen without filling unconfirmed dates, months or zero collections", async ({ page }) => {
  const { panel, posts } = await fixture(page);
  await expect(panel.getByLabel("계산 M0 기준일")).toHaveValue("");
  await expect(panel.getByLabel("토지계약 분석용 사건 날짜")).toHaveValue("");
  await expect(panel.getByLabel("표에 없는 월은 분양수금 0원으로 가정합니다")).not.toBeChecked();
  await panel.getByLabel("계산 M0 기준일").fill("2026-01-01");
  await panel.getByLabel("시간·수금 가정 이유").fill("기준월만 먼저 기록하고 실제 사건 날짜는 추가 확인");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(posts[0]?.terms).toEqual({ cashFlowTiming: {
    schemaVersion: "cash-flow-timing-assumptions-v1", sourceBasisHash: source.sourceBasisHash,
    baseDate: "2026-01-01", firstMonthlyMonthIndex: null, unlistedMonthsAreZero: false,
    eventBindings: [{ sourcePeriodIndex: 0, plannedDate: null, monthIndex: null }], collections: [],
  } });
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel.getByLabel("계산 M0 기준일")).toHaveValue("2026-01-01");
  await expect(panel.getByLabel("토지계약 분석용 사건 날짜")).toHaveValue("");
  await expect(panel.getByLabel("토지계약 계산 월차")).toHaveValue("");
  await expect(panel.getByText("기존 예산 현금흐름은 계속 검토할 수 있습니다.", { exact: false })).toBeVisible();
});

test("saved explicit timing reaches a new monthly request and displays the captured basis after reopening", async ({ page }) => {
  const { panel, posts, calculations } = await fixture(page);
  await complete(page);
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel.getByLabel("수금 1 잔금", { exact: true })).toHaveValue("30000000000");
  await expect(panel.getByLabel("토지계약 분석용 사건 날짜")).toHaveValue("2026-01-15");
  expect(posts).toHaveLength(1);
  await page.goto(`/project/${projectId}/feasibility`, { waitUntil: "domcontentloaded" });
  const monthly = page.getByRole("region", { name: "금융 검토안 계산과 비교", exact: true });
  await expect(monthly.getByLabel("월별 금융 계산 방식")).toBeEnabled();
  await monthly.getByLabel("월별 금융 계산 방식").selectOption("reviewed_monthly_v1");
  await monthly.getByLabel("월별 금융 검토 이유").fill("저장한 시간 가정의 새 계산");
  await monthly.getByRole("button", { name: "월별 금융 검토안 계산·저장", exact: true }).click();
  await expect(monthly.getByRole("status").filter({ hasText: "계산·저장했습니다" })).toBeVisible();
  expect(calculations).toHaveLength(1);
  expect(calculations[0]?.reviewedMonthlyFinancing).toMatchObject({ expectedReviewHash: "timing-review-1" });
  await page.reload({ waitUntil: "domcontentloaded" });
  const saved = monthly.getByRole("region", { name: "저장 계산의 기간·이자 기준", exact: true });
  await expect(saved.getByText("사용자 분석용 시간·수금 가정", { exact: true })).toBeVisible();
  await expect(saved).toContainText("M0 기준일 2026-01-01 · 원본 첫 월 → M1");
  await expect(saved).toContainText("실제 날짜와 약정 확인 전의 명시적 합성 분석 가정");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("duplicate collection months are rejected before saving any other timing edit", async ({ page }) => {
  const { panel, posts } = await fixture(page);
  await complete(page);
  await panel.getByRole("button", { name: "월별 수금 추가", exact: true }).click();
  for (const label of ["월차", "계약금", "중도금", "잔금"]) {
    await panel.getByLabel(`수금 2 ${label}`, { exact: true }).fill(label === "월차" ? "2" : "0");
  }
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("같은 월차의 수금은 한 행에 합쳐 입력하세요.");
  expect(posts).toHaveLength(0);
  await expect(panel.getByLabel("수금 1 잔금", { exact: true })).toHaveValue("30000000000");
});

test("a concurrent save conflict preserves the timing dates, collections and reason", async ({ page }) => {
  const { panel, posts } = await fixture(page, { saveStatus: 409 });
  await complete(page);
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("저장된 입력이 변경되었습니다.");
  await expect(panel.getByLabel("토지계약 분석용 사건 날짜")).toHaveValue("2026-01-15");
  await expect(panel.getByLabel("수금 1 잔금", { exact: true })).toHaveValue("30000000000");
  await expect(panel.getByLabel("시간·수금 가정 이유")).toHaveValue("실제 날짜와 약정 확인 전의 명시적 합성 분석 가정");
  expect(posts).toHaveLength(1);
});
