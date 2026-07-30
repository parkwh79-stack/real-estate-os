import { expect, test } from "@playwright/test";
import { safeInternalNextPath } from "../lib/safe-navigation";

test("callback next paths reject slash-backslash and encoded authority escapes", () => {
  expect(safeInternalNextPath("/project/p-1?tab=risk")).toBe(
    "/project/p-1?tab=risk",
  );
  expect(safeInternalNextPath("/\\\\evil.example")).toBe("/start");
  expect(safeInternalNextPath("//evil.example")).toBe("/start");
  expect(safeInternalNextPath("/\u0000evil.example")).toBe("/start");
});
import {
  PASSWORD_RECOVERY_NEXT_PATH,
  passwordRecoveryCallbackPlan,
  passwordRecoveryCallbackUrl,
} from "../app/api/auth/supabase/password/recovery-contract";

test("password recovery callback preserves reset destination and safe access state", () => {
  const callback = passwordRecoveryCallbackUrl("https://realytics.example");
  expect(callback.pathname).toBe("/auth/callback");
  expect(callback.searchParams.get("next")).toBe(
    PASSWORD_RECOVERY_NEXT_PATH,
  );
  expect(callback.searchParams.get("recovery")).toBe("1");

  for (const accessState of [
    "member",
    "tenant_selection_required",
    "onboarding_required",
    "authority_unavailable",
  ] as const) {
    const plan = passwordRecoveryCallbackPlan(
      "https://realytics.example",
      PASSWORD_RECOVERY_NEXT_PATH,
      accessState,
    );
    expect(plan.setRecoveryContextCookie).toBe(true);
    const destination = plan.destination;
    expect(destination.pathname).toBe("/login");
    expect(destination.searchParams.get("mode")).toBe("reset");
    expect(destination.searchParams.get("flow")).toBe("password_recovery");
    expect(destination.searchParams.get("accessState")).toBe(accessState);
  }
});

test("browser auth state contains no persisted REALYTICS token or session id", async ({
  page,
}) => {
  let browserAuthHeaders: Record<string, string> = {};
  let authRequestSeen = false;
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "realytics.auth.session.v1",
      JSON.stringify({
        accessToken: "must-be-removed",
        session: { sessionId: "must-be-removed" },
      }),
    );
  });
  await page.route("**/api/backend/auth/me", async (route) => {
    browserAuthHeaders = await route.request().allHeaders();
    authRequestSeen = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: false,
        user: { userId: "anonymous", role: "anonymous" },
        permissions: [],
      }),
    });
  });
  await page.route("**/api/backend/auth/stats", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        usersCount: 0,
        dataUsageCount: 0,
        projectScaleTrillionWon: 0,
      }),
    });
  });

  await page.goto("/login");

  await expect.poll(async () => {
    return await page.evaluate(() =>
      window.localStorage.getItem("realytics.auth.session.v1"));
  }).toBeNull();
  expect(authRequestSeen).toBe(true);
  expect(browserAuthHeaders.authorization).toBeUndefined();
  expect(browserAuthHeaders["x-realytics-session-id"]).toBeUndefined();
  expect(browserAuthHeaders["x-realytics-user-id"]).toBeUndefined();
  expect(browserAuthHeaders["x-realytics-tenant-id"]).toBeUndefined();
  expect(browserAuthHeaders["x-realytics-bff"]).toBe("same-origin");
});

test("local pilot and legacy session exchange are fail-closed without opt-in", async ({
  page,
  request,
}) => {
  await page.route("**/api/backend/auth/**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authenticated: false,
        user: { userId: "anonymous", role: "anonymous" },
        permissions: [],
      }),
    });
  });
  await page.goto("/login");

  await expect(page.getByTestId("local-pilot-login-tab")).toHaveCount(0);

  const exchange = await request.post("/api/auth/session-exchange", {
    data: { sessionId: "opaque-but-not-allowed" },
  });
  expect(exchange.status()).toBe(404);
  await expect(exchange.json()).resolves.toMatchObject({
    error: "legacy_oauth_session_exchange_disabled",
  });
});

