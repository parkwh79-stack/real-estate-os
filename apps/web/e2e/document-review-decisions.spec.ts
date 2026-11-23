import { expect, test, type Page } from "@playwright/test";
import type {
  DocumentReviewData,
  ParsedFactCandidate,
  ReviewDecisionRecord,
  SourceLocatorRecord,
} from "../lib/document-review-workspace";

// MOCK-DATA: independent synthetic originals and already-authorized human reviewer.
// This route fixture does not exercise or replace server RBAC/MFA/maker-checker gates.
const projectId = "synthetic-document-review-decisions";
const archiveId = "c".repeat(64);
const otherArchiveId = "d".repeat(64);
const fileName = "합성검증_브릿지론_31후보.xlsx";
const otherFileName = "합성검증_담보현황.xlsx";
const reviewerId = "synthetic-human-checker";
const producerId = "synthetic-parser-maker";
const principalId = "synthetic-bridge-principal";
const rateId = "synthetic-bridge-interest-rate";
const reasonLabel = "결정 근거 또는 보완 사유";
const reviewUrl = `/project/${projectId}/documents/review?archiveId=${archiveId}`;

type ReviewPost = {
  candidateId: string;
  body: { action: string; reason: string; expectedDecisionSequence: number; conflictIds: string[] };
};

function locator(cell: string, other = false): SourceLocatorRecord {
  return {
    evidenceId: `synthetic-${other ? "collateral" : "bridge"}-${cell}`,
    documentVersionId: other ? "synthetic-collateral-v1" : "synthetic-bridge-v1",
    fileSha256: other ? otherArchiveId : archiveId,
    sheetName: "sheet",
    cellReference: cell,
  };
}

function bridgeCandidate(id: string, fieldKey: string, cell: string, value: string | number, unit?: string, rawValue: unknown = value): ParsedFactCandidate {
  const row = Number(cell.match(/\d+$/)![0]);
  const column = cell.replace(/\d+$/, "");
  const loanAmount = fieldKey === "financing.tranche.loan_amount";
  return {
    candidateId: id,
    actorId: producerId,
    parsingRunId: "synthetic-bridge-run",
    documentVersionId: "synthetic-bridge-v1",
    entityId: `synthetic-bridge-row-${row}`,
    fieldKey,
    rawValue,
    normalizedValue: value,
    unit,
    reviewStatus: "pending",
    approvalStatus: "not_requested",
    validationStatus: loanAmount ? "warning" : "passed",
    validationCodes: loanAmount ? ["FACT_BASIS_REQUIRED"] : [],
    presenceStatus: "present",
    sourceLocator: locator("A1"),
    evidence: [locator("A1"), ...(loanAmount ? [locator("F2")] : []), locator(`${column}3`), locator(cell)],
    metadata: {
      extractionRule: "explicit_bridge_financial_table_literal_v1",
      sourceTable: { sheetName: "sheet", headerRow: 3, row },
      sourceFinancialContext: "current_bridge_snapshot", referenceOnly: true, noFuturePfProjection: true,
      sourceAsOfStatus: "unconfirmed", financingBalanceBasisStatus: "unconfirmed",
      sourceTableCurrencyUnit: "억원", sourceCalculationState: "literal",
      sourceDocumentPurpose: "원본 금융현황·현재 브리지 대출 검토용; 미래 PF 약정 아님",
      ...(loanAmount ? { sourceUnit: "억원" } : {}),
    },
  };
}

function workbookIdentity(id: string) {
  return {
    archiveId: id, sourceSha256: id, fileName: id === archiveId ? fileName : otherFileName,
    archiveVersionId: "synthetic-source-v1", archiveETag: `synthetic-etag-${id[0]}`,
    archiveIntegrityStatus: "verified", analysisOnly: true, approvalEligible: false,
  };
}

function columnNumber(label: string) {
  return [...label].reduce((value, character) => value * 26 + character.charCodeAt(0) - 64, 0);
}

