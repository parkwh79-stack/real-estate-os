import { expect, test } from "@playwright/test";

import {
  evaluateHighRiskAuthOperationalGate,
  evaluateMakerCheckerOperationalGate,
} from "../lib/supabase/auth-operational-gate";


test("high-risk auth gate fails closed without provider user, MFA, and AAL2", () => {
  expect(evaluateHighRiskAuthOperationalGate({})).toEqual({
    operational: false,
    providerLinked: false,
    verifiedMfaFactor: false,
    aal2: false,
    limitations: [
      "authenticated_user_missing",
      "google_or_kakao_identity_not_linked",
      "verified_mfa_factor_missing",
      "aal2_step_up_required",
    ],
  });
  expect(evaluateHighRiskAuthOperationalGate({
    userId: "maker",
    requestedProvider: "google",
    identities: [{ provider: "google" }],
    factors: [{ factorType: "totp", status: "unverified" }],
    currentLevel: "aal1",
  })).toMatchObject({
    operational: false,
    limitations: ["verified_mfa_factor_missing", "aal2_step_up_required"],
  });
});


test("high-risk auth gate requires the requested Google or Kakao identity", () => {
  expect(evaluateHighRiskAuthOperationalGate({
    userId: "checker",
    requestedProvider: "kakao",
    identities: [{ provider: "google" }],
    factors: [{ factorType: "totp", status: "verified" }],
    currentLevel: "aal2",
  })).toMatchObject({
    operational: false,
    limitations: ["kakao_identity_not_linked"],
  });
  expect(evaluateHighRiskAuthOperationalGate({
    userId: "checker",
    requestedProvider: "kakao",
    identities: [{ provider: "kakao" }],
    factors: [{ factorType: "totp", status: "verified" }],
    currentLevel: "aal2",
  })).toMatchObject({ operational: true, limitations: [] });
});


test("maker-checker gate requires two AAL2 users in the same tenant", () => {
  expect(evaluateMakerCheckerOperationalGate({
    makerUserId: "same-user",
    checkerUserId: "same-user",
    makerTenantId: "tenant-1",
    checkerTenantId: "tenant-1",
    makerOperational: true,
    checkerOperational: true,
  })).toMatchObject({
    operational: false,
    limitations: ["maker_checker_identity_not_separated"],
  });
  expect(evaluateMakerCheckerOperationalGate({
    makerUserId: "maker",
    checkerUserId: "checker",
    makerTenantId: "tenant-1",
    checkerTenantId: "tenant-1",
    makerOperational: true,
    checkerOperational: true,
  })).toEqual({
    operational: true,
    identitiesSeparated: true,
    tenantMatched: true,
    limitations: [],
  });
});
