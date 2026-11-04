import { expect, test, type Page } from "@playwright/test";
import { hasArchivedFeasibilityReviewDraft, intakeProcessingFailure } from "../lib/intake-processing-status";

function workbookCandidateResponse() {
  const hash = "a".repeat(64);
  return {
    project_id: "start-recovery",
    upload: { archiveId: hash, originalFileHash: hash, archiveStored: true, documentType: "feasibility_workbook",
      originalFileName: "bonri.xlsx", feasibilityStudyV2DraftHash: "b".repeat(64) },
    validation: { status: "failed", sourceDocumentHash: hash,
      formulaIntegrity: { hasBlockingFormulaErrors: true, formulaErrorCount: 37 } },
    feasibilityStudyV2Draft: { version: "realytics-feasibility-study-v2", project_id: "start-recovery", currency: "KRW",
      monthly_cash_flow: Array.from({ length: 64 }, (_, index) => ({ month_index: index + 1,
        period: new Date(Date.UTC(2025, index, 1)).toISOString().slice(0, 7), cash_in: {}, cash_out: {} })) },
  };
}

async function fixture(page: Page, files: string[]) {
  const state = { failFiles: new Set<string>(), uploads: [] as string[], creates: 0, finalizeCalls: 0,
    finalizeStatus: 200, nextStage: undefined as string | undefined, finalizeGate: Promise.resolve(),
    validationCalls: 0, candidateCalls: 0, landCalls: 0, enrichmentCalls: 0 };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/\/$/, "");
    const reply = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path.endsWith("/auth/me")) return reply({ authenticated: true, authSource: "bearer_token",
      user: { userId: "start-user", tenantId: "start-tenant", role: "analyst", status: "active" },
      session: { userId: "start-user", tenantId: "start-tenant" }, permissions: ["project:read", "project:write", "project:analyze"] });
    if (path.endsWith("/projects/recent-work")) return reply({ items: [] });
    if (path.endsWith("/intake/continuation-check")) return reply({ matches: [] });
    if (path.endsWith("/projects") && request.method() === "POST") {
      state.creates += 1;
      return reply({ id: "start-recovery" });
    }
    if (path.endsWith("/intake/classify")) return reply({ files: files.map((filename) => ({ filename,
      documentType: filename === "bonri.xlsx" ? "feasibility_workbook" : filename === "land.xlsx" ? "land_ledger" : filename.endsWith("market.csv") ? "market_evidence" : "business_plan",
      label: "검토 자료", confidence: 1, needsReview: false, archiveStored: filename !== "unarchived-market.csv" })) });
    if (path.endsWith("/intake/triage")) return reply({ recommendedTrack: "development", recommendationSource: "server_rules", aiAssisted: false, persisted: true,
      tracks: [{ track: "development", label: "부동산 개발", title: "부동산 개발", description: "저장 자료 검토", entryHref: "input-package",
        score: 1, confidence: 1, recommended: true, rationale: "분류된 원본 자료", reasons: [] }] });
    if (path.endsWith("/upload")) {
      const filename = /filename="([^"]+)"/.exec(request.postDataBuffer()?.toString("utf8") ?? "")?.[1] ?? "unknown";
      state.uploads.push(filename);
      if (state.failFiles.has(filename)) return reply({ detail: "parse unavailable" }, 503);
      return reply({ project: { id: "start-recovery" }, status: "completed" });
    }
    if (path.endsWith("/feasibility-workbook/validate")) {
      state.validationCalls += 1;
      return reply(workbookCandidateResponse());
    }
    if (path.endsWith("/feasibility-workbook/import-candidate")) {
      state.candidateCalls += 1;
      const submitted = request.postDataJSON();
      expect(submitted.feasibilityStudyV2Draft.monthly_cash_flow).toHaveLength(64);
      expect(submitted.validation.formulaIntegrity.formulaErrorCount).toBe(37);
      expect(submitted.sourceDocument.archiveStored).toBe(true);
      return reply({ schemaVersion: "realytics-feasibility-workbook-candidate-v1", projectId: "start-recovery",
        status: "candidate", authoritative: false, importId: "review-candidate" });
    }
    if (path.endsWith("/land-ledger/analyze")) {
      state.landCalls += 1;
      return reply({ project_id: "start-recovery", landLedger: { summary: { parcelCount: 9 } }, landEvidenceEnrichment: "deferred" });
    }
    if (path.endsWith("/land-ledger/enrich")) {
      state.enrichmentCalls += 1;
      return reply({ detail: "enrichment is not permitted in this fixture" }, 503);
    }
    if (path.endsWith("/intake/finalize")) {
      state.finalizeCalls += 1;
      expect(request.postDataJSON()).toEqual({ track: "development", run: false });
      await state.finalizeGate;
      return reply(state.finalizeStatus === 200
        ? { projectId: "start-recovery", intakeReadinessPlan: { stages: [] }, contextCompleteness: "partial", nextStage: state.nextStage }
        : { detail: "legacy planning unavailable" }, state.finalizeStatus);
    }
    if (path.endsWith("/workspace/context")) return reply({ projectId: "start-recovery", contextCompleteness: "partial",
      unavailableSections: ["landEvidence", "missingInputs"], projectMemory: { projectName: "저장된 시작 자료" }, pageData: { sourceDocuments: [] } });
    if (path.endsWith("/feasibility-workbook/review")) return reply({ values: {}, fieldReviews: {}, reviewHash: "empty", sourceDocuments: [] });
    return reply({ detail: "not_mocked" }, 404);
  });
  await page.goto("/start");
  await page.locator('input[type="file"]').setInputFiles(files.map((name) => ({
    name, mimeType: name.endsWith(".xlsx") ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : "application/json",
    buffer: Buffer.from('{"fixture":"retained source"}'),
  })));
  const intake = page.getByRole("region", { name: "자료 분류와 처리" });
  await expect(intake.getByRole("listitem")).toHaveCount(files.length);
  return { state, intake };
}