function columnLabel(value: number) {
  let label = "";
  for (let column = value; column > 0; column = Math.floor((column - 1) / 26)) {
    label = String.fromCharCode(65 + (column - 1) % 26) + label;
  }
  return label;
}

async function fixture(page: Page) {
  const state = {
    reviewStatus: 201,
    reviewError: "" as string | { error: string },
    reloadUnavailable: false,
    omitSavedDecisions: false,
    failReloadAfterSave: false,
    workspaceRequests: 0,
    reviewPosts: [] as ReviewPost[],
    decisions: [] as ReviewDecisionRecord[],
    unexpectedMutations: [] as string[],
    cellRequests: [] as { archiveId: string; reference: string }[],
  };
  const rateTerms = [
    { row: 4, value: 0.077, raw: "변동 7.70%", type: "floating" },
    { row: 6, value: 0.055, raw: "고정 5.50%", type: "fixed" },
    { row: 8, value: 0.065, raw: "변동 6.50%", type: "floating" },
    { row: 10, value: 0.072, raw: "고정 7.20%", type: "fixed" },
  ];
  // Match the extractor's 31-candidate distribution, including two facts per rate cell.
  const candidates = [
    ...Array.from({ length: 11 }, (_, index) => {
      const row = index + 4;
      return bridgeCandidate(index === 0 ? principalId : `synthetic-bridge-loan-${row}`,
        "financing.tranche.loan_amount", `B${row}`, (50 - index * 2) * 100_000_000, "krw", 50 - index * 2);
    }),
    ...Array.from({ length: 11 }, (_, index) => bridgeCandidate(`synthetic-bridge-lender-${index + 4}`,
      "financing.tranche.name", `A${index + 4}`, `합성 대주 ${index + 1}`)),
    ...rateTerms.flatMap((term, index) => [
      bridgeCandidate(index === 0 ? rateId : `synthetic-bridge-rate-${term.row}`,
        "financing.tranche.interest_rate", `E${term.row}`, term.value, "ratio", term.raw),
      bridgeCandidate(`synthetic-bridge-rate-type-${term.row}`,
        "financing.tranche.interest_rate_type", `E${term.row}`, term.type, undefined, term.raw),
    ]),
    bridgeCandidate("synthetic-bridge-maturity", "financing.tranche.maturity_date", "D4", "2027-05-17"),
  ];
  const readModel = (): DocumentReviewData => ({
    schemaVersion: "realytics-document-review-workspace-v1",
    viewModelVersion: "document-review-workspace-2026-07-27",
    projectId, snapshotId: `synthetic-review-${state.decisions.length}`, snapshotHash: "e".repeat(64),
    observedAt: "2026-11-09T00:00:00Z",
    sourceDocuments: [archiveId, otherArchiveId].map((id) => ({
      archiveId: id, originalFileName: id === archiveId ? fileName : otherFileName,
      contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      archiveIntegrityStatus: "verified", uploadedAt: "2026-11-09T00:00:00Z",
    })),
    runs: [],
    packages: [{
      parsingRun: {
        parsingRunId: "synthetic-bridge-run", documentVersionId: "synthetic-bridge-v1",
        profileId: "realytics.source_review_candidates", profileVersion: "1.1.0",
        executionStatus: "completed", requestedBy: producerId,
      },
      documentVersion: {
        documentVersionId: "synthetic-bridge-v1", archiveId, sourceHash: archiveId, fileName,
        mediaType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
      candidateSetSeal: { candidateSetHash: "a".repeat(64), conflictSetHash: "b".repeat(64) },
      candidates, conflicts: [], reviewDecisions: state.omitSavedDecisions ? [] : state.decisions,
      canonicalSelections: [], approvedInputSnapshots: [],
    }, {
      parsingRun: {
        parsingRunId: "synthetic-collateral-run", documentVersionId: "synthetic-collateral-v1",
        profileId: "realytics.source_review_candidates", profileVersion: "1.1.0",
        executionStatus: "completed", requestedBy: producerId,
      },
      documentVersion: {
        documentVersionId: "synthetic-collateral-v1", archiveId: otherArchiveId,
        sourceHash: otherArchiveId, fileName: otherFileName,
        mediaType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
      candidateSetSeal: { candidateSetHash: "f".repeat(64) },
      candidates: [{
        candidateId: "synthetic-collateral-value", actorId: producerId, documentVersionId: "synthetic-collateral-v1",
        fieldKey: "collateral.synthetic.appraisal", normalizedValue: 9_000_000_000, unit: "KRW",
        reviewStatus: "pending", approvalStatus: "not_requested", validationStatus: "warning",
        sourceLocator: locator("B4", true), evidence: [locator("B4", true)],
      }],
      conflicts: [], reviewDecisions: [], canonicalSelections: [], approvedInputSnapshots: [],
    }],
    glossaryCandidates: [], reviewQueue: [], snapshotDiffs: [], warnings: [],
    authority: { candidateOnly: true, authoritativeProjectionMutated: false },
  });

  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path.endsWith("/auth/me")) return reply({
      authenticated: true,
      user: { userId: reviewerId, tenantId: "synthetic-review-tenant", role: "reviewer", status: "active" },
      session: { userId: reviewerId, tenantId: "synthetic-review-tenant", mfaVerified: true },
      permissions: ["project:read", "evidence:read", "project:review", "approval:write"],
    });
    if (path.endsWith("/auth/ai-credentials/current")) return reply({ revoked: true });
    if (path.endsWith("/projects")) return reply([{ project: { id: projectId, name: "합성 문서 검토 회귀" } }]);
    if (path.endsWith("/workspace/context")) return reply({ projectId, projectMemory: { projectName: "합성 문서 검토 회귀" } });
    if (path.endsWith("/workspace/document-review")) {
      state.workspaceRequests++;
      return state.reloadUnavailable ? reply({ detail: "synthetic_read_model_unavailable" }, 503) : reply(readModel());
    }
    const reviewMatch = path.match(/\/parsing-v2\/candidates\/([^/]+)\/reviews$/);
    if (reviewMatch && request.method() === "POST") {
      const candidateId = decodeURIComponent(reviewMatch[1]!);
      const body = request.postDataJSON() as ReviewPost["body"];
      state.reviewPosts.push({ candidateId, body });
      if (state.reviewStatus !== 201) return reply({ detail: state.reviewError }, state.reviewStatus);
      const decision: ReviewDecisionRecord = {
        reviewDecisionId: `synthetic-review-decision-${state.decisions.length + 1}`,
        candidateId, decisionSequence: body.expectedDecisionSequence + 1,
        action: body.action, reason: body.reason, reviewerId,
        resultingReviewStatus: body.action === "approve" ? "approved" : body.action === "reject" ? "rejected" : "needs_revision",
        resultingApprovalStatus: body.action === "approve" ? "approved" : body.action === "reject" ? "rejected" : "pending",
        decidedAt: "2026-11-09T01:00:00Z",
      };
      state.decisions.push(decision);
      if (state.failReloadAfterSave) state.reloadUnavailable = true;
      return reply(decision, 201);
    }
    if (path.endsWith("/workbook/sheets")) {
      const id = path.includes(otherArchiveId) ? otherArchiveId : archiveId;
      return reply({
        schemaVersion: "realytics-workbook-original-sheets-v1", ...workbookIdentity(id),
        sheets: [{ sheetName: "sheet", sheetIndex: 0, rowCount: 40, columnCount: 8, declaredRowCount: 40, declaredColumnCount: 8, state: "visible" }],
        limits: { maxRows: 10000, maxColumns: 512, maxCells: 400, maxSheets: 100 },
        defaultSheetName: "sheet", defaultRange: "A1:H20", warnings: [], truncated: false,
      });
    }
    if (path.endsWith("/workbook/cells")) {
      const id = path.includes(otherArchiveId) ? otherArchiveId : archiveId;
      const query = url.searchParams;
      const reference = query.get("cell_reference")!;
      state.cellRequests.push({ archiveId: id, reference });
      const match = reference.match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/)!;
      const focusColumn = columnNumber(match[1]!); const focusRow = Number(match[2]);
      const lastFocusColumn = columnNumber(match[3] ?? match[1]!); const lastFocusRow = Number(match[4] ?? match[2]);
      const firstColumn = Math.max(1, focusColumn - Number(query.get("context_columns")));
      const lastColumn = lastFocusColumn + Number(query.get("context_columns"));
      const firstRow = Math.max(1, focusRow - Number(query.get("context_rows")));
      const lastRow = lastFocusRow + Number(query.get("context_rows"));
      const values: Record<string, string | number> = id === archiveId
        ? {
          A1: "합성 브릿지론 현황", F2: "(단위: 억원)", A3: "대주", B3: "대출금", D3: "만기", E3: "금리", D4: "2027-05-17",
          ...Object.fromEntries(Array.from({ length: 11 }, (_, index) => [
            [`A${index + 4}`, `합성 대주 ${index + 1}`], [`B${index + 4}`, 50 - index * 2],
          ]).flat()),
          ...Object.fromEntries(rateTerms.map((term) => [`E${term.row}`, term.raw])),
        }
        : { A1: "합성 담보 현황", B3: "감정평가액", B4: 90 };
      return reply({
        schemaVersion: "realytics-workbook-evidence-preview-v1", ...workbookIdentity(id),
        sheetName: "sheet", focusRange: reference,
        previewRange: `${columnLabel(firstColumn)}${firstRow}:${columnLabel(lastColumn)}${lastRow}`,
        columns: Array.from({ length: lastColumn - firstColumn + 1 }, (_, offset) => ({ columnNumber: firstColumn + offset, label: columnLabel(firstColumn + offset) })),
        rows: Array.from({ length: lastRow - firstRow + 1 }, (_, rowOffset) => {
          const rowNumber = firstRow + rowOffset;
          return { rowNumber, cells: Array.from({ length: lastColumn - firstColumn + 1 }, (_, columnOffset) => {
            const columnNumber = firstColumn + columnOffset;
            const coordinate = columnLabel(columnNumber) + rowNumber;
            const value = values[coordinate] ?? null;
            return {
              coordinate, rowNumber, columnNumber, displayValue: value, rawValue: value,
              cachedValue: null, formula: null, numberFormat: "General", dataType: typeof value === "number" ? "n" : "s",
              valueState: value === null ? "empty" : "literal",
              focused: rowNumber >= focusRow && rowNumber <= lastFocusRow && columnNumber >= focusColumn && columnNumber <= lastFocusColumn,
            };
          }) };
        }),
        formulaPolicy: "display_only_never_executed",
      });
    }
    if (path.endsWith("/glossary/terms")) return reply({ terms: [] });
    if (!["GET", "HEAD"].includes(request.method())) state.unexpectedMutations.push(`${request.method()} ${path}`);
    return reply({});
  });
  await page.goto(reviewUrl, { waitUntil: "domcontentloaded" });
  await expect(page.getByText("파싱값 32건 · 미검토 32건 · 승인된 입력값 0건")).toBeVisible({ timeout: 20_000 });
  await selectCandidate(page, principalId);
  return state;
}

