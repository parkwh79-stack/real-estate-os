import { expect, test, type Page } from "@playwright/test";
import { bindCashflowDependents, type CashflowResult, type CashFlowPeriodBasis } from "../lib/project-client";

test.setTimeout(90_000);

const policy = { method: "reviewed_monthly_v1", interestBasis: "opening_balance", annualDayBasis: "monthly_12",
  feeBasis: "committed_limit_first_draw", maturityBasis: "project_month_zero", repaymentSource: "repayment_reserve",
  costTreatment: "replace_budget_finance_cost", overdueInterestBasis: "contract_rate_simple", revolving: "non_revolving", rounding: "unrounded_analysis" };
const period = { baseMonth: "2026-01", lastMonthIndex: 2 };
const baselineRun = { calculationRunId: "base-run", label: "기준", createdAt: "2026-10-14T01:00:00Z",
  policyMode: "budget_finance_allocation", period };
const changedRun = { calculationRunId: "changed-run", label: "변경", createdAt: "2026-10-14T01:01:00Z",
  policyMode: "reviewed_monthly_v1", period };
const calendarBasis: CashFlowPeriodBasis = { schemaVersion: "cash-flow-period-basis-v1", kind: "calendar_monthly",
  calculationPeriodCount: 3, firstCalculationPeriodIndex: 0, lastCalculationPeriodIndex: 2,
  interestCalculation: { status: "calculated", firstChargedMonthIndex: 1, lastChargedMonthIndex: 2,
    chargedPeriodCount: 2, method: "opening_balance_monthly_12" } };
const sourceBasis: CashFlowPeriodBasis = { schemaVersion: "cash-flow-period-basis-v1", kind: "source_sequence",
  periods: ["토지 잔금", "착공", "1개월", "2개월"].map((label, index) => ({ sourcePeriodIndex: index, label,
    periodKind: index < 2 ? "event" : "monthly", sourceMonthNumber: index < 2 ? null : index - 1,
    sourceLocator: { sourceName: "저장한 원본.xlsx", sheet: "현금흐름", cell: `${["E", "F", "G", "H"][index]}2`, monthCell: `${["E", "F", "G", "H"][index]}3` } })),
  scheduleBindings: { construction_start: { sourcePeriodIndex: 1, sourceMonthNumber: null },
    completion: { sourcePeriodIndex: null, sourceMonthNumber: null } },
  warnings: ["source_period_duration_unconfirmed"], calculationPeriodCount: 3,
  firstCalculationPeriodIndex: 0, lastCalculationPeriodIndex: 2,
  interestCalculation: { status: "not_calculated", reason: "source_period_duration_unconfirmed" } };

function result(monthly: boolean, missingMonthlyValues = false, periodBasis?: CashFlowPeriodBasis, firstRowMaximum = false) {
  const won = 100_000_000;
  const income = monthly ? [102, 1.02, 300] : [100, 9, 300];
  const cost = monthly ? [102, 1.02, 1.0302] : [100, 9, 0];
  const beforePf = monthly ? [-102, -1.02, 298.9698] : firstRowMaximum ? [-109, 9, 300] : [-100, -9, 300];
  const cumulativeBeforePf = beforePf.map((_, index) => beforePf.slice(0, index + 1).reduce((sum, value) => sum + value, 0));
  const maximumDeficit = Math.min(...cumulativeBeforePf);
  return { analysisType: "realytics_cash_flow_engine_v1", status: "completed", dataStatus: "partial",
    summary: { totalRevenue: 300 * won, totalProjectCost: (monthly ? 104.0502 : 109) * won,
      developmentProfit: (monthly ? 195.9498 : 191) * won,
      financialCost: (monthly ? 4.0502 : 9) * won, pfLimit: 200 * won,
      maxMinus: maximumDeficit * won, maxMinusMonth: cumulativeBeforePf.indexOf(maximumDeficit) + 1 },
    sourceMetadata: { analysisOnly: true, reportEligible: false },
    ...(periodBasis ? { periodBasis } : {}),
    ...(monthly ? { monthlyFinancing: { budgetFinancialCost: 9 * won, calculatedFinancialCost: 4.0502 * won } } : {}),
    monthlyCashFlow: [0, 1, 2].map((monthIndex) => ({ monthIndex, period: periodBasis?.kind === "source_sequence"
      ? `원본 ${monthIndex + 1}: ${periodBasis.periods?.[monthIndex]?.label ?? "미기록"}` : `M${monthIndex}`,
      cashIn: { totalCashIn: income[monthIndex]! * won }, cashOut: { totalCashOut: cost[monthIndex]! * won },
      netCashFlowBeforePfDraw: beforePf[monthIndex]! * won, cumulativeCashFlowBeforePfDraw: cumulativeBeforePf[monthIndex]! * won,
      ...(monthly ? { netCashFlow: missingMonthlyValues ? null : (monthIndex === 2 ? 195.9498 : 0) * won,
        endingCash: missingMonthlyValues ? null : (monthIndex === 2 ? 195.9498 : 0) * won,
        monthlyFinancing: { interest: [0, 1.02, 1.0302][monthIndex]! * won, fees: (monthIndex === 0 ? 2 : 0) * won,
          principalRepayment: missingMonthlyValues ? null : (monthIndex === 2 ? 103.02 : 0) * won,
          outstandingDebt: [102, 103.02, 0][monthIndex]! * won,
          overdueDebt: 0, unfundedOperatingCost: (monthIndex === 2 ? 1.0302 : 0) * won } } : {}),
    })) };
}

