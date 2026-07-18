export type LegalResourceType =
  | "statute"
  | "provision"
  | "precedent"
  | "interpretation"
  | "administrative_rule"
  | "local_ordinance"
  | "constitutional_decision"
  | "committee_decision"
  | "legal_term";

export type LegalContentClass =
  | "official_text"
  | "ai_summary"
  | "human_opinion"
  | "unknown";

export type LegalSourceAuthority =
  | "law_go_kr"
  | "supreme_court"
  | "constitutional_court"
  | "central_government"
  | "local_government"
  | "public_agency"
  | "national_tax_service"
  | "tenant_document"
  | "human_counsel"
  | "caller_supplied"
  | "unknown";

export type LegalTransport =
  | "local_verified_snapshot"
  | "law_go_kr_direct_api"
  | "self_hosted_mcp"
  | "remote_mcp"
  | "tenant_object_storage"
  | "caller_context"
  | "local_database"
  | "direct_api"
  | "manual"
  | "unknown";

export type LegalVerificationStatus =
  | "human_reviewed"
  | "verified_snapshot"
  | "source_attested"
  | "citation_verified"
  | "verified"
  | "candidate"
  | "untrusted_candidate"
  | "failed"
  | "stale"
  | "not_verified"
  | "unknown";

export type LegalFreshnessStatus = "fresh" | "current" | "stale" | "unknown";

export interface LegalResourceSummary {
  resourceId: string;
  resourceVersionId?: string;
  title: string;
  citation?: string;
  excerpt?: string;
  resourceType: LegalResourceType;
  contentClass: LegalContentClass;
  sourceAuthority: LegalSourceAuthority;
  transport: LegalTransport;
  verificationStatus: LegalVerificationStatus;
  freshnessStatus: LegalFreshnessStatus;
  stale: boolean;
  asOfDate?: string;
  effectiveFrom?: string;
  effectiveTo?: string;
  sourceUrl?: string;
  locator?: string;
  officialText?: string;
  aiSummary?: string;
  humanOpinion?: string;
}

export interface LegalSearchResponse {
  query: string;
  asOfDate?: string;
  resourceType?: LegalResourceType;
  results: LegalResourceSummary[];
  authoritativeCount: number;
  candidateCount: number;
  satisfiedByLocalSnapshot: boolean;
  externalEgressAcknowledged?: boolean;
  externalEgressPolicy?: string;
}

export interface LegalResourceDetail extends LegalResourceSummary {
  officialIdentifiers?: Record<string, string>;
  article?: string;
  observedAt?: string;
  fetchedAt?: string;
  verifiedAt?: string;
  sourcePayloadSha256?: string;
  normalizedContentSha256?: string;
}

export interface LegalSearchParams {
  query: string;
  resourceType?: LegalResourceType | "all";
  asOf?: string;
  publicQueryAcknowledged?: boolean;
}
