import { expect, test, type Page } from "@playwright/test";
import type { WorkspacePageContext } from "../lib/workspace-context";

const partialContext: WorkspacePageContext = {
  projectId: "context-recovery", contextCompleteness: "partial",
  unavailableSections: ["landEvidence", "parsedFacts", "assumptionOverrides", "missingInputs"],
  projectMemory: { projectName: "저장 자료 확인", sourceDocumentCount: 2 },
  pageData: { sourceDocumentSummary: { count: 2 }, sourceDocuments: [
    { originalFileName: "saved-one.pdf" }, { originalFileName: "saved-two.pdf" },
  ] },
};
const emptyContext: WorkspacePageContext = {
  projectId: "context-recovery", contextCompleteness: "complete", unavailableSections: [],
  projectMemory: { projectName: "빈 자료 확인", sourceDocumentCount: 0, landEvidenceCount: 0 },
  missingInputRegister: [], dealStateGraph: { graph: { conflicts: [] } },
  pageData: { sourceDocumentSummary: { count: 0 }, sourceDocuments: [],
    landLedgerSummary: { parcelCount: 0, pnuCount: 0 }, landEvidenceSummary: { count: 0 }, parsedFacts: [], parsedFactSummary: { recordCount: 0 } },
};

async function fixture(page: Page, status: number, body: WorkspacePageContext = partialContext, suffix = "") {
  const state = { status, body, reviewCalls: 0, mutations: [] as string[] };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const reply = (payload: unknown, code = 200) => route.fulfill({ status: code, json: payload });
    if (request.method() !== "GET") state.mutations.push(path);
    if (path.endsWith("/auth/me")) return reply({ authenticated: true, authSource: "bearer_token",
      user: { userId: "context-user", tenantId: "context-tenant", role: "analyst", status: "active" },
      session: { userId: "context-user", tenantId: "context-tenant" }, permissions: ["project:read", "project:write", "assumption:override"] });
    if (path.endsWith("/workspace/context")) return reply(state.status === 200 ? state.body : { detail: "workspace_unavailable" }, state.status);
    if (path.endsWith("/feasibility-workbook/review")) {
      state.reviewCalls += 1;
      return reply({ values: { total_revenue: 100_000_000 }, fieldReviews: {}, terms: { schedule: {}, financing: [] },
        termReviews: {}, reviewHash: "saved-context-review", sourceDocuments: [] });
    }
    if (path.endsWith("/intake/classify")) return reply({ files: [{ filename: "retained.csv", documentType: "business_plan", label: "사업계획서", confidence: 1, needsReview: false, archiveStored: true }] });
    return reply({ detail: "not_mocked" }, 404);
  });
  await page.goto(`/project/context-recovery/input-package${suffix}`);
  const inputPage = page.getByTestId("input-package-page");
  await expect(inputPage.getByRole("heading", { name: "자료 입력 패키지", exact: true })).toBeVisible();
  return { state, inputPage };
}

test("failed context omits false empty data and retry preserves selected files and edited review input", async ({ page }) => {
  const { state, inputPage } = await fixture(page, 500);
  await expect(inputPage.getByRole("alert")).toContainText("자료 입력 패키지 상태를 불러오지 못했습니다.");
  await expect(inputPage.getByRole("region", { name: "저장 자료 현황" })).toHaveCount(0);
  for (const text of ["현재 공통 자료 기준으로 즉시 표시할 보완 항목이 없습니다.", "아직 저장된 원본 문서가 없습니다.", "아직 저장된 파싱 후보값이 없습니다."]) {
    await expect(inputPage.getByText(text, { exact: false })).toHaveCount(0);
  }
  const intake = inputPage.getByRole("region", { name: "자료 분류와 처리" });
  await intake.locator('input[type="file"]').setInputFiles({ name: "retained.csv", mimeType: "text/csv", buffer: Buffer.from("item,value\nfixture,1") });
  await expect(intake.getByText("retained.csv", { exact: true })).toBeVisible();
  const materialsTab = inputPage.getByRole("tab", { name: "자료·원본" });
  const reviewTab = inputPage.getByRole("tab", { name: "사업수지 검토" });
  const review = inputPage.getByRole("region", { name: "사업수지 입력 검토" });
  await materialsTab.focus();
  await materialsTab.press("ArrowRight");
  await expect(reviewTab).toHaveAttribute("aria-selected", "true");
  await expect(reviewTab).toBeFocused();
  await expect(review.getByLabel("총매출 (억원)")).toHaveValue("1");
  await review.getByLabel("총매출 (억원)").fill("2");
  const initialReviewCalls = state.reviewCalls;
  state.status = 200;
  await inputPage.getByRole("button", { name: "자료 상태 다시 불러오기", exact: true }).click();
  const metrics = inputPage.getByRole("region", { name: "저장 자료 현황" });
  await expect(metrics.getByText("저장 문서", { exact: true }).locator("..")).toContainText("2건");
  await expect(inputPage.getByText(/일부 자료 상태만 확인했습니다/)).toBeVisible();
  await expect(metrics.getByText("보완 필요", { exact: true }).locator("..")).toContainText("미확인");
  await expect(review.getByLabel("총매출 (억원)")).toHaveValue("2");
  await materialsTab.click();
  await expect(inputPage.getByText("saved-one.pdf", { exact: true })).toBeVisible();
  await expect(intake.getByText("retained.csv", { exact: true })).toBeVisible();
  expect(state.reviewCalls).toBe(initialReviewCalls);
  expect(state.mutations).toEqual(["/api/backend/projects/context-recovery/intake/classify"]);
});

