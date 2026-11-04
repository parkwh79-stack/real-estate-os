import { expect, test, type Page } from "@playwright/test";

const projectId = "synthetic-land-reconciliation";

async function fixture(page: Page, options: {
  purchase?: number | null;
  legacy?: boolean;
  failure?: number;
  staleReview?: boolean;
  legalReview?: boolean;
  ledgerState?: "empty" | "unavailable" | "ready";
} = {}) {
  const purchase = options.purchase === undefined ? 42_000_000_000 : options.purchase;
  const source = (cell: string) => ({ source_document: "합성 수지.xlsx", sheet_name: "사업수지", cell });
  const writes: string[] = [];
  let review: Record<string, unknown> = options.staleReview ? { comment: "이전 계약 확인 메모", comparisonHash: "old-source" } : {};
  let legalSummary: Record<string, unknown> | null = null;
  const legalReview = {
    reviewId: "synthetic-legal-review",
    developabilityAnalysis: {
      permitRouteReview: {
        useClassification: "mixed_use",
        plannedDate: "2028-06-01",
        routes: [{ code: "housing", label: "주택건설사업계획승인" }, { code: "building", label: "건축허가·신고" }],
        approvalEvidenceStatus: "missing",
      },
      heightReview: {
        plannedHeightM: 81.5,
        candidateLimitM: 100,
        confirmedLimitM: null,
        parcelReviews: [{ parcelIndex: 1, pnu: "1111010100100010000", districtUnitPlan: "합성 지구단위계획구역", sourceLocator: { apiRowId: "parcel-1" } }],
      },
    },
  };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/\/$/, "");
    if (request.method() !== "GET") writes.push(path);
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = {
      authenticated: true, authSource: "bearer_token",
      user: { userId: "synthetic-reviewer", tenantId: "synthetic-tenant", role: "admin", status: "active" },
      permissions: ["project:read", "project:write", "project:review"],
    };
    else if (path.endsWith("/projects")) body = [{ project: { id: projectId, name: "합성 토지 대사" } }];
    else if (path.endsWith("/legal-pod/preview") && options.legalReview) {
      expect(request.method()).toBe("POST");
      body = { legalReview, previewOnly: true, previewHash: "b".repeat(64) };
    }
    else if (path.endsWith("/legal-pod/preview-summary") && options.legalReview) {
      if (request.method() === "POST") {
        expect(request.postDataJSON()).toEqual({ sections: ["permit"], parcelIndexes: [], expectedPreviewHash: "b".repeat(64) });
        legalSummary = { recordId: "synthetic-summary", summary: { permit: legalReview.developabilityAnalysis.permitRouteReview } };
        body = legalSummary;
      } else {
        body = { items: legalSummary ? [legalSummary] : [] };
      }
    }
    else if (path.endsWith("/land-cost-review") && request.method() === "PUT") {
      if (options.failure) { await route.fulfill({ status: options.failure, json: { detail: "저장하지 못했습니다. 자료와 권한을 확인하세요." } }); return; }
      const submitted = request.postDataJSON() as { comment: string; expectedComparisonHash: string };
      expect(submitted.expectedComparisonHash).toBe("a".repeat(64));
      review = { comment: submitted.comment, comparisonHash: submitted.expectedComparisonHash, reviewedBy: "server-reviewer",
        reviewedAt: "2026-10-14T03:00:00Z", reviewHistory: [{ comment: submitted.comment }] };
      body = { projectId, review };
    }
    else if (path.endsWith("/workspace/context")) body = {
      projectId, projectMemory: { projectName: "합성 토지 대사" },
      ...(options.ledgerState === "unavailable" ? { contextCompleteness: "partial", unavailableSections: ["landLedger", "landEvidence"] } : {}),
      pageData: {
        landLedger: options.ledgerState === "empty" ? {} : {
          summary: { parcelCount: 2, totalContractAmount: 41_374_000_000 },
          parcels: [1, 2].map((sequence) => ({ sequence, addressFragment: `합성동 ${sequence}`, contractGroupId: "joint", totalContractAmount: null,
            paymentReviewVersion: options.legacy ? undefined : "ledger-recorded-payments-v1",
            paymentStatus: options.legacy ? "fully_paid" : "payment_unknown",
            paymentSummary: { knownRecordedPaidAmount: sequence === 1 ? 0 : null, recordedPaidMissingCount: sequence === 1 ? 0 : 1,
              knownScheduledAmount: 100_000_000, scheduledMissingCount: 0 },
            paymentSource: { sheet: "합성 토지조서", rows: [sequence + 4] },
            ...(options.ledgerState === "ready" ? { pnu: `11110101001000${sequence}0000`, pnuResolution: { lotVerification: "verified" } } : {}),
          })),
          contractGroups: [{ contractGroupId: "joint", parcelSequences: [1, 2], totalContractAmount: 12_500_000_000,
            sourceLocator: { sourceName: "합성 토지조서.xlsx", sheet: "토지조서", range: "I7:I12" } }],
        },
        landCostReconciliation: {
          comparisonHash: "a".repeat(64), review, reviewMatchesCurrentSources: review.comparisonHash === "a".repeat(64),
          status: purchase === null ? "incomplete" : "needs_review", studyStatus: "candidate",
          workbookValidationStatus: "failed", requiresLedgerReimport: options.legacy ?? false,
          ledgerContract: { value: 41_374_000_000, sourceLocator: { sourceName: "합성 토지조서.xlsx", sheet: "토지조서", cell: "I28" } },
          purchaseBudget: { value: purchase, excel: { sheet_name: "사업수지", cell: "D19" }, provenance: source("D19") },
          inclusiveBudget: { value: 46_032_000_000, excel: { sheet_name: "사업수지", cell: "D26" }, provenance: source("D26") },
          ancillaryBudgetKrw: purchase === null ? null : 46_032_000_000 - purchase,
          purchaseMinusContractKrw: purchase === null ? null : purchase - 41_374_000_000,
          individualAmountMissingCount: 3,
        },
      },
    };
    else if (path.endsWith(`/projects/${projectId}`)) body = { project: { id: projectId, name: "합성 토지 대사" } };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto(`/project/${projectId}/land${options.ledgerState === "empty" ? "?track=fund" : ""}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "계약·수지 금액 대사" })).toBeVisible();
  return writes;
}

test("empty land ledger prioritizes uploading within the selected track", async ({ page }) => {
  const writes = await fixture(page, { ledgerState: "empty" });
  const nextAction = page.getByRole("region", { name: "다음 조치" });
  const upload = nextAction.getByRole("link", { name: "토지조서 추가" });
  await expect(upload).toBeInViewport();
  await expect(upload).toHaveAttribute("href", `/project/${projectId}/input-package?track=fund#intake-upload`);
  await expect(nextAction.getByText("등록 자료 없음")).toBeVisible();
  await expect(nextAction.getByText("— · 산정 전")).toBeVisible();
  await expect(nextAction.getByRole("button", { name: "PNU 확인", exact: true })).toHaveCount(0);
  await expect(nextAction.getByRole("button", { name: "토지·인허가 미리보기" })).toBeEnabled();
  expect(writes).toEqual([]);
});