test("BFF rejects cross-site mutations before contacting FastAPI", async ({ request }) => {
  const response = await request.post("/api/backend/projects", {
    headers: {
      Origin: "https://attacker.invalid",
    },
    data: { name: "cross-site" },
  });

  expect(response.status()).toBe(403);
  await expect(response.json()).resolves.toMatchObject({
    error: "cross_site_request_rejected",
  });
});

test("password BFF rejects cross-site and malformed authentication input", async ({
  request,
}) => {
  const crossSite = await request.post("/api/auth/supabase/password", {
    headers: { Origin: "https://attacker.invalid" },
    data: {
      mode: "login",
      email: "person@example.com",
      password: "not-sent-to-supabase",
    },
  });
  expect(crossSite.status()).toBe(403);
  await expect(crossSite.json()).resolves.toEqual({
    error: "cross_site_request_rejected",
  });
  expect(crossSite.headers()["cache-control"]).toContain("no-store");

  const invalidMode = await request.post("/api/auth/supabase/password", {
    data: {
      mode: "password-reset-via-login",
      email: "person@example.com",
      password: "not-sent-to-supabase",
    },
  });
  expect(invalidMode.status()).toBe(400);
  await expect(invalidMode.json()).resolves.toEqual({
    error: "invalid_authentication_input",
  });
  expect(invalidMode.headers()["cache-control"]).toContain("no-store");
});

test("tenant selector BFF rejects cross-site and malformed selectors before authority lookup", async ({ request }) => {
  const crossSite = await request.post("/api/auth/supabase/tenant", {
    headers: { Origin: "https://attacker.invalid" },
    data: { tenantId: "tenant-a" },
  });
  expect(crossSite.status()).toBe(403);
  await expect(crossSite.json()).resolves.toEqual({
    error: "cross_site_request_rejected",
  });

  const invalid = await request.post("/api/auth/supabase/tenant", {
    data: { tenantId: "tenant-a\r\nX-Forged: true" },
  });
  expect(invalid.status()).toBe(400);
  await expect(invalid.json()).resolves.toEqual({
    error: "invalid_tenant_selector",
  });
  expect(invalid.headers()["cache-control"]).toContain("no-store");
});

test("signup and recovery enforce the server password contract", async ({
  request,
}) => {
  const weakSignup = await request.post("/api/auth/supabase/password", {
    data: {
      mode: "signup",
      email: "person@example.com",
      password: "short",
    },
  });
  expect(weakSignup.status()).toBe(400);
  await expect(weakSignup.json()).resolves.toEqual({
    error: "weak_password_input",
    passwordPolicy: {
      minLength: 12,
      maxLength: 128,
      requiresLowercase: true,
      requiresUppercase: true,
      requiresDigit: true,
      requiresSymbol: true,
    },
  });
  expect(weakSignup.headers()["cache-control"]).toContain("no-store");

  const missingRecoveryContext = await request.post(
    "/api/auth/supabase/password/recovery",
    {
      data: {
        action: "complete",
        password: "A-valid-new-password-7",
      },
    },
  );
  expect(missingRecoveryContext.status()).toBe(401);
  await expect(missingRecoveryContext.json()).resolves.toEqual({
    error: "password_recovery_context_required",
  });
  expect(missingRecoveryContext.headers()["cache-control"]).toContain(
    "no-store",
  );
});

