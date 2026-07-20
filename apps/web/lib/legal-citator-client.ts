import { apiBase, authHeaders } from "./auth-client";
import type {
  LegalCitatorCitationQueueItem,
  LegalCitatorEvidenceBinding,
  LegalCitatorHumanReviewStatus,
  LegalCitatorRecordKind,
  LegalCitatorRelationQueueItem,
  LegalCitatorReviewInput,
  LegalCitatorReviewOutcome,
  LegalCitatorReviewQueue,
  LegalCitatorReviewRequest,
  LegalCitatorReviewStatus,
} from "./legal-citator-types";

type JsonRecord = Record<string, unknown>;

const REVIEW_STATUSES = [
  "approved",
  "rejected",
  "needs_revision",
] as const satisfies readonly LegalCitatorReviewStatus[];

const HUMAN_REVIEW_STATUSES = [
  "not_required",
  "pending",
  "approved",
  "rejected",
] as const satisfies readonly LegalCitatorHumanReviewStatus[];

export async function getLegalCitatorReviewQueue(
  projectId: string,
  options: {
    recordKind?: LegalCitatorRecordKind;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<LegalCitatorReviewQueue> {
  const limit = Math.max(1, Math.min(options.limit ?? 100, 500));
  const params = new URLSearchParams({ limit: String(limit) });
  if (options.recordKind) params.set("recordKind", options.recordKind);
  const response = await fetch(
    `${citatorBase(projectId)}/review-queue?${params.toString()}`,
    {
      cache: "no-store",
      headers: authHeaders("web"),
      signal: options.signal,
    },
  );
  return normalizeLegalCitatorReviewQueue(
    await readOrThrow(response, "Citator 검토 대기열을 불러오지 못했습니다."),
  );
}

export async function reviewLegalCitation(
  projectId: string,
  citationId: string,
  input: LegalCitatorReviewInput,
): Promise<LegalCitatorReviewOutcome> {
  return postReview(
    projectId,
    `/citations/${encodeURIComponent(citationId)}/reviews`,
    input,
    "citation",
  );
}

export async function reviewLegalRelation(
  projectId: string,
  assertionId: string,
  input: LegalCitatorReviewInput,
): Promise<LegalCitatorReviewOutcome> {
  return postReview(
    projectId,
    `/relations/${encodeURIComponent(assertionId)}/reviews`,
    input,
    "relation",
  );
}

export async function buildLegalCitatorReviewRequest(
  input: LegalCitatorReviewInput,
  recordKind?: LegalCitatorRecordKind,
): Promise<LegalCitatorReviewRequest> {
  const reason = input.reason.trim();
  if (!reason) throw new Error("검토 사유를 입력해 주세요.");

  const evidenceExcerpt = input.evidenceExcerpt?.trim() || undefined;
  if (input.reviewStatus === "approved" && !evidenceExcerpt) {
    throw new Error("승인에는 확인한 원문의 exact excerpt가 필요합니다.");
  }

  const objectEvidenceItemId =
    input.objectEvidenceItemId?.trim() || undefined;
  if (recordKind === "citation" && objectEvidenceItemId) {
    throw new Error(
      "objectEvidenceItemId is only valid for a relation review.",
    );
  }
  if (
    recordKind === "relation" &&
    input.reviewStatus === "approved" &&
    !objectEvidenceItemId
  ) {
    throw new Error(
      "An approved relation review requires an object evidence item ID.",
    );
  }

  const request: LegalCitatorReviewRequest = {
    reviewStatus: input.reviewStatus,
    reason,
  };
  if (evidenceExcerpt) {
    request.evidenceExcerpt = evidenceExcerpt;
    request.evidenceExcerptSha256 = await sha256BrowserText(evidenceExcerpt);
  }
  if (objectEvidenceItemId) {
    request.objectEvidenceItemId = objectEvidenceItemId;
  }
  const predecessor = input.supersedesReviewEventId?.trim();
  if (predecessor) request.supersedesReviewEventId = predecessor;
  const delegation = input.legalApprovalDelegationId?.trim();
  if (delegation) request.legalApprovalDelegationId = delegation;
  return request;
}

export async function sha256BrowserText(value: string): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error("브라우저가 SHA-256 Web Crypto를 지원하지 않습니다.");
  }
  const bytes = new TextEncoder().encode(value);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

export function normalizeLegalCitatorReviewQueue(
  body: unknown,
): LegalCitatorReviewQueue {
  const source = strictRecord(body, "Citator review queue");
  const schemaVersion = requiredText(source, "schemaVersion");
  if (schemaVersion !== "realytics-legal-citator-review-queue-v2") {
    throw new Error("지원하지 않는 Citator review queue 계약입니다.");
  }
  if (
    requiredText(source, "authorityModel") !== "tenant_project_review_overlay"
  ) {
    throw new Error("Citator project review overlay 계약이 아닙니다.");
  }
  if (source.corpusRecordsImmutable !== true) {
    throw new Error("Citator corpus immutability attestation이 없습니다.");
  }
  const citations = strictArray(source.citations, "citations").map(
    normalizeCitation,
  );
  const relations = strictArray(source.relations, "relations").map(
    normalizeRelation,
  );
  const totalCount = requiredInteger(source, "totalCount", 0);
  if (totalCount !== citations.length + relations.length) {
    throw new Error("Citator review queue totalCount가 항목 수와 다릅니다.");
  }
  return {
    schemaVersion,
    authorityModel: "tenant_project_review_overlay",
    corpusRecordsImmutable: true,
    citations,
    relations,
    totalCount,
  };
}

export function normalizeLegalCitatorReviewOutcome(
  body: unknown,
  expectedKind: LegalCitatorRecordKind,
): LegalCitatorReviewOutcome {
  const source = strictRecord(body, "Citator review outcome");
  if (
    requiredText(source, "schemaVersion") !==
      "realytics-legal-citator-review-v2" ||
    requiredText(source, "authorityModel") !==
      "tenant_project_review_overlay" ||
    source.corpusRecordMutated !== false ||
    source.appendOnly !== true
  ) {
    throw new Error(
      "Citator append-only review 응답 계약이 올바르지 않습니다.",
    );
  }
  const recordKind = enumValue(source, "recordKind", [
    "citation",
    "relation",
  ] as const);
  if (recordKind !== expectedKind) {
    throw new Error("Citator review 응답의 recordKind가 요청과 다릅니다.");
  }
  const reviewStatus = enumValue(source, "reviewStatus", REVIEW_STATUSES);
  const finalStatus = requiredText(source, "finalStatus");
  const objectEvidenceBinding =
    source.objectEvidenceBinding === null ||
    source.objectEvidenceBinding === undefined
      ? undefined
      : normalizeEvidenceBinding(source.objectEvidenceBinding);
  if (recordKind === "citation" && objectEvidenceBinding) {
    throw new Error(
      "A citation review response cannot contain an object evidence binding.",
    );
  }
  if (
    recordKind === "relation" &&
    (reviewStatus === "approved" || finalStatus === "verified") &&
    !objectEvidenceBinding
  ) {
    throw new Error(
      "A verified relation review response requires an object evidence binding.",
    );
  }
  return {
    schemaVersion: "realytics-legal-citator-review-v2",
    authorityModel: "tenant_project_review_overlay",
    corpusRecordMutated: false,
    recordKind,
    sourceRecordId: requiredText(source, "sourceRecordId"),
    reviewVersionId: requiredText(source, "reviewVersionId"),
    reviewVersion: requiredInteger(source, "reviewVersion", 1),
    reviewEventId: requiredText(source, "reviewEventId"),
    reviewStatus,
    finalStatus,
    reviewerId: requiredText(source, "reviewerId"),
    reviewerRole: requiredText(source, "reviewerRole"),
    reviewedAt: requiredText(source, "reviewedAt"),
    evidenceExcerptSha256: optionalSha256(source, "evidenceExcerptSha256"),
    legalApprovalDelegationId: optionalText(
      source,
      "legalApprovalDelegationId",
    ),
    evidenceBinding: normalizeEvidenceBinding(source.evidenceBinding),
    objectEvidenceBinding,
    appendOnly: true,
  };
}

async function postReview(
  projectId: string,
  path: string,
  input: LegalCitatorReviewInput,
  expectedKind: LegalCitatorRecordKind,
): Promise<LegalCitatorReviewOutcome> {
  const payload = await buildLegalCitatorReviewRequest(input, expectedKind);
  const response = await fetch(`${citatorBase(projectId)}${path}`, {
    method: "POST",
    headers: {
      ...authHeaders("web"),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
  return normalizeLegalCitatorReviewOutcome(
    await readOrThrow(response, "Citator 검토 결과를 저장하지 못했습니다."),
    expectedKind,
  );
}

function normalizeCitation(value: unknown): LegalCitatorCitationQueueItem {
  const source = strictRecord(value, "citation queue item");
  if (requiredText(source, "recordKind") !== "citation") {
    throw new Error("citation queue item의 recordKind가 올바르지 않습니다.");
  }
  return {
    ...normalizeQueueBase(source),
    recordKind: "citation",
    identifier: requiredText(source, "identifier"),
  };
}

function normalizeRelation(value: unknown): LegalCitatorRelationQueueItem {
  const source = strictRecord(value, "relation queue item");
  if (requiredText(source, "recordKind") !== "relation") {
    throw new Error("relation queue item의 recordKind가 올바르지 않습니다.");
  }
  return {
    ...normalizeQueueBase(source),
    recordKind: "relation",
    subjectResourceId: requiredText(source, "subjectResourceId"),
    objectResourceId: requiredText(source, "objectResourceId"),
    relationType: requiredText(source, "relationType"),
  };
}

function normalizeQueueBase(source: JsonRecord) {
  const latestReviewVersionId = optionalText(source, "latestReviewVersionId");
  const latestReviewEventId = optionalText(source, "latestReviewEventId");
  if (latestReviewVersionId && !latestReviewEventId) {
    throw new Error(
      "최신 Citator project review version에 append-only event가 없습니다.",
    );
  }
  const reviewBindingCurrent = nullableBoolean(source, "reviewBindingCurrent");
  if (latestReviewVersionId && reviewBindingCurrent === null) {
    throw new Error(
      "최신 Citator project review의 evidence binding 상태가 없습니다.",
    );
  }
  if (!latestReviewVersionId && reviewBindingCurrent !== null) {
    throw new Error(
      "Citator review version 없이 evidence binding 상태가 설정되었습니다.",
    );
  }
  return {
    recordId: requiredText(source, "recordId"),
    status: requiredText(source, "status"),
    corpusStatus: requiredText(source, "corpusStatus"),
    assertionMethod: requiredText(source, "assertionMethod"),
    confidence: optionalNumber(source, "confidence"),
    assertedAt: requiredText(source, "assertedAt"),
    humanReviewStatus: enumValue(
      source,
      "humanReviewStatus",
      HUMAN_REVIEW_STATUSES,
    ),
    reviewedBy: optionalText(source, "reviewedBy"),
    reviewedAt: optionalText(source, "reviewedAt"),
    latestReviewEventId,
    latestReviewVersionId,
    reviewVersion: optionalInteger(source, "reviewVersion", 1),
    reviewStatus: optionalEnumValue(source, "reviewStatus", REVIEW_STATUSES),
    currentEvidenceBinding: normalizeEvidenceBinding(
      source.currentEvidenceBinding,
    ),
    reviewBindingCurrent,
  };
}

function normalizeEvidenceBinding(value: unknown): LegalCitatorEvidenceBinding {
  const source = strictRecord(value, "Citator evidence binding");
  if (source.current !== true) {
    throw new Error("Citator evidence binding이 current 상태가 아닙니다.");
  }
  const snapshotAsOfDate = requiredText(source, "snapshotAsOfDate");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(snapshotAsOfDate)) {
    throw new Error("snapshotAsOfDate가 YYYY-MM-DD 형식이 아닙니다.");
  }
  return {
    snapshotId: requiredText(source, "snapshotId"),
    snapshotVersion: requiredInteger(source, "snapshotVersion", 1),
    snapshotSha256: requiredSha256(source, "snapshotSha256"),
    snapshotAsOfDate,
    evidenceItemId: requiredText(source, "evidenceItemId"),
    resourceVersionId: requiredText(source, "resourceVersionId"),
    provisionId: requiredText(source, "provisionId"),
    exactLocator: requiredText(source, "exactLocator"),
    sourceFetchId: requiredText(source, "sourceFetchId"),
    providerId: requiredText(source, "providerId"),
    excerptSha256: requiredSha256(source, "excerptSha256"),
    provisionSha256: requiredSha256(source, "provisionSha256"),
    verifiedAt: requiredText(source, "verifiedAt"),
    sourceAuthority: requiredText(source, "sourceAuthority"),
    authorityTier: requiredText(source, "authorityTier"),
    freshnessPolicyVersion: requiredText(source, "freshnessPolicyVersion"),
    current: true,
  };
}

function citatorBase(projectId: string): string {
  return `${apiBase}/projects/${encodeURIComponent(projectId)}/legal/citator`;
}

async function readOrThrow(
  response: Response,
  fallback: string,
): Promise<unknown> {
  const body = await response.json().catch(() => ({}));
  if (response.ok) return body;
  const source =
    body !== null && typeof body === "object" ? (body as JsonRecord) : {};
  const detail =
    source.detail !== null && typeof source.detail === "object"
      ? (source.detail as JsonRecord)
      : {};
  const message =
    optionalText(detail, "message") ??
    optionalText(detail, "error") ??
    optionalText(source, "message") ??
    optionalText(source, "error") ??
    `${fallback} (${response.status})`;
  throw new Error(message);
}

function strictRecord(value: unknown, label: string): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label}가 JSON object가 아닙니다.`);
  }
  return value as JsonRecord;
}

function strictArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) throw new Error(`${label}가 배열이 아닙니다.`);
  return value;
}

function requiredText(source: JsonRecord, key: string): string {
  const value = optionalText(source, key);
  if (!value) throw new Error(`${key}가 없거나 빈 문자열입니다.`);
  return value;
}

function optionalText(source: JsonRecord, key: string): string | undefined {
  const value = source[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function requiredInteger(
  source: JsonRecord,
  key: string,
  minimum: number,
): number {
  const value = source[key];
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum
  ) {
    throw new Error(`${key}가 ${minimum} 이상의 정수가 아닙니다.`);
  }
  return value;
}

function optionalInteger(
  source: JsonRecord,
  key: string,
  minimum: number,
): number | undefined {
  if (source[key] === null || source[key] === undefined) return undefined;
  return requiredInteger(source, key, minimum);
}

function optionalNumber(source: JsonRecord, key: string): number | undefined {
  const value = source[key];
  if (value === null || value === undefined) return undefined;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${key}가 유한한 숫자가 아닙니다.`);
  }
  return value;
}

