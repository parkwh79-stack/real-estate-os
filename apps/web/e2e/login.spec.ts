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
