import { expect, test, type Page } from "@playwright/test";
import {
  backendErrorMessage,
  parseBackendErrorResponse,
} from "../lib/backend-error";
import { normalizeRecentWorkResponse } from "../lib/project-client";

async function mockAuthenticatedStart(
  page: Page,
  recentWork: { status?: number; body: unknown },
  options: {
    authenticated?: () => boolean;
    authSource?: string;
    role?: string;
    permissions?: string[];
  } = {},
) {
  await page.route("**/api/backend/auth/me", async (route) => {
    if (options.authenticated && !options.authenticated()) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          authenticated: false,
          user: { userId: "anonymous", role: "anonymous" },
          permissions: [],
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: true,
        user: {
          userId: "recent-work-user",
          tenantId: "tenant-pilot",
          email: "reviewer@example.com",
          displayName: "최근 작업 검토자",
          role: options.role ?? "analyst",
          status: "active",
        },
        session: { userId: "recent-work-user", tenantId: "tenant-pilot" },
        permissions: options.permissions ?? ["project:read", "project:write"],
        authSource: options.authSource ?? "bearer_token",
      }),
    });
  });
  await page.route("**/api/backend/projects/recent-work?*", async (route) => {
    await route.fulfill({
      status: recentWork.status ?? 200,
      contentType: "application/json",
      body: JSON.stringify(recentWork.body),
    });
  });
}

test("preserves structured FastAPI permission details for exact user guidance", async () => {
  const parsed = await parseBackendErrorResponse(
    new Response(
      JSON.stringify({
        detail: {
          error: "forbidden",
          requiredPermission: "project:write",
          role: "reviewer",
        },
      }),
      { status: 403, headers: { "Content-Type": "application/json" } },
    ),
  );

  expect(parsed).toEqual({
    status: 403,
    error: "forbidden",
    requiredPermission: "project:write",
    role: "reviewer",
    reason: null,
    action: null,
    message: null,
  });
  expect(backendErrorMessage(parsed, { action: "새 심사 공간 생성" })).toContain(
    "현재 역할 'reviewer'에는 'project:write' 권한이 없습니다.",
  );
});

test("account switch revokes the current session before opening an interactive login", async ({ page }) => {
  let signedOut = false;
  const logoutSteps: string[] = [];
  let signoutPayload: Record<string, unknown> | null = null;
  await mockAuthenticatedStart(
    page,
    { body: { schemaVersion: "realytics-recent-work-v3", count: 0, items: [] } },
    { authenticated: () => !signedOut, authSource: "bearer_token" },
  );
  await page.route("**/api/backend/auth/ai-credentials/current", async (route) => {
    logoutSteps.push("credentials");
    await route.fulfill({ status: 204, body: "" });
  });
  await page.route("**/api/auth/supabase/signout", async (route) => {
    logoutSteps.push("supabase-session");
    signoutPayload = route.request().postDataJSON() as Record<string, unknown>;
    signedOut = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ signedOut: true, scope: "local" }),
    });
  });

  await page.goto("/start");
  await page.getByTestId("switch-account-button").click();

  await expect(page).toHaveURL(/\/login\?next=\/start&switch=1$/);
  expect(signoutPayload).toEqual({ scope: "local" });
  expect(logoutSteps).toEqual([
    "credentials",
    "supabase-session",
  ]);
  await expect(page.getByText(/계정 전환을 위해/)).toBeVisible();
});

test("account switch stays on the current page when Supabase revocation fails", async ({ page }) => {
  await mockAuthenticatedStart(
    page,
    { body: { schemaVersion: "realytics-recent-work-v3", count: 0, items: [] } },
    { authSource: "bearer_token" },
  );
  await page.route("**/api/backend/auth/ai-credentials/current", async (route) => {
    await route.fulfill({ status: 204, body: "" });
  });
  await page.route("**/api/auth/supabase/signout", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "session_revocation_failed" }),
    });
  });

  await page.goto("/start");
  await page.getByTestId("switch-account-button").click();

  await expect(page).toHaveURL(/\/start$/);
  await expect(page.getByTestId("switch-account-error")).toContainText(
    "현재 로그인 세션을 종료하지 못했습니다.",
  );
  await expect(page.getByTestId("switch-account-button")).toBeEnabled();
});

