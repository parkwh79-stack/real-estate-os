export type LegalMatterType =
  | "statutory"
  | "permit"
  | "land_title"
  | "contract"
  | "security"
  | "trust"
  | "dispute"
  | "compliance"
  | "other";

export type LegalMatterStatus =
  | "open"
  | "under_review"
  | "blocked"
  | "resolved"
  | "waived"
  | "closed"
  | "superseded";

export type LegalSeverity = "info" | "low" | "medium" | "high" | "critical";

export interface LegalGovernanceMatter {
  matterId: string;
  matterKey: string;
  matterVersion: number;
  title: string;
  matterType: LegalMatterType;
  status: LegalMatterStatus;
  severity: LegalSeverity;
  ownerUserId?: string;
  dueAt?: string;
  openedAt: string;
  closedAt?: string;
  createdBy: string;
  supersedesMatterId?: string;
}

export type LegalWaiverStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "expired"
  | "cured"
  | "revoked"
  | "superseded";

export type LegalWaiverCureStatus =
  | "not_started"
  | "in_progress"
  | "evidence_submitted"
  | "verified"
  | "failed"
  | "not_applicable";

export interface LegalWaiverCondition {
  code: string;
  description: string;
  dueAt?: string;
  requiredEvidenceType?: string;
}

export interface LegalGovernanceWaiver {
  waiverId: string;
  matterId: string;
  evidenceSnapshotId: string;
  waiverVersion: number;
  status: LegalWaiverStatus;
  reason: string;
  conditions: LegalWaiverCondition[];
  validFrom?: string;
  validUntil: string;
  cureStatus: LegalWaiverCureStatus;
  cureEvidenceSnapshotId?: string;
  proposedBy: string;
  proposedAt: string;
  approvedBy?: string;
  approvedAt?: string;
  supersedesWaiverId?: string;
}

export type CounselOpinionStatus =
  | "draft"
  | "issued"
  | "approved"
  | "withdrawn"
  | "superseded";

/**
 * Read model intentionally excludes objectLocator and document content.
 * Privileged counsel material is opened only through a separately authorized
 * object-access workflow; this workspace displays lifecycle metadata.
 */
export interface CounselOpinionMetadata {
  opinionId: string;
  matterId: string;
  evidenceSnapshotId: string;
  opinionVersion: number;
  status: CounselOpinionStatus;
  contentSha256: string;
  privileged: boolean;
  protectedObjectPresent: boolean;
  authoredBy: string;
  authoredAt: string;
  approvedBy?: string;
  approvedAt?: string;
  supersedesOpinionId?: string;
}

export type LegalAnalysisKind =
  | "ai_summary"
  | "deterministic_analysis"
  | "counsel_summary";

export type HumanReviewStatus = "pending" | "approved" | "rejected";

export interface LegalAnalysisMetadata {
  analysisVersionId: string;
  matterId: string;
  evidenceSnapshotId: string;
  analysisKind: LegalAnalysisKind;
  analysisVersion: number;
  contentSha256: string;
  inputSnapshotSha256: string;
  modelId?: string;
  analysisOnly: boolean;
  reportEligible: boolean;
  humanReviewStatus: HumanReviewStatus;
  protectedObjectPresent: boolean;
  reviewedBy?: string;
  reviewerRole?:
    | "legal_reviewer"
    | "legal_counsel_approver"
    | "external_counsel";
  reviewedAt?: string;
  createdBy: string;
  createdAt: string;
  supersedesAnalysisVersionId?: string;
}

export interface LegalAnalysisReviewInput {
  reviewStatus: Exclude<HumanReviewStatus, "pending">;
  reason: string;
  reportEligible?: boolean;
}

export type LegalAnnotationType =
  | "issue"
  | "interpretation"
  | "counsel_note"
  | "cure_evidence"
  | "cross_reference";

export type LegalAnnotationStatus = "active" | "resolved" | "superseded";

export interface LegalGovernanceAnnotation {
  annotationId: string;
  matterId: string;
  legalResourceId: string;
  legalResourceVersionId: string;
  legalProvisionId?: string;
  exactLocator: string;
  annotationTextSha256: string;
  annotationType: LegalAnnotationType;
  status: LegalAnnotationStatus;
  createdBy: string;
  createdAt: string;
  supersedesAnnotationId?: string;
}

export interface LegalGovernanceWorkspace {
  schemaVersion: "realytics-legal-governance-workspace-v1" | string;
  projectId: string;
  reviewWriteAvailable?: boolean;
  matters: LegalGovernanceMatter[];
  waivers: LegalGovernanceWaiver[];
  counselOpinions: CounselOpinionMetadata[];
  analysisVersions: LegalAnalysisMetadata[];
  annotations: LegalGovernanceAnnotation[];
  approvalDelegations: LegalApprovalDelegation[];
  tenantTermAliases: TenantLegalTermAlias[];
  requiredClaimSets: LegalRequiredClaimSet[];
}

export type LegalRequiredClaimSetStatus =
  | "draft"
  | "approved"
  | "rejected"
  | "superseded";

export type LegalRequiredClaimReviewScope =
  | "approval_gate"
  | "report_eligibility"
  | "counsel_opinion";

export interface LegalRequiredClaim {
  claimKey: string;
  claimTextSha256: string;
  description?: string;
  ordinal: number;
}

