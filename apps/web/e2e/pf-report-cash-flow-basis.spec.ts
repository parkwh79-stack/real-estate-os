import { expect, test, type Page } from "@playwright/test";

test.setTimeout(90_000);

async function fixture(page: Page, failure = 0) {
  const posts: unknown[] = [];
  let hasReport = false;
  const run = { calculationRunId: "saved-cash-flow-original", label: "검토한 기준 계산", inputHash: "a".repeat(64),
    resultHash: "b".repeat(64), period: { baseMonth: "2026-01", lastMonthIndex: 2 }, policyMode: "reviewed_monthly_v1" };
  const metadata = { report_id: "frozen-pf", version: 1, title: "현금흐름 연결 PF 초안", status: "draft",
    created_at: "2026-10-15T01:00:00Z", metadata: { report_eligible: false } };
  const snapshot = { calculationRunId: run.calculationRunId, createdAt: "2026-10-14T00:00:00Z",
    inputSnapshot: { sourceMetadata: { reviewReason: "합성 검토 가정" } },
    resultSnapshot: { summary: { totalRevenue: 1000, totalProjectCost: 600, endingCash: 400 } } };
  const detail = { ...metadata, report_markdown: "# 저장된 현금흐름 · 1000원", report: { cashFlowCalculationSnapshot: snapshot,
    sections: { summaryCashFlow: { summary: snapshot.resultSnapshot.summary } } } };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/$/, "");
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = { authenticated: true,
      user: { userId: "reviewer", role: "admin", status: "active", tenantId: "synthetic" },
      session: { userId: "reviewer", tenantId: "synthetic" }, permissions: ["project:read", "report:export"] };
    else if (path.endsWith("/workspace/context")) body = { projectId: "basis", projectMemory: { projectName: "합성 검토" }, pageData: {} };
    else if (path.endsWith("/report-templates")) body = { templates: [] };
    else if (path.endsWith("/orchestrations")) body = [];
    else if (path.endsWith("/deal-state-records")) body = { records: [] };
    else if (path.endsWith("/cash-flow/comparison")) body = { availableRuns: [run], selection: null, comparisonHash: "c".repeat(64), comparison: null };
    else if (path.endsWith("/reports/generate")) {
      posts.push(route.request().postDataJSON());
      if (failure) return route.fulfill({ status: failure, json: { detail: "계산 기록을 다시 확인해 주세요." } });
      hasReport = true; body = detail;
    } else if (path.endsWith("/exports")) body = { export_id: "json-export" };
    else if (path.endsWith("/download")) body = { report: detail.report };
    else if (path.endsWith("/reports/frozen-pf")) body = detail;
    else if (path.endsWith("/reports")) body = hasReport ? [metadata] : [];
    else if (path.endsWith("/projects/basis")) body = { project: { name: "현재 수정된 프로젝트" }, feasibility_input: { total_revenue: 99999 } };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/basis/report", { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("region", { name: "저장된 비교와 검토 초안", exact: true });
  const selector = panel.getByLabel("보고서에 넣을 현금흐름 계산");
  await expect(selector).toBeEnabled({ timeout: 60_000 });
  return { panel, selector, posts, snapshot };
}

test("selected calculation is sent as references, saved, reopened and downloaded consistently", async ({ page }) => {
  const { panel, selector, posts, snapshot } = await fixture(page);
  const create = panel.getByRole("button", { name: "선택 계산으로 PF 초안 생성", exact: true });
  await expect(create).toBeDisabled();
  await selector.selectOption("saved-cash-flow-original");
  await create.click();
  const saved = page.locator('[aria-label="PF 초안에 고정된 현금흐름"]');
  await expect(saved).toContainText("saved-cash-flow-original");
  await expect(saved).toContainText("1,000원");
  await expect(saved).not.toContainText("99,999");
  expect(posts).toEqual([{ cashFlowBasis: { calculationRunId: "saved-cash-flow-original", inputHash: "a".repeat(64), resultHash: "b".repeat(64) } }]);
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(saved).toContainText("1,000원");
  await expect(saved).toContainText("다른 절까지 동일 실행으로 검증된 것은 아닙니다");
  const waiting = page.waitForEvent("download");
  await saved.getByRole("button", { name: "입력·결과 JSON 다운로드" }).click();
  const download = await waiting;
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(JSON.parse(Buffer.concat(chunks).toString()).report.cashFlowCalculationSnapshot).toEqual(snapshot);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

for (const status of [403, 409, 503]) {
  test(`creation failure ${status} preserves the selected calculation`, async ({ page }) => {
    const { panel, selector, posts } = await fixture(page, status);
    await selector.selectOption("saved-cash-flow-original");
    await panel.getByRole("button", { name: "선택 계산으로 PF 초안 생성", exact: true }).click();
    await expect(panel.getByRole("alert")).toBeVisible();
    await expect(selector).toHaveValue("saved-cash-flow-original");
    await expect(panel.getByRole("button", { name: "선택 계산으로 PF 초안 생성", exact: true })).toBeEnabled();
    expect(posts).toHaveLength(1);
    await expect(page.locator('[aria-label="PF 초안에 고정된 현금흐름"]')).toHaveCount(0);
  });
}

test("duplicate creation is blocked and a response after navigation is ignored", async ({ page }) => {
  const { panel, selector, posts } = await fixture(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/reports/generate", async (route) => {
    await pending;
    try { await route.fallback(); } catch { /* navigation aborted this request */ }
  });
  await selector.selectOption("saved-cash-flow-original");
  const create = panel.getByRole("button", { name: "선택 계산으로 PF 초안 생성", exact: true });
  await create.click();
  await expect(create).toBeDisabled();
  await expect(selector).toBeDisabled();
  await page.goto("/project/other/report", { waitUntil: "domcontentloaded" });
  release();
  await expect(page).toHaveURL(/\/project\/other\/report/);
  await expect(page.locator('[aria-label="PF 초안에 고정된 현금흐름"]')).toHaveCount(0);
  expect(posts.length).toBeLessThanOrEqual(1);
});
