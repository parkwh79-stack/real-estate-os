import { expect, test, type Page } from "@playwright/test";

const archiveId = "a".repeat(64);
const hash = "b".repeat(64);
const pageResult = (pageNumber: number, overrides: Record<string, unknown> = {}) => ({
  pageNumber, status: "completed", sourceKind: "ocr", text: `검토 원문 ${pageNumber}페이지`, textSha256: hash,
  blocks: [], width: 595, height: 842, rotation: 0, coordinateSpace: "pdf_points_top_left", confidence: null,
  ...overrides,
});
const envelope = (pages: unknown[], extra: Record<string, unknown> = {}) => ({
  schemaVersion: "realytics-local-pdf-reading-v1", fileSha256: archiveId,
  readerFingerprint: hash, projectId: "reading-project", archiveId, pages,
  analysisOnly: true, reportEligible: false, factPromotionAllowed: false, approvalEligible: false,
  pageCount: 2, pendingOcrPages: [], completedOcrPages: [], failedPages: [], ...extra,
});

async function fixture(page: Page, withFactDocument = false, query = "") {
  const state = {
    gets: [] as number[], posts: [] as unknown[], mutations: [] as string[],
    archiveRequests: [] as string[],
    cached: new Map<number, unknown>(),
    postResult: (pageNumber: number): unknown => envelope([pageResult(pageNumber)]),
    postGate: Promise.resolve(),
  };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const archive = path.match(/\/source-documents\/([^/]+)/)?.[1];
    if (archive) state.archiveRequests.push(archive);
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/auth/me")) return reply({
      authenticated: true, user: { userId: "reading-user", tenantId: "reading-tenant", role: "viewer", displayName: "원본 검토자" },
      session: { userId: "reading-user", tenantId: "reading-tenant" }, permissions: ["project:read", "evidence:read"], authSource: "bearer_token",
    });
    if (path.endsWith("/auth/ai-credentials/current")) return reply({ revoked: true });
    if (path.endsWith("/projects") || path.endsWith("/projects/")) return reply([{ project: { id: "reading-project", name: "문서 읽기 검토" } }]);
    if (path.endsWith("/workspace/context")) return reply({ projectId: "reading-project", projectMemory: { projectName: "문서 읽기 검토" } });
    if (path.endsWith("/workspace/document-review")) return reply({
      schemaVersion: "realytics-document-review-workspace-v1", viewModelVersion: "document-review-workspace-2026-07-27",
      projectId: "reading-project", snapshotId: "reading-view", snapshotHash: hash,
      sourceDocuments: [
        { archiveId, originalFileName: "검토 원본.pdf", contentType: "application/pdf", archiveIntegrityStatus: "verified", uploadedAt: "2026-11-04T00:00:00Z" },
        ...(withFactDocument ? [{ archiveId: "c".repeat(64), originalFileName: "다른 원본.pdf", contentType: "application/pdf", uploadedAt: "2026-01-01T00:00:00Z" }] : []),
      ],
      runs: [], packages: withFactDocument ? [{
        parsingRun: { parsingRunId: "run-other", documentVersionId: "version-other", executionStatus: "completed" },
        documentVersion: { documentVersionId: "version-other", archiveId: "c".repeat(64), fileName: "다른 원본.pdf", mediaType: "application/pdf" },
        candidates: [{
          candidateId: "candidate-other", documentVersionId: "version-other", fieldKey: "feasibility.total_project_cost",
          normalizedValue: 100, unit: "KRW", reviewStatus: "pending", validationStatus: "passed",
          evidence: [{ documentVersionId: "version-other", fileSha256: "c".repeat(64), pageNumber: 1, bbox: [10, 20, 100, 200], coordinateSpace: "pdf_points_top_left" }],
        }], conflicts: [], reviewDecisions: [], canonicalSelections: [], approvedInputSnapshots: [],
      }] : [], glossaryCandidates: [], reviewQueue: [], snapshotDiffs: [], warnings: [],
    });
    if (/\/pdf\/pages\/\d+\.png$/.test(path)) return route.fulfill({
      status: 200, contentType: "image/png",
      headers: { "X-REALYTICS-Page-Width": "595", "X-REALYTICS-Page-Height": "842", "X-REALYTICS-Page-Count": "2", "X-REALYTICS-Coordinate-Space": "pdf_points_top_left" },
      body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jzWQAAAAASUVORK5CYII=", "base64"),
    });
    const cachedPage = path.match(/\/reading\/pages\/(\d+)$/);
    if (cachedPage) {
      const number = Number(cachedPage[1]);
      state.gets.push(number);
      return reply(state.cached.get(number) ?? envelope([], { status: "not_generated", pageCount: null, coverageAssessed: false }));
    }
    if (path.endsWith("/reading") && request.method() === "POST") {
      const body = request.postDataJSON();
      state.posts.push(body);
      const result = state.postResult(body.pageNumbers[0]);
      await state.postGate;
      // Closing the reading panel intentionally aborts this browser request.
      await reply(result).catch(() => undefined);
      return;
    }
    if (!["GET", "HEAD"].includes(request.method())) state.mutations.push(path);
    return reply({ detail: "not_mocked" }, 404);
  });
  await page.goto(`/project/reading-project/documents/review${query}`);
  await expect(page.getByRole("img", { name: "PDF 1페이지", exact: true })).toBeVisible();
  return state;
}