test("a successful complete empty context alone may display zero counts and empty sections", async ({ page }) => {
  const { state, inputPage } = await fixture(page, 200, emptyContext);
  const metrics = inputPage.getByRole("region", { name: "저장 자료 현황" });
  await expect(metrics.getByText("저장 문서", { exact: true }).locator("..")).toContainText("0건");
  await expect(inputPage.getByText(/아직 저장된 원본 문서가 없습니다/)).toBeVisible();
  await inputPage.getByRole("tab", { name: "사업수지 검토" }).click();
  await inputPage.getByText("AI 추출값 · 0건", { exact: true }).click();
  await expect(inputPage.getByText(/아직 저장된 파싱 후보값이 없습니다/)).toBeVisible();
  await inputPage.getByRole("tab", { name: "보완 작업" }).click();
  await expect(inputPage.getByText("현재 공통 자료 기준으로 즉시 표시할 보완 항목이 없습니다.", { exact: true })).toBeVisible();
  await inputPage.getByText("토지 자료 현황과 보완 실행", { exact: true }).click();
  await expect(inputPage.getByText("PNU 확보", { exact: true }).locator("..")).toContainText("산정 전");
  await expect(inputPage.getByRole("alert")).toHaveCount(0);
  const issues = page.getByRole("region", { name: "프로젝트 정보" }).getByText("열린 이슈", { exact: true }).locator("..");
  await expect(issues.locator("dd")).toHaveText("0");
  expect(state.mutations).toEqual([]);
});

test("partial or omitted sections do not become zero issues or a reassuring empty register", async ({ page }) => {
  const { inputPage } = await fixture(page, 200, partialContext);
  await expect(inputPage.getByText(/일부 자료 상태만 확인했습니다/)).toBeVisible();
  await expect(inputPage.getByRole("region", { name: "자료 분류와 처리" })).toBeVisible();
  await expect(inputPage.getByRole("button", { name: "추가 자료 파일 선택", exact: true })).toHaveAttribute("aria-disabled", "false");
  await inputPage.getByRole("tab", { name: "사업수지 검토" }).click();
  await expect(inputPage.getByRole("region", { name: "사업수지 입력 검토" })).toBeVisible();
  await inputPage.getByText("AI 추출값 · 미확인", { exact: true }).click();
  await expect(inputPage.getByText("파싱 후보값 목록을 확인하지 못했습니다.", { exact: true })).toBeVisible();
  await inputPage.getByRole("tab", { name: "보완 작업" }).click();
  await expect(inputPage.getByText("보완 필요 항목을 확인하지 못했습니다.", { exact: true })).toBeVisible();
  await expect(inputPage.getByText("현재 공통 자료 기준으로 즉시 표시할 보완 항목이 없습니다.", { exact: true })).toHaveCount(0);
  const issues = page.getByRole("region", { name: "프로젝트 정보" }).getByText("열린 이슈", { exact: true }).locator("..");
  await expect(issues.locator("dd")).toHaveText("미확인");
  await inputPage.getByText("토지 자료 현황과 보완 실행", { exact: true }).click();
  for (const label of ["자료 보완 계획 생성", "PNU 일괄 해석", "Market-Product Pod", "Feasibility-CF Pod"]) {
    await expect(inputPage.getByRole("button", { name: label, exact: true })).toBeDisabled();
  }
  await expect(inputPage.getByRole("link", { name: "토지·인허가 미리보기", exact: true })).toBeVisible();
});

test("the materials action fits the initial mobile viewport even with a partial workspace", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { inputPage } = await fixture(page, 200, partialContext);
  await expect(inputPage.getByText(/일부 자료 상태만 확인했습니다/)).toBeVisible();
  await expect(inputPage.getByRole("button", { name: "추가 자료 파일 선택", exact: true })).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("a Cash Flow financing link opens the matching review tab and retains the track", async ({ page }) => {
  const { inputPage } = await fixture(page, 200, partialContext, "?track=pf#cash-flow-financing-review");
  await expect(inputPage.getByRole("tab", { name: "사업수지 검토" })).toHaveAttribute("aria-selected", "true");
  await expect(inputPage.locator("#cash-flow-financing-review")).toBeVisible();
  await expect(page).toHaveURL(/\?track=pf&view=review#cash-flow-financing-review$/);
});
