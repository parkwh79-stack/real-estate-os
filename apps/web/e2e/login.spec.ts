import { expect, test } from "@playwright/test";

test("unauthenticated users can reach the real login entry point", async ({ page }) => {
  await page.goto("/login");

  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByTestId("login-heading")).toBeVisible();
  await expect(page.getByLabel("이메일")).toBeVisible();
  await expect(page.getByLabel("비밀번호", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "비회원으로 체험" })).toBeVisible();
  await expect(page.getByText(/Google·Kakao는 가입과 기관 승인이 끝난 뒤/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Google 계정으로 로그인/ })).toHaveCount(0);
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
  await expect(page.getByRole("status")).toContainText("가입 요청이라면 메일이 발송");
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
