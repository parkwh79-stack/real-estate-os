import { expect, test, type Page } from "@playwright/test";

const projectId = "workbook-original-preview";
const archiveId = "9".repeat(64);
const otherArchiveId = "a".repeat(64);
const fileName = "합성검증_상봉_브릿지론현황.xlsx";
const identity = { archiveId, sourceSha256: archiveId, fileName, archiveVersionId: "source-v1", archiveETag: "etag-v1", archiveIntegrityStatus: "verified", analysisOnly: true, approvalEligible: false };

async function fixture(page: Page, options: { candidate?: boolean; viewer?: boolean; sheetsStatus?: number; mismatchedCells?: boolean } = {}) {
  const state = { candidate: Boolean(options.candidate), cellRequests: [] as URLSearchParams[], extractions: [] as Record<string, unknown>[], mutations: [] as string[], sheetsStatus: options.sheetsStatus ?? 200, mismatchedCells: options.mismatchedCells ?? false };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({ json, status });
    if (path.endsWith("/auth/me")) return reply({ authenticated: true, user: { userId: "workbook-viewer", tenantId: "preview-tenant", role: options.viewer ? "viewer" : "analyst" }, permissions: ["project:read", "evidence:read", ...(options.viewer ? [] : ["project:analyze"])] });
    if (path.endsWith("/auth/ai-credentials/current")) return reply({ revoked: true });
    if (path.endsWith("/projects")) return reply([{ project: { id: projectId, name: "합성 원본 미리보기" } }]);
    if (path.endsWith("/workspace/context")) return reply({ projectId, projectMemory: { projectName: "합성 원본 미리보기" } });
    if (path.endsWith("/workspace/document-review")) return reply({
      schemaVersion: "realytics-document-review-workspace-v1", viewModelVersion: "document-review-workspace-2026-07-27", projectId, snapshotId: "original-preview-view", snapshotHash: "f".repeat(64),
      sourceDocuments: [
        { archiveId: otherArchiveId, originalFileName: "합성검증_QA.xlsx", contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", uploadedAt: "2026-11-06T00:00:00Z" },
        { archiveId, originalFileName: fileName, contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", archiveIntegrityStatus: "verified", uploadedAt: "2026-11-05T00:00:00Z" },
      ],
      runs: [], packages: [{
        parsingRun: { parsingRunId: state.candidate ? "run-new" : "run-empty-old", documentVersionId: "bridge-v1", profileId: "realytics.source_review_candidates", profileVersion: state.candidate ? "1.1.0" : "1.0.0", executionStatus: "completed" },
        documentVersion: { documentVersionId: "bridge-v1", archiveId, sourceHash: archiveId, fileName, mediaType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
        candidates: state.candidate ? [{ candidateId: "principal-candidate", documentVersionId: "bridge-v1", fieldKey: "financing.existing_obligation.principal", normalizedValue: 5_000_000_000, unit: "KRW", reviewStatus: "pending", approvalStatus: "not_requested", validationStatus: "warning", evidence: [{ documentVersionId: "bridge-v1", fileSha256: archiveId, sheetName: "sheet", cellReference: "B4" }] }] : [],
        conflicts: [], reviewDecisions: [], canonicalSelections: [], approvedInputSnapshots: [],
      }], glossaryCandidates: [], reviewQueue: [], snapshotDiffs: [], warnings: [],
    });
    if (path.endsWith("/workbook/sheets")) return reply({
      schemaVersion: "realytics-workbook-original-sheets-v1", ...identity,
      sheets: [
        { sheetName: "sheet", sheetIndex: 0, rowCount: 107, columnCount: 7, declaredRowCount: 107, declaredColumnCount: 7, state: "visible", dimensionsTruncated: false },
        { sheetName: "담보 현황", sheetIndex: 1, rowCount: 5, columnCount: 3, declaredRowCount: 5, declaredColumnCount: 3, state: "visible", dimensionsTruncated: false },
      ], limits: { maxRows: 10000, maxColumns: 512, maxCells: 400, maxSheets: 100 }, defaultSheetName: "sheet", defaultRange: "A1:G20", warnings: [], truncated: false,
    }, state.sheetsStatus);
    if (path.endsWith("/workbook/cells")) {
      const query = new URL(request.url()).searchParams; state.cellRequests.push(query);
      const reference = query.get("cell_reference")!; const match = reference.match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/)!;
      const col = (label: string) => [...label].reduce((value, char) => value * 26 + char.charCodeAt(0) - 64, 0);
      const label = (number: number) => number <= 26 ? String.fromCharCode(64 + number) : "AA";
      const focusFirstColumn = col(match[1]!); const focusLastColumn = col(match[3] ?? match[1]!);
      const focusFirstRow = Number(match[2]); const focusLastRow = Number(match[4] ?? match[2]);
      const firstRow = Math.max(1, focusFirstRow - Number(query.get("context_rows"))); const lastRow = focusLastRow + Number(query.get("context_rows"));
      const firstColumn = Math.max(1, focusFirstColumn - Number(query.get("context_columns"))); const lastColumn = focusLastColumn + Number(query.get("context_columns"));
      const sourceValues: Record<string, unknown> = query.get("sheet_name") === "담보 현황" ? { A1: "담보 제공 현황", B3: 0 } : { A1: "합성 상봉 브릿지론 현황", F2: "(단위: 억원)", A3: "대주명", B3: "대출 잔액", B4: 50.4, C4: 0.055, D4: "2025-06-17", E4: 100.8, G4: "#DIV/0!", A21: "다음 행의 원본" };
      const rows = Array.from({ length: lastRow - firstRow + 1 }, (_, index) => {
        const rowNumber = firstRow + index;
        return { rowNumber, cells: Array.from({ length: lastColumn - firstColumn + 1 }, (_, offset) => {
          const columnNumber = firstColumn + offset; const coordinate = label(columnNumber) + rowNumber;
          const formula = coordinate === "E4" ? "=B4*2" : coordinate === "F4" ? "=X999" : coordinate === "G4" ? "=1/0" : null;
          const displayValue = sourceValues[coordinate] ?? null;
          return { coordinate, rowNumber, columnNumber, displayValue, rawValue: formula ?? displayValue, cachedValue: formula ? displayValue : null, formula, dataType: formula ? "f" : typeof displayValue === "number" ? "n" : "s", valueState: formula ? displayValue === null ? "formula_cache_missing" : coordinate === "G4" ? "formula_cache_error" : "formula_cached" : displayValue === null ? "empty" : "literal", numberFormat: coordinate === "C4" ? "0.0%" : "General", focused: rowNumber >= focusFirstRow && rowNumber <= focusLastRow && columnNumber >= focusFirstColumn && columnNumber <= focusLastColumn };
        }) };
      });
      return reply({ schemaVersion: "realytics-workbook-evidence-preview-v1", ...identity, sourceSha256: state.mismatchedCells ? "b".repeat(64) : archiveId, sheetName: query.get("sheet_name"), focusRange: reference, previewRange: `${label(firstColumn)}${firstRow}:${label(lastColumn)}${lastRow}`, columns: Array.from({ length: lastColumn - firstColumn + 1 }, (_, index) => ({ columnNumber: firstColumn + index, label: label(firstColumn + index) })), rows, formulaPolicy: "display_only_never_executed" });
    }
    if (path.endsWith("/parsing-v2/runs") && request.method() === "POST") {
      const body = request.postDataJSON(); state.extractions.push(body); if (body.archiveId === archiveId) state.candidate = true;
      return reply({ candidateOnly: true, authoritative: false, parsingRun: { projectId, parsingRunId: "run-new", executionStatus: "completed" }, candidateCollection: { candidateCount: body.archiveId === archiveId ? 1 : 0 } }, 201);
    }
    if (path.endsWith("/glossary/terms")) return reply({ terms: [] });
    if (!["GET", "HEAD"].includes(request.method())) state.mutations.push(path);
    return reply({});
  });
  await page.goto(`/project/${projectId}/documents/review?archiveId=${archiveId}`, { waitUntil: "domcontentloaded" });
  return state;
}

