import { apiBase, authHeaders } from "./auth-client";

export type JsonRecord = Record<string, unknown>;

export type SourceDocumentRecord = JsonRecord & {
  archiveId?: string;
  originalFileHash?: string;
  sha256?: string;
  originalFileName?: string;
  fileName?: string;
  documentType?: string;
  contentType?: string;
  mediaType?: string;
  fileSizeBytes?: number;
  sizeBytes?: number;
  uploadedAt?: string;
  createdAt?: string;
  validationStatus?: string;
  archiveIntegrityStatus?: string;
  sourceAuthority?: string;
  downloadUrl?: string;
};

export type SourceLocatorRecord = JsonRecord & {
  evidenceId?: string;
  documentId?: string;
  documentName?: string;
  documentVersionId?: string;
  fileSha256?: string;
  page?: number;
  pageNumber?: number;
  sheet?: string;
  sheetName?: string;
  cell?: string;
  cellReference?: string;
  cellRange?: string;
  tableId?: string;
  row?: number;
  rowIndex?: number;
  column?: number;
  columnIndex?: number;
  paragraphIndex?: number;
  bbox?: number[];
  coordinateSpace?: string;
  jsonPath?: string;
  rawTextExcerpt?: string;
  rawTextSha256?: string;
  parserMethod?: string;
  sourceUrl?: string;
  apiEndpoint?: string;
  apiRowId?: string;
  artifactKey?: string;
  metadata?: JsonRecord;
};

function locatorText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  return normalized || null;
}

/** Missing identity permits reading the original, never an evidence overlay. */
export function sourceLocatorMatchesDocument(locator: unknown, document: unknown): boolean {
  if (!locator || typeof locator !== "object" || Array.isArray(locator)
    || !document || typeof document !== "object" || Array.isArray(document)) return false;
  const source = locator as JsonRecord;
  const target = document as JsonRecord;
  const identity = (record: JsonRecord, key: string): string | null => {
    const value = record[key];
    return value === null || value === undefined ? "" : typeof value === "string" ? value.trim() : null;
  };
  const hash = identity(source, "fileSha256");
  const archive = identity(target, "archiveId");
  const version = identity(source, "documentVersionId");
  const documentVersion = identity(target, "documentVersionId");
  const sourceDocument = identity(source, "documentId");
  const targetDocument = identity(target, "documentId");
  if ([hash, archive, version, documentVersion, sourceDocument, targetDocument].some((value) => value === null)) return false;
  if ((hash && !/^[0-9a-f]{64}$/i.test(hash)) || (archive && !/^[0-9a-f]{64}$/i.test(archive))) return false;
  let matched = false;
  if (hash && archive) {
    if (hash.toLowerCase() !== archive.toLowerCase()) return false;
    matched = true;
  }
  if (version && documentVersion) {
    if (version !== documentVersion) return false;
    matched = true;
  }
  if (sourceDocument) {
    if (sourceDocument === targetDocument || (archive && sourceDocument.toLowerCase() === archive.toLowerCase())) matched = true;
    else if (targetDocument) return false;
  }
  return matched;
}

function locatorNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const normalized = Number(value);
  return Number.isFinite(normalized) ? normalized : null;
}

/**
 * Stable inverse-navigation identity for an external evidence location.
 *
 * Coordinate identities take precedence over evidence IDs so multiple Facts
 * may point to one real source location. Empty or unbound locators return null
 * and must never be grouped together by callers.
 */
