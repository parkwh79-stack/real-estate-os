import { expect, test, type Page } from "@playwright/test";

async function dashboard(page: Page, options: { existing?: number; readOnly?: boolean; malformed?: boolean; badRow?: boolean } = {}) {
  const state = {
    count: options.existing ?? 0, creates: 0, classifications: 0, uploads: 0, failClassify: false,
    createGate: Promise.resolve(), classifyGate: Promise.resolve(), uploadBodies: [] as string[],
    tenantId: "intake-tenant", malformedUpload: false,
  };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/\/$/, "");
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/auth/me")) return reply({ authenticated: true,
      user: { userId: "intake-user", tenantId: state.tenantId, displayName: "입력 검토자", role: options.readOnly ? "reviewer" : "analyst" },
      session: { userId: "intake-user", tenantId: state.tenantId }, permissions: options.readOnly ? ["project:read"] : ["project:read", "project:write"], authSource: "bearer_token" });
    if (path.endsWith("/projects/recent-work")) return reply({ items: state.count ? [{
      projectId: "existing", projectName: "최근 검토 프로젝트", lastWorkedAt: "2026-11-04T01:00:00Z", latestRunStatus: "completed",
      sourceDocumentCount: 2, openReviewCount: 0, riskCount: 0, approvalStatus: "not_requested", nextAction: "continue_next_review_step",
    }] : [] });
    if (path.endsWith("/projects")) {
      if (request.method() === "POST") {
        state.creates += 1;
        await state.createGate;
        state.count += 1;
        return reply({ id: "created-project" });
      }
      return reply(options.malformed ? { unexpected: true } : options.badRow ? [{ project: { id: "bad-row", name: 123 }, land: { address: {} } }] : Array.from({ length: state.count }, (_, index) => ({
        project: { id: `all-${index + 1}`, name: `전체 프로젝트 ${index + 1}` }, land: { address: `주소 ${index + 1}` },
      })));
    }
    if (path.endsWith("/projects/created-project/workspace/context")) return reply({
      projectId: "created-project", normalizedPage: "input_package", workflowTrack: "development",
      projectMemory: { projectName: "review", sourceDocumentCount: state.uploads },
      pageData: { sourceDocumentSummary: { count: state.uploads }, sourceDocuments: state.uploads
        ? [{ originalFileName: "review.pdf", documentType: "business_plan" }] : [] },
    });
    if (path.endsWith("/projects/created-project/feasibility-workbook/review")) return reply({
      values: {}, fieldReviews: {}, reviewHash: "empty-workbook-review", sourceDocuments: [],
    });
    if (path.endsWith("/intake/classify")) {
      state.classifications += 1;
      state.uploadBodies.push(request.postDataBuffer()?.toString("utf8") ?? "");
      await state.classifyGate;
      if (state.failClassify) return reply({ detail: "classification_unavailable" }, 503);
      return reply({ files: [{ filename: "review.pdf", documentType: "business_plan", label: "사업계획서", confidence: 1, needsReview: false, archiveStored: true }] });
    }
    if (path.endsWith("/upload")) {
      state.uploads += 1;
      state.uploadBodies.push(request.postDataBuffer()?.toString("utf8") ?? "");
      if (state.malformedUpload) return route.fulfill({ status: 200, contentType: "application/json", body: "not json" });
      return reply({ project: { id: "created-project" } });
    }
    return reply({ error: "not_mocked" }, 404);
  });
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "대시보드", exact: true })).toBeVisible();
  return state;
}

const file = { name: "review.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7\noriginal-file-bytes") };