async function fixture(page: Page, options: { runConflict?: boolean; saveConflict?: boolean; initialTwo?: boolean;
  saveGate?: Promise<void>; missingMonthlyValues?: boolean; periodBasis?: CashFlowPeriodBasis;
  firstRowMaximum?: boolean; maximumRowValue?: unknown; budgetError?: string | Record<string, unknown>; budgetPartial?: boolean } = {}) {
  let calculated = options.initialTwo ?? false;
  let saved = false;
  let workingStart = "2026-01-01";
  const sourceSequence = options.periodBasis?.kind === "source_sequence";
  const frozenPeriod = sourceSequence ? { ...period, kind: "source_sequence", baseMonth: null } : period;
  const baseRun = { ...baselineRun, period: frozenPeriod };
  const nextRun = { ...changedRun, period: frozenPeriod, ...(sourceSequence ? { policyMode: "budget_finance_allocation" } : {}) };
  const calculations: Record<string, unknown>[] = [];
  const selections: Record<string, unknown>[] = [];
  const budgetRuns: Record<string, unknown>[] = [];
  const legacyPodRuns: Record<string, unknown>[] = [];
  let calculationReads = 0;
  const storedResult = () => {
    const value = result(calculated && !sourceSequence, options.missingMonthlyValues, options.periodBasis, options.firstRowMaximum);
    return "maximumRowValue" in options ? { ...value, summary: { ...value.summary, maxMinusMonth: options.maximumRowValue } } : value;
  };
  const preview = () => ({ status: "partial", reasons: sourceSequence ? [] : ["financing_policy_mismatch"],
    baseline: baseRun, comparison: nextRun, sourceChanged: false,
    metrics: [{ key: "financialCost", label: "금융비용", unit: "KRW", baselineValue: 900_000_000,
      comparisonValue: 405_020_000, delta: -494_980_000 },
      { key: "outstandingDebt", label: "잔여 대출", unit: "KRW", baselineValue: null, comparisonValue: 0, delta: null }],
    inputChanges: [{ path: "monthlyFinancing", label: "금융 계산 방식", before: null, after: policy }], inputChangeCount: 1 });
  const state = (isPreview = false) => ({ comparisonHash: saved ? "saved-state" : "current-state",
    availableRuns: calculated ? [nextRun, baseRun] : [baseRun],
    selection: saved || isPreview ? { baselineRunId: "base-run", comparisonRunId: "changed-run", saved: !isPreview } : null,
    comparison: saved || isPreview ? preview() : null });
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    let body: unknown = {};
    if (path.endsWith("/auth/me")) {
      body = { authenticated: true, user: { userId: "reviewer", role: "admin", status: "active", tenantId: "r2" },
        session: { userId: "reviewer", tenantId: "r2" }, permissions: ["project:read", "project:write", "assumption:override"], authSource: "bearer_token" };
    } else if (path.endsWith("/workspace/context")) body = { projectId: "r2-1", projectMemory: { projectName: "R2 검토" }, pageData: {} };
    else if (path.endsWith("/orchestrations")) body = [];
    else if (path.endsWith("/feasibility-workbook/review")) body = { reviewHash: "reviewed-input", terms: { schedule: { construction_start: workingStart } } };
    else if (path.endsWith("/calculation-runs")) {
      calculationReads += 1;
      const engine = url.searchParams.get("engine_key");
      body = { records: engine === "cash_flow_engine" ? [{ calculationRunId: calculated ? "changed-run" : "base-run", engineKey: engine,
        inputSnapshot: { financialInputHash: calculated ? "new-hash" : "base-hash", ...(calculated && !sourceSequence ? { monthlyFinancing: policy } : {}) },
        resultSnapshot: storedResult() }] : [{ calculationRunId: "stale-child", engineKey: engine,
        inputSnapshot: { parentCalculationRunId: "base-run", financialInputHash: "base-hash" },
        outputMetrics: [{ key: "waterfall.shortfall", value: 999999 }], resultSnapshot: { scenarios: [{ scenarioId: "이전 계산의 시나리오" }] } }] };
    } else if (path.endsWith("/cash-flow/run")) {
      const requestBody = route.request().postDataJSON() as Record<string, unknown>;
      if (Object.keys(requestBody).length === 0) {
        budgetRuns.push(requestBody);
        await route.fulfill(options.budgetError
          ? { status: 422, json: { detail: options.budgetError } }
          : { status: 200, json: { ...result(false, false, options.periodBasis), calculationRunId: "base-run",
            dependentExecutionStatus: options.budgetPartial ? "partial" : "completed" } });
        return;
      }
      calculations.push(requestBody);
      if (sourceSequence) {
        await route.fulfill({ status: 400, json: { detail: "원본 사건·기간 순서를 실제 달력과 대응하지 못했습니다. 착공·준공 및 사전 사건의 시간 대응을 확인한 뒤 월별 금융 검토를 계산하세요." } }); return;
      }
      if (options.runConflict) {
        await route.fulfill({ status: 409, json: { detail: "저장된 입력이 변경되었습니다. 최신 값을 다시 불러온 뒤 계산하세요." } }); return;
      }
      calculated = true; body = { ...result(true, false, options.periodBasis), calculationRunId: "changed-run" };
    } else if (path.endsWith("/chief-orchestration/run-pod")) {
      const requestBody = route.request().postDataJSON();
      budgetRuns.push(requestBody);
      legacyPodRuns.push(requestBody);
      body = { status: "completed" };
    } else if (path.endsWith("/cash-flow/comparison")) {
      if (route.request().method() === "POST") {
        selections.push(route.request().postDataJSON());
        await options.saveGate;
        if (options.saveConflict) {
          await route.fulfill({ status: 409, json: { detail: { error: "comparison_state_conflict", message: "프로젝트 또는 계산 기록이 변경되었습니다. 다시 불러온 뒤 저장해 주세요." } } }); return;
        }
        saved = true;
      }
      body = state(url.searchParams.has("baselineRunId"));
    }
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/r2-1/feasibility", { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("region", { name: "금융 검토안 계산과 비교", exact: true });
  await expect(panel.getByLabel("월별 금융 계산 방식")).toBeEnabled({ timeout: 60_000 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth))
    .toBeLessThanOrEqual(page.viewportSize()!.width);
  return { panel, calculations, selections, budgetRuns, legacyPodRuns, calculationReads: () => calculationReads, changeWorkingDates: () => { workingStart = "2099-12-01"; } };
}