function enumValue<const T extends readonly string[]>(
  source: JsonRecord,
  key: string,
  allowed: T,
): T[number] {
  const value = requiredText(source, key);
  if (!allowed.includes(value)) {
    throw new Error(`${key} 값이 허용된 enum이 아닙니다.`);
  }
  return value as T[number];
}

function optionalEnumValue<const T extends readonly string[]>(
  source: JsonRecord,
  key: string,
  allowed: T,
): T[number] | undefined {
  const value = optionalText(source, key);
  if (value === undefined) return undefined;
  if (!allowed.includes(value)) {
    throw new Error(`${key} 값이 허용된 enum이 아닙니다.`);
  }
  return value as T[number];
}

function optionalSha256(source: JsonRecord, key: string): string | undefined {
  const value = optionalText(source, key);
  if (value === undefined) return undefined;
  const normalized = value.replace(/^sha256:/i, "").toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(normalized)) {
    throw new Error(`${key}가 SHA-256 digest가 아닙니다.`);
  }
  return normalized;
}

function requiredSha256(source: JsonRecord, key: string): string {
  const value = optionalSha256(source, key);
  if (!value) throw new Error(`${key}가 없거나 SHA-256 digest가 아닙니다.`);
  return value;
}

function nullableBoolean(source: JsonRecord, key: string): boolean | null {
  const value = source[key];
  if (value === null) return null;
  if (typeof value !== "boolean") {
    throw new Error(`${key}가 boolean 또는 null이 아닙니다.`);
  }
  return value;
}
