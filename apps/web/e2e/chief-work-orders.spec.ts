import { expect, test } from "@playwright/test";

test.setTimeout(90_000);

test("saved agent assignments, material requests and execution evidence survive reload", async ({ page }) => {
  const plan = {
    projectId: "work-orders", sharedDealStateGraph: {}, agentPods: [],
    podRuns: [{ podKey: "market_product", status: "completed", progressPercent: 100 }],
    taskGraph: { workflowTrack: "development", workOrders: [
      { workOrderId: "w1", podKey: "market_product", toAgent: "MarketEvidenceAgent", reviewMode: "llm_evidence_aware_review",
        action: "collect_public_evidence", status: "executed", reason: "공개자료 수집 후 재검토 실행",
        requestedReason: "누락된 비교사례 확인", sourceReviewId: "saved-review-1", sourceArtifactKey: "saved-source-1", resultArtifactKey: "saved-result-1" },
      { workOrderId: "w2", podKey: "pf_credit", toAgent: "CreditReviewAgent", reviewMode: "deterministic_fallback_review",
        action: "request_material", status: "needs_material", reason: "실제 약정서 필요" },
      { workOrderId: "w3", podKey: "land_legal", toAgent: "LegalReviewAgent", reviewMode: "llm_evidence_aware_review",
        action: "rerun_pod", status: "queued", reason: "다음 실행에서 검토" },
    ], missingInputWorkPlan: [{ itemId: "loan", podKey: "pf_credit", agents: ["CreditReviewAgent"],
      description: "대출 약정 확인", acceptableSources: ["PF Term Sheet"], action: "request_material", limitation: "사용자 자료 필요" }] },
  };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = { authenticated: true, user: { userId: "u", role: "admin", status: "active", tenantId: "test" }, permissions: ["project:read"] };
    else if (path.endsWith("/chief-orchestration/plan")) body = plan;
    else if (path.includes("/pod-ai-reviews") || path.includes("/recalculation-events")) body = { records: [] };
    else if (path.includes("/legal-reviews") || path.includes("/permit-reviews")) body = { items: [] };
    else if (path.endsWith("/workspace/context")) body = { projectId: "work-orders", projectMemory: {}, pageData: {} };
    else if (path.endsWith("/projects/work-orders")) body = { project: { name: "합성 업무 배정 검증" } };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/work-orders/chief-orchestration", { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("region", { name: "추가 업무 배정" });
  await expect(panel).toBeVisible({ timeout: 60_000 });
  await panel.locator("summary").filter({ hasText: "재검토 실행 완료" }).click();
  await expect(panel).toContainText("LLM 검토 제안 → MarketEvidenceAgent");
  await expect(panel).toContainText("saved-result-1");
  await panel.locator("summary").filter({ hasText: "추가 자료 필요" }).click();
  await expect(panel).toContainText("규칙 기반 검토 → CreditReviewAgent");
  await expect(panel).toContainText("실제 약정서 필요");
  await panel.locator("summary").filter({ hasText: "Pod별 필요한 자료" }).click();
  await expect(panel).toContainText("PF Term Sheet");
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel).toContainText("재검토 실행 완료");
  await expect(panel).toContainText("실행 대기");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});
