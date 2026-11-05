import { expect, test, type Page } from "@playwright/test";
import type { RecentWorkItem } from "../lib/project-client";

function workItem(id: string, overrides: Partial<RecentWorkItem> = {}): RecentWorkItem {
  return {
    projectId: id, projectName: `프로젝트 ${id}`, lastWorkedAt: "2026-11-04T02:00:00Z",
    latestRunId: `run-${id}`, latestRunStatus: "completed", sourceDocumentCount: 2,
    openReviewCount: 0, riskCount: 0, approvalStatus: "not_requested",
    approvalReadinessStatus: "not_started", nextAction: "continue_next_review_step", ...overrides,
  };
}

async function mockDashboard(page: Page, items: RecentWorkItem[], options: { recentStatus?: number; projectsStatus?: number; total?: number } = {}) {
  const state = { recentStatus: options.recentStatus ?? 200, projectsStatus: options.projectsStatus ?? 200, writes: [] as string[] };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/\/$/, "");
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (request.method() !== "GET") state.writes.push(path);
    if (path.endsWith("/auth/me")) return reply({ authenticated: true,
      user: { userId: "dashboard-user", tenantId: "tenant-dashboard", role: "analyst", displayName: "대시보드 검토자" },
      session: { userId: "dashboard-user", tenantId: "tenant-dashboard" }, permissions: ["project:read", "project:write"], authSource: "bearer_token" });
    if (path.endsWith("/projects/recent-work")) return reply(state.recentStatus === 200
      ? { schemaVersion: "realytics-recent-work-v2", count: items.length, items }
      : { detail: { error: "recent_activity_authority_unavailable" } }, state.recentStatus);
    if (path.endsWith("/projects")) return reply(state.projectsStatus === 200
      ? Array.from({ length: options.total ?? items.length }, (_, index) => ({ id: `visible-${index}`, name: `전체 프로젝트 ${index + 1}` }))
      : { detail: { error: "project_authority_unavailable" } }, state.projectsStatus);
    return reply({ detail: { error: "not_mocked" } }, 404);
  });
  await page.goto("/dashboard");
  await expect(page.getByTestId("dashboard-work-inbox")).toBeVisible();
  return state;
}

const summary = (page: Page, name: string) => page.getByTestId("dashboard-work-inbox").getByRole("group", { name, exact: true });
const projects = (page: Page) => page.getByRole("list", { name: "최근 프로젝트", exact: true });
const projectRow = (page: Page, name: string) => projects(page).getByRole("listitem", { name: `${name} 작업`, exact: true });

test("failed recent work remains unknown instead of green zero and recovers only on refresh", async ({ page }) => {
  const item = workItem("recovered", { latestRunStatus: "running" });
  const state = await mockDashboard(page, [item], { recentStatus: 503, total: 7 });
  await expect(summary(page, "전체 프로젝트")).toContainText("7개");
  for (const label of ["확인 필요", "처리 중"]) {
    await expect(summary(page, label)).toContainText("—");
    await expect(summary(page, label)).toContainText("최근 작업 확인 불가");
    await expect(summary(page, label)).not.toContainText("0개");
    await expect(summary(page, label).getByRole("button")).toBeDisabled();
  }
  await expect(projects(page)).toHaveCount(0);
  await expect(page.getByText("최근 작업을 불러오지 못했습니다.", { exact: true })).toBeVisible();
  state.recentStatus = 200;
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await expect(projectRow(page, "프로젝트 recovered")).toBeVisible();
  await expect(summary(page, "처리 중")).toContainText("1개");
  expect(state.writes).toEqual([]);
});

test("a failed total does not hide usable recent work or label it as the full workspace", async ({ page }) => {
  await mockDashboard(page, [workItem("available")], { projectsStatus: 503, total: 35 });
  await expect(summary(page, "전체 프로젝트")).toContainText("—");
  await expect(summary(page, "확인 필요")).toContainText("0개");
  await expect(summary(page, "확인 필요")).toContainText("최근 작업 1개 중");
  await expect(page.getByText(/최근 작업 1개 기준.*전체 현황과 다를 수 있습니다/)).toBeVisible();
  await expect(projectRow(page, "프로젝트 available").getByRole("link", { name: "프로젝트 available 검토 이어가기", exact: true })).toHaveAttribute("href", "/project/available/chief-orchestration");
});

