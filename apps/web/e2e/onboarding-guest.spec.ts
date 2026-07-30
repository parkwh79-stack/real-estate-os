import { expect, test, type Page } from "@playwright/test";

test("guest walkthrough stays public, sample-only, and has no upload control", async ({ page }) => {
  const backendRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/backend/")) backendRequests.push(request.url());
  });

  await page.goto("/guest");

  await expect(page.getByTestId("guest-sample-only")).toContainText("샘플 데이터 전용");
  await expect(page.getByTestId("guest-upload-disabled")).toBeDisabled();
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await expect(page.getByText(/실제 프로젝트·Tenant·문서 API를 호출하지 않으며/)).toBeVisible();
  expect(backendRequests).toEqual([]);
});

async function mockMemberlessOnboarding(
  page: Page,
  initialProfile: Record<string, unknown> | null = null,
) {
  let profile: Record<string, unknown> | null = initialProfile;
  let profilePayload: Record<string, unknown> | null = null;
  let requestPayload: Record<string, unknown> | null = null;

  await page.route("**/api/backend/auth/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: { error: "principal_not_registered" } }),
      });
      return;
    }
    if (path.endsWith("/auth/onboarding/status")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accessState: "onboarding_required",
          profile,
          requests: [],
        }),
      });
      return;
    }
    if (path.endsWith("/auth/onboarding/profile") && request.method() === "PUT") {
      profilePayload = request.postDataJSON() as Record<string, unknown>;
      profile = {
        displayName: profilePayload.displayName,
        phone: profilePayload.phone,
        jobTitle: profilePayload.jobTitle,
        termsVersion: profilePayload.termsVersion,
        privacyVersion: profilePayload.privacyVersion,
        profileVersion: 1,
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ profile }),
      });
      return;
    }
    if (path.endsWith("/auth/onboarding/requests") && request.method() === "POST") {
      requestPayload = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          request: {
            requestId: "request-new-1",
            requestType: "new_organization",
            organizationId: null,
            organizationName: requestPayload.organizationName,
            businessRegistrationNumber: requestPayload.businessRegistrationNumber,
            requestedRole: "analyst",
            message: requestPayload.message,
            status: "pending",
            requestVersion: 1,
            decisionReason: null,
            submittedAt: "2026-07-30T00:00:00Z",
            decisionAt: null,
            updatedAt: "2026-07-30T00:00:00Z",
          },
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
  await page.route("**/api/auth/supabase/security", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        userId: "memberless-subject",
        identities: [{ identityId: "email-1", provider: "email", email: "new@example.com", createdAt: null, lastSignInAt: null }],
        factors: [],
        assurance: { currentLevel: "aal1", nextLevel: "aal2", currentAuthenticationMethods: [] },
        totpEnrollment: { eligible: true, accountNameSource: "verified_email", limitation: null },
        operationalForHighRiskActions: false,
        limitations: [],
      }),
    });
  });

  return {
    getProfilePayload: () => profilePayload,
    getRequestPayload: () => requestPayload,
  };
}

test("verified memberless user saves a versioned onboarding profile", async ({ page }) => {
  const state = await mockMemberlessOnboarding(page);
  await page.goto("/onboarding");

  await expect(page.getByRole("heading", { name: "기관 워크스페이스 가입" })).toBeVisible();
  await expect(page.getByText("new@example.com")).toBeVisible();
  await page.getByLabel("표시명").fill("신규 분석 담당자");
  await page.getByLabel(/이용약관 realytics-terms-2026-07-30/).check();
  await page.getByLabel(/개인정보 처리방침 realytics-privacy-2026-07-30/).check();
  await page.getByRole("button", { name: "프로필·동의 저장" }).click();

  await expect.poll(() => state.getProfilePayload()).toEqual({
    displayName: "신규 분석 담당자",
    phone: null,
    jobTitle: null,
    termsAccepted: true,
    privacyAccepted: true,
    termsVersion: "realytics-terms-2026-07-30",
    privacyVersion: "realytics-privacy-2026-07-30",
    expectedProfileVersion: 0,
  });
  await expect(page.getByText("프로필과 현재 정책 동의를 안전하게 저장했습니다.")).toBeVisible();
});

