import { expect, test } from "@playwright/test";

test.setTimeout(90_000);
test.use({ actionTimeout: 12_000 });

test("법정 비용을 근거와 월차에 연결해 저장하고 같은 실행을 다시 연다", async ({ page }) => {
  const projectId = "statutory-demo";
  const baseRunId = "saved-cost-basis";
  const savedRunId = "statutory-review-run";
  const financialInputHash = "a".repeat(64);
  const document = { archiveId: "b".repeat(64), fileName: "학교용지부담금 고지서.pdf" };
  const reviewedCost = {
    key: "school_site_charge",
    amountKrw: 25_000_000,
    amountKind: "notified",
    inclusion: "additional",
    location: "고지서 2쪽",
    reason: "고지액 확인 및 기존 원본 소계와 대사",
    paymentMonthIndex: 2,
  };
  const resultSnapshot = {
    summary: { maxMinus: 100_000_000, maxMinusMonth: 3 },
    monthlyCashFlow: [0, 1, 2].map((monthIndex) => ({ monthIndex, endingCash: 100_000_000 })),
  };
  const baseRun = { calculationRunId: baseRunId, engineKey: "cash_flow_engine", resultSnapshot };
  const anotherRun = { ...baseRun, calculationRunId: "latest-other-run" };
  const savedRun = {
    calculationRunId: savedRunId,
    engineKey: "cash_flow_engine",
    resultSnapshot,
    inputSnapshot: {
      statutoryCostSelection: {
        schemaVersion: "realytics-statutory-cost-selection-v1",
        baseCalculationRunId: baseRunId,
        additionalCostKrw: reviewedCost.amountKrw,
        items: [{
          ...reviewedCost,
          label: "학교용지부담금",
          sourceLocator: { ...document, location: reviewedCost.location },
        }],
      },
    },
  };
  const submissions: unknown[] = [];
  let exactReopens = 0;
  let listShowsSavedRun = false;
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname.endsWith("/auth/me")) {
      body = {
        authenticated: true,
        user: { userId: "analyst", role: "analyst", status: "active", tenantId: "demo" },
        session: { userId: "analyst", tenantId: "demo" },
        permissions: ["project:read", "project:analyze"],
      };
    } else if (url.pathname.endsWith("/workspace/context")) {
      body = { projectId, projectMemory: { projectName: "법정 비용 검증" }, pageData: {} };
    } else if (url.pathname.endsWith("/calculation-runs/" + savedRunId)) {
      exactReopens += 1;
      body = savedRun;
    } else if (url.pathname.endsWith("/calculation-runs")) {
      body = { records: url.searchParams.get("engine_key") === "cash_flow_engine"
        ? [...(listShowsSavedRun ? [savedRun] : []), anotherRun, baseRun]
        : [] };
    } else if (url.pathname.endsWith("/statutory-cost-review")) {
      body = {
        items: [{
          key: reviewedCost.key,
          label: "학교용지부담금",
          status: "unknown",
          amountKrw: null,
          missingInputs: ["부과 대상 분양가격"],
          evidence: [],
        }, {
          key: "building_artwork",
          label: "건축물 미술작품 설치·출연",
          status: "unknown",
          amountKrw: null,
          missingInputs: ["관할 기준 건축비"],
          evidence: [],
        }],
        basisRuns: [{
          calculationRunId: anotherRun.calculationRunId,
          financialInputHash: "c".repeat(64),
          createdAt: "2026-11-18T02:00:00Z",
          totalProjectCost: 350_000_000,
          lastMonthIndex: 2,
        }, {
          calculationRunId: baseRunId,
          financialInputHash,
          createdAt: "2026-11-18T01:00:00Z",
          totalProjectCost: 300_000_000,
          lastMonthIndex: 2,
        }],
        documents: [document],
      };
    } else if (url.pathname.endsWith("/statutory-cost-review/calculate")) {
      submissions.push(route.request().postDataJSON());
      body = { calculationRunId: savedRunId };
    }
    await route.fulfill({ json: body });
  });

  await page.goto(`/project/${projectId}/feasibility`);
  const header = page.locator("summary").filter({ hasText: /^법정 비용 검토/ });
  await header.click();
  const schoolArticle = page.locator("article").filter({ has: page.getByRole("heading", { name: "학교용지부담금" }) });
  await expect(schoolArticle).toContainText("산정액 미확인");
  const basis = page.getByLabel("기준 현금흐름");
  await expect(basis).toHaveValue(anotherRun.calculationRunId);
  await basis.selectOption(baseRunId);
  await page.getByRole("checkbox", { name: "학교용지부담금 채택 검토" }).check();
  await page.getByLabel("학교용지부담금 금액(원)").fill(String(reviewedCost.amountKrw));
  await page.getByRole("combobox", { name: /^금액 구분/ }).selectOption(reviewedCost.amountKind);
  await page.getByLabel("기준 사업비 반영").selectOption(reviewedCost.inclusion);
  await page.getByLabel(/납부 월차/).fill(String(reviewedCost.paymentMonthIndex));
  await page.getByRole("combobox", { name: /^근거 문서/ }).selectOption(document.archiveId);
  await page.getByLabel("문서 위치").fill(reviewedCost.location);
  await page.getByLabel("포함 여부를 확인한 근거").fill(reviewedCost.reason);
  await page.getByRole("button", { name: "법정 비용 반영·계산 저장" }).click();

  await expect(header).toContainText("이 실행에 1건 반영");
  expect(exactReopens).toBeGreaterThan(0);
  expect(submissions).toEqual([{
    baseCalculationRunId: baseRunId,
    expectedFinancialInputHash: financialInputHash,
    items: [{ ...reviewedCost, archiveId: document.archiveId }],
  }]);

  listShowsSavedRun = true;
  await page.reload();
  await header.click();
  await expect(page.getByText("이 실행의 법정 비용 · 추가 25,000,000원")).toBeVisible();
  await expect(page.getByText(/학교용지부담금 · 25,000,000원 · 고지액 · M2 추가/)).toContainText(document.fileName);
  await expect(basis).toHaveValue(baseRunId);
  await expect(page.getByRole("combobox", { name: /^근거 문서/ })).toHaveValue(document.archiveId);
  await expect(page.getByLabel(/납부 월차/)).toHaveValue("2");

  await page.goto(`/project/${projectId}/cashflow`);
  await expect(page.getByRole("link", { name: "수식 Excel 다운로드" })).toHaveAttribute(
    "href", `/api/projects/${projectId}/cashflow/export?anchorCalculationRunId=${savedRunId}`,
  );
});