test("explicitly choose a monthly policy, calculate and reopen the saved comparison", async ({ page }) => {
  const { panel, calculations, selections, changeWorkingDates } = await fixture(page, { periodBasis: calendarBasis });
  await panel.getByRole("button", { name: "월별 금융 검토안 계산·저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("계산 방식을 선택하세요");
  expect(calculations).toHaveLength(0);
  await panel.getByLabel("월별 금융 계산 방식").selectOption("reviewed_monthly_v1");
  await panel.getByLabel("월별 금융 검토 이유").fill("저장한 금리와 만기로 검토");
  await panel.getByRole("button", { name: "월별 금융 검토안 계산·저장", exact: true }).click();
  await expect(panel.getByRole("status").filter({ hasText: "계산·저장했습니다" })).toBeVisible();
  expect(calculations).toEqual([{ reviewedMonthlyFinancing: { policy, reason: "저장한 금리와 만기로 검토", expectedReviewHash: "reviewed-input" } }]);
  await expect(panel.getByText("최근 저장된 월별 금융 원장 · 분석용", { exact: true })).toBeVisible();
  await expect(panel.getByLabel("기준 계산")).toHaveValue("base-run");
  await expect(panel.getByLabel("변경 계산")).toHaveValue("changed-run");
  await panel.getByRole("button", { name: "차이 확인", exact: true }).click();
  await expect(panel.getByText("금융비용", { exact: true })).toBeVisible();
  await expect(panel.getByText("금융비용 계산 방식이 다른 두 안입니다.", { exact: true })).toBeVisible();
  await expect(panel.getByText("미확인", { exact: true }).first()).toBeVisible();
  await panel.getByRole("button", { name: "비교 선택 저장", exact: true }).click();
  await expect(panel.getByRole("status").filter({ hasText: "비교 선택을 저장했습니다" })).toBeVisible();
  expect(selections).toEqual([{ baselineRunId: "base-run", comparisonRunId: "changed-run", expectedComparisonHash: "current-state" }]);
  const savedBasis = panel.getByRole("region", { name: "저장 계산의 기간·이자 기준", exact: true });
  await expect(savedBasis).toContainText("M1 ~ M2");
  await expect(savedBasis).toContainText("2개 계산 행");
  changeWorkingDates();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel.getByLabel("기준 계산")).toHaveValue("base-run");
  await expect(panel.getByLabel("변경 계산")).toHaveValue("changed-run");
  await expect(panel.getByText("금융비용", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "비교 선택 저장", exact: true })).toBeDisabled();
  await expect(savedBasis).toContainText("계산 기록: changed-run");
  await expect(savedBasis).toContainText("M1 ~ M2");
  await expect(savedBasis).not.toContainText("2099");
});

