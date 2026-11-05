import { defineConfig, devices } from "@playwright/test";

const liveAuthSmoke = process.env.REALYTICS_LIVE_AUTH_SMOKE === "1";
const productionBuild = process.env.REALYTICS_E2E_PRODUCTION === "1";
const baseURL = process.env.REALYTICS_E2E_BASE_URL ?? (productionBuild ? "https://localhost:3032" : "http://localhost:3000");
const testOrigin = new URL(baseURL);
if (productionBuild && !liveAuthSmoke && (testOrigin.protocol !== "https:"
  || !["localhost", "127.0.0.1", "[::1]"].includes(testOrigin.hostname))) {
  throw new Error("Production E2E uses an HTTPS loopback origin.");
}
process.env.REALYTICS_E2E_BASE_URL ??= baseURL;

export default defineConfig({
  testDir: "./e2e",
  testIgnore: liveAuthSmoke ? [] : ["**/auth-live.spec.ts"],
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL,
    ignoreHTTPSErrors: productionBuild && !liveAuthSmoke,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: liveAuthSmoke ? undefined : {
    // CI verifies the release bundle; dev mode remains available for local debugging.
    command: productionBuild
      ? `npm exec next -- build && node ../../scripts/playwright-production-server.mjs ${testOrigin.port || "443"}`
      : `npm exec next -- dev --port ${new URL(baseURL).port || "3000"} --webpack`,
    url: `${baseURL}/login`,
    ignoreHTTPSErrors: productionBuild,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      REALYTICS_API_ORIGIN: productionBuild
        ? "https://realytics-api-obozektnwa-an.a.run.app"
        : "http://127.0.0.1:8000",
      REALYTICS_WEB_ORIGIN: baseURL,
      REALYTICS_NEXT_DIST_DIR: productionBuild ? ".next-e2e-production" : ".next-e2e",
      // Contract tests intercept auth routes; never depend on a developer's .env.
      NEXT_PUBLIC_SUPABASE_URL: "https://auth.e2e.invalid",
      NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_e2e_fixture_only",
      REALYTICS_NAVER_OAUTH_VERIFIED: process.env.REALYTICS_NAVER_OAUTH_VERIFIED ?? "false",
    },
  },
});