test("zero-candidate Excel originals remain readable with sheets, bounded windows and formula/cache distinctions", async ({ page }) => {
  const state = await fixture(page, { viewer: true });
  const viewer = page.getByRole("region", { name: "Excel 원본 미리보기" });
  await expect(viewer.getByRole("table", { name: "sheet A1:G20" })).toBeVisible({ timeout: 20_000 });
  await expect(viewer.getByText("합성 상봉 브릿지론 현황", { exact: true })).toBeVisible();
  await expect(viewer.getByText("(단위: 억원)", { exact: true })).toBeVisible();
  await expect(viewer.getByRole("button", { name: "B4 셀 상세" })).toHaveText("50");
  await expect(viewer.getByRole("button", { name: "C4 셀 상세" })).toHaveText("6%");
  await expect(viewer.getByRole("button", { name: "F4 셀 상세" })).toContainText("수식 · 저장 결과 없음");
  await expect(viewer.getByRole("button", { name: "G4 셀 상세" })).toContainText("수식 · 저장 결과 오류");
  await viewer.getByRole("button", { name: "E4 셀 상세" }).click();
  await expect(viewer.getByText("=B4*2", { exact: true })).toBeVisible();
  await expect(viewer.getByText("원본 저장 결과 101 · 재계산하지 않음")).toBeVisible();
  await expect(page.getByText("파싱값 0건 · 미검토 0건 · 승인된 입력값 0건")).toBeVisible();
  await expect(page.getByText("저장된 검토 후보가 아직 없습니다.", { exact: false })).toBeVisible();
  await viewer.getByRole("button", { name: "다음 Excel 행" }).click();
  await expect(viewer.getByText("다음 행의 원본", { exact: true })).toBeVisible();
  expect(state.cellRequests.at(-1)?.get("cell_reference")).toBe("A21:G40");
  await viewer.getByLabel("Excel 시트 선택").selectOption("담보 현황");
  await expect(viewer.getByText("담보 제공 현황", { exact: true })).toBeVisible();
  await expect(viewer.getByRole("button", { name: "B3 셀 상세" })).toHaveText("0");
  await viewer.getByLabel("Excel 셀 범위").fill("A1:ZZ9999");
  const requestsBefore = state.cellRequests.length;
  await viewer.getByRole("button", { name: "범위 보기" }).click();
  await expect(viewer.getByRole("alert")).toContainText("최대 400셀");
  expect(state.cellRequests).toHaveLength(requestsBefore);
  expect(state.mutations).toEqual([]); expect(state.extractions).toEqual([]);
});

