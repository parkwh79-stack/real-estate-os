import { defineConfig, devices } from "@playwright/test";

const liveAuthSmoke = process.env.REALYTICS_LIVE_AUTH_SMOKE === "1";
const baseURL = process.env.REALYTICS_E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: liveAuthSmoke ? [] : ["**/auth-live.spec.ts"],
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: liveAuthSmoke ? undefined : {
    command: "npm run dev",
    url: `${baseURL}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: {
      REALYTICS_API_ORIGIN: "http://127.0.0.1:8000",
    },
  },
});
