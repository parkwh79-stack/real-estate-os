import { expect, test } from "@playwright/test";

test("recovery session stays on the password reset form", async ({ page }) => {
  await page.route("**/api/backend/auth/me", async (route) => {
    await route.fulfill({ json: {
      authenticated: true,
      user: { userId: "recovery-user", role: "admin", status: "active", tenantId: "recovery-test" },
      session: { userId: "recovery-user", tenantId: "recovery-test" },
      permissions: ["project:read", "project:write"],
      authSource: "bearer_token",
    } });
  });
  let attempted = false;
  await page.route("**/api/auth/supabase/password/recovery", async (route) => {
    attempted = true;
    expect(route.request().postDataJSON().action).toBe("complete");
    await route.fulfill({ status: 401, json: { error: "password_recovery_context_required" } });
  });
  await page.goto("/login?mode=reset&flow=password_recovery");
  await page.locator('input[name="password"]').fill("Reset-only-fixture-7!");
  await page.getByLabel("비밀번호 확인").fill("Reset-only-fixture-7!");
  await page.getByRole("button", { name: "새 비밀번호 저장" }).click();
  await expect.poll(() => attempted).toBe(true);
  await expect(page.getByRole("alert").filter({ hasText: "재설정 링크가 만료" })).toBeVisible();
  await expect(page).toHaveURL(/\/login\?mode=reset/);
});

test("unauthenticated users can reach the real login entry point", async ({ page }) => {
  await page.route("**/api/backend/auth/me", (route) => route.fulfill({ status: 401, json: { error: "unauthenticated" } }));
  await page.route("**/api/auth/supabase/readiness", (route) => route.fulfill({ json: {
    providerUsable: { google: false, kakao: false, naver: false },
  } }));
  await page.goto("/login");

  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByTestId("login-heading")).toBeVisible();
  await expect(page.getByLabel("이메일")).toBeVisible();
  await expect(page.getByLabel("비밀번호", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "비회원으로 체험" })).toBeVisible();
  await expect(page.getByRole("region", { name: "간편 로그인" })).toBeVisible();
  await expect(page.getByRole("button", { name: "네이버 로그인 · 준비 중" })).toBeDisabled();
  await expect(page.getByRole("button", { name: /Google 계정으로 로그인/ })).toHaveCount(0);
});