export function sourceLocatorSemanticKey(
  locator: SourceLocatorRecord,
  fallbackDocumentVersionId?: string,
): string | null {
  const metadata =
    locator.metadata && typeof locator.metadata === "object" && !Array.isArray(locator.metadata)
      ? locator.metadata
      : {};
  const binding = {
    documentVersionId:
      locatorText(locator.documentVersionId) ??
      locatorText(fallbackDocumentVersionId),
    fileSha256: locatorText(locator.fileSha256),
    documentId: locatorText(locator.documentId),
  };
  const hasDocumentBinding = Object.values(binding).some(Boolean);
  const page = locatorNumber(locator.pageNumber ?? locator.page);
  const sheet = locatorText(locator.sheetName ?? locator.sheet);
  const cell = locatorText(locator.cellReference ?? locator.cellRange ?? locator.cell);
  const tableId = locatorText(locator.tableId);
  const row = locatorNumber(locator.rowIndex ?? locator.row);
  const column = locatorNumber(locator.columnIndex ?? locator.column);
  const paragraphIndex = locatorNumber(locator.paragraphIndex);
  const jsonPath = locatorText(locator.jsonPath);
  const apiEndpoint = locatorText(locator.apiEndpoint ?? locator.sourceUrl);
  const apiRowId = locatorText(locator.apiRowId);
  const artifactKey = locatorText(locator.artifactKey);
  const rawTextSha256 = locatorText(locator.rawTextSha256);
  const rawBbox = Array.isArray(locator.bbox) ? locator.bbox : metadata.bbox;
  const bbox = Array.isArray(rawBbox) && rawBbox.length === 4
    ? rawBbox.map(locatorNumber)
    : null;
  const validBbox = bbox?.every((value) => value !== null) ? bbox : null;
  const coordinateSpace = locatorText(
    locator.coordinateSpace ?? metadata.bboxCoordinateSystem,
  );

  if (apiEndpoint && apiRowId) {
    return JSON.stringify({ v: 1, kind: "api", apiEndpoint, apiRowId });
  }
  if (artifactKey) {
    return JSON.stringify({ v: 1, kind: "artifact", artifactKey, jsonPath });
  }
  if (hasDocumentBinding && sheet && cell) {
    return JSON.stringify({ v: 1, kind: "excel", binding, sheet, cell });
  }
  if (hasDocumentBinding && page !== null && validBbox) {
    return JSON.stringify({
      v: 1,
      kind: "pdf_bbox",
      binding,
      page,
      bbox: validBbox,
      coordinateSpace,
    });
  }
  if (hasDocumentBinding && page !== null) {
    return JSON.stringify({
      v: 1,
      kind: "page",
      binding,
      page,
      tableId,
      row,
      column,
    });
  }
  if (hasDocumentBinding && paragraphIndex !== null) {
    return JSON.stringify({
      v: 1,
      kind: "paragraph",
      binding,
      paragraphIndex,
    });
  }
  if (hasDocumentBinding && jsonPath) {
    return JSON.stringify({ v: 1, kind: "json_path", binding, jsonPath });
  }
  if (hasDocumentBinding && rawTextSha256) {
    return JSON.stringify({
      v: 1,
      kind: "text_hash",
      binding,
      rawTextSha256,
    });
  }
  const evidenceId = locatorText(locator.evidenceId);
  return evidenceId
    ? JSON.stringify({ v: 1, kind: "evidence", evidenceId })
    : null;
}

export type PdfPagePreview = {
  blob: Blob;
  pageNumber: number;
  pageCount: number;
  pageWidth: number;
  pageHeight: number;
  coordinateSpace: string;
};

export type WorkbookPreviewCell = {
  coordinate: string;
  rowNumber: number;
  columnNumber: number;
  displayValue: unknown;
  formula?: string | null;
  dataType: string;
  focused: boolean;
  rawValue?: unknown;
  cachedValue?: unknown;
  valueState?: "literal" | "formula_cached" | "formula_cache_missing" | "formula_cache_error" | "empty" | "error";
  numberFormat?: string;
};

export type WorkbookOriginalSheets = {
  schemaVersion: "realytics-workbook-original-sheets-v1";
  archiveId: string;
  sourceSha256: string;
  fileName: string;
  archiveVersionId?: string | null;
  archiveETag?: string | null;
  archiveIntegrityStatus: "verified";
  sheets: Array<{
    sheetName: string;
    sheetIndex: number;
    rowCount: number;
    columnCount: number;
    declaredRowCount: number | null;
    declaredColumnCount: number | null;
    state: string;
    dimensionsTruncated: boolean;
  }>;
  limits: { maxRows: number; maxColumns: number; maxCells: number; maxSheets: number };
  defaultSheetName: string | null;
  defaultRange: string | null;
  warnings: string[];
  truncated: boolean;
  analysisOnly: true;
  approvalEligible: false;
};

