import { expect, test, type Page } from "@playwright/test";
import { accountDisplayName } from "../lib/account-profile";

const UUID = "11111111-2222-3333-4444-555555555555";

async function fixture(page: Page) {
  const state = { displayName: UUID, saveStatus: 200, calls: [] as unknown[], invalidResponse: false };
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (path.endsWith("/auth/me")) return reply({ authenticated: true, authSource: "bearer_token",
      user: { userId: UUID, tenantId: "profile-tenant", displayName: state.displayName, role: "analyst", status: "active" },
      session: { userId: UUID, tenantId: "profile-tenant" }, permissions: ["project:read", "project:write"] });
    if (path.endsWith("/auth/profile")) {
      const payload = route.request().postDataJSON(); state.calls.push(payload);
      expect(route.request().method()).toBe("PUT");
      expect(Object.keys(payload)).toEqual(["displayName"]);
      if (state.saveStatus !== 200) return reply({ detail: { error: "account_profile_authority_unavailable" } }, state.saveStatus);
      if (state.invalidResponse) return reply({});
      state.displayName = payload.displayName;
      return reply({ schemaVersion: "realytics-account-profile-v1", displayName: state.displayName });
    }
    if (path.endsWith("/projects/recent-work")) return reply({ items: [] });
    if (path.endsWith("/projects")) return reply([]);
    return reply({ detail: "not_mocked" }, 404);
  });
  await page.goto("/dashboard");
  const trigger = page.getByRole("button", { name: "내 계정 메뉴", exact: true });
  await expect(trigger).toBeVisible();
  await trigger.click();
  await expect(page.getByText(UUID, { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "이름 변경", exact: true }).click();
  return { state, trigger, form: page.getByRole("form", { name: "표시 이름 변경", exact: true }) };
}

test("internal account identifiers never become display names", () => {
  for (const name of [UUID, `https://issuer.test/auth/v1::${UUID}`, "internal-user"]) {
    expect(accountDisplayName({ userId: "internal-user", displayName: name })).toBe("내 계정");
  }
  expect(accountDisplayName({ userId: UUID, displayName: "  직접 정한 이름  " })).toBe("직접 정한 이름");
});

test("profile updates require a same-origin authenticated session", async ({ request }) => {
  const unauthenticated = await request.put("/api/backend/auth/profile", {
    headers: { "X-User-Id": UUID, "X-Role": "admin" }, data: { displayName: "새 이름" },
  });
  expect(unauthenticated.status()).toBe(401);
  const crossOrigin = await request.put("/api/backend/auth/profile", {
    headers: { Origin: "https://other.example" }, data: { displayName: "새 이름" },
  });
  expect(crossOrigin.status()).toBe(403);
});

test("account name can be saved in the menu and survives a page reload", async ({ page }) => {
  const { state, trigger, form } = await fixture(page);
  const input = form.getByLabel("표시 이름", { exact: true });
  await expect(input).toHaveValue("");
  await expect(input).toBeFocused();
  await input.fill("  이름 검토자  ");
  await form.getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(form.getByRole("status")).toHaveText("이름을 저장했습니다.");
  await expect(trigger).toContainText("이름 검토자");
  expect(state.calls).toEqual([{ displayName: "이름 검토자" }]);
  await page.reload();
  await trigger.click();
  await expect(page.getByText("이름 검토자", { exact: true }).last()).toBeVisible();
  await expect(page.getByText(UUID, { exact: true })).toHaveCount(0);
});

test("failed or invalid saves retain the draft and never claim success", async ({ page }) => {
  const { state, form } = await fixture(page);
  const input = form.getByLabel("표시 이름", { exact: true });
  await input.fill("재시도할 이름");
  state.saveStatus = 503;
  await form.getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("저장하지 못했습니다");
  await expect(input).toHaveValue("재시도할 이름");
  await expect(form.getByRole("status")).toHaveCount(0);
  state.saveStatus = 200; state.invalidResponse = true;
  await form.getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(form.getByRole("alert")).toContainText("저장 결과를 확인하지 못했습니다");
  await expect(form.getByRole("status")).toHaveCount(0);
  state.invalidResponse = false;
  await form.getByRole("button", { name: "이름 저장", exact: true }).click();
  await expect(form.getByRole("status")).toHaveText("이름을 저장했습니다.");
});

test("blank and UUID names do not issue a save request", async ({ page }) => {
  const { state, form } = await fixture(page);
  for (const value of ["   ", UUID]) {
    await form.getByLabel("표시 이름", { exact: true }).fill(value);
    await form.getByRole("button", { name: "이름 저장", exact: true }).click();
    await expect(form.getByRole("alert")).toContainText("계정 번호 대신");
  }
  expect(state.calls).toEqual([]);
});