test("login and signup offer enabled social providers without bypassing email forms", async ({ page }) => {
  await page.route("**/api/backend/auth/me", (route) => route.fulfill({ status: 401, json: { error: "unauthenticated" } }));
  await page.route("**/api/auth/supabase/readiness", (route) => route.fulfill({ json: {
    providerUsable: { google: true, kakao: true, naver: false },
  } }));
  await page.goto("/login");
  await expect(page.getByRole("button", { name: "카카오로 계속하기" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Google 계정으로 로그인" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "네이버 로그인 · 준비 중" })).toBeDisabled();
  await page.getByRole("tab", { name: "처음이신가요? 가입 신청" }).click();
  await expect(page.getByRole("button", { name: "이메일 가입 신청" })).toBeVisible();
  await expect(page.getByRole("button", { name: "카카오로 계속하기" })).toBeEnabled();
  let oauthPath = "";
  await page.route("**/api/auth/supabase/oauth?**", async (route) => {
    oauthPath = route.request().url();
    await route.fulfill({ contentType: "text/html", body: "OAuth test boundary" });
  });
  await page.getByRole("button", { name: "카카오로 계속하기" }).click();
  await expect.poll(() => oauthPath).toContain("provider=kakao");
});

test("verified Naver uses the same-origin OAuth entry and preserves the destination", async ({ page }) => {
  await page.route("**/api/backend/auth/me", (route) => route.fulfill({ status: 401, json: { error: "unauthenticated" } }));
  await page.route("**/api/auth/supabase/readiness", (route) => route.fulfill({ json: {
    providerUsable: { google: true, kakao: true, naver: true },
  } }));
  let oauthPath = "";
  await page.route("**/api/auth/supabase/oauth?**", async (route) => {
    oauthPath = route.request().url();
    await route.fulfill({ contentType: "text/html", body: "OAuth test boundary" });
  });
  await page.goto("/login?next=/project/sample&switch=1");
  await page.getByRole("button", { name: "네이버로 계속하기" }).click();
  await expect.poll(() => oauthPath).toContain("provider=naver");
  const started = new URL(oauthPath);
  expect(started.pathname).toBe("/api/auth/supabase/oauth");
  expect(started.searchParams.get("next")).toBe("/project/sample");
  expect(started.searchParams.get("switch")).toBe("1");
});

test("signup collects profile intent and both versioned consents", async ({ page }) => {
  await page.goto("/login?mode=signup");

  await expect(page.getByRole("tab", { name: "처음이신가요? 가입 신청" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("표시명")).toBeVisible();
  await expect(page.getByText(/realytics-terms-2026-07-30/)).toBeVisible();
  await expect(page.getByText(/realytics-privacy-2026-07-30/)).toBeVisible();
  await expect(page.getByText("12~128자 · 대/소문자·숫자·기호 포함")).toBeVisible();
});

test("signup sends versioned consent and offers privacy-safe confirmation resend", async ({ page }) => {
  await page.clock.install();
  let signupPayload: Record<string, unknown> | null = null;
  let resendPayload: Record<string, unknown> | null = null;
  await page.route("**/api/auth/supabase/password", async (route) => {
    signupPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: false,
        requiresEmailConfirmation: true,
        accessState: "onboarding_required",
        next: "/onboarding",
      }),
    });
  });
  await page.route("**/api/auth/supabase/password/confirmation", async (route) => {
    resendPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        accepted: true,
        accountState: "undisclosed",
        deliveryState: "unconfirmed",
      }),
    });
  });

  await page.goto("/login?mode=signup");
  await page.getByLabel("표시명").fill("가입 검증자");
  await page.getByLabel("이메일").fill("signup@example.com");
  await page.locator('input[name="password"]').fill("A-valid-signup-password-7!");
  await page.getByLabel("비밀번호 확인").fill("A-valid-signup-password-7!");
  await page.getByLabel(/서비스 이용약관/).check();
  await page.getByLabel(/개인정보 처리방침/).check();
  await page.getByRole("button", { name: "이메일 가입 신청" }).click();

  await expect(page.getByTestId("email-confirmation-state")).toBeVisible();
  await expect(page.getByRole("heading", { name: "확인 메일 요청을 접수했습니다" })).toBeVisible();
  await expect(page.getByTestId("email-confirmation-state")).toContainText("실제 발송 여부는 이 화면에서 확인할 수 없습니다");
  expect(signupPayload).toMatchObject({
    termsAccepted: true,
    privacyAccepted: true,
    termsVersion: "realytics-terms-2026-07-30",
    privacyVersion: "realytics-privacy-2026-07-30",
  });
  await expect(page.getByText(/계정 존재 여부 보호/)).toBeVisible();
  await page.clock.runFor(61_000);
  await page.getByRole("button", { name: "확인 메일 다시 보내기" }).click();
  await expect.poll(() => resendPayload).toMatchObject({
    email: "signup@example.com",
  });
  await expect(page.getByRole("status")).toContainText("확인 메일 재요청을 접수했습니다");
  await expect(page.getByRole("status")).toContainText("실제 발송 여부는 이 화면에서 확인할 수 없습니다");
});

