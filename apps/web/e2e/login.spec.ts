import { expect, test } from "@playwright/test";

test("unauthenticated users can reach the real login entry point", async ({ page }) => {
  await page.goto("/login");

  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { name: "보안 로그인" })).toBeVisible();
  await expect(page.getByText("기본 제공: Gemini 무료 fallback")).toBeVisible();
});