test("OAuth callback accepts authorization codes only", async ({ request }) => {
  const response = await request.get(
    "/auth/callback?next=/project/deal-1&switch=1",
    { maxRedirects: 0 },
  );

  expect(response.status()).toBeGreaterThanOrEqual(300);
  expect(response.status()).toBeLessThan(400);
  const retryLocation = new URL(response.headers().location ?? "");
  expect(retryLocation.pathname).toBe("/login");
  expect(retryLocation.searchParams.get("error")).toBe("missing_oauth_code");
  expect(retryLocation.searchParams.get("next")).toBe("/project/deal-1");
  expect(retryLocation.searchParams.get("switch")).toBe("1");
  expect(retryLocation.searchParams.has("session_id")).toBe(false);
  expect(response.headers()["cache-control"]).toContain("no-store");
});

test("configured OAuth start stores PKCE state in hardened cookies", async ({
  request,
}) => {
  const response = await request.get(
    "/api/auth/supabase/oauth?provider=google&next=/start",
    { maxRedirects: 0 },
  );
  const location = response.headers().location ?? "";
  test.skip(
    location.includes("oauth_not_configured"),
    "Supabase Auth environment is not configured for this test run.",
  );

  expect(response.status()).toBeGreaterThanOrEqual(300);
  expect(response.status()).toBeLessThan(400);
  expect(location).toContain("/auth/v1/authorize");
  expect(new URL(location).searchParams.get("prompt")).toBeNull();
  const redirectTo = new URL(location).searchParams.get("redirect_to");
  expect(redirectTo).not.toBeNull();
  expect(new URL(redirectTo!).origin).toBe(new URL(response.url()).origin);
  const setCookies = response
    .headersArray()
    .filter((header) => header.name.toLowerCase() === "set-cookie")
    .map((header) => header.value);
  expect(setCookies.length).toBeGreaterThan(0);
  for (const cookie of setCookies) {
    expect(cookie).toContain("HttpOnly");
    expect(cookie.toLowerCase()).toContain("samesite=lax");
  }
});

test("Kakao OAuth start sends a server-controlled scope override", async ({
  request,
}) => {
  const response = await request.get(
    "/api/auth/supabase/oauth?provider=kakao&next=/start",
    { maxRedirects: 0 },
  );
  const location = response.headers().location ?? "";
  test.skip(
    location.includes("oauth_not_configured"),
    "Supabase Auth environment is not configured for this test run.",
  );

  expect(response.status()).toBeGreaterThanOrEqual(300);
  expect(response.status()).toBeLessThan(400);
  expect(location).toContain("/auth/v1/authorize");
  const requestedScope = new URL(location).searchParams.get("scope");
  expect(requestedScope).toBe("profile_nickname profile_image");
  expect(new URL(location).searchParams.get("prompt")).toBeNull();
  expect(new URL(location).searchParams.has("scopes")).toBe(false);
  expect(requestedScope).not.toContain("account_email");
});

test("explicit account switching uses provider-specific interactive prompts", async ({
  request,
}) => {
  const google = await request.get(
    "/api/auth/supabase/oauth?provider=google&next=/start&switch=1&prompt=none",
    { maxRedirects: 0 },
  );
  const googleLocation = google.headers().location ?? "";
  test.skip(
    googleLocation.includes("oauth_not_configured"),
    "Supabase Auth environment is not configured for this test run.",
  );
  expect(new URL(googleLocation).searchParams.get("prompt")).toBe("select_account");

  const kakao = await request.get(
    "/api/auth/supabase/oauth?provider=kakao&next=/start&switch=1&prompt=none&scope=account_email",
    { maxRedirects: 0 },
  );
  const kakaoLocation = kakao.headers().location ?? "";
  expect(new URL(kakaoLocation).searchParams.get("prompt")).toBe("login");
  expect(new URL(kakaoLocation).searchParams.get("scope")).toBe(
    "profile_nickname profile_image",
  );

  const ignoredSwitch = await request.get(
    "/api/auth/supabase/oauth?provider=google&next=/start&switch=true&prompt=select_account",
    { maxRedirects: 0 },
  );
  expect(
    new URL(ignoredSwitch.headers().location ?? "").searchParams.get("prompt"),
  ).toBeNull();
});

