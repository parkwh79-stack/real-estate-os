import { expect, test } from "@playwright/test";
import { mock } from "node:test";
import { fetchRealyticsBearerIdentity } from "../lib/backend-bearer-identity";
import {
  classifyRealyticsBearerIdentity,
  loginErrorForIdentityResult,
} from "../lib/backend-identity-result";

test.describe("OAuth callback identity boundary classification", () => {
  test("accepts only an explicit authenticated identity response", () => {
    expect(
      classifyRealyticsBearerIdentity(200, { authenticated: true }),
    ).toEqual({
      status: "accepted",
    });
    expect(
      classifyRealyticsBearerIdentity(200, { authenticated: false }),
    ).toEqual({
      status: "not_provisioned",
    });
    expect(
      classifyRealyticsBearerIdentity(200, { authenticated: "true" }),
    ).toEqual({
      status: "backend_unavailable",
    });
  });

  test("separates tenant selection from missing membership", () => {
    const selection = classifyRealyticsBearerIdentity(400, {
      detail: { error: "tenant_selection_required", activeMembershipCount: 2 },
    });
    const missingMembership = classifyRealyticsBearerIdentity(403, {
      detail: { error: "principal_not_registered", issuer: "sensitive-issuer" },
    });

    expect(selection).toEqual({ status: "tenant_selection_required" });
    expect(loginErrorForIdentityResult(selection)).toBe(
      "tenant_selection_required",
    );
    expect(missingMembership).toEqual({ status: "not_provisioned" });
    expect(loginErrorForIdentityResult(missingMembership)).toBe(
      "identity_not_provisioned",
    );
    expect(
      classifyRealyticsBearerIdentity(403, {
        detail: { error: "tenant_inactive", tenantStatus: "suspended" },
      }),
    ).toEqual({ status: "not_provisioned" });
    expect(
      loginErrorForIdentityResult(missingMembership, {
        invitationPending: true,
      }),
    ).toBe("invitation_accept_failed");
  });

  test("reports a normalized PostgreSQL authority outage separately", () => {
    const result = classifyRealyticsBearerIdentity(503, {
      detail: {
        error: "identity_membership_authority_unavailable",
        internalReason: "must-not-leak",
      },
    });

    expect(result).toEqual({ status: "identity_authority_unavailable" });
    expect(loginErrorForIdentityResult(result)).toBe(
      "identity_authority_unavailable",
    );
    expect(
      classifyRealyticsBearerIdentity(503, {
        detail: { error: "oidc_verifier_unavailable" },
      }),
    ).toEqual({ status: "identity_authority_unavailable" });
  });

  test("maps infrastructure and unknown failures to backend unavailable", () => {
    expect(
      classifyRealyticsBearerIdentity(503, {
        detail: { error: "upstream_dependency_failed" },
      }),
    ).toEqual({ status: "backend_unavailable" });
    expect(classifyRealyticsBearerIdentity(502, null)).toEqual({
      status: "backend_unavailable",
    });
    expect(
      classifyRealyticsBearerIdentity(403, { detail: "forbidden" }),
    ).toEqual({
      status: "backend_unavailable",
    });
  });

  test("treats bearer rejection as an identity authority failure", () => {
    const result = classifyRealyticsBearerIdentity(401, {
      detail: { error: "invalid_token", token: "must-not-leak" },
    });

    expect(result).toEqual({ status: "identity_authority_unavailable" });
    expect(loginErrorForIdentityResult(result)).toBe(
      "identity_authority_unavailable",
    );
  });
});

