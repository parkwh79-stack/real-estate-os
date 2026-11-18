import { apiBase, authHeaders } from "./auth-client";
import { backendErrorMessageForResponse } from "./backend-error";

export type DocumentPageReading = {
  pageNumber: number;
  status: "completed" | "empty" | "unavailable" | "timeout" | "failed";
  sourceKind: "native" | "ocr";
  text: string;
  textSha256: string | null;
  errorCode?: string;
  stored?: boolean;
};

type ReadingTarget = { projectId: string; archiveId: string; pageNumber: number; signal: AbortSignal };
const invalidResponse = "선택한 페이지의 읽기 결과를 확인하지 못했습니다. 원본을 확인하고 다시 시도해 주세요.";

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function selectedPage(body: unknown, target: ReadingTarget, allowMissing: boolean): DocumentPageReading | null {
  const response = record(body);
  if (!response || response.schemaVersion !== "realytics-local-pdf-reading-v1"
    || response.analysisOnly !== true || response.reportEligible !== false || response.factPromotionAllowed !== false
    || typeof response.fileSha256 !== "string" || !/^[a-f0-9]{64}$/.test(response.fileSha256)
    || response.projectId !== target.projectId || response.archiveId !== target.archiveId
    || response.fileSha256 !== target.archiveId.toLowerCase()
    || !Array.isArray(response.pages)) throw new Error(invalidResponse);
  if (allowMissing && response.status === "not_generated" && response.pages.length === 0) return null;
  const { pageNumber } = target;
  const page = response.pages.length === 1 ? record(response.pages[0]) : null;
  if (!page || page.pageNumber !== pageNumber
    || typeof page.status !== "string" || !["completed", "empty", "unavailable", "timeout", "failed"].includes(page.status)
    || typeof page.sourceKind !== "string" || !["native", "ocr"].includes(page.sourceKind) || typeof page.text !== "string"
    || (page.errorCode !== undefined && page.errorCode !== null && typeof page.errorCode !== "string")
    || (page.status === "completed" && (typeof page.textSha256 !== "string" || !/^[a-f0-9]{64}$/.test(page.textSha256)))) {
    throw new Error(invalidResponse);
  }
  return {
    pageNumber, status: page.status as DocumentPageReading["status"],
    sourceKind: page.sourceKind as DocumentPageReading["sourceKind"], text: page.text,
    textSha256: typeof page.textSha256 === "string" ? page.textSha256 : null,
    errorCode: typeof page.errorCode === "string" ? page.errorCode : undefined,
    stored: Array.isArray(response.unstoredPages) && response.unstoredPages.includes(pageNumber)
      ? false : Array.isArray(response.storedPages) && response.storedPages.includes(pageNumber) ? true : undefined,
  };
}

async function requestReading(target: ReadingTarget, generate: boolean): Promise<DocumentPageReading | null> {
  const base = `${apiBase}/projects/${encodeURIComponent(target.projectId)}/source-documents/${encodeURIComponent(target.archiveId)}/reading`;
  const response = await fetch(generate ? base : `${base}/pages/${target.pageNumber}`, {
    method: generate ? "POST" : "GET", cache: "no-store", signal: target.signal,
    headers: { ...authHeaders("web-evidence-viewer"), ...(generate ? { "Content-Type": "application/json" } : {}) },
    ...(generate ? { body: JSON.stringify({ pageNumbers: [target.pageNumber] }) } : {}),
  });
  if (!response.ok) throw new Error(await backendErrorMessageForResponse(response, { action: "페이지 글자 읽기" }));
  let body: unknown;
  try { body = await response.json(); } catch { throw new Error(invalidResponse); }
  return selectedPage(body, target, !generate);
}

export function fetchDocumentPageReading(target: ReadingTarget): Promise<DocumentPageReading | null> {
  return requestReading(target, false);
}

export function generateDocumentPageReading(target: ReadingTarget): Promise<DocumentPageReading | null> {
  return requestReading(target, true);
}
