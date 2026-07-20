import { expect, test } from "@playwright/test";
import {
  buildLegalCitatorReviewRequest,
  getLegalCitatorReviewQueue,
  normalizeLegalCitatorReviewQueue,
  reviewLegalCitation,
  reviewLegalRelation,
  sha256BrowserText,
} from "../lib/legal-citator-client";
import type { LegalCitatorReviewInput } from "../lib/legal-citator-types";

const NOW = "2026-07-20T10:00:00+09:00";
const EXCERPT = "공식 원문 exact excerpt";
const EXCERPT_SHA =
  "d16fc9d9958069fa4c057ae6b5e763d4eb3107cb0ec6f565f08f58dad78148db";
const CURRENT_BINDING = {
  snapshotId: "snapshot-current",
  snapshotVersion: 3,
  snapshotSha256: "1".repeat(64),
  snapshotAsOfDate: "2026-07-20",
  evidenceItemId: "evidence-item-current",
  resourceVersionId: "resource-version-current",
  provisionId: "provision-current",
  exactLocator: "law:article:11",
  sourceFetchId: "source-fetch-current",
  providerId: "law-go-kr",
  excerptSha256: EXCERPT_SHA,
  provisionSha256: "2".repeat(64),
  verifiedAt: NOW,
  sourceAuthority: "MINISTRY_OF_GOVERNMENT_LEGISLATION",
  authorityTier: "primary_official",
  freshnessPolicyVersion: "legal-freshness-v1",
  current: true,
} as const;

test("keeps immutable Corpus candidate status separate from project overlay review", () => {
  const queue = normalizeLegalCitatorReviewQueue({
    schemaVersion: "realytics-legal-citator-review-queue-v2",
    authorityModel: "tenant_project_review_overlay",
    corpusRecordsImmutable: true,
    totalCount: 2,
    citations: [
      {
        recordKind: "citation",
        recordId: "citation-1",
        identifier: "건축법 제1조",
        status: "unresolved",
        corpusStatus: "ambiguous",
        assertionMethod: "machine_inferred",
        confidence: 0.82,
        assertedAt: NOW,
        humanReviewStatus: "pending",
        reviewStatus: "needs_revision",
        reviewVersion: 1,
        latestReviewVersionId: "citation-review-version-1",
        latestReviewEventId: "citation-review-event-1",
        reviewedBy: "reviewer-1",
        reviewedAt: NOW,
        currentEvidenceBinding: CURRENT_BINDING,
        reviewBindingCurrent: true,
      },
    ],
    relations: [
      {
        recordKind: "relation",
        recordId: "relation-1",
        subjectResourceId: "resource-subject",
        objectResourceId: "resource-object",
        relationType: "limits",
        status: "unknown",
        corpusStatus: "candidate",
        assertionMethod: "machine_inferred",
        assertedAt: NOW,
        humanReviewStatus: "pending",
        currentEvidenceBinding: CURRENT_BINDING,
        reviewBindingCurrent: null,
      },
    ],
  });

  expect(queue.citations[0]).toMatchObject({
    corpusStatus: "ambiguous",
    status: "unresolved",
    reviewStatus: "needs_revision",
    latestReviewEventId: "citation-review-event-1",
  });
  expect(queue.relations[0]).toMatchObject({
    corpusStatus: "candidate",
    status: "unknown",
    reviewStatus: undefined,
  });
});

