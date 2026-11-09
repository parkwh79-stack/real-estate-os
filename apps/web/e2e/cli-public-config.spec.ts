import { expect, test } from "@playwright/test";
import { cliPublicConfig } from "../lib/cli-public-config";

const fixture = {
  REALYTICS_API_ORIGIN: "https://api.realytics.co.kr",
  NEXT_PUBLIC_SUPABASE_URL: "https://auth.realytics.co.kr/",
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_synthetic_public_configuration",
};

test("CLI bootstrap exposes only public origins, issuer and publishable configuration", () => {
  const result = cliPublicConfig({ ...fixture, ...{ REALYTICS_DATABASE_URL: "private-sentinel" } });
  expect(result).toEqual({
    schemaVersion: "realytics-cli-config-v1", clientId: "realytics-cli",
    apiOrigin: fixture.REALYTICS_API_ORIGIN, supabaseUrl: "https://auth.realytics.co.kr",
    publishableKey: fixture.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
    issuer: "https://auth.realytics.co.kr/auth/v1", authMethods: ["password", "refresh_token"],
  });
  expect(JSON.stringify(result)).not.toContain("private-sentinel");
});

test("CLI bootstrap rejects misnamed secrets, absent config and non-public recipients", () => {
  for (const value of ["sb_secret_private_sentinel", "eyJ.role.service_role.signature", "", "sb_publishable_bad\nvalue"]) {
    expect(cliPublicConfig({ ...fixture, NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: value })).toBeNull();
  }
  for (const url of ["http://example.com", "https://user:password@example.com", "https://example.com/path", "https://127.0.0.1", "https://10.0.0.1"]) {
    expect(cliPublicConfig({ ...fixture, REALYTICS_API_ORIGIN: url })).toBeNull();
    expect(cliPublicConfig({ ...fixture, NEXT_PUBLIC_SUPABASE_URL: url })).toBeNull();
  }
  expect(cliPublicConfig({})).toBeNull();
});

test("invalid public CLI bootstrap fails closed without exporting a browser session", async ({ request }) => {
  const response = await request.get("/api/cli/config");
  // The E2E server deliberately uses auth.e2e.invalid, never a real issuer.
  expect(response.status()).toBe(503);
  expect(response.headers()["cache-control"]).toContain("no-store");
  expect(response.headers()["set-cookie"]).toBeUndefined();
  const value = await response.json();
  expect(value).toEqual({ error: "cli_public_configuration_unavailable" });
});
