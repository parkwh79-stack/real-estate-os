import { expect, test } from "@playwright/test";

test("AAL2 tenant administrator approves a pending existing-organization request with CAS", async ({ page }) => {
  let pending = true;
  let decisionPayload: Record<string, unknown> | null = null;
  const listTenantIds: Array<string | null> = [];
  const issuer = "https://project.supabase.co/auth/v1";

  await page.route("**/api/backend/auth/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authenticated: true, user: { userId: "admin-sub", tenantId: "tenant-1", email: "admin@example.com", displayName: "관리자", role: "admin", metadata: { issuer, subject: "admin-sub" } }, session: { userId: "admin-sub", tenantId: "tenant-1" }, permissions: ["user:manage"], authSource: "bearer_token" }) });
      return;
    }
    if (path.endsWith("/auth/access-requests") && request.method() === "GET") {
      listTenantIds.push(new URL(request.url()).searchParams.get("tenantId"));
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ requests: pending ? [{ requestId: "req-1", requestType: "existing_organization", organizationId: "tenant-1", organizationName: "샘플금융", businessRegistrationNumber: null, requestedRole: "analyst", message: "PF 분석 업무를 담당합니다.", status: "pending", requestVersion: 3, decisionReason: null, submittedAt: "2026-07-30T01:00:00Z", decisionAt: null, updatedAt: "2026-07-30T01:00:00Z", requesterSubject: "applicant-sub", requesterEmail: "analyst@example.com" }] : [] }) });
      return;
    }
    if (path.endsWith("/auth/onboarding/status")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ accessState: "member", profile: null, requests: [], platformOperator: false }) });
      return;
    }
    if (path.endsWith("/auth/access-requests/req-1/decision") && request.method() === "POST") {
      decisionPayload = request.postDataJSON() as Record<string, unknown>;
      pending = false;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ request: { requestId: "req-1", status: "approved", requestVersion: 4 } }) });
      return;
    }
    if (path.endsWith("/auth/ai-profile")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ profile: null, effectiveAi: {} }) });
      return;
    }
    if (path.endsWith("/auth/memberships")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schemaVersion: "realytics-tenant-membership-list-v1", memberships: [] }) });
      return;
    }
    if (path.endsWith("/auth/invitations")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ invitations: [] }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_mocked" }) });
  });
  await page.route("**/api/auth/supabase/readiness", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ configured: true, providers: { google: true, kakao: true }, providerUsable: { google: true, kakao: true }, providerReady: { google: true, kakao: true }, canonicalOriginReady: true, backend: { ready: true }, operational: true, limitations: [] }) }));
  await page.route("**/api/auth/supabase/security", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ userId: "admin-sub", identities: [], factors: [], assurance: { currentLevel: "aal2", nextLevel: "aal2", currentAuthenticationMethods: [] }, totpEnrollment: { eligible: true, accountNameSource: "verified_email", limitation: null }, operationalForHighRiskActions: true, limitations: [] }) }));

  await page.goto("/settings");
  const panel = page.getByTestId("onboarding-access-request-management");
  await expect(panel.getByText("analyst@example.com")).toBeVisible();
  await panel.getByLabel("결정 사유").fill("기관 재직과 분석 역할을 확인했습니다.");
  await panel.getByRole("button", { name: "승인", exact: true }).click();

  await expect.poll(() => decisionPayload).toEqual({ decision: "approve", expectedRequestVersion: 3, assignedRole: "analyst", reason: "기관 재직과 분석 역할을 확인했습니다." });
  expect(listTenantIds.every((tenantId) => tenantId === "tenant-1")).toBe(true);
  await expect(panel.getByText("대기 중인 가입 신청이 없습니다")).toBeVisible();
});

