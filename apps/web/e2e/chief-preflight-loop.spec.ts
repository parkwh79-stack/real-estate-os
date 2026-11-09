import { expect, test, type Page } from "@playwright/test";

const projectId = "chief-loop-sample";
const podKeys = ["land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"];
const material = {
  itemId: "source_documents_missing", category: "intake", description: "사업 원본을 확인해 주세요.",
  purpose: "분석의 원본 출처 확인", severity: "blocking", requiredFor: ["all_pods"],
  acceptableSources: ["사업계획서"], action: "provide_material",
};
const receipt = (index: number) => (index + 1).toString(16).padStart(64, "0");

function result(revision: string, index: number, status = "stable", canContinue = false) {
  return {
    schemaVersion: "realytics-chief-pod-preflight-v1", projectId, workflowTrack: "development",
    inputRevision: revision, analysisOnly: true,
    receiptId: receipt(index), resumeState: { checkpoint: index, privateValue: "synthetic-private-review" },
    pods: podKeys.map((podKey) => ({
      podKey, status: "checked", executionStatus: status === "retry_needed" && podKey === "land_legal" ? "failed" : "completed",
      checks: [], agentExecutions: [],
    })),
    publicLookups: status === "retry_needed"
      ? [{ key: "parcels", label: "필지 조회", status: "failed", message: "공공 조회 연결을 완료하지 못했습니다." }] : [],
    materialRequests: status === "stable" ? [] : [material],
    reviewLoop: {
      status, stopReason: canContinue ? "time_budget" : status === "limit_reached" ? "execution_limit" : status === "retry_needed" ? "agent_error" : "fixed_point",
      rounds: 1, executedCount: 1, reusedCount: 5, canContinue,
      pendingPodKeys: status === "limit_reached" ? ["pf_credit"] : [], agents: [],
    },
  };
}

async function mockLoop(page: Page, response: (index: number, revision: string) => { status?: number; body?: unknown }) {
  const state = {
    revision: "1".repeat(64),
    user: "analyst-a",
    tenant: "tenant-a",
    canAnalyze: true,
    bodies: [] as Array<Record<string, unknown>>,
    formalRuns: 0,
  };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: {
      authenticated: true, user: { userId: state.user, tenantId: state.tenant, role: state.canAnalyze ? "analyst" : "viewer" },
      permissions: ["project:read", ...(state.canAnalyze ? ["project:analyze"] : [])],
    } });
    if (path.endsWith("/workspace/context")) return route.fulfill({ json: {
      projectId, projectMemory: { projectName: "합성 반복 검토" },
      pageData: { landLedger: { parcels: [], summary: { parcelCount: 0, pnuCount: 0 } } },
    } });
    if (path.endsWith("/chief-orchestration/plan")) return route.fulfill({ json: {
      schemaVersion: "realytics-chief-deal-orchestration-v1", projectId, preflightInputRevision: state.revision,
      agentPods: [], podRuns: podKeys.map((podKey) => ({ podKey, status: "planned", progressPercent: 0 })),
      sharedDealStateGraph: { missingInputRegister: [material] },
      taskGraph: { workflowTrack: "development", recommendedRunOrder: podKeys },
    } });
    if (path.endsWith("/chief-orchestration/approval-readiness")) return route.fulfill({ json: {
      schemaVersion: "realytics-deal-state-approval-readiness-v1", projectId, readable: true, assessmentStatus: "unassessed",
    } });
    if (path.endsWith("/chief-orchestration/preflight")) {
      const index = state.bodies.length;
      state.bodies.push(route.request().postDataJSON() as Record<string, unknown>);
      const next = response(index, state.revision);
      return route.fulfill({ status: next.status ?? 200, json: next.body ?? result(state.revision, index) });
    }
    if (path.endsWith("/chief-orchestration/run-all-pods")) state.formalRuns += 1;
    return route.fulfill({ json: {} });
  });
  await page.goto("/project/" + projectId + "/chief-orchestration?track=development", { waitUntil: "domcontentloaded" });
  return state;
}