test("first visit has one start action and no empty metrics or mounted upload form", async ({ page }) => {
  const state = await dashboard(page);
  const start = page.getByRole("button", { name: "첫 프로젝트 시작", exact: true });
  await expect(start).toBeVisible();
  await expect(page.getByTestId("dashboard-work-inbox")).toHaveCount(0);
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await start.click();
  const dialog = page.getByRole("dialog", { name: "새 프로젝트", exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('input[type="file"]')).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(start).toBeFocused();
  expect(state.creates).toBe(0);
});

test("all projects remain searchable beyond the recent feed and survive direct anchor entry", async ({ page }) => {
  await dashboard(page, { existing: 27 });
  await expect(page.getByRole("button", { name: "새 프로젝트", exact: true })).toBeVisible();
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await page.goto("/dashboard#all-projects");
  const all = page.locator("#all-projects");
  await expect(all).toHaveAttribute("open", "");
  await expect(all.getByRole("link")).toHaveCount(12);
  await all.getByRole("button", { name: "다음", exact: true }).click();
  await expect(all.getByRole("link", { name: /전체 프로젝트 13/ })).toBeVisible();
  await all.getByRole("searchbox", { name: "전체 프로젝트 검색" }).fill("프로젝트 27");
  await expect(all.getByRole("link")).toHaveCount(1);
  await expect(all.getByRole("link")).toHaveAttribute("href", "/project/all-27");
  await expect(all.getByRole("button", { name: "다음", exact: true })).toHaveCount(0);
});

test("malformed project collection is unavailable instead of a new-user zero state", async ({ page }) => {
  await dashboard(page, { malformed: true });
  await expect(page.getByTestId("dashboard-work-inbox").getByRole("group", { name: "전체 프로젝트", exact: true })).toContainText("—");
  await expect(page.getByRole("button", { name: "첫 프로젝트 시작", exact: true })).toHaveCount(0);
  await expect(page.locator("#all-projects summary")).toContainText("확인 불가");
});

test("upload survives closing while creating and processing preserves exact file bytes without another project", async ({ page }) => {
  const state = await dashboard(page);
  let release!: () => void;
  state.createGate = new Promise<void>((resolve) => { release = resolve; });
  await page.getByRole("button", { name: "첫 프로젝트 시작", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles(file);
  await expect.poll(() => state.creates).toBe(1);
  await dialog.getByRole("button", { name: "자료 업로드 창 닫기" }).click();
  release();
  await expect.poll(() => state.classifications).toBe(1);
  await page.getByRole("button", { name: "자료 업로드 이어가기", exact: true }).click();
  await expect(dialog.getByText("review.pdf", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "자료 업로드 이어가기", exact: true }).click();
  await dialog.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(dialog.getByText(/처리 완료/)).toBeVisible();
  expect(state.creates).toBe(1);
  expect(state.classifications).toBe(1);
  expect(state.uploads).toBe(1);
  expect(state.uploadBodies).toHaveLength(2);
  expect(state.uploadBodies.every((body) => body.includes("original-file-bytes"))).toBe(true);
  await dialog.getByRole("link", { name: "프로젝트 자료 보기" }).click();
  await expect(page).toHaveURL(/\/project\/created-project\/input-package$/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "자료 입력 패키지", exact: true, level: 1 })).toBeVisible();
  await expect(page.getByText("review.pdf", { exact: true })).toBeVisible();
});

test("classification failure retains files for explicit retry after reopening", async ({ page }) => {
  const state = await dashboard(page);
  state.failClassify = true;
  await page.getByRole("button", { name: "첫 프로젝트 시작", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles(file);
  await expect(dialog.getByRole("button", { name: "분류 다시 시도", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  state.failClassify = false;
  await page.getByRole("button", { name: "자료 업로드 이어가기", exact: true }).click();
  await dialog.getByRole("button", { name: "분류 다시 시도", exact: true }).click();
  await expect(dialog.getByText("review.pdf", { exact: true })).toBeVisible();
  expect(state.creates).toBe(1);
  expect(state.classifications).toBe(2);
  expect(state.uploadBodies.every((body) => body.includes("original-file-bytes"))).toBe(true);
});

test("read-only user can review but cannot open project creation or upload", async ({ page }) => {
  const state = await dashboard(page, { existing: 1, readOnly: true });
  await expect(page.getByTestId("dashboard-new-deal-disabled")).toBeDisabled();
  await expect(page.getByTestId("review-only-notice")).toContainText("새 프로젝트 생성과 자료 업로드는 사용할 수 없습니다");
  await expect(page.getByRole("link", { name: "최근 검토 프로젝트", exact: true })).toBeVisible();
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  expect(state.creates).toBe(0);
});

test("invalid project text fields stay unavailable without crashing the dashboard", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await dashboard(page, { badRow: true });
  await expect(page.getByTestId("dashboard-work-inbox").getByRole("group", { name: "전체 프로젝트", exact: true })).toContainText("—");
  await expect(page.locator("#all-projects summary")).toContainText("확인 불가");
  expect(pageErrors).toEqual([]);
});

test("a malformed successful HTTP response cannot mark a file processed and can be retried", async ({ page }) => {
  const state = await dashboard(page);
  state.malformedUpload = true;
  await page.getByRole("button", { name: "첫 프로젝트 시작", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles(file);
  await expect(dialog.getByText("review.pdf", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(dialog.getByRole("listitem").filter({ hasText: "실패" })).toBeVisible();
  await expect(dialog.getByText(/처리 완료/)).toHaveCount(0);
  state.malformedUpload = false;
  await dialog.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(dialog.getByRole("listitem").filter({ hasText: "처리 완료" })).toBeVisible();
  expect(state.uploads).toBe(2);
  expect(state.creates).toBe(1);
});

test("pending upload can cancel search navigation and remain available in the same workspace", async ({ page }) => {
  const state = await dashboard(page);
  await page.getByRole("button", { name: "첫 프로젝트 시작", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles(file);
  await expect(dialog.getByText("review.pdf", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  const search = page.getByRole("combobox", { name: "프로젝트 검색", exact: true });
  await search.fill("전체 프로젝트");
  await search.press("Enter");
  await expect(page.getByRole("listbox", { name: "프로젝트 검색 결과" }).getByRole("option")).toBeVisible();
  let confirmations = 0;
  page.once("dialog", async (confirmation) => { confirmations += 1; await confirmation.dismiss(); });
  await search.press("ArrowDown");
  await search.press("Enter");
  await expect.poll(() => confirmations).toBe(1);
  await expect(page).toHaveURL(/\/dashboard$/);
  await search.press("Escape");
  await page.getByRole("button", { name: "자료 업로드 이어가기", exact: true }).click();
  await expect(dialog.getByText("review.pdf", { exact: true })).toBeVisible();
  expect(state.creates).toBe(1);
});

test("a workspace identity change discards the prior workspace's in-memory upload", async ({ page }) => {
  const state = await dashboard(page);
  await page.getByRole("button", { name: "첫 프로젝트 시작", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles(file);
  await expect(dialog.getByText("review.pdf", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  state.tenantId = "other-tenant";
  state.count = 0;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("realytics-auth-session-changed")));
  await expect(page.getByRole("button", { name: "첫 프로젝트 시작", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "자료 업로드 이어가기", exact: true })).toHaveCount(0);
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  expect(state.creates).toBe(1);
});