test("platform operator approves a new organization with a fixed initial admin membership", async ({ page }) => {
  let pending = true;
  let decisionPayload: Record<string, unknown> | null = null;
  const listTenantIds: Array<string | null> = [];
  const reviewScopes: Array<string | null> = [];
  const issuer = "https://project.supabase.co/auth/v1";

  await page.route("**/api/backend/auth/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authenticated: true, user: { userId: "operator-sub", tenantId: "platform-ops", email: "operator@example.com", displayName: "플랫폼 운영자", role: "viewer", metadata: { issuer, subject: "operator-sub" } }, session: { userId: "operator-sub", tenantId: "platform-ops" }, permissions: [], authSource: "bearer_token" }) });
      return;
    }
    if (path.endsWith("/auth/access-requests") && request.method() === "GET") {
      const tenantId = new URL(request.url()).searchParams.get("tenantId");
      reviewScopes.push(new URL(request.url()).searchParams.get("reviewScope"));
      listTenantIds.push(tenantId);
      const platformRequests = tenantId === null && pending ? [{ requestId: "req-new", requestType: "new_organization", organizationId: null, organizationName: "신규자산운용", businessRegistrationNumber: "1234567890", requestedRole: "analyst", message: "신규 기관 개설을 요청합니다.", status: "pending", requestVersion: 1, decisionReason: null, submittedAt: "2026-07-30T01:00:00Z", decisionAt: null, updatedAt: "2026-07-30T01:00:00Z", requesterSubject: "founder-sub", requesterEmail: "founder@example.com" }] : [];
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ reviewScope: tenantId ? "tenant" : "platform", requests: platformRequests }) });
      return;
    }
    if (path.endsWith("/auth/onboarding/status")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ accessState: "member", profile: null, requests: [], platformOperator: true }) });
      return;
    }
    if (path.endsWith("/auth/access-requests/req-new/decision") && request.method() === "POST") {
      decisionPayload = request.postDataJSON() as Record<string, unknown>;
      pending = false;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ request: { requestId: "req-new", status: "approved", requestVersion: 2 } }) });
      return;
    }
    if (path.endsWith("/auth/ai-profile")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ profile: null, effectiveAi: {} }) });
      return;
    }
    if (path.endsWith("/auth/memberships")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ schemaVersion: "realytics-tenant-membership-list-v1", memberships: [] }) });
      return;
    }
    if (path.endsWith("/auth/invitations")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ invitations: [] }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_mocked" }) });
  });
  await page.route("**/api/auth/supabase/readiness", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ configured: true, providers: { google: true, kakao: true }, providerUsable: { google: true, kakao: true }, providerReady: { google: true, kakao: true }, canonicalOriginReady: true, backend: { ready: true }, operational: true, limitations: [] }) }));
  await page.route("**/api/auth/supabase/security", async (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ userId: "operator-sub", identities: [], factors: [], assurance: { currentLevel: "aal2", nextLevel: "aal2", currentAuthenticationMethods: [] }, totpEnrollment: { eligible: true, accountNameSource: "verified_email", limitation: null }, operationalForHighRiskActions: true, limitations: [] }) }));

  await page.goto("/settings");
  const panel = page.getByTestId("onboarding-access-request-management");
  await expect(panel).toBeVisible();
  await expect(panel.getByText("Platform operator queue")).toBeVisible();
  const row = page.getByTestId("access-request-req-new");
  await expect(row.getByText("신규 기관 신청 · 승인 시 최초 기관 관리자")).toBeVisible();
  await expect(row.getByText("123-45-67890")).toBeVisible();
  await expect(row.getByText("미검증—승인 전 확인 필요")).toBeVisible();
  await expect(row.getByText("증빙 미첨부")).toBeVisible();
  await expect(row.locator('select[aria-label="발급 역할"]')).toHaveCount(0);
  await expect(row.getByLabel("발급 역할")).toHaveText("최초 기관 관리자");
  await row.getByLabel("결정 사유").fill("사업자 정보와 개설 권한을 확인했습니다.");
  await row.getByRole("button", { name: "승인", exact: true }).click();

  await expect.poll(() => decisionPayload).toEqual({ decision: "approve", expectedRequestVersion: 1, assignedRole: "admin", reason: "사업자 정보와 개설 권한을 확인했습니다." });
  expect(listTenantIds.every((tenantId) => tenantId === null)).toBe(true);
  expect(reviewScopes.every((scope) => scope === "platform")).toBe(true);
});
