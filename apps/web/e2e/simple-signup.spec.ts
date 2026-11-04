import { expect, test, type Page } from "@playwright/test";

const workspaceId = `personal-${"a".repeat(64)}`;

async function mockSimpleSignup(page: Page, signedIn = true) {
  const pendingRequest = { requestId: "pending-existing", requestType: "existing_organization", organizationId: "future-team",
    organizationName: "후순위 기관 신청", requestedRole: "viewer", status: "pending", requestVersion: 2,
    businessRegistrationNumber: null, message: null, decisionReason: null, submittedAt: "2026-11-03T00:00:00Z", decisionAt: null, updatedAt: "2026-11-03T00:00:00Z" };
  const state = {
    signedIn, selected: false, enabled: true, capabilityStatus: 200, prepareStatus: 200, tenantStatus: 200,
    invalidResponse: false, authReads: 0, onboardingReads: 0, projectReads: 0,
    prepareBodies: [] as unknown[], prepareHeaders: [] as Record<string, string>[], tenantBodies: [] as unknown[],
    otherWrites: [] as string[], signupBody: null as Record<string, unknown> | null,
    prepareGate: Promise.resolve(), pendingRequest,
  };
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const reply = async (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/auth/me")) {
      state.authReads += 1;
      if (!state.signedIn) return reply({ authenticated: false });
      if (!state.selected) return reply({ detail: { error: "principal_not_registered" } }, 403);
      return reply({ authenticated: true, user: { userId: "signup-sub", tenantId: workspaceId, email: "signup@example.com", displayName: "가입 검증자", role: "analyst" },
        session: { userId: "signup-sub", tenantId: workspaceId }, permissions: ["project:read", "project:write"], authSource: "bearer_token" });
    }
    if (path.endsWith("/auth/workspaces/default")) {
      if (request.method() === "GET") {
        return state.capabilityStatus === 200
          ? reply({ schemaVersion: "realytics-default-workspace-capability-v1", enabled: state.enabled })
          : reply({ detail: { error: "identity_membership_authority_unavailable" } }, state.capabilityStatus);
      }
      state.prepareBodies.push(request.postDataJSON());
      state.prepareHeaders.push(request.headers());
      await state.prepareGate;
      if (state.prepareStatus !== 200) return reply({ detail: { error: "identity_membership_authority_unavailable" } }, state.prepareStatus);
      return reply({ schemaVersion: "realytics-default-workspace-v1", tenantId: state.invalidResponse ? "../foreign" : workspaceId,
        role: "analyst", created: state.prepareBodies.length === 1, next: state.invalidResponse ? "https://other.invalid" : "/dashboard" });
    }
    if (path.endsWith("/auth/onboarding/status")) {
      state.onboardingReads += 1;
      return reply({ accessState: "onboarding_required", profile: null, requests: [pendingRequest] });
    }
    if (request.method() !== "GET") state.otherWrites.push(path);
    if (path.endsWith("/projects/recent-work")) { state.projectReads += 1; return reply({ items: [] }); }
    if (path.endsWith("/projects")) { state.projectReads += 1; return reply([]); }
    return reply({ error: "not_mocked" }, 404);
  });
  await page.route("**/api/auth/supabase/security", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    userId: "signup-sub", identities: [{ identityId: "email-1", provider: "email", email: "signup@example.com" }], factors: [],
    assurance: { currentLevel: "aal1", nextLevel: "aal2", currentAuthenticationMethods: [] }, limitations: [],
  }) }));
  await page.route("**/api/auth/supabase/readiness", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    configured: true, settingsReachable: true, providers: { google: false, kakao: false, naver: false }, providerReady: { google: false, kakao: false, naver: false },
    providerUsable: { google: false, kakao: false, naver: false }, canonicalOriginReady: true, backend: { ready: true }, limitations: [],
  }) }));
  await page.route("**/api/auth/supabase/password", async (route) => {
    state.signupBody = route.request().postDataJSON();
    state.signedIn = true;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      authenticated: true, requiresEmailConfirmation: false, accessState: "onboarding_required", next: "/onboarding",
    }) });
  });
  await page.route("**/api/auth/supabase/tenant", async (route) => {
    state.tenantBodies.push(route.request().postDataJSON());
    if (state.tenantStatus !== 200) {
      await route.fulfill({ status: state.tenantStatus, contentType: "application/json", body: JSON.stringify({ error: "tenant_selection_not_authorized" }) });
      return;
    }
    state.selected = true;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ selected: true, tenantId: workspaceId }) });
  });
  return state;
}

