import { expect, test } from "@playwright/test";
import {
  evaluateRecentAal2,
  evaluateTotpEnrollmentEligibility,
  findVerifiedTotpFactor,
  parseTotpChallengeInput,
} from "../lib/supabase/mfa-security";
import {
  evaluateIdentityUnlink,
  safeIdentityLinkRedirectUrl,
} from "../lib/supabase/identity-security";

test("recovery-sensitive actions require a timestamped, recent TOTP AAL2 proof", () => {
  const now = 1_800_000_000;
  expect(evaluateRecentAal2("aal2", [
    { method: "password", timestamp: now - 30 },
    { method: "totp", timestamp: now - 299 },
  ], now)).toEqual({
    eligible: true,
    verifiedAt: new Date((now - 299) * 1000).toISOString(),
    limitation: null,
  });
  expect(evaluateRecentAal2("aal2", [
    { method: "totp", timestamp: now - 301 },
  ], now).limitation).toBe("recent_aal2_required");
  expect(evaluateRecentAal2("aal2", ["password", "totp"], now).limitation)
    .toBe("recent_aal2_required");
  expect(evaluateRecentAal2("aal1", [
    { method: "totp", timestamp: now },
  ], now).limitation).toBe("aal2_step_up_required");
});

test("email remains the primary identity and provider redirects are allowlisted", () => {
  const identities = [
    { identityId: "email-id", provider: "email" },
    { identityId: "google-id", provider: "google" },
  ];
  expect(evaluateIdentityUnlink(identities, "email-id")).toEqual({
    eligible: false,
    limitation: "primary_email_identity_cannot_be_unlinked",
  });
  expect(evaluateIdentityUnlink(identities, "google-id")).toEqual({
    eligible: true,
    limitation: null,
  });
  expect(evaluateIdentityUnlink([identities[1]!], "google-id").limitation)
    .toBe("last_identity_cannot_be_unlinked");

  expect(safeIdentityLinkRedirectUrl(
    "google",
    "https://accounts.google.com/o/oauth2/v2/auth?client_id=public",
  )).toContain("accounts.google.com");
  expect(safeIdentityLinkRedirectUrl(
    "kakao",
    "https://kauth.kakao.com/oauth/authorize?client_id=public",
  )).toContain("kauth.kakao.com/oauth/authorize");
  for (const unsafe of [
    "https://attacker.invalid/oauth",
    "https://accounts.google.com.attacker.invalid/oauth",
    "http://accounts.google.com/oauth",
    "https://accounts.google.com:444/o/oauth2/v2/auth",
    "https://user:password@accounts.google.com/oauth",
    "javascript:alert(1)",
  ]) {
    expect(safeIdentityLinkRedirectUrl(
      "google",
      unsafe,
    )).toBeNull();
  }
  expect(safeIdentityLinkRedirectUrl(
    "google",
    "https://accounts.google.com/logout?continue=https://attacker.invalid",
  )).toBeNull();
});

test("TOTP enrollment requires a verified Auth email without treating it as identity", () => {
  expect(evaluateTotpEnrollmentEligibility({})).toEqual({
    eligible: false,
    accountNameSource: null,
    limitation: "auth_email_required",
  });
  expect(evaluateTotpEnrollmentEligibility({
    email: "checker@example.com",
  })).toEqual({
    eligible: false,
    accountNameSource: null,
    limitation: "verified_auth_email_required",
  });
  expect(evaluateTotpEnrollmentEligibility({
    email: " checker@example.com ",
    email_confirmed_at: "2026-07-28T03:00:00Z",
  })).toEqual({
    eligible: true,
    accountNameSource: "verified_email",
    limitation: null,
  });
});

test("existing-factor TOTP challenge input is strict and factor selection is fail-closed", () => {
  expect(parseTotpChallengeInput({
    factorId: "factor-totp-1",
    code: " 123456 ",
  })).toEqual({ factorId: "factor-totp-1", code: "123456" });
  expect(parseTotpChallengeInput({ factorId: "factor-totp-1", code: "12 3456" })).toBeNull();
  expect(parseTotpChallengeInput({ factorId: "factor/totp/1", code: "123456" })).toBeNull();

  const factors = [
    { id: "verified-totp", factor_type: "totp", status: "verified" },
    { id: "unverified-totp", factor_type: "totp", status: "unverified" },
    { id: "verified-phone", factor_type: "phone", status: "verified" },
  ];
  expect(findVerifiedTotpFactor(factors, "verified-totp")?.id).toBe("verified-totp");
  expect(findVerifiedTotpFactor(factors, "unverified-totp")).toBeNull();
  expect(findVerifiedTotpFactor(factors, "verified-phone")).toBeNull();
  expect(findVerifiedTotpFactor(factors, "missing-factor")).toBeNull();
});