test("OAuth start canonicalizes an alternate loopback host before issuing PKCE state", async ({
  request,
}) => {
  const baseURL = new URL(
    process.env.REALYTICS_E2E_BASE_URL ?? "http://localhost:3000",
  );
  const alternateHost = baseURL.hostname === "localhost"
    ? "127.0.0.1"
    : baseURL.hostname === "127.0.0.1"
      ? "localhost"
      : null;
  test.skip(!alternateHost, "Only applies to a local loopback OAuth rehearsal.");

  const alternateOrigin = `${baseURL.protocol}//${alternateHost}:${baseURL.port}`;
  const response = await request.get(
    `${alternateOrigin}/api/auth/supabase/oauth?provider=google&next=/start`,
    { maxRedirects: 0 },
  );
  const location = response.headers().location ?? "";

  expect(response.status()).toBeGreaterThanOrEqual(300);
  expect(response.status()).toBeLessThan(400);
  expect(new URL(location).origin).toBe(baseURL.origin);
  expect(location).toContain("/api/auth/supabase/oauth");
  expect(
    response.headersArray().some(
      (header) => header.name.toLowerCase() === "set-cookie",
    ),
  ).toBe(false);
});

test("auth readiness is secret-safe and reports both production providers", async ({
  request,
}) => {
  const response = await request.get("/api/auth/supabase/readiness");
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.schemaVersion).toBe("realytics-supabase-auth-readiness-v1");
  expect(body.providers).toEqual({
    google: expect.any(Boolean),
    kakao: expect.any(Boolean),
  });
  expect(body.providerUsable).toEqual({
    google: expect.any(Boolean),
    kakao: expect.any(Boolean),
  });
  expect(body.providerReady).toEqual({
    google: expect.any(Boolean),
    kakao: expect.any(Boolean),
  });
  for (const provider of ["google", "kakao"] as const) {
    expect(body.providerReady[provider]).toBe(Boolean(
      body.configured
        && body.settingsReachable
        && body.providerUsable[provider]
        && body.canonicalOriginReady
        && body.backend.ready,
    ));
  }
  if (body.providers.kakao && !body.providerUsable.kakao) {
    expect(body.limitations).toContain(
      "kakao_email_optional_not_verified",
    );
  }
  expect(body.limitations).not.toContain(
    "kakao_account_email_scope_not_verified",
  );
  expect(body.configurationReady).toEqual(expect.any(Boolean));
  expect(body.configurationReady).toBe(
    body.providerReady.google && body.providerReady.kakao,
  );
  expect(body.operational).toBe(false);
  expect(body.limitations).toEqual(expect.arrayContaining([
    body.configurationReady
      ? "real_user_aal2_maker_checker_smoke_required"
      : "configuration_gate_incomplete",
  ]));
  expect(body.backend).toEqual(expect.objectContaining({
    ready: expect.any(Boolean),
  }));
  expect(
    body.backend.membershipAuthority === null
      || typeof body.backend.membershipAuthority === "string",
  ).toBe(true);
  const rendered = JSON.stringify(body).toLowerCase();
  expect(rendered).not.toContain("publishable_key");
  expect(rendered).not.toContain("service_role");
  expect(rendered).not.toContain("refresh_token");
});

test("security mutations and session revocation reject cross-site requests", async ({
  request,
}) => {
  const security = await request.post("/api/auth/supabase/security", {
    headers: { Origin: "https://attacker.invalid" },
    data: { action: "mfa_enroll_totp" },
  });
  expect(security.status()).toBe(403);
  await expect(security.json()).resolves.toMatchObject({
    error: "cross_site_request_rejected",
  });

  const signout = await request.post("/api/auth/supabase/signout", {
    headers: { Origin: "https://attacker.invalid" },
    data: { scope: "global" },
  });
  expect(signout.status()).toBe(403);
  await expect(signout.json()).resolves.toMatchObject({
    error: "cross_site_request_rejected",
  });
});

