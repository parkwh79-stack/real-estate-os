import { expect, test, type Page } from "@playwright/test";
import type { CalculationRun } from "../lib/calculation-runs";

test.setTimeout(90_000);

const RUN: CalculationRun = {
  calculationRunId: "calc:pf_metrics:saved-original", engineKey: "pf_metrics", engineVersion: "pf_metrics-v1",
  createdAt: "2026-10-14T03:00:00Z", inputDigest: { capturePolicy: "effective_request_v1" },
  inputSnapshot: { total_revenue: 2800, finance_cost: 0, unknown_value: null,
    sourceLocator: { documentName: "합성 검증 자료", sheet: "입력", cell: "A1" } },
  resultSnapshot: { ltv: .5, unknown_metric: null }, outputMetrics: [{ key: "pf_metrics.ltv", value: .5 }],
};

async function fixture(page: Page, initialRuns: CalculationRun[] = [structuredClone(RUN)]) {
  const state = { runs: initialRuns, status: 200, malformed: false, liveRevenue: 2800, calls: 0,
    hold: undefined as Promise<void> | undefined };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/$/, "");
    const projectId = /\/projects\/([^/]+)/.exec(path)?.[1] ?? "history-a";
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = {
      authenticated: true, authSource: "bearer_token",
      user: { userId: "history-reviewer", tenantId: "synthetic-tenant", role: "admin", status: "active" },
      session: { userId: "history-reviewer", tenantId: "synthetic-tenant" }, permissions: ["project:read"],
    };
    else if (path.endsWith("/projects")) body = [
      { project: { id: "history-a", name: "계산 이력 A" } }, { project: { id: "history-b", name: "계산 이력 B" } },
    ];
    else if (path.endsWith("/workspace/context")) body = { projectId, projectMemory: { projectName: "합성 계산 이력" }, pageData: {} };
    else if (path.endsWith("/deal-state-records")) body = { projectId, records: [] };
    else if (path.endsWith("/calculation-runs")) {
      state.calls += 1;
      const records = structuredClone(projectId === "history-a" ? state.runs : []);
      if (projectId === "history-a") await state.hold;
      await route.fulfill({ status: state.status, json: state.status !== 200 ? { detail: "internal detail must stay private" }
        : state.malformed ? {} : { projectId, records } });
      return;
    } else if (path.endsWith(`/projects/${projectId}`)) body = {
      project: { id: projectId, name: "합성 계산 이력" }, feasibility_input: { total_revenue: state.liveRevenue },
    };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/history-a/risk", { waitUntil: "domcontentloaded", timeout: 60_000 });
  const history = page.getByRole("region", { name: "계산 실행 이력", exact: true });
  await expect(history).toBeVisible({ timeout: 60_000 });
  return { state, history };
}