export type WorkbookCellPreview = {
  schemaVersion: "realytics-workbook-evidence-preview-v1";
  sheetName: string;
  focusRange: string;
  previewRange: string;
  columns: Array<{ columnNumber: number; label: string }>;
  rows: Array<{ rowNumber: number; cells: WorkbookPreviewCell[] }>;
  formulaPolicy: "display_only_never_executed";
  archiveId?: string;
  sourceSha256?: string;
  fileName?: string;
  archiveVersionId?: string | null;
  archiveETag?: string | null;
  archiveIntegrityStatus?: string;
  analysisOnly?: boolean;
  approvalEligible?: boolean;
};

export type PdfLocatorOverlay = {
  bbox: [number, number, number, number];
  leftPercent: number;
  topPercent: number;
  widthPercent: number;
  heightPercent: number;
};

export function pdfLocatorOverlay(
  locator: SourceLocatorRecord,
  page: { pageWidth: number; pageHeight: number; coordinateSpace: string },
): PdfLocatorOverlay | null {
  const metadata = locator.metadata && typeof locator.metadata === "object" && !Array.isArray(locator.metadata)
    ? locator.metadata
    : {};
  const candidate = Array.isArray(locator.bbox) ? locator.bbox : metadata.bbox;
  if (!Array.isArray(candidate) || candidate.length !== 4) return null;
  const numbers = candidate.map(Number);
  if (!numbers.every(Number.isFinite)) return null;
  const [left, top, right, bottom] = numbers as [number, number, number, number];
  const locatorSpace = String(locator.coordinateSpace ?? metadata.bboxCoordinateSystem ?? "").trim();
  if (
    locatorSpace !== "pdf_points_top_left"
    || page.coordinateSpace !== "pdf_points_top_left"
    || !Number.isFinite(page.pageWidth)
    || !Number.isFinite(page.pageHeight)
    || page.pageWidth <= 0
    || page.pageHeight <= 0
    || left < 0
    || top < 0
    || right <= left
    || bottom <= top
    || right > page.pageWidth
    || bottom > page.pageHeight
  ) return null;
  return {
    bbox: [left, top, right, bottom],
    leftPercent: (left / page.pageWidth) * 100,
    topPercent: (top / page.pageHeight) * 100,
    widthPercent: ((right - left) / page.pageWidth) * 100,
    heightPercent: ((bottom - top) / page.pageHeight) * 100,
  };
}

export type ParserV2Run = JsonRecord & {
  parsingRunId?: string;
  documentVersionId?: string;
  executionStatus?: string;
  approvalStatus?: string;
  requestedAt?: string;
  startedAt?: string;
  completedAt?: string;
  createdAt?: string;
  parserVersion?: string;
  ontologyVersion?: string;
  profileId?: string;
  profileVersion?: string;
  candidateSetHash?: string;
};

export type ParserV2DocumentVersion = JsonRecord & {
  documentId?: string;
  documentVersionId?: string;
  archiveId?: string;
  sourceHash?: string;
  sha256?: string;
  mediaType?: string;
  fileName?: string;
  originalFileName?: string;
  sizeBytes?: number;
  versionNumber?: number;
  sourceAuthority?: string;
  ingestedAt?: string;
};

