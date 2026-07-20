import { expect, test } from "@playwright/test";

test("unauthenticated users can reach the real login entry point", async ({ page }) => {
  await page.goto("/login");

  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByTestId("login-heading")).toBeVisible();
  await expect(page.getByText("기본 제공: Gemini 무료 fallback")).toBeVisible();
});