test("scope stays at the loaded twenty while filters distinguish processing from action and show six recent rows", async ({ page }) => {
  const items = Array.from({ length: 20 }, (_, index) => workItem(String(index + 1), { lastWorkedAt: new Date(Date.UTC(2026, 10, 4, 12, 59 - index)).toISOString() }));
  items[0]!.latestRunStatus = "running";
  items[1]!.latestRunStatus = "queued";
  items[2]!.latestRunStatus = "failed";
  items[3]!.latestRunStatus = "needs_recalculation";
  items[4]!.approvalStatus = "rejected";
  await mockDashboard(page, items, { total: 35 });
  await expect(summary(page, "전체 프로젝트")).toContainText("35개");
  await expect(summary(page, "확인 필요")).toContainText("3개");
  await expect(summary(page, "처리 중")).toContainText("2개");
  await expect(summary(page, "확인 필요")).toContainText("최근 작업 20개 중");
  await expect(projects(page).getByRole("listitem")).toHaveCount(6);
  await expect(projects(page).getByRole("listitem").first()).toHaveAccessibleName("프로젝트 1 작업");
  await page.getByRole("button", { name: "불러온 20개 모두 보기", exact: true }).click();
  await expect(projects(page).getByRole("listitem")).toHaveCount(20);
  await page.getByRole("group", { name: "최근 작업 필터" }).getByRole("button", { name: /^처리 중/ }).click();
  await expect(projects(page).getByRole("listitem")).toHaveCount(2);
  await expect(projectRow(page, "프로젝트 1")).toBeVisible();
  await expect(projectRow(page, "프로젝트 2")).toBeVisible();
  await page.getByRole("group", { name: "최근 작업 필터" }).getByRole("button", { name: /^확인 필요/ }).click();
  await expect(projects(page).getByRole("listitem")).toHaveCount(3);
  await expect(projectRow(page, "프로젝트 1")).toHaveCount(0);
  await expect(projectRow(page, "프로젝트 3")).toContainText("최근 실행 실패");
  await expect(projectRow(page, "프로젝트 5")).toContainText("승인 반려");
});

