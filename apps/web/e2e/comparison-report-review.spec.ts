import { expect, test, type Page } from "@playwright/test";

test.setTimeout(90_000);

const comparison = {
  status: "partial", reasons: ["financing_policy_mismatch"],
  baseline: { calculationRunId: "base-1", period: { baseMonth: "2026-01", lastMonthIndex: 24 }, inputReviews: [
    { field: "total_revenue", label: "총매출", classification: "원본 대조 입력(최종 승인 아님)", reason: "원본 매출 셀 대조", sourceName: "검증자료.xlsx", sourceCell: "사업수지!D11" }] },
  comparison: { calculationRunId: "changed-1", period: { baseMonth: "2026-01", lastMonthIndex: 24 }, inputReviews: [
    { field: "total_revenue", label: "총매출", classification: "분석용 사용자 가정", reason: "매출 증가 분석 가정" }] },
  metrics: [{ key: "financialCost", label: "금융비용", unit: "KRW", baselineValue: 900_000_000, comparisonValue: 400_000_000, delta: -500_000_000 }],
  inputChanges: [{ path: "pfStructure[0].interestRate", label: "대출 연 금리", before: 0.06, after: 0.05 }],
};

async function fixture(page: Page, options: { conflict?: boolean; noSelection?: boolean; existing?: boolean; gate?: Promise<void>; exportConflict?: boolean } = {}) {
  const calls: unknown[] = [];
  const exports: unknown[] = [];
  const downloads: string[] = [];
  let exists = options.existing ?? false;
  let liveHash = "a".repeat(64);
  let liveComparison = structuredClone(comparison);
  const snapshot = {
    schemaVersion: "test-comparison-snapshot", projectId: "r3-1", capturedAt: "2026-10-14T03:00:00Z",
    selection: { baselineRunId: "base-1", comparisonRunId: "changed-1" }, comparison: structuredClone(comparison),
    baselineRun: { inputSnapshot: {} }, comparisonRun: { inputSnapshot: { reviewedMonthlyFinancingReview: { reason: "저장 당시 금리 검토" } } },
  };
  const metadata = { report_id: "comparison-report-1", project_id: "r3-1", run_id: null, version: 1,
    title: "현금흐름 비교 검토 초안", status: "draft", created_at: "2026-10-14T03:00:00Z",
    metadata: { complete: true, report_eligible: false, usage_restriction: "analysis_only_not_final_approval" } };
  const detail = { ...metadata, report_markdown: "# 현금흐름 비교 검토 초안\n저장 당시 금리 검토",
    report: { template: "cash-flow-comparison-review-v1", cashFlowComparisonSnapshot: snapshot,
      sectionOrder: [{ key: "summary", title: "요약" }, { key: "changes", title: "가정 변경" }, { key: "limits", title: "한계" }, { key: "sources", title: "출처" }] } };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = { authenticated: true,
      user: { userId: "reviewer", role: "admin", status: "active", tenantId: "r3" },
      session: { userId: "reviewer", tenantId: "r3" }, permissions: ["project:read", "project:write", "report:export"], authSource: "bearer_token" };
    else if (path.endsWith("/workspace/context")) body = { projectId: "r3-1", projectMemory: { projectName: "보고서 검토" }, pageData: {} };
    else if (path.endsWith("/report-templates")) body = { templates: [] };
    else if (path.endsWith("/orchestrations")) body = [];
    else if (path.endsWith("/deal-state-records")) body = { records: [] };
    else if (path.endsWith("/cash-flow/comparison")) body = { comparisonHash: liveHash, availableRuns: [],
      selection: options.noSelection ? null : { ...snapshot.selection, saved: true }, comparison: options.noSelection ? null : liveComparison };
    else if (path.endsWith("/reports/cash-flow-comparison")) {
      calls.push(route.request().postDataJSON()); await options.gate;
      if (options.conflict) {
        await route.fulfill({ status: 409, json: { detail: { error: "comparison_state_conflict", message: "저장된 비교가 변경되었습니다. 다시 불러온 뒤 생성하세요." } } }); return;
      }
      exists = true; body = detail;
    } else if (path.endsWith("/reports/comparison-report-1/exports")) {
      exports.push(route.request().postDataJSON());
      if (options.exportConflict) {
        await route.fulfill({ status: 409, json: { detail: "저장된 비교 검토 초안의 원본·본문 검증에 실패했습니다." } }); return;
      }
      body = { export_id: "frozen-export-1" };
    } else if (path.endsWith("/exports/frozen-export-1/download")) {
      downloads.push(path);
      await route.fulfill({ contentType: "application/octet-stream", body: "frozen comparison export" }); return;
    } else if (path.endsWith("/reports/comparison-report-1")) body = detail;
    else if (path.endsWith("/reports")) body = exists ? [metadata] : [];
    else if (path.endsWith("/projects/r3-1")) body = { project: { name: "보고서 검토", type: "mixed_use" } };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/r3-1/report", { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("region", { name: "저장된 비교와 검토 초안", exact: true });
  await expect(panel.getByRole("button", { name: "비교 다시 불러오기", exact: true })).toBeEnabled({ timeout: 60_000 });
  return { panel, calls, exports, downloads, changeLive: () => {
    liveHash = "b".repeat(64);
    liveComparison = { ...structuredClone(comparison), metrics: [{ ...comparison.metrics[0]!, comparisonValue: 990_000_000, delta: 90_000_000 }] };
  } };
}