export type ParsedFactCandidate = JsonRecord & {
  candidateId?: string;
  parsingRunId?: string;
  documentId?: string;
  documentVersionId?: string;
  canonicalField?: string;
  fieldKey?: string;
  entityId?: string;
  rawValue?: unknown;
  normalizedValue?: unknown;
  value?: unknown;
  unit?: string;
  originType?: string;
  presenceStatus?: string;
  confidence?: number;
  authority?: string;
  validationStatus?: string;
  validationCodes?: string[];
  validationFindings?: JsonRecord[];
  reviewStatus?: string;
  approvalStatus?: string;
  conflictStatus?: string;
  evidence?: SourceLocatorRecord[];
  sourceLocator?: SourceLocatorRecord;
  targetModules?: string[];
  createdAt?: string;
};

export type SourceConflictRecord = JsonRecord & {
  conflictId?: string;
  fieldKey?: string;
  canonicalField?: string;
  entityId?: string;
  candidateIds?: string[];
  documentVersionIds?: string[];
  conflictType?: string;
  conflictCode?: string;
  conflictStatus?: string;
  severity?: string;
  notes?: string;
  detectedAt?: string;
  resolvedAt?: string;
};

export type ReviewDecisionRecord = JsonRecord & {
  reviewDecisionId?: string;
  candidateId?: string;
  decisionSequence?: number;
  action?: string;
  resultingReviewStatus?: string;
  resultingApprovalStatus?: string;
  reviewerId?: string;
  reason?: string;
  conflictIds?: string[];
  decidedAt?: string;
  createdAt?: string;
};

export type CanonicalSelectionRecord = JsonRecord & {
  selectionId?: string;
  candidateId?: string;
  reviewDecisionId?: string;
  approvalStatus?: string;
  validityStatus?: string;
  selectionVersion?: number;
  selectedAt?: string;
  createdAt?: string;
};

export type ApprovedInputSnapshotRecord = JsonRecord & {
  snapshotId?: string;
  selectionIds?: string[];
  selections?: CanonicalSelectionRecord[];
  inputItems?: Array<JsonRecord & {
    fieldKey?: string;
    canonicalField?: string;
    entityId?: string;
    normalizedValue?: unknown;
    unit?: string;
    candidateId?: string;
    selectionId?: string;
    sourceHash?: string;
    sha256?: string;
  }>;
  purpose?: string;
  approvalStatus?: string;
  validityStatus?: string;
  snapshotHash?: string;
  snapshotVersion?: number;
  supersedesSnapshotId?: string;
  approvedAt?: string;
  createdAt?: string;
};

export type ParserV2RunPackage = {
  schemaVersion?: string;
  candidateOnly?: boolean;
  authoritative?: boolean;
  projectProjectionMutated?: boolean;
  projectId?: string;
  parsingRun: ParserV2Run;
  documentVersion: ParserV2DocumentVersion;
  candidates: ParsedFactCandidate[];
  conflicts: SourceConflictRecord[];
  candidateSetSeal?: JsonRecord | null;
  reviewDecisions: ReviewDecisionRecord[];
  canonicalSelections: CanonicalSelectionRecord[];
  approvedInputSnapshots: ApprovedInputSnapshotRecord[];
};

export type GlossaryCandidateRecord = JsonRecord & {
  candidateId?: string;
  surface?: string;
  status?: string;
  projectId?: string;
  runId?: string;
  sourceField?: string;
  suggestedCanonicalField?: string;
  category?: string;
  confidence?: number;
  sourceLocator?: SourceLocatorRecord;
};

export type GlossaryTermRecord = JsonRecord & {
  termId?: string;
  preferredTerm?: string;
  displayNameKo?: string;
  canonicalField?: string;
  category?: string;
  reviewStatus?: string;
  aliases?: string[];
  source?: string;
};

export type DocumentReviewQueueItem = JsonRecord & {
  queueItemId?: string;
  candidateId?: string;
  parsingRunId?: string;
  documentVersionId?: string;
  fieldKey?: string;
  entityId?: string;
  validationOutcome?: "passed" | "warning" | "failed" | "unknown";
  issueCodes?: string[];
  openConflictIds?: string[];
  evidenceLocatorCount?: number;
  severity?: "blocking" | "high" | "medium" | "low";
};

