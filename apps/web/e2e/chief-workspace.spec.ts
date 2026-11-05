import { expect, test, type Page } from "@playwright/test";

const basePlan = {
  schemaVersion: "realytics-chief-deal-orchestration-v1",
  projectId: "deal-workspace",
  preflightInputRevision: "1".repeat(64),
  sharedDealStateGraph: {},
  agentPods: [],
  podRuns: [
    { podKey: "land_legal", status: "planned", progressPercent: 0 },
    { podKey: "market_product", status: "planned", progressPercent: 0 },
    { podKey: "feasibility_cashflow", status: "planned", progressPercent: 0 },
  ],
  taskGraph: { workflowTrack: "development", recommendedRunOrder: ["land_legal", "market_product", "feasibility_cashflow"] },
};

async function mockDealRoom(page: Page, blocked: boolean) {
  let runRequests = 0;
  let preflightRequests = 0;
  let formalCompleted = false;
  const requested: string[] = [];
  const plan = blocked ? {
    ...basePlan,
    sharedDealStateGraph: { missingInputRegister: [
      { itemId: "regulation_data_missing", category: "land", description: "토지이용계획 근거가 필요합니다.", severity: "high", requiredFor: ["land_legal"] },
      { itemId: "source_documents_missing", category: "intake", description: "원본 심사자료를 올려 주세요.", severity: "blocking", requiredFor: ["all_pods"], acceptableSources: ["사업계획서", "사업수지 Excel"] },
    ] },
    runAllSummary: {
      requestedPodCount: 3, completedCount: 0, blockedCount: 3, status: "completed_with_blockers",
      rows: basePlan.podRuns.map((row) => ({ ...row, status: "blocked", error: "pod_input_validation_failed" })),
    },
  } : basePlan;

  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    requested.push(path);
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = { authenticated: true, user: { userId: "analyst", tenantId: "tenant-a", role: "analyst" }, permissions: ["project:read", "project:analyze"] };
    if (path.endsWith("/workspace/context")) body = { projectId: "deal-workspace", projectMemory: { projectName: "개발 사업 예시" }, pageData: {} };
    if (path.endsWith("/chief-orchestration/plan")) body = formalCompleted ? { ...plan, podRuns: basePlan.podRuns.map((row) => ({ ...row, status: "completed", progressPercent: 100 })) } : plan;
    if (path.endsWith("/chief-orchestration/approval-readiness")) body = { schemaVersion: "realytics-deal-state-approval-readiness-v1", projectId: "deal-workspace", readable: true, assessmentStatus: "unassessed" };
    if (path.endsWith("/chief-orchestration/preflight")) {
      preflightRequests += 1;
      body = {
        schemaVersion: "realytics-chief-pod-preflight-v1", projectId: "deal-workspace", analysisOnly: true,
        workflowTrack: "development", inputRevision: "1".repeat(64), receiptId: "f".repeat(64), resumeState: { scope: "synthetic-review" },
        reviewLoop: { status: blocked ? "needs_material" : "stable", stopReason: blocked ? "user_material_required" : "fixed_point", rounds: 1, executedCount: 6, reusedCount: 0, pendingPodKeys: [], canContinue: false },
        pods: ["land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"]
          .map((podKey) => ({ podKey, status: "checked", executionStatus: "completed", checks: [] })),
        publicLookups: [{ key: "land", label: "토지 공개 근거", status: "checked", message: "확인한 필지와 일치합니다." }],
        materialRequests: blocked ? [{
          itemId: "source_documents_missing", category: "intake", description: "원본 심사자료를 올려 주세요.", purpose: "사업의 원본 근거 확인",
          severity: "blocking", requiredFor: ["all_pods"], acceptableSources: ["사업계획서"],
        }] : [],
      };
    }
    if (path.endsWith("/chief-orchestration/run-all-pods")) {
      runRequests += 1;
      formalCompleted = true;
      body = { ...plan, podRuns: basePlan.podRuns.map((row) => ({ ...row, status: "completed", progressPercent: 100 })) };
    }
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/deal-workspace/chief-orchestration?track=development", { waitUntil: "domcontentloaded" });
  return { runRequests: () => runRequests, preflightRequests: () => preflightRequests, requested };
}

test("the user sees next actions and opens a focused review without loading diagnostics", async ({ page }) => {
  const requests = await mockDealRoom(page, true);
  await expect(page.getByRole("heading", { name: "저장된 자료부터 자동으로 확인하세요" })).toBeVisible({ timeout: 45_000 });
  const next = page.getByRole("region", { name: "다음 할 일" });
  await expect(next.getByRole("heading", { name: "심사 자료 올리기" })).toHaveCount(0);
  await expect(next.getByText("각 검토의 확인을 마친 뒤 필요한 자료를 안내합니다.")).toBeVisible();
  await expect(page.getByText(/pod_input_validation_failed|Event Bus|전체 실행 완료/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "분석 시작" })).toHaveCount(0);
  await expect(page.getByTestId("deal-room-glass")).toHaveCSS("backdrop-filter", "blur(16px) saturate(1.28)");
  const atmosphere = page.getByTestId("deal-room-parallax");
  const offset = () => atmosphere.evaluate((el) => Number.parseFloat(getComputedStyle(el).getPropertyValue("--dashboard-parallax-y")));
  if (await page.evaluate(() => window.matchMedia("(min-width: 768px)").matches)) {
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.setViewportSize({ width: 1280, height: 480 });
    await page.evaluate(() => window.scrollTo(0, 140));
    await expect.poll(offset).toBeGreaterThan(0);
  }
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect.poll(offset).toBe(0);
  await page.getByRole("button", { name: /^토지·인허가/ }).click();
  const drawer = page.getByRole("dialog", { name: "토지·인허가" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByRole("link", { name: "관련 화면 열기" })).toHaveAttribute("href", "/project/deal-workspace/land?track=development");
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  expect(requests.runRequests()).toBe(0);
  expect(requests.requested.filter((path) => /recalculation-events|pod-ai-reviews|legal\/workspace/.test(path))).toEqual([]);
  expect(requests.preflightRequests()).toBe(0);
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await expect(page.getByText("사전 검토 결과 · 6개 영역 · 제출·확인 항목 1건")).toBeVisible();
  await expect(next.getByRole("heading", { name: "심사 자료 올리기" })).toBeVisible();
  await expect(next.getByRole("link", { name: "자료 입력" })).toHaveAttribute("href", "/project/deal-workspace/input-package?track=development");
  await expect(next.getByText("필지와 토지 근거 확인")).toHaveCount(0);
  const purpose = next.getByText("사용 목적 · 사업의 원본 근거 확인");
  if (!(await purpose.isVisible())) await next.getByText("필요한 자료 1건", { exact: true }).click();
  await expect(purpose).toBeVisible();
  expect(requests.runRequests()).toBe(0);
});

test("eligible analysis executes after the user starts it", async ({ page }) => {
  const requests = await mockDealRoom(page, false);
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click({ timeout: 45_000 });
  await expect(page.getByText("사전 검토 결과 · 6개 영역 · 제출·확인 항목 0건")).toBeVisible();
  const start = page.getByRole("button", { name: "분석 시작", exact: true });
  await expect(start).toBeVisible({ timeout: 45_000 });
  expect(requests.runRequests()).toBe(0);
  await start.click();
  await expect(page.getByRole("heading", { name: "검토 결과를 확인하세요" })).toBeVisible();
  await expect(page.getByRole("link", { name: "보고서 보기" })).toHaveAttribute("href", "/project/deal-workspace/report?track=development");
  expect(requests.runRequests()).toBe(1);
});
