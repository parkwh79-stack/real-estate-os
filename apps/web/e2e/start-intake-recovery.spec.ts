import { expect, test, type Page } from "@playwright/test";
import { hasArchivedFeasibilityReviewDraft, hasArchivedWorkbookSourceReview, intakeProcessingFailure } from "../lib/intake-processing-status";

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

function unmappedWorkbookResponse() {
  const hash = "d".repeat(64);
  return { project_id: "start-recovery", feasibilityStudyV2Draft: null,
    upload: { archiveId: hash, originalFileHash: hash, archiveStored: true, documentType: "feasibility_workbook" },
    validation: { status: "needs_mapping", sourceDocumentHash: hash, templateSupport: { status: "unsupported_layout" },
      diagnostics: [{ code: "unsupported_workbook_layout" }], formulaIntegrity: { status: "not_evaluated" } },
    sourceReview: { reason: "unsupported_workbook_layout", candidateOnly: true, authoritative: false,
      projectId: "start-recovery", archiveId: hash, sourceDocumentHash: hash,
      profileId: "realytics.source_review_candidates", profileVersion: "1.0.0" } };
}

async function fixture(page: Page, files: string[]) {
  const state = { failFiles: new Set<string>(), uploads: [] as string[], creates: 0, finalizeCalls: 0,
    finalizeStatus: 200, nextStage: undefined as string | undefined, finalizeGate: Promise.resolve(),
    validationCalls: 0, candidateCalls: 0, landCalls: 0, enrichmentCalls: 0, documentReviewCalls: 0,
    parserFailures: 0, reviewCandidateCount: 1, zeroLand: false };
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
    if (path.endsWith("/intake/classify")) return reply({ files: files.map((filename) => {
      if (filename === "unmapped.xlsx") return { filename, documentType: "feasibility_workbook", label: "사업수지", confidence: 0.98,
        needsReview: false, archiveStored: true, archiveId: "d".repeat(64), processingTypes: ["feasibility_workbook", "land_ledger"] };
      if (filename === "bridge.xlsx") return { filename, documentType: "unknown", label: "금융현황 · 항목 매핑 필요", confidence: 0,
        needsReview: true, archiveStored: true, archiveId: "e".repeat(64), processingMode: "document_review", processingTypes: [] };
      if (filename === "memo.pdf") return { filename, documentType: "investment_committee_memo", label: "투자심의 메모", confidence: 0.48,
        needsReview: true, archiveStored: true, archiveId: "c".repeat(64), processingMode: "document_review",
        processingTypes: ["business_plan", "basic_architectural_plan", "investment_committee_memo"], topics: ["투자심의"] };
      if (filename === "unknown-type.xlsx") return { filename, documentType: "unrecognized_kind", label: "유형 미확인", confidence: 0.82,
        needsReview: false, archiveStored: true, processingTypes: ["land_ledger"] };
      if (filename === "mismatch.xlsx") return { filename, documentType: "feasibility_workbook", label: "사업수지", confidence: 0.98,
        needsReview: true, archiveStored: true, processingTypes: [], formatIssue: "extension_mismatch" };
      return { filename,
        documentType: filename === "bonri.xlsx" ? "feasibility_workbook" : filename === "land.xlsx" ? "land_ledger"
          : /market\.(csv|xlsx)$/.test(filename) ? "market_evidence" : "business_plan",
        label: "검토 자료", confidence: 1, needsReview: false, archiveStored: filename !== "unarchived-market.csv" };
    }) });
    if (path.endsWith("/intake/triage")) return reply({ recommendedTrack: "development", recommendationSource: "server_rules", aiAssisted: false, persisted: true,
      tracks: [{ track: "development", label: "부동산 개발", title: "부동산 개발", description: "저장 자료 검토", entryHref: "input-package",
        score: 1, confidence: 1, recommended: true, rationale: "분류된 원본 자료", reasons: [] }] });
    if (path.endsWith("/upload")) {
      const filename = /filename="([^"]+)"/.exec(request.postDataBuffer()?.toString("utf8") ?? "")?.[1] ?? "unknown";
      state.uploads.push(filename);
      if (state.failFiles.has(filename)) return reply({ detail: "parse unavailable" }, 503);
      return reply({ project: { id: "start-recovery" }, status: "completed" });
    }
    if (path.endsWith("/parsing-v2/runs") && request.method() === "POST") {
      state.documentReviewCalls += 1;
      const submitted = request.postDataJSON();
      expect(["c", "d", "e"].map((char) => char.repeat(64))).toContain(submitted.archiveId);
      expect(submitted).toEqual({ archiveId: submitted.archiveId, profileId: "realytics.source_review_candidates", profileVersion: "1.0.0", idempotencyKey: "source-review-v1-" + submitted.archiveId });
      if (state.parserFailures > 0) { state.parserFailures -= 1; return reply({ detail: "parser_connection_unavailable" }, 503); }
      return reply({ candidateOnly: true, authoritative: false, candidateCollection: { candidateCount: state.reviewCandidateCount },
        parsingRun: { parsingRunId: "synthetic-review", executionStatus: "completed" } }, 201);
    }
    if (path.endsWith("/feasibility-workbook/validate")) {
      state.validationCalls += 1;
      return reply(files.includes("unmapped.xlsx") ? unmappedWorkbookResponse() : workbookCandidateResponse());
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
      return reply({ project_id: "start-recovery", landLedger: { summary: { parcelCount: state.zeroLand ? 0 : 9 } }, landEvidenceEnrichment: "deferred" });
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
    name, mimeType: name.endsWith(".xlsx") ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" : name.endsWith(".pdf") ? "application/pdf" : "text/csv",
    buffer: Buffer.from('{"fixture":"retained source"}'),
  })));
  const intake = page.getByRole("region", { name: "자료 분류와 처리" });
  await expect(intake.getByRole("listitem")).toHaveCount(files.length);
  return { state, intake };
}

