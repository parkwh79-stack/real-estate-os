import { expect, test, type Page, type APIRequestContext } from "@playwright/test";
import type { NextRequest } from "next/server";
import { GET as render3DBootstrap } from "../app/vworld-3d/route";

const projectId = "parcel-3d-sample";
const pnu = "1111010100100010000";
const projectMap = {
  center: { lng: 126.9, lat: 37.5 },
  parcelGeoJson: {
    type: "FeatureCollection",
    features: [{
      type: "Feature",
      geometry: { type: "Polygon", coordinates: [[[126.9, 37.5], [126.901, 37.5], [126.901, 37.501], [126.9, 37.5]]] },
      properties: { pnu, addressFragment: "합성동 1" },
    }],
  },
};

async function browser3DConfigured(request: APIRequestContext) {
  const response = await request.get("/vworld-3d?lng=126.9&lat=37.5");
  expect([200, 503]).toContain(response.status());
  expect(await response.text()).not.toContain("server-only-fixture");
  return response.status() === 200;
}

async function bootstrapFixture(page: Page) {
  // Render the real bootstrap with an explicitly synthetic browser key. A server
  // credential must never substitute for this separately public contract.
  const previousPublic = process.env.NEXT_PUBLIC_VWORLD_API_KEY;
  const previousPrivate = process.env.VWORLD_API_KEY;
  let html: string;
  try {
    process.env.NEXT_PUBLIC_VWORLD_API_KEY = "browser-vworld-fixture";
    process.env.VWORLD_API_KEY = "server-only-fixture";
    const response = await render3DBootstrap({ nextUrl: new URL("https://fixture.invalid/vworld-3d?lng=126.9&lat=37.5") } as NextRequest);
    expect(response.status).toBe(200);
    html = await response.text();
    expect(html).not.toContain("server-only-fixture");
  } finally {
    if (previousPublic === undefined) delete process.env.NEXT_PUBLIC_VWORLD_API_KEY;
    else process.env.NEXT_PUBLIC_VWORLD_API_KEY = previousPublic;
    if (previousPrivate === undefined) delete process.env.VWORLD_API_KEY;
    else process.env.VWORLD_API_KEY = previousPrivate;
  }
  await page.route("**/vworld-3d?**", (route) => route.fulfill({ contentType: "text/html", body: html }));
  await page.route("https://map.vworld.kr/**", (route) => route.fulfill({ contentType: "application/javascript", body: `
    window.vw = {
      Map: function () {
        this.setOption = function () {};
        this.setMapId = function () {};
        this.start = function () {
          window.__mapStarted = true;
          window.__finish3D = function (render) {
            if (render) {
              var canvas = document.createElement("canvas");
              canvas.width = 640; canvas.height = 480;
              canvas.getContext = function () { return { isContextLost: function () { return false; } }; };
              document.getElementById("vmap").appendChild(canvas);
            }
            // The provider initializes asynchronously after map.start(), as its
            // public ws3dInitCallBack contract requires.
            setTimeout(function () { vw.ws3dInitCallBack(); }, 100);
          };
        };
      },
      CameraPosition: function () {}, CoordZ: function () {}, Direction: function () {}
    };
  ` }));
  await page.route("**/3d-engine-fixture", (route) => route.fulfill({ contentType: "text/html", body: `
    <!doctype html><html><body><script>
      window.__mapMessages = [];
      window.addEventListener("message", function (event) { window.__mapMessages.push(event.data); });
    </script><iframe src="/vworld-3d?lng=126.9&lat=37.5" title="bootstrap fixture"></iframe></body></html>
  ` }));
  await page.goto("/3d-engine-fixture");
  const frame = page.frameLocator('iframe[title="bootstrap fixture"]');
  await expect.poll(() => frame.locator("body").evaluate(() => Reflect.get(window, "__mapStarted"))).toBe(true);
  return frame;
}

async function mapMessages(page: Page) {
  return page.evaluate(() => Reflect.get(window, "__mapMessages") as string[]);
}

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
    if (path.endsWith("/map")) return route.fulfill({ json: projectMap });
    return route.fulfill({ json: {} });
  });
  await page.route("https://tile.openstreetmap.org/**", (route) => route.abort());
  await page.route("**/api/vworld?**", (route) => route.abort());
  await page.route("https://api.vworld.kr/**", (route) => route.abort());
  // Contract tests never call the real provider or consume a real key/quota.
  await page.route("https://map.vworld.kr/**", (route) => route.abort());
});

