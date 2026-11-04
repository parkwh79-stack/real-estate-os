import { expect, test, type Page } from "@playwright/test";

test.setTimeout(90_000);

const PROJECT_A = "risk-review-a";
const PROJECT_B = "risk-review-b";
const EXISTING_COMMENT = "대지면적 근거 확인 필요\n다음 할 일: 원본 입력 N11 확인";
const NEXT_COMMENT = "보완 사유: 원본 대지면적 누락\n다음 할 일: 담당자가 등기와 토지조서를 대조";
const SOURCE = { documentId: "synthetic-budget", documentName: "합성 사업수지.xlsx", sheet: "입력", cell: "N17" };

type ReviewEntry = { reviewStatus: string; reviewedBy: string; reviewedAt: string; comment?: string; reviewComment?: string };
type RiskRecord = {
  recordId: string; recordType: string; key: string; title: string; severity: string; description: string;
  sourceType: string; sourceLocator: typeof SOURCE; reviewStatus: string; comment?: string; reviewComment?: string;
  reviewedBy: string; reviewedAt: string; reviewHistory: ReviewEntry[];
};
type SaveCall = { projectId: string; recordId: string; method: string; body: unknown };
type Failure = { status: number; detail: Record<string, string> };

function risk(projectId: string, secondary = false): RiskRecord {
  const comment = secondary ? "다른 항목의 기존 승인 의견" : projectId === PROJECT_A ? EXISTING_COMMENT : "다른 프로젝트에 저장된 보완 내용";
  const reviewStatus = secondary ? "approved" : "needs_revision";
  return {
    recordId: secondary ? "unrelated-risk" : "land-area-risk", recordType: "risk_issue",
    key: secondary ? "PRESERVED_APPROVAL" : "LAND_AREA_MISSING",
    title: secondary ? "기존 별도 검토 항목" : projectId === PROJECT_A ? "대지면적 근거 확인" : "다른 사업 대지면적 확인",
    severity: secondary ? "low" : "high", description: "합성 검증 자료의 입력 근거를 확인해야 합니다.",
    sourceType: "feasibility_workbook", sourceLocator: structuredClone(SOURCE), reviewStatus, comment,
    reviewedBy: "verified-previous-reviewer", reviewedAt: "2026-10-14T00:00:00Z",
    reviewHistory: [{ reviewStatus, reviewedBy: "verified-previous-reviewer", reviewedAt: "2026-10-14T00:00:00Z", comment }],
  };
}