test("a legacy saved run shows missing period basis instead of deriving it from current dates", async ({ page }) => {
  const { panel, changeWorkingDates } = await fixture(page);
  const savedBasis = panel.getByRole("region", { name: "저장 계산의 기간·이자 기준", exact: true });
  await expect(savedBasis).toContainText("기간 기준이 미기록");
  changeWorkingDates();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(savedBasis).toContainText("기간 기준이 미기록");
  await expect(savedBasis).not.toContainText("2099");
  await expect(savedBasis.getByRole("link", { name: "저장 입력·원본 자료 확인" })).toHaveAttribute("href", "/project/r2-1/input-package");
});

test("source events stay distinct from months across comparison, rejection and budget rerun", async ({ page }) => {
  const { panel, calculations, budgetRuns, legacyPodRuns, changeWorkingDates } = await fixture(page, { periodBasis: sourceBasis, initialTwo: true });
  const savedBasis = panel.getByRole("region", { name: "저장 계산의 기간·이자 기준", exact: true });
  await expect(savedBasis).toContainText("4개 · 사전 사건 2개");
  await expect(savedBasis).toContainText("실제 계산 행3개");
  await expect(savedBasis).toContainText("원본 1: 토지 잔금 ~ 원본 3: 1개월");
  await expect(savedBasis).toContainText("달력 대응과 실제 이자기간은 미확인");
  await expect(savedBasis).toContainText("실제 월별 이자는 계산하지 않았습니다");
  await expect(page.getByRole("heading", { name: "원본 순서별 Cash Flow", exact: true })).toBeVisible();
  await expect(page.getByText("최근 저장 결과의 최초 18개 계산 행", { exact: false })).toBeVisible();
  await expect(page.getByRole("link", { name: "저장된 현금흐름 전체 보기", exact: true })).toHaveAttribute("href", "/project/r2-1/cashflow");
  await savedBasis.getByText("원본 사건·기간과 출처 확인", { exact: true }).click();
  await expect(savedBasis).toContainText("현금흐름!F2");
  await expect(savedBasis).toContainText("1번째 월");
  await panel.getByLabel("기준 계산").selectOption("base-run");
  await panel.getByLabel("변경 계산").selectOption("changed-run");
  await panel.getByRole("button", { name: "차이 확인", exact: true }).click();
  await expect(panel).toContainText("원본 순서 1~3 · 달력·이자기간 미확인");
  await panel.getByRole("button", { name: "비교 선택 저장", exact: true }).click();
  changeWorkingDates();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(savedBasis).toContainText("원본 1: 토지 잔금 ~ 원본 3: 1개월");
  await expect(savedBasis).not.toContainText("2099");
  await panel.getByLabel("월별 금융 계산 방식").selectOption("reviewed_monthly_v1");
  await panel.getByLabel("월별 금융 검토 이유").fill("사건 시간 대응 확인 전 검토");
  await panel.getByRole("button", { name: "월별 금융 검토안 계산·저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("사전 사건의 시간 대응을 확인");
  await expect(panel.getByLabel("월별 금융 검토 이유")).toHaveValue("사건 시간 대응 확인 전 검토");
  expect(calculations).toHaveLength(1);
  await page.goto("/project/r2-1/cashflow", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "원본 순서별 현금흐름 및 Max Minus", exact: true })).toBeVisible();
  const cashTable = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Cash-In", exact: true }) });
  await expect(cashTable.getByRole("cell", { name: "원본 1: 토지 잔금", exact: true })).toBeVisible();
  await expect(cashTable.getByRole("cell", { name: "M0", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Cash Flow 재실행", exact: true }).click();
  await expect.poll(() => budgetRuns.length).toBe(1);
  expect(budgetRuns[0]).toEqual({});
  expect(legacyPodRuns).toHaveLength(0);
  await expect(page.getByRole("status").filter({ hasText: "Cash Flow 계산을 저장했습니다" })).toBeVisible();
  await expect(page.getByRole("region", { name: "저장 계산의 기간·이자 기준", exact: true })).toContainText("원본 사건·기간 순서 기준");
});

for (const firstRowMaximum of [true, false]) {
  test(`one-based maximum-deficit row ${firstRowMaximum ? 1 : 2} labels and highlights the same source event`, async ({ page }) => {
    const index = firstRowMaximum ? 0 : 1;
    const label = firstRowMaximum ? "원본 1: 토지 잔금" : "원본 2: 착공";
    await fixture(page, { periodBasis: sourceBasis, firstRowMaximum });
    const metric = page.locator('[data-metric-key="cashflow.max_minus"]');
    await expect(metric.getByText(label, { exact: true })).toBeVisible();
    await page.getByRole("link", { name: "저장된 현금흐름 전체 보기", exact: true }).click();
    await expect(page.getByText(`${label} 발생`, { exact: true })).toBeVisible();
    const mainTable = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Cash-In", exact: true }) });
    await expect(mainTable.locator("tbody tr").nth(index)).toHaveClass(/bg-rose-50/);
    await expect(mainTable.locator("tbody tr").nth(index === 0 ? 1 : 0)).not.toHaveClass(/bg-rose-50/);
    await page.goto("/cashflow-detail?projectId=r2-1", { waitUntil: "domcontentloaded" });
    await expect(page.getByText(`${label} 시점`, { exact: true })).toBeVisible();
    const legacyTable = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "현금유입", exact: true }) });
    await expect(legacyTable.locator("tbody tr").nth(index)).toHaveClass(/bg-rose-50/);
    await expect(legacyTable.locator("tbody tr").nth(index === 0 ? 1 : 0)).not.toHaveClass(/bg-rose-50/);
  });
}