test("confirmation requests guide existing social accounts back to every enabled login method", async ({ page }) => {
  await page.route("**/api/backend/auth/me", (route) => route.fulfill({ status: 401, json: { error: "unauthenticated" } }));
  await page.route("**/api/auth/supabase/readiness", (route) => route.fulfill({ json: {
    providerUsable: { google: true, kakao: true, naver: false },
  } }));
  let signupRequests = 0;
  await page.route("**/api/auth/supabase/password", async (route) => {
    signupRequests += 1;
    await route.fulfill({ status: 202, json: {
      authenticated: false,
      requiresEmailConfirmation: true,
      accessState: "onboarding_required",
      next: "/onboarding",
    } });
  });

  await page.goto("/login?mode=signup");
  await page.getByLabel("표시명").fill("가입 검증자");
  await page.getByLabel("이메일").fill("existing-social@example.com");
  await page.locator('input[name="password"]').fill("A-valid-signup-password-7!");
  await page.getByLabel("비밀번호 확인").fill("A-valid-signup-password-7!");
  await page.getByLabel(/서비스 이용약관/).check();
  await page.getByLabel(/개인정보 처리방침/).check();
  await page.getByRole("button", { name: "이메일 가입 신청" }).click();

  await expect(page.getByTestId("email-confirmation-state")).toContainText("Google·카카오로 이미 가입한 주소는 새 가입 메일이 오지 않을 수 있습니다");
  await page.getByRole("button", { name: "기존 계정으로 로그인", exact: true }).click();

  await expect(page.getByRole("tab", { name: "로그인", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("이메일")).toHaveValue("existing-social@example.com");
  await expect(page.getByLabel("비밀번호", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "이메일로 로그인", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Google 계정으로 로그인", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "카카오로 계속하기", exact: true })).toBeEnabled();
  expect(signupRequests).toBe(1);
});

test("password recovery keeps account and delivery state undisclosed", async ({ page }) => {
  let requestPayload: Record<string, unknown> | null = null;
  await page.route("**/api/auth/supabase/password/recovery", async (route) => {
    requestPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        accepted: true,
        accountState: "undisclosed",
        deliveryState: "unconfirmed",
      }),
    });
  });

  await page.goto("/login");
  await page.getByRole("button", { name: "비밀번호 찾기" }).click();
  await page.getByLabel("이메일").fill("unknown@example.com");
  await page.getByRole("button", { name: "재설정 메일 보내기" }).click();

  await expect.poll(() => requestPayload).toMatchObject({
    action: "request",
    email: "unknown@example.com",
  });
  await expect(page.getByRole("status")).toContainText("계정 존재 여부와 실제 발송 여부");
  await expect(page.getByRole("button", { name: /다시 요청/ })).toBeDisabled();
});

for (const destinationAuthenticated of [true, false]) {
  test(`password login navigates without an intermediate identity request and the destination ${destinationAuthenticated ? "verifies access" : "rejects a missing session"}`, async ({ page }) => {
    let submitted = false;
    let initialChecks = 0;
    const identityRequestPages: string[] = [];
    let releaseDestination!: () => void;
    const destinationCheck = new Promise<void>((resolve) => { releaseDestination = resolve; });
    await page.route("**/api/backend/**", (route) => route.fulfill({ status: 404, json: { error: "not_mocked" } }));
    await page.route("**/api/auth/supabase/readiness", (route) => route.fulfill({ json: { providerUsable: {} } }));
    await page.route("**/api/backend/projects/recent-work?*", (route) => route.fulfill({ json: { items: [] } }));
    await page.route("**/api/backend/auth/me", async (route) => {
      const requestedFrom = new URL(route.request().headers().referer ?? page.url()).pathname;
      if (!submitted) {
        initialChecks += 1;
        return route.fulfill({ status: 401, json: { error: "unauthenticated" } });
      }
      identityRequestPages.push(requestedFrom);
      if (requestedFrom === "/start") {
        await destinationCheck;
        if (destinationAuthenticated) return route.fulfill({ json: {
          authenticated: true, authSource: "bearer_token",
          user: { userId: "password-user", tenantId: "password-tenant", role: "analyst", status: "active" },
          session: { userId: "password-user", tenantId: "password-tenant" }, permissions: ["project:read", "project:write"],
        } });
      }
      return route.fulfill({ status: 401, json: { error: "unauthenticated" } });
    });
    await page.route("**/api/auth/supabase/password", async (route) => {
      expect(route.request().postDataJSON()).toMatchObject({ mode: "login", email: "login@example.com" });
      submitted = true;
      await route.fulfill({ json: { authenticated: true, accessState: "member", next: "/start" } });
    });
    await page.goto("/login");
    await expect.poll(() => initialChecks).toBeGreaterThan(0);
    await page.getByLabel("이메일").fill("login@example.com");
    await page.getByLabel("비밀번호", { exact: true }).fill("A-valid-login-password-7!");
    await page.getByRole("button", { name: "이메일로 로그인", exact: true }).click();
    try {
      await expect(page).toHaveURL(/\/start$/);
      await expect.poll(() => identityRequestPages).toEqual(["/start"]);
      await expect(page.getByText("로그인·워크스페이스 확인 중", { exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: "새 자료로 분석 시작" })).toHaveCount(0);
    } finally {
      releaseDestination();
    }
    if (destinationAuthenticated) {
      await expect(page.getByRole("heading", { name: "새 자료로 분석 시작" })).toBeVisible();
    } else {
      await expect(page).toHaveURL(/\/login\?next=/);
      await expect(page.getByTestId("login-heading")).toBeVisible();
    }
  });
}