async function fixture(page: Page, options: { failure?: Failure; loadFailure?: Failure; gate?: Promise<void>; legacyReviewComment?: boolean } = {}) {
  const records: Record<string, RiskRecord[]> = {
    [PROJECT_A]: [risk(PROJECT_A), risk(PROJECT_A, true)],
    [PROJECT_B]: [risk(PROJECT_B), risk(PROJECT_B, true)],
  };
  if (options.legacyReviewComment) {
    const legacy = records[PROJECT_A]![0]!;
    legacy.reviewComment = legacy.comment;
    delete legacy.comment;
    legacy.reviewHistory = legacy.reviewHistory.map(({ comment, ...entry }) => ({ ...entry, reviewComment: comment }));
  }
  const calls: SaveCall[] = [];
  const unrelatedBefore = structuredClone(records[PROJECT_A]![1]);
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/\/$/, "");
    const projectId = /\/projects\/([^/]+)/.exec(path)?.[1] ?? PROJECT_A;
    let body: unknown = {};
    if (path.endsWith("/auth/me")) body = {
      authenticated: true, authSource: "bearer_token",
      user: { userId: "authenticated-reviewer", tenantId: "risk-tenant", role: "admin", status: "active" },
      session: { userId: "authenticated-reviewer", tenantId: "risk-tenant" },
      permissions: ["project:read", "project:write", "project:review"],
    };
    else if (path.endsWith("/projects")) body = [
      { project: { id: PROJECT_A, name: "보완 검토 A" } }, { project: { id: PROJECT_B, name: "보완 검토 B" } },
    ];
    else if (path.endsWith("/workspace/context")) body = {
      projectId, projectMemory: { projectName: projectId === PROJECT_A ? "보완 검토 A" : "보완 검토 B" }, pageData: {},
    };
    else if (path.endsWith("/deal-state-records")) {
      if (options.loadFailure) {
        await route.fulfill({ status: options.loadFailure.status, json: { detail: options.loadFailure.detail } }); return;
      }
      body = { projectId, records: structuredClone(records[projectId] ?? []) };
    }
    else if (path.endsWith("/calculation-runs")) body = { projectId, records: [] };
    else if (/\/deal-state-records\/[^/]+\/review$/.test(path) && request.method() === "PATCH") {
      const recordId = decodeURIComponent(path.split("/").at(-2)!);
      const submitted = request.postDataJSON() as { reviewStatus: string; comment: string };
      calls.push({ projectId, recordId, method: request.method(), body: submitted });
      await options.gate;
      if (options.failure) {
        await route.fulfill({ status: options.failure.status, json: { detail: options.failure.detail } }); return;
      }
      const current = records[projectId]!.find((row) => row.recordId === recordId)!;
      const entry = { reviewStatus: submitted.reviewStatus, comment: submitted.comment,
        reviewedBy: "server-verified-reviewer", reviewedAt: "2026-10-14T03:00:00Z" };
      Object.assign(current, entry, { reviewHistory: [...current.reviewHistory, entry] });
      body = { schemaVersion: "realytics-deal-state-record-review-v1", projectId, record: structuredClone(current) };
    } else if (path.endsWith(`/projects/${projectId}`)) body = { project: { id: projectId, name: "합성 보완 검토" } };
    await route.fulfill({ status: 200, json: body });
  });
  await page.goto(`/project/${PROJECT_A}/risk`, { waitUntil: "domcontentloaded", timeout: 60_000 });
  if (options.loadFailure) await expect(page.getByRole("main").getByRole("alert")).toBeVisible({ timeout: 60_000 });
  else await expect(page.getByRole("row").filter({ hasText: "대지면적 근거 확인" }).getByRole("button", { name: "보완요청", exact: true })).toBeEnabled({ timeout: 60_000 });
  return { calls, records, unrelatedBefore };
}

async function openEditor(page: Page, title = "대지면적 근거 확인") {
  await page.getByRole("row").filter({ hasText: title }).getByRole("button", { name: "보완요청", exact: true }).click();
  const editor = page.getByRole("region", { name: "보완요청 작성", exact: true });
  await expect(editor).toBeVisible();
  return editor;
}

test("save the requested work, preserve source and prior reviews, then reopen the persisted note", async ({ page }) => {
  const { calls, records, unrelatedBefore } = await fixture(page);
  const editor = await openEditor(page);
  const field = editor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true });
  await expect(field).toHaveValue(EXISTING_COMMENT);
  await field.fill(`  ${NEXT_COMMENT}  `);
  await editor.getByRole("button", { name: "보완요청 저장", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("보완요청을 저장했습니다");
  expect(calls).toEqual([{ projectId: PROJECT_A, recordId: "land-area-risk", method: "PATCH",
    body: { reviewStatus: "needs_revision", comment: NEXT_COMMENT } }]);
  const row = page.getByRole("row").filter({ hasText: "대지면적 근거 확인" });
  await expect(field).toHaveValue(NEXT_COMMENT);
  await expect(row).toContainText("합성 사업수지.xlsx");
  await expect(row).toContainText("N17");
  const history = editor.locator("details").filter({ has: page.locator("summary", { hasText: "저장된 검토 이력" }) });
  await history.locator("summary").click();
  await expect(history).toContainText("server-verified-reviewer");
  await expect(history).toContainText(EXISTING_COMMENT);
  await expect(history).toContainText(NEXT_COMMENT);
  expect(records[PROJECT_A]![0]!.sourceLocator).toEqual(SOURCE);
  expect(records[PROJECT_A]![1]).toEqual(unrelatedBefore);
  await page.reload({ waitUntil: "domcontentloaded" });
  const reopened = await openEditor(page);
  await expect(reopened.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true })).toHaveValue(NEXT_COMMENT);
  await reopened.locator("summary", { hasText: "저장된 검토 이력" }).click();
  await expect(reopened.locator("details")).toContainText("server-verified-reviewer");
  await expect(reopened.locator("details")).toContainText(NEXT_COMMENT);
  await expect(row).toContainText("N17");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});

