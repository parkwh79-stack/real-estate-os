import { apiBase, authHeaders } from "./auth-client";
import type {
  CounselOpinionInput,
  CounselOpinionMetadata,
  CounselOpinionStatus,
  HumanReviewStatus,
  LegalApprovalDelegation,
  LegalApprovalDelegationInput,
  LegalAnalysisInput,
  LegalAnalysisKind,
  LegalAnalysisMetadata,
  LegalAnalysisReviewInput,
  LegalAnnotationInput,
  LegalAnnotationStatus,
  LegalAnnotationType,
  LegalGovernanceAnnotation,
  LegalGovernanceMatter,
  LegalGovernanceWaiver,
  LegalGovernanceWorkspace,
  LegalGovernanceWriteResponse,
  LegalMatterInput,
  LegalMatterStatus,
  LegalMatterType,
  LegalRequiredClaim,
  LegalRequiredClaimReviewScope,
  LegalRequiredClaimSet,
  LegalRequiredClaimSetInput,
  LegalRequiredClaimSetStatus,
  LegalSeverity,
  LegalWaiverCondition,
  LegalWaiverCureStatus,
  LegalWaiverInput,
  LegalWaiverStatus,
  TenantLegalTermAlias,
  TenantLegalTermAliasInput,
  TenantLegalTermAliasReviewInput,
  TenantLegalTermAliasStatus,
} from "./legal-governance-types";

type JsonRecord = Record<string, unknown>;

export async function getLegalGovernanceWorkspace(
  projectId: string,
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<LegalGovernanceWorkspace> {
  const limit = Math.max(1, Math.min(options.limit ?? 200, 500));
  const response = await fetch(
    `${governanceBase(projectId)}/workspace?limit=${limit}`,
    {
      cache: "no-store",
      headers: authHeaders("web"),
      signal: options.signal,
    },
  );
  const payload = record(
    await readOrThrow(response, "법률 기록을 불러오지 못했습니다."),
  );
  const workspace = normalizeLegalGovernanceWorkspace(payload);
  if (
    workspace.projectId !== projectId
    || (payload.source !== undefined && payload.source !== "normalized_postgres")
    || !Array.isArray(payload.matters)
  ) {
    throw new Error("서버에서 이 프로젝트의 법률 기록을 확인하지 못했습니다.");
  }
  return workspace;
}

export async function grantLegalApprovalDelegation(
  projectId: string,
  input: LegalApprovalDelegationInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    "/approval-delegations",
    input,
    "legalApprovalDelegationId",
  );
}

export async function revokeLegalApprovalDelegation(
  projectId: string,
  delegationId: string,
  reason: string,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    `/approval-delegations/${encodeURIComponent(delegationId)}/revoke`,
    { reason },
    "legalApprovalDelegationId",
  );
}

export async function appendLegalMatter(
  projectId: string,
  input: LegalMatterInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(projectId, "/matters", input, "projectLegalMatterId");
}

export async function proposeLegalRequiredClaimSet(
  projectId: string,
  input: LegalRequiredClaimSetInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    "/required-claim-sets",
    input,
    "legalRequiredClaimSetId",
  );
}

export async function approveLegalRequiredClaimSet(
  projectId: string,
  claimSetId: string,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    `/required-claim-sets/${encodeURIComponent(claimSetId)}/approve`,
    {},
    "legalRequiredClaimSetId",
  );
}

export async function proposeLegalWaiver(
  projectId: string,
  input: LegalWaiverInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(projectId, "/waivers", input, "legalWaiverId");
}

export async function approveLegalWaiver(
  projectId: string,
  waiverId: string,
  validFrom?: string,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    `/waivers/${encodeURIComponent(waiverId)}/approve`,
    validFrom ? { validFrom } : {},
    "legalWaiverId",
  );
}

export async function appendCounselOpinion(
  projectId: string,
  input: CounselOpinionInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(projectId, "/counsel-opinions", input, "counselOpinionId");
}

export async function approveCounselOpinion(
  projectId: string,
  opinionId: string,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    `/counsel-opinions/${encodeURIComponent(opinionId)}/approve`,
    {},
    "counselOpinionId",
  );
}