test("email signup reaches the real dashboard with one server-chosen workspace and leaves pending requests alone", async ({ page }) => {
  const state = await mockSimpleSignup(page, false);
  let release!: () => void;
  state.prepareGate = new Promise<void>((resolve) => { release = resolve; });
  await page.goto("/login?mode=signup");
  await page.getByLabel("표시명").fill("가입 검증자");
  await page.getByLabel("이메일").fill("signup@example.com");
  await page.locator('input[name="password"]').fill("A-valid-signup-password-7!");
  await page.getByLabel("비밀번호 확인").fill("A-valid-signup-password-7!");
  await page.getByLabel(/서비스 이용약관/).check();
  await page.getByLabel(/개인정보 처리방침/).check();
  await page.getByRole("button", { name: "이메일 가입 신청" }).click();
  try {
    await expect(page.getByRole("heading", { name: "내 작업공간", exact: true })).toBeVisible();
    await expect.poll(() => state.prepareBodies.length).toBe(1);
    await expect(page.getByRole("heading", { name: "기관 워크스페이스 가입" })).toHaveCount(0);
    await expect(page.getByLabel("요청 역할")).toHaveCount(0);
    expect(state.tenantBodies).toEqual([]);
    expect(state.onboardingReads).toBe(0);
  } finally { release(); }
  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "대시보드", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "첫 프로젝트 시작", exact: true })).toBeVisible();
  await expect.poll(() => state.projectReads).toBeGreaterThan(0);
  expect(state.prepareBodies).toEqual([{}]);
  expect(state.prepareHeaders.every((headers) => !headers.authorization && headers["x-realytics-bff"] === "same-origin")).toBe(true);
  expect(state.tenantBodies).toEqual([{ tenantId: workspaceId }]);
  expect(state.otherWrites).toEqual([]);
  expect(state.pendingRequest).toMatchObject({ status: "pending", requestVersion: 2, requestedRole: "viewer" });
  expect(state.signupBody).toMatchObject({ mode: "signup", termsAccepted: true, privacyAccepted: true });
});

test("capability failure never becomes the organization form or a default-workspace write", async ({ page }) => {
  const state = await mockSimpleSignup(page);
  state.capabilityStatus = 503;
  await page.goto("/onboarding");
  await expect(page.getByTestId("default-workspace-status").getByRole("alert")).toContainText("내 작업공간 설정을 확인하지 못했습니다");
  expect(state.prepareBodies).toEqual([]);
  await expect(page.getByRole("heading", { name: "기관 워크스페이스 가입" })).toHaveCount(0);
  state.capabilityStatus = 200;
  await page.getByRole("button", { name: "다시 확인", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  expect(state.prepareBodies).toEqual([{}]);
});

test("a failed preparation only retries after a user action and duplicate clicks cannot repeat the POST", async ({ page }) => {
  const state = await mockSimpleSignup(page);
  state.prepareStatus = 503;
  await page.goto("/onboarding");
  await expect(page.getByTestId("default-workspace-status").getByRole("alert")).toContainText("내 작업공간을 준비하지 못했습니다");
  expect(state.prepareBodies).toEqual([{}]);
  const authReads = state.authReads;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("realytics-auth-session-changed")));
  await expect.poll(() => state.authReads).toBeGreaterThan(authReads);
  expect(state.prepareBodies).toHaveLength(1);

  let release!: () => void;
  state.prepareGate = new Promise<void>((resolve) => { release = resolve; });
  state.prepareStatus = 200;
  try {
    await page.getByRole("button", { name: "다시 시도", exact: true }).evaluate((button) => {
      (button as HTMLButtonElement).click();
      (button as HTMLButtonElement).click();
    });
    await expect.poll(() => state.prepareBodies.length).toBe(2);
    await expect(page.getByTestId("default-workspace-status").getByRole("status")).toContainText("내 작업공간을 준비하고 있습니다");
    expect(state.tenantBodies).toEqual([]);
  } finally { release(); }
  await expect(page).toHaveURL(/\/dashboard$/);
  expect(state.prepareBodies).toEqual([{}, {}]);
});

test("Tenant verification failure cannot open the dashboard or create a second workspace on explicit retry", async ({ page }) => {
  const state = await mockSimpleSignup(page);
  state.tenantStatus = 403;
  await page.goto("/onboarding");
  await expect(page.getByTestId("default-workspace-status").getByRole("alert")).toContainText("내 작업공간을 준비하지 못했습니다");
  await expect(page).toHaveURL(/\/onboarding$/);
  expect(state.prepareBodies).toEqual([{}]);
  expect(state.selected).toBe(false);
  state.tenantStatus = 200;
  await page.getByRole("button", { name: "다시 시도", exact: true }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
  expect(state.prepareBodies).toEqual([{}]);
  expect(state.tenantBodies).toEqual([{ tenantId: workspaceId }, { tenantId: workspaceId }]);
});

test("an invalid workspace response cannot select a tenant or redirect to an untrusted destination", async ({ page }) => {
  const state = await mockSimpleSignup(page);
  state.invalidResponse = true;
  await page.goto("/onboarding");
  await expect(page.getByTestId("default-workspace-status").getByRole("alert")).toContainText("내 작업공간을 준비하지 못했습니다");
  await expect(page).toHaveURL(/\/onboarding$/);
  expect(state.prepareBodies).toEqual([{}]);
  expect(state.tenantBodies).toEqual([]);
  expect(state.selected).toBe(false);
});

test("an explicitly disabled capability preserves the pending organization flow without a default-workspace POST", async ({ page }) => {
  const state = await mockSimpleSignup(page);
  state.enabled = false;
  await page.goto("/onboarding");
  await expect(page.getByRole("heading", { name: "기관 워크스페이스 가입" })).toBeVisible();
  await expect(page.getByTestId("onboarding-request-status")).toContainText("후순위 기관 신청");
  expect(state.prepareBodies).toEqual([]);
  expect(state.tenantBodies).toEqual([]);
  expect(state.pendingRequest.status).toBe("pending");
});