test("session revocation rejects unknown scopes without caching the response", async ({
  request,
}) => {
  const response = await request.post("/api/auth/supabase/signout", {
    data: { scope: "current-and-all-future-sessions" },
  });
  expect(response.status()).toBe(400);
  await expect(response.json()).resolves.toEqual({ error: "invalid_signout_scope" });
  expect(response.headers()["cache-control"]).toContain("no-store");
});

test("identity linking rejects providers outside the approved allow-list", async ({
  request,
}) => {
  const response = await request.post("/api/auth/supabase/identity/link", {
    data: { provider: "github", next: "/settings" },
  });
  expect(response.status()).toBe(400);
  await expect(response.json()).resolves.toMatchObject({
    error: "unsupported_identity_provider",
  });
});

test("invitation intent is isolated in an HttpOnly cookie before OAuth", async ({
  request,
}) => {
  const token = "t".repeat(48);
  const response = await request.get(
    `/api/auth/supabase/invitation/start?token=${token}`,
    { maxRedirects: 0 },
  );
  expect(response.status()).toBeGreaterThanOrEqual(300);
  expect(response.status()).toBeLessThan(400);
  expect(response.headers().location).toContain("/login?invitation=pending");
  expect(response.headers().location).not.toContain(token);
  expect(response.headers()["referrer-policy"]).toBe("no-referrer");
  const setCookies = response
    .headersArray()
    .filter((header) => header.name.toLowerCase() === "set-cookie")
    .map((header) => header.value);
  expect(setCookies.some((cookie) => (
    cookie.includes("realytics-invitation=")
      && cookie.includes("HttpOnly")
      && cookie.toLowerCase().includes("samesite=lax")
  ))).toBe(true);
});