export async function appendLegalAnalysis(
  projectId: string,
  input: LegalAnalysisInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    "/analysis-versions",
    input,
    "legalAnalysisVersionId",
  );
}

export async function reviewLegalAnalysis(
  projectId: string,
  analysisVersionId: string,
  input: LegalAnalysisReviewInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    `/analysis-versions/${encodeURIComponent(analysisVersionId)}/review`,
    input,
    "legalAnalysisVersionId",
  );
}

export async function proposeTenantLegalTermAlias(
  projectId: string,
  input: TenantLegalTermAliasInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    "/term-aliases",
    input,
    "tenantLegalTermAliasId",
  );
}

export async function reviewTenantLegalTermAlias(
  projectId: string,
  aliasId: string,
  input: TenantLegalTermAliasReviewInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    `/term-aliases/${encodeURIComponent(aliasId)}/review`,
    input,
    "tenantLegalTermAliasId",
  );
}

export async function appendLegalAnnotation(
  projectId: string,
  input: LegalAnnotationInput,
): Promise<LegalGovernanceWriteResponse> {
  return write(
    projectId,
    "/annotations",
    input,
    "projectLegalAnnotationId",
  );
}

export function normalizeLegalGovernanceWorkspace(
  body: unknown,
): LegalGovernanceWorkspace {
  const source = record(body);
  return {
    schemaVersion:
      text(source, "schemaVersion", "schema_version") ??
      "realytics-legal-governance-workspace-v1",
    projectId: text(source, "projectId", "project_id") ?? "",
    reviewWriteAvailable:
      typeof source.reviewWriteAvailable === "boolean" ? source.reviewWriteAvailable : undefined,
    matters: array(source.matters).map(normalizeMatter),
    waivers: array(source.waivers).map(normalizeWaiver),
    counselOpinions: array(
      source.counselOpinions ?? source.counsel_opinions,
    ).map(normalizeOpinion),
    analysisVersions: array(
      source.analysisVersions ?? source.analysis_versions,
    ).map(normalizeAnalysis),
    annotations: array(source.annotations).map(normalizeAnnotation),
    approvalDelegations: array(
      source.approvalDelegations ?? source.approval_delegations,
    ).map(normalizeDelegation),
    tenantTermAliases: array(
      source.tenantTermAliases ?? source.tenant_term_aliases,
    ).map(normalizeTenantTermAlias),
    requiredClaimSets: array(
      source.requiredClaimSets ?? source.required_claim_sets,
    ).map(normalizeRequiredClaimSet),
  };
}

function normalizeRequiredClaimSet(value: unknown): LegalRequiredClaimSet {
  const source = record(value);
  return {
    claimSetId:
      text(
        source,
        "legalRequiredClaimSetId",
        "legal_required_claim_set_id",
      ) ?? "",
    claimSetKey: text(source, "claimSetKey", "claim_set_key") ?? "",
    claimSetVersion: integer(
      source,
      "claimSetVersion",
      "claim_set_version",
    ),
    reviewScope: enumeration(
      source,
      ["reviewScope", "review_scope"],
      REQUIRED_CLAIM_SCOPES,
      "approval_gate",
    ),
    status: enumeration(
      source,
      ["status"],
      REQUIRED_CLAIM_SET_STATUSES,
      "draft",
    ),
    claimSetSha256:
      text(source, "claimSetSha256", "claim_set_sha256") ?? "",
    claims: array(source.claims).map(normalizeRequiredClaim),
    createdBy: text(source, "createdBy", "created_by") ?? "",
    createdAt: text(source, "createdAt", "created_at") ?? "",
    approvedBy: text(source, "approvedBy", "approved_by"),
    approvedAt: text(source, "approvedAt", "approved_at"),
    supersedesClaimSetId: text(
      source,
      "supersedesClaimSetId",
      "supersedes_claim_set_id",
    ),
  };
}

