import { expect, test } from "@playwright/test";
import {
  approveLegalRequiredClaimSet,
  normalizeLegalGovernanceWorkspace,
  proposeLegalRequiredClaimSet,
  reviewTenantLegalTermAlias,
} from "../lib/legal-governance-client";

test("normalizes PostgreSQL snake_case rows into the tenant governance read model", () => {
  const workspace = normalizeLegalGovernanceWorkspace({
    schemaVersion: "realytics-legal-governance-workspace-v1",
    projectId: "project-1",
    matters: [
      {
        project_legal_matter_id: "matter-1",
        matter_key: "permit-1",
        matter_version: 2,
        title: "사업계획승인 조건",
        matter_type: "permit",
        status: "under_review",
        severity: "high",
        opened_at: "2026-07-20T09:00:00+09:00",
        created_by: "reviewer-1",
      },
    ],
    waivers: [
      {
        legal_waiver_id: "waiver-1",
        project_legal_matter_id: "matter-1",
        legal_evidence_snapshot_id: "snapshot-1",
        waiver_version: 1,
        status: "proposed",
        reason: "조건부 보완",
        conditions_json: [
          {
            code: "CURE-1",
            description: "공식 증빙 첨부",
            requiredEvidenceType: "permit_document",
          },
        ],
        valid_until: "2026-08-20T09:00:00+09:00",
        cure_status: "not_started",
        proposed_by: "maker-1",
        proposed_at: "2026-07-20T09:00:00+09:00",
      },
    ],
    counselOpinions: [],
    analysisVersions: [],
    annotations: [],
    tenantTermAliases: [
      {
        tenant_legal_term_alias_id: "alias-1",
        term_id: "term-1",
        alias_version: 1,
        alias_text: "브릿지론",
        normalized_alias: "브릿지론",
        status: "candidate",
        proposed_by: "maker-1",
        proposed_at: "2026-07-20T09:00:00+09:00",
      },
    ],
    requiredClaimSets: [
      {
        legalRequiredClaimSetId: "claim-set-1",
        claimSetKey: "approval-gate",
        claimSetVersion: 3,
        reviewScope: "approval_gate",
        status: "approved",
        claimSetSha256: "a".repeat(64),
        createdBy: "maker-1",
        createdAt: "2026-07-20T09:00:00+09:00",
        approvedBy: "checker-1",
        approvedAt: "2026-07-20T10:00:00+09:00",
        claims: [
          {
            claimKey: "permit.zoning",
            claimTextSha256: "b".repeat(64),
            description: "용도지역 적용성",
            ordinal: 1,
          },
        ],
      },
    ],
  });

  expect(workspace.projectId).toBe("project-1");
  expect(workspace.matters[0]).toMatchObject({
    matterId: "matter-1",
    matterVersion: 2,
    matterType: "permit",
    status: "under_review",
    severity: "high",
  });
  expect(workspace.waivers[0]?.conditions[0]).toEqual({
    code: "CURE-1",
    description: "공식 증빙 첨부",
    dueAt: undefined,
    requiredEvidenceType: "permit_document",
  });
  expect(workspace.tenantTermAliases[0]).toMatchObject({
    aliasId: "alias-1",
    termId: "term-1",
    aliasText: "브릿지론",
    status: "candidate",
  });
  expect(workspace.requiredClaimSets[0]).toMatchObject({
    claimSetId: "claim-set-1",
    claimSetKey: "approval-gate",
    claimSetVersion: 3,
    reviewScope: "approval_gate",
    status: "approved",
    approvedBy: "checker-1",
  });
  expect(workspace.requiredClaimSets[0]?.claims[0]).toEqual({
    claimKey: "permit.zoning",
    claimTextSha256: "b".repeat(64),
    description: "용도지역 적용성",
    ordinal: 1,
  });
});

test("uses maker-checker required-claim endpoints without caller identity fields", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    requests.push({ url: String(input), init });
    return new Response(
      JSON.stringify({
        schemaVersion: "realytics-legal-governance-write-v1",
        legalRequiredClaimSetId: "claim-set-1",
        appendOnly: true,
      }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    await proposeLegalRequiredClaimSet("project-1", {
      claimSetKey: "approval-gate",
      claimSetVersion: 1,
      reviewScope: "approval_gate",
      claims: [
        {
          claimKey: "permit.zoning",
          claimTextSha256: "a".repeat(64),
        },
      ],
    });
    await approveLegalRequiredClaimSet("project-1", "claim-set-1");
  } finally {
    globalThis.fetch = originalFetch;
  }

  expect(requests[0]?.url).toContain(
    "/projects/project-1/legal/required-claim-sets",
  );
  expect(JSON.parse(String(requests[0]?.init?.body))).toEqual({
    claimSetKey: "approval-gate",
    claimSetVersion: 1,
    reviewScope: "approval_gate",
    claims: [
      {
        claimKey: "permit.zoning",
        claimTextSha256: "a".repeat(64),
      },
    ],
  });
  expect(requests[1]?.url).toContain(
    "/required-claim-sets/claim-set-1/approve",
  );
  expect(JSON.parse(String(requests[1]?.init?.body))).toEqual({});
});

