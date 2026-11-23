import { expect, test, type Page } from "@playwright/test";

const projectId = "archive-extraction-sample";
const ids = ["a", "b", "c"].map((value) => value.repeat(64));

async function mockArchive(page: Page, canAnalyze = true, view: "evidence" | "documents/review" = "evidence") {
  const requests: Array<Record<string, unknown>> = [];
  let active = 0;
  let maxActive = 0;
  let bAttempts = 0;
  let reviewReads = 0;
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: {
      authenticated: true, user: { userId: "analyst", tenantId: "tenant-a", role: canAnalyze ? "analyst" : "viewer" },
      permissions: ["project:read", "evidence:read", ...(canAnalyze ? ["project:analyze"] : [])],
    } });
    if (path.endsWith("/workspace/context")) return route.fulfill({ json: { projectId, projectMemory: { projectName: "합성 원본 추출" } } });
    if (path.endsWith("/workspace/document-review")) {
      reviewReads += 1;
      return route.fulfill({ json: {
      schemaVersion: "realytics-document-review-workspace-v1", projectId, snapshotId: "review-snapshot",
      snapshotHash: "f".repeat(64), viewModelVersion: "v1", observedAt: "2026-11-05T00:00:00Z",
      sourceDocuments: ids.map((archiveId, index) => ({ archiveId, originalFileName: "원본-" + index + (index === 0 ? ".pdf" : ".xlsx") })),
      packages: [], runs: [], reviewQueue: [], snapshotDiffs: [], glossaryCandidates: [], warnings: [],
    } });
    }
    if (path.endsWith("/deal-state-records")) return route.fulfill({ json: { projectId, records: [] } });
    if (path.endsWith("/evidence-graphs")) return route.fulfill({ json: { projectId, records: [] } });
    if (path.endsWith("/parsing-v2/runs")) {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      requests.push(body);
      active += 1;
      maxActive = Math.max(maxActive, active);
      let response: Record<string, unknown> = {
        candidateOnly: true, authoritative: false,
        parsingRun: { projectId, parsingRunId: "completed-run", executionStatus: "completed" },
        candidateCollection: { candidateCount: body.archiveId === ids[2] ? 0 : 2 },
      };
      let status = 201;
      if (body.archiveId === ids[1]) {
        bAttempts += 1;
        if (bAttempts === 1) {
          status = 503;
          response = { detail: "network or storage unavailable" };
        } else if (bAttempts === 2) {
          response = {
            candidateOnly: true, authoritative: false,
            parsingRun: { projectId, parsingRunId: "known-failed-run", executionStatus: "failed" },
          };
        }
      }
      await route.fulfill({ status, json: response });
      active -= 1;
      return;
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/project/" + projectId + "/" + view + "?track=pf", { waitUntil: "domcontentloaded" });
  return { requests, maxActive: () => maxActive, reviewReads: () => reviewReads };
}

for (const view of ["evidence", "documents/review"] as const) {
test(view + " runs archived originals serially and retries known terminal failures with a new key", async ({ page }) => {
  const state = await mockArchive(page, true, view);
  const action = page.getByRole("region", { name: "보관 원본값 추출" });
  expect(state.requests).toEqual([]);
  if (view === "documents/review") {
    await expect(action.getByRole("button", { name: "보관 원본값 추출", exact: true })).toBeVisible();
    await expect(action.getByRole("status")).toContainText("추출 대상 3개");
    await expect(action.getByRole("list")).toBeHidden();
    await action.locator("summary", { hasText: "문서별 추출 상태 보기" }).click();
  }
  const initialReads = state.reviewReads();
  await action.getByRole("button", { name: "보관 원본값 추출", exact: true }).click();
  await expect(action.getByText(/검토 후보 2건/)).toHaveCount(1);
  await expect(action.getByText(/지원하는 명시적 항목을 찾지 못했습니다/)).toHaveCount(1);
  await expect(action.getByRole("button", { name: "실패한 문서 다시 확인", exact: true })).toBeEnabled();
  expect(state.requests.map((item) => item.archiveId)).toEqual(ids);
  expect(state.maxActive()).toBe(1);
  await expect.poll(state.reviewReads).toBeGreaterThan(initialReads);
  for (const item of state.requests) expect(item).toEqual({
    archiveId: item.archiveId,
    profileId: "realytics.source_review_candidates",
    profileVersion: "1.1.0",
    idempotencyKey: "source-review-v1-1-0-" + item.archiveId,
  });
  await action.getByRole("button", { name: "실패한 문서 다시 확인", exact: true }).click();
  await expect(action.getByText(/원본값 추출이 중단됐습니다/)).toBeVisible();
  expect(state.requests).toHaveLength(4);
  expect(state.requests[3]).toEqual(state.requests[1]);
  await action.getByRole("button", { name: "실패한 문서 다시 확인", exact: true }).click();
  await expect(action.getByText(/검토 후보 2건/)).toHaveCount(2);
  expect(state.requests).toHaveLength(5);
  expect(state.requests[4]!.archiveId).toBe(ids[1]);
  expect(state.requests[4]!.idempotencyKey).not.toBe(state.requests[1]!.idempotencyKey);
  await expect(action.getByRole("button", { name: "값이 없는 문서 다시 추출", exact: true })).toBeEnabled();
});

test(view + " read-only accounts cannot start archived candidate extraction", async ({ page }) => {
  const state = await mockArchive(page, false, view);
  const action = page.getByRole("region", { name: "보관 원본값 추출" });
  await expect(action.getByText("원본값 추출에는 프로젝트 분석 권한이 필요합니다.")).toBeVisible();
  await expect(action.getByRole("button", { name: "보관 원본값 추출", exact: true })).toHaveCount(0);
  await expect(action.getByRole("button", { name: "실패한 문서 다시 확인", exact: true })).toHaveCount(0);
  expect(state.requests).toHaveLength(0);
});
}