function normalizeRequiredClaim(value: unknown): LegalRequiredClaim {
  const source = record(value);
  return {
    claimKey: text(source, "claimKey", "claim_key") ?? "",
    claimTextSha256:
      text(source, "claimTextSha256", "claim_text_sha256") ?? "",
    description: text(source, "description"),
    ordinal: integer(source, "ordinal"),
  };
}

function normalizeTenantTermAlias(value: unknown): TenantLegalTermAlias {
  const source = record(value);
  return {
    aliasId:
      text(
        source,
        "tenantLegalTermAliasId",
        "tenant_legal_term_alias_id",
      ) ?? "",
    termId: text(source, "termId", "term_id") ?? "",
    aliasVersion: integer(source, "aliasVersion", "alias_version"),
    aliasText: text(source, "aliasText", "alias_text") ?? "",
    normalizedAlias:
      text(source, "normalizedAlias", "normalized_alias") ?? "",
    status: enumeration(
      source,
      ["status"],
      TERM_ALIAS_STATUSES,
      "candidate",
    ),
    proposedBy: text(source, "proposedBy", "proposed_by") ?? "",
    proposedAt: text(source, "proposedAt", "proposed_at") ?? "",
    reviewedBy: text(source, "reviewedBy", "reviewed_by"),
    reviewedAt: text(source, "reviewedAt", "reviewed_at"),
    supersedesAliasId: text(
      source,
      "supersedesAliasId",
      "supersedes_alias_id",
    ),
  };
}

function normalizeDelegation(value: unknown): LegalApprovalDelegation {
  const source = record(value);
  return {
    delegationId:
      text(
        source,
        "legalApprovalDelegationId",
        "legal_approval_delegation_id",
      ) ?? "",
    delegationKey:
      text(source, "delegationKey", "delegation_key") ?? "",
    delegationVersion: integer(
      source,
      "delegationVersion",
      "delegation_version",
    ),
    grantorId: text(source, "grantorId", "grantor_id") ?? "",
    grantorRole: "legal_counsel_approver",
    granteeId: text(source, "granteeId", "grantee_id") ?? "",
    granteeRole: enumeration(
      source,
      ["granteeRole", "grantee_role"],
      DELEGATION_ROLES,
      "external_counsel",
    ),
    scope: enumeration(
      source,
      ["scope"],
      DELEGATION_SCOPES,
      "approval_gate",
    ),
    validFrom: text(source, "validFrom", "valid_from") ?? "",
    validUntil: text(source, "validUntil", "valid_until") ?? "",
    status: enumeration(
      source,
      ["status"],
      DELEGATION_STATUSES,
      "revoked",
    ),
    reason: text(source, "reason") ?? "",
    supersedesDelegationId: text(
      source,
      "supersedesDelegationId",
      "supersedes_delegation_id",
    ),
    createdAt: text(source, "createdAt", "created_at") ?? "",
  };
}

function normalizeMatter(value: unknown): LegalGovernanceMatter {
  const source = record(value);
  return {
    matterId: text(source, "projectLegalMatterId", "project_legal_matter_id") ?? "",
    matterKey: text(source, "matterKey", "matter_key") ?? "",
    matterVersion: integer(source, "matterVersion", "matter_version"),
    title: text(source, "title") ?? "제목 미확인",
    matterType: enumeration(
      source,
      ["matterType", "matter_type"],
      MATTER_TYPES,
      "other",
    ),
    status: enumeration(
      source,
      ["status"],
      MATTER_STATUSES,
      "open",
    ),
    severity: enumeration(
      source,
      ["severity"],
      SEVERITIES,
      "medium",
    ),
    ownerUserId: text(source, "ownerUserId", "owner_user_id"),
    dueAt: text(source, "dueAt", "due_at"),
    openedAt: text(source, "openedAt", "opened_at") ?? "",
    closedAt: text(source, "closedAt", "closed_at"),
    createdBy: text(source, "createdBy", "created_by") ?? "",
    supersedesMatterId: text(
      source,
      "supersedesMatterId",
      "supersedes_matter_id",
    ),
  };
}