for (const batch of ["mixed", "all failed"] as const) {
  test(`${batch} intake stays on the same project and retries only unfinished files before the legacy success route`, async ({ page }) => {
    const files = batch === "mixed" ? ["completed.json", "retry.json"] : ["retry.json"];
    const { state, intake } = await fixture(page, files);
    state.failFiles.add("retry.json");
    const enter = page.getByRole("button", { name: "선택 트랙 워크스페이스로 이동", exact: true });
    await expect(enter).toBeDisabled();
    await intake.getByRole("button", { name: /^분류대로 처리/ }).click();
    await expect(intake.getByRole("alert")).toContainText("처리하지 못한 자료가 남아 있습니다.");
    await expect(page).toHaveURL(/\/start$/);
    await expect(enter).toBeDisabled();
    await expect(intake.getByRole("listitem").filter({ hasText: "retry.json" })).toContainText("실패");
    if (batch === "mixed") await expect(intake.getByRole("listitem").filter({ hasText: "completed.json" })).toContainText("처리 완료");
    expect(state.finalizeCalls).toBe(0);
    expect(state.creates).toBe(1);
    state.failFiles.clear();
    await intake.getByRole("button", { name: /^분류대로 처리/ }).click();
    await expect(page).toHaveURL(/\/project\/start-recovery\/chief-orchestration\?track=development$/);
    expect(state.uploads.filter((filename) => filename === "retry.json")).toHaveLength(2);
    expect(state.uploads.filter((filename) => filename === "completed.json")).toHaveLength(batch === "mixed" ? 1 : 0);
    expect(state.creates).toBe(1);
    expect(state.finalizeCalls).toBe(1);
  });
}

test("finalization failure preserves completed rows and retries connection without reuploading, honoring normalized input review", async ({ page }) => {
  const { state, intake } = await fixture(page, ["completed.json"]);
  state.finalizeStatus = 503;
  state.nextStage = "input_review";
  await intake.getByRole("button", { name: /^분류대로 처리/ }).click();
  const failure = page.getByRole("alert").filter({ hasText: "자료 처리는 완료됐지만 연결·보완 계획 확인을 마치지 못했습니다." });
  await expect(failure).toBeVisible();
  await expect(page).toHaveURL(/\/start$/);
  await expect(intake.getByRole("listitem")).toContainText("처리 완료");
  await expect(page.getByRole("button", { name: "선택 트랙 워크스페이스로 이동", exact: true })).toBeDisabled();
  const retry = failure.getByRole("button", { name: "자료 연결 다시 시도", exact: true });
  await expect(retry).toBeEnabled();
  state.finalizeStatus = 200;
  let release!: () => void;
  state.finalizeGate = new Promise<void>((resolve) => { release = resolve; });
  await retry.click();
  try {
    await expect.poll(() => state.finalizeCalls).toBe(2);
    await expect(intake.getByRole("button", { name: "추가 자료 파일 선택", exact: true })).toHaveAttribute("aria-disabled", "true");
    expect(state.uploads).toEqual(["completed.json"]);
  } finally {
    release();
  }
  await expect(page).toHaveURL(/\/project\/start-recovery\/input-package\?track=development$/);
  expect(state.uploads).toEqual(["completed.json"]);
  expect(state.creates).toBe(1);
  expect(state.finalizeCalls).toBe(2);
});