test("unavailable ledger does not look empty or offer an enrichment write", async ({ page }) => {
  await fixture(page, { ledgerState: "unavailable" });
  const nextAction = page.getByRole("region", { name: "다음 조치" });
  await expect(nextAction.getByRole("button", { name: "토지 상태 다시 불러오기" })).toBeVisible();
  await expect(nextAction.getByText("조회 불가", { exact: true })).toBeVisible();
  await expect(nextAction.getByRole("link", { name: "토지조서 추가" })).toHaveCount(0);
  await expect(nextAction.getByRole("button", { name: "PNU 확인", exact: true })).toHaveCount(0);
});

test("preview clears on reload and explicit selection saves only the permit summary", async ({ page }) => {
  const writes = await fixture(page, { legalReview: true, ledgerState: "ready" });
  const launch = page.getByRole("button", { name: "토지·인허가 미리보기" });
  const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "토지·인허가 검토", exact: true }) });
  await expect(page.getByRole("region", { name: "다음 조치" }).getByText("2/2필지")).toBeVisible();
  await launch.click();
  await expect(panel.getByText("주택건설사업계획승인")).toBeVisible();
  await expect(panel.getByText("건축허가·신고")).toBeVisible();
  await expect(panel.getByText("발급된 승인·허가 문서 미확인")).toBeVisible();
  await expect(panel.getByText("확인된 허용 높이").locator("..")).toContainText("미확인");
  await expect(panel.getByText("검증 대기 높이 기준").locator("..")).toContainText("100m");
  await expect(panel.getByText("계획 높이", { exact: true }).locator("..")).toContainText("약 82m");
  await panel.getByText("필지별 지구단위계획 근거 1건").click();
  await expect(panel.getByRole("cell", { name: "합성 지구단위계획구역" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "선택 항목만 저장" })).toBeDisabled();
  await page.reload();
  await expect(panel.getByText("주택건설사업계획승인")).toHaveCount(0);
  expect(writes.map((path) => path.split("/").at(-1))).toEqual(["preview"]);

  await launch.click();
  await panel.getByRole("checkbox", { name: "인허가 경로" }).check();
  await panel.getByRole("button", { name: "선택 항목만 저장" }).click();
  await expect(panel.getByText("저장한 예비 검토 1")).toBeVisible();
  await page.reload();
  await panel.getByRole("button", { name: "저장한 요약 보기" }).click();
  await panel.getByText("저장한 예비 검토 1").click();
  await expect(panel.getByText("주택건설사업계획승인")).toBeVisible();
  await expect(panel.getByText("확인된 허용 높이")).toHaveCount(0);
  expect(writes.map((path) => path.split("/").at(-1))).toEqual(["preview", "preview", "preview-summary"]);
});

