import { expect, test } from "@playwright/test";
import {
  findVerifiedTotpFactor,
  parseTotpChallengeInput,
} from "../lib/supabase/mfa-security";

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
  await expect.poll(async () => page.evaluate(() => (
    JSON.stringify({ ...localStorage, ...sessionStorage }).includes("123456")
  ))).toBe(false);
});