test("policy mismatch reloads authoritative versions and requires fresh consent", async ({ page }) => {
  let termsVersion = "realytics-terms-2026-07-30";
  let privacyVersion = "realytics-privacy-2026-07-30";
  let putCalls = 0;
  let acceptedPayload: Record<string, unknown> | null = null;

  await page.route("**/api/backend/auth/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      // Historical API revisions use 503 for an explicitly missing
      // principal. The memberless Supabase session must still reach
      // onboarding instead of bouncing back to login.
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: { error: "principal_not_registered" } }) });
      return;
    }
    if (path.endsWith("/auth/onboarding/status")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ accessState: "onboarding_required", profile: null, requests: [], policyVersions: { termsVersion, privacyVersion } }) });
      return;
    }
    if (path.endsWith("/auth/onboarding/profile") && request.method() === "PUT") {
      putCalls += 1;
      if (putCalls === 1) {
        termsVersion = "realytics-terms-2026-08-01";
        privacyVersion = "realytics-privacy-2026-08-01";
        await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { error: "terms_version_mismatch", currentPolicyVersions: { termsVersion, privacyVersion } } }) });
        return;
      }
      acceptedPayload = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ profile: { displayName: "정책 확인자", phone: null, jobTitle: null, termsVersion, privacyVersion, profileVersion: 1 } }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_mocked" }) });
  });
  await page.route("**/api/auth/supabase/security", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ userId: "policy-subject", identities: [{ identityId: "email-1", provider: "email", email: "policy@example.com", createdAt: null, lastSignInAt: null }], factors: [], assurance: { currentLevel: "aal1", nextLevel: "aal2", currentAuthenticationMethods: [] }, totpEnrollment: { eligible: true, accountNameSource: "verified_email", limitation: null }, operationalForHighRiskActions: false, limitations: [] }) });
  });

  await page.goto("/onboarding");
  await page.getByLabel("표시명").fill("정책 확인자");
  await page.getByLabel(/이용약관 realytics-terms-2026-07-30/).check();
  await page.getByLabel(/개인정보 처리방침 realytics-privacy-2026-07-30/).check();
  await page.getByRole("button", { name: "프로필·동의 저장" }).click();

  await expect(page.getByText("이용약관이 개정되었습니다. 최신 내용을 다시 확인하고 동의해 주세요.")).toBeVisible();
  const newTerms = page.getByLabel(/이용약관 realytics-terms-2026-08-01/);
  const newPrivacy = page.getByLabel(/개인정보 처리방침 realytics-privacy-2026-08-01/);
  await expect(newTerms).not.toBeChecked();
  await expect(newPrivacy).not.toBeChecked();
  await expect(page.getByRole("button", { name: "프로필·동의 저장" })).toBeDisabled();

  await newTerms.check();
  await newPrivacy.check();
  await page.getByRole("button", { name: "프로필·동의 저장" }).click();
  await expect.poll(() => acceptedPayload).toMatchObject({
    termsVersion: "realytics-terms-2026-08-01",
    privacyVersion: "realytics-privacy-2026-08-01",
    termsAccepted: true,
    privacyAccepted: true,
  });
});

test("new organization normalizes a required business number and does not request a role", async ({ page }) => {
  const state = await mockMemberlessOnboarding(page, {
    displayName: "기관 설립자",
    phone: null,
    jobTitle: "대표",
    termsVersion: "realytics-terms-2026-07-30",
    privacyVersion: "realytics-privacy-2026-07-30",
    profileVersion: 1,
  });
  await page.goto("/onboarding");

  await page.getByLabel("기관 검색").fill("금융");
  await page.getByRole("button", { name: "검색", exact: true }).click();
  await expect(page.getByText("기관명 세 글자 이상을 입력해 주세요.")).toBeVisible();

  await expect(page.getByText("승인 시 최초 기관 관리자", { exact: true })).toBeVisible();
  await page.getByLabel("기관명").fill("신규자산운용");
  await page.getByLabel("사업자등록번호(필수)").fill("123-45-67890");
  await page.getByRole("button", { name: "신규 기관 검토 요청" }).click();

  await expect.poll(() => state.getRequestPayload()).toEqual({
    requestType: "new_organization",
    organizationName: "신규자산운용",
    businessRegistrationNumber: "1234567890",
    message: null,
  });
});

test("multi-tenant Supabase user selects a FastAPI-verified workspace without a login loop", async ({ page }) => {
  let selectedTenant: string | null = null;
  await page.route("**/api/backend/auth/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: { error: "tenant_selection_required" } }) });
      return;
    }
    if (path.endsWith("/auth/onboarding/status")) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          accessState: "member",
          profile: null,
          requests: [],
          memberships: [
            { organizationId: "tenant-a", organizationName: "알파자산운용", role: "analyst", status: "active", membershipVersion: 2, organizationStatus: "active", updatedAt: "2026-07-30T00:00:00Z" },
            { organizationId: "tenant-b", organizationName: "베타금융", role: "viewer", status: "active", membershipVersion: 4, organizationStatus: "active", updatedAt: "2026-07-30T00:00:00Z" },
          ],
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_mocked" }) });
  });
  await page.route("**/api/auth/supabase/security", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ userId: "multi-sub", identities: [{ identityId: "google-1", provider: "google", email: "multi@example.com", createdAt: null, lastSignInAt: null }], factors: [], assurance: { currentLevel: "aal1", nextLevel: "aal2", currentAuthenticationMethods: [] }, totpEnrollment: { eligible: true, accountNameSource: "verified_email", limitation: null }, operationalForHighRiskActions: false, limitations: [] }) });
  });
  await page.route("**/api/auth/supabase/tenant", async (route) => {
    selectedTenant = (route.request().postDataJSON() as { tenantId: string }).tenantId;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ selected: true, tenantId: selectedTenant }) });
  });

  await page.goto("/onboarding");
  await expect(page.getByRole("heading", { name: "접속할 워크스페이스를 선택하세요" })).toBeVisible();
  await expect(page).toHaveURL(/\/onboarding/);
  await page.getByTestId("tenant-option-tenant-b").click();
  await expect.poll(() => selectedTenant).toBe("tenant-b");
});