test("collection progress shows matched parcels and remaining private evidence", async ({ page }) => {
  await mockLoop(page, (index, revision) => ({ body: { ...result(revision, index), publicLookups: [
    { key: "land_use_planning", label: "용도지역·토지이용계획", status: "deferred", checkedCount: 2, requestedCount: 3,
      message: "남은 필지의 공개 조회를 이어서 확인합니다." },
    { key: "market", label: "국토교통부 실거래가", status: "empty", message: "조회 기간의 거래를 확인하지 못했습니다." },
  ] } }));
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await page.getByText(/사전 검토 결과 · 6개 영역/).click();
  await expect(page.getByText("용도지역·토지이용계획 · 2/3필지 · 남은 필지의 공개 조회를 이어서 확인합니다.")).toBeVisible();
  await expect(page.getByText("국토교통부 실거래가 · 조회 기간의 거래를 확인하지 못했습니다.")).toBeVisible();
  await expect(page.getByText("공개 근거는 이번 분석에서만 사용합니다. 공사 견적·대출 약정·최종 승인 자료는 원본 확인이 필요합니다.")).toBeVisible();
});

test("failed preparation does not enable analysis and explicit retry restarts the failed request", async ({ page }) => {
  const state = await mockLoop(page, (index) => index === 0 ? { status: 503, body: { detail: "unavailable" } } : {});
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await expect(page.getByRole("heading", { name: "자동 확인을 완료하지 못했습니다" })).toBeVisible();
  await expect(page.getByRole("button", { name: /분석 시작|분석 다시 실행/ })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "심사 자료 올리기" })).toHaveCount(0);
  expect(state.formalRuns).toBe(0);
  await page.getByRole("button", { name: "사전 검토 다시 실행", exact: true }).click();
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toBeVisible();
  expect(state.bodies).toEqual([{ workflowTrack: "development" }, { workflowTrack: "development" }]);
});