export type SnapshotDiffValue = {
  normalizedValue?: unknown;
  unit?: string;
  candidateId?: string;
  selectionId?: string;
  documentVersionId?: string;
  sourceHash?: string;
  sourceLocator?: SourceLocatorRecord;
  authority?: string;
  effectiveDate?: string;
  asOfDate?: string;
};

export type ApprovedSnapshotDiff = JsonRecord & {
  fromSnapshotId?: string;
  fromSnapshotHash?: string;
  toSnapshotId?: string;
  toSnapshotHash?: string;
  toSnapshotVersion?: number;
  approvedAt?: string;
  comparable?: boolean;
  issueCodes?: string[];
  changeCount?: number;
  changes?: Array<{
    changeType?: "added" | "removed" | "changed";
    fieldKey?: string;
    entityId?: string;
    before?: SnapshotDiffValue | null;
    after?: SnapshotDiffValue | null;
  }>;
};

export interface DocumentReviewData {
  schemaVersion: "realytics-document-review-workspace-v1";
  viewModelVersion: string;
  projectId: string;
  snapshotId: string;
  snapshotHash: string;
  asOfDate?: string | null;
  observedAt: string;
  freshness?: {
    status?: "observed" | "unknown";
    latestSourceAt?: string | null;
    observedAt?: string;
    warningCodes?: string[];
  };
  sourceDocuments: SourceDocumentRecord[];
  runs: ParserV2Run[];
  packages: ParserV2RunPackage[];
  glossaryCandidates: GlossaryCandidateRecord[];
  reviewQueue: DocumentReviewQueueItem[];
  snapshotDiffs: ApprovedSnapshotDiff[];
  issueSummary?: {
    count?: number;
    byCode?: Record<string, number>;
    bySeverity?: Record<string, number>;
  };
  authority?: {
    candidateOnly?: boolean;
    authoritativeProjectionMutated?: boolean;
    approvalAuthority?: string;
    assembly?: string;
  };
  warnings: string[];
}

async function responseError(response: Response, fallback: string) {
  try {
    const body = (await response.json()) as { detail?: unknown; message?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (body.detail && typeof body.detail === "object") {
      const detail = body.detail as { message?: unknown; error?: unknown };
      if (typeof detail.message === "string") return detail.message;
      if (typeof detail.error === "string") return detail.error;
    }
    if (typeof body.message === "string") return body.message;
  } catch {
    // The status-aware fallback is safer than reflecting arbitrary HTML/text.
  }
  return `${fallback} (${response.status})`;
}

async function fetchJson<T>(url: string, fallback: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    signal,
    cache: "no-store",
    headers: authHeaders("web-auditor"),
  });
  if (!response.ok) throw new Error(await responseError(response, fallback));
  return (await response.json()) as T;
}

export async function fetchDocumentReviewData(projectId: string, signal?: AbortSignal): Promise<DocumentReviewData> {
  const projectPath = encodeURIComponent(projectId);
  const result = await fetchJson<DocumentReviewData>(
    `${apiBase}/projects/${projectPath}/workspace/document-review`,
    "문서 검토 결과를 불러오지 못했습니다.",
    signal,
  );
  if (
    result.schemaVersion !== "realytics-document-review-workspace-v1"
    || !result.snapshotId
    || !/^[0-9a-f]{64}$/.test(result.snapshotHash)
    || !result.viewModelVersion
  ) {
    throw new Error("문서 검토 Read Model의 버전 또는 무결성 계약이 올바르지 않습니다.");
  }
  return {
    ...result,
    sourceDocuments: result.sourceDocuments ?? [],
    runs: result.runs ?? [],
    packages: result.packages ?? [],
    glossaryCandidates: result.glossaryCandidates ?? [],
    reviewQueue: result.reviewQueue ?? [],
    snapshotDiffs: result.snapshotDiffs ?? [],
    warnings: result.warnings ?? [],
  };
}