function normalizeWaiver(value: unknown): LegalGovernanceWaiver {
  const source = record(value);
  return {
    waiverId: text(source, "legalWaiverId", "legal_waiver_id") ?? "",
    matterId:
      text(source, "projectLegalMatterId", "project_legal_matter_id") ?? "",
    evidenceSnapshotId:
      text(source, "legalEvidenceSnapshotId", "legal_evidence_snapshot_id") ?? "",
    waiverVersion: integer(source, "waiverVersion", "waiver_version"),
    status: enumeration(source, ["status"], WAIVER_STATUSES, "proposed"),
    reason: text(source, "reason") ?? "",
    conditions: array(source.conditions ?? source.conditions_json).map(
      normalizeCondition,
    ),
    validFrom: text(source, "validFrom", "valid_from"),
    validUntil: text(source, "validUntil", "valid_until") ?? "",
    cureStatus: enumeration(
      source,
      ["cureStatus", "cure_status"],
      CURE_STATUSES,
      "not_started",
    ),
    cureEvidenceSnapshotId: text(
      source,
      "cureEvidenceSnapshotId",
      "cure_evidence_snapshot_id",
    ),
    proposedBy: text(source, "proposedBy", "proposed_by") ?? "",
    proposedAt: text(source, "proposedAt", "proposed_at") ?? "",
    approvedBy: text(source, "approvedBy", "approved_by"),
    approvedAt: text(source, "approvedAt", "approved_at"),
    supersedesWaiverId: text(
      source,
      "supersedesWaiverId",
      "supersedes_waiver_id",
    ),
  };
}

function normalizeCondition(value: unknown): LegalWaiverCondition {
  const source = record(value);
  return {
    code: text(source, "code") ?? "",
    description: text(source, "description") ?? "",
    dueAt: text(source, "dueAt", "due_at"),
    requiredEvidenceType: text(
      source,
      "requiredEvidenceType",
      "required_evidence_type",
    ),
  };
}

function normalizeOpinion(value: unknown): CounselOpinionMetadata {
  const source = record(value);
  return {
    opinionId: text(source, "counselOpinionId", "counsel_opinion_id") ?? "",
    matterId:
      text(source, "projectLegalMatterId", "project_legal_matter_id") ?? "",
    evidenceSnapshotId:
      text(source, "legalEvidenceSnapshotId", "legal_evidence_snapshot_id") ?? "",
    opinionVersion: integer(source, "opinionVersion", "opinion_version"),
    status: enumeration(source, ["status"], OPINION_STATUSES, "draft"),
    contentSha256: text(source, "contentSha256", "content_sha256") ?? "",
    privileged: bool(source, "privileged", true),
    protectedObjectPresent: bool(
      source,
      "protectedObjectPresent",
      false,
      "protected_object_present",
    ),
    authoredBy: text(source, "authoredBy", "authored_by") ?? "",
    authoredAt: text(source, "authoredAt", "authored_at") ?? "",
    approvedBy: text(source, "approvedBy", "approved_by"),
    approvedAt: text(source, "approvedAt", "approved_at"),
    supersedesOpinionId: text(
      source,
      "supersedesOpinionId",
      "supersedes_opinion_id",
    ),
  };
}

