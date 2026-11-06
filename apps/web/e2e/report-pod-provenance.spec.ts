import { expect, test, type Page } from "@playwright/test";

test.setTimeout(90_000);
const PODS = ["land_legal", "market_product", "cost_schedule", "feasibility_cashflow", "pf_credit", "public_policy"];

async function fixture(page: Page, unavailable = false) {
  const calls: string[] = [];
  const findings = Array.from({ length: 12 }, (_, index) => ({
    findingId: `finding-${index}`, podKey: PODS[index % PODS.length], category: "missing_evidence",
    severity: "high", message: `저장 당시 보완 의견 ${index + 1}`, recommendedAction: "원본과 대조",
    sourceLocators: [{ documentName: "합성 검증자료.xlsx", sheet: "입력", cell: `A${index + 1}` }],
    reviewId: `original-review-${index}`, runId: "original-chief-run", sourceArtifactKeys: [`original-artifact-${index}`],
    artifactBindingStatus: index === 10 ? "unverified" : index === 11 ? "mismatch" : "matched",
  }));
  const metadata = { report_id: "pod-report-1", project_id: "pod-report", version: 1, title: "Pod 근거 검토 초안",
    status: "draft", created_at: "2026-10-15T01:00:00Z", metadata: { report_eligible: false } };
  const detail = { ...metadata, report_markdown: "# Pod 근거 검토 초안", report: {
    title: "Pod 근거 검토 초안", sections: { financialTerms: { content: "합성 금융조건" } },
    paragraphEvidenceLinks: {
      podReviewReadStatus: unavailable ? "unavailable" : "loaded",
      podReviewReadMessage: unavailable ? "Pod 검토 이력을 조회하지 못했습니다. 지적사항이 없다는 뜻은 아닙니다." : null,
      links: [{ paragraphId: "section:financialTerms", sectionKey: "financialTerms", title: "금융조건",
        podAiReviewFindings: unavailable ? [] : findings, linkedRecords: [], linkedRecordCount: 0 }],
    },
  } };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/$/, "");
    calls.push(path);
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = { authenticated: true,
      user: { userId: "reviewer", role: "admin", status: "active", tenantId: "synthetic" },
      session: { userId: "reviewer", tenantId: "synthetic" }, permissions: ["project:read", "report:export"] };
    else if (path.endsWith("/workspace/context")) body = { projectId: "pod-report", projectMemory: { projectName: "합성 보고서" }, pageData: {} };
    else if (path.endsWith("/report-templates")) body = { templates: [] };
    else if (path.endsWith("/orchestrations")) body = [];
    else if (path.endsWith("/deal-state-records")) body = { records: [] };
    else if (path.endsWith("/cash-flow/comparison")) body = { availableRuns: [], comparisonHash: "a".repeat(64), selection: null };
    else if (path.endsWith("/reports/pod-report-1")) body = detail;
    else if (path.endsWith("/reports")) body = [metadata];
    else if (path.endsWith("/projects/pod-report")) body = { project: { name: "합성 보고서" },
      chief_orchestration_state: { runId: "later-live-run" } };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/pod-report/report", { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("heading", { name: "문단별 저장 근거", exact: true }).locator("xpath=ancestor::section[1]");
  await expect(panel).toBeVisible({ timeout: 60_000 });
  return { panel, calls };
}

test("all saved Pod findings and source locators survive reopening without using live reviews", async ({ page }) => {
  const { panel, calls } = await fixture(page);
  await expect(panel.getByRole("listitem")).toHaveCount(12);
  for (const pod of PODS) await expect(panel).toContainText(pod);
  await expect(panel).toContainText("합성 검증자료.xlsx · 시트 입력 · 셀 A12");
  await panel.getByText("저장된 검토 식별정보", { exact: true }).first().click();
  await expect(panel).toContainText("original-chief-run");
  await expect(panel).toContainText("original-review-0");
  await expect(panel).not.toContainText("later-live-run");
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel.getByRole("listitem")).toHaveCount(12);
  await expect(panel).toContainText("저장 당시 보완 의견 12");
  expect(calls.some((path) => path.includes("pod-ai-review"))).toBe(false);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test("unbound and mismatched reviews are visibly distinguished from matching artifacts", async ({ page }) => {
  const { panel } = await fixture(page);
  await expect(panel).toContainText("보고서 생성 시점의 Pod 산출물과 연결됨");
  await expect(panel).toContainText("Pod 산출물 연결 여부 미확인");
  await expect(panel).toContainText("현재 Pod 산출물과 다른 과거 검토");
});

test("a captured review-store failure is shown instead of claiming there were no findings", async ({ page }) => {
  const { panel } = await fixture(page, true);
  await expect(panel.getByRole("alert")).toContainText("지적사항이 없다는 뜻은 아닙니다");
  await expect(panel.getByRole("listitem")).toHaveCount(0);
});
