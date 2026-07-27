import { expect, test } from "@playwright/test";
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
