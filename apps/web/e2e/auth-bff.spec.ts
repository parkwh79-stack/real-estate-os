import { expect, test } from "@playwright/test";

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

test("OAuth callback accepts authorization codes only", async ({ request }) => {
  const response = await request.get("/auth/callback", { maxRedirects: 0 });

  expect(response.status()).toBeGreaterThanOrEqual(300);
  expect(response.status()).toBeLessThan(400);
  expect(response.headers().location).toContain("/login?error=missing_oauth_code");
  expect(response.headers().location).not.toContain("session_id=");
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
