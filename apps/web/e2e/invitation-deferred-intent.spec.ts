import { expect, test, type Page, type Route } from "@playwright/test";
import {
  classifyInvitationAcceptanceResponse,
  shouldClearInvitationIntent,
} from "../lib/invitation-intent";

test("invitation responses preserve only retryable HttpOnly intents", () => {
  const profileRequired = classifyInvitationAcceptanceResponse(409, {
    detail: { error: "onboarding_profile_required" },
  });
  expect(profileRequired.state).toBe("profile_required");
  expect(shouldClearInvitationIntent(profileRequired.state)).toBe(false);

  const policyRequired = classifyInvitationAcceptanceResponse(409, {
    detail: {
      error: "terms_version_mismatch",
      currentPolicyVersions: {
        termsVersion: "terms-v2",
        privacyVersion: "privacy-v2",
      },
    },
  });
  expect(policyRequired).toMatchObject({
    state: "policy_update_required",
    currentPolicyVersions: {
      termsVersion: "terms-v2",
      privacyVersion: "privacy-v2",
    },
  });
  expect(shouldClearInvitationIntent(policyRequired.state)).toBe(false);

  const expired = classifyInvitationAcceptanceResponse(410, {
    detail: { error: "invitation_expired" },
  });
  expect(expired.state).toBe("rejected");
  expect(shouldClearInvitationIntent(expired.state)).toBe(true);

  const accepted = classifyInvitationAcceptanceResponse(200, {
    invitation: { tenantId: "tenant-approved" },
  });
  expect(accepted).toMatchObject({
    state: "accepted",
    tenantId: "tenant-approved",
  });
  expect(shouldClearInvitationIntent(accepted.state)).toBe(true);

  const malformedSuccess = classifyInvitationAcceptanceResponse(200, {
    invitation: { status: "accepted" },
  });
  expect(malformedSuccess.state).toBe("authority_unavailable");
  expect(shouldClearInvitationIntent(malformedSuccess.state)).toBe(false);
});

test("deferred invitation BFF rejects cross-site calls and requires its HttpOnly intent", async ({
  request,
}) => {
  const crossSite = await request.post(
    "/api/auth/supabase/invitation/accept",
    { headers: { Origin: "https://attacker.invalid" } },
  );
  expect(crossSite.status()).toBe(403);
  await expect(crossSite.json()).resolves.toEqual({
    error: "cross_site_request_rejected",
  });

  const missing = await request.post(
    "/api/auth/supabase/invitation/accept",
  );
  expect(missing.status()).toBe(409);
  await expect(missing.json()).resolves.toMatchObject({
    error: "invitation_intent_missing",
    invitation: "not_pending",
    next: "/onboarding",
  });
  expect(missing.headers()["cache-control"]).toContain("no-store");
});

test("manual invitation BFF rejects CSRF and malformed input without reflecting secrets", async ({
  request,
}) => {
  const secret = `manual-${"A".repeat(57)}`;
  const crossSite = await request.post(
    "/api/auth/supabase/invitation/code",
    {
      headers: { Origin: "https://attacker.invalid" },
      data: { code: secret },
    },
  );
  expect(crossSite.status()).toBe(403);
  expect(crossSite.headers()["cache-control"]).toContain("no-store");
  const crossSiteBody = await crossSite.text();
  expect(crossSiteBody).toBe(JSON.stringify({
    error: "cross_site_request_rejected",
  }));
  expect(crossSiteBody).not.toContain(secret);

  for (const data of [
    {},
    { code: "too-short" },
    { code: `bad whitespace ${"B".repeat(48)}` },
    { code: secret, unexpected: "must-not-be-accepted" },
  ]) {
    const malformed = await request.post(
      "/api/auth/supabase/invitation/code",
      { data },
    );
    expect(malformed.status()).toBe(400);
    expect(malformed.headers()["cache-control"]).toContain("no-store");
    const malformedBody = await malformed.text();
    expect(malformedBody).toBe(JSON.stringify({
      error: "invitation_code_invalid",
    }));
    expect(malformedBody).not.toContain(secret);
    expect(malformedBody).not.toContain("must-not-be-accepted");
  }
});