for (const batch of ["mixed", "all failed"] as const) {
  test(`${batch} intake stays on the same project and retries only unfinished files before the legacy success route`, async ({ page }) => {
    const files = batch === "mixed" ? ["completed.pdf", "retry.pdf"] : ["retry.pdf"];
    const { state, intake } = await fixture(page, files);
    state.failFiles.add("retry.pdf");
    const enter = page.getByRole("button", { name: "선택 트랙 워크스페이스로 이동", exact: true });
    await expect(enter).toBeDisabled();
    await intake.getByRole("button", { name: /^자료 처리/ }).click();
    await expect(intake.getByRole("alert")).toContainText("사업계획서 / IM 처리");
    await expect(page).toHaveURL(/\/start$/);
    await expect(enter).toBeDisabled();
    await expect(intake.getByRole("listitem").filter({ hasText: "retry.pdf" })).toContainText("실패");
    if (batch === "mixed") await expect(intake.getByRole("listitem").filter({ hasText: "completed.pdf" })).toContainText("처리 완료");
    expect(state.finalizeCalls).toBe(0);
    expect(state.creates).toBe(1);
    state.failFiles.clear();
    await intake.getByRole("button", { name: /^자료 처리/ }).click();
    await expect(page).toHaveURL(/\/project\/start-recovery\/chief-orchestration\?track=development$/, { timeout: 15_000 });
    expect(state.uploads.filter((filename) => filename === "retry.pdf")).toHaveLength(2);
    expect(state.uploads.filter((filename) => filename === "completed.pdf")).toHaveLength(batch === "mixed" ? 1 : 0);
    expect(state.creates).toBe(1);
    expect(state.finalizeCalls).toBe(1);
  });
}

test("finalization failure preserves completed rows and retries connection without reuploading, honoring normalized input review", async ({ page }) => {
  const { state, intake } = await fixture(page, ["completed.pdf"]);
  state.finalizeStatus = 503;
  state.nextStage = "input_review";
  await intake.getByRole("button", { name: /^자료 처리/ }).click();
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
    expect(state.uploads).toEqual(["completed.pdf"]);
  } finally {
    release();
  }
  await expect(page).toHaveURL(/\/project\/start-recovery\/input-package\?track=development$/);
  expect(state.uploads).toEqual(["completed.pdf"]);
  expect(state.creates).toBe(1);
  expect(state.finalizeCalls).toBe(2);
});