test("whitespace cannot replace a saved request with an empty note", async ({ page }) => {
  const { calls } = await fixture(page);
  const editor = await openEditor(page);
  await editor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true }).fill(" \n\t  ");
  const save = editor.locator("button[type='submit']");
  if (await save.isEnabled()) await save.click();
  else await expect(save).toBeDisabled();
  await expect(editor).toContainText(/입력|빈|공백/);
  expect(calls).toHaveLength(0);
});

test("restore a legacy reviewComment-only request and retain its history when saving a new note", async ({ page }) => {
  const { calls, records } = await fixture(page, { legacyReviewComment: true });
  expect(records[PROJECT_A]![0]).not.toHaveProperty("comment");
  expect(records[PROJECT_A]![0]!.reviewHistory[0]).not.toHaveProperty("comment");
  const editor = await openEditor(page);
  const field = editor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true });
  await expect(field).toHaveValue(EXISTING_COMMENT);
  const history = editor.locator("details");
  await history.locator("summary", { hasText: "저장된 검토 이력" }).click();
  await expect(history).toContainText(EXISTING_COMMENT);
  await expect(history).toContainText("verified-previous-reviewer");
  await field.fill(NEXT_COMMENT);
  await editor.getByRole("button", { name: "보완요청 저장", exact: true }).click();
  await expect(editor.getByRole("status")).toContainText("보완요청을 저장했습니다");
  expect(calls).toEqual([{ projectId: PROJECT_A, recordId: "land-area-risk", method: "PATCH",
    body: { reviewStatus: "needs_revision", comment: NEXT_COMMENT } }]);
  await expect(field).toHaveValue(NEXT_COMMENT);
  await expect(history).toContainText(EXISTING_COMMENT);
  await expect(history).toContainText(NEXT_COMMENT);
  expect(records[PROJECT_A]![0]!.reviewHistory).toHaveLength(2);
  expect(records[PROJECT_A]![0]!.reviewHistory[0]!.reviewComment).toBe(EXISTING_COMMENT);
  expect(records[PROJECT_A]![0]!.reviewHistory[1]!.comment).toBe(NEXT_COMMENT);
  await page.reload({ waitUntil: "domcontentloaded" });
  const reopened = await openEditor(page);
  await expect(reopened.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true })).toHaveValue(NEXT_COMMENT);
  await reopened.locator("summary", { hasText: "저장된 검토 이력" }).click();
  await expect(reopened.locator("details")).toContainText(EXISTING_COMMENT);
  await expect(reopened.locator("details")).toContainText(NEXT_COMMENT);
});

const failures: Array<Failure & { expected: string }> = [
  { status: 403, detail: { error: "forbidden", requiredPermission: "project:review", role: "analyst", message: "보완요청 저장 권한이 없습니다." }, expected: "project:review" },
  { status: 409, detail: { error: "review_conflict", message: "다른 검토자가 변경했습니다. 다시 확인해 주세요." }, expected: "다른 검토자가 변경했습니다" },
  { status: 500, detail: { error: "review_save_failed", message: "검토 원장 저장에 실패했습니다. 잠시 후 다시 시도해 주세요." }, expected: "검토 원장 저장에 실패했습니다" },
];
for (const failure of failures) {
  test(`a structured ${failure.status} rejection preserves the draft and the saved record`, async ({ page }) => {
    const { calls, records } = await fixture(page, { failure });
    const editor = await openEditor(page);
    await editor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true }).fill(NEXT_COMMENT);
    await editor.getByRole("button", { name: "보완요청 저장", exact: true }).click();
    await expect(editor.getByRole("alert")).toContainText(failure.expected);
    await expect(editor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true })).toHaveValue(NEXT_COMMENT);
    await expect(editor.getByRole("button", { name: "보완요청 저장", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Risk 재평가", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "PF-Credit Pod 실행", exact: true })).toBeDisabled();
    await expect(page.getByRole("row").filter({ hasText: "기존 별도 검토 항목" }).getByRole("button", { name: "승인", exact: true })).toBeDisabled();
    expect(calls).toHaveLength(1);
    expect(records[PROJECT_A]![0]!.comment).toBe(EXISTING_COMMENT);
    expect(records[PROJECT_A]![0]!.reviewHistory).toHaveLength(1);
    await expect(page.getByRole("status")).toHaveCount(0);
    await editor.getByRole("button", { name: "닫기", exact: true }).click();
    await expect(page.getByRole("button", { name: "Risk 재평가", exact: true })).toBeEnabled();
  });
}

