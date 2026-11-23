import { expect, test, type Page } from "@playwright/test";
import { eokToWon, wonToEok, termsDraft, reviewedTermsPayload, type ReviewTerms, type WorkbookFinancingReview, type WorkbookFinancialReview, workbookSourceCell, workbookSourceCells, canLoadWorkbookFinancialField } from "../lib/feasibility-review";

test.setTimeout(90_000);

test("money survives won to eok to won without rounding", () => {
  for (const won of [0, 1, -1, 853951767703, -123456789, Number.MAX_SAFE_INTEGER]) {
    expect(eokToWon(wonToEok(won))).toBe(won);
  }
  expect(wonToEok(853951767703)).toBe("8539.51767703");
  for (const invalid of ["", "Infinity", "NaN", "1e3", "0.000000001", "1,000", "90071993"]) {
    expect(() => eokToWon(invalid)).toThrow();
  }
});

async function mockReview(page: Page, options: { conflict?: boolean; saveGate?: Promise<void>; initialTerms?: ReviewTerms; workbookReview?: WorkbookFinancingReview; financialReview?: WorkbookFinancialReview } = {}) {
  const source = { archiveId: "a".repeat(64), originalFileName: "검토자료.xlsx" };
  const state = {
    values: { total_revenue: 853951767703 } as Record<string, number>,
    fieldReviews: {} as Record<string, Record<string, unknown>>,
    terms: options.initialTerms ?? { schedule: {}, financing: [] } as ReviewTerms,
    termReviews: {} as Record<string, Record<string, unknown>>,
    workbookFinancingReview: options.workbookReview,
    workbookFinancialReview: options.financialReview,
    reviewHash: "first", sourceDocuments: [source],
  };
  const saves: Record<string, unknown>[] = [];
  const previews: { sheetName: string | null; cellReference: string | null }[] = [];
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith("/auth/me")) {
      body = { authenticated: true, user: { userId: "reviewer", role: "admin", status: "active", tenantId: "tenant-review" },
        session: { userId: "reviewer", tenantId: "tenant-review" }, permissions: ["project:read", "project:write", "assumption:override"], authSource: "bearer_token" };
    } else if (path.endsWith("/workspace/context")) {
      body = { projectId: "review-1", projectMemory: { projectName: "R1 검토" }, pageData: {} };
    } else if (path.endsWith("/feasibility-workbook/review")) {
      body = state;
    } else if (path.endsWith("/feasibility-workbook/apply-input")) {
      const submitted = route.request().postDataJSON();
      saves.push(submitted);
      await options.saveGate;
      if (options.conflict) {
        await route.fulfill({ status: 409, json: { detail: "저장된 입력이 변경되었습니다. 최신 값을 다시 불러온 뒤 수정하세요." } });
        return;
      }
      Object.assign(state.values, submitted.feasibility_input);
      for (const [key, review] of Object.entries(submitted.fieldReviews)) {
        state.fieldReviews[key] = { ...(review as Record<string, unknown>), sourceDocument: submitted.fieldSourceDocuments[key] ?? null };
      }
      if (submitted.terms?.schedule) Object.assign(state.terms.schedule, submitted.terms.schedule);
      if (submitted.terms?.financing) state.terms.financing = submitted.terms.financing;
      for (const [key, review] of Object.entries(submitted.termReviews ?? {})) {
        state.termReviews[key] = { ...(review as Record<string, unknown>), sourceDocument: submitted.termSourceDocuments?.[key] ?? null };
      }
      state.reviewHash = "saved";
      body = { reviewState: state };
    } else if (path.endsWith("/workbook/cells")) {
      const params = new URL(route.request().url()).searchParams;
      previews.push({ sheetName: params.get("sheet_name"), cellReference: params.get("cell_reference") });
      body = { schemaVersion: "realytics-workbook-evidence-preview-v1", sheetName: "손익", focusRange: "I2", previewRange: "I2",
        columns: [], rows: [{ rowNumber: 2, cells: [{ coordinate: "I2", displayValue: 853951767704, focused: true }] }], formulaPolicy: "display_only_never_executed" };
    }
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto("/project/review-1/input-package?view=review", { waitUntil: "domcontentloaded" });
  const panel = page.getByRole("region", { name: "사업수지 입력 검토" });
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("8539.51767703", { timeout: 60_000 });
  return { panel, saves, source, previews };
}

