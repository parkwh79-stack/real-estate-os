export interface NormalizedLegalApprovalReadiness {
  legalEvidenceStatus?: string;
  legalEvidenceVerifiedReferenceCount?: number | string;
  legalEvidenceVerifiedCitationCount?: number | string;
  legalEvidenceRequiredClaimCount?: number | string;
  legalEvidenceVerifiedClaimCount?: number | string;
  legalEvidenceClaimCoverageComplete?: boolean;
  legalEvidenceRequiredClaimPolicyValid?: boolean;
  legalEvidenceRequiredClaimSetId?: string;
  legalEvidenceRequiredClaimSetVersion?: number | string;
  legalEvidenceRequiredClaimSetSha256?: string;
  legalEvidenceSnapshotId?: string;
  legalEvidenceSnapshotVersion?: number | string;
  legalEvidenceSnapshotSha256?: string;
  legalEvidenceSnapshotGateEligible?: boolean;
  legalEvidenceSnapshotSuperseded?: boolean;
  legalEvidenceStale?: boolean;
  legalEvidenceOpenConflictCount?: number | string;
  legalEvidenceLineageValid?: boolean;
  legalEvidenceSource?: string;
  legalEvidenceNormalizedRequired?: boolean;
  legalEvidenceNormalizedReadable?: boolean;
  legalEvidenceLatestDecisionId?: string;
  legalEvidenceLatestDecisionVersion?: number | string;
  legalEvidenceLatestDecisionSupersedesId?: string;
  legalEvidenceLatestDecisionChainValid?: boolean;
  legalActiveBlockingMatterCount?: number | string;
  legalWaivedMatterCount?: number | string;
  legalInvalidWaiverCount?: number | string;
  legalMatterWaiverStateValid?: boolean;
  legalMatterWaiverGovernanceValid?: boolean;
  legalGovernanceChangedAfterDecision?: boolean;
}

/**
 * UI guard only. The server re-evaluates the same facts from normalized
 * PostgreSQL before accepting a decision. Missing fields fail closed and no
 * legacy Legal Pod projection participates in this result.
 */
export function isNormalizedLegalEvidenceApprovalEligible(
  gate: NormalizedLegalApprovalReadiness | null | undefined,
): boolean {
  if (
    gate?.legalEvidenceNormalizedRequired === false
    || gate?.legalEvidenceSource !== "normalized_postgres"
    || gate?.legalEvidenceNormalizedReadable !== true
    || String(gate.legalEvidenceStatus ?? "").toLowerCase() !== "verified"
  ) {
    return false;
  }
  const requiredClaimCount = nonNegative(gate.legalEvidenceRequiredClaimCount);
  const verifiedClaimCount = nonNegative(gate.legalEvidenceVerifiedClaimCount);
  return (
    nonNegative(gate.legalEvidenceVerifiedReferenceCount) > 0
    && nonNegative(gate.legalEvidenceVerifiedCitationCount) > 0
    && gate.legalEvidenceRequiredClaimPolicyValid === true
    && gate.legalEvidenceClaimCoverageComplete === true
    && requiredClaimCount > 0
    && verifiedClaimCount === requiredClaimCount
    && Boolean(gate.legalEvidenceRequiredClaimSetId?.trim())
    && nonNegative(gate.legalEvidenceRequiredClaimSetVersion) > 0
    && Boolean(
      gate.legalEvidenceRequiredClaimSetSha256
        ?.trim()
        .startsWith("sha256:"),
    )
    && Boolean(gate.legalEvidenceSnapshotId?.trim())
    && nonNegative(gate.legalEvidenceSnapshotVersion) > 0
    && Boolean(
      gate.legalEvidenceSnapshotSha256?.trim().startsWith("sha256:"),
    )
    && gate.legalEvidenceSnapshotGateEligible === true
    && gate.legalEvidenceSnapshotSuperseded === false
    && gate.legalEvidenceStale === false
    && nonNegative(gate.legalEvidenceOpenConflictCount) === 0
    && gate.legalEvidenceLineageValid === true
    && nonNegative(gate.legalActiveBlockingMatterCount) === 0
    && nonNegative(gate.legalInvalidWaiverCount) === 0
    && gate.legalMatterWaiverStateValid === true
  );
}

function nonNegative(value: number | string | undefined): number {
  if (value === undefined || String(value).trim() === "") return -1;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : -1;
}
