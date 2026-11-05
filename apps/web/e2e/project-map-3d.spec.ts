import { expect, test } from "@playwright/test";

const projectId = "parcel-3d-sample";
const pnu = "1111010100100010000";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/backend/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) {
      return route.fulfill({ json: {
        authenticated: true,
        user: { userId: "map-user", tenantId: "map-tenant", role: "analyst", status: "active" },
        permissions: ["project:read"],
      } });
    }
    if (path.endsWith("/workspace/context")) {
      return route.fulfill({ json: { projectId, projectMemory: { projectName: "합성 3D 지도" } } });
    }
    if (path.endsWith("/map")) {
      return route.fulfill({ json: {
        center: { lng: 126.9, lat: 37.5 },
        parcelGeoJson: {
          type: "FeatureCollection",
          features: [{
            type: "Feature",
            geometry: { type: "Polygon", coordinates: [[[126.9, 37.5], [126.901, 37.5], [126.901, 37.501], [126.9, 37.5]]] },
            properties: { pnu, addressFragment: "합성동 1" },
          }],
        },
      } });
    }
    return route.fulfill({ json: {} });
  });
  await page.route("https://tile.openstreetmap.org/**", (route) => route.abort());
  await page.route("**/api/vworld?**", (route) => route.abort());
});

test("3D page displays the parcel map if the WebGL service is unavailable", async ({ page, request }) => {
  const bootstrap = await request.get("/vworld-3d?lng=126.9&lat=37.5");
  expect([200, 503]).toContain(bootstrap.status());
  expect((await bootstrap.text()).includes("server-only-fixture")).toBe(false);
  if (bootstrap.status() === 503) {
    await page.route("**/vworld-3d?**", () => { throw new Error("Unconfigured 3D map must not request the SDK"); });
  } else {
    await page.route("**/vworld-3d?**", (route) => route.fulfill({
      contentType: "text/html",
      body: '<script>window.parent.postMessage("realytics:vworld-3d-unavailable", window.location.origin);</script>',
    }));
  }

  await page.goto(`/project/${projectId}/map-3d?track=pf`, { waitUntil: "domcontentloaded" });
  const parcelMap = page.getByRole("region", { name: "2D 필지 지도" });
  await expect(parcelMap.getByText("현재 3D 지도를 불러올 수 없어 2D 필지 지도를 표시합니다.")).toBeVisible();
  await expect(parcelMap.locator("canvas.maplibregl-canvas")).toBeVisible();
  await expect(page.getByTitle("VWorld 3D 지도")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "전체 지도·거래 분석 보기" })).toHaveAttribute("href", `/project/${projectId}/map?track=pf`);
});

test("3D page carries an exact parcel focus into the project map", async ({ page }) => {
  const mapRequest = page.waitForRequest((request) => new URL(request.url()).pathname === `/api/backend/projects/${projectId}/map`);
  await page.goto(`/project/${projectId}/map-3d?track=pf&pnu=${pnu}`, { waitUntil: "domcontentloaded" });
  const query = new URL((await mapRequest).url()).searchParams;
  expect(query.get("focus_pnu")).toBe(pnu);
  expect(query.get("geometry_limit")).toBe("1");
  await expect(page.getByRole("region", { name: "2D 필지 지도" }).locator("canvas.maplibregl-canvas")).toBeVisible();
  await expect(page.getByRole("link", { name: "지도로 돌아가기" })).toHaveAttribute("href", `/project/${projectId}/map?track=pf&pnu=${pnu}`);
});