test("source formula errors stay review candidates and deferred land evidence does not repeat successful parsing on mixed retry", async ({ page }) => {
  const { state, intake } = await fixture(page, ["bonri.xlsx", "land.xlsx", "retry.pdf"]);
  state.failFiles.add("retry.pdf");
  state.nextStage = "input_review";
  await intake.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(intake.getByRole("alert")).toContainText("사업계획서 / IM 처리");
  await expect(intake.getByRole("listitem").filter({ hasText: "bonri.xlsx" })).toContainText("검토 후보 저장 · 수식 오류 확인 필요");
  await expect(intake.getByRole("listitem").filter({ hasText: "land.xlsx" })).toContainText("9필지 저장 · PNU 증빙 보강 대기");
  expect(state.validationCalls).toBe(1);
  expect(state.candidateCalls).toBe(1);
  expect(state.landCalls).toBe(1);
  expect(state.enrichmentCalls).toBe(0);
  expect(state.finalizeCalls).toBe(0);
  await expect(page).toHaveURL(/\/start$/);
  state.failFiles.clear();
  await intake.getByRole("button", { name: /^자료 처리/ }).click();
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
  const { state, intake } = await fixture(page, ["completed.pdf", "market.csv"]);
  state.nextStage = "input_review";
  const archived = intake.getByRole("listitem").filter({ hasText: "market.csv" });
  await expect(archived).toContainText("원본 저장 · 자동 처리 경로 없음");
  await expect(page.getByRole("button", { name: "선택 트랙 워크스페이스로 이동", exact: true })).toBeDisabled();
  let release!: () => void;
  state.finalizeGate = new Promise<void>((resolve) => { release = resolve; });
  await intake.getByRole("button", { name: /^자료 처리/ }).click();
  try {
    await expect.poll(() => state.finalizeCalls).toBe(1);
    await expect(intake.getByRole("alert")).toHaveCount(0);
    await expect(archived).toContainText("원본 저장 · 자동 처리 경로 없음");
    expect(state.uploads).toEqual(["completed.pdf"]);
  } finally { release(); }
  await expect(page).toHaveURL(/\/project\/start-recovery\/input-package\?track=development$/);
  expect(state.creates).toBe(1);
});

