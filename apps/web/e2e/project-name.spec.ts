import { expect, test, type Page } from "@playwright/test";

const originalFile = "original-source.csv";
const file = { name: originalFile, mimeType: "text/csv", buffer: Buffer.from("item,value\noriginal,1") };

async function projectFixture(page: Page, readOnly = false) {
  const state = { name: "original-source", updates: [] as unknown[], updateStatus: 200, wrongProject: false, partialOverview: false, creates: [] as unknown[], uploadBodies: [] as string[] };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/\/$/, "");
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path.endsWith("/auth/me")) return reply({ authenticated: true, authSource: "bearer_token",
      user: { userId: "name-user", tenantId: "name-tenant", role: readOnly ? "reviewer" : "analyst", status: "active" },
      session: { userId: "name-user", tenantId: "name-tenant" }, permissions: readOnly ? ["project:read"] : ["project:read", "project:write", "assumption:override"] });
    if (path.endsWith("/projects/recent-work")) return reply({ items: [] });
    if (path.endsWith("/projects/intake/continuation-check")) return reply({ matches: [] });
    if (path.endsWith("/projects")) {
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        state.creates.push(body);
        state.name = body.project?.name ?? body.name;
        return reply({ project: { id: "rename-project", name: state.name } });
      }
      return reply([]);
    }
    if (path.endsWith("/projects/rename-project") && request.method() === "PUT") {
      const body = request.postDataJSON();
      state.updates.push(body);
      if (state.updateStatus !== 200) return reply({ detail: "temporarily_unavailable" }, state.updateStatus);
      if (!state.wrongProject) state.name = body.project.name;
      return reply({ project: { id: state.wrongProject ? "other-project" : "rename-project", name: body.project.name }, source_documents: [{ originalFileName: originalFile }] });
    }
    if (path.endsWith("/workspace/context") && state.partialOverview) return reply({
      projectId: "rename-project", contextCompleteness: "partial", unavailableSections: ["landEvidence", "missingInputs"],
      projectMemory: { projectName: state.name, sourceDocumentCount: 0 },
      pageData: { sourceDocumentSummary: { count: 0 }, landLedgerSummary: { parcelCount: 0, pnuCount: 0 } },
    });
    if (path.endsWith("/workspace/context")) return reply({ projectId: "rename-project", contextCompleteness: "complete", unavailableSections: [],
      projectMemory: { projectName: state.name, sourceDocumentCount: 1 }, missingInputRegister: [], dealStateGraph: { graph: { conflicts: [] } },
      pageData: { sourceDocumentSummary: { count: 1 }, sourceDocuments: [{ originalFileName: originalFile }], parsedFacts: [] } });
    if (path.endsWith("/feasibility-workbook/review")) return reply({ values: { total_revenue: 100_000_000 }, fieldReviews: {}, terms: { schedule: {}, financing: [] }, termReviews: {}, reviewHash: "name-review", sourceDocuments: [] });
    if (path.endsWith("/intake/classify")) {
      state.uploadBodies.push(request.postDataBuffer()?.toString("utf8") ?? "");
      return reply({ files: [{ filename: originalFile, documentType: "business_plan", label: "사업계획서", confidence: 1, needsReview: false, archiveStored: true }] });
    }
    return reply({ detail: "not_mocked" }, 404);
  });
  return state;
}

const projectHeader = (page: Page) => page.getByRole("region", { name: "프로젝트 정보", exact: true });
const renameDialog = (page: Page) => page.getByRole("dialog", { name: "프로젝트 이름 변경", exact: true });

test("project rename persists on reopen without changing original files or unsaved review values", async ({ page }) => {
  const state = await projectFixture(page);
  await page.goto("/project/rename-project/input-package?view=review");
  const review = page.getByRole("region", { name: "사업수지 입력 검토" });
  await expect(review.getByLabel("총매출 (억원)")).toHaveValue("1");
  await review.getByLabel("총매출 (억원)").fill("2");
  await projectHeader(page).getByRole("button", { name: "이름 변경", exact: true }).click();
  await expect(renameDialog(page).getByLabel("프로젝트 이름", { exact: true })).toHaveValue("original-source");
  await renameDialog(page).getByLabel("프로젝트 이름", { exact: true }).fill("  본리동 개발 검토  ");
  await renameDialog(page).getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(renameDialog(page)).toBeHidden();
  await expect(projectHeader(page).getByRole("link", { name: "본리동 개발 검토", exact: true })).toBeVisible();
  await expect(review.getByLabel("총매출 (억원)")).toHaveValue("2");
  expect(state.updates).toEqual([{ project: { name: "본리동 개발 검토" } }]);
  await page.getByRole("tab", { name: "자료·원본", exact: true }).click();
  await expect(page.getByText(originalFile, { exact: true })).toBeVisible();
  await page.reload();
  await expect(projectHeader(page).getByRole("link", { name: "본리동 개발 검토", exact: true })).toBeVisible();
  await expect(page.getByText(originalFile, { exact: true })).toBeVisible();
  await projectHeader(page).getByRole("link", { name: "본리동 개발 검토", exact: true }).click();
  await expect(page.getByRole("heading", { name: "프로젝트 개요", exact: true })).toBeVisible();
  await expect(projectHeader(page).getByRole("link", { name: "본리동 개발 검토", exact: true })).toBeVisible();
});