test("tenant administrator issues and revokes hash-only invitations", async ({
  page,
}) => {
  const invitationToken = "live-once-" + "t".repeat(48);
  const issueRequests: Array<Record<string, unknown>> = [];
  let revokeRequestSeen = false;

  await page.route("**/api/backend/auth/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          authenticated: true,
          user: {
            userId: "tenant-admin",
            tenantId: "tenant-live",
            email: "admin@example.com",
            displayName: "Tenant Admin",
            role: "admin",
            status: "active",
          },
          session: { userId: "tenant-admin", tenantId: "tenant-live" },
          permissions: ["user:manage"],
          authSource: "bearer_token",
        }),
      });
      return;
    }
    if (path.endsWith("/auth/ai-profile")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ profile: null, effectiveAi: {} }),
      });
      return;
    }
    if (path.endsWith("/auth/invitations") && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          invitations: [{
            invitationId: "inv-existing",
            tenantId: "tenant-live",
            role: "viewer",
            status: "pending",
            invitedEmail: "existing@example.com",
            invitedIssuer: null,
            invitedSubject: null,
            issuedByIssuer: "https://issuer.example",
            issuedBySubject: "tenant-admin",
            issuedAt: "2026-07-24T01:00:00Z",
            expiresAt: "2026-07-27T01:00:00Z",
            acceptedByIssuer: null,
            acceptedBySubject: null,
            acceptedAt: null,
            revokedAt: null,
            revocationReason: null,
          }],
        }),
      });
      return;
    }
    if (path.endsWith("/auth/invitations") && request.method() === "POST") {
      const issueRequest = request.postDataJSON() as Record<string, unknown>;
      issueRequests.push(issueRequest);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          invitation: {
            invitationId: "inv-new",
            tenantId: "tenant-live",
            role: "analyst",
            status: "pending",
            invitedEmail: issueRequest.invitedEmail ?? null,
            invitedIssuer: issueRequest.invitedIssuer ?? null,
            invitedSubject: issueRequest.invitedSubject ?? null,
            issuedByIssuer: "https://issuer.example",
            issuedBySubject: "tenant-admin",
            issuedAt: "2026-07-24T02:00:00Z",
            expiresAt: "2026-07-27T02:00:00Z",
            acceptedByIssuer: null,
            acceptedBySubject: null,
            acceptedAt: null,
            revokedAt: null,
            revocationReason: null,
          },
          token: invitationToken,
          tokenPolicy: "shown_once_never_persisted_in_plaintext",
        }),
      });
      return;
    }
    if (
      path.endsWith("/auth/invitations/inv-existing/revoke")
      && request.method() === "POST"
    ) {
      revokeRequestSeen = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          invitation: { invitationId: "inv-existing", status: "revoked" },
        }),
      });
      return;
    }
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: "not_mocked" }),
    });
  });
  await page.route("**/api/auth/supabase/readiness", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        providers: { google: false, kakao: false },
        backend: { ready: true, membershipAuthority: "postgres" },
        operational: false,
        limitations: ["google_provider_not_enabled", "kakao_provider_not_enabled"],
      }),
    });
  });
  await page.route("**/api/auth/supabase/security", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        identities: [{
          identityId: "identity-email",
          provider: "email",
          email: "admin@example.com",
          createdAt: "2026-07-24T01:00:00Z",
          lastSignInAt: "2026-07-24T01:00:00Z",
        }],
        factors: [],
        assurance: {
          currentLevel: "aal1",
          nextLevel: "aal2",
          currentAuthenticationMethods: [],
        },
      }),
    });
  });

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "테넌트 사용자 초대" })).toBeVisible();
  await expect(page.getByText("existing@example.com")).toBeVisible();

  await page.getByLabel("초대 대상 이메일").fill("new.user@example.com");
  await page.getByRole("button", { name: "일회성 초대 발급" }).click();
  await expect.poll(() => issueRequests[0]).toMatchObject({
    role: "analyst",
    invitedEmail: "new.user@example.com",
    expiresInHours: 72,
  });
  expect(issueRequests[0]).not.toHaveProperty("invitedIssuer");
  expect(issueRequests[0]).not.toHaveProperty("invitedSubject");
  const issuedLink = page.getByLabel("일회성 초대 링크");
  await expect(issuedLink).toHaveValue(new RegExp(invitationToken));
  await expect.poll(async () => page.evaluate((token) => (
    JSON.stringify({ ...localStorage, ...sessionStorage }).includes(token)
  ), invitationToken)).toBe(false);

  await page.getByRole("button", { name: "Supabase 신원 초대" }).click();
  const issuerInput = page.getByLabel("Supabase Auth issuer");
  if (!await issuerInput.inputValue()) {
    await issuerInput.fill("https://project.supabase.co/auth/v1");
  }
  const issuer = await issuerInput.inputValue();
  const subject = "6a7f0a6a-92c8-4cc3-b8ae-17db4983e7d8";
  await page.getByLabel("Supabase Auth subject (사용자 UUID)").fill("email-is-not-a-subject");
  await page.getByRole("button", { name: "일회성 초대 발급" }).click();
  await expect(page.getByText("Supabase Auth subject는 사용자 상세의 정확한 UUID여야 합니다.")).toBeVisible();
  expect(issueRequests).toHaveLength(1);
  await page.getByLabel("Supabase Auth subject (사용자 UUID)").fill(subject);
  await page.getByRole("button", { name: "일회성 초대 발급" }).click();
  await expect.poll(() => issueRequests.length).toBe(2);
  expect(issueRequests[1]).toMatchObject({
    role: "analyst",
    invitedIssuer: issuer,
    invitedSubject: subject,
    expiresInHours: 72,
  });
  expect(issueRequests[1]).not.toHaveProperty("invitedEmail");

  await page.getByRole("button", { name: "초대 취소" }).click();
  await expect.poll(() => revokeRequestSeen).toBe(true);
});
