import { expect, test } from "@playwright/test";
import {
  fetchDocumentReviewData,
  fetchPdfPagePreview,
  fetchWorkbookCellPreview,
  pdfLocatorOverlay,
  recordParserV2Review,
  ParserReviewSaveError,
  resolveParserV2Conflict,
  sourceLocatorSemanticKey,
  sourceLocatorMatchesDocument,
  sourceDocumentContentUrl,
} from "../lib/document-review-workspace";

test("evidence overlays require a positive source identity and reject conflicting or array bindings", () => {
  const archiveId = "a".repeat(64);
  const document = { archiveId, documentId: "document:source", documentVersionId: "version:source" };
  const position = { sheetName: "sheet", cellReference: "B4" };
  expect(sourceLocatorMatchesDocument(position, document)).toBe(false);
  expect(sourceLocatorMatchesDocument({ ...position, fileSha256: archiveId.toUpperCase() }, document)).toBe(true);
  expect(sourceLocatorMatchesDocument({ ...position, documentVersionId: "version:source" }, document)).toBe(true);
  expect(sourceLocatorMatchesDocument({ ...position, documentId: "document:source" }, document)).toBe(true);
  expect(sourceLocatorMatchesDocument({ ...position, documentId: archiveId }, document)).toBe(true);
  expect(sourceLocatorMatchesDocument({ fileSha256: "b".repeat(64), documentVersionId: "version:source" }, document)).toBe(false);
  expect(sourceLocatorMatchesDocument({ fileSha256: archiveId, documentVersionId: "version:other" }, document)).toBe(false);
  expect(sourceLocatorMatchesDocument({ fileSha256: archiveId, documentId: "document:other" }, document)).toBe(false);
  expect(sourceLocatorMatchesDocument({ documentVersionId: "version:unverified" }, { archiveId })).toBe(false);
  expect(sourceLocatorMatchesDocument([], document)).toBe(false);
  expect(sourceLocatorMatchesDocument({ fileSha256: archiveId }, [])).toBe(false);
  expect(sourceLocatorMatchesDocument({ fileSha256: [archiveId], documentVersionId: "version:source" }, document)).toBe(false);
  expect(sourceLocatorMatchesDocument({ fileSha256: archiveId, documentVersionId: ["version:source"] }, document)).toBe(false);
  expect(sourceLocatorMatchesDocument({ fileSha256: "not-a-sha256", documentVersionId: "version:source" }, document)).toBe(false);
});

test("builds fail-closed semantic locator keys for inverse Fact navigation", () => {
  const evidenceA = sourceLocatorSemanticKey({ evidenceId: "evidence-a" });
  const evidenceB = sourceLocatorSemanticKey({ evidenceId: "evidence-b" });
  expect(evidenceA).not.toBe(evidenceB);

  const apiA = sourceLocatorSemanticKey({
    apiEndpoint: "https://example.test/transactions",
    apiRowId: "row-1",
  });
  const apiB = sourceLocatorSemanticKey({
    apiEndpoint: "https://example.test/transactions",
    apiRowId: "row-2",
  });
  expect(apiA).not.toBe(apiB);

  const artifactA = sourceLocatorSemanticKey({
    artifactKey: "artifact:pf:1",
    jsonPath: "$.riskRegister[0]",
  });
  const artifactB = sourceLocatorSemanticKey({
    artifactKey: "artifact:pf:1",
    jsonPath: "$.riskRegister[1]",
  });
  expect(artifactA).not.toBe(artifactB);

  const bboxA = sourceLocatorSemanticKey({
    documentVersionId: "document-v1",
    fileSha256: "a".repeat(64),
    pageNumber: 7,
    bbox: [10, 20, 30, 40],
    coordinateSpace: "pdf_points_top_left",
  });
  const bboxB = sourceLocatorSemanticKey({
    documentVersionId: "document-v1",
    fileSha256: "b".repeat(64),
    pageNumber: 7,
    bbox: [10, 20, 30, 40],
    coordinateSpace: "pdf_points_top_left",
  });
  expect(bboxA).not.toBe(bboxB);

  expect(
    sourceLocatorSemanticKey({ pageNumber: 7 }, "document-v1"),
  ).not.toBe(sourceLocatorSemanticKey({ pageNumber: 7 }, "document-v2"));
  expect(sourceLocatorSemanticKey({})).toBeNull();
});

