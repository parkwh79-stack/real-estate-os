import { expect, test } from "@playwright/test";

test("market depth separates archived sources, missing evidence, planning estimates, and public reads", async ({ page }) => {
  const projectId = "market-evidence";
  let publicRequests = 0;
  let depthUnavailable = false;
  const writes: unknown[] = [];
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: {
      authenticated: true, user: { userId: "reviewer", tenantId: "tenant", role: "analyst", status: "active" },
      permissions: ["project:read", "project:analyze", "project:write"],
    } });
    if (path.endsWith("/workspace/context")) return route.fulfill({ json: {
      projectId, projectMemory: { projectName: "시장 근거 테스트" }, pageData: {},
    } });
    if (path.endsWith("/market-product-depth/analyze")) {
      if (depthUnavailable) return route.fulfill({ status: 503 });
      return route.fulfill({ json: {
        schemaVersion: "realytics-market-product-depth-v1",
        sourceProvenance: { archivedDocumentCount: 4, unspecified: 4, developerProvided: 0, salesAgentProvided: 0, publicData: 0 },
        absorptionModel: { rateBasis: "planning_benchmark", monthlyAbsorptionRate: 0.05, estimatedMonthsToSellout: 20 },
        aiRecommendedNextActions: { actions: [{ priority: "high", action: "시행사 분양가 근거를 확보해 주세요." }] },
      } });
    }
    if (path.endsWith("/marketability/analyze")) {
      expect(new URL(route.request().url()).searchParams.get("live_comparables")).toBe("true");
      expect(route.request().postDataJSON()).toEqual({});
      publicRequests += 1;
      return route.fulfill({ json: { publicLookup: publicRequests === 1
        ? { status: "unavailable", count: 0 }
        : { status: "available", count: 3, sourceName: "국토교통부", lawdCd: "11110", month: "202609", medianPerPy: 25000000 } } });
    }
    if (route.request().method() !== "GET") writes.push(route.request().postData());
    return route.fulfill({ json: {} });
  });

  await page.goto("/project/" + projectId + "/marketability?track=development", { waitUntil: "domcontentloaded" });
  const depth = page.getByRole("region", { name: "자료 제공주체 비교 · 시장성 심화" });
  const publicRead = depth.getByRole("region", { name: "공개 실거래 조회 결과" });
  await expect(depth.getByText(/보관 원본 4건/)).toBeVisible();
  await expect(depth.getByText("제공주체 미확인", { exact: true }).locator("..")).toContainText("4건");
  await expect(depth.getByText("시행사 제공", { exact: true }).locator("..")).toContainText("확인된 자료 없음");
  await expect(depth.getByText("자동 보강", { exact: true }).locator("..")).toContainText("미확인");
  await expect(depth.getByText(/완판 예상 기간을 산정하지 않았습니다/)).toBeVisible();
  await expect(depth.getByText(/월 5%/)).not.toBeVisible();
  await depth.getByText("계획 참고 가정 보기").click();
  await expect(depth.getByText(/일반 계획용 가정 · 월 5% · 가정상 20개월/)).toBeVisible();
  await expect(depth.getByRole("link", { name: "시장 자료 보완" })).toHaveAttribute("href", "/project/" + projectId + "/input-package?track=development#intake-upload");
  await expect(publicRead.getByRole("status")).toHaveText("공개 실거래는 아직 조회하지 않았습니다.");
  expect(publicRequests).toBe(0);

  await publicRead.getByRole("button", { name: "공개 실거래 조회" }).click();
  await expect(publicRead.getByRole("status")).toHaveText("공개 실거래 조회 연결을 사용할 수 없습니다.");
  await publicRead.getByRole("button", { name: "공개 실거래 조회" }).click();
  await expect(publicRead.getByRole("status")).toHaveText("국토부 공개 실거래 조회 완료");
  await expect(publicRead.getByText(/3건 · 중위값 2,500만\/평/)).toBeVisible();
  await expect(depth.getByText("저장된 공공자료", { exact: true }).locator("..")).toContainText("확인된 자료 없음");
  expect(writes).toEqual([]);

  depthUnavailable = true;
  await depth.getByRole("button", { name: "저장 근거 다시 확인" }).click();
  await expect(depth.getByRole("alert")).toHaveText(/저장된 시장 근거를 조회하지 못했습니다/);
  await expect(depth.getByText("시행사 제공", { exact: true })).toHaveCount(0);
});