test("normalizes legacy v2 and extended project/review/analysis contracts", () => {
  const legacy = normalizeRecentWorkResponse({
    schemaVersion: "realytics-recent-work-v2",
    count: 1,
    items: [
      {
        projectId: "legacy-1",
        projectName: "기존 PF 프로젝트",
        lastWorkedAt: "2026-07-27T09:00:00Z",
        latestRunStatus: "completed",
        sourceDocumentCount: 3,
        openReviewCount: 1,
        riskCount: 2,
        approvalStatus: "pending",
        nextAction: "resume_open_reviews",
      },
    ],
  });
  expect(legacy.items[0]).toMatchObject({
    kind: "project",
    projectId: "legacy-1",
    executionStatus: "completed",
    href: "/project/legacy-1/chief-orchestration",
  });

  const extended = normalizeRecentWorkResponse({
    schema_version: "realytics-recent-work-v3",
    partial: true,
    warnings: ["analysis projection delayed"],
    projects: [
      {
        project_id: "deal-1",
        project_name: "성수 복합개발",
        updated_at: "2026-07-28T08:00:00Z",
        href: "https://attacker.invalid/steal",
      },
    ],
    reviews: [
      {
        id: "review-1",
        project: { id: "deal-1", name: "성수 복합개발" },
        title: "사업수지 근거 검토",
        approval_status: "pending",
        updated_at: "2026-07-28T09:00:00Z",
      },
    ],
    analyses: [
      {
        id: "analysis-1",
        project_id: "deal-1",
        project_name: "성수 복합개발",
        title: "PF Review 실행 결과",
        execution_status: "completed_with_warnings",
        updated_at: "2026-07-28T10:00:00Z",
        href: "/project/deal-1/feasibility",
      },
    ],
  });
  expect(extended.items.map((item) => item.kind)).toEqual(["analysis", "review", "project"]);
  expect(extended.items.find((item) => item.kind === "project")?.href).toBe("/project/deal-1");
  expect(extended.items.find((item) => item.kind === "review")?.href).toBe("/project/deal-1/documents/review");
  expect(extended.items.find((item) => item.kind === "analysis")?.href).toBe("/project/deal-1/feasibility");
  expect(extended.warnings).toEqual([
    "analysis projection delayed",
    "서버가 일부 최근 작업만 반환했습니다.",
  ]);
  expect(normalizeRecentWorkResponse({ projects: [], reviews: [], analyses: [] }).items).toEqual([]);
});

test("shows tenant recent projects, reviews and analyses with direct return links", async ({ page }) => {
  await mockAuthenticatedStart(page, {
    body: {
      schemaVersion: "realytics-recent-work-v3",
      totalCount: 3,
      items: [
        {
          itemId: "project:deal-1",
          kind: "project",
          projectId: "deal-1",
          projectName: "성수 복합개발",
          title: "성수 복합개발",
          summary: "원본 문서 8건과 미해결 검토 2건",
          updatedAt: "2026-07-28T08:00:00Z",
          executionStatus: "running",
          approvalStatus: "not_requested",
          openReviewCount: 2,
          href: "/project/deal-1",
          nextAction: "continue_next_review_step",
        },
        {
          itemId: "review:review-1",
          kind: "review",
          projectId: "deal-1",
          projectName: "성수 복합개발",
          title: "사업수지 근거 검토",
          summary: "약한 근거 2건을 확인해야 합니다.",
          updatedAt: "2026-07-28T09:00:00Z",
          approvalStatus: "pending",
          href: "/project/deal-1/documents/review",
          nextAction: "resume_open_reviews",
        },
        {
          itemId: "analysis:run-1",
          kind: "analysis",
          projectId: "deal-1",
          projectName: "성수 복합개발",
          title: "PF Review 실행 결과",
          summary: "Cash Flow 재계산 결과를 확인하세요.",
          updatedAt: "2026-07-28T10:00:00Z",
          executionStatus: "completed_with_warnings",
          approvalStatus: "conditional",
          href: "/project/deal-1/feasibility",
          nextAction: "inspect_latest_run",
        },
      ],
    },
  });

  await page.goto("/start");

  await expect(page.getByTestId("recent-work-panel")).toBeVisible();
  await expect(page.getByRole("heading", { name: "최근 프로젝트와 심사 이어가기" })).toBeVisible();
  await expect(page.getByTestId("recent-work-project")).toHaveCount(1);
  await expect(page.getByTestId("recent-work-review")).toHaveCount(1);
  await expect(page.getByTestId("recent-work-analysis")).toHaveCount(1);
  await expect(page.getByText("실행 · 경고 포함 완료")).toBeVisible();
  await expect(page.getByText("승인 · 조건부 승인")).toBeVisible();
  await expect(page.getByTestId("recent-work-analysis")).toHaveAttribute("href", "/project/deal-1/feasibility");
  await expect(page.getByRole("heading", { name: "새 자료로 분석 시작" })).toBeVisible();

  await page.getByRole("button", { name: /심사·검토/ }).click();
  await expect(page.getByTestId("recent-work-review")).toBeVisible();
  await expect(page.getByTestId("recent-work-project")).toHaveCount(0);
  await expect(page.getByTestId("recent-work-analysis")).toHaveCount(0);
});

test("distinguishes recent-work permission failure from a true empty history", async ({ page }) => {
  await mockAuthenticatedStart(page, {
    status: 403,
    body: { detail: "membership_required" },
  });
  await page.goto("/start");

  await expect(page.getByTestId("recent-work-error")).toContainText("저장된 최근 작업을 불러오지 못했습니다.");
  await expect(page.getByTestId("recent-work-error")).toContainText("Tenant 프로젝트 조회 권한");
  await expect(page.getByRole("button", { name: "다시 불러오기" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "새 자료로 분석 시작" })).toBeVisible();
});