test("source loan candidates remain distinct from saved working terms and survive review/reopen", async ({ page }) => {
  const workbookReview: WorkbookFinancingReview = {
    sourceWorkbook: "검토자료.xlsx", sourceDocument: { archiveId: "a".repeat(64), originalFileName: "검토자료.xlsx" },
    terms: [
      { trancheId: "tr_a", name: "Tr.A", principalReconciled: true, fields: {
        amount: { value: 50_000_000_000, sourceCell: "입력!D130", status: "read" },
        interest_rate: { value: .055, sourceCell: "입력!D131", status: "read" },
        fee_rate: { value: .02, sourceCell: "입력!D132", status: "read" },
      } },
      { trancheId: "tr_b", name: "Tr.B", principalReconciled: true, fields: {
        amount: { value: 0, sourceCell: "입력!E130", status: "read" },
      } },
      { trancheId: "tr_c", name: "Tr.C", principalReconciled: false, fields: {
        amount: { value: null, sourceCell: "입력!F130", status: "missing" },
      } },
      { trancheId: "mortgage", name: "모기지", phase: "refinancing", contractVerified: false,
        unconfirmedTerms: ["약정 체결", "법적 상환순위", "만기와 기산일"], principalReconciled: true, fields: {
          amount: { value: 70_000_000_000, sourceCell: "'1. 사업수지'!AC9", status: "read" },
          term_months: { value: 120, sourceCell: "'0. 개요입력'!G40", status: "read" },
      } },
    ],
  };
  const { panel, saves, source } = await mockReview(page, { workbookReview, initialTerms: {
    schedule: {}, financing: [{ tranche_id: "saved", name: "검토 대출", priority: "senior",
      amount: 50_000_000_000, interest_rate: .04, fee_rate: null, term_months: null, repayment_source: null }],
  } });
  await panel.getByText("원본에서 읽은 대출 조건", { exact: true }).click();
  await expect(panel.getByLabel("Tr.A 원본 조건")).toContainText("금액: 500억원");
  await expect(panel.getByLabel("Tr.A 원본 조건")).toContainText("금리: 5.5%");
  await expect(panel.getByLabel("Tr.B 원본 조건")).toContainText("금액: 0억원");
  await expect(panel.getByLabel("Tr.C 원본 조건")).toContainText("금액: 미입력");
  await expect(panel.getByLabel("Tr.C 원본 조건")).toContainText("자동 적용하지 않습니다");
  await expect(panel.getByLabel("모기지 원본 조건")).toContainText("후속 대환자금 후보 · 초기 PF와 구분");
  await expect(panel.getByLabel("모기지 원본 조건")).toContainText("초기 PF 금액이나 아래 저장 대출에 자동 합산하지 않습니다");
  await expect(panel.getByLabel("모기지 원본 조건")).toContainText("체결 약정 미확인");
  await expect(panel.getByLabel("모기지 원본 조건")).toContainText("원본 기간 가정: 120개월");
  await expect(panel.getByLabel("모기지 원본 조건")).toContainText("'0. 개요입력'!G40 원본 보기");
  await expect(panel.getByLabel("대출 2 명칭", { exact: true })).toHaveCount(0);
  await expect(panel.getByLabel("대출 1 연 금리 (%)", { exact: true })).toHaveValue("4");
  expect(saves).toHaveLength(0);
  await panel.getByLabel("확인할 원본").selectOption(source.archiveId);
  await panel.getByLabel("대출 1 연 금리 (%)", { exact: true }).fill("5.5");
  await panel.getByLabel("대출 조건 입력 구분").selectOption("document_correction");
  await panel.getByLabel("대출 조건 출처 셀").fill("입력!D131");
  await panel.getByLabel("대출 조건 검토 이유").fill("사업수지 원본 금리 대조, 실제 약정 조건은 미확인");
  await panel.getByRole("button", { name: "검토 입력 저장" }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves).toHaveLength(1);
  await page.reload();
  await expect(panel.getByLabel("대출 1 연 금리 (%)", { exact: true })).toHaveValue("5.5");
  await expect(panel.getByLabel("대출 1 만기 (개월)", { exact: true })).toHaveValue("");
  await expect(panel.getByLabel("대출 조건 출처 셀")).toHaveValue("입력!D131");
  await panel.getByText("원본에서 읽은 대출 조건", { exact: true }).click();
  await expect(panel.getByLabel("Tr.B 원본 조건")).toContainText("금액: 0억원");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("correct a source value and save an assumption, then reopen the review", async ({ page }) => {
  const { panel, saves, source } = await mockReview(page);
  await panel.getByLabel("확인할 원본").selectOption(source.archiveId);
  await panel.getByLabel("총매출 입력 구분").selectOption("document_correction");
  await panel.getByLabel("총매출 출처 셀").fill("손익!I2");
  await panel.getByRole("button", { name: "원본 보기" }).click();
  await expect(panel.getByLabel("원본 셀 미리보기")).toContainText("853951767704");
  await panel.getByLabel("총매출 (억원)").fill("8539.51767704");
  await panel.getByLabel("총매출 검토 이유").fill("원본 대조 후 1원 수정");
  await panel.getByLabel("금융비용 (억원)").fill("1.23456789");
  await panel.getByLabel("금융비용 검토 이유").fill("자료 수령 전 분석용 가정");
  await panel.getByRole("button", { name: "검토 입력 저장" }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves).toHaveLength(1);
  expect(saves[0]?.feasibility_input).toEqual({ total_revenue: 853951767704, finance_cost: 123456789 });
  expect(saves[0]?.expectedReviewHash).toBe("first");
  await page.reload();
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("8539.51767704");
  await expect(panel.getByLabel("금융비용 (억원)")).toHaveValue("1.23456789");
  await expect(panel.getByLabel("금융비용 입력 구분")).toHaveValue("user_assumption");
  await expect(panel.getByLabel("총매출 출처 셀")).toHaveValue("손익!I2");
  await expect(panel.getByLabel("총사업비 (억원)")).toHaveValue("");
  await expect(panel.getByRole("link", { name: "사업수지 분석으로 이동" })).toHaveAttribute("href", "/project/review-1/feasibility");
});

test("invalid amounts cannot partially save the other edited fields", async ({ page }) => {
  const { panel, saves } = await mockReview(page);
  await panel.getByLabel("총매출 (억원)").fill("NaN");
  await panel.getByLabel("총매출 검토 이유").fill("잘못된 입력");
  await panel.getByRole("button", { name: "검토 입력 저장" }).click();
  await expect(panel.getByRole("alert")).toContainText("금액은 억원 단위 숫자로");
  expect(saves).toHaveLength(0);
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("NaN");
});

test("a stale save keeps the user's edit and asks for a reload", async ({ page }) => {
  const { panel, saves } = await mockReview(page, { conflict: true });
  await panel.getByLabel("총매출 (억원)").fill("9000");
  await panel.getByLabel("총매출 검토 이유").fill("사용자 가정");
  await panel.getByRole("button", { name: "검토 입력 저장" }).click();
  await expect(panel.getByRole("alert")).toContainText("저장된 입력이 변경되었습니다");
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("9000");
  expect(saves).toHaveLength(1);
});

for (const [destination, label] of [
  ["feasibility", "사업수지 분석으로 이동"],
  ["documents/review", "문서·출처 검토"],
] as const) {
  test(`save the changed inputs before continuing to ${destination}`, async ({ page }) => {
    let releaseSave!: () => void;
    const saveGate = new Promise<void>((resolve) => { releaseSave = resolve; });
    const { panel, saves, source } = await mockReview(page, { saveGate });
    await panel.getByLabel("확인할 원본").selectOption(source.archiveId);
    await panel.getByLabel("총매출 입력 구분").selectOption("document_correction");
    await panel.getByLabel("총매출 출처 셀").fill("손익!I2");
    await panel.getByLabel("총매출 (억원)").fill("8539.51767704");
    await panel.getByLabel("총매출 검토 이유").fill("원본 대조 후 1원 수정");
    await panel.getByLabel("금융비용 (억원)").fill("1.23456789");
    await panel.getByLabel("금융비용 검토 이유").fill("자료 수령 전 분석용 가정");
    try {
      await panel.getByRole("button", { name: `저장하고 ${label}`, exact: true }).dblclick();
      await expect.poll(() => saves.length).toBe(1);
      await expect(page).toHaveURL(/\/input-package\?view=review$/);
      await expect(panel.getByRole("button", { name: "검토 입력 저장", exact: true })).toBeDisabled();
      await expect(panel.getByRole("button", { name: "저장하고 사업수지 분석으로 이동", exact: true })).toBeDisabled();
      await expect(panel.getByRole("button", { name: "저장하고 문서·출처 검토", exact: true })).toBeDisabled();
      expect(saves[0]?.feasibility_input).toEqual({ total_revenue: 853951767704, finance_cost: 123456789 });
      expect(saves[0]?.expectedReviewHash).toBe("first");
      expect(saves[0]?.fieldSourceDocuments).toEqual({ total_revenue: source });
    } finally { releaseSave(); }
    await expect(page).toHaveURL(`/project/review-1/${destination}`, { timeout: 30_000 });
    expect(saves).toHaveLength(1);
    await page.goto("/project/review-1/input-package?view=review");
    await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("8539.51767704");
    await expect(panel.getByLabel("금융비용 검토 이유")).toHaveValue("자료 수령 전 분석용 가정");
    await expect(panel.getByLabel("총매출 출처 셀")).toHaveValue("손익!I2");
  });

  test(`a rejected save keeps edits on the page instead of navigating to ${destination}`, async ({ page }) => {
    const { panel, saves } = await mockReview(page, { conflict: true });
    await panel.getByLabel("총매출 (억원)").fill("9000");
    await panel.getByLabel("총매출 검토 이유").fill("저장 충돌 후에도 유지할 가정");
    await panel.getByRole("button", { name: `저장하고 ${label}`, exact: true }).click();
    await expect(panel.getByRole("alert")).toContainText("저장된 입력이 변경되었습니다");
    await expect(page).toHaveURL(/\/input-package\?view=review$/);
    await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("9000");
    await expect(panel.getByLabel("총매출 검토 이유")).toHaveValue("저장 충돌 후에도 유지할 가정");
    await expect(panel.getByRole("button", { name: `저장하고 ${label}`, exact: true })).toBeEnabled();
    expect(saves).toHaveLength(1);
  });
}

test("invalid or unexplained edits stay in the review before continuing", async ({ page }) => {
  const { panel, saves } = await mockReview(page);
  const next = panel.getByRole("button", { name: "저장하고 사업수지 분석으로 이동", exact: true });
  await panel.getByLabel("금융비용 (억원)").fill("NaN");
  await next.click();
  await expect(panel.getByRole("alert")).toContainText("금융비용: 수정 또는 가정의 이유를 입력하세요");
  await panel.getByLabel("금융비용 검토 이유").fill("입력 검증용 가정");
  await next.click();
  await expect(panel.getByRole("alert")).toContainText("금액은 억원 단위 숫자로");
  await expect(page).toHaveURL(/\/input-package\?view=review$/);
  await expect(panel.getByLabel("금융비용 (억원)")).toHaveValue("NaN");
  expect(saves).toHaveLength(0);
});

test("an unchanged review can continue without submitting inputs", async ({ page }) => {
  const { panel, saves } = await mockReview(page);
  await panel.getByRole("link", { name: "사업수지 분석으로 이동", exact: true }).click();
  await expect(page).toHaveURL("/project/review-1/feasibility", { timeout: 30_000 });
  expect(saves).toHaveLength(0);
});

test("a late save cannot navigate back after leaving the review", async ({ page }) => {
  let releaseSave!: () => void;
  const saveGate = new Promise<void>((resolve) => { releaseSave = resolve; });
  const { panel, saves } = await mockReview(page, { saveGate });
  await panel.getByLabel("총매출 (억원)").fill("9000");
  await panel.getByLabel("총매출 검토 이유").fill("이동 전에 요청한 가정 저장");
  const saved = page.waitForResponse((response) => response.url().endsWith("/feasibility-workbook/apply-input"));
  try {
    await panel.getByRole("button", { name: "저장하고 사업수지 분석으로 이동", exact: true }).click();
    await expect.poll(() => saves.length).toBe(1);
    await page.getByRole("region", { name: "프로젝트 정보" }).getByRole("link", { name: "R1 검토", exact: true }).click();
    await expect(page).toHaveURL("/project/review-1", { timeout: 30_000 });
    await expect(panel).toHaveCount(0);
  } finally { releaseSave(); }
  await (await saved).finished();
  // Include any navigation requests triggered by the completed save in settlement.
  await page.waitForLoadState("networkidle");
  await expect(page).toHaveURL("/project/review-1");
});

async function addLoan(panel: ReturnType<Page["getByRole"]>) {
  await panel.getByRole("button", { name: "대출 항목 추가", exact: true }).click();
  await panel.getByLabel("대출 1 명칭").fill("검토 대출 A");
  await panel.getByLabel("대출 1 순위").selectOption("senior");
  await panel.getByLabel("대출 조건 검토 이유").fill("약정 수령 전 분석 가정");
}

test("save amounts, calendar dates and source-reviewed loan terms together, then reopen", async ({ page }) => {
  const { panel, saves, source } = await mockReview(page);
  await panel.getByLabel("총매출 (억원)").fill("8539.51767704");
  await panel.getByLabel("총매출 검토 이유").fill("1원 대조 수정");
  await panel.getByLabel("착공일", { exact: true }).fill("2026-11-01");
  await panel.getByLabel("준공일", { exact: true }).fill("2028-10-31");
  await panel.getByLabel("사업기간 검토 이유").fill("시행사 제시 계획일");
  await addLoan(panel);
  await panel.getByLabel("대출 1 금액 (억원)").fill("1.23456789");
  await panel.getByLabel("대출 1 연 금리 (%)").fill("4.25");
  await panel.getByLabel("대출 1 수수료율 (%)").fill("0");
  await panel.getByLabel("대출 1 만기 (개월)").fill("24");
  await panel.getByLabel("대출 1 상환 재원").fill("분양대금");
  await panel.getByLabel("확인할 원본").selectOption(source.archiveId);
  await panel.getByLabel("대출 조건 입력 구분").selectOption("document_correction");
  await panel.getByLabel("대출 조건 출처 셀").fill("금융!B2");
  await panel.getByRole("button", { name: "대출 조건 원본 보기", exact: true }).click();
  await expect(panel.getByLabel("원본 셀 미리보기")).toBeVisible();
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves).toHaveLength(1);
  expect(saves[0]?.feasibility_input).toEqual({ total_revenue: 853951767704 });
  expect(saves[0]?.terms).toMatchObject({ schedule: { construction_start: "2026-11-01", completion: "2028-10-31" },
    financing: [{ name: "검토 대출 A", priority: "senior", amount: 123456789, interest_rate: 0.0425,
      fee_rate: 0, term_months: 24, repayment_source: "분양대금" }] });
  expect(saves[0]?.termSourceDocuments).toEqual({ financing: source });
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel.getByLabel("착공일", { exact: true })).toHaveValue("2026-11-01");
  await expect(panel.getByLabel("대출 1 금액 (억원)")).toHaveValue("1.23456789");
  await expect(panel.getByLabel("대출 1 연 금리 (%)")).toHaveValue("4.25");
  await expect(panel.getByLabel("대출 1 수수료율 (%)")).toHaveValue("0");
  await expect(panel.getByLabel("대출 조건 출처 셀")).toHaveValue("금융!B2");
  await expect(panel.getByLabel("사업기간 검토 이유")).toHaveValue("시행사 제시 계획일");
});