export async function fetchGlossaryTermsForField(canonicalField: string): Promise<GlossaryTermRecord[]> {
  if (!canonicalField.trim()) return [];
  const params = new URLSearchParams({ canonical_field: canonicalField.trim() });
  const body = await fetchJson<{ terms?: GlossaryTermRecord[] }>(
    `${apiBase}/glossary/terms?${params.toString()}`,
    "표준 용어 매핑을 불러오지 못했습니다.",
  );
  return body.terms ?? [];
}

export function sourceDocumentContentUrl(projectId: string, archiveId: string) {
  return `${apiBase}/projects/${encodeURIComponent(projectId)}/source-documents/${encodeURIComponent(archiveId)}/content`;
}

export async function fetchPdfPagePreview(input: {
  projectId: string;
  archiveId: string;
  pageNumber: number;
  scale?: number;
}): Promise<PdfPagePreview> {
  const response = await fetch(
    `${apiBase}/projects/${encodeURIComponent(input.projectId)}/source-documents/${encodeURIComponent(input.archiveId)}/pdf/pages/${encodeURIComponent(String(input.pageNumber))}.png?scale=${encodeURIComponent(String(input.scale ?? 1.5))}`,
    { cache: "no-store", headers: authHeaders("web-evidence-viewer") },
  );
  if (!response.ok) {
    throw new Error(await responseError(response, "PDF 근거 페이지를 불러오지 못했습니다."));
  }
  const pageWidth = Number(response.headers.get("X-REALYTICS-Page-Width"));
  const pageHeight = Number(response.headers.get("X-REALYTICS-Page-Height"));
  const pageCount = Number(response.headers.get("X-REALYTICS-Page-Count"));
  if (![pageWidth, pageHeight, pageCount].every((value) => Number.isFinite(value) && value > 0)) {
    throw new Error("PDF 페이지 geometry 응답이 불완전합니다.");
  }
  return {
    blob: await response.blob(),
    pageNumber: input.pageNumber,
    pageCount,
    pageWidth,
    pageHeight,
    coordinateSpace: response.headers.get("X-REALYTICS-Coordinate-Space") || "",
  };
}

function workbookPreviewError(status: number) {
  if (status === 401 || status === 403) return "이 원본을 읽을 권한이 없거나 로그인 상태가 만료됐습니다.";
  if (status === 404) return "보관 원본 또는 요청한 시트를 찾을 수 없습니다.";
  if (status === 415) return "화면에서 읽을 수 있는 Excel 형식(XLSX·XLSM)이 아닙니다.";
  if (status === 422) return "Excel 원본이나 요청한 셀 범위를 읽을 수 없습니다. 다른 범위나 원본 파일을 확인해 주세요.";
  return "Excel 미리보기 요청을 완료하지 못했습니다. 원본 다시 읽기를 눌러 주세요.";
}

export async function fetchWorkbookOriginalSheets(input: {
  projectId: string;
  archiveId: string;
  signal?: AbortSignal;
}): Promise<WorkbookOriginalSheets> {
  const response = await fetch(
    `${apiBase}/projects/${encodeURIComponent(input.projectId)}/source-documents/${encodeURIComponent(input.archiveId)}/workbook/sheets`,
    { cache: "no-store", headers: authHeaders("web-evidence-viewer"), signal: input.signal },
  );
  if (!response.ok) throw new Error(workbookPreviewError(response.status));
  const result = await response.json() as WorkbookOriginalSheets;
  if (result.schemaVersion !== "realytics-workbook-original-sheets-v1"
    || result.archiveId !== input.archiveId || result.sourceSha256 !== input.archiveId
    || result.archiveIntegrityStatus !== "verified" || result.analysisOnly !== true || result.approvalEligible !== false
    || !Array.isArray(result.sheets) || result.sheets.length > 100 || !Array.isArray(result.warnings)
    || !result.limits || !Number.isInteger(result.limits.maxCells) || result.limits.maxCells < 1 || result.limits.maxCells > 400
    || !Number.isInteger(result.limits.maxRows) || result.limits.maxRows < 1 || result.limits.maxRows > 10_000
    || !Number.isInteger(result.limits.maxColumns) || result.limits.maxColumns < 1 || result.limits.maxColumns > 512
    || result.sheets.some((sheet) => typeof sheet.sheetName !== "string" || !sheet.sheetName
      || !Number.isInteger(sheet.rowCount) || sheet.rowCount < 0 || !Number.isInteger(sheet.columnCount) || sheet.columnCount < 0)) {
    throw new Error("선택한 Excel 원본과 시트 정보의 무결성을 확인할 수 없습니다.");
  }
  return result;
}