test("valid Supabase session fails closed when the PostgreSQL identity authority is unavailable", async ({ page }) => {
  let onboardingStatusRequests = 0;
  await page.route("**/api/backend/auth/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ error: "backend_unavailable", requestId: "request-1" }) });
      return;
    }
    if (path.endsWith("/auth/onboarding/status")) onboardingStatusRequests += 1;
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "identity_authority_unavailable" }) });
  });
  await page.route("**/api/auth/supabase/security", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ userId: "valid-sub", identities: [{ identityId: "email-1", provider: "email", email: "valid@example.com", createdAt: null, lastSignInAt: null }], factors: [], assurance: { currentLevel: "aal1", nextLevel: "aal2", currentAuthenticationMethods: [] }, totpEnrollment: { eligible: true, accountNameSource: "verified_email", limitation: null }, operationalForHighRiskActions: false, limitations: [] }) });
  });

  await page.goto("/onboarding");
  await expect(page.getByTestId("identity-authority-unavailable")).toBeVisible();
  await expect(page.getByText("로그인 세션은 유효하지만 PostgreSQL 멤버십 권위 서비스가 응답하지 않습니다.")).toBeVisible();
  await expect(page).toHaveURL(/\/onboarding/);
  expect(onboardingStatusRequests).toBe(0);
});

test("each rejected access request resubmits from its own persisted snapshot", async ({ page }) => {
  const payloads = new Map<string, Record<string, unknown>>();
  const requests = [
    { requestId: "rejected-existing", requestType: "existing_organization", organizationId: "tenant-1", organizationName: "기존금융", businessRegistrationNumber: null, requestedRole: "viewer", message: "기존 신청 메시지", status: "rejected", requestVersion: 3, decisionReason: "역할 보완", submittedAt: "2026-07-30T00:00:00Z", decisionAt: "2026-07-30T01:00:00Z", updatedAt: "2026-07-30T01:00:00Z" },
    { requestId: "rejected-new", requestType: "new_organization", organizationId: null, organizationName: "스냅샷자산운용", businessRegistrationNumber: "9876543210", requestedRole: "analyst", message: "신규 신청 메시지", status: "rejected", requestVersion: 6, decisionReason: "증빙 보완", submittedAt: "2026-07-30T00:00:00Z", decisionAt: "2026-07-30T01:00:00Z", updatedAt: "2026-07-30T01:00:00Z" },
  ];
  await page.route("**/api/backend/auth/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      await route.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ detail: { error: "principal_not_registered" } }) });
      return;
    }
    if (path.endsWith("/auth/onboarding/status")) {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ accessState: "onboarding_required", profile: { displayName: "재신청자", phone: null, jobTitle: null, termsVersion: "realytics-terms-2026-07-30", privacyVersion: "realytics-privacy-2026-07-30", profileVersion: 2 }, requests }) });
      return;
    }
    const match = path.match(/\/auth\/onboarding\/requests\/([^/]+)\/resubmit$/);
    if (match?.[1] && request.method() === "POST") {
      const requestId = match[1];
      payloads.set(requestId, request.postDataJSON() as Record<string, unknown>);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ request: { ...requests.find((item) => item.requestId === requestId), status: "pending" } }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_mocked" }) });
  });
  await page.route("**/api/auth/supabase/security", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ userId: "rejected-sub", identities: [{ identityId: "email-1", provider: "email", email: "rejected@example.com", createdAt: null, lastSignInAt: null }], factors: [], assurance: { currentLevel: "aal1", nextLevel: "aal2", currentAuthenticationMethods: [] }, totpEnrollment: { eligible: true, accountNameSource: "verified_email", limitation: null }, operationalForHighRiskActions: false, limitations: [] }) });
  });

  await page.goto("/onboarding");
  const existingForm = page.getByTestId("resubmit-form-rejected-existing");
  await existingForm.getByLabel("요청 역할").selectOption("analyst");
  await existingForm.getByLabel("신청 메시지(선택)").fill("기존 신청만 보완");
  await existingForm.getByRole("button", { name: "이 보완값으로 재제출" }).click();
  await expect.poll(() => payloads.get("rejected-existing")).toEqual({ expectedRequestVersion: 3, requestedRole: "analyst", message: "기존 신청만 보완" });

  const newForm = page.getByTestId("resubmit-form-rejected-new");
  await newForm.getByRole("button", { name: "이 보완값으로 재제출" }).click();
  await expect.poll(() => payloads.get("rejected-new")).toEqual({ expectedRequestVersion: 6, message: "신규 신청 메시지", organizationName: "스냅샷자산운용", businessRegistrationNumber: "9876543210" });
});
