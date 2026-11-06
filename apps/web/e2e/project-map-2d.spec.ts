import { expect, test, type Page, type Route } from "@playwright/test";

const projectId = "map-provider-sample";
const image = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64");

async function projectData(page: Page) {
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { authenticated: true,
      user: { userId: "map-user", role: "analyst", tenantId: "maps", status: "active" }, permissions: ["project:read"] } });
    if (path.endsWith("/workspace/context")) return route.fulfill({ json: { projectId, projectMemory: { projectName: "공급자 전환 검증" } } });
    if (path.endsWith("/map")) return route.fulfill({ json: {
      center: { lng: 126.9, lat: 37.5 }, parcelGeoJson: { type: "FeatureCollection", features: [{
        type: "Feature", properties: { pnu: "1111010100100010000", addressFragment: "검증 필지 1" },
        geometry: { type: "Polygon", coordinates: [[[126.9, 37.5, 20], [126.901, 37.5, 20], [126.901, 37.501, 20], [126.9, 37.5, 20]]] },
      }] },
    } });
    return route.fulfill({ json: {} });
  });
}

test("background provider failures remain visible and retry preserves the parcel selection", async ({ page }) => {
  await projectData(page);
  let failing = true;
  const tiles = async (route: Route) => failing
    ? route.fulfill({ status: 502, json: { reason: "provider_rejected_request" }, headers: { "Cache-Control": "no-store" } })
    : route.fulfill({ contentType: "image/png", body: image });
  await page.route("**/api/vworld?**", tiles);
  await page.route("https://tile.openstreetmap.org/**", tiles);
  await page.goto(`/project/${projectId}/map`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible({ timeout: 20_000 });
  const failure = page.getByRole("alert").filter({ hasText: "배경지도를 불러오지 못했습니다" });
  await expect(failure).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("OpenStreetMap · 배경지도", { exact: true })).toBeVisible();
  const selection = page.getByRole("checkbox", { name: /검증 필지 1/ });
  await expect(selection).toBeChecked();
  await selection.uncheck();
  await expect(selection).not.toBeChecked();
  failing = false;
  await page.getByRole("button", { name: "지도 다시 시도", exact: true }).click();
  await expect(failure).toHaveCount(0);
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible({ timeout: 20_000 });
  await expect(selection).not.toBeChecked();
});

test("an optional WMS tile failure does not remove the background or parcel map", async ({ page }) => {
  await projectData(page);
  await page.route("https://tile.openstreetmap.org/**", (route) => route.fulfill({ contentType: "image/png", body: image }));
  await page.route("**/api/vworld?**", (route) => new URL(route.request().url()).searchParams.get("kind") === "wms"
    ? route.fulfill({ status: 502, json: { reason: "provider_rejected_request" } })
    : route.fulfill({ contentType: "image/png", body: image }));
  await page.goto(`/project/${projectId}/map`, { waitUntil: "domcontentloaded" });
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible({ timeout: 20_000 });
  const cadastral = page.getByRole("checkbox", { name: "VWorld 지적도", exact: true });
  if (await cadastral.count()) {
    await cadastral.check();
    await expect(page.getByRole("status").filter({ hasText: "일부 지적도·용도지역 타일" })).toBeVisible();
    await expect(page.getByText("VWorld · 배경지도", { exact: true })).toBeVisible();
  }
  await expect(page.getByRole("alert").filter({ hasText: "배경지도를 불러오지 못했습니다" })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: /검증 필지 1/ })).toBeChecked();
});