test("settings upgrades an aal1 session with an existing verified TOTP factor", async ({ page }) => {
  let assuranceLevel: "aal1" | "aal2" = "aal1";
  let challengePayload: Record<string, unknown> | null = null;
  let identityLinkRequests = 0;

  await page.route("**/api/backend/auth/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          authenticated: true,
          user: {
            userId: "mfa-user",
            tenantId: "tenant-live",
            email: "mfa.user@example.com",
            displayName: "MFA User",
            role: "reviewer",
            status: "active",
          },
          session: { userId: "mfa-user", tenantId: "tenant-live" },
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
        providers: { google: true, kakao: false },
        providerUsable: { google: true, kakao: false },
        providerReady: { google: true, kakao: false },
        canonicalOriginReady: true,
        configurationReady: true,
        backend: { ready: true, membershipAuthority: "postgres" },
        operational: false,
        limitations: ["kakao_provider_not_enabled"],
      }),
    });
  });
  await page.route("**/api/auth/supabase/identity/link", async (route) => {
    identityLinkRequests += 1;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ error: "test_should_not_reach_link_bff" }),
    });
  });
  await page.route("**/api/auth/supabase/security", async (route) => {
    if (route.request().method() === "POST") {
      challengePayload = route.request().postDataJSON() as Record<string, unknown>;
      assuranceLevel = "aal2";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schemaVersion: "realytics-mfa-reauthentication-v1",
          verified: true,
          assuranceLevel: "aal2",
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schemaVersion: "realytics-auth-security-profile-v1",
        userId: "mfa-user",
        requestedProvider: null,
        identities: [],
        factors: [{
          factorId: "verified-totp",
          factorType: "totp",
          status: "verified",
          friendlyName: "Pilot Authenticator",
          createdAt: "2026-07-27T01:00:00Z",
          updatedAt: "2026-07-27T01:00:00Z",
        }],
        assurance: {
          currentLevel: assuranceLevel,
          nextLevel: "aal2",
          currentAuthenticationMethods: [],
          recentAal2: {
            eligible: assuranceLevel === "aal2",
            verifiedAt: assuranceLevel === "aal2" ? "2026-07-30T01:00:00Z" : null,
            limitation: assuranceLevel === "aal2" ? null : "aal2_step_up_required",
          },
        },
        totpEnrollment: {
          eligible: true,
          accountNameSource: "verified_email",
          limitation: null,
        },
        operationalForHighRiskActions: assuranceLevel === "aal2",
        limitations: assuranceLevel === "aal2" ? [] : ["aal2_step_up_required"],
      }),
    });
  });

  await page.goto("/settings");
  await expect(page.getByText("AAL2 추가 인증")).toBeVisible();
  await page.getByLabel("AAL2 일회용 코드").fill("123456");
  await page.getByRole("button", { name: "현재 세션 AAL2 인증" }).click();

  await expect.poll(() => challengePayload).toEqual({
    action: "mfa_reauthenticate_totp",
    factorId: "verified-totp",
    code: "123456",
  });
  await expect(page.getByText("현재 세션: aal2")).toBeVisible();
  await expect(page.getByLabel("AAL2 일회용 코드")).toHaveCount(0);
  let confirmationSeen = false;
  page.once("dialog", async (dialog) => {
    confirmationSeen = dialog.message().includes("Google 계정");
    await dialog.dismiss();
  });
  await page.getByRole("button", { name: "Google 연결" }).click();
  await expect.poll(() => confirmationSeen).toBe(true);
  expect(identityLinkRequests).toBe(0);
  await expect.poll(async () => page.evaluate(() => (
    JSON.stringify({ ...localStorage, ...sessionStorage }).includes("123456")
  ))).toBe(false);
});