test("blank name and cancellation do not save; failed save retains draft and can retry", async ({ page }) => {
  const state = await projectFixture(page);
  await page.goto("/project/rename-project");
  const trigger = projectHeader(page).getByRole("button", { name: "이름 변경", exact: true });
  await trigger.click();
  await renameDialog(page).getByLabel("프로젝트 이름", { exact: true }).fill("   ");
  await renameDialog(page).getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(renameDialog(page).getByRole("alert")).toContainText("프로젝트 이름을 입력해 주세요");
  expect(state.updates).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(renameDialog(page).getByLabel("프로젝트 이름", { exact: true })).toHaveValue("original-source");
  await renameDialog(page).getByLabel("프로젝트 이름", { exact: true }).fill("재시도할 프로젝트");
  state.updateStatus = 503;
  await renameDialog(page).getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(renameDialog(page).getByRole("alert")).toContainText("프로젝트 이름 저장");
  await expect(renameDialog(page).getByLabel("프로젝트 이름", { exact: true })).toHaveValue("재시도할 프로젝트");
  await expect(projectHeader(page).getByRole("link", { name: "original-source", exact: true })).toBeVisible();
  state.updateStatus = 200;
  await renameDialog(page).getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(renameDialog(page)).toBeHidden();
  await expect(projectHeader(page).getByRole("link", { name: "재시도할 프로젝트", exact: true })).toBeVisible();
});

test("read-only account cannot edit the project name", async ({ page }) => {
  const state = await projectFixture(page, true);
  await page.goto("/project/rename-project/input-package");
  await expect(projectHeader(page).getByRole("link", { name: "original-source", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "이름 변경", exact: true })).toHaveCount(0);
  expect(state.updates).toEqual([]);
});

test("project overview distinguishes an empty list from unavailable evidence and work items", async ({ page }) => {
  const state = await projectFixture(page);
  state.partialOverview = true;
  await page.goto("/project/rename-project");
  await expect(page.getByText("저장 자료", { exact: true }).locator("..")).toContainText("0건");
  await expect(page.getByText("PNU 확보", { exact: true }).locator("..")).toContainText("산정 전");
  await expect(page.getByText("토지 증빙", { exact: true }).locator("..")).toContainText("미확인");
  await expect(page.getByText("보완 항목을 확인하지 못했습니다.", { exact: false })).toBeVisible();
  await expect(page.getByText("현재 조회된 보완 항목은 없습니다.", { exact: true })).toHaveCount(0);
});

test("a different project in the save response cannot replace the displayed name", async ({ page }) => {
  const state = await projectFixture(page);
  state.wrongProject = true;
  await page.goto("/project/rename-project");
  await projectHeader(page).getByRole("button", { name: "이름 변경", exact: true }).click();
  await renameDialog(page).getByLabel("프로젝트 이름", { exact: true }).fill("잘못된 응답 이름");
  await renameDialog(page).getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(renameDialog(page).getByRole("alert")).toContainText("저장된 프로젝트 이름을 확인하지 못했습니다");
  await expect(projectHeader(page).getByRole("link", { name: "original-source", exact: true })).toBeVisible();
});

for (const entry of ["/start", "/dashboard"]) {
  for (const customName of ["직접 입력한 검토 이름", ""]) {
    test(`${entry} creates once with ${customName ? "a chosen name" : "the filename suggestion"} and keeps original upload bytes`, async ({ page }) => {
      const state = await projectFixture(page);
      await page.goto(entry);
      if (entry === "/dashboard") await page.getByRole("button", { name: "첫 프로젝트 시작", exact: true }).click();
      const input = page.getByLabel("프로젝트 이름 (선택)", { exact: true });
      await expect(input).toBeVisible();
      if (customName) await input.fill(` ${customName} `);
      await page.locator('input[type="file"]').setInputFiles(file);
      await expect(page.getByRole("button", { name: "이름 변경", exact: true })).toBeVisible();
      await expect.poll(() => state.uploadBodies.length).toBe(1);
      expect(state.creates).toHaveLength(1);
      const create = state.creates[0] as { project?: { name: string }; name?: string };
      expect(create.project?.name ?? create.name).toBe(customName || "original-source");
      expect(state.uploadBodies[0]).toContain(`filename="${originalFile}"`);
      expect(state.uploadBodies[0]).toContain("original,1");
      expect(state.updates).toEqual([]);
      await page.getByRole("button", { name: "이름 변경", exact: true }).click();
      await expect(renameDialog(page).getByLabel("프로젝트 이름", { exact: true })).toHaveValue(customName || "original-source");
      await renameDialog(page).getByRole("button", { name: "취소", exact: true }).click();
      expect(state.updates).toEqual([]);
    });
  }
}
