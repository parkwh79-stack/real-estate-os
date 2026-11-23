import { expect, test, type Page } from "@playwright/test";

test.setTimeout(90_000);
test.use({ actionTimeout: 12_000 });

async function verifyStatutoryCostWorkflow(page: Page, publicOnly: boolean) {
  const projectId = "statutory-demo";
  const baseRunId = "saved-cost-basis";
  const savedRunId = "statutory-review-run";
  const financialInputHash = "a".repeat(64);
  const document = { archiveId: "b".repeat(64), fileName: "학교용지부담금 고지서.pdf" };
  const businessEvent = { status: "actual", kind: "sale_notice_approval", eventDate: "2025-07-31",
    archiveId: document.archiveId, location: "1쪽 사업 승인일" };
  const lawBasis = {
    schemaVersion: "realytics-school-site-law-basis-v1", basisHash: "d".repeat(64),
    event: { status: "actual", kind: "sale_notice_approval", date: businessEvent.eventDate,
      sourceLocator: { ...document, location: businessEvent.location } },
    editionStatus: publicOnly ? "edition_unconfirmed" : "edition_verified", applicability: "unknown",
    edition: publicOnly ? null : { title: "학교용지 확보 등에 관한 특례법", officialVersionId: "test-edition",
      effectiveFrom: "2025-01-01", effectiveTo: null, contentHash: "sha256:" + "f".repeat(64),
      url: "https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=123", provisions: [] },
    publicReference: publicOnly ? { officialVersionId: "267343", effectiveFrom: "2025-06-21",
      url: "https://www.law.go.kr/LSW/lsInfoP.do?efYd=20250621&lsiSeq=267343",
      amendmentUrl: "https://www.law.go.kr/LSW/lsInfoP.do?efYd=20250621&lsiSeq=267343&viewCls=lsRvsDocInfoR" } : undefined,
  };
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
          legalBasis: lawBasis,
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
    } else if (url.pathname.endsWith("/statutory-cost-review/school-site-law-basis")) {
      expect(route.request().postDataJSON()).toEqual(businessEvent);
      body = lawBasis;
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
  await page.getByLabel("사건일 상태").selectOption("actual");
  await page.getByLabel("검토 기준 사건").selectOption(businessEvent.kind);
  await page.getByLabel("사업 사건일", { exact: true }).fill(businessEvent.eventDate);
  await page.getByLabel("사건 증빙 문서").selectOption(document.archiveId);
  await page.getByLabel("사건 증빙 위치").fill(businessEvent.location);
  await expect(page.getByRole("button", { name: "법정 비용 반영·계산 저장" })).toBeDisabled();
  await page.getByRole("button", { name: "사건일 법령 판본 조회" }).click();
  if (lawBasis.publicReference) {
    await expect(page.getByText(/해당 날짜의 공식 판본 미확인/)).toBeVisible();
    await expect(page.getByRole("link", { name: "원문 보기", exact: true })).toHaveAttribute("href", lawBasis.publicReference.url);
  } else {
    await expect(page.getByText(/해당 날짜의 공식 판본 확인/)).toBeVisible();
  }
  await page.getByRole("button", { name: "법정 비용 반영·계산 저장" }).click();

  await expect(header).toContainText("이 실행에 1건 반영");
  expect(exactReopens).toBeGreaterThan(0);
  expect(submissions).toEqual([{
    baseCalculationRunId: baseRunId,
    expectedFinancialInputHash: financialInputHash,
    schoolSiteEvent: { ...businessEvent, expectedBasisHash: lawBasis.basisHash },
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
  await expect(page.getByLabel("사업 사건일", { exact: true })).toHaveValue(businessEvent.eventDate);
  if (lawBasis.publicReference) {
    await expect(page.getByRole("link", { name: "개정문·부칙 보기" }).first()).toHaveAttribute("href", lawBasis.publicReference.amendmentUrl);
  } else {
    await expect(page.getByText(/판본 test-edition/).first()).toBeVisible();
  }

  await page.goto(`/project/${projectId}/cashflow`);
  await expect(page.getByRole("link", { name: "수식 Excel 다운로드" })).toHaveAttribute(
    "href", `/api/projects/${projectId}/cashflow/export?anchorCalculationRunId=${savedRunId}`,
  );
}

test("법정 비용을 검증 판본과 월차에 연결해 저장하고 다시 연다", async ({ page }) => {
  await verifyStatutoryCostWorkflow(page, false);
});

test("공개 시행본과 부칙을 같은 법정 비용 실행에서 다시 연다", async ({ page }) => {
  await verifyStatutoryCostWorkflow(page, true);
});