test("archive links select only listed documents and preserve a manual choice on refresh", async ({ page }) => {
  const linkedArchive = "c".repeat(64);
  const unavailableArchive = "f".repeat(64);
  const state = await fixture(page, true, `?archiveId=${linkedArchive.toUpperCase()}&track=pf`);
  const navigator = page.getByRole("complementary", { name: "문서 및 위치 탐색", exact: true });
  const defaultDocument = navigator.getByRole("button", { name: /검토 원본.pdf/ });
  const linkedDocument = navigator.getByRole("button", { name: /다른 원본.pdf/ });
  await expect(linkedDocument).toHaveAttribute("aria-pressed", "true");
  expect(new Set(state.archiveRequests)).toEqual(new Set([linkedArchive]));
  await expect(page.getByRole("link", { name: "자료 입력 패키지로 돌아가기" })).toHaveAttribute("href", "/project/reading-project/input-package?track=pf");

  await defaultDocument.click();
  const refresh = page.getByRole("button", { name: "최신 원장 새로고침" });
  await Promise.all([
    page.waitForResponse((response) => response.url().endsWith("/workspace/document-review")),
    refresh.click(),
  ]);
  await expect(refresh).toBeEnabled();
  await expect(defaultDocument).toHaveAttribute("aria-pressed", "true");

  await page.goto(`/project/reading-project/documents/review?archiveId=${unavailableArchive}`);
  await expect(page.getByText("이 프로젝트의 문서 목록에서 링크된 문서를 찾을 수 없습니다.")).toBeVisible();
  await expect(defaultDocument).toHaveAttribute("aria-pressed", "true");
  expect(state.archiveRequests).not.toContain(unavailableArchive);

  await page.goto(`/project/reading-project/documents/review?archiveId=${linkedArchive}&archiveId=${unavailableArchive}`);
  await expect(page.getByText("문서 링크 형식이 올바르지 않습니다. 목록에서 검토할 문서를 선택하세요.")).toBeVisible();
  await expect(defaultDocument).toHaveAttribute("aria-pressed", "true");
  expect(state.archiveRequests).not.toContain(unavailableArchive);
});

test("a source-only PDF reads the selected page on request without changing calculation or review data", async ({ page }) => {
  const state = await fixture(page);
  expect(state.posts).toEqual([]);
  expect(state.gets).toEqual([]);
  await page.getByRole("button", { name: "다음 PDF 페이지", exact: true }).click();
  await expect(page.getByRole("img", { name: "PDF 2페이지", exact: true })).toBeVisible();
  expect(state.posts).toEqual([]);
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  const reading = page.getByRole("region", { name: "읽은 글자 · 2페이지", exact: true });
  await expect(reading.getByText("검토 원문 2페이지", { exact: true })).toBeVisible();
  await expect(reading.getByText("이미지 글자 인식(OCR)", { exact: true })).toBeVisible();
  await expect(reading.getByText("검토 필요", { exact: true })).toBeVisible();
  await expect(reading.getByText(/계산·보고서에 자동 반영되지 않습니다/)).toBeVisible();
  expect(state.gets).toEqual([2]);
  expect(state.posts).toEqual([{ pageNumbers: [2] }]);
  expect(state.mutations).toEqual([]);
});