export async function fetchWorkbookCellPreview(input: {
  projectId: string;
  archiveId: string;
  sheetName: string;
  cellReference: string;
  contextRows?: number;
  contextColumns?: number;
  signal?: AbortSignal;
}): Promise<WorkbookCellPreview> {
  const params = new URLSearchParams({
    sheet_name: input.sheetName,
    cell_reference: input.cellReference,
    context_rows: String(input.contextRows ?? 3),
    context_columns: String(input.contextColumns ?? 3),
  });
  const response = await fetch(
    `${apiBase}/projects/${encodeURIComponent(input.projectId)}/source-documents/${encodeURIComponent(input.archiveId)}/workbook/cells?${params.toString()}`,
    { cache: "no-store", headers: authHeaders("web-evidence-viewer"), signal: input.signal },
  );
  if (!response.ok) throw new Error(workbookPreviewError(response.status));
  return await response.json() as WorkbookCellPreview;
}

export async function recordParserV2Review(input: {
  projectId: string;
  candidateId: string;
  expectedDecisionSequence: number;
  action: "approve" | "reject" | "request_revision";
  reason: string;
  conflictIds: string[];
}): Promise<ReviewDecisionRecord> {
  const response = await fetch(
    `${apiBase}/projects/${encodeURIComponent(input.projectId)}/parsing-v2/candidates/${encodeURIComponent(input.candidateId)}/reviews`,
    {
      method: "POST",
      headers: { ...authHeaders("web-reviewer"), "Content-Type": "application/json" },
      body: JSON.stringify({
        expectedDecisionSequence: input.expectedDecisionSequence,
        action: input.action,
        reason: input.reason,
        conflictIds: input.conflictIds,
      }),
    },
  );
  if (!response.ok) {
    throw new Error(await responseError(response, "Parser V2 검토 결정을 저장하지 못했습니다."));
  }
  return (await response.json()) as ReviewDecisionRecord;
}

export async function resolveParserV2Conflict(input: {
  projectId: string;
  conflictId: string;
  reviewDecisionId: string;
  expectedDecisionSequence: number;
  notes?: string;
}): Promise<SourceConflictRecord> {
  const response = await fetch(
    `${apiBase}/projects/${encodeURIComponent(input.projectId)}/parsing-v2/conflicts/${encodeURIComponent(input.conflictId)}/resolve`,
    {
      method: "POST",
      headers: { ...authHeaders("web-reviewer"), "Content-Type": "application/json" },
      body: JSON.stringify({
        reviewDecisionId: input.reviewDecisionId,
        expectedDecisionSequence: input.expectedDecisionSequence,
        resolutionStatus: "resolved",
        notes: input.notes?.trim() || undefined,
      }),
    },
  );
  if (!response.ok) {
    throw new Error(await responseError(response, "Parser V2 SourceConflict를 해결하지 못했습니다."));
  }
  return (await response.json()) as SourceConflictRecord;
}

export function safeSourceUrl(value: unknown): string | null {
  const url = String(value ?? "").trim();
  if (!url) return null;
  if (url.startsWith("/")) return `${apiBase}${url}`;
  if (/^https?:\/\//i.test(url)) return url;
  return null;
}

export function errorMessage(value: unknown): string {
  return value instanceof Error ? value.message : "알 수 없는 오류가 발생했습니다.";
}

export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonRecord) : {};
}
