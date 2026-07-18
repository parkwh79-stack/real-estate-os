import { apiBase, authHeaders } from "./auth-client";
import type {
  LegalContentClass,
  LegalFreshnessStatus,
  LegalResourceDetail,
  LegalResourceSummary,
  LegalResourceType,
  LegalSearchParams,
  LegalSearchResponse,
  LegalSourceAuthority,
  LegalTransport,
  LegalVerificationStatus,
} from "./legal-intelligence-types";

type JsonRecord = Record<string, unknown>;

export async function searchLegalResources(
  input: LegalSearchParams,
  signal?: AbortSignal,
): Promise<LegalSearchResponse> {
  const params = new URLSearchParams({ q: input.query.trim() });
  if (input.resourceType && input.resourceType !== "all") {
    params.set("resource_type", input.resourceType);
  }
  if (input.asOf) params.set("as_of", input.asOf);
  if (input.publicQueryAcknowledged) {
    params.set("public_query_acknowledged", "true");
  }

  const response = await fetch(`${apiBase}/legal/search?${params.toString()}`, {
    cache: "no-store",
    headers: authHeaders("web"),
    signal,
  });
  const body = await readJson(response);
  if (!response.ok) {
    throw new Error(apiErrorMessage(body, `법률정보 검색에 실패했습니다. (${response.status})`));
  }
  return normalizeSearchResponse(body, input);
}

export async function getLegalResource(
  resourceId: string,
  signal?: AbortSignal,
): Promise<LegalResourceDetail> {
  const response = await fetch(
    `${apiBase}/legal/resources/${encodeURIComponent(resourceId)}`,
    {
      cache: "no-store",
      headers: authHeaders("web"),
      signal,
    },
  );
  const body = await readJson(response);
  if (!response.ok) {
    throw new Error(apiErrorMessage(body, `법률정보 상세를 불러오지 못했습니다. (${response.status})`));
  }
  const record = asRecord(body);
  const resource = asRecord(record.resource ?? record.item ?? record.data ?? body);
  return normalizeResource(resource, false);
}

async function readJson(response: Response): Promise<unknown> {
  return response.json().catch(() => ({}));
}

function apiErrorMessage(body: unknown, fallback: string): string {
  const record = asRecord(body);
  const detail = record.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (isRecord(detail)) {
    const message = firstString(detail, "message", "error", "reason");
    if (message) return message;
  }
  return firstString(record, "message", "error") ?? fallback;
}

function normalizeSearchResponse(
  body: unknown,
  input: LegalSearchParams,
): LegalSearchResponse {
  const record = asRecord(body);
  const authoritative = arrayFrom(
    record.authoritativeHits ?? record.authoritative_hits,
  );
  const candidates = arrayFrom(record.candidateHits ?? record.candidate_hits);
  const explicitResults = arrayFrom(
    record.results ?? record.items ?? record.hits ?? record.data,
  );
  const rawResults = explicitResults.length
    ? explicitResults
    : [...authoritative, ...candidates];
  const queryRecord = asRecord(record.query);
  const providerTrace = arrayFrom(record.providerTrace ?? record.provider_trace);
  const providerFreshness = freshnessFromProviderTrace(providerTrace);

  const results = rawResults
    .map((item) => normalizeResource(asRecord(item), providerFreshness === "stale"))
    .filter((item, index, all) =>
      all.findIndex(
        (candidate) =>
          candidate.resourceId === item.resourceId &&
          candidate.resourceVersionId === item.resourceVersionId,
      ) === index,
    );

  return {
    query:
      firstString(record, "queryText", "query_text") ??
      firstString(queryRecord, "query", "text") ??
      input.query,
    asOfDate:
      firstString(record, "asOfDate", "as_of_date", "asOf", "as_of") ??
      firstString(queryRecord, "asOfDate", "as_of_date", "asOf", "as_of") ??
      input.asOf,
    resourceType:
      normalizeResourceType(
        firstString(record, "resourceType", "resource_type") ??
          firstArrayString(queryRecord, "resourceKinds", "resource_kinds") ??
          (input.resourceType === "all" ? undefined : input.resourceType),
      ),
    results,
    authoritativeCount: numberOr(
      record.authoritativeCount ?? record.authoritative_count,
      authoritative.length || results.filter(isVerifiedResource).length,
    ),
    candidateCount: numberOr(
      record.candidateCount ?? record.candidate_count,
      candidates.length || results.filter((item) => !isVerifiedResource(item)).length,
    ),
    satisfiedByLocalSnapshot: booleanOr(
      record.satisfiedByLocalSnapshot ?? record.satisfied_by_local_snapshot,
      false,
    ),
    externalEgressAcknowledged: booleanOr(
      record.externalEgressAcknowledged ??
        record.external_egress_acknowledged,
      false,
    ),
    externalEgressPolicy: firstString(
      record,
      "externalEgressPolicy",
      "external_egress_policy",
    ),
  };
}