test("missing loan terms stay empty while zero survives, and deleting the last row persists", async ({ page }) => {
  const { panel, saves } = await mockReview(page);
  await addLoan(panel);
  await panel.getByLabel("대출 1 연 금리 (%)").fill("0");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves[0]?.terms).toMatchObject({ financing: [{ amount: null, interest_rate: 0, fee_rate: null,
    term_months: null, repayment_source: null }] });
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel.getByLabel("대출 1 연 금리 (%)")).toHaveValue("0");
  await expect(panel.getByLabel("대출 1 만기 (개월)")).toHaveValue("");
  await expect(panel.getByLabel("대출 1 수수료율 (%)")).toHaveValue("");
  await panel.getByRole("button", { name: "대출 1 삭제", exact: true }).click();
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves[1]?.terms).toEqual({ financing: [] });
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(panel.getByText("저장된 대출 조건이 없습니다.", { exact: true })).toBeVisible();
});

test("invalid date or unexplained terms block the whole amount and terms save", async ({ page }) => {
  const { panel, saves } = await mockReview(page);
  await panel.getByLabel("금융비용 (억원)").fill("1");
  await panel.getByLabel("금융비용 검토 이유").fill("가정 금액");
  await panel.getByLabel("준공일", { exact: true }).fill("2026-02-30");
  await panel.getByRole("button", { name: "저장하고 사업수지 분석으로 이동", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("사업기간: 수정 또는 가정의 이유");
  await panel.getByLabel("사업기간 검토 이유").fill("날짜 확인");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("실제 날짜");
  expect(saves).toHaveLength(0);
  await expect(page).toHaveURL(/\/input-package\?view=review$/);
  await expect(panel.getByLabel("금융비용 (억원)")).toHaveValue("1");
});

test("conflicting term edits keep dates, rates and reasons until explicitly reloaded", async ({ page }) => {
  const { panel, saves } = await mockReview(page, { conflict: true });
  await panel.getByLabel("착공일", { exact: true }).fill("2027-01-01");
  await panel.getByLabel("사업기간 검토 이유").fill("새 계획");
  await addLoan(panel);
  await panel.getByLabel("대출 1 연 금리 (%)").fill("0");
  await panel.getByRole("button", { name: "저장하고 사업수지 분석으로 이동", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("저장된 입력이 변경되었습니다");
  await expect(panel.getByLabel("착공일", { exact: true })).toHaveValue("2027-01-01");
  await expect(panel.getByLabel("대출 1 연 금리 (%)")).toHaveValue("0");
  await expect(panel.getByLabel("사업기간 검토 이유")).toHaveValue("새 계획");
  expect(saves).toHaveLength(1);
  await panel.getByRole("button", { name: "저장값 다시 불러오기 (수정 취소)", exact: true }).click();
  await expect(panel.getByLabel("착공일", { exact: true })).toHaveValue("");
  await expect(panel.getByText("저장된 대출 조건이 없습니다.", { exact: true })).toBeVisible();
});

test("only term edits also save before continuing and clear a stored date explicitly", async ({ page }) => {
  const { panel, saves } = await mockReview(page);
  await panel.getByLabel("착공일", { exact: true }).fill("2027-01-01");
  await panel.getByLabel("사업기간 검토 이유").fill("예정일 입력");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  await panel.getByLabel("착공일", { exact: true }).fill("");
  await panel.getByLabel("사업기간 검토 이유").fill("일정 재검토로 미입력 전환");
  await panel.getByRole("button", { name: "저장하고 사업수지 분석으로 이동", exact: true }).click();
  await expect(page).toHaveURL("/project/review-1/feasibility", { timeout: 30_000 });
  expect(saves[1]?.feasibility_input).toEqual({});
  expect(saves[1]?.terms).toEqual({ schedule: { construction_start: null } });
  await page.goto("/project/review-1/input-package?view=review", { waitUntil: "domcontentloaded" });
  await expect(panel.getByLabel("착공일", { exact: true })).toHaveValue("");
  await expect(panel.getByLabel("사업기간 검토 이유")).toHaveValue("일정 재검토로 미입력 전환");
});

test("terms serialization rejects invalid rates and tenors without inventing missing values", () => {
  const draft = termsDraft();
  draft.financingDirty = true;
  draft.reviews.financing.reason = "검증";
  draft.financing = [{ tranche_id: "a", name: "A", priority: "senior", amount: "", interest_rate: "0",
    fee_rate: "", term_months: "", repayment_source: "" }];
  expect(reviewedTermsPayload(draft)).toMatchObject({ terms: { financing: [{ amount: null, interest_rate: 0,
    fee_rate: null, term_months: null, repayment_source: null }] } });
  for (const value of ["-1", "101", "NaN", "Infinity", "1e1"]) {
    draft.financing[0]!.interest_rate = value;
    expect(() => reviewedTermsPayload(draft)).toThrow();
  }
  draft.financing[0]!.interest_rate = "4.25";
  for (const value of ["0", "-1", "1.5", "1201", "NaN"]) {
    draft.financing[0]!.term_months = value;
    expect(() => reviewedTermsPayload(draft)).toThrow();
  }
});

test("malformed legacy financing stays visible and cannot silently become missing values", async ({ page }) => {
  const initialTerms = { schedule: {}, financing: [{ tranche_id: "legacy", name: "원본 대출", priority: "bridge",
    amount: "123456789", interest_rate: "0.05", fee_rate: 0, term_months: true, repayment_source: "분양대금" }] } as unknown as ReviewTerms;
  const { panel, saves } = await mockReview(page, { initialTerms });
  await expect(panel.getByLabel("대출 1 금액 (억원)")).toHaveValue("원본값 확인 필요: 123456789");
  await expect(panel.getByLabel("대출 1 연 금리 (%)")).toHaveValue("원본값 확인 필요: 0.05");
  await expect(panel.getByLabel("대출 1 만기 (개월)")).toHaveValue("원본값 확인 필요: true");
  await panel.getByLabel("대출 1 명칭").fill("검토 중인 기존 대출");
  await panel.getByLabel("대출 조건 검토 이유").fill("원본 형식 확인");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("금액은 억원 단위 숫자로");
  expect(saves).toHaveLength(0);
  await panel.getByLabel("대출 1 금액 (억원)").fill("1.23456789");
  await panel.getByLabel("대출 1 연 금리 (%)").fill("5");
  await panel.getByLabel("대출 1 만기 (개월)").fill("");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves[0]?.terms).toMatchObject({ financing: [{ amount: 123456789, interest_rate: 0.05, term_months: null }] });
});

test("calendar order and legacy precision require an explicit correction", () => {
  const draft = termsDraft();
  draft.scheduleDirty = true;
  draft.changedSchedule = ["completion"];
  draft.reviews.schedule.reason = "일정 검토";
  draft.schedule = { construction_start: "2027-02-01", completion: "2027-01-01" };
  expect(() => reviewedTermsPayload(draft)).toThrow("준공일은 착공일보다 앞설 수 없습니다");
  draft.schedule.completion = "0000-01-01";
  expect(() => reviewedTermsPayload(draft)).toThrow("실제 날짜");
  const stored = termsDraft({ schedule: {}, financing: [{ tranche_id: "a", name: "기존", priority: "other",
    amount: 1, interest_rate: 1e-20, fee_rate: null, term_months: null, repayment_source: null }] });
  expect(stored.financing[0]?.interest_rate).toContain("원본값 확인 필요");
  stored.financingDirty = true;
  stored.reviews.financing.reason = "명시 확인 전";
  expect(() => reviewedTermsPayload(stored)).toThrow();
});


function mappedFinancialReview(): WorkbookFinancialReview {
  return { schemaVersion: "sangbong-financial-mapping-v1", profile: "sangbong-sale-feasibility", variant: "sale", status: "needs_review",
    sourceWorkbook: "상봉 사업수지.xlsx", sourceDocument: { archiveId: "a".repeat(64), originalFileName: "검토자료.xlsx" },
    sourceFields: {
      total_cost: { label: "총사업비", value: 123456789, rawValue: 123456.789, formula: "=SUM(K54:K80)",
        sourceCell: "'1. 사업수지'!K81", sourceUnit: "KRW_THOUSAND", normalizedUnit: "KRW", status: "needs_review", calculationState: "saved_formula_cache" },
      pf_amount: { label: "PF 총액", value: 0, rawValue: 0, sourceCell: "'0. 개요입력'!K10", normalizedUnit: "KRW", status: "read", calculationState: "literal" },
      finance_cost: { label: "금융비용", value: null, sourceCell: "'1. 사업수지'!K70", normalizedUnit: "KRW", status: "missing", calculationState: "unavailable" },
    }, limitations: ["저장 수식값이며 재계산 미검증입니다."] };
}

test("mapped workbook fields require explicit source review and retain source cells after save and reopen", async ({ page }) => {
  const { panel, saves, previews, source } = await mockReview(page, { financialReview: mappedFinancialReview() });
  const mapping = panel.getByLabel("원본 재무 항목 검토", { exact: true });
  await expect(mapping).toContainText("123,456,789원");
  await expect(mapping).toContainText("저장된 수식값 · 재계산 미검증");
  await expect(mapping.getByLabel("금융비용 불러오기 선택")).toBeDisabled();
  await expect(panel.getByLabel("총사업비 (억원)")).toHaveValue("");
  expect(saves).toHaveLength(0);
  await mapping.getByRole("button", { name: "'1. 사업수지'!K81 원본 보기", exact: true }).click();
  expect(previews).toEqual([{ sheetName: "1. 사업수지", cellReference: "K81" }]);
  await mapping.getByLabel("총사업비 불러오기 선택").check();
  await mapping.getByLabel("PF 총액 불러오기 선택").check();
  await mapping.getByRole("button", { name: "선택 항목을 검토 입력에 불러오기" }).click();
  await expect(panel.getByLabel("총사업비 (억원)")).toHaveValue("1.23456789");
  await expect(panel.getByLabel("PF 총액 (억원)")).toHaveValue("0");
  await expect(panel.getByLabel("금융비용 (억원)")).toHaveValue("");
  await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("8539.51767703");
  await expect(panel.getByLabel("총사업비 출처 셀")).toHaveValue("'1. 사업수지'!K81");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("총사업비: 수정 또는 가정의 이유를 입력하세요");
  expect(saves).toHaveLength(0);
  await panel.getByLabel("총사업비 검토 이유").fill("원본 천원 단위를 원으로 대조, 저장 수식값의 분석 가정");
  await panel.getByLabel("PF 총액 검토 이유").fill("원본의 명시적 0 확인");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves).toHaveLength(1);
  expect(saves[0]?.feasibility_input).toEqual({ total_cost: 123456789, pf_amount: 0 });
  expect(saves[0]?.fieldSourceDocuments).toEqual({ total_cost: source, pf_amount: source });
  await page.reload();
  await expect(panel.getByLabel("총사업비 (억원)")).toHaveValue("1.23456789");
  await expect(panel.getByLabel("총사업비 출처 셀")).toHaveValue("'1. 사업수지'!K81");
  await expect(panel.getByLabel("총사업비 검토 이유")).toHaveValue("원본 천원 단위를 원으로 대조, 저장 수식값의 분석 가정");
  await expect(panel.getByLabel("원본 재무 항목 검토", { exact: true })).toContainText("123,456,789원");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("loading mapped fields preserves unsaved financial edits and their reason", async ({ page }) => {
  const { panel, saves } = await mockReview(page, { financialReview: mappedFinancialReview() });
  await panel.getByLabel("총사업비 (억원)").fill("9000");
  await panel.getByLabel("총사업비 검토 이유").fill("유지할 사용자 검토안");
  const mapping = panel.getByLabel("원본 재무 항목 검토", { exact: true });
  await mapping.getByLabel("총사업비 불러오기 선택").check();
  await mapping.getByLabel("PF 총액 불러오기 선택").check();
  await mapping.getByRole("button", { name: "선택 항목을 검토 입력에 불러오기" }).click();
  await expect(panel.getByLabel("총사업비 (억원)")).toHaveValue("9000");
  await expect(panel.getByLabel("총사업비 검토 이유")).toHaveValue("유지할 사용자 검토안");
  await expect(panel.getByLabel("PF 총액 (억원)")).toHaveValue("0");
  await expect(panel.getByRole("status")).toContainText("저장 전 수정 중인 1개 항목은 유지했습니다");
  expect(saves).toHaveLength(0);
});

for (const applyEligible of [false, true]) {
  test(`rental gross receipts load only with explicit verified revenue basis: ${applyEligible}`, async ({ page }) => {
    const mapping = mappedFinancialReview();
    mapping.variant = "rental"; mapping.profile = "sangbong-private-rental-feasibility";
    mapping.sourceFields.total_revenue = { label: "총매출·임대수익(분양전환 정산 기준)", value: 346395641063,
      sourceCell: "'1. 사업수지'!K51", normalizedUnit: "KRW", status: "needs_review", calculationState: "saved_formula_cache", applyEligible,
      amountBasis: "gross_conversion_consideration_plus_rents" };
    const { panel, saves } = await mockReview(page, { financialReview: mapping });
    const select = panel.getByLabel("총매출·임대수익(분양전환 정산 기준) 불러오기 선택");
    if (applyEligible) {
      await expect(select).toBeEnabled();
      await select.check();
      await panel.getByRole("button", { name: "선택 항목을 검토 입력에 불러오기" }).click();
      await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("3463.95641063");
    } else {
      await expect(select).toBeDisabled();
      await expect(panel.getByLabel("총매출 (억원)")).toHaveValue("8539.51767703");
    }
    expect(saves).toHaveLength(0);
  });
}

test("source locator quotes and mapped loading guards retain exact cells and unknown values", () => {
  expect(workbookSourceCell("'0. 개요입력'!$B$5")).toEqual({ sheetName: "0. 개요입력", cellReference: "B5" });
  expect(workbookSourceCell("'O''Brien'!B5")).toEqual({ sheetName: "O'Brien", cellReference: "B5" });
  expect(workbookSourceCell("'0. 개요입력'!SUM(B5)")).toBeNull();
  expect(workbookSourceCell("'1. 사업수지'!K90, '1. 사업수지'!K100")).toBeNull();
  expect(workbookSourceCells("'A, B'!K90, 'O''Brien'!K100")).toEqual(["'A, B'!K90", "'O''Brien'!K100"]);
  const review = mappedFinancialReview();
  expect(canLoadWorkbookFinancialField(review, "pf_amount")).toBe(true);
  expect(canLoadWorkbookFinancialField(review, "finance_cost")).toBe(false);
  review.sourceFields.pf_amount!.value = Number.NaN;
  expect(canLoadWorkbookFinancialField(review, "pf_amount")).toBe(false);
});


test("composite cost and initial PF candidates retain every source cell through review and save", async ({ page }) => {
  const financial = mappedFinancialReview();
  const component = (value: number, cell: string) => ({ label: "합계 구성값", value, rawValue: value / 1000000,
    sourceCell: cell, normalizedUnit: "KRW", status: "read", calculationState: "saved_formula_cache" });
  financial.variant = "rental"; financial.profile = "sangbong-private-rental-feasibility";
  const costCells = ["'1. 사업수지'!K90", "'1. 사업수지'!K100"];
  const pfCells = ["'1. 사업수지'!AC7", "'1. 사업수지'!AC8"];
  financial.sourceFields.other_cost = { label: "기타사업비", value: 123456789, rawValue: "123456.789",
    sourceCell: costCells.join(", "), normalizedUnit: "KRW", sourceUnit: "KRW_THOUSAND", status: "read",
    calculationState: "derived_from_source_values", calculationExpression: "SUM(K90,K100)",
    components: [component(100000000, costCells[0]!), component(23456789, costCells[1]!)] };
  financial.sourceFields.pf_amount = { label: "초기 PF 약정 계획액", value: 70000000000, rawValue: [30000, 40000],
    sourceCell: pfCells.join(", "), normalizedUnit: "KRW", sourceUnit: "KRW_MILLION", status: "read",
    calculationState: "derived_from_source_candidates", applyEligible: true,
    sourceComponents: [component(30000000000, pfCells[0]!), component(40000000000, pfCells[1]!)] };
  const { panel, saves, previews } = await mockReview(page, { financialReview: financial });
  const mapping = panel.getByLabel("원본 재무 항목 검토", { exact: true });
  await expect(mapping).toContainText("원본 저장값 합계");
  await expect(mapping).toContainText("30,000 + 40,000 백만원");
  for (const cell of [...costCells, ...pfCells]) await expect(mapping.getByRole("button", { name: `${cell} 원본 보기`, exact: true })).toBeEnabled();
  await mapping.getByLabel("기타사업비 불러오기 선택").check();
  await mapping.getByLabel("초기 PF 약정 계획액 불러오기 선택").check();
  await mapping.getByRole("button", { name: "선택 항목을 검토 입력에 불러오기" }).click();
  await expect(panel.getByLabel("기타사업비 (억원)")).toHaveValue("1.23456789");
  await expect(panel.getByLabel("PF 총액 (억원)")).toHaveValue("700");
  await expect(panel.getByLabel("기타사업비 출처 셀")).toHaveValue(costCells.join(", "));
  await expect(panel.getByLabel("PF 총액 출처 셀")).toHaveValue(pfCells.join(", "));
  await panel.getByRole("button", { name: "원본 보기", exact: true }).last().click();
  const choices = panel.getByLabel("합계 원본 셀 선택", { exact: true });
  await expect(choices.getByRole("button")).toHaveCount(2);
  await choices.getByRole("button", { name: `${pfCells[1]} 원본 보기`, exact: true }).click();
  expect(previews.at(-1)).toEqual({ sheetName: "1. 사업수지", cellReference: "AC8" });
  expect(saves).toHaveLength(0);
  await panel.getByLabel("기타사업비 검토 이유").fill("구성 사업비의 원본 셀과 합계를 확인한 분석 가정");
  await panel.getByLabel("PF 총액 검토 이유").fill("초기 두 대출만 합산, 후속 모기지 제외");
  await panel.getByRole("button", { name: "검토 입력 저장", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("입력과 검토 이유를 저장했습니다");
  expect(saves[0]?.feasibility_input).toEqual({ other_cost: 123456789, pf_amount: 70000000000 });
  const reviews = saves[0]?.fieldReviews as Record<string, { sourceCell: string }>;
  expect(reviews.pf_amount?.sourceCell).toBe(pfCells.join(", "));
  await page.reload();
  await expect(panel.getByLabel("PF 총액 (억원)")).toHaveValue("700");
  await panel.getByRole("button", { name: "원본 보기", exact: true }).last().click();
  const reopenedChoices = panel.getByLabel("합계 원본 셀 선택", { exact: true });
  await expect(reopenedChoices.getByRole("button")).toHaveCount(2);
  await reopenedChoices.getByRole("button", { name: `${pfCells[0]} 원본 보기`, exact: true }).click();
  expect(previews.at(-1)).toEqual({ sheetName: "1. 사업수지", cellReference: "AC7" });
});