test("loads the tenant/project Citator v2 queue with a bounded typed filter", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    requests.push({ url: String(input), init });
    return new Response(
      JSON.stringify({
        schemaVersion: "realytics-legal-citator-review-queue-v2",
        authorityModel: "tenant_project_review_overlay",
        corpusRecordsImmutable: true,
        totalCount: 0,
        citations: [],
        relations: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    await getLegalCitatorReviewQueue("project/1", {
      recordKind: "relation",
      limit: 999,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  expect(requests[0]?.url).toContain(
    "/projects/project%2F1/legal/citator/review-queue?limit=500&recordKind=relation",
  );
  expect(requests[0]?.init?.cache).toBe("no-store");
});

test("fails closed when queue attestation or exact overlay event chain is missing", () => {
  const base = {
    schemaVersion: "realytics-legal-citator-review-queue-v2",
    authorityModel: "tenant_project_review_overlay",
    corpusRecordsImmutable: true,
    totalCount: 1,
    relations: [],
    citations: [
      {
        recordKind: "citation",
        recordId: "citation-1",
        identifier: "건축법 제1조",
        status: "unknown",
        corpusStatus: "unknown",
        assertionMethod: "machine_inferred",
        assertedAt: NOW,
        humanReviewStatus: "pending",
        currentEvidenceBinding: CURRENT_BINDING,
        reviewBindingCurrent: null,
      },
    ],
  };

  expect(() =>
    normalizeLegalCitatorReviewQueue({
      ...base,
      corpusRecordsImmutable: false,
    }),
  ).toThrow(/immutability/);
  expect(() =>
    normalizeLegalCitatorReviewQueue({
      ...base,
      citations: [
        {
          ...base.citations[0],
          latestReviewVersionId: "review-version-without-event",
        },
      ],
    }),
  ).toThrow(/append-only event/);
  expect(() =>
    normalizeLegalCitatorReviewQueue({
      ...base,
      citations: [
        {
          ...base.citations[0],
          currentEvidenceBinding: {
            ...CURRENT_BINDING,
            current: false,
          },
        },
      ],
    }),
  ).toThrow(/current/);
});

test("hashes the trimmed exact excerpt with browser Web Crypto", async () => {
  await expect(sha256BrowserText(EXCERPT)).resolves.toBe(EXCERPT_SHA);
  await expect(
    buildLegalCitatorReviewRequest({
      reviewStatus: "approved",
      reason: "  공식 조문을 대조했습니다.  ",
      evidenceExcerpt: `  ${EXCERPT}  `,
      supersedesReviewEventId: " review-event-1 ",
      legalApprovalDelegationId: " delegation-1 ",
    }),
  ).resolves.toEqual({
    reviewStatus: "approved",
    reason: "공식 조문을 대조했습니다.",
    evidenceExcerpt: EXCERPT,
    evidenceExcerptSha256: EXCERPT_SHA,
    supersedesReviewEventId: "review-event-1",
    legalApprovalDelegationId: "delegation-1",
  });
  await expect(
    buildLegalCitatorReviewRequest({
      reviewStatus: "approved",
      reason: "근거 확인",
    }),
  ).rejects.toThrow(/exact excerpt/);
});

test("requires an exact object evidence item for relation approval", async () => {
  await expect(
    buildLegalCitatorReviewRequest(
      {
        reviewStatus: "approved",
        reason: "interpretive relation checked",
        evidenceExcerpt: EXCERPT,
      },
      "relation",
    ),
  ).rejects.toThrow(/object evidence item ID/);

  await expect(
    buildLegalCitatorReviewRequest(
      {
        reviewStatus: "approved",
        reason: "interpretive relation checked",
        evidenceExcerpt: EXCERPT,
        objectEvidenceItemId: " object-evidence-item-current ",
      },
      "relation",
    ),
  ).resolves.toMatchObject({
    reviewStatus: "approved",
    objectEvidenceItemId: "object-evidence-item-current",
    evidenceExcerptSha256: EXCERPT_SHA,
  });

  await expect(
    buildLegalCitatorReviewRequest(
      {
        reviewStatus: "needs_revision",
        reason: "recheck",
        objectEvidenceItemId: "object-evidence-item-current",
      },
      "citation",
    ),
  ).rejects.toThrow(/only valid for a relation/);
});

test("posts citation and relation reviews without caller actor or role fields", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    requests.push({ url: String(input), init });
    const relation = String(input).includes("/relations/");
    return new Response(
      JSON.stringify({
        schemaVersion: "realytics-legal-citator-review-v2",
        authorityModel: "tenant_project_review_overlay",
        corpusRecordMutated: false,
        recordKind: relation ? "relation" : "citation",
        sourceRecordId: relation ? "relation-1" : "citation-1",
        reviewVersionId: relation
          ? "relation-review-version-1"
          : "citation-review-version-2",
        reviewVersion: relation ? 1 : 2,
        reviewEventId: relation
          ? "relation-review-event-1"
          : "citation-review-event-2",
        reviewStatus: "approved",
        finalStatus: relation ? "verified" : "resolved",
        reviewerId: "server-session-reviewer",
        reviewerRole: "legal_counsel_approver",
        reviewedAt: NOW,
        evidenceExcerptSha256: EXCERPT_SHA,
        legalApprovalDelegationId: relation ? undefined : "delegation-1",
        evidenceBinding: CURRENT_BINDING,
        objectEvidenceBinding: relation
          ? {
              ...CURRENT_BINDING,
              evidenceItemId: "object-evidence-item-current",
              resourceVersionId: "object-version-current",
              provisionId: "object-provision-current",
              exactLocator: "case:holding:42",
              sourceFetchId: "object-source-fetch-current",
            }
          : undefined,
        appendOnly: true,
      }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    const maliciouslyExtended = {
      reviewStatus: "approved",
      reason: "공식 원문 확인",
      evidenceExcerpt: EXCERPT,
      supersedesReviewEventId: "citation-review-event-1",
      legalApprovalDelegationId: "delegation-1",
      actorId: "caller-controlled-actor",
      reviewerRole: "tenant_admin",
    } as LegalCitatorReviewInput;
    await reviewLegalCitation(
      "project/with spaces",
      "citation/1",
      maliciouslyExtended,
    );
    await reviewLegalRelation("project/with spaces", "relation/1", {
      reviewStatus: "approved",
      evidenceExcerpt: EXCERPT,
      objectEvidenceItemId: "object-evidence-item-current",
      reason: "관계 대상 불일치",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  expect(requests[0]?.url).toContain(
    "/projects/project%2Fwith%20spaces/legal/citator/citations/citation%2F1/reviews",
  );
  expect(JSON.parse(String(requests[0]?.init?.body))).toEqual({
    reviewStatus: "approved",
    reason: "공식 원문 확인",
    evidenceExcerpt: EXCERPT,
    evidenceExcerptSha256: EXCERPT_SHA,
    supersedesReviewEventId: "citation-review-event-1",
    legalApprovalDelegationId: "delegation-1",
  });
  expect(requests[1]?.url).toContain(
    "/projects/project%2Fwith%20spaces/legal/citator/relations/relation%2F1/reviews",
  );
  expect(JSON.parse(String(requests[1]?.init?.body))).toEqual({
    reviewStatus: "approved",
    evidenceExcerpt: EXCERPT,
    evidenceExcerptSha256: EXCERPT_SHA,
    objectEvidenceItemId: "object-evidence-item-current",
    reason: "관계 대상 불일치",
  });
});