test("an existing empty extraction can be rerun explicitly without promoting preview cells or approval", async ({ page }) => {
  const state = await fixture(page);
  const viewer = page.getByRole("region", { name: "Excel 원본 미리보기" });
  await expect(viewer.getByRole("table")).toBeVisible({ timeout: 20_000 });
  const action = page.getByRole("region", { name: "보관 원본값 추출" });
  // One old completed/empty result plus one never-extracted QA original.
  await action.getByRole("button", { name: "보관 원본값 추출", exact: true }).click();
  await expect(page.getByText("파싱값 1건 · 미검토 1건 · 승인된 입력값 0건")).toBeVisible();
  expect(state.extractions.find((item) => item.archiveId === archiveId)).toMatchObject({ profileVersion: "1.1.0" });
  expect(String(state.extractions.find((item) => item.archiveId === archiveId)?.idempotencyKey)).toContain("source-review-v1-1-0-");
  expect(String(state.extractions.find((item) => item.archiveId === archiveId)?.idempotencyKey)).not.toBe("source-review-v1-1-0-" + archiveId);
  await viewer.getByRole("button", { name: "B4 셀 상세" }).click();
  await expect(viewer.getByRole("button", { name: "이 셀과 연결된 파싱값 보기" })).toBeVisible();
  await viewer.getByLabel("Excel 시트 선택").selectOption("담보 현황");
  await expect(viewer.getByText("담보 제공 현황", { exact: true })).toBeVisible();
  await expect(viewer.getByRole("button", { name: "이 셀과 연결된 파싱값 보기" })).toHaveCount(0);
  await action.getByRole("button", { name: "값이 없는 문서 다시 추출", exact: true }).click();
  await expect.poll(() => state.extractions.filter((item) => item.archiveId === otherArchiveId).length).toBe(2);
  const repeatedEmpty = state.extractions.filter((item) => item.archiveId === otherArchiveId);
  expect(repeatedEmpty[1]?.idempotencyKey).not.toBe(repeatedEmpty[0]?.idempotencyKey);
  await expect(page.getByText("파싱값 1건 · 미검토 1건 · 승인된 입력값 0건")).toBeVisible();
  expect(state.mutations).toEqual([]);
});

test("unreadable and mismatched Excel previews expose a retry and never display stale cells", async ({ page }) => {
  const state = await fixture(page, { sheetsStatus: 415 });
  const viewer = page.getByRole("region", { name: "Excel 원본 미리보기" });
  await expect(viewer.getByRole("alert")).toContainText("Excel 형식(XLSX·XLSM)이 아닙니다");
  await expect(viewer.getByRole("table")).toHaveCount(0);
  state.sheetsStatus = 200; state.mismatchedCells = true;
  await viewer.getByRole("button", { name: "원본 다시 읽기" }).click();
  await expect(viewer.getByRole("alert")).toContainText("버전·무결성을 확인할 수 없습니다");
  await expect(viewer.getByRole("table")).toHaveCount(0);
  state.mismatchedCells = false;
  await viewer.getByRole("button", { name: "원본 다시 읽기" }).click();
  await expect(viewer.getByText("합성 상봉 브릿지론 현황", { exact: true })).toBeVisible();
  expect(state.mutations).toEqual([]);
});