test("a cached native page reopens without generating OCR or displaying a confidence number", async ({ page }) => {
  const state = await fixture(page);
  state.cached.set(1, envelope([pageResult(1, { sourceKind: "native", text: "원본에 저장된 글자 5.5%" })]));
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  const reading = page.getByRole("region", { name: "읽은 글자 · 1페이지", exact: true });
  await expect(reading.getByText("PDF 원문", { exact: true })).toBeVisible();
  await expect(reading.getByText("원본에 저장된 글자 5.5%", { exact: true })).toBeVisible();
  await expect(reading.getByText(/신뢰도|confidence/)).toHaveCount(0);
  await reading.getByRole("button", { name: "읽은 글자 닫기", exact: true }).click();
  await expect(reading).toHaveCount(0);
  await expect(page.getByRole("button", { name: "글자 읽기", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  await expect(reading.getByText("PDF 원문", { exact: true })).toBeVisible();
  expect(state.posts).toEqual([]);
  expect(state.gets).toEqual([1, 1]);
});

test("an unrelated document fact cannot block source reading and its BBox stays on the matching page", async ({ page }) => {
  const state = await fixture(page, true);
  await expect(page.getByRole("button", { name: "글자 읽기", exact: true })).toBeVisible();
  await expect(page.getByText("locator와 선택 원본의 lineage가 일치하지 않습니다.", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /근거 BBox/ })).toHaveCount(0);
  await page.getByRole("complementary", { name: "문서 및 위치 탐색", exact: true }).getByRole("button", { name: /다른 원본.pdf/ }).click();
  await expect(page.getByRole("button", { name: /근거 BBox/ })).toBeVisible();
  await page.getByRole("button", { name: "다음 PDF 페이지", exact: true }).click();
  await expect(page.getByRole("img", { name: "PDF 2페이지", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /근거 BBox/ })).toHaveCount(0);
  await page.getByRole("button", { name: "이전 PDF 페이지", exact: true }).click();
  await expect(page.getByRole("button", { name: /근거 BBox/ })).toBeVisible();
  expect(state.posts).toEqual([]);
});

test("a cache write failure shows the successful text without promising it was saved", async ({ page }) => {
  const state = await fixture(page);
  state.postResult = (number) => envelope([pageResult(number)], {
    storedPages: [], unstoredPages: [number], cacheWarnings: [{ pageNumber: number, errorCode: "cache_write_failed" }],
  });
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  const reading = page.getByRole("region", { name: "읽은 글자 · 1페이지", exact: true });
  await expect(reading.getByText("검토 원문 1페이지", { exact: true })).toBeVisible();
  await expect(reading.getByRole("status")).toContainText("현재 읽기 결과는 저장되지 않았습니다");
  expect(state.posts).toHaveLength(1);
  expect(state.mutations).toEqual([]);
});

for (const [status, errorCode, message] of [
  ["unavailable", "OCR_EXECUTOR_UNAVAILABLE", "서버의 이미지 글자 인식(OCR) 엔진을 사용할 수 없습니다."],
  ["timeout", "OCR_TIMEOUT", "이 페이지를 읽는 데 시간이 오래 걸려 중단했습니다."],
  ["empty", "", "이 페이지에서 읽을 수 있는 글자를 찾지 못했습니다."],
] as const) {
  test(`${status} is an explicit reading limitation with a deliberate retry`, async ({ page }) => {
    const state = await fixture(page);
    state.postResult = (number) => envelope([pageResult(number, { status, errorCode, text: "" })]);
    await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
    const reading = page.getByRole("region", { name: "읽은 글자 · 1페이지", exact: true });
    await expect(reading.getByRole("status")).toContainText(message);
    expect(state.posts).toHaveLength(1);
    state.postResult = (number) => envelope([pageResult(number)]);
    await reading.getByRole("button", { name: "다시 읽기", exact: true }).click();
    await expect(reading.getByText("검토 원문 1페이지", { exact: true })).toBeVisible();
    expect(state.posts).toHaveLength(2);
  });
}

test("closing an in-flight reading prevents its delayed text from appearing after reopening", async ({ page }) => {
  const state = await fixture(page);
  let release!: () => void;
  state.postGate = new Promise<void>((resolve) => { release = resolve; });
  state.postResult = (number) => envelope([pageResult(number, { text: "늦게 도착한 이전 요청" })]);
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  await page.getByRole("button", { name: "읽은 글자 닫기", exact: true }).click();
  await expect(page.getByRole("button", { name: "글자 읽기", exact: true })).toBeFocused();
  state.cached.set(1, envelope([pageResult(1, { text: "서버에 저장된 최신 읽기" })]));
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  await expect(page.getByText("서버에 저장된 최신 읽기", { exact: true })).toBeVisible();
  release();
  await expect(page.getByText("늦게 도착한 이전 요청", { exact: true })).toHaveCount(0);
  await expect(page.getByText("서버에 저장된 최신 읽기", { exact: true })).toBeVisible();
});

test("changing the selected page discards an in-flight reading from the previous page", async ({ page }) => {
  const state = await fixture(page);
  let release!: () => void;
  state.postGate = new Promise<void>((resolve) => { release = resolve; });
  state.postResult = (number) => envelope([pageResult(number, { text: "이전 페이지의 늦은 글자" })]);
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  await expect.poll(() => state.posts.length).toBe(1);
  await page.getByRole("button", { name: "다음 PDF 페이지", exact: true }).click();
  await expect(page.getByRole("img", { name: "PDF 2페이지", exact: true })).toBeVisible();
  state.cached.set(2, envelope([pageResult(2, { text: "새로 선택한 2페이지" })]));
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  await expect(page.getByText("새로 선택한 2페이지", { exact: true })).toBeVisible();
  release();
  await expect(page.getByText("이전 페이지의 늦은 글자", { exact: true })).toHaveCount(0);
  expect(state.posts).toEqual([{ pageNumbers: [1] }]);
});

test("a malformed or mismatched page response remains an error beside the original image", async ({ page }) => {
  const state = await fixture(page);
  state.postResult = () => envelope([pageResult(2, { text: { value: "잘못된 페이지" } })]);
  await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
  const reading = page.getByRole("region", { name: "읽은 글자 · 1페이지", exact: true });
  await expect(reading.getByRole("alert")).toContainText("선택한 페이지의 읽기 결과를 확인하지 못했습니다");
  await expect(page.getByRole("img", { name: "PDF 1페이지", exact: true })).toBeVisible();
  await expect(page.getByText("잘못된 페이지", { exact: true })).toHaveCount(0);
  expect(state.mutations).toEqual([]);
});

for (const [mismatch, replacement] of [
  ["document", { fileSha256: "c".repeat(64) }],
  ["project", { projectId: "another-project" }],
] as const) {
  test(`a valid response for another ${mismatch} is rejected before displaying its text`, async ({ page }) => {
    const state = await fixture(page);
    state.postResult = (number) => envelope([pageResult(number, { text: "다른 자료의 비공개 글자" })], replacement);
    await page.getByRole("button", { name: "글자 읽기", exact: true }).click();
    const reading = page.getByRole("region", { name: "읽은 글자 · 1페이지", exact: true });
    await expect(reading.getByRole("alert")).toContainText("선택한 페이지의 읽기 결과를 확인하지 못했습니다");
    await expect(page.getByRole("img", { name: "PDF 1페이지", exact: true })).toBeVisible();
    await expect(page.getByText("다른 자료의 비공개 글자", { exact: true })).toHaveCount(0);
    expect(state.posts).toEqual([{ pageNumbers: [1] }]);
    expect(state.mutations).toEqual([]);
  });
}
