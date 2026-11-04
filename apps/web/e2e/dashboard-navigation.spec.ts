import { expect, test, type Page } from "@playwright/test";

const PROJECTS = [
  { project: { id: "bonri-a", name: "본리동 A" }, land: { address: "대구 본리동 1" } },
  { project: { id: "bonri-b", name: "본리동 B" }, land: { address: "대구 본리동 2" } },
];

async function fixture(page: Page) {
  const state = { signedIn: true, signoutStatus: 200, signouts: 0, searches: 0, searchGate: Promise.resolve(), searchResponse: PROJECTS as unknown };
  await page.addInitScript(() => localStorage.setItem("realytics.selected.project.v1", "chosen-project"));
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path.endsWith("/auth/me")) return reply(state.signedIn ? {
      authenticated: true, user: { userId: "navigation-user", tenantId: "navigation-tenant", displayName: "메뉴 검토자", role: "analyst" },
      session: { userId: "navigation-user", tenantId: "navigation-tenant" }, permissions: ["project:read", "project:write"], authSource: "bearer_token",
    } : { authenticated: false });
    if (path.endsWith("/projects/recent-work")) return reply({ items: [] });
    if (path.endsWith("/projects")) return reply(PROJECTS);
    if (path.endsWith("/projects/")) { state.searches += 1; await state.searchGate; return reply(state.searchResponse); }
    if (path.endsWith("/auth/ai-credentials/current")) return reply({ revoked: true });
    return reply({ detail: "not_mocked" }, 404);
  });
  await page.route("**/api/auth/supabase/signout", async (route) => {
    state.signouts += 1;
    if (state.signoutStatus === 200) state.signedIn = false;
    await route.fulfill({ status: state.signoutStatus, contentType: "application/json", body: JSON.stringify({ signedOut: state.signoutStatus === 200 }) });
  });
  await page.route("**/api/auth/supabase/readiness", async (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ configured: true, settingsReachable: true,
      providers: { google: false, kakao: false, naver: false }, providerReady: { google: false, kakao: false, naver: false },
      providerUsable: { google: false, kakao: false, naver: false }, canonicalOriginReady: true, backend: { ready: true }, limitations: [] }),
  }));
  await page.goto("/dashboard");
  await expect(page.getByRole("button", { name: "내 계정 메뉴", exact: true })).toBeVisible();
  return state;
}

test("navigation groups keep existing tools and the selected project reachable", async ({ page, viewport }) => {
  await fixture(page);
  const mobile = (viewport?.width ?? 1280) < 1024;
  if (mobile) await page.getByRole("button", { name: "메뉴 열기", exact: true }).click();
  const navigation = mobile
    ? page.getByRole("dialog", { name: "REALYTICS 메뉴", exact: true }).getByRole("navigation", { name: "주 메뉴", exact: true })
    : page.locator("aside").getByRole("navigation", { name: "주 메뉴", exact: true });
  await expect(navigation.getByRole("link", { name: "내 프로젝트", exact: true })).toHaveAttribute("href", "/dashboard#all-projects");
  await expect(navigation.getByRole("link", { name: "AI 딜룸", exact: true })).toHaveAttribute("href", "/project/chosen-project/chief-orchestration");
  await expect(navigation.getByRole("link", { name: "사업성 분석", exact: true })).not.toBeVisible();
  await navigation.getByText("분석 도구", { exact: true }).click();
  await expect(navigation.getByRole("link", { name: "사업성 분석", exact: true })).toHaveAttribute("href", "/feasibility?projectId=chosen-project");
  await expect(navigation.getByRole("link", { name: "현금흐름", exact: true })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "실시간 모니터링", exact: true })).toBeVisible();
  await navigation.getByText("자료실", { exact: true }).click();
  for (const name of ["업무 템플릿", "표준 용어", "용어 검토"]) await expect(navigation.getByRole("link", { name, exact: true })).toBeVisible();
  await expect(navigation.getByRole("link", { name: "REALYTICS 블로그 (새 창)", exact: true })).toHaveCount(1);
  await expect(page.getByText("VWorld · 실거래가 연동", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "알림", exact: true })).toHaveCount(0);
});