test("reopen and download the identical stored input and result after live inputs change", async ({ page }) => {
  const { state, history } = await fixture(page);
  await history.getByRole("button", { name: "입력·결과 보기", exact: true }).click();
  let details = page.getByRole("region", { name: "저장된 계산 입력과 결과", exact: true });
  await expect(details).toContainText("계산에 사용한 요청이 저장되어 있습니다.");
  await details.locator("summary", { hasText: "저장된 입력 원문" }).click();
  await details.locator("summary", { hasText: "저장된 결과 원문" }).click();
  await expect(details.locator("pre").first()).toContainText('"finance_cost": 0');
  await expect(details.locator("pre").first()).toContainText('"unknown_value": null');
  await expect(details.locator("pre").first()).toContainText("합성 검증 자료");
  const downloadEvent = page.waitForEvent("download");
  await details.getByRole("button", { name: "저장 내용 다운로드 (JSON)", exact: true }).click();
  const download = await downloadEvent;
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
  expect(JSON.parse(Buffer.concat(chunks).toString("utf8"))).toEqual(RUN);
  expect(download.suggestedFilename()).toBe("calculation-calc_pf_metrics_saved-original.json");
  state.liveRevenue = 9999;
  await page.reload({ waitUntil: "domcontentloaded" });
  await history.getByRole("button", { name: "입력·결과 보기", exact: true }).click();
  details = page.getByRole("region", { name: "저장된 계산 입력과 결과", exact: true });
  await details.locator("summary", { hasText: "저장된 입력 원문" }).click();
  await expect(details.locator("details").filter({ has: page.locator("summary", { hasText: "저장된 입력 원문" }) }).locator("pre"))
    .toContainText('"total_revenue": 2800');
  await expect(details).not.toContainText("9999");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test("legacy runs expose absent snapshots without filling them with current project values", async ({ page }) => {
  const legacy = { ...RUN, inputSnapshot: {}, resultSnapshot: undefined, inputDigest: {} };
  const { history } = await fixture(page, [legacy]);
  await history.getByRole("button", { name: "입력·결과 보기", exact: true }).click();
  const details = page.getByRole("region", { name: "저장된 계산 입력과 결과", exact: true });
  await expect(details).toContainText("전체 입력 저장 여부는 확인되지 않았습니다.");
  for (const label of ["저장된 입력 원문", "저장된 결과 원문"]) await details.locator("summary", { hasText: label }).click();
  await expect(details).toContainText("입력 원문이 저장되지 않았습니다.");
  await expect(details).toContainText("결과 원문이 저장되지 않았습니다.");
  await expect(details).not.toContainText("2800");
});

for (const status of [403, 503, 200]) {
  test(`lookup failure ${status} is visible and retry recovers without a false empty state`, async ({ page }) => {
    const { state, history } = await fixture(page);
    state.status = status;
    state.malformed = status === 200;
    await history.getByRole("button", { name: "새로고침", exact: true }).click();
    await expect(history.getByRole("alert")).toContainText(status === 403 ? "권한" : status === 503 ? "불러오지 못했습니다" : "응답을 확인하지 못했습니다");
    await expect(history).not.toContainText("저장된 계산 실행이 없습니다");
    await expect(history).not.toContainText("internal detail");
    await expect(history.getByRole("button", { name: "입력·결과 보기", exact: true })).toHaveCount(0);
    state.status = 200; state.malformed = false;
    await history.getByRole("button", { name: "새로고침", exact: true }).click();
    await expect(history.getByRole("button", { name: "입력·결과 보기", exact: true })).toBeVisible();
    await expect(history.getByRole("alert")).toHaveCount(0);
  });
}

test("comparison survives detail inspection and warns about different calculation versions", async ({ page }) => {
  const previous = { ...RUN, calculationRunId: "calc:pf_metrics:older", engineVersion: "pf_metrics-v0",
    createdAt: "2026-10-13T00:00:00Z", outputMetrics: [{ key: "pf_metrics.ltv", value: .4 }] };
  const { history } = await fixture(page, [RUN, previous]);
  await history.getByRole("checkbox").nth(0).check();
  await history.getByRole("checkbox").nth(1).check();
  await expect(history).toContainText("계산 버전이 다르거나 기록되지 않았습니다.");
  await history.getByRole("button", { name: "입력·결과 보기", exact: true }).first().click();
  await expect(history.getByRole("checkbox").first()).toBeChecked();
  await expect(history.getByRole("checkbox").nth(1)).toBeChecked();
  await expect(history).toContainText("▲ +0.1");
});

test("a late lookup from the previous project cannot populate the new project's history", async ({ page }) => {
  const { state, history } = await fixture(page);
  let release!: () => void;
  state.hold = new Promise<void>((resolve) => { release = resolve; });
  const initialCalls = state.calls;
  try {
    await history.getByRole("button", { name: "새로고침", exact: true }).click();
    await expect.poll(() => state.calls).toBeGreaterThan(initialCalls);
    const search = page.getByPlaceholder("주소, PNU, 프로젝트 검색", { exact: true });
    await search.fill("계산 이력 B");
    await search.press("Enter");
    await expect(page).toHaveURL("/project/history-b");
    await page.locator("a[href='/project/history-b/risk']").first().click();
    await expect(history).toContainText("저장된 계산 실행이 없습니다");
  } finally { release(); }
  await expect(history.getByRole("button", { name: "입력·결과 보기", exact: true })).toHaveCount(0);
  await expect(history).not.toContainText("saved-original");
});
