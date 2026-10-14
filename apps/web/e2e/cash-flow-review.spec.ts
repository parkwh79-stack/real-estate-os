import { expect, test, type Page } from "@playwright/test";
import { bindCashflowDependents, type CashflowResult } from "../lib/project-client";

test.setTimeout(90_000);

const policy = { method: "reviewed_monthly_v1", interestBasis: "opening_balance", annualDayBasis: "monthly_12",
  feeBasis: "committed_limit_first_draw", maturityBasis: "project_month_zero", repaymentSource: "repayment_reserve",
  costTreatment: "replace_budget_finance_cost", overdueInterestBasis: "contract_rate_simple", revolving: "non_revolving", rounding: "unrounded_analysis" };
const period = { baseMonth: "2026-01", lastMonthIndex: 2 };
const baselineRun = { calculationRunId: "base-run", label: "기준", createdAt: "2026-10-14T01:00:00Z",
  policyMode: "budget_finance_allocation", period };
const changedRun = { calculationRunId: "changed-run", label: "변경", createdAt: "2026-10-14T01:01:00Z",
  policyMode: "reviewed_monthly_v1", period };

function result(monthly: boolean, missingMonthlyValues = false) {
  const won = 100_000_000;
  const income = monthly ? [102, 1.02, 300] : [100, 9, 300];
  const cost = monthly ? [102, 1.02, 1.0302] : [100, 9, 0];
  const beforePf = monthly ? [-102, -1.02, 298.9698] : [-100, -9, 300];
  const cumulativeBeforePf = monthly ? [-102, -103.02, 195.9498] : [-100, -109, 191];
  return { analysisType: "realytics_cash_flow_engine_v1", status: "completed", dataStatus: "partial",
    summary: { totalRevenue: 300 * won, totalProjectCost: (monthly ? 104.0502 : 109) * won,
      developmentProfit: (monthly ? 195.9498 : 191) * won,
      financialCost: (monthly ? 4.0502 : 9) * won, pfLimit: 200 * won,
      maxMinus: (monthly ? -103.02 : -109) * won, maxMinusMonth: 1 },
    sourceMetadata: { analysisOnly: true, reportEligible: false },
    ...(monthly ? { monthlyFinancing: { budgetFinancialCost: 9 * won, calculatedFinancialCost: 4.0502 * won } } : {}),
    monthlyCashFlow: [0, 1, 2].map((monthIndex) => ({ monthIndex, period: `M${monthIndex}`,
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
  saveGate?: Promise<void>; missingMonthlyValues?: boolean } = {}) {
  let calculated = options.initialTwo ?? false;
  let saved = false;
  const calculations: Record<string, unknown>[] = [];
  const selections: Record<string, unknown>[] = [];
  const preview = () => ({ status: "partial", reasons: ["financing_policy_mismatch"],
    baseline: baselineRun, comparison: changedRun, sourceChanged: false,
    metrics: [{ key: "financialCost", label: "금융비용", unit: "KRW", baselineValue: 900_000_000,
      comparisonValue: 405_020_000, delta: -494_980_000 },
      { key: "outstandingDebt", label: "잔여 대출", unit: "KRW", baselineValue: null, comparisonValue: 0, delta: null }],
    inputChanges: [{ path: "monthlyFinancing", label: "금융 계산 방식", before: null, after: policy }], inputChangeCount: 1 });
  const state = (isPreview = false) => ({ comparisonHash: saved ? "saved-state" : "current-state",
    availableRuns: calculated ? [changedRun, baselineRun] : [baselineRun],
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
    else if (path.endsWith("/feasibility-workbook/review")) body = { reviewHash: "reviewed-input" };
    else if (path.endsWith("/calculation-runs")) {
      const engine = url.searchParams.get("engine_key");
      body = { records: engine === "cash_flow_engine" ? [{ calculationRunId: calculated ? "changed-run" : "base-run", engineKey: engine,
        inputSnapshot: { financialInputHash: calculated ? "new-hash" : "base-hash", ...(calculated ? { monthlyFinancing: policy } : {}) },
        resultSnapshot: result(calculated, options.missingMonthlyValues) }] : [{ calculationRunId: "stale-child", engineKey: engine,
        inputSnapshot: { parentCalculationRunId: "base-run", financialInputHash: "base-hash" },
        outputMetrics: [{ key: "waterfall.shortfall", value: 999999 }], resultSnapshot: { scenarios: [{ scenarioId: "이전 계산의 시나리오" }] } }] };
    } else if (path.endsWith("/cash-flow/run")) {
      calculations.push(route.request().postDataJSON());
      if (options.runConflict) {
        await route.fulfill({ status: 409, json: { detail: "저장된 입력이 변경되었습니다. 최신 값을 다시 불러온 뒤 계산하세요." } }); return;
      }
      calculated = true; body = { ...result(true), calculationRunId: "changed-run" };
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
  return { panel, calculations, selections };
}

test("explicitly choose a monthly policy, calculate and reopen the saved comparison", async ({ page }) => {
  const { panel, calculations, selections } = await fixture(page);
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
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel.getByLabel("기준 계산")).toHaveValue("base-run");
  await expect(panel.getByLabel("변경 계산")).toHaveValue("changed-run");
  await expect(panel.getByText("금융비용", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "비교 선택 저장", exact: true })).toBeDisabled();
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
  await expect(repaidMonth.getByRole("cell")).toHaveText(["M2", "300", "104.1", "195.9", "195.9"]);
});

test("dependent records require both the same parent and exact input basis", () => {
  const cashflow: CashflowResult = {
    runId: "parent", source: "pf_review_orchestration", monthly: [],
    maxMinusEok: null, maxMinusMonth: null, summary: {},
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