test("reopen saved contract and budget scopes without allocating or approving values", async ({ page }) => {
  const writes = await fixture(page);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "계약·수지 금액 대사" }) });
    await expect(panel.getByText("413.74억원", { exact: true })).toBeVisible();
    await expect(panel.getByText("420억원", { exact: true })).toBeVisible();
    await expect(panel.getByText("460.32억원", { exact: true })).toBeVisible();
    await expect(panel.getByText("40.32억원", { exact: true })).toBeVisible();
    await expect(panel.getByText("6.26억원", { exact: true })).toBeVisible();
    await expect(panel.getByText("합성 수지.xlsx · 사업수지 · D19", { exact: true })).toBeVisible();
    await expect(panel.getByText(/개인별 계약금액 3건/)).toBeVisible();
    await expect(panel.getByText(/수지 검토 초안의 원본값/)).toBeVisible();
    await expect(panel.getByText(/수지 원본의 수식·검증 오류/)).toBeVisible();
    await expect(page.getByText("공동계약 1: 125억원", { exact: true })).toBeVisible();
    const parcels = page.locator("section").filter({ has: page.getByRole("heading", { name: "필지별 검토" }) });
    await expect(parcels.getByText("공동계약 · 필지별 미배분", { exact: true })).toHaveCount(2);
    if (attempt === 0) await page.reload();
  }
  expect(writes).toEqual([]);
});

test("missing budget stays unconfirmed and requests the source", async ({ page }) => {
  await fixture(page, { purchase: null });
  await expect(page.getByText(/비교할 원본 금액이 부족합니다/)).toBeVisible();
  const row = page.getByText("차액 (순수 매입예산 − 계약 합계)", { exact: true }).locator("..");
  await expect(row.getByText("미확인", { exact: true })).toBeVisible();
});

test("real zero and negative difference remain visible", async ({ page }) => {
  await fixture(page, { purchase: 0 });
  const row = page.getByText("수지 순수 매입예산", { exact: true }).locator("..");
  await expect(row.getByText("0억원", { exact: true })).toBeVisible();
  await expect(page.getByText("-413.74억원", { exact: true })).toBeVisible();
});