test("missing, zero, malformed and out-of-range maximum-deficit positions remain unknown", async ({ page }) => {
  for (const maximumRowValue of [null, 0, 1.5, "2", 4]) {
    await fixture(page, { periodBasis: sourceBasis, maximumRowValue });
    await expect(page.locator('[data-metric-key="cashflow.max_minus"]').getByText("발생 위치 미확인", { exact: true })).toBeVisible();
    await page.getByRole("link", { name: "저장된 현금흐름 전체 보기", exact: true }).click();
    await expect(page.getByText("발생 위치 미확인", { exact: true })).toBeVisible();
    const table = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "Cash-In", exact: true }) });
    await expect(table.locator("tbody tr[class*='bg-rose-50']")).toHaveCount(0);
  }
});

test("a changed input hash keeps the chosen calculation policy and reason", async ({ page }) => {
  const { panel, calculations } = await fixture(page, { runConflict: true });
  await panel.getByLabel("월별 금융 계산 방식").selectOption("reviewed_monthly_v1");
  await panel.getByLabel("월별 금융 검토 이유").fill("충돌 후에도 유지할 검토 이유");
  await panel.getByRole("button", { name: "월별 금융 검토안 계산·저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("저장된 입력이 변경되었습니다");
  await expect(panel.getByLabel("월별 금융 계산 방식")).toHaveValue("reviewed_monthly_v1");
  await expect(panel.getByLabel("월별 금융 검토 이유")).toHaveValue("충돌 후에도 유지할 검토 이유");
  expect(calculations).toHaveLength(1);
});

test("a conflicting comparison save retains the selected runs and preview", async ({ page }) => {
  const { panel, selections } = await fixture(page, { saveConflict: true, initialTwo: true });
  await panel.getByLabel("기준 계산").selectOption("base-run");
  await panel.getByLabel("변경 계산").selectOption("changed-run");
  await panel.getByRole("button", { name: "차이 확인", exact: true }).click();
  await panel.getByRole("button", { name: "비교 선택 저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("프로젝트 또는 계산 기록이 변경되었습니다");
  await expect(panel.getByLabel("기준 계산")).toHaveValue("base-run");
  await expect(panel.getByLabel("변경 계산")).toHaveValue("changed-run");
  await expect(panel.getByText("금융비용", { exact: true })).toBeVisible();
  expect(selections).toHaveLength(1);
});

test("a pending comparison save blocks duplicate actions", async ({ page }) => {
  let release!: () => void;
  const saveGate = new Promise<void>((resolve) => { release = resolve; });
  const { panel, selections } = await fixture(page, { initialTwo: true, saveGate });
  await panel.getByLabel("기준 계산").selectOption("base-run");
  await panel.getByLabel("변경 계산").selectOption("changed-run");
  const save = panel.getByRole("button", { name: "비교 선택 저장", exact: true });
  try {
    await save.dblclick();
    await expect.poll(() => selections.length).toBe(1);
    await expect(save).toBeDisabled();
    await expect(panel.getByLabel("기준 계산")).toBeDisabled();
  } finally { release(); }
  await expect(panel.getByRole("status").filter({ hasText: "비교 선택을 저장했습니다" })).toBeVisible();
});

test("the cashflow screen never shows old one-shot scenarios beside a monthly review", async ({ page }) => {
  await fixture(page, { initialTwo: true });
  await page.goto("/project/r2-1/cashflow", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("월별 금융 검토안은 별도 상환 기준을 사용합니다.", { exact: false })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("이전 계산의 시나리오", { exact: true })).toHaveCount(0);
  const cashTable = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "지출 (원금상환 포함)", exact: true }) });
  await expect(cashTable.getByRole("columnheader", { name: "수입 (PF 포함)", exact: true })).toBeVisible();
  await expect(cashTable.getByRole("columnheader", { name: "월말 계정합계", exact: true })).toBeVisible();
  await expect(page.getByText("PF 인출 포함 수입 − 사업·금융비와 원금상환 포함 지출 = 순현금흐름.", { exact: false })).toBeVisible();
  const firstMonth = cashTable.getByRole("row").filter({ has: page.getByRole("cell", { name: "M0", exact: true }) });
  await expect(firstMonth.getByRole("cell")).toHaveText(["M0", "102", "102", "0", "0"]);
  const repaidMonth = cashTable.getByRole("row").filter({ has: page.getByRole("cell", { name: "M2", exact: true }) });
  await expect(repaidMonth.getByRole("cell")).toHaveText(["M2", "300", "104", "196", "196"]);
  await expect(page.getByText("M1 발생", { exact: true })).toBeVisible();
  const maximumMonth = cashTable.getByRole("row").filter({ has: page.getByRole("cell", { name: "M1", exact: true }) });
  await expect(maximumMonth).toHaveClass(/bg-rose-50/);
  await expect(repaidMonth).not.toHaveClass(/bg-rose-50/);
});