function normalizeAnalysis(value: unknown): LegalAnalysisMetadata {
  const source = record(value);
  const reviewerRole = text(source, "reviewerRole", "reviewer_role");
  return {
    analysisVersionId:
      text(
        source,
        "legalAnalysisVersionId",
        "legal_analysis_version_id",
      ) ?? "",
    matterId:
      text(source, "projectLegalMatterId", "project_legal_matter_id") ?? "",
    evidenceSnapshotId:
      text(source, "legalEvidenceSnapshotId", "legal_evidence_snapshot_id") ?? "",
    analysisKind: enumeration(
      source,
      ["analysisKind", "analysis_kind"],
      ANALYSIS_KINDS,
      "ai_summary",
    ),
    analysisVersion: integer(source, "analysisVersion", "analysis_version"),
    contentSha256: text(source, "contentSha256", "content_sha256") ?? "",
    inputSnapshotSha256:
      text(source, "inputSnapshotSha256", "input_snapshot_sha256") ?? "",
    modelId: text(source, "modelId", "model_id"),
    analysisOnly: bool(source, "analysisOnly", true, "analysis_only"),
    reportEligible: bool(source, "reportEligible", false, "report_eligible"),
    humanReviewStatus: enumeration(
      source,
      ["humanReviewStatus", "human_review_status"],
      REVIEW_STATUSES,
      "pending",
    ),
    protectedObjectPresent: bool(
      source,
      "protectedObjectPresent",
      false,
      "protected_object_present",
    ),
    reviewedBy: text(source, "reviewedBy", "reviewed_by"),
    reviewerRole:
      reviewerRole &&
      ANALYSIS_REVIEWER_ROLES.includes(
        reviewerRole as (typeof ANALYSIS_REVIEWER_ROLES)[number],
      )
        ? (reviewerRole as (typeof ANALYSIS_REVIEWER_ROLES)[number])
        : undefined,
    reviewedAt: text(source, "reviewedAt", "reviewed_at"),
    createdBy: text(source, "createdBy", "created_by") ?? "",
    createdAt: text(source, "createdAt", "created_at") ?? "",
    supersedesAnalysisVersionId: text(
      source,
      "supersedesAnalysisVersionId",
      "supersedes_analysis_version_id",
    ),
  };
}

function normalizeAnnotation(value: unknown): LegalGovernanceAnnotation {
  const source = record(value);
  return {
    annotationId:
      text(
        source,
        "projectLegalAnnotationId",
        "project_legal_annotation_id",
      ) ?? "",
    matterId:
      text(source, "projectLegalMatterId", "project_legal_matter_id") ?? "",
    legalResourceId:
      text(source, "legalResourceId", "legal_resource_id") ?? "",
    legalResourceVersionId:
      text(source, "legalResourceVersionId", "legal_resource_version_id") ?? "",
    legalProvisionId: text(
      source,
      "legalProvisionId",
      "legal_provision_id",
    ),
    exactLocator: text(source, "exactLocator", "exact_locator") ?? "",
    annotationTextSha256:
      text(source, "annotationTextSha256", "annotation_text_sha256") ?? "",
    annotationType: enumeration(
      source,
      ["annotationType", "annotation_type"],
      ANNOTATION_TYPES,
      "issue",
    ),
    status: enumeration(
      source,
      ["status"],
      ANNOTATION_STATUSES,
      "active",
    ),
    createdBy: text(source, "createdBy", "created_by") ?? "",
    createdAt: text(source, "createdAt", "created_at") ?? "",
    supersedesAnnotationId: text(
      source,
      "supersedesAnnotationId",
      "supersedes_annotation_id",
    ),
  };
}