test.describe("bounded read-only bearer membership lookup", () => {
  test.beforeEach(() => {
    // The fake API clock advances up to four minutes; do not expire the
    // Playwright runner's own deadline while testing the upstream deadline.
    test.setTimeout(300_000);
    mock.timers.enable({ apis: ["setTimeout"] });
    // Native AbortSignal timers use Node internals rather than the fake clock.
    // Keep their abort behavior while advancing time without a real two-minute wait.
    mock.method(AbortSignal, "timeout", (delay: number) => {
      const controller = new AbortController();
      setTimeout(() => controller.abort(new DOMException("deadline", "TimeoutError")), delay);
      return controller.signal;
    });
  });
  test.afterEach(() => {
    mock.restoreAll();
    mock.timers.reset();
  });

  test("a cold API can confirm membership after ten seconds without another login or lookup", async () => {
    const token = "server-only-bearer";
    const fetchMock = mock.method(globalThis, "fetch", (_input: Parameters<typeof fetch>[0], init?: RequestInit) => new Promise<Response>((resolve, reject) => {
      const responseTimer = setTimeout(() => resolve(Response.json({
        authenticated: true,
        user: { userId: "private-subject", tenantId: "tenant-a" },
        internalDetail: "must-not-leak",
      })), 46_000);
      init?.signal?.addEventListener("abort", () => {
        clearTimeout(responseTimer);
        reject(init?.signal?.reason);
      }, { once: true });
    }));
    let settled = false;
    const pending = fetchRealyticsBearerIdentity("https://api.realytics.co.kr", token, "tenant-a")
      .then((result) => { settled = true; return result; });

    mock.timers.tick(10_001);
    await Promise.resolve();
    expect(settled).toBe(false);
    mock.timers.tick(35_999);
    const result = await pending;
    expect(result).toEqual({ status: "accepted" });
    expect(fetchMock.mock.callCount()).toBe(1);
    const [url, init] = fetchMock.mock.calls[0]!.arguments;
    expect(url).toBe("https://api.realytics.co.kr/auth/me");
    expect(init).toMatchObject({ method: "GET", cache: "no-store", redirect: "manual" });
    expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${token}`);
    expect(new Headers(init?.headers).get("X-REALYTICS-TENANT-ID")).toBe("tenant-a");
    expect(JSON.stringify(result)).not.toMatch(/server-only-bearer|private-subject|must-not-leak/);
  });

  test("an unresponsive authority stops at the shared two-minute deadline and never grants access or retries", async () => {
    const fetchMock = mock.method(globalThis, "fetch", (_input: Parameters<typeof fetch>[0], init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(init?.signal?.reason), { once: true });
    }));
    let settled = false;
    const pending = fetchRealyticsBearerIdentity("https://api.realytics.co.kr", "private-token")
      .then((result) => { settled = true; return result; });

    mock.timers.tick(119_999);
    await Promise.resolve();
    expect(settled).toBe(false);
    expect(fetchMock.mock.calls[0]!.arguments[1]?.signal?.aborted).toBe(false);
    mock.timers.tick(1);
    const result = await pending;
    expect(result).toEqual({ status: "backend_unavailable" });
    expect(fetchMock.mock.calls[0]!.arguments[1]?.signal?.aborted).toBe(true);
    mock.timers.tick(120_000);
    expect(fetchMock.mock.callCount()).toBe(1);
    expect(JSON.stringify(result)).not.toContain("private-token");
  });

  test("an upstream redirect cannot become an accepted identity or expose its destination", async () => {
    const fetchMock = mock.method(globalThis, "fetch", async () => new Response(null, {
      status: 302,
      headers: { Location: "https://other.invalid/private-token" },
    }));
    const result = await fetchRealyticsBearerIdentity("https://api.realytics.co.kr", "private-token");
    expect(result).toEqual({ status: "backend_unavailable" });
    expect(fetchMock.mock.callCount()).toBe(1);
    expect(fetchMock.mock.calls[0]!.arguments[1]?.redirect).toBe("manual");
    expect(JSON.stringify(result)).not.toMatch(/other\.invalid|private-token/);
  });

  test("explicit legacy membership gaps stay separate from authority outages and actual server failures", async () => {
    const replies = [
      { status: 503, error: "principal_not_registered", expected: "not_provisioned" },
      { status: 503, error: "identity_membership_authority_unavailable", expected: "identity_authority_unavailable" },
      { status: 500, error: "unexpected_store_error", expected: "backend_unavailable" },
    ];
    let replyIndex = 0;
    const fetchMock = mock.method(globalThis, "fetch", async () => {
      const reply = replies[replyIndex++]!;
      return Response.json({ detail: { error: reply.error, internalReason: "must-not-leak" } }, { status: reply.status });
    });
    for (const [index, reply] of replies.entries()) {
      const result = await fetchRealyticsBearerIdentity("https://api.realytics.co.kr", "private-token");
      expect(result).toEqual({ status: reply.expected });
      expect(fetchMock.mock.callCount()).toBe(index + 1);
      expect(JSON.stringify(result)).not.toMatch(/private-token|must-not-leak/);
    }
  });
});