test("dependent records require both the same parent and exact input basis", () => {
  const cashflow: CashflowResult = {
    runId: "parent", source: "pf_review_orchestration", monthly: [],
    maxMinusEok: null, maxMinusMonth: null, maxMinusPeriodIndex: null, summary: {},
    inputSnapshot: { financialInputHash: "same" },
  };
  const child = { calculationRunId: "child", engineKey: "waterfall", inputSnapshot: { parentCalculationRunId: "parent", financialInputHash: "same" } };
  expect(bindCashflowDependents(cashflow, { waterfall: child }).waterfall).toBe(child);
  expect(bindCashflowDependents(cashflow, { waterfall: { ...child, inputSnapshot: { ...child.inputSnapshot, financialInputHash: "different" } } }).waterfall).toBeNull();
  expect(bindCashflowDependents({ ...cashflow, runId: "new-parent" }, { waterfall: child }).waterfall).toBeNull();
  expect(bindCashflowDependents({ ...cashflow, inputSnapshot: { monthlyFinancing: policy, financialInputHash: "same" } }, { waterfall: child }).waterfall).toBeNull();
});

test("missing monthly repayment and account values remain unknown without legacy fallback", async ({ page }) => {
  await fixture(page, { initialTwo: true, missingMonthlyValues: true });
  await page.goto("/project/r2-1/cashflow", { waitUntil: "domcontentloaded" });
  const cashTable = page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: "지출 (원금상환 포함)", exact: true }) });
  const firstMonth = cashTable.getByRole("row").filter({ has: page.getByRole("cell", { name: "M0", exact: true }) });
  await expect(firstMonth.getByRole("cell")).toHaveText(["M0", "102", "미확인", "미확인", "미확인"], { timeout: 30_000 });
});