test("older ledger parsing explicitly requests reimport", async ({ page }) => {
  await fixture(page, { legacy: true });
  await expect(page.getByText(/기존 토지조서는 공동계약 구분을 위해 다시 가져와야/)).toBeVisible();
  const parcel = page.getByRole("button", { name: /합성동 1.*상세 보기/ });
  await parcel.click();
  const drawer = page.getByRole("dialog", { name: "합성동 1" });
  await expect(drawer.getByText(/이전 지급 완료 표시는 사용하지 않습니다/)).toBeVisible();
  await expect(drawer.getByText("조서상 전액 기지급", { exact: true })).toHaveCount(0);
  await drawer.getByRole("button", { name: "필지 상세 닫기" }).click();
  await page.getByText("토지 기본 정보·저장된 증빙", { exact: true }).click();
  await expect(page.getByText("등기 소유자 대조").locator("..")).toContainText("— · 대조 전");
});

test("reopened payment review keeps unknown and explicit zero apart from scheduled amounts", async ({ page }) => {
  await fixture(page);
  await expect(page.getByRole("region", { name: "다음 조치" }).getByRole("button", { name: "PNU 확인", exact: true })).toBeVisible();
  for (let attempt = 0; attempt < 2; attempt += 1) {
    for (const [parcel, amount] of [[1, "0억원"], [2, "미확인"]] as const) {
      const row = page.getByRole("button", { name: new RegExp(`합성동 ${parcel}.*상세 보기`) });
      await row.focus();
      await page.keyboard.press("Enter");
      const drawer = page.getByRole("dialog", { name: `합성동 ${parcel}` });
      await expect(drawer.getByRole("button", { name: "필지 상세 닫기" })).toBeFocused();
      const payment = drawer.locator("section").filter({ has: page.getByRole("heading", { name: "조서상 지급 내역" }) });
      await expect(payment.getByText(amount, { exact: true })).toBeVisible();
      await expect(payment.getByText("1억원", { exact: true })).toBeVisible();
      await expect(payment.getByText(new RegExp(`합성 토지조서 · 원본 행 ${parcel + 4}`))).toBeVisible();
      if (parcel === 2) await expect(payment.getByText(/미기재: 기지급 1건/)).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(drawer).toBeHidden();
      await expect(row).toBeFocused();
    }
    if (attempt === 0) await page.reload();
  }
});

test("save reasoning then reopen it with the same source amounts", async ({ page }) => {
  const writes = await fixture(page);
  const editor = page.getByRole("textbox", { name: "토지비 검토 메모" });
  const save = page.getByRole("button", { name: "검토 메모 저장", exact: true });
  await expect(save).toBeDisabled();
  await editor.fill("예산 기준일과 계약 부대조건을 확인할 예정입니다.");
  await save.click();
  await expect(page.getByRole("status").filter({ hasText: "검토 메모를 저장했습니다." })).toBeVisible();
  await page.reload();
  await expect(editor).toHaveValue("예산 기준일과 계약 부대조건을 확인할 예정입니다.");
  await expect(page.getByText(/저장된 메모 1건/)).toBeVisible();
  expect(writes.filter((path) => path.endsWith("/land-cost-review"))).toHaveLength(1);
});

for (const status of [400, 403, 409, 500]) {
  test(`retain reasoning when save returns ${status}`, async ({ page }) => {
    await fixture(page, { failure: status });
    const editor = page.getByRole("textbox", { name: "토지비 검토 메모" });
    await editor.fill("확인 중인 사유를 보존합니다.");
    await page.getByRole("button", { name: "검토 메모 저장", exact: true }).click();
    await expect(page.getByRole("status").filter({ hasText: "저장하지 못했습니다." })).toBeVisible();
    await expect(editor).toHaveValue("확인 중인 사유를 보존합니다.");
  });
}

test("old-source note is visible but not silently reused for the current review", async ({ page }) => {
  await fixture(page, { staleReview: true });
  await expect(page.getByText(/이전 원본의 검토 메모입니다/)).toContainText("이전 계약 확인 메모");
  await expect(page.getByRole("textbox", { name: "토지비 검토 메모" })).toHaveValue("");
  await expect(page.getByRole("button", { name: "검토 메모 저장", exact: true })).toBeDisabled();
});
