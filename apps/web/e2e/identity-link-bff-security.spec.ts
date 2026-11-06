import { expect, test } from "@playwright/test";

test("identity linking has no GET side effect and rejects cross-site POST", async ({ request }) => {
  const getResponse = await request.get(
    "/api/auth/supabase/identity/link?provider=google&next=/settings",
    { maxRedirects: 0 },
  );
  expect(getResponse.status()).toBe(405);

  const crossSite = await request.post("/api/auth/supabase/identity/link", {
    headers: { Origin: "https://attacker.invalid" },
    data: {
      provider: "google",
      next: "https://attacker.invalid/collect",
      secret: "must-never-be-reflected",
    },
  });
  expect(crossSite.status()).toBe(403);
  expect(crossSite.headers()["cache-control"]).toContain("no-store");
  const bodyText = await crossSite.text();
  expect(bodyText).toBe(JSON.stringify({ error: "cross_site_request_rejected" }));
  expect(bodyText).not.toContain("must-never-be-reflected");
  expect(bodyText).not.toContain("attacker.invalid/collect");
});

test("identity and TOTP recovery mutations share the CSRF boundary", async ({ request }) => {
  for (const action of [
    { action: "identity_unlink", identityId: "sensitive-identity-id" },
    { action: "mfa_unenroll", factorId: "sensitive-factor-id" },
  ]) {
    const response = await request.post("/api/auth/supabase/security", {
      headers: { Origin: "https://attacker.invalid" },
      data: action,
    });
    expect(response.status()).toBe(403);
    expect(response.headers()["cache-control"]).toContain("no-store");
    const bodyText = await response.text();
    expect(bodyText).toBe(JSON.stringify({ error: "cross_site_request_rejected" }));
    expect(bodyText).not.toContain("sensitive-");
  }
});

test("malformed link requests fail with a provider-neutral, secret-safe error", async ({ request }) => {
  const response = await request.post("/api/auth/supabase/identity/link", {
    data: {
      provider: "https://attacker.invalid/provider?client_secret=never-reflect",
      next: "//attacker.invalid",
    },
  });
  expect(response.status()).toBe(400);
  const bodyText = await response.text();
  expect(bodyText).toBe(JSON.stringify({ error: "unsupported_identity_provider" }));
  expect(bodyText).not.toContain("client_secret");
  expect(bodyText).not.toContain("attacker.invalid");
});