function normalizeResource(
  record: JsonRecord,
  providerIsStale: boolean,
): LegalResourceDetail {
  const attestation = asRecord(record.attestation);
  const content = asRecord(record.content);
  const locatorValue = record.locator ?? attestation.locator;
  const locatorRecord = asRecord(locatorValue);
  const sourceUrl =
    firstString(
      record,
      "sourceUrl",
      "source_url",
      "officialUrl",
      "official_url",
    ) ??
    firstString(locatorRecord, "sourceUrl", "source_url", "url") ??
    urlString(locatorValue);
  const officialText =
    firstString(record, "officialText", "official_text") ??
    firstString(
      content,
      "officialText",
      "official_text",
      "본문",
      "조문내용",
      "판례내용",
      "content",
      "text",
    );
  const verificationStatus = normalizeVerification(
    firstString(record, "verificationStatus", "verification_status") ??
      firstString(attestation, "verificationStatus", "verification_status", "trustLevel", "trust_level"),
  );
  const freshnessStatus = normalizeFreshness(
    firstString(record, "freshnessStatus", "freshness_status", "freshness"),
    providerIsStale,
  );
  const stale =
    booleanOr(record.stale ?? record.isStale ?? record.is_stale, false) ||
    verificationStatus === "stale" ||
    freshnessStatus === "stale";
  const resourceId =
    firstString(record, "resourceId", "resource_id", "id") ??
    firstString(attestation, "officialResourceId", "official_resource_id") ??
    "";
  const officialIdentifiers = stringRecord(
    record.officialIdentifiers ?? record.official_identifiers,
  );

  return {
    resourceId,
    resourceVersionId:
      firstString(record, "resourceVersionId", "resource_version_id") ??
      firstString(attestation, "officialVersionId", "official_version_id"),
    title:
      firstString(record, "title", "resourceName", "resource_name", "name") ??
      firstString(content, "법령명", "판례명", "title", "name") ??
      "제목 미확인",
    citation:
      firstString(record, "citation") ??
      firstString(attestation, "article") ??
      firstString(content, "법령명한글", "사건명"),
    excerpt:
      firstString(record, "excerpt", "summary") ??
      firstString(content, "요약", "판시사항"),
    resourceType: normalizeResourceType(
      firstString(record, "resourceType", "resource_type", "resourceKind", "resource_kind") ??
        firstString(attestation, "resourceKind", "resource_kind"),
    ),
    contentClass: normalizeContentClass(
      firstString(record, "contentClass", "content_class"),
      officialText,
    ),
    sourceAuthority: normalizeAuthority(
      firstString(record, "sourceAuthority", "source_authority") ??
        firstString(attestation, "sourceAuthority", "source_authority"),
    ),
    transport: normalizeTransport(
      firstString(record, "transport", "sourceTransport", "source_transport") ??
        firstString(attestation, "sourceTransport", "source_transport"),
    ),
    verificationStatus,
    freshnessStatus: stale ? "stale" : freshnessStatus,
    stale,
    asOfDate:
      firstString(record, "asOfDate", "as_of_date", "asOf", "as_of") ??
      firstString(attestation, "asOf", "as_of"),
    effectiveFrom:
      firstString(record, "effectiveFrom", "effective_from", "validFrom", "valid_from") ??
      firstString(attestation, "effectiveFrom", "effective_from"),
    effectiveTo:
      firstString(record, "effectiveTo", "effective_to", "validTo", "valid_to") ??
      firstString(attestation, "effectiveTo", "effective_to"),
    sourceUrl,
    locator:
      typeof locatorValue === "string"
        ? locatorValue
        : firstString(locatorRecord, "label", "citation", "path"),
    officialText,
    aiSummary:
      firstString(record, "aiSummary", "ai_summary") ??
      firstString(content, "aiSummary", "ai_summary"),
    humanOpinion:
      firstString(record, "humanOpinion", "human_opinion", "counselOpinion", "counsel_opinion") ??
      firstString(content, "humanOpinion", "human_opinion", "counselOpinion", "counsel_opinion"),
    officialIdentifiers:
      Object.keys(officialIdentifiers).length ? officialIdentifiers : undefined,
    article:
      firstString(record, "article", "provision") ??
      firstString(attestation, "article"),
    observedAt: firstString(record, "observedAt", "observed_at"),
    fetchedAt:
      firstString(record, "fetchedAt", "fetched_at") ??
      firstString(attestation, "fetchedAt", "fetched_at"),
    verifiedAt:
      firstString(record, "verifiedAt", "verified_at") ??
      firstString(attestation, "verifiedAt", "verified_at"),
    sourcePayloadSha256: firstString(
      record,
      "sourcePayloadSha256",
      "source_payload_sha256",
    ),
    normalizedContentSha256:
      firstString(record, "normalizedContentSha256", "normalized_content_sha256") ??
      firstString(attestation, "contentHash", "content_hash"),
  };
}