test("3D page displays the parcel map if the WebGL service is unavailable", async ({ page, request }) => {
  const configured = await browser3DConfigured(request);
  if (!configured) {
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
  if (configured) {
    await page.getByRole("button", { name: "3D 지도 다시 시도" }).click();
    // Failed attempts stay retryable and never discard the project/PNU context.
    await expect(parcelMap).toBeVisible();
  }
});

test("3D page frames only the exact matching parcel and preserves its map link", async ({ page, request }) => {
  const configured = await browser3DConfigured(request);
  await page.route("**/vworld-3d?**", (route) => route.fulfill({ contentType: "text/html", body: "<p>3D engine fixture</p>" }));
  const mapRequest = page.waitForRequest((request) => new URL(request.url()).pathname === `/api/backend/projects/${projectId}/map`);
  await page.goto(`/project/${projectId}/map-3d?track=pf&pnu=${pnu}`, { waitUntil: "domcontentloaded" });
  const query = new URL((await mapRequest).url()).searchParams;
  expect(query.get("focus_pnu")).toBe(pnu);
  expect(query.get("geometry_limit")).toBe("1");
  if (configured) {
    const iframe = page.getByTitle("VWorld 3D 지도");
    await expect(iframe).toBeVisible();
    const src = new URL((await iframe.getAttribute("src"))!, "https://fixture.invalid");
    expect(Number(src.searchParams.get("lng"))).toBeCloseTo(126.9005);
    expect(Number(src.searchParams.get("lat"))).toBeCloseTo(37.5005);
    await expect(page.getByText("선택한 필지 주변의 3D 공간을 표시합니다.", { exact: false })).toBeVisible();
  } else {
    await expect(page.getByRole("region", { name: "2D 필지 지도" }).locator("canvas.maplibregl-canvas")).toBeVisible();
  }
  await expect(page.getByRole("link", { name: "지도로 돌아가기" })).toHaveAttribute("href", `/project/${projectId}/map?track=pf&pnu=${pnu}`);
});

test("an unknown parcel never borrows the project center or national reference view", async ({ page }) => {
  const unknownPnu = "1111010100100020000";
  let iframeRequests = 0;
  await page.route("**/vworld-3d?**", (route) => { iframeRequests++; return route.fulfill({ body: "" }); });
  await page.goto(`/project/${projectId}/map-3d?track=pf&pnu=${unknownPnu}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("region", { name: "2D 필지 지도" })).toBeVisible();
  await expect(page.getByText("이 프로젝트에서 요청한 필지를 확인할 수 없습니다.")).toBeVisible();
  await expect(page.getByTitle("VWorld 3D 지도")).toHaveCount(0);
  await expect(page.getByText("전국 3D 참고지도", { exact: false })).toHaveCount(0);
  expect(iframeRequests).toBe(0);
});

test("an unlocated project can show only an explicitly labeled national reference", async ({ page, request }) => {
  const configured = await browser3DConfigured(request);
  await page.route(`**/api/backend/projects/${projectId}/map?**`, (route) => route.fulfill({ json: {
    center: null, parcelGeoJson: { type: "FeatureCollection", features: [] },
  } }));
  await page.route("**/vworld-3d?**", (route) => route.fulfill({ contentType: "text/html", body: "<p>National 3D fixture</p>" }));
  await page.goto(`/project/${projectId}/map-3d?track=development`, { waitUntil: "domcontentloaded" });
  if (configured) {
    await expect(page.getByText("전국 3D 참고지도 · 사업 위치 미확인.", { exact: false })).toBeVisible();
    const iframe = page.getByTitle("VWorld 3D 지도");
    await expect(iframe).toBeVisible();
    const src = new URL((await iframe.getAttribute("src"))!, "https://fixture.invalid");
    expect(src.searchParams.get("z")).toBe("1000000");
    expect(src.searchParams.get("lng")).toBe("127.6");
    expect(src.searchParams.get("lat")).toBe("36.2");
  } else {
    await expect(page.getByRole("region", { name: "2D 필지 지도" })).toBeVisible();
    await expect(page.getByTitle("VWorld 3D 지도")).toHaveCount(0);
  }
});

test("bootstrap waits for the asynchronous SDK callback and live render, then detects context loss", async ({ page }) => {
  const frame = await bootstrapFixture(page);
  expect(await mapMessages(page)).not.toContain("realytics:vworld-3d-ready");
  await expect(frame.getByText("3D 엔진 로딩 중…")).toBeVisible();
  await frame.locator("body").evaluate(() => Reflect.get(window, "__finish3D")(true));
  await expect.poll(() => mapMessages(page)).toContain("realytics:vworld-3d-ready");
  await expect(frame.getByText("3D 엔진 로딩 중…")).toHaveCount(0);
  await frame.locator("canvas").evaluate((canvas) => canvas.dispatchEvent(new Event("webglcontextlost")));
  await expect.poll(() => mapMessages(page)).toContain("realytics:vworld-3d-unavailable");
});

test("bootstrap times out if SDK callback has no usable WebGL render", async ({ page }) => {
  await page.clock.install();
  const frame = await bootstrapFixture(page);
  await frame.locator("body").evaluate(() => Reflect.get(window, "__finish3D")(false));
  await page.clock.runFor(200);
  await page.clock.fastForward(11_000);
  await expect(frame.getByText("3D 지도를 불러오지 못했습니다.")).toBeVisible();
  expect(await mapMessages(page)).toContain("realytics:vworld-3d-unavailable");
  expect(await mapMessages(page)).not.toContain("realytics:vworld-3d-ready");
});

test("an initialized SDK without its callback cannot acknowledge readiness", async ({ page }) => {
  await page.clock.install();
  const frame = await bootstrapFixture(page);
  await page.clock.fastForward(31_000);
  await expect(frame.getByText("3D 지도를 불러오지 못했습니다.")).toBeVisible();
  expect(await mapMessages(page)).not.toContain("realytics:vworld-3d-ready");
});

test("the parent watchdog ignores unrelated ready messages and retries with a fresh frame", async ({ page, request }) => {
  const configured = await browser3DConfigured(request);
  await page.clock.install();
  await page.route("**/vworld-3d?**", (route) => route.fulfill({ contentType: "text/html", body: "<p>Unresponsive SDK fixture</p>" }));
  await page.goto(`/project/${projectId}/map-3d?track=pf`, { waitUntil: "domcontentloaded" });
  if (configured) {
    await expect(page.getByTitle("VWorld 3D 지도")).toBeVisible();
    await page.evaluate(() => window.postMessage("realytics:vworld-3d-ready", window.location.origin));
    await page.clock.fastForward(46_000);
    await expect(page.getByRole("region", { name: "2D 필지 지도" })).toBeVisible();
    await page.getByRole("button", { name: "3D 지도 다시 시도" }).click();
    await expect(page.getByTitle("VWorld 3D 지도")).toHaveAttribute("src", /attempt=1/);
    await expect(page.getByText("3D 엔진과 지형을 불러오고 있습니다.")).toBeVisible();
  } else {
    await expect(page.getByRole("region", { name: "2D 필지 지도" })).toBeVisible();
  }
});


test("bootstrap never substitutes the private server key for an unconfigured browser key", async () => {
  const previousPublic = process.env.NEXT_PUBLIC_VWORLD_API_KEY;
  const previousPrivate = process.env.VWORLD_API_KEY;
  try {
    delete process.env.NEXT_PUBLIC_VWORLD_API_KEY;
    process.env.VWORLD_API_KEY = "server-only-fixture";
    const response = await render3DBootstrap({ nextUrl: new URL("https://fixture.invalid/vworld-3d?lng=126.9&lat=37.5") } as NextRequest);
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("server-only-fixture");
  } finally {
    if (previousPublic === undefined) delete process.env.NEXT_PUBLIC_VWORLD_API_KEY;
    else process.env.NEXT_PUBLIC_VWORLD_API_KEY = previousPublic;
    if (previousPrivate === undefined) delete process.env.VWORLD_API_KEY;
    else process.env.VWORLD_API_KEY = previousPrivate;
  }
});
