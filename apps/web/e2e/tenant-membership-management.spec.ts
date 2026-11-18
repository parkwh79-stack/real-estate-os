import { expect, test, type Page } from "@playwright/test";

type PatchResult = "success" | "conflict" | "step_up";

async function mockMembershipSettings(page: Page, patchResult: PatchResult) {
  const issuer = "https://project.supabase.co/auth/v1";
  let targetRole = "reviewer";
  let targetVersion = 1;
  let listCalls = 0;
  let patchPayload: Record<string, unknown> | null = null;

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
            userId: "current-subject",
            tenantId: "tenant-live",
            email: "admin@example.com",
            displayName: "Tenant Admin",
            role: "admin",
            status: "active",
            metadata: { issuer, subject: "current-subject", principalType: "human" },
          },
          session: { userId: "current-subject", tenantId: "tenant-live" },
          permissions: ["user:manage", "project:read", "project:write"],
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
    if (path.endsWith("/auth/memberships") && request.method() === "GET") {
      listCalls += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schemaVersion: "realytics-tenant-membership-list-v1",
          memberships: [
            {
              tenantId: "tenant-live",
              issuer,
              subject: "current-subject",
              principalType: "human",
              principalStatus: "active",
              email: "admin@example.com",
              displayName: "Tenant Admin",
              role: "admin",
              status: "active",
              membershipVersion: 4,
              updatedAt: "2026-07-30T01:00:00Z",
            },
            {
              tenantId: "tenant-live",
              issuer,
              subject: "target-subject",
              principalType: "human",
              principalStatus: "active",
              email: "reviewer@example.com",
              displayName: "PF Reviewer",
              role: targetRole,
              status: "active",
              membershipVersion: targetVersion,
              updatedAt: "2026-07-30T02:00:00Z",
            },
          ],
        }),
      });
      return;
    }
    if (path.endsWith("/auth/memberships/role") && request.method() === "PATCH") {
      patchPayload = request.postDataJSON() as Record<string, unknown>;
      if (patchResult === "conflict") {
        targetRole = "analyst";
        targetVersion = 2;
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({ detail: { error: "membership_version_conflict" } }),
        });
        return;
      }
      if (patchResult === "step_up") {
        await route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              error: "step_up_required",
              action: "tenant_membership_role_change",
              reason: "aal2_required",
            },
          }),
        });
        return;
      }
      targetRole = String(patchPayload.role ?? "analyst");
      targetVersion += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schemaVersion: "realytics-tenant-membership-role-change-v1",
          membership: {
            tenantId: "tenant-live",
            issuer,
            subject: "target-subject",
            principalType: "human",
            principalStatus: "active",
            email: "reviewer@example.com",
            displayName: "PF Reviewer",
            role: targetRole,
            status: "active",
            membershipVersion: targetVersion,
            updatedAt: "2026-07-30T03:00:00Z",
          },
        }),
      });
      return;
    }
    if (path.endsWith("/auth/invitations") && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ invitations: [] }),
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
        identities: [],
        factors: [],
        assurance: {
          currentLevel: "aal2",
          nextLevel: "aal2",
          currentAuthenticationMethods: [],
        },
        totpEnrollment: {
          eligible: true,
          accountNameSource: "verified_email",
          limitation: null,
        },
        operationalForHighRiskActions: true,
        limitations: [],
      }),
    });
  });

  return {
    getListCalls: () => listCalls,
    getPatchPayload: () => patchPayload,
  };
}

test("admin changes another provider-neutral membership role with version and reason", async ({ page }) => {
  const state = await mockMembershipSettings(page, "success");
  await page.goto("/settings");

  await expect(page.getByRole("heading", { name: "테넌트 사용자 역할 관리" })).toBeVisible();
  const selfRow = page.getByTestId("membership-row-current-subject");
  await expect(selfRow.getByText("현재 사용자 · 자기 역할 변경 차단")).toBeVisible();
  await expect(selfRow.getByLabel("Tenant Admin 변경할 역할")).toBeDisabled();

  const targetRow = page.getByTestId("membership-row-target-subject");
  await expect(targetRow.getByText("https://project.supabase.co/auth/v1")).toBeVisible();
  await targetRow.getByLabel("PF Reviewer 변경할 역할").selectOption("analyst");
  await targetRow.getByLabel("PF Reviewer 역할 변경 사유").fill("PF 분석 업무 배정 승인");
  await targetRow.getByRole("button", { name: "역할 변경" }).click();

  await expect.poll(() => state.getPatchPayload()).toEqual({
    targetIssuer: "https://project.supabase.co/auth/v1",
    targetSubject: "target-subject",
    expectedMembershipVersion: 1,
    role: "analyst",
    reason: "PF 분석 업무 배정 승인",
  });
  await expect(page.getByText(/PF Reviewer의 역할을 분석 담당/)).toBeVisible();
  await expect(targetRow.getByLabel("PF Reviewer 변경할 역할")).toHaveValue("analyst");
  await expect.poll(() => state.getListCalls()).toBeGreaterThanOrEqual(2);
});

test("membership version conflict reloads the authoritative row", async ({ page }) => {
  const state = await mockMembershipSettings(page, "conflict");
  await page.goto("/settings");

  const targetRow = page.getByTestId("membership-row-target-subject");
  await targetRow.getByLabel("PF Reviewer 변경할 역할").selectOption("admin");
  await targetRow.getByLabel("PF Reviewer 역할 변경 사유").fill("관리자 업무 이관");
  await targetRow.getByRole("button", { name: "역할 변경" }).click();

  await expect(page.getByTestId("membership-management-error")).toContainText(
    "다른 관리자가 먼저 이 사용자의 역할을 변경했습니다.",
  );
  await expect.poll(() => state.getListCalls()).toBeGreaterThanOrEqual(2);
  await expect(targetRow.getByLabel("PF Reviewer 변경할 역할")).toHaveValue("analyst");
});

test("stale AAL2 is reported as an explicit step-up requirement", async ({ page }) => {
  await mockMembershipSettings(page, "step_up");
  await page.goto("/settings");

  const targetRow = page.getByTestId("membership-row-target-subject");
  await targetRow.getByLabel("PF Reviewer 변경할 역할").selectOption("analyst");
  await targetRow.getByLabel("PF Reviewer 역할 변경 사유").fill("분석 업무 배정");
  await targetRow.getByRole("button", { name: "역할 변경" }).click();

  await expect(page.getByTestId("membership-management-error")).toContainText(
    "역할 변경에는 최근 TOTP AAL2 추가 인증이 필요합니다.",
  );
});