test("normal rows have no fabricated blocking reason and keyboard detail keeps approval separate", async ({ page }) => {
  await mockDashboard(page, [workItem("normal", { projectName: "부동산 개발 자료를 다시 확인하는 긴 이름의 프로젝트", approvalStatus: "approved", approvalReadinessStatus: "ready_for_approval" })]);
  const row = projectRow(page, "부동산 개발 자료를 다시 확인하는 긴 이름의 프로젝트");
  await expect(row).toContainText("등록된 확인 항목 없음");
  await expect(row).not.toContainText("차단 사유 상세 미제공");
  await expect(row.getByText("정식 승인", { exact: true })).toBeHidden();
  const toggle = row.getByRole("button");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(row.getByText("정식 승인", { exact: true })).toBeVisible();
  await expect(row.getByText("원본 문서", { exact: true })).toBeVisible();
  await expect(row.getByText("분석 실행 상태와 정식 승인은 별도로 확인합니다.", { exact: true })).toBeVisible();
  await page.keyboard.press("Space");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(toggle).toBeFocused();
  await expect(row.getByText("정식 승인", { exact: true })).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("severe execution blockers and rejection stay visible without expanding details", async ({ page }) => {
  await mockDashboard(page, [workItem("blocked", { latestRunStatus: "completed_with_blockers", approvalStatus: "rejected", approvalReadinessStatus: "hold", openReviewCount: 3 })]);
  const row = projectRow(page, "프로젝트 blocked");
  for (const text of ["실행 차단 항목 확인 필요", "승인 반려", "승인 보류"]) await expect(row.getByText(text, { exact: true }).first()).toBeVisible();
  await expect(row.getByRole("button", { name: /상세 보기$/ })).toHaveAttribute("aria-expanded", "false");
});

test("resume honors saved in-project routes and rejects external or another-project destinations", async ({ page }) => {
  const items = [
    workItem("saved", { href: "/project/saved/feasibility?run=saved-run" }),
    workItem("external", { href: "//external.invalid/steal" }),
    workItem("other", { href: "/project/somebody-else/input-package" }),
    workItem("encoded", { href: "/project/encoded/%2e%2e/somebody-else/input-package" }),
  ];
  await mockDashboard(page, items);
  await expect(projectRow(page, "프로젝트 saved").getByRole("link", { name: "프로젝트 saved 검토 이어가기", exact: true })).toHaveAttribute("href", "/project/saved/feasibility?run=saved-run");
  for (const id of ["external", "other", "encoded"]) await expect(projectRow(page, `프로젝트 ${id}`).getByRole("link", { name: `프로젝트 ${id} 검토 이어가기`, exact: true })).toHaveAttribute("href", `/project/${id}/chief-orchestration`);
});

test("unknown run state needs confirmation while a stored risk count alone does not imply unresolved work", async ({ page }) => {
  await mockDashboard(page, [
    workItem("unknown", { latestRunStatus: "future_status" }),
    workItem("unrecorded", { latestRunStatus: undefined }),
    workItem("new", { latestRunId: undefined, latestRunStatus: "not_started", nextAction: "run_initial_analysis" }),
    workItem("risk-history", { riskCount: 8 }),
  ]);
  await expect(summary(page, "확인 필요")).toContainText("2개");
  await expect(projectRow(page, "프로젝트 unknown")).toContainText("최근 실행 상태 확인 필요");
  await expect(projectRow(page, "프로젝트 unrecorded")).toContainText("최근 실행 상태 확인 필요");
  await expect(projectRow(page, "프로젝트 new")).toContainText("미실행");
  await expect(projectRow(page, "프로젝트 new").getByRole("link", { name: /분석 시작/ })).toBeVisible();
  await page.getByRole("group", { name: "최근 작업 필터" }).getByRole("button", { name: /^확인 필요/ }).click();
  await expect(projectRow(page, "프로젝트 new")).toHaveCount(0);
  await expect(projectRow(page, "프로젝트 risk-history")).toHaveCount(0);
});


test("summary cards filter the loaded work and open the full project list with keyboard support", async ({ page }) => {
  const state = await mockDashboard(page, [
    workItem("normal"), workItem("attention", { latestRunStatus: "failed" }), workItem("processing", { latestRunStatus: "running" }),
  ], { total: 35 });
  const attention = summary(page, "확인 필요").getByRole("button", { name: "확인이 필요한 최근 작업 보기" });
  await attention.click();
  await expect(attention).toHaveAttribute("aria-pressed", "true");
  await expect(projects(page).getByRole("listitem")).toHaveCount(1);
  await expect(projectRow(page, "프로젝트 attention")).toBeVisible();
  await expect(page.getByRole("group", { name: "최근 작업 필터" }).getByRole("button", { name: /^확인 필요/ })).toHaveAttribute("aria-pressed", "true");

  const processing = summary(page, "처리 중").getByRole("button", { name: "처리 중인 최근 작업 보기" });
  await processing.focus();
  await page.keyboard.press("Enter");
  await expect(processing).toHaveAttribute("aria-pressed", "true");
  await expect(attention).toHaveAttribute("aria-pressed", "false");
  await expect(projects(page).getByRole("listitem")).toHaveCount(1);
  await expect(projectRow(page, "프로젝트 processing")).toBeVisible();

  const allProjects = page.locator("#all-projects");
  const allLink = summary(page, "전체 프로젝트").getByRole("link", { name: "전체 프로젝트 보기" });
  await allLink.focus();
  await page.keyboard.press("Enter");
  await expect(allProjects).toHaveAttribute("open", "");
  await expect(allProjects.getByRole("link", { name: "프로젝트 visible-0", exact: true })).toHaveAttribute("href", "/project/visible-0");
  await allProjects.locator("summary").click();
  await expect(allProjects).not.toHaveAttribute("open", "");
  await allLink.click();
  await expect(allProjects).toHaveAttribute("open", "");
  expect(state.writes).toEqual([]);
});