test("saving the onboarding profile completes a deferred invite with an empty browser POST", async ({
  page,
}) => {
  const pendingTokenSentinel = "server-only-invitation-token-sentinel";
  let profile: Record<string, unknown> | null = null;
  let acceptRequestUrl = "";
  let acceptRequestBody: string | null | undefined;
  await mockMemberlessPrincipal(page, async (route, path) => {
    const request = route.request();
    if (path.endsWith("/auth/onboarding/status")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accessState: "onboarding_required",
          profile,
          requests: [],
          policyVersions: {
            termsVersion: "realytics-terms-2026-07-30",
            privacyVersion: "realytics-privacy-2026-07-30",
          },
        }),
      });
      return true;
    }
    if (path.endsWith("/auth/onboarding/profile") && request.method() === "PUT") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      profile = {
        displayName: payload.displayName,
        phone: null,
        jobTitle: null,
        termsVersion: payload.termsVersion,
        privacyVersion: payload.privacyVersion,
        profileVersion: 1,
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ profile }),
      });
      return true;
    }
    return false;
  });
  await page.route("**/api/auth/supabase/invitation/accept", async (route) => {
    acceptRequestUrl = route.request().url();
    acceptRequestBody = route.request().postData();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schemaVersion: "realytics-deferred-invitation-accept-v1",
        invitation: "accepted",
        tenantId: "tenant-approved",
        next: "/start",
      }),
    });
  });
  await page.route("**/start", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<main>approved tenant entered</main>",
    });
  });

  await page.goto("/onboarding?invitation=profile_required");
  await expect(page.getByText(/초대가 안전하게 보류되어 있습니다/)).toBeVisible();
  await page.getByLabel("표시명").fill("초대 사용자");
  await page.getByLabel(/이용약관 realytics-terms-2026-07-30/).check();
  await page.getByLabel(/개인정보 처리방침 realytics-privacy-2026-07-30/).check();
  await page.getByRole("button", { name: "프로필·동의 저장" }).click();

  await expect.poll(() => acceptRequestUrl).not.toBe("");
  expect(acceptRequestBody).toBeNull();
  expect(acceptRequestUrl).not.toContain(pendingTokenSentinel);
  await expect(page).toHaveURL(/\/start$/);
  await expect(page.getByText("approved tenant entered")).toBeVisible();
  expect(await page.evaluate((sentinel) => (
    JSON.stringify({ ...localStorage, ...sessionStorage }).includes(sentinel)
  ), pendingTokenSentinel)).toBe(false);
});

test("current onboarding profile can submit a masked one-time code without retaining it", async ({
  page,
}) => {
  const invitationCode = `manual_${"c".repeat(56)}`;
  let receivedUrl = "";
  let receivedBody: Record<string, unknown> | null = null;
  let releaseResponse!: () => void;
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });

  await mockMemberlessPrincipal(page, async (route, path) => {
    if (path.endsWith("/auth/onboarding/status")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accessState: "onboarding_required",
          profile: {
            displayName: "초대 사용자",
            phone: null,
            jobTitle: null,
            termsVersion: "realytics-terms-2026-07-30",
            privacyVersion: "realytics-privacy-2026-07-30",
            profileVersion: 1,
          },
          requests: [],
          policyVersions: {
            termsVersion: "realytics-terms-2026-07-30",
            privacyVersion: "realytics-privacy-2026-07-30",
          },
        }),
      });
      return true;
    }
    return false;
  });
  await page.route("**/api/auth/supabase/invitation/code", async (route) => {
    receivedUrl = route.request().url();
    receivedBody = route.request().postDataJSON() as Record<string, unknown>;
    await responseGate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schemaVersion: "realytics-manual-invitation-accept-v1",
        invitation: "accepted",
        tenantId: "tenant-manual-approved",
        next: "/start",
      }),
    });
  });
  await page.route("**/start", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<main>manual invitation accepted</main>",
    });
  });

  await page.goto("/onboarding");
  const codeInput = page.getByLabel("초대 코드 직접 입력");
  await expect(codeInput).toHaveAttribute("type", "password");
  await expect(codeInput).toBeEnabled();
  await codeInput.fill(invitationCode);
  await page.getByTestId("accept-manual-invitation").click();

  await expect.poll(() => receivedUrl).not.toBe("");
  await expect(codeInput).toHaveValue("");
  expect(receivedUrl).not.toContain(invitationCode);
  expect(receivedBody).toEqual({ code: invitationCode });
  expect(await page.evaluate((secret) => (
    JSON.stringify({ ...localStorage, ...sessionStorage }).includes(secret)
  ), invitationCode)).toBe(false);

  releaseResponse();
  await expect(page).toHaveURL(/\/start$/);
  await expect(page.getByText("manual invitation accepted")).toBeVisible();
});

async function mockMemberlessPrincipal(
  page: Page,
  handler: (
    route: Route,
    path: string,
  ) => Promise<boolean>,
) {
  await page.route("**/api/backend/auth/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: { error: "principal_not_registered" } }),
      });
      return;
    }
    if (await handler(route, path)) return;
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ error: "not_mocked" }),
    });
  });
  await page.route("**/api/auth/supabase/security", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        userId: "invited-subject",
        identities: [{
          identityId: "email-1",
          provider: "email",
          email: "invited@example.com",
          createdAt: null,
          lastSignInAt: null,
        }],
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
        limitations: [],
      }),
    });
  });
}