export interface LegalRequiredClaimSet {
  claimSetId: string;
  claimSetKey: string;
  claimSetVersion: number;
  reviewScope: LegalRequiredClaimReviewScope;
  status: LegalRequiredClaimSetStatus;
  claimSetSha256: string;
  claims: LegalRequiredClaim[];
  createdBy: string;
  createdAt: string;
  approvedBy?: string;
  approvedAt?: string;
  supersedesClaimSetId?: string;
}

export interface LegalRequiredClaimSetInput {
  claimSetKey: string;
  claimSetVersion?: number;
  reviewScope?: LegalRequiredClaimReviewScope;
  claims: Array<{
    claimKey: string;
    claimTextSha256: string;
    description?: string;
    metadata?: Record<string, unknown>;
  }>;
  supersedesClaimSetId?: string;
  metadata?: Record<string, unknown>;
}

export type TenantLegalTermAliasStatus =
  | "candidate"
  | "approved"
  | "rejected"
  | "superseded";

export interface TenantLegalTermAlias {
  aliasId: string;
  termId: string;
  aliasVersion: number;
  aliasText: string;
  normalizedAlias: string;
  status: TenantLegalTermAliasStatus;
  proposedBy: string;
  proposedAt: string;
  reviewedBy?: string;
  reviewedAt?: string;
  supersedesAliasId?: string;
}

export interface TenantLegalTermAliasInput {
  termId: string;
  aliasVersion?: number;
  aliasText: string;
  normalizedAlias: string;
  status?: "candidate";
  supersedesAliasId?: string;
  metadata?: Record<string, unknown>;
}

export interface TenantLegalTermAliasReviewInput {
  reviewStatus: "approved" | "rejected" | "needs_revision";
  reason: string;
  metadata?: Record<string, unknown>;
}

export type LegalApprovalDelegationRole =
  | "legal_counsel_approver"
  | "external_counsel";

export type LegalApprovalDelegationScope =
  | "approval_gate"
  | "counsel_opinion"
  | "waiver_approval";

export interface LegalApprovalDelegation {
  delegationId: string;
  delegationKey: string;
  delegationVersion: number;
  grantorId: string;
  grantorRole: "legal_counsel_approver";
  granteeId: string;
  granteeRole: LegalApprovalDelegationRole;
  scope: LegalApprovalDelegationScope;
  validFrom: string;
  validUntil: string;
  status: "active" | "revoked" | "expired" | "consumed";
  reason: string;
  supersedesDelegationId?: string;
  createdAt: string;
}

export interface LegalApprovalDelegationInput {
  granteeId: string;
  granteeRole: LegalApprovalDelegationRole;
  scope: LegalApprovalDelegationScope;
  validFrom?: string;
  validUntil: string;
  reason: string;
  metadata?: Record<string, unknown>;
}

export interface LegalMatterInput {
  matterKey: string;
  matterVersion?: number;
  title: string;
  matterType: LegalMatterType;
  status?: LegalMatterStatus;
  severity?: LegalSeverity;
  ownerUserId?: string;
  dueAt?: string;
  openedAt?: string;
  closedAt?: string;
  supersedesMatterId?: string;
  metadata?: Record<string, unknown>;
}

export interface LegalWaiverInput {
  projectLegalMatterId: string;
  legalEvidenceSnapshotId: string;
  waiverVersion?: number;
  reason: string;
  conditions: Array<{
    code: string;
    description: string;
    dueAt?: string;
    requiredEvidenceType?: string;
  }>;
  validUntil: string;
  cureStatus?: LegalWaiverCureStatus;
  cureEvidenceSnapshotId?: string;
  supersedesWaiverId?: string;
  metadata?: Record<string, unknown>;
}

export interface CounselOpinionInput {
  projectLegalMatterId: string;
  legalEvidenceSnapshotId: string;
  opinionVersion?: number;
  status?: Exclude<CounselOpinionStatus, "approved">;
  objectLocator: string;
  contentSha256: string;
  privileged?: boolean;
  authoredAt?: string;
  supersedesOpinionId?: string;
  metadata?: Record<string, unknown>;
}

export interface LegalAnalysisInput {
  projectLegalMatterId: string;
  legalEvidenceSnapshotId: string;
  analysisKind: LegalAnalysisKind;
  analysisVersion?: number;
  objectLocator: string;
  contentSha256: string;
  inputSnapshotSha256: string;
  modelId?: string;
  promptSha256?: string;
  analysisOnly?: boolean;
  reportEligible?: false;
  humanReviewStatus?: "pending";
  supersedesAnalysisVersionId?: string;
  metadata?: Record<string, unknown>;
}

export interface LegalAnnotationInput {
  projectLegalMatterId: string;
  legalResourceId: string;
  legalResourceVersionId: string;
  legalProvisionId?: string;
  exactLocator: string;
  annotationTextSha256: string;
  annotationType: LegalAnnotationType;
  status?: LegalAnnotationStatus;
  supersedesAnnotationId?: string;
  metadata?: Record<string, unknown>;
}

export interface LegalGovernanceWriteResponse {
  schemaVersion: "realytics-legal-governance-write-v1" | string;
  appendOnly: true;
  recordId: string;
}