test("shows an explicit empty state and keeps new analysis available", async ({ page }) => {
  await mockAuthenticatedStart(page, {
    body: {
      schemaVersion: "realytics-recent-work-v3",
      count: 0,
      items: [],
    },
  });
  await page.goto("/start");

  await expect(page.getByText("저장된 최근 작업이 없습니다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "새 분석 시작" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "새 자료로 분석 시작" })).toBeVisible();
});

test("keeps reviewer recent-work navigation readable and disables all start-page creation entry points", async ({ page }) => {
  await mockAuthenticatedStart(
    page,
    {
      body: {
        schemaVersion: "realytics-recent-work-v3",
        count: 1,
        items: [
          {
            itemId: "review:assigned-1",
            kind: "review",
            projectId: "deal-review",
            projectName: "배정된 PF 심사",
            title: "근거 및 승인조건 검토",
            summary: "미해결 검토 2건",
            updatedAt: "2026-07-29T09:00:00Z",
            approvalStatus: "pending",
            href: "/project/deal-review/documents/review",
            nextAction: "resume_open_reviews",
          },
        ],
      },
    },
    { role: "reviewer", permissions: ["project:read", "approval:write"] },
  );

  await page.goto("/start");

  await expect(page.getByTestId("review-only-notice")).toBeVisible();
  await expect(page.getByTestId("review-only-role")).toHaveText("reviewer");
  await expect(
    page.getByTestId("review-only-notice").getByText("필요 권한 · project:write"),
  ).toBeVisible();
  await expect(page.getByTestId("recent-work-review")).toHaveAttribute(
    "href",
    "/project/deal-review/documents/review",
  );
  await expect(page.getByTestId("new-analysis-dropzone")).toHaveAttribute(
    "aria-disabled",
    "true",
  );
  await expect(
    page.getByTestId("recent-work-panel").getByRole("button", { name: "심사 전용" }),
  ).toBeDisabled();
  await expect(page.locator('input[type="file"]')).toBeDisabled();
});

test("disables dashboard deal creation for reviewer while keeping assigned work visible", async ({ page }) => {
  await mockAuthenticatedStart(
    page,
    {
      body: {
        schemaVersion: "realytics-recent-work-v3",
        count: 1,
        items: [
          {
            itemId: "review:dashboard-1",
            kind: "review",
            projectId: "deal-dashboard",
            projectName: "대시보드 배정 심사",
            title: "승인조건 재검토",
            summary: "미해결 검토 1건",
            updatedAt: "2026-07-29T10:00:00Z",
            approvalStatus: "pending",
            sourceDocumentCount: 3,
            openReviewCount: 1,
            riskCount: 0,
            href: "/project/deal-dashboard/documents/review",
            nextAction: "resume_open_reviews",
          },
        ],
      },
    },
    { role: "reviewer", permissions: ["project:read", "approval:write"] },
  );
  await page.route("**/api/backend/projects", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ project: { id: "deal-dashboard", name: "대시보드 배정 심사" } }]),
    });
  });

  await page.goto("/dashboard");

  await expect(page.getByTestId("review-only-notice")).toBeVisible();
  await expect(page.getByTestId("dashboard-new-deal-disabled")).toBeDisabled();
  await expect(page.getByTestId("dashboard-intake-read-only")).toContainText(
    "새 프로젝트 생성과 자료 업로드는 사용할 수 없습니다",
  );
  await expect(
    page.locator('a[href="/project/deal-dashboard"]:visible'),
  ).toContainText("대시보드 배정 심사");
  await expect(page.locator('#new-deal-intake input[type="file"]')).toHaveCount(0);
});

test("shows exact missing project:write permission and allows the same file to be retried", async ({ page }) => {
  await mockAuthenticatedStart(page, {
    body: {
      schemaVersion: "realytics-recent-work-v3",
      count: 0,
      items: [],
    },
  });
  await page.route("**/api/backend/projects/intake/continuation-check", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ matches: [], shouldAsk: false }),
    });
  });
  let createAttempts = 0;
  await page.route("**/api/backend/projects/", async (route) => {
    createAttempts += 1;
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          error: "forbidden",
          requiredPermission: "project:write",
          role: "reviewer",
        },
      }),
    });
  });
  await page.goto("/start");

  const fileInput = page.locator('input[type="file"]');
  const file = {
    name: "pilot-plan.json",
    mimeType: "application/json",
    buffer: Buffer.from('{"project":"pilot"}'),
  };
  await fileInput.setInputFiles(file);

  await expect(page.getByText(/현재 역할 'reviewer'에는 'project:write' 권한이 없습니다/)).toBeVisible();
  await expect.poll(() => createAttempts).toBe(1);

  await fileInput.setInputFiles(file);
  await expect.poll(() => createAttempts).toBe(2);
});
