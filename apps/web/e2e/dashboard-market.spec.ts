import { expect, test } from "@playwright/test";

test("market lookup is on demand, display only, and distinguishes unavailable from empty", async ({ page }) => {
  const requests: { method: string; query: URLSearchParams }[] = [];
  let status = "ok";
  await page.route("**/api/backend/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname.endsWith("/auth/me")) {
      return route.fulfill({ json: {
        authenticated: true,
        user: { userId: "market-user", tenantId: "market-tenant", role: "analyst", displayName: "테스트 검토자" },
        session: { userId: "market-user", tenantId: "market-tenant" },
        permissions: ["project:read"],
        authSource: "bearer_token",
      } });
    }
    if (url.pathname.endsWith("/projects/recent-work")) {
      return route.fulfill({ json: { schemaVersion: "realytics-recent-work-v2", count: 0, items: [] } });
    }
    if (url.pathname.endsWith("/projects")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/api/market/transactions")) {
      requests.push({ method: request.method(), query: url.searchParams });
      return route.fulfill({ json: {
        records: status === "ok" ? [{ address: "가상 검증 아파트", totalPrice: 615_000_000, areaSqm: 84.91 }] : [],
        diagnostic: { status },
      } });
    }
    return route.fulfill({ status: 404, json: {} });
  });

  await page.goto("/dashboard");
  const market = page.getByTestId("dashboard-market");
  await expect(market.getByRole("heading", { name: "지도·실거래가" })).toBeVisible();
  const mapToggle = market.getByRole("button", { name: "지도 보기" });
  await expect(mapToggle).toHaveAttribute("aria-expanded", "false");
  await mapToggle.click();
  await expect(market.getByRole("button", { name: "지도 접기" })).toHaveAttribute("aria-expanded", "true");
  await market.getByRole("button", { name: "지도 접기" }).click();
  const lookup = market.getByRole("button", { name: "조회", exact: true });
  await expect(lookup).toBeDisabled();
  await market.getByLabel("법정동코드").fill("11650");
  const month = market.getByLabel("조회월 (YYYYMM)");
  await month.clear();
  await month.pressSequentially("202613");
  await expect(month).toHaveValue("202613");
  await expect(lookup).toBeDisabled();
  await month.clear();
  await month.pressSequentially("202610");
  await expect(month).toHaveValue("202610");
  await expect(lookup).toBeEnabled();
  expect(requests).toHaveLength(0);
  await lookup.click();
  await expect(market.getByText("가상 검증 아파트")).toBeVisible();
  await expect(market.getByText("61,500만원")).toBeVisible();
  await expect(market.getByText("85㎡")).toBeVisible();
  expect(requests[0]?.method).toBe("GET");
  expect(Object.fromEntries(requests[0]!.query)).toEqual({
    lawd_cd: "11650", deal_ym: "202610", asset_type: "apartment", deal_kind: "trade",
  });

  await month.clear();
  await month.pressSequentially("202609");
  await expect(market.getByText("가상 검증 아파트")).toHaveCount(0);
  expect(requests).toHaveLength(1);
  status = "skipped";
  await lookup.click();
  await expect(market.getByRole("alert")).toContainText("아직 연결되지 않았습니다");
  await expect(market.getByText(/이번 조회 거래/)).toHaveCount(0);
  status = "empty";
  await lookup.click();
  await expect(market.getByRole("status").filter({ hasText: "이번 조회 거래" })).toContainText("0건");
  expect(requests.map((request) => request.method)).toEqual(["GET", "GET", "GET"]);
});