const inspector = (page: Page) => page.getByRole("complementary", { name: "문서 검토 Inspector" });
const viewer = (page: Page) => page.getByRole("region", { name: "Excel 원본 미리보기" });
const reviewAlerts = (page: Page) => page.locator('[role="alert"]:not(#__next-route-announcer__)');

async function openReview(page: Page) {
  const panel = inspector(page);
  const tab = panel.getByRole("tab", { name: "검토 결정", exact: true });
  if (!await tab.isVisible()) await panel.getByText("검토 더보기", { exact: true }).click();
  await tab.click();
  await expect(panel.getByLabel(reasonLabel)).toBeVisible();
  return panel;
}

async function selectCandidate(page: Page, candidateId: string) {
  const panel = inspector(page);
  await panel.getByRole("tab", { name: "파싱값", exact: true }).click();
  await panel.getByRole("button").filter({ has: page.getByText(`Candidate ${candidateId}`, { exact: true }) }).click();
}

test("bridge candidates focus the unique value-row evidence while retaining manual title and header navigation", async ({ page }) => {
  const state = await fixture(page);
  await expect(viewer(page).getByRole("button", { name: "B4 셀 상세", exact: true })).toBeVisible();
  await expect(viewer(page).getByRole("button", { name: "B4 셀 상세", exact: true }).locator("..")).toHaveClass(/bg-amber-100/);
  await expect(viewer(page).getByRole("button", { name: "A1 셀 상세", exact: true }).locator("..")).not.toHaveClass(/bg-amber-100/);
  expect(state.cellRequests.at(-1)?.reference).toBe("B4");

  const principalCandidate = inspector(page).getByRole("button").filter({ has: page.getByText(`Candidate ${principalId}`, { exact: true }) });
  await expect(principalCandidate.getByText("대출금", { exact: true })).toBeVisible();
  await expect(principalCandidate.getByText("금액 기준 미확인 · 계산용 승인 입력 선택 불가", { exact: true })).toBeVisible();
  const review = await openReview(page);
  await expect(review.getByText("현재 브리지 현황 · 참조용", { exact: true })).toBeVisible();
  await expect(review.getByText("원본 대조에 대한 사람의 검토 결정을 기록합니다. 기준일과 잔액·약정 기준은 미확인 상태입니다.", { exact: true })).toBeVisible();
  await expect(review.getByText("금액 기준이 확인되기 전에는 검토 결정을 저장해도 계산용 승인 입력으로 선택할 수 없습니다.", { exact: true })).toBeVisible();
  await expect(review.getByText("이 후보는 미래 PF 약정이나 현금흐름 계산 입력에 반영되지 않습니다.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "근거 상세", exact: true }).click();
  const evidence = inspector(page);
  await expect(evidence.getByText("sheet!A1", { exact: true })).toBeVisible();
  await expect(evidence.getByText("sheet!F2", { exact: true })).toBeVisible();
  await expect(evidence.getByText("sheet!B3", { exact: true })).toBeVisible();
  await expect(evidence.getByText("sheet!B4", { exact: true })).toBeVisible();
  await evidence.getByRole("button").filter({ has: page.getByText("sheet!B3", { exact: true }) }).click();
  await expect(viewer(page).getByRole("button", { name: "B3 셀 상세", exact: true }).locator("..")).toHaveClass(/bg-amber-100/);
  await evidence.getByRole("button").filter({ has: page.getByText("sheet!A1", { exact: true }) }).click();
  await expect(viewer(page).getByRole("button", { name: "A1 셀 상세", exact: true }).locator("..")).toHaveClass(/bg-amber-100/);
  await selectCandidate(page, rateId);
  const rateCandidate = inspector(page).getByRole("button").filter({ has: page.getByText(`Candidate ${rateId}`, { exact: true }) });
  await expect(rateCandidate.getByText("금리", { exact: true })).toBeVisible();
  await expect(rateCandidate.getByText("8%", { exact: true })).toBeVisible();
  await expect(viewer(page).getByRole("button", { name: "E4 셀 상세", exact: true })).toHaveText("변동 7.70%");
  await expect(viewer(page).getByRole("button", { name: "E4 셀 상세", exact: true }).locator("..")).toHaveClass(/bg-amber-100/);
  expect(state.cellRequests.at(-1)?.reference).toBe("E4");
  expect(state.reviewPosts).toEqual([]);
  expect(state.unexpectedMutations).toEqual([]);
});

test("unsent reasons belong to each candidate and restore when returning to that candidate", async ({ page }) => {
  const state = await fixture(page);
  const reason = "합성 원본 B4의 대출잔액과 원화 환산 후보를 대조했습니다.";
  await (await openReview(page)).getByLabel(reasonLabel).fill(reason);
  await selectCandidate(page, rateId);
  await expect((await openReview(page)).getByLabel(reasonLabel)).toHaveValue("");
  await selectCandidate(page, principalId);
  await expect((await openReview(page)).getByLabel(reasonLabel)).toHaveValue(reason);
  expect(state.reviewPosts).toEqual([]);
});

test("switching documents clears the prior cell inverse filter instead of hiding the next document candidates", async ({ page }) => {
  const state = await fixture(page);
  await viewer(page).getByRole("button", { name: "B4 셀 상세", exact: true }).click();
  await viewer(page).getByRole("button", { name: "이 셀과 연결된 파싱값 보기", exact: true }).click();
  await expect(inspector(page).getByText("원문 위치 → 관련 Fact 역조회", { exact: true })).toBeVisible();
  await page.getByRole("complementary", { name: "문서 및 위치 탐색" }).getByRole("button", { name: new RegExp(otherFileName) }).click();
  await expect(inspector(page).getByText("Candidate synthetic-collateral-value", { exact: true })).toBeVisible();
  await expect(inspector(page).getByText("원문 위치 → 관련 Fact 역조회", { exact: true })).toHaveCount(0);
  await expect(inspector(page).getByText("표시할 파싱값이 없습니다.", { exact: true })).toHaveCount(0);
  await expect(viewer(page).getByRole("button", { name: "B4 셀 상세", exact: true })).toHaveText("90");
  expect(state.cellRequests.at(-1)?.archiveId).toBe(otherArchiveId);
  expect(state.reviewPosts).toEqual([]);
});

test("an independent human review persists on reopen without creating canonical selections or approved inputs", async ({ page }) => {
  const state = await fixture(page);
  const reason = "합성 원본 sheet!B4의 50억원과 5,000,000,000원 후보를 대조했습니다.";
  const panel = await openReview(page);
  await panel.getByLabel(reasonLabel).fill(reason);
  await panel.getByRole("button", { name: "검토 승인", exact: true }).click();
  await expect(page.getByText("검토 승인 결정이 append-only 원장에 저장되었습니다.", { exact: false })).toBeVisible();
  expect(state.reviewPosts).toEqual([{ candidateId: principalId, body: { action: "approve", reason, expectedDecisionSequence: 0, conflictIds: [] } }]);
  expect(state.decisions[0]?.reviewerId).toBe(reviewerId);
  expect(reviewerId).not.toBe(producerId);

  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByText("파싱값 32건 · 미검토 31건 · 승인된 입력값 0건")).toBeVisible();
  await selectCandidate(page, principalId);
  const reopened = await openReview(page);
  await expect(reopened.getByText("기존 결정 1건", { exact: true })).toBeVisible();
  await expect(reopened.getByText(reason, { exact: true })).toBeVisible();
  await expect(reopened.getByText("#1 approve", { exact: true })).toBeVisible();
  await expect(reopened.getByLabel(reasonLabel)).toHaveValue("");
  expect(state.reviewPosts).toHaveLength(1);
  // Any canonical selection, approved snapshot or other mutation fails this assertion.
  expect(state.unexpectedMutations).toEqual([]);
});

test("a saved decision with a failed refresh remains saved, blocks duplicate submission and recovers by rereading", async ({ page }) => {
  const state = await fixture(page);
  state.failReloadAfterSave = true;
  const reason = "합성 B4 원본 대조 완료. 저장 후 조회 장애와 중복 제출 방지를 확인합니다.";
  const panel = await openReview(page);
  await panel.getByLabel(reasonLabel).fill(reason);
  await panel.getByRole("button", { name: "검토 승인", exact: true }).click();
  await expect(page.getByText("검토 결정은 저장되었지만 최신 검토 상태를 다시 불러오지 못했습니다.", { exact: false })).toBeVisible();
  expect(state.decisions).toHaveLength(1);
  expect(state.reviewPosts).toHaveLength(1);
  await expect(panel.getByLabel(reasonLabel)).toHaveValue(reason);
  for (const name of ["검토 승인", "반려", "보완 요청"]) {
    await expect(panel.getByRole("button", { name, exact: true })).toBeDisabled();
  }
  // Editing a stale form must not re-enable a second POST before a successful GET.
  const additionalDraft = reason + " 추가로 대조할 합성 메모";
  await panel.getByLabel(reasonLabel).fill(additionalDraft);
  await expect(panel.getByRole("button", { name: "검토 승인", exact: true })).toBeDisabled();
  state.reloadUnavailable = false;
  state.omitSavedDecisions = true;
  const retry = page.getByRole("button", { name: "저장 상태 다시 확인", exact: true });
  const requestsBefore = state.workspaceRequests;
  await retry.click();
  await expect.poll(() => state.workspaceRequests).toBeGreaterThan(requestsBefore);
  await expect(retry).toBeEnabled();
  await expect(panel.getByRole("button", { name: "검토 승인", exact: true })).toBeDisabled();
  await expect(panel.getByLabel(reasonLabel)).toHaveValue(additionalDraft);
  expect(state.reviewPosts).toHaveLength(1);
  // A successful GET without the returned decision is still stale.
  state.omitSavedDecisions = false;
  await retry.click();
  await expect(page.getByText("파싱값 32건 · 미검토 31건 · 승인된 입력값 0건")).toBeVisible();
  await expect(reviewAlerts(page)).toHaveCount(0);
  const refreshed = await openReview(page);
  await expect(refreshed.getByText("기존 결정 1건", { exact: true })).toBeVisible();
  await expect(refreshed.getByText(reason, { exact: true })).toBeVisible();
  await expect(refreshed.getByLabel(reasonLabel)).toHaveValue(additionalDraft);
  expect(state.reviewPosts).toHaveLength(1);
  expect(state.unexpectedMutations).toEqual([]);
});

for (const status of [403, 409]) {
  test(`a ${status} review refusal explains recovery and preserves the unsaved reason`, async ({ page }) => {
    const state = await fixture(page);
    state.reviewStatus = status;
    state.reviewError = status === 403
      ? { error: "parser_v2_review_pilot_mfa_required" }
      : "review decision sequence changed; reload before appending a decision";
    const reason = `합성 원본 대조 사유 ${status}: 저장 거절 후에도 보완할 수 있어야 합니다.`;
    const panel = await openReview(page);
    await panel.getByLabel(reasonLabel).fill(reason);
    await panel.getByRole("button", { name: "검토 승인", exact: true }).click();
    await expect(reviewAlerts(page)).toContainText(status === 403
      ? "검토 결정을 저장하려면 다단계 인증을 완료해 주세요."
      : "다른 검토 기록이 먼저 저장되었습니다. 최신 기록을 다시 불러온 뒤 제출해 주세요.");
    await expect(panel.getByLabel(reasonLabel)).toHaveValue(reason);
    await expect(panel.getByText("기존 결정 0건", { exact: true })).toBeVisible();
    if (status === 409) {
      await expect(panel.getByRole("button", { name: "검토 승인", exact: true })).toBeDisabled();
      await page.getByRole("button", { name: "저장 상태 다시 확인", exact: true }).click();
      await expect(panel.getByRole("button", { name: "검토 승인", exact: true })).toBeEnabled();
      await expect(panel.getByLabel(reasonLabel)).toHaveValue(reason);
    }
    expect(state.decisions).toEqual([]);
    expect(state.reviewPosts).toHaveLength(1);
    expect(state.unexpectedMutations).toEqual([]);
  });
}