test("known internal follow-up copy is localized without changing the preflight receipt", async ({ page }) => {
  const internalDescription = "Run Evidence Graph/source locator enrichment or attach locator-backed evidence.";
  const userDescription = "원본 문서의 페이지·표·셀 위치와 연결된 근거를 확인해 주세요.";
  const sourceSpecificDescription = "Additional source-specific check remains unresolved.";
  const requests = [
    { ...material, itemId: "cost_assumption_missing", description: internalDescription, severity: "high", requiredFor: ["cost_schedule"] },
    { ...material, itemId: "schedule_missing", description: "사업 일정은 사용자가 확인해야 합니다.", severity: "high", requiredFor: ["cost_schedule"] },
    { ...material, itemId: "source_specific_follow_up", description: sourceSpecificDescription, severity: "high", requiredFor: ["cost_schedule"] },
  ];
  const originalResumeState = { materialRequests: requests, checkpoint: 0, privateValue: "synthetic-private-review" };
  const state = await mockLoop(page, (index, revision) => ({ body: {
    ...result(revision, index, "needs_material"),
    materialRequests: requests,
    resumeState: originalResumeState,
  } }));
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await expect(page.getByText("사전 검토 결과 · 6개 영역 · 제출·확인 항목 3건")).toBeVisible();
  const next = page.getByRole("region", { name: "다음 할 일" });
  await next.getByText("필요한 자료 2건", { exact: true }).click();
  await expect(next.getByText(userDescription, { exact: true })).toBeVisible();
  await expect(next.getByText("사업 일정은 사용자가 확인해야 합니다.", { exact: true })).toBeVisible();
  await next.getByText("필요한 자료 1건", { exact: true }).click();
  await expect(next.getByText(sourceSpecificDescription, { exact: true })).toBeVisible();
  await expect(page.getByText(internalDescription, { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /^공사비·일정/ }).click();
  const drawer = page.getByRole("dialog", { name: "공사비·일정" });
  await expect(drawer.getByText(userDescription, { exact: true })).toBeVisible();
  await expect(drawer.getByText(sourceSpecificDescription, { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  state.revision = "2".repeat(64);
  await page.getByRole("button", { name: "자료 반영 확인", exact: true }).click();
  await expect.poll(() => state.bodies.length).toBe(2);
  expect(state.bodies[1]?.previousReview).toEqual({ receiptId: receipt(0), resumeState: originalResumeState });
  expect(state.formalRuns).toBe(0);
});

test("only explicit failed retry reuses the receipt and input changes recheck automatically", async ({ page }) => {
  const state = await mockLoop(page, (index, revision) => ({
    body: result(revision, index, index === 0 ? "retry_needed" : "stable"),
  }));
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await expect(page.getByRole("button", { name: "실패한 확인 다시 시도", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "심사 자료 올리기" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /분석 시작|분석 다시 실행/ })).toHaveCount(0);
  expect(state.bodies).toHaveLength(1);
  await page.getByRole("button", { name: "실패한 확인 다시 시도", exact: true }).click();
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toBeVisible();
  expect(state.bodies[1]).toEqual({
    workflowTrack: "development", retryFailed: true,
    previousReview: { receiptId: receipt(0), resumeState: { checkpoint: 0, privateValue: "synthetic-private-review" } },
  });
  await page.getByRole("button", { name: "자료 반영 확인", exact: true }).click();
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toBeVisible();
  expect(state.bodies).toHaveLength(2);
  state.revision = "2".repeat(64);
  await page.getByRole("button", { name: "자료 반영 확인", exact: true }).click();
  await expect.poll(() => state.bodies.length).toBe(3);
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toBeVisible();
  expect(state.bodies[2]).toEqual({
    workflowTrack: "development",
    previousReview: { receiptId: receipt(1), resumeState: { checkpoint: 1, privateValue: "synthetic-private-review" } },
  });
  expect(state.formalRuns).toBe(0);
});

test("time-budget continuation is bounded and uses the latest receipt", async ({ page }) => {
  const state = await mockLoop(page, (index, revision) => ({
    body: result(revision, index, index < 3 ? "limit_reached" : "stable", index < 3),
  }));
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await expect(page.getByRole("button", { name: "자동 확인 계속", exact: true })).toBeVisible();
  expect(state.bodies).toHaveLength(3);
  await expect(page.getByRole("heading", { name: "심사 자료 올리기" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /분석 시작|분석 다시 실행/ })).toHaveCount(0);
  await page.getByRole("button", { name: "자동 확인 계속", exact: true }).click();
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toBeVisible();
  expect(state.bodies).toHaveLength(4);
  expect(state.bodies[3]).toEqual({
    workflowTrack: "development",
    previousReview: { receiptId: receipt(2), resumeState: { checkpoint: 2, privateValue: "synthetic-private-review" } },
  });
});

test("a cumulative execution limit does not auto-loop or enable analysis", async ({ page }) => {
  const state = await mockLoop(page, (index, revision) => ({ body: result(revision, index, "limit_reached", false) }));
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await expect(page.getByRole("heading", { name: "자동 확인 범위에 도달했습니다" })).toBeVisible();
  expect(state.bodies).toHaveLength(1);
  await expect(page.getByRole("button", { name: "자동 확인 계속", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /분석 시작|분석 다시 실행/ })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "심사 자료 올리기" })).toHaveCount(0);
});

test("SPA return reuses memory while account change clears private preparation", async ({ page }) => {
  const state = await mockLoop(page, () => ({}));
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^토지·인허가/ }).click();
  await page.getByRole("dialog", { name: "토지·인허가" }).getByRole("link", { name: "관련 화면 열기" }).click();
  await expect(page).toHaveURL(new RegExp("/project/" + projectId + "/land\\?track=development$"));
  await page.goBack();
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toBeVisible();
  expect(state.bodies).toHaveLength(1);
  expect(await page.evaluate(() => [localStorage, sessionStorage].some((storage) =>
    Object.keys(storage).some((key) => (storage.getItem(key) ?? "").includes("synthetic-private-review")),
  ))).toBe(false);
  state.user = "analyst-b";
  state.tenant = "tenant-b";
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("realytics-auth-session-changed")));
  await expect(page.getByRole("button", { name: "사전 검토 실행", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "사전 검토 실행", exact: true }).click();
  await expect(page.getByRole("button", { name: "분석 시작", exact: true })).toBeVisible();
  expect(state.bodies).toHaveLength(2);
  expect(state.bodies[1]).toEqual({ workflowTrack: "development" });
  state.canAnalyze = false;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("realytics-auth-session-changed")));
  await expect(page.getByText("현재 계정은 검토 결과를 조회할 수 있습니다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "사전 검토 실행", exact: true })).toHaveCount(0);
  expect(state.bodies).toHaveLength(2);
});