test("an archived unsupported-only selection can be reclassified or explicitly connected through the normalized next stage", async ({ page }) => {
  const { state, intake } = await fixture(page, ["market.xlsx"]);
  state.nextStage = "input_review";
  const enter = page.getByRole("button", { name: "선택 트랙 워크스페이스로 이동", exact: true });
  await expect(intake.getByRole("listitem")).toContainText("원본만 보관");
  await expect(enter).toBeEnabled();
  await expect(intake.getByRole("button", { name: /^자료 처리/ })).toBeDisabled();
  await intake.getByText("유형 바꾸기", { exact: true }).click();
  await intake.getByRole("combobox").selectOption("land_ledger");
  await expect(enter).toBeDisabled();
  await expect(intake.getByRole("button", { name: /^자료 처리/ })).toBeEnabled();
  await intake.getByRole("combobox").selectOption("archive_only");
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

test("a weak PDF recommendation requires confirmation and prepares review from the archived original", async ({ page }) => {
  const { state, intake } = await fixture(page, ["memo.pdf"]);
  state.nextStage = "input_review";
  const row = intake.getByRole("listitem");
  const process = intake.getByRole("button", { name: /^자료 처리/ });
  await expect(row).toContainText("추천 · 투자심의 메모");
  await expect(row).toContainText("유형 확인 필요");
  await expect(row.getByRole("combobox")).toHaveCount(0);
  await expect(process).toBeDisabled();
  expect(state.documentReviewCalls).toBe(0);
  await row.getByText(/분류 근거 · 48%/).click();
  await expect(row.getByText("감지한 주제 · 투자심의")).toBeVisible();
  await row.getByRole("button", { name: "이 자료 검토" }).click();
  await expect(process).toBeEnabled();
  let release!: () => void;
  state.finalizeGate = new Promise<void>((resolve) => { release = resolve; });
  await process.click();
  try {
    await expect(row).toContainText("문서 검토 준비");
    await expect(row.getByRole("link", { name: "문서 검토 열기" })).toHaveAttribute("href", `/project/start-recovery/documents/review?track=development&archiveId=${"c".repeat(64)}`);
    expect(state.documentReviewCalls).toBe(1);
    expect(state.uploads).toEqual([]);
    expect(state.validationCalls).toBe(0);
  } finally { release(); }
  await expect(page).toHaveURL(/\/project\/start-recovery\/input-package\?track=development$/);
});

test("an unsupported recommendation leaves a real placeholder and only allows compatible parsers", async ({ page }) => {
  const { state, intake } = await fixture(page, ["unknown-type.xlsx", "mismatch.xlsx"]);
  state.nextStage = "input_review";
  const unknown = intake.getByRole("listitem").filter({ hasText: "unknown-type.xlsx" });
  const mismatch = intake.getByRole("listitem").filter({ hasText: "mismatch.xlsx" });
  const process = intake.getByRole("button", { name: /^자료 처리/ });
  await expect(process).toBeDisabled();
  await expect(mismatch).toContainText("파일 확장자와 실제 형식");
  await expect(mismatch.getByRole("combobox")).toHaveCount(0);
  await unknown.getByText("유형 선택", { exact: true }).click();
  const type = unknown.getByRole("combobox");
  await expect(type).toHaveValue("");
  await expect(type.locator("option")).toHaveText(["처리 유형 선택", "토지대장 / 토지조서", "원본만 보관"]);
  await type.selectOption("land_ledger");
  await process.click();
  await expect(page).toHaveURL(/\/project\/start-recovery\/input-package\?track=development$/);
  expect(state.landCalls).toBe(1);
  expect(state.validationCalls).toBe(0);
});


test("unsupported workbook keeps mapping guidance and retries source review from archive without financial import", async ({ page }) => {
  const { state, intake } = await fixture(page, ["unmapped.xlsx", "retry.pdf"]);
  state.failFiles.add("retry.pdf"); state.parserFailures = 1;
  const row = intake.getByRole("listitem").filter({ hasText: "unmapped.xlsx" });
  await intake.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(row).toContainText("실패");
  expect(state.validationCalls).toBe(1); expect(state.documentReviewCalls).toBe(1); expect(state.candidateCalls).toBe(0);
  state.reviewCandidateCount = 0;
  await intake.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(row).toContainText("원본 저장 · 검토 필요");
  await expect(row).toContainText("사업수지 양식 매핑 필요");
  await expect(row).toContainText("원본 읽음 · 추출 항목 연결 필요");
  await expect(row).not.toContainText("처리 완료");
  await expect(row.getByRole("link", { name: /문서 검토/ })).toHaveAttribute("href", /documents\/review/);
  expect(state.validationCalls).toBe(1); expect(state.documentReviewCalls).toBe(2); expect(state.candidateCalls).toBe(0);
  expect(state.landCalls).toBe(0); expect(state.creates).toBe(1);
});

test("zero-parcel HTTP success never displays saved or done and never calls enrichment", async ({ page }) => {
  const { state, intake } = await fixture(page, ["land.xlsx"]); state.zeroLand = true;
  await intake.getByRole("button", { name: /^자료 처리/ }).click();
  const row = intake.getByRole("listitem");
  await expect(row).toContainText("실패"); await expect(row).toContainText("지번·면적 행을 찾지 못했습니다");
  await expect(row).not.toContainText("0필지 저장"); await expect(row).not.toContainText("처리 완료");
  expect(state.enrichmentCalls).toBe(0); expect(state.finalizeCalls).toBe(0);
});

test("debt-status workbook uses original review and zero candidates stay inspection", async ({ page }) => {
  const { state, intake } = await fixture(page, ["bridge.xlsx", "retry.pdf"]);
  state.failFiles.add("retry.pdf"); state.reviewCandidateCount = 0;
  const row = intake.getByRole("listitem").filter({ hasText: "bridge.xlsx" });
  await expect(row).toContainText("금융현황");
  await row.getByRole("button", { name: "이 자료 검토", exact: true }).click();
  await intake.getByRole("button", { name: /^자료 처리/ }).click();
  await expect(row).toContainText("원본 저장 · 검토 필요"); await expect(row).toContainText("원본 읽음 · 추출 항목 연결 필요");
  await expect(row).not.toContainText("처리 완료");
  expect(state.validationCalls).toBe(0); expect(state.candidateCalls).toBe(0); expect(state.landCalls).toBe(0); expect(state.documentReviewCalls).toBe(1);
});

test("unsupported source review is bound to its project, original hash and mapping-only diagnostic", () => {
  const body = unmappedWorkbookResponse();
  expect(hasArchivedWorkbookSourceReview(body)).toBe(true); expect(intakeProcessingFailure(body, "feasibility_workbook")).toBeNull();
  for (const invalid of [
    { ...body, upload: { ...body.upload, archiveStored: false } },
    { ...body, sourceReview: { ...body.sourceReview, projectId: "other" } },
    { ...body, sourceReview: { ...body.sourceReview, authoritative: true } },
    { ...body, validation: { ...body.validation, sourceDocumentHash: "f".repeat(64) } },
    { ...body, validation: { ...body.validation, status: "parse_failed" } },
    { ...body, status: "failed" },
  ]) expect(intakeProcessingFailure(invalid, "feasibility_workbook")).not.toBeNull();
});