test("source formula errors stay review candidates and deferred land evidence does not repeat successful parsing on mixed retry", async ({ page }) => {
  const { state, intake } = await fixture(page, ["bonri.xlsx", "land.xlsx", "retry.json"]);
  state.failFiles.add("retry.json");
  state.nextStage = "input_review";
  await intake.getByRole("button", { name: /^분류대로 처리/ }).click();
  await expect(intake.getByRole("alert")).toContainText("처리하지 못한 자료가 남아 있습니다.");
  await expect(intake.getByRole("listitem").filter({ hasText: "bonri.xlsx" })).toContainText("검토 후보 저장 · 수식 오류 확인 필요");
  await expect(intake.getByRole("listitem").filter({ hasText: "land.xlsx" })).toContainText("9필지 저장 · PNU 증빙 보강 대기");
  expect(state.validationCalls).toBe(1);
  expect(state.candidateCalls).toBe(1);
  expect(state.landCalls).toBe(1);
  expect(state.enrichmentCalls).toBe(0);
  expect(state.finalizeCalls).toBe(0);
  await expect(page).toHaveURL(/\/start$/);
  state.failFiles.clear();
  await intake.getByRole("button", { name: /^분류대로 처리/ }).click();
  await expect(page).toHaveURL(/\/project\/start-recovery\/input-package\?track=development$/);
  expect(state.validationCalls).toBe(1);
  expect(state.candidateCalls).toBe(1);
  expect(state.landCalls).toBe(1);
  expect(state.enrichmentCalls).toBe(0);
  expect(state.finalizeCalls).toBe(1);
});

test("only a source-bound feasibility candidate may pass validation-failed into the candidate endpoint", () => {
  const valid = workbookCandidateResponse();
  expect(hasArchivedFeasibilityReviewDraft(valid)).toBe(true);
  expect(intakeProcessingFailure(valid)).toBeNull();
  for (const invalid of [
    { ...valid, feasibilityStudyV2Draft: null },
    { ...valid, upload: { ...valid.upload, archiveStored: false } },
    { ...valid, validation: { ...valid.validation, sourceDocumentHash: "c".repeat(64) } },
    { ...valid, feasibilityStudyV2Draft: { ...valid.feasibilityStudyV2Draft, project_id: "other-project" } },
    { ...valid, validation: { ...valid.validation, status: "parse_failed" } },
    { ...valid, status: "failed" },
  ]) expect(intakeProcessingFailure(invalid)).not.toBeNull();
});


test("an archived unsupported document does not block the actionable files in a mixed batch", async ({ page }) => {
  const { state, intake } = await fixture(page, ["completed.json", "market.csv"]);
  state.nextStage = "input_review";
  const archived = intake.getByRole("listitem").filter({ hasText: "market.csv" });
  await expect(archived).toContainText("원본 저장 · 자동 처리 경로 없음");
  await expect(page.getByRole("button", { name: "선택 트랙 워크스페이스로 이동", exact: true })).toBeDisabled();
  let release!: () => void;
  state.finalizeGate = new Promise<void>((resolve) => { release = resolve; });
  await intake.getByRole("button", { name: /^분류대로 처리/ }).click();
  try {
    await expect.poll(() => state.finalizeCalls).toBe(1);
    await expect(intake.getByRole("alert")).toHaveCount(0);
    await expect(archived).toContainText("원본 저장 · 자동 처리 경로 없음");
    expect(state.uploads).toEqual(["completed.json"]);
  } finally { release(); }
  await expect(page).toHaveURL(/\/project\/start-recovery\/input-package\?track=development$/);
  expect(state.creates).toBe(1);
});

test("an archived unsupported-only selection can be reclassified or explicitly connected through the normalized next stage", async ({ page }) => {
  const { state, intake } = await fixture(page, ["market.csv"]);
  state.nextStage = "input_review";
  const enter = page.getByRole("button", { name: "선택 트랙 워크스페이스로 이동", exact: true });
  await expect(intake.getByRole("listitem")).toContainText("원본 저장 · 자동 처리 경로 없음");
  await expect(enter).toBeEnabled();
  await expect(intake.getByRole("button", { name: /^분류대로 처리/ })).toBeDisabled();
  await intake.getByRole("combobox").selectOption("business_plan");
  await expect(enter).toBeDisabled();
  await expect(intake.getByRole("button", { name: /^분류대로 처리/ })).toBeEnabled();
  await intake.getByRole("combobox").selectOption("market_evidence");
  await expect(enter).toBeEnabled();
  expect(state.finalizeCalls).toBe(0);
  await enter.click();
  await expect(page).toHaveURL(/\/project\/start-recovery\/input-package\?track=development$/);
  expect(state.uploads).toEqual([]);
  expect(state.finalizeCalls).toBe(1);
});

test("unsupported documents without confirmed archival stay pending", async ({ page }) => {
  const { state, intake } = await fixture(page, ["unarchived-market.csv"]);
  await expect(intake.getByText("원본 저장 · 자동 처리 경로 없음", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "선택 트랙 워크스페이스로 이동", exact: true })).toBeDisabled();
  expect(state.finalizeCalls).toBe(0);
  expect(state.uploads).toEqual([]);
});