test("mobile navigation contains keyboard focus, closes with Escape, and restores its trigger", async ({ page, viewport }) => {
  await fixture(page);
  const trigger = page.getByRole("button", { name: "메뉴 열기", exact: true });
  if ((viewport?.width ?? 1280) >= 1024) {
    await expect(trigger).not.toBeVisible();
    await expect(page.locator("aside")).toBeVisible();
    return;
  }
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "REALYTICS 메뉴", exact: true });
  await expect(dialog.getByRole("button", { name: "메뉴 닫기", exact: true })).toBeFocused();
  for (let index = 0; index < 12; index += 1) {
    await page.keyboard.press("Tab");
    await expect.poll(() => dialog.evaluate((element) => element.contains(document.activeElement))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await trigger.click();
  await dialog.getByRole("link", { name: "내 프로젝트", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page).toHaveURL(/\/dashboard#all-projects$/);
});

test("search presents similar projects without navigating and supports keyboard selection", async ({ page }) => {
  await fixture(page);
  const search = page.getByRole("combobox", { name: "프로젝트 검색", exact: true });
  await search.fill("본리동");
  await search.press("Enter");
  await expect(page.getByRole("option")).toHaveCount(2);
  await expect(page).toHaveURL(/\/dashboard$/);
  await search.press("ArrowDown");
  await search.press("ArrowDown");
  await expect(page.getByRole("option", { name: /본리동 B/ })).toHaveAttribute("aria-selected", "true");
  await search.press("Enter");
  await expect(page).toHaveURL(/\/project\/bonri-b$/);
});

test("editing the query discards a delayed search response and no-match results stay on the dashboard", async ({ page }) => {
  const state = await fixture(page);
  let release!: () => void;
  state.searchGate = new Promise<void>((resolve) => { release = resolve; });
  const search = page.getByRole("combobox", { name: "프로젝트 검색", exact: true });
  try {
    await search.fill("본리동");
    await search.press("Enter");
    await expect.poll(() => state.searches).toBe(1);
    await search.fill("없는 프로젝트");
  } finally { release(); }
  state.searchGate = Promise.resolve();
  await search.press("Enter");
  await expect(page.getByText("일치하는 프로젝트가 없습니다.", { exact: true })).toBeVisible();
  await expect(page.getByRole("option")).toHaveCount(0);
  await expect(page).toHaveURL(/\/dashboard$/);
  await search.press("Escape");
  await expect(search).toHaveAttribute("aria-expanded", "false");
});

test("malformed search fields show a safe failure without crashing and valid parcel matches still work", async ({ page }) => {
  const state = await fixture(page);
  const crashes: string[] = [];
  page.on("pageerror", (error) => crashes.push(error.message));
  const search = page.getByRole("combobox", { name: "프로젝트 검색", exact: true });
  for (const malformed of [
    { project: { id: "broken-name", name: { unexpected: "본리동" } } },
    { project: { id: "broken-address", name: "본리동" }, land: { address: { unexpected: "주소" } } },
  ]) {
    state.searchResponse = [malformed];
    await search.fill("본리동");
    await search.press("Enter");
    await expect(page.getByRole("alert").filter({ hasText: "프로젝트를 검색하지 못했습니다" })).toBeVisible();
    await expect(page.getByRole("option")).toHaveCount(0);
    await expect(page).toHaveURL(/\/dashboard$/);
  }
  state.searchResponse = [{ ...PROJECTS[0], land_acquisition: [null, { pnu: {} }, { pnu: "2729010100100010001" }] }];
  await search.fill("2729010100100010001");
  await search.press("Enter");
  await expect(page.getByRole("option", { name: /본리동 A/ })).toBeVisible();
  expect(crashes).toEqual([]);
});

test("account menu exposes logout on every screen size and preserves the session on a failed logout", async ({ page }) => {
  const state = await fixture(page);
  const trigger = page.getByRole("button", { name: "내 계정 메뉴", exact: true });
  await trigger.click();
  await expect(page.getByRole("link", { name: "계정·AI 설정", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await expect(page.getByRole("button", { name: "로그아웃", exact: true })).toHaveCount(0);
  await trigger.click();
  state.signoutStatus = 503;
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "로그아웃하지 못했습니다" })).toBeVisible();
  await expect(page).toHaveURL(/\/dashboard$/);
  expect(state.signedIn).toBe(true);
  state.signoutStatus = 200;
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page).toHaveURL((url) => url.pathname === "/login"
    && (url.search === "" || (url.searchParams.size === 1 && url.searchParams.get("next") === "/dashboard")));
  expect(state.signouts).toBe(2);
});
