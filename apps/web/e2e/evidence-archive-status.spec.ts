import { expect, test, type Page } from "@playwright/test";

const projectId = "evidence-archive-sample";
const hashes = ["a", "b", "c", "d"].map((value) => value.repeat(64));

async function mockEvidence(page: Page, options: { recordsFail?: boolean; originalFail?: boolean; graphFail?: boolean; empty?: boolean } = {}) {
  await page.route("**/api/backend/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: {
      authenticated: true,
      user: { userId: "review-user", tenantId: "review-tenant", role: "analyst", status: "active" },
      permissions: ["project:read", "project:write", "project:analyze"],
    } });
    if (path.endsWith("/workspace/context")) return route.fulfill({ json: { projectId, projectMemory: { projectName: "합성 근거 자료" } } });
    if (path.endsWith("/workspace/document-review")) {
      if (options.originalFail) return route.fulfill({ status: 500, json: { detail: "unavailable" } });
      return route.fulfill({ json: {
        schemaVersion: "realytics-document-review-workspace-v1",
        viewModelVersion: "v1",
        projectId,
        snapshotId: "snapshot-sample",
        snapshotHash: "f".repeat(64),
        observedAt: "2026-11-05T00:00:00Z",
        sourceDocuments: options.empty ? [] : hashes.map((archiveId, index) => ({
          archiveId, originalFileName: "원본 " + (index + 1) + ".pdf",
          downloadUrl: "https://other-project.example/should-not-follow",
        })),
        runs: [],
        packages: options.empty ? [] : [
          ...hashes.map((archiveId, index) => ({
            documentVersion: { archiveId, documentVersionId: "version-" + index },
            parsingRun: { parsingRunId: "run-" + index, executionStatus: "completed", requestedAt: "2026-11-05T00:00:00Z" },
          })),
          {
            documentVersion: { archiveId: hashes[0], documentVersionId: "version-0" },
            parsingRun: { parsingRunId: "old-failure", executionStatus: "failed", requestedAt: "2026-11-04T00:00:00Z" },
          },
        ],
      } });
    }
    if (path.endsWith("/deal-state-records")) {
      const recordType = url.searchParams.get("record_type");
      if (options.recordsFail && recordType === "evidence") return route.fulfill({ status: 500, json: { detail: "unavailable" } });
      return route.fulfill({ json: {
        projectId, recordType, records: options.recordsFail && recordType === "metric"
          ? [{ recordId: "metric-sample", recordType: "metric", key: "확인된 매출", value: 123, unit: "원", sourceLocator: { cell: "A1" } }]
          : [],
      } });
    }
    if (path.endsWith("/evidence-graphs")) {
      if (options.graphFail) return route.fulfill({ status: 500, json: { detail: "unavailable" } });
      return route.fulfill({ json: { projectId, records: [] } });
    }
    return route.fulfill({ json: {} });
  });
}

test("evidence shows archived originals and latest structure runs independently of formal facts", async ({ page }) => {
  await mockEvidence(page);
  await page.goto("/project/" + projectId + "/evidence?track=pf", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("보관 문서 4건 · 구조 파싱 완료 4건")).toBeVisible();
  await expect(page.getByText("구조 파싱 완료", { exact: true })).toHaveCount(4);
  await expect(page.getByRole("link", { name: "원본 1.pdf 원본 검토" })).toHaveAttribute(
    "href", "/project/" + projectId + "/documents/review?track=pf&archiveId=" + hashes[0],
  );
  await expect(page.getByRole("region", { name: "정식 근거 집계" })).toContainText("0건");
  await expect(page.getByText("저장된 근거 그래프가 없습니다.", { exact: true })).toBeVisible();
  await expect(page.getByText(/조건에 맞는 정식 근거 기록이 없습니다/)).toBeVisible();
  await expect(page.getByRole("link", { name: "AI 딜룸으로 이동" })).toHaveAttribute("href", "/project/" + projectId + "/chief-orchestration?track=pf");
});

test("partial record failure preserves originals and successful rows without showing false zero", async ({ page }) => {
  await mockEvidence(page, { recordsFail: true, graphFail: true });
  await page.goto("/project/" + projectId + "/evidence?track=pf", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("보관 문서 4건 · 구조 파싱 완료 4건")).toBeVisible();
  await expect(page.getByText(/일부 근거 기록을 불러오지 못했습니다/)).toBeVisible();
  await expect(page.getByText("확인된 매출", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "정식 근거 집계" })).toContainText("조회 실패");
  await expect(page.getByRole("region", { name: "정식 근거 집계" })).toContainText("일부 미확인");
  await expect(page.getByText(/근거 그래프 목록을 불러오지 못했습니다/)).toBeVisible();
  await expect(page.getByText("저장된 근거 그래프가 없습니다.", { exact: true })).toHaveCount(0);
});

test("original lookup failure and confirmed empty original archive have distinct messages", async ({ page }) => {
  await mockEvidence(page, { originalFail: true });
  await page.goto("/project/" + projectId + "/evidence", { waitUntil: "domcontentloaded" });
  await expect(page.getByText(/보관 문서와 파싱 상태를 불러오지 못했습니다/)).toBeVisible();
  await expect(page.getByText("이 프로젝트에 보관된 문서가 없습니다.", { exact: true })).toHaveCount(0);
  await page.unroute("**/api/backend/**");
  await mockEvidence(page, { empty: true });
  await page.getByRole("button", { name: "근거 새로고침", exact: true }).click();
  await expect(page.getByText("이 프로젝트에 보관된 문서가 없습니다.", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "자료 올리기", exact: true })).toHaveAttribute("href", "/project/" + projectId + "/input-package#intake-upload");
});