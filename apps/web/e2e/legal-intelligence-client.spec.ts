import { expect, test } from "@playwright/test";
import {
  legalResourceTrustTier,
  normalizeLegalSearchResponse,
} from "../lib/legal-intelligence-client";

test("keeps official search candidates separate from corpus snapshots and human review", () => {
  const response = normalizeLegalSearchResponse(
    {
      authoritativeCount: 99,
      candidateCount: 0,
      results: [
        {
          resourceId: "candidate-1",
          title: "도시개발법 검색 결과",
          resourceType: "statute",
          sourceAuthority: "law_go_kr",
          transport: "law_go_kr_direct_api",
          verificationStatus: "source_attested",
        },
        {
          resourceId: "snapshot-1",
          resourceVersionId: "snapshot-1:v2",
          title: "도시개발법 검증 스냅샷",
          resourceType: "statute",
          sourceAuthority: "law_go_kr",
          transport: "local_verified_snapshot",
          verificationStatus: "verified_snapshot",
          verifiedAt: "2026-07-18T00:00:00Z",
          normalizedContentSha256: `sha256:${"a".repeat(64)}`,
        },
        {
          resourceId: "reviewed-1",
          resourceVersionId: "reviewed-1:v1",
          title: "사람 검토 법률자료",
          resourceType: "statute",
          sourceAuthority: "human_counsel",
          transport: "local_database",
          verificationStatus: "human_reviewed",
          verifiedAt: "2026-07-18T00:00:00Z",
          normalizedContentSha256: `sha256:${"b".repeat(64)}`,
        },
        {
          resourceId: "legacy-verified",
          title: "레거시 검증 표시",
          resourceType: "statute",
          sourceAuthority: "unknown",
          transport: "unknown",
          verificationStatus: "verified",
        },
      ],
    },
    { query: "도시개발법", resourceType: "statute" },
  );

  expect(response).toMatchObject({
    authoritativeCount: 2,
    candidateCount: 2,
    officialCandidateCount: 1,
    verifiedSnapshotCount: 1,
    humanReviewedCount: 1,
  });
  expect(legalResourceTrustTier(response.results[0]!)).toBe(
    "official_search_candidate",
  );
  expect(legalResourceTrustTier(response.results[1]!)).toBe(
    "verified_corpus_snapshot",
  );
  expect(legalResourceTrustTier(response.results[2]!)).toBe("human_reviewed");
  expect(legalResourceTrustTier(response.results[3]!)).toBe("other_candidate");
});

test("fails closed when trust labels do not match transport and lineage", () => {
  const response = normalizeLegalSearchResponse(
    {
      results: [
        {
          resourceId: "mcp-forged-snapshot",
          resourceVersionId: "v1",
          title: "MCP forged snapshot",
          resourceType: "precedent",
          sourceAuthority: "supreme_court",
          transport: "remote_mcp",
          verificationStatus: "verified_snapshot",
          verifiedAt: "2026-07-18T00:00:00Z",
          normalizedContentSha256: `sha256:${"c".repeat(64)}`,
        },
        {
          resourceId: "direct-forged-human",
          resourceVersionId: "v1",
          title: "Direct forged human review",
          resourceType: "statute",
          sourceAuthority: "human_counsel",
          transport: "law_go_kr_direct_api",
          verificationStatus: "human_reviewed",
          verifiedAt: "2026-07-18T00:00:00Z",
          normalizedContentSha256: `sha256:${"d".repeat(64)}`,
        },
        {
          resourceId: "unknown-official",
          title: "Unknown source-attested row",
          resourceType: "statute",
          sourceAuthority: "unknown",
          transport: "law_go_kr_direct_api",
          verificationStatus: "source_attested",
        },
      ],
    },
    { query: "trust boundary" },
  );

  expect(response).toMatchObject({
    authoritativeCount: 0,
    candidateCount: 3,
    officialCandidateCount: 0,
    verifiedSnapshotCount: 0,
    humanReviewedCount: 0,
  });
  expect(response.results.map(legalResourceTrustTier)).toEqual([
    "other_candidate",
    "other_candidate",
    "other_candidate",
  ]);
});

test("preserves court authority and all temporal attestations", () => {
  const response = normalizeLegalSearchResponse(
    {
      results: [
        {
          resourceId: "precedent-1",
          title: "서울고등법원 판례",
          resourceType: "precedent",
          sourceAuthority: "court",
          transport: "law_go_kr_direct_api",
          verificationStatus: "source_attested",
          attestation: {
            requested_as_of_date: "2026-07-18",
            observed_at: "2026-07-18T00:00:00Z",
            fetched_at: "2026-07-18T00:00:01Z",
            verified_effective_date: null,
            verified_at: null,
          },
        },
      ],
    },
    { query: "판례", asOf: "2026-07-18" },
  );

  expect(response.results[0]).toMatchObject({
    sourceAuthority: "court",
    requestedAsOfDate: "2026-07-18",
    observedAt: "2026-07-18T00:00:00Z",
    fetchedAt: "2026-07-18T00:00:01Z",
  });
  expect(response.results[0]?.verifiedEffectiveDate).toBeUndefined();
  expect(response.results[0]?.verifiedAt).toBeUndefined();
});