test("loads one versioned server read model without browser-side run-package assembly", async () => {
  const originalFetch = globalThis.fetch;
  const requested: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    requested.push(url);
    if (url.endsWith("/projects/project%2F1/workspace/document-review")) {
      return jsonResponse({
        schemaVersion: "realytics-document-review-workspace-v1",
        viewModelVersion: "document-review-workspace-2026-07-27",
        projectId: "project/1",
        snapshotId: "document-review:project/1:" + "c".repeat(20),
        snapshotHash: "c".repeat(64),
        observedAt: "2026-07-27T02:00:00+00:00",
        asOfDate: "2026-07-27T01:00:00+00:00",
        freshness: { status: "observed" },
        sourceDocuments: [{ archiveId: "a".repeat(64), originalFileName: "plan.pdf", downloadUrl: "/projects/project%2F1/source-documents/archive/download" }],
        runs: [{ parsingRunId: "run-1", documentVersionId: "document-v1", executionStatus: "completed" }],
        packages: [{
          schemaVersion: "realytics-parser-v2-run-package-v2",
          candidateOnly: true,
          authoritative: false,
          projectProjectionMutated: false,
          parsingRun: { parsingRunId: "run-1", documentVersionId: "document-v1", executionStatus: "completed" },
          documentVersion: { documentVersionId: "document-v1", archiveId: "a".repeat(64), fileName: "plan.pdf" },
          candidates: [{
            candidateId: "candidate-1",
            documentVersionId: "document-v1",
            fieldKey: "feasibility.total_project_cost",
            normalizedValue: 100,
            unit: "KRW",
            reviewStatus: "pending",
            approvalStatus: "not_requested",
            validationStatus: "passed",
            evidence: [{ documentVersionId: "document-v1", pageNumber: 7, tableId: "cost", rowIndex: 3, rawTextSha256: "b".repeat(64) }],
          }],
          conflicts: [],
          reviewDecisions: [],
          canonicalSelections: [],
          approvedInputSnapshots: [],
        }],
        glossaryCandidates: [],
        reviewQueue: [],
        snapshotDiffs: [],
        warnings: [],
      });
    }
    return new Response(null, { status: 404 });
  }) as typeof fetch;

  try {
    const result = await fetchDocumentReviewData("project/1");
    expect(result.sourceDocuments).toHaveLength(1);
    expect(result.packages).toHaveLength(1);
    expect(result.packages[0]?.candidateOnly).toBe(true);
    expect(result.packages[0]?.authoritative).toBe(false);
    expect(result.packages[0]?.candidates[0]?.evidence?.[0]).toMatchObject({ pageNumber: 7, tableId: "cost", rowIndex: 3 });
    expect(result.warnings).toEqual([]);
    expect(result.snapshotHash).toBe("c".repeat(64));
    expect(requested).toEqual(["/api/backend/projects/project%2F1/workspace/document-review"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects an unversioned or unsealed document review response", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () => jsonResponse({
    schemaVersion: "realytics-document-review-workspace-v1",
    viewModelVersion: "document-review-workspace-2026-07-27",
    snapshotId: "unsafe",
    snapshotHash: "not-a-sha256",
  })) as typeof fetch;

  try {
    await expect(fetchDocumentReviewData("project-1")).rejects.toThrow("버전 또는 무결성 계약");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("submits only the existing append-only Parser V2 review contract", async () => {
  const originalFetch = globalThis.fetch;
  let request: { url: string; init?: RequestInit } | undefined;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    request = { url: String(input), init };
    return jsonResponse({
      reviewDecisionId: "decision-2",
      candidateId: "candidate-1",
      decisionSequence: 2,
      action: "request_revision",
      resultingReviewStatus: "needs_revision",
      resultingApprovalStatus: "pending",
    }, 201);
  }) as typeof fetch;

  try {
    await recordParserV2Review({
      projectId: "project/1",
      candidateId: "candidate-1",
      expectedDecisionSequence: 1,
      action: "request_revision",
      reason: "페이지와 표 좌표의 재확인이 필요합니다.",
      conflictIds: ["conflict-1"],
    });
    expect(request?.url).toBe("/api/backend/projects/project%2F1/parsing-v2/candidates/candidate-1/reviews");
    expect(request?.init?.method).toBe("POST");
    expect(JSON.parse(String(request?.init?.body))).toEqual({
      expectedDecisionSequence: 1,
      action: "request_revision",
      reason: "페이지와 표 좌표의 재확인이 필요합니다.",
      conflictIds: ["conflict-1"],
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("review save failures explain server gates without reflecting internal responses", async () => {
  const originalFetch = globalThis.fetch;
  const input = { projectId: "project-1", candidateId: "candidate-1", expectedDecisionSequence: 0,
    action: "approve" as const, reason: "원본 대조 사유", conflictIds: [] };
  const cases = [
    { status: 401, detail: "private-auth-detail", message: "다시 로그인", reload: false },
    { status: 403, detail: { error: "parser_v2_review_pilot_mfa_required" }, message: "다단계 인증", reload: false },
    { status: 403, detail: { error: "parser_v2_review_pilot_identity_not_allowlisted" }, message: "검토 권한", reload: false },
    { status: 409, detail: "maker-checker separation prohibits reviewing a candidate created by the same actor", message: "다른 검토자", reload: false },
    { status: 409, detail: "review decision sequence changed; reload before appending a decision", message: "최신 기록", reload: true },
    { status: 503, detail: { error: "parser_v2_review_pilot_disabled", message: "private-environment-detail" }, message: "검토 기능 설정", reload: false },
    { status: 500, detail: "private-database-detail", message: "결과를 확인하지 못했습니다", reload: true },
    { status: 422, detail: "private-validation-detail", message: "결정 사유", reload: false },
  ];
  try {
    for (const item of cases) {
      let calls = 0;
      globalThis.fetch = (async () => { calls += 1; return jsonResponse({ detail: item.detail }, item.status); }) as typeof fetch;
      const error = await recordParserV2Review(input).then(() => null, (caught: unknown) => caught);
      expect(error).toBeInstanceOf(ParserReviewSaveError);
      expect((error as ParserReviewSaveError).status).toBe(item.status);
      expect((error as ParserReviewSaveError).requiresReload).toBe(item.reload);
      expect((error as Error).message).toContain(item.message);
      expect((error as Error).message).not.toMatch(/private-|parser_v2_|maker-checker|sequence changed/);
      expect(calls).toBe(1);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("uncertain or mismatched save responses require a fresh read and never automatically retry", async () => {
  const originalFetch = globalThis.fetch;
  const input = { projectId: "project-1", candidateId: "candidate-1", expectedDecisionSequence: 0,
    action: "request_revision" as const, reason: "작성 사유 유지", conflictIds: [] };
  const replies = [
    () => { throw new Error("private-network-detail"); },
    () => new Response("not-json", { status: 201 }),
    () => jsonResponse({ reviewDecisionId: "decision-1", candidateId: "another-candidate", decisionSequence: 1, action: "request_revision" }, 201),
    () => jsonResponse({ reviewDecisionId: "decision-1", candidateId: "candidate-1", decisionSequence: 0, action: "request_revision" }, 201),
    () => jsonResponse({ reviewDecisionId: "decision-1", candidateId: "candidate-1", decisionSequence: 1, action: "approve" }, 201),
  ];
  try {
    for (const reply of replies) {
      let calls = 0;
      globalThis.fetch = (async () => { calls += 1; return reply(); }) as typeof fetch;
      const error = await recordParserV2Review(input).then(() => null, (caught: unknown) => caught);
      expect(error).toBeInstanceOf(ParserReviewSaveError);
      expect((error as ParserReviewSaveError).requiresReload).toBe(true);
      expect((error as Error).message).not.toContain("private-");
      expect(calls).toBe(1);
      expect(input.reason).toBe("작성 사유 유지");
    }
    for (const sequence of [1, 99]) {
      globalThis.fetch = (async () => jsonResponse({ reviewDecisionId: "another-sequence-decision", candidateId: "candidate-1",
        decisionSequence: sequence, action: "request_revision" }, 201)) as typeof fetch;
      const error = await recordParserV2Review({ ...input, expectedDecisionSequence: 3 }).then(() => null, (caught: unknown) => caught);
      expect(error).toBeInstanceOf(ParserReviewSaveError);
      expect((error as ParserReviewSaveError).requiresReload).toBe(true);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("resolves a conflict only through an existing approved review decision reference", async () => {
  const originalFetch = globalThis.fetch;
  let request: { url: string; init?: RequestInit } | undefined;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    request = { url: String(input), init };
    return jsonResponse({ conflictId: "conflict-1", conflictStatus: "resolved" }, 201);
  }) as typeof fetch;

  try {
    await resolveParserV2Conflict({
      projectId: "project/1",
      conflictId: "conflict/1",
      reviewDecisionId: "decision-2",
      expectedDecisionSequence: 2,
      notes: "승인된 winning candidate 적용",
    });
    expect(request?.url).toBe("/api/backend/projects/project%2F1/parsing-v2/conflicts/conflict%2F1/resolve");
    expect(request?.init?.method).toBe("POST");
    expect(JSON.parse(String(request?.init?.body))).toEqual({
      reviewDecisionId: "decision-2",
      expectedDecisionSequence: 2,
      resolutionStatus: "resolved",
      notes: "승인된 winning candidate 적용",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("builds same-origin authenticated content URLs and reads real PDF geometry headers", async () => {
  expect(sourceDocumentContentUrl("project/1", "archive 1")).toBe(
    "/api/backend/projects/project%2F1/source-documents/archive%201/content",
  );
  const originalFetch = globalThis.fetch;
  let requested = "";
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    requested = String(input);
    return new Response(new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" }), {
      status: 200,
      headers: {
        "Content-Type": "image/png",
        "X-REALYTICS-Page-Width": "595",
        "X-REALYTICS-Page-Height": "842",
        "X-REALYTICS-Page-Count": "12",
        "X-REALYTICS-Coordinate-Space": "pdf_points_top_left",
      },
    });
  }) as typeof fetch;

  try {
    const result = await fetchPdfPagePreview({
      projectId: "project/1",
      archiveId: "archive 1",
      pageNumber: 7,
    });
    expect(requested).toBe(
      "/api/backend/projects/project%2F1/source-documents/archive%201/pdf/pages/7.png?scale=1.5",
    );
    expect(result).toMatchObject({
      pageNumber: 7,
      pageCount: 12,
      pageWidth: 595,
      pageHeight: 842,
      coordinateSpace: "pdf_points_top_left",
    });
    expect(result.blob.type).toBe("image/png");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("requests a bounded workbook preview using the exact sheet and cell locator", async () => {
  const originalFetch = globalThis.fetch;
  let requested = "";
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    requested = String(input);
    return jsonResponse({
      schemaVersion: "realytics-workbook-evidence-preview-v1",
      sheetName: "사업수지",
      focusRange: "C2:C3",
      previewRange: "B2:D3",
      columns: [{ columnNumber: 2, label: "B" }],
      rows: [{
        rowNumber: 2,
        cells: [{
          coordinate: "C2",
          rowNumber: 2,
          columnNumber: 3,
          displayValue: 125000000,
          formula: null,
          dataType: "n",
          focused: true,
        }],
      }],
      formulaPolicy: "display_only_never_executed",
    });
  }) as typeof fetch;

  try {
    const result = await fetchWorkbookCellPreview({
      projectId: "project/1",
      archiveId: "archive 1",
      sheetName: "사업수지",
      cellReference: "C2:C3",
      contextRows: 0,
      contextColumns: 1,
    });
    expect(requested).toContain("/workbook/cells?");
    expect(requested).toContain("sheet_name=%EC%82%AC%EC%97%85%EC%88%98%EC%A7%80");
    expect(requested).toContain("cell_reference=C2%3AC3");
    expect(result.rows[0]?.cells[0]).toMatchObject({ coordinate: "C2", focused: true });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("draws a PDF overlay only from a valid stored top-left bbox locator", () => {
  const page = {
    pageWidth: 600,
    pageHeight: 800,
    coordinateSpace: "pdf_points_top_left",
  };
  expect(pdfLocatorOverlay({
    pageNumber: 1,
    bbox: [60, 80, 180, 240],
    coordinateSpace: "pdf_points_top_left",
  }, page)).toMatchObject({
    leftPercent: 10,
    topPercent: 10,
    widthPercent: 20,
    heightPercent: 20,
  });
  expect(pdfLocatorOverlay({ pageNumber: 1 }, page)).toBeNull();
  expect(pdfLocatorOverlay({
    pageNumber: 1,
    metadata: {
      bbox: [60, 80, 180, 240],
      bboxCoordinateSystem: "pdf_points_bottom_left",
    },
  }, page)).toBeNull();
  expect(pdfLocatorOverlay({
    pageNumber: 1,
    bbox: [60, 80, 700, 240],
    coordinateSpace: "pdf_points_top_left",
  }, page)).toBeNull();
});

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
