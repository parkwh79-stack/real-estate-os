export type LegalCitatorRecordKind = "citation" | "relation";

export type LegalCitatorReviewStatus =
  | "approved"
  | "rejected"
  | "needs_revision";

export type LegalCitatorHumanReviewStatus =
  | "not_required"
  | "pending"
  | "approved"
  | "rejected";

export interface LegalCitatorEvidenceBinding {
  snapshotId: string;
  snapshotVersion: number;
  snapshotSha256: string;
  snapshotAsOfDate: string;
  evidenceItemId: string;
  resourceVersionId: string;
  provisionId: string;
  exactLocator: string;
  sourceFetchId: string;
  providerId: string;
  excerptSha256: string;
  provisionSha256: string;
  verifiedAt: string;
  sourceAuthority: string;
  authorityTier: string;
  freshnessPolicyVersion: string;
  current: true;
}

interface LegalCitatorQueueItemBase {
  recordId: string;
  status: string;
  corpusStatus: string;
  assertionMethod: string;
  confidence?: number;
  assertedAt: string;
  humanReviewStatus: LegalCitatorHumanReviewStatus;
  reviewedBy?: string;
  reviewedAt?: string;
  latestReviewEventId?: string;
  latestReviewVersionId?: string;
  reviewVersion?: number;
  reviewStatus?: LegalCitatorReviewStatus;
  currentEvidenceBinding: LegalCitatorEvidenceBinding;
  reviewBindingCurrent: boolean | null;
}

export interface LegalCitatorCitationQueueItem extends LegalCitatorQueueItemBase {
  recordKind: "citation";
  identifier: string;
}

export interface LegalCitatorRelationQueueItem extends LegalCitatorQueueItemBase {
  recordKind: "relation";
  subjectResourceId: string;
  objectResourceId: string;
  relationType: string;
}

export type LegalCitatorQueueItem =
  | LegalCitatorCitationQueueItem
  | LegalCitatorRelationQueueItem;

export interface LegalCitatorReviewQueue {
  schemaVersion: "realytics-legal-citator-review-queue-v2";
  authorityModel: "tenant_project_review_overlay";
  corpusRecordsImmutable: true;
  citations: LegalCitatorCitationQueueItem[];
  relations: LegalCitatorRelationQueueItem[];
  totalCount: number;
}

/**
 * The browser supplies only review evidence and chain/delegation references.
 * Reviewer identity and role are deliberately absent and must come from the
 * authenticated server session.
 */
export interface LegalCitatorReviewInput {
  reviewStatus: LegalCitatorReviewStatus;
  reason: string;
  evidenceExcerpt?: string;
  objectEvidenceItemId?: string;
  supersedesReviewEventId?: string;
  legalApprovalDelegationId?: string;
}

export interface LegalCitatorReviewRequest {
  reviewStatus: LegalCitatorReviewStatus;
  reason: string;
  evidenceExcerpt?: string;
  evidenceExcerptSha256?: string;
  objectEvidenceItemId?: string;
  supersedesReviewEventId?: string;
  legalApprovalDelegationId?: string;
}

export interface LegalCitatorReviewOutcome {
  schemaVersion: "realytics-legal-citator-review-v2";
  authorityModel: "tenant_project_review_overlay";
  corpusRecordMutated: false;
  recordKind: LegalCitatorRecordKind;
  sourceRecordId: string;
  reviewVersionId: string;
  reviewVersion: number;
  reviewEventId: string;
  reviewStatus: LegalCitatorReviewStatus;
  finalStatus: string;
  reviewerId: string;
  reviewerRole: string;
  reviewedAt: string;
  evidenceExcerptSha256?: string;
  legalApprovalDelegationId?: string;
  evidenceBinding: LegalCitatorEvidenceBinding;
  objectEvidenceBinding?: LegalCitatorEvidenceBinding;
  appendOnly: true;
}