test("password login retains the server's workspace-unavailable routing without a client membership lookup", async ({ page }) => {
  let submitted = false;
  let initialChecks = 0;
  let checksAfterLogin = 0;
  await page.route("**/api/backend/**", (route) => route.fulfill({ status: 404, json: { error: "not_mocked" } }));
  await page.route("**/api/auth/supabase/readiness", (route) => route.fulfill({ json: { providerUsable: {} } }));
  await page.route("**/api/backend/auth/me", async (route) => {
    if (submitted) checksAfterLogin += 1;
    else initialChecks += 1;
    await route.fulfill({ status: 401, json: { error: "unauthenticated" } });
  });
  await page.route("**/api/auth/supabase/password", async (route) => {
    submitted = true;
    await route.fulfill({ json: { authenticated: true, accessState: "authority_unavailable", next: "/onboarding" } });
  });
  await page.route("**/onboarding", (route) => route.fulfill({ contentType: "text/html", body: "Workspace check boundary" }));
  await page.goto("/login");
  await expect.poll(() => initialChecks).toBeGreaterThan(0);
  await page.getByLabel("이메일").fill("login@example.com");
  await page.getByLabel("비밀번호", { exact: true }).fill("A-valid-login-password-7!");
  await page.getByRole("button", { name: "이메일로 로그인", exact: true }).click();
  await expect(page).toHaveURL(/\/onboarding$/);
  await expect(page.getByText("Workspace check boundary", { exact: true })).toBeVisible();
  expect(checksAfterLogin).toBe(0);
});



test("session change verifies identity again before showing protected content", async ({ page }) => {
  let checks = 0;
  let changed = false;
  let release!: () => void;
  const verification = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/backend/**", (route) => route.fulfill({ status: 404, json: {} }));
  await page.route("**/api/backend/projects/recent-work?*", (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/auth/supabase/readiness", (route) => route.fulfill({ json: { providerUsable: {} } }));
  await page.route("**/api/backend/auth/me", async (route) => {
    checks += 1;
    if (changed) {
      await verification;
      return route.fulfill({ status: 401, json: { error: "session_revoked" } });
    }
    return route.fulfill({ json: {
      authenticated: true, authSource: "bearer_token",
      user: { userId: "session-before", tenantId: "verified-tenant", role: "analyst", status: "active" },
      session: { userId: "session-before", tenantId: "verified-tenant" }, permissions: ["project:read"],
    } });
  });
  await page.goto("/start");
  await expect(page.getByRole("heading", { name: "새 자료로 분석 시작" })).toBeVisible();
  expect(checks).toBe(1);
  changed = true;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("realytics-auth-session-changed")));
  try {
    await expect(page.getByText("로그인·워크스페이스 확인 중", { exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "새 자료로 분석 시작" })).toHaveCount(0);
    await expect.poll(() => checks).toBe(2);
  } finally {
    release();
  }
  await expect(page).toHaveURL(/\/login\?next=/);
});
