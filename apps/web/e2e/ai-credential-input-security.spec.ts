import { expect, test, type Page } from "@playwright/test";

interface CredentialCapture {
  provider?: string;
  model?: string;
  apiKey?: string;
  expiresOnLogout?: boolean;
}

async function mockAuthenticatedSettings(
  page: Page,
  onCredential: (body: CredentialCapture) => Promise<{ status: number; body: object }>,
) {
  await page.route("**/api/backend/auth/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          authenticated: true,
          user: {
            userId: "credential-test-user",
            tenantId: "tenant-live",
            email: "credential.user@example.com",
            displayName: "Credential User",
            role: "reviewer",
            status: "active",
          },
          session: { userId: "credential-test-user", tenantId: "tenant-live" },
          permissions: [],
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
    if (path.endsWith("/auth/ai-credentials") && request.method() === "POST") {
      const result = await onCredential(request.postDataJSON() as CredentialCapture);
      await route.fulfill({
        status: result.status,
        contentType: "application/json",
        body: JSON.stringify(result.body),
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
        providers: { google: true, kakao: true },
        providerUsable: { google: true, kakao: true },
        providerReady: { google: true, kakao: true },
        canonicalOriginReady: true,
        configurationReady: true,
        backend: { ready: true, membershipAuthority: "postgres" },
        operational: true,
        limitations: [],
      }),
    });
  });

  await page.route("**/api/auth/supabase/security", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schemaVersion: "realytics-auth-security-profile-v1",
        userId: "credential-test-user",
        identities: [],
        factors: [],
        assurance: {
          currentLevel: "aal1",
          nextLevel: "aal2",
          currentAuthenticationMethods: [],
        },
        totpEnrollment: {
          eligible: true,
          accountNameSource: "verified_email",
          limitation: null,
        },
        operationalForHighRiskActions: false,
        limitations: ["aal2_step_up_required"],
      }),
    });
  });

  await page.route("**/api/auth/supabase/signout", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ signedOut: true }),
    });
  });
}

async function secretResidue(page: Page, secret: string) {
  return page.evaluate((candidate) => ({
    fieldValues: Array.from(document.querySelectorAll("input, textarea"))
      .some((field) => (field as HTMLInputElement).value.includes(candidate)),
    valueAttributes: Array.from(document.querySelectorAll("input, textarea"))
      .some((field) => (field.getAttribute("value") ?? "").includes(candidate)),
    html: document.documentElement.innerHTML.includes(candidate),
    text: document.body.innerText.includes(candidate),
    storage: JSON.stringify({ ...localStorage, ...sessionStorage }).includes(candidate),
  }), secret);
}

test("AI API token is not a login password and is cleared before the request completes", async ({ page }) => {
  const sentinel = "sentinel-browser-credential-do-not-persist";
  let captured: CredentialCapture | null = null;
  let releaseCredential: (() => void) | undefined;
  const credentialGate = new Promise<void>((resolve) => {
    releaseCredential = resolve;
  });

  await mockAuthenticatedSettings(page, async (body) => {
    captured = body;
    await credentialGate;
    return { status: 200, body: { profile: { apiKeyConfigured: true } } };
  });

  await page.goto("/settings");
  const tokenInput = page.getByTestId("ai-api-key-input");
  await expect(tokenInput).toHaveAttribute("type", "text");
  await expect(tokenInput).toHaveAttribute("name", "realytics-llm-api-token");
  await expect(tokenInput).toHaveAttribute("autocomplete", "off");
  await expect(tokenInput).toHaveAttribute("data-form-type", "other");
  await expect(tokenInput).toHaveAttribute("data-1p-ignore", "true");
  await expect(tokenInput).toHaveAttribute("data-lpignore", "true");
  await expect(tokenInput).toHaveClass(/text-transparent/);
  await expect(page.getByRole("textbox", { name: "모델명" })).toHaveAttribute("autocomplete", "off");

  await tokenInput.fill(sentinel);
  await page.getByTestId("save-ai-profile").click();
  await expect.poll(() => captured).not.toBeNull();
  expect(captured).toMatchObject({
    provider: "gemini",
    model: "gemini-2.5-flash",
    apiKey: sentinel,
    expiresOnLogout: true,
  });
  await expect(tokenInput).toHaveValue("");
  expect(await secretResidue(page, sentinel)).toEqual({
    fieldValues: false,
    valueAttributes: false,
    html: false,
    text: false,
    storage: false,
  });

  releaseCredential?.();
  await expect(tokenInput).toHaveValue("");
});

test("AI API token stays cleared when credential storage fails", async ({ page }) => {
  const sentinel = "sentinel-failed-credential-do-not-persist";
  let captured: CredentialCapture | null = null;
  await mockAuthenticatedSettings(page, async (body) => {
    captured = body;
    return {
      status: 500,
      body: { error: `credential_rejected api_key=${sentinel}` },
    };
  });

  await page.goto("/settings");
  const tokenInput = page.getByTestId("ai-api-key-input");
  await tokenInput.fill(sentinel);
  await page.getByTestId("save-ai-profile").click();
  await expect.poll(() => captured).not.toBeNull();
  await expect(tokenInput).toHaveValue("");
  await expect(page.getByText("AI API Key를 안전 저장소에 연결하지 못했습니다. 입력값은 폐기되었습니다.")).toBeVisible();
  expect(await secretResidue(page, sentinel)).toEqual({
    fieldValues: false,
    valueAttributes: false,
    html: false,
    text: false,
    storage: false,
  });
});

test("AI API token is cleared before logout starts", async ({ page }) => {
  const sentinel = "sentinel-logout-credential-do-not-persist";
  await mockAuthenticatedSettings(page, async () => ({
    status: 200,
    body: { profile: { apiKeyConfigured: true } },
  }));

  await page.goto("/settings");
  const tokenInput = page.getByTestId("ai-api-key-input");
  await tokenInput.fill(sentinel);
  await page.getByTestId("settings-logout").click();

  await expect(tokenInput).toHaveValue("");
  expect(await secretResidue(page, sentinel)).toEqual({
    fieldValues: false,
    valueAttributes: false,
    html: false,
    text: false,
    storage: false,
  });
});