function freshnessFromProviderTrace(items: unknown[]): LegalFreshnessStatus {
  let hasFresh = false;
  for (const item of items) {
    const freshness = normalizeFreshness(
      firstString(asRecord(item), "freshness", "freshnessStatus", "freshness_status"),
      false,
    );
    if (freshness === "stale") return "stale";
    if (freshness === "fresh" || freshness === "current") hasFresh = true;
  }
  return hasFresh ? "fresh" : "unknown";
}

function isVerifiedResource(item: LegalResourceSummary): boolean {
  return [
    "human_reviewed",
    "verified_snapshot",
    "source_attested",
    "citation_verified",
    "verified",
  ].includes(item.verificationStatus);
}

function normalizeResourceType(value: unknown): LegalResourceType {
  const normalized = enumText(value);
  const values: LegalResourceType[] = [
    "statute",
    "provision",
    "precedent",
    "interpretation",
    "administrative_rule",
    "local_ordinance",
    "constitutional_decision",
    "committee_decision",
    "legal_term",
  ];
  return values.includes(normalized as LegalResourceType)
    ? (normalized as LegalResourceType)
    : "statute";
}

function normalizeContentClass(
  value: unknown,
  officialText?: string,
): LegalContentClass {
  const normalized = enumText(value);
  if (["official_text", "ai_summary", "human_opinion"].includes(normalized)) {
    return normalized as LegalContentClass;
  }
  return officialText ? "official_text" : "unknown";
}

function normalizeAuthority(value: unknown): LegalSourceAuthority {
  const normalized = enumText(value);
  const values: LegalSourceAuthority[] = [
    "law_go_kr",
    "supreme_court",
    "constitutional_court",
    "national_tax_service",
    "central_government",
    "local_government",
    "public_agency",
    "tenant_document",
    "human_counsel",
    "caller_supplied",
    "unknown",
  ];
  return values.includes(normalized as LegalSourceAuthority)
    ? (normalized as LegalSourceAuthority)
    : "unknown";
}

function normalizeTransport(value: unknown): LegalTransport {
  const normalized = enumText(value);
  const values: LegalTransport[] = [
    "local_verified_snapshot",
    "law_go_kr_direct_api",
    "self_hosted_mcp",
    "remote_mcp",
    "tenant_object_storage",
    "caller_context",
    "local_database",
    "direct_api",
    "manual",
    "unknown",
  ];
  return values.includes(normalized as LegalTransport)
    ? (normalized as LegalTransport)
    : "unknown";
}

function normalizeVerification(value: unknown): LegalVerificationStatus {
  const normalized = enumText(value);
  const values: LegalVerificationStatus[] = [
    "human_reviewed",
    "verified_snapshot",
    "source_attested",
    "citation_verified",
    "verified",
    "candidate",
    "untrusted_candidate",
    "failed",
    "stale",
    "not_verified",
    "unknown",
  ];
  return values.includes(normalized as LegalVerificationStatus)
    ? (normalized as LegalVerificationStatus)
    : "unknown";
}

function normalizeFreshness(
  value: unknown,
  staleFallback: boolean,
): LegalFreshnessStatus {
  const normalized = enumText(value);
  if (["fresh", "current", "stale"].includes(normalized)) {
    return normalized as LegalFreshnessStatus;
  }
  return staleFallback ? "stale" : "unknown";
}

function enumText(value: unknown): string {
  return typeof value === "string"
    ? value.trim().toLowerCase().replaceAll("-", "_")
    : "";
}

function asRecord(value: unknown): JsonRecord {
  return isRecord(value) ? value : {};
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function arrayFrom(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function firstString(record: JsonRecord, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function firstArrayString(record: JsonRecord, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      const found = value.find((item) => typeof item === "string" && item.trim());
      if (typeof found === "string") return found;
    }
  }
  return undefined;
}

function booleanOr(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function numberOr(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function urlString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.toString() : undefined;
  } catch {
    return undefined;
  }
}

function stringRecord(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value)
      .filter((entry): entry is [string, string | number] =>
        typeof entry[1] === "string" || typeof entry[1] === "number",
      )
      .map(([key, item]) => [key, String(item)]),
  );
}