async function write(
  projectId: string,
  path: string,
  body: unknown,
  idKey: string,
): Promise<LegalGovernanceWriteResponse> {
  const response = await fetch(`${governanceBase(projectId)}${path}`, {
    method: "POST",
    headers: {
      ...authHeaders("web"),
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const payload = record(
    await readOrThrow(response, "법률 거버넌스 변경을 저장하지 못했습니다."),
  );
  const recordId = text(payload, idKey);
  if (!recordId) throw new Error("서버가 생성된 법률 레코드 식별자를 반환하지 않았습니다.");
  return {
    schemaVersion:
      text(payload, "schemaVersion", "schema_version") ??
      "realytics-legal-governance-write-v1",
    appendOnly: payload.appendOnly === true || payload.append_only === true,
    recordId,
  } as LegalGovernanceWriteResponse;
}

async function readOrThrow(response: Response, fallback: string): Promise<unknown> {
  const body = await response.json().catch(() => ({}));
  if (response.ok) return body;
  const source = record(body);
  const code = text(record(source.detail), "error") ?? text(source, "error");
  if (code === "normalized_legal_store_required") {
    throw new Error("현재 서버에서 법률 기록을 저장할 수 없습니다.");
  }
  if (response.status === 401) throw new Error("로그인한 뒤 다시 시도해 주세요.");
  if (response.status === 403 || response.status === 404) {
    throw new Error("이 프로젝트의 법률 기록을 확인하거나 변경할 권한을 확인해 주세요.");
  }
  if (response.status >= 500) {
    throw new Error("법률 기록 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.");
  }
  throw new Error(`${fallback} (${response.status})`);
}

function governanceBase(projectId: string): string {
  return `${apiBase}/projects/${encodeURIComponent(projectId)}/legal`;
}

function record(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function text(source: JsonRecord, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return undefined;
}

function integer(source: JsonRecord, ...keys: string[]): number {
  for (const key of keys) {
    const value = source[key];
    if (typeof value === "number" && Number.isInteger(value)) return value;
  }
  return 1;
}

function bool(
  source: JsonRecord,
  primaryKey: string,
  fallback: boolean,
  alternateKey?: string,
): boolean {
  const value = source[primaryKey] ?? (alternateKey ? source[alternateKey] : undefined);
  return typeof value === "boolean" ? value : fallback;
}

function enumeration<T extends string>(
  source: JsonRecord,
  keys: string[],
  values: readonly T[],
  fallback: T,
): T {
  const value = text(source, ...keys);
  return value && values.includes(value as T) ? (value as T) : fallback;
}

const MATTER_TYPES = [
  "statutory",
  "permit",
  "land_title",
  "contract",
  "security",
  "trust",
  "dispute",
  "compliance",
  "other",
] as const satisfies readonly LegalMatterType[];
const MATTER_STATUSES = [
  "open",
  "under_review",
  "blocked",
  "resolved",
  "waived",
  "closed",
  "superseded",
] as const satisfies readonly LegalMatterStatus[];
const SEVERITIES = [
  "info",
  "low",
  "medium",
  "high",
  "critical",
] as const satisfies readonly LegalSeverity[];
const WAIVER_STATUSES = [
  "proposed",
  "approved",
  "rejected",
  "expired",
  "cured",
  "revoked",
  "superseded",
] as const satisfies readonly LegalWaiverStatus[];
const CURE_STATUSES = [
  "not_started",
  "in_progress",
  "evidence_submitted",
  "verified",
  "failed",
  "not_applicable",
] as const satisfies readonly LegalWaiverCureStatus[];
const OPINION_STATUSES = [
  "draft",
  "issued",
  "approved",
  "withdrawn",
  "superseded",
] as const satisfies readonly CounselOpinionStatus[];
const ANALYSIS_KINDS = [
  "ai_summary",
  "deterministic_analysis",
  "counsel_summary",
] as const satisfies readonly LegalAnalysisKind[];
const REVIEW_STATUSES = [
  "pending",
  "approved",
  "rejected",
] as const satisfies readonly HumanReviewStatus[];
const ANALYSIS_REVIEWER_ROLES = [
  "legal_reviewer",
  "legal_counsel_approver",
  "external_counsel",
] as const;
const TERM_ALIAS_STATUSES = [
  "candidate",
  "approved",
  "rejected",
  "superseded",
] as const satisfies readonly TenantLegalTermAliasStatus[];
const REQUIRED_CLAIM_SET_STATUSES = [
  "draft",
  "approved",
  "rejected",
  "superseded",
] as const satisfies readonly LegalRequiredClaimSetStatus[];
const REQUIRED_CLAIM_SCOPES = [
  "approval_gate",
  "report_eligibility",
  "counsel_opinion",
] as const satisfies readonly LegalRequiredClaimReviewScope[];
const ANNOTATION_TYPES = [
  "issue",
  "interpretation",
  "counsel_note",
  "cure_evidence",
  "cross_reference",
] as const satisfies readonly LegalAnnotationType[];
const ANNOTATION_STATUSES = [
  "active",
  "resolved",
  "superseded",
] as const satisfies readonly LegalAnnotationStatus[];
const DELEGATION_ROLES = [
  "legal_counsel_approver",
  "external_counsel",
] as const;
const DELEGATION_SCOPES = [
  "approval_gate",
  "counsel_opinion",
  "waiver_approval",
] as const;
const DELEGATION_STATUSES = [
  "active",
  "revoked",
  "expired",
  "consumed",
] as const;