test("posts tenant alias review decisions without caller identity fields", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    requests.push({ url: String(input), init });
    return new Response(
      JSON.stringify({
        schemaVersion: "realytics-legal-term-alias-review-v1",
        tenantLegalTermAliasId: "alias-successor-1",
        reviewEventId: "alias-review-event-1",
        status: "candidate",
        appendOnly: true,
      }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    await reviewTenantLegalTermAlias("project/1", "alias/1", {
      reviewStatus: "needs_revision",
      reason: "표준 정의와 범위를 맞춰 주세요.",
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  expect(requests[0]?.url).toContain(
    "/projects/project%2F1/legal/term-aliases/alias%2F1/review",
  );
  expect(JSON.parse(String(requests[0]?.init?.body))).toEqual({
    reviewStatus: "needs_revision",
    reason: "표준 정의와 범위를 맞춰 주세요.",
  });
});

test("keeps AI analysis and counsel opinion as separate metadata-only records", () => {
  const workspace = normalizeLegalGovernanceWorkspace({
    project_id: "project-2",
    matters: [],
    waivers: [],
    counsel_opinions: [
      {
        counsel_opinion_id: "opinion-1",
        project_legal_matter_id: "matter-1",
        legal_evidence_snapshot_id: "snapshot-1",
        opinion_version: 1,
        status: "issued",
        protected_object_present: true,
        content_sha256: "a".repeat(64),
        privileged: true,
        authored_by: "external-counsel",
        authored_at: "2026-07-20T09:00:00Z",
      },
    ],
    analysis_versions: [
      {
        legal_analysis_version_id: "analysis-1",
        project_legal_matter_id: "matter-1",
        legal_evidence_snapshot_id: "snapshot-1",
        analysis_kind: "ai_summary",
        analysis_version: 1,
        protected_object_present: true,
        content_sha256: "b".repeat(64),
        input_snapshot_sha256: "c".repeat(64),
        model_id: "internal-model",
        analysis_only: true,
        report_eligible: false,
        human_review_status: "pending",
        created_by: "reviewer-1",
        created_at: "2026-07-20T09:00:00Z",
      },
    ],
    annotations: [],
  });

  const opinion = workspace.counselOpinions[0]!;
  const analysis = workspace.analysisVersions[0]!;

  expect(opinion).toMatchObject({
    opinionId: "opinion-1",
    privileged: true,
    protectedObjectPresent: true,
    status: "issued",
  });
  expect("objectLocator" in opinion).toBe(false);
  expect(analysis).toMatchObject({
    analysisVersionId: "analysis-1",
    analysisKind: "ai_summary",
    analysisOnly: true,
    reportEligible: false,
    humanReviewStatus: "pending",
    protectedObjectPresent: true,
  });
  expect("objectLocator" in analysis).toBe(false);
});

test("normalizes exact corpus locator separately from internal record identity", () => {
  const workspace = normalizeLegalGovernanceWorkspace({
    projectId: "project-3",
    matters: [],
    waivers: [],
    counselOpinions: [],
    analysisVersions: [],
    annotations: [
      {
        project_legal_annotation_id: "annotation-1",
        project_legal_matter_id: "matter-1",
        legal_resource_id: "statute-1",
        legal_resource_version_id: "statute-1:2026-01-01",
        legal_provision_id: "article-12",
        exact_locator: "국가법령정보센터 > 도시개발법 > 제12조 제1항",
        annotation_text_sha256: "d".repeat(64),
        annotation_type: "cross_reference",
        status: "active",
        created_by: "reviewer-1",
        created_at: "2026-07-20T09:00:00Z",
      },
    ],
  });

  expect(workspace.annotations[0]).toMatchObject({
    annotationId: "annotation-1",
    legalResourceId: "statute-1",
    legalResourceVersionId: "statute-1:2026-01-01",
    legalProvisionId: "article-12",
    exactLocator: "국가법령정보센터 > 도시개발법 > 제12조 제1항",
  });
});