test("a pending save accepts one request and prevents duplicate submission", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const { calls } = await fixture(page, { gate });
  const editor = await openEditor(page);
  await editor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true }).fill(NEXT_COMMENT);
  const save = editor.locator("button[type='submit']");
  try {
    await save.dblclick();
    await expect.poll(() => calls.length).toBe(1);
    await expect(save).toBeDisabled();
    await expect(editor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true })).toHaveValue(NEXT_COMMENT);
  } finally { release(); }
  await expect(page.getByRole("status")).toContainText("보완요청을 저장했습니다");
  expect(calls).toHaveLength(1);
});

test("a late response after a client project change cannot replace the other project's saved review", async ({ page, viewport }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const { calls, records } = await fixture(page, { gate });
  const editor = await openEditor(page);
  await editor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true }).fill(NEXT_COMMENT);
  const response = page.waitForResponse((value) => value.request().method() === "PATCH" && value.url().endsWith("/land-area-risk/review"), { timeout: 60_000 });
  try {
    await editor.getByRole("button", { name: "보완요청 저장", exact: true }).click();
    await expect.poll(() => calls.length).toBe(1);
    const search = page.getByRole("combobox", { name: "프로젝트 검색", exact: true });
    await search.fill("보완 검토 B");
    await search.press("Enter");
    await page.getByRole("option", { name: /보완 검토 B/ }).click();
    await expect(page).toHaveURL(`/project/${PROJECT_B}`, { timeout: 30_000 });
    if ((viewport?.width ?? 1280) < 1024) {
      await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
    }
    const navigation = page.getByRole("navigation", { name: "주 메뉴", exact: true });
    await navigation.getByText("리스크·승인", { exact: true }).click();
    await navigation.getByRole("link", { name: "위험 항목 검토", exact: true }).click();
    await expect(page).toHaveURL(`/project/${PROJECT_B}/risk`, { timeout: 30_000 });
    await expect(page.getByRole("row").filter({ hasText: "다른 사업 대지면적 확인" })).toBeVisible();
  } finally { release(); }
  expect((await response).status()).toBe(200);
  const otherEditor = await openEditor(page, "다른 사업 대지면적 확인");
  await expect(otherEditor.getByRole("textbox", { name: "보완 사유·다음 할 일", exact: true })).toHaveValue("다른 프로젝트에 저장된 보완 내용");
  await expect(page.getByRole("status")).toHaveCount(0);
  expect(records[PROJECT_B]![0]!.reviewHistory).toHaveLength(1);
  expect(calls.map((call) => call.projectId)).toEqual([PROJECT_A]);
});

test("a failed risk record lookup displays the server error instead of presenting a successful empty load", async ({ page }) => {
  const { calls } = await fixture(page, { loadFailure: { status: 500,
    detail: { error: "risk_read_failed", message: "위험 원장 조회에 실패했습니다. 저장 자료는 유지됩니다." } } });
  await expect(page.getByRole("main").getByRole("alert")).toContainText("위험 원장 조회에 실패했습니다");
  await expect(page.getByRole("region", { name: "보완요청 작성", exact: true })).toHaveCount(0);
  expect(calls).toHaveLength(0);
});