test("Cash Flow recalculation saves through the deterministic route and reloads its records", async ({ page }) => {
  const state = await fixture(page, { budgetPartial: true });
  const readsBefore = state.calculationReads();
  await page.getByRole("button", { name: "Cash Flow 재계산", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Cash Flow 계산을 저장했습니다" }))
    .toContainText("일부 후속 시나리오는 완료되지 않았으므로");
  expect(state.budgetRuns).toEqual([{}]);
  expect(state.legacyPodRuns).toHaveLength(0);
  expect(state.calculations).toHaveLength(0);
  expect(state.calculationReads()).toBeGreaterThan(readsBefore);
  await expect(page.getByRole("button", { name: "Cash Flow 재계산", exact: true })).toBeEnabled();
});

for (const detail of ["대출 금액이 미입력되었습니다.", { error: "invalid_financial_input", message: "대출 금액과 사업기간을 확인해 주세요." }]) {
  test(`Cash Flow recalculation explains ${typeof detail} errors and retains saved results`, async ({ page }) => {
    const state = await fixture(page, { budgetError: detail });
    const readsBefore = state.calculationReads();
    await page.getByRole("button", { name: "Cash Flow 재계산", exact: true }).click();
    await expect(page.getByText(typeof detail === "string" ? detail : detail.message, { exact: false })).toBeVisible();
    expect(state.budgetRuns).toEqual([{}]);
    expect(state.legacyPodRuns).toHaveLength(0);
    expect(state.calculationReads()).toBe(readsBefore);
    await expect(page.getByRole("status").filter({ hasText: "Cash Flow 계산을 저장했습니다" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Cash Flow 재계산", exact: true })).toBeEnabled();
  });
}
