import { expect, test } from "@playwright/test";
import { isNormalizedLegalEvidenceApprovalEligible } from "../lib/legal-approval-readiness";

const completeNormalizedReadiness = {
  legalEvidenceStatus: "verified",
  legalEvidenceVerifiedReferenceCount: 2,
  legalEvidenceVerifiedCitationCount: 2,
  legalEvidenceRequiredClaimCount: 2,
  legalEvidenceVerifiedClaimCount: 2,
  legalEvidenceClaimCoverageComplete: true,
  legalEvidenceRequiredClaimPolicyValid: true,
  legalEvidenceRequiredClaimSetId: "required-claims-1",
  legalEvidenceRequiredClaimSetVersion: 2,
  legalEvidenceRequiredClaimSetSha256: `sha256:${"c".repeat(64)}`,
  legalEvidenceSnapshotId: "snapshot-1",
  legalEvidenceSnapshotVersion: 3,
  legalEvidenceSnapshotSha256: `sha256:${"a".repeat(64)}`,
  legalEvidenceSnapshotGateEligible: true,
  legalEvidenceSnapshotSuperseded: false,
  legalEvidenceStale: false,
  legalEvidenceOpenConflictCount: 0,
  legalEvidenceLineageValid: true,
  legalEvidenceSource: "normalized_postgres",
  legalEvidenceNormalizedRequired: true,
  legalEvidenceNormalizedReadable: true,
  legalActiveBlockingMatterCount: 0,
  legalWaivedMatterCount: 1,
  legalInvalidWaiverCount: 0,
  legalMatterWaiverStateValid: true,
  legalMatterWaiverGovernanceValid: true,
  legalGovernanceChangedAfterDecision: false,
};

test("accepts only complete normalized PostgreSQL legal readiness", () => {
  expect(
    isNormalizedLegalEvidenceApprovalEligible(completeNormalizedReadiness),
  ).toBe(true);
});

test("fails closed when a legacy projection copies passing booleans", () => {
  expect(
    isNormalizedLegalEvidenceApprovalEligible({
      ...completeNormalizedReadiness,
      legalEvidenceSource: "legacy_projection_fallback",
      legalEvidenceNormalizedReadable: undefined,
    }),
  ).toBe(false);
});

test("rejects superseded or incomplete normalized snapshots", () => {
  expect(
    isNormalizedLegalEvidenceApprovalEligible({
      ...completeNormalizedReadiness,
      legalEvidenceSnapshotSuperseded: true,
    }),
  ).toBe(false);
  expect(
    isNormalizedLegalEvidenceApprovalEligible({
      ...completeNormalizedReadiness,
      legalEvidenceVerifiedClaimCount: 1,
    }),
  ).toBe(false);
  expect(
    isNormalizedLegalEvidenceApprovalEligible({
      ...completeNormalizedReadiness,
      legalEvidenceRequiredClaimPolicyValid: false,
    }),
  ).toBe(false);
  expect(
    isNormalizedLegalEvidenceApprovalEligible({
      ...completeNormalizedReadiness,
      legalActiveBlockingMatterCount: 1,
    }),
  ).toBe(false);
  expect(
    isNormalizedLegalEvidenceApprovalEligible({
      ...completeNormalizedReadiness,
      legalInvalidWaiverCount: 1,
    }),
  ).toBe(false);
  expect(
    isNormalizedLegalEvidenceApprovalEligible({
      ...completeNormalizedReadiness,
      legalEvidenceOpenConflictCount: undefined,
    }),
  ).toBe(false);
});

test("allows a new human decision when legal state is valid but the old decision is stale", () => {
  expect(
    isNormalizedLegalEvidenceApprovalEligible({
      ...completeNormalizedReadiness,
      legalMatterWaiverGovernanceValid: false,
      legalGovernanceChangedAfterDecision: true,
    }),
  ).toBe(true);
});