test("save a comparison draft and reopen its fixed values after live inputs change", async ({ page }) => {
  const { panel, calls, changeLive } = await fixture(page);
  await panel.getByRole("button", { name: "저장된 비교로 초안 생성·저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("비교 검토 초안을 저장했습니다");
  expect(calls).toEqual([{ expectedComparisonHash: "a".repeat(64) }]);
  const captured = panel.getByLabel("초안에 고정된 비교", { exact: true });
  await expect(captured.getByRole("cell", { name: "400,000,000", exact: true })).toBeVisible();
  await expect(captured.getByText("변경안 검토 이유: 저장 당시 금리 검토", { exact: true })).toBeVisible();
  await expect(captured.getByText("총매출 · 원본 대조 입력(최종 승인 아님)", { exact: true })).toBeVisible();
  await expect(captured.getByText("총매출 · 분석용 사용자 가정", { exact: true })).toBeVisible();
  await expect(captured.getByText("검증자료.xlsx · 사업수지!D11", { exact: true })).toBeVisible();
  changeLive();
  await panel.getByRole("button", { name: "비교 다시 불러오기", exact: true }).click();
  await expect(captured.getByRole("cell", { name: "400,000,000", exact: true })).toBeVisible();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(captured.getByRole("cell", { name: "400,000,000", exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(captured.getByText("매출 증가 분석 가정", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "최종 발행", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("a stale comparison keeps the saved draft visible until an explicit refresh", async ({ page }) => {
  const { panel, calls } = await fixture(page, { conflict: true, existing: true });
  await panel.getByRole("button", { name: "저장된 비교로 초안 생성·저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("저장된 비교가 변경되었습니다");
  await expect(panel.getByLabel("초안에 고정된 비교").getByRole("cell", { name: "400,000,000", exact: true })).toBeVisible();
  expect(calls).toHaveLength(1);
});

test("without a saved selection the draft action stays unavailable", async ({ page }) => {
  const { panel, calls } = await fixture(page, { noSelection: true });
  await expect(panel.getByRole("button", { name: "저장된 비교로 초안 생성·저장", exact: true })).toBeDisabled();
  await expect(panel.getByText("사업성 화면에서 서로 다른 두 계산을 비교하고 선택을 저장하세요.", { exact: false })).toBeVisible();
  expect(calls).toHaveLength(0);
});

test("a pending creation accepts one request and disables both report actions", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const { panel, calls } = await fixture(page, { gate });
  const create = panel.getByRole("button", { name: "저장된 비교로 초안 생성·저장", exact: true });
  try {
    await create.dblclick();
    await expect.poll(() => calls.length).toBe(1);
    await expect(create).toBeDisabled();
    await expect(panel.getByRole("button", { name: "비교 다시 불러오기", exact: true })).toBeDisabled();
  } finally { release(); }
  await expect(panel.getByRole("status")).toContainText("비교 검토 초안을 저장했습니다");
});

for (const [label, format] of [["PDF", "pdf"], ["Word", "docx"]] as const) {
  test(`download the saved comparison ${label} without requiring a current selection`, async ({ page }) => {
    const { panel, exports, downloads, calls } = await fixture(page, { existing: true, noSelection: true });
    const downloaded = page.waitForEvent("download");
    await panel.getByRole("button", { name: `이 초안 ${label} 다운로드`, exact: true }).click();
    expect((await downloaded).suggestedFilename()).toBe(`cash-flow-comparison-review.${format}`);
    expect(exports).toEqual([{ format }]);
    expect(downloads).toEqual(["/api/backend/projects/r3-1/reports/comparison-report-1/exports/frozen-export-1/download"]);
    expect(calls).toHaveLength(0);
    await expect(panel.getByRole("status")).toContainText("선택한 초안에 고정된 비교를 내려받았습니다");
  });
}

test("a rejected export keeps the captured draft and does not download a file", async ({ page }) => {
  const { panel, downloads } = await fixture(page, { existing: true, exportConflict: true });
  await panel.getByRole("button", { name: "이 초안 PDF 다운로드", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("원본·본문 검증에 실패했습니다");
  await expect(panel.getByLabel("초안에 고정된 비교").getByRole("cell", { name: "400,000,000", exact: true })).toBeVisible();
  expect(downloads).toHaveLength(0);
});
