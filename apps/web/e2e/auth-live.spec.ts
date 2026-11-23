import fs from "node:fs";
import path from "node:path";
import { expect, test, type APIRequestContext } from "@playwright/test";

import { evaluateMakerCheckerOperationalGate } from "../lib/supabase/auth-operational-gate";


const liveEnabled = process.env.REALYTICS_LIVE_AUTH_SMOKE === "1";
const storageStateInput = process.env.REALYTICS_LIVE_AUTH_STORAGE_STATE ?? "";
function resolveStorageState(input: string): string {
  if (!input) return "";
  const direct = path.resolve(input);
  if (fs.existsSync(direct)) return direct;
  return path.resolve(process.cwd(), "..", "..", input);
}

const storageStatePath = resolveStorageState(storageStateInput);
const expectedProvider = process.env.REALYTICS_LIVE_AUTH_PROVIDER ?? "";
const requireAal2 = process.env.REALYTICS_LIVE_AUTH_REQUIRE_AAL2 === "1";
const revokeOtherSessions = (
  process.env.REALYTICS_LIVE_AUTH_REVOKE_OTHERS === "1"
);

if (storageStatePath && fs.existsSync(storageStatePath)) {
  test.use({ storageState: storageStatePath });
}

test.describe("REALYTICS live Supabase Auth smoke", () => {
  test.beforeEach(() => {
    test.skip(!liveEnabled, "Set REALYTICS_LIVE_AUTH_SMOKE=1 to run live auth tests.");
    expect(
      Boolean(storageStatePath && fs.existsSync(storageStatePath)),
      "A manually authenticated, ignored Playwright storage state is required.",
    ).toBe(true);
    expect(
      ["google", "kakao"],
      "Set REALYTICS_LIVE_AUTH_PROVIDER to google or kakao.",
    ).toContain(expectedProvider);
  });

  test("provider, signed session, membership, and MFA state are authoritative", async ({
    request,
  }) => {
    const readinessResponse = await request.get(
      "/api/auth/supabase/readiness",
    );
    expect(readinessResponse.status()).toBe(200);
    const readiness = await readinessResponse.json() as {
      providers: Record<string, boolean>;
      providerUsable: Record<string, boolean>;
      providerReady: Record<string, boolean>;
      canonicalOriginReady: boolean;
      configurationReady: boolean;
      operational: boolean;
      backend: {
        ready: boolean;
        membershipAuthority: string | null;
      };
    };
    expect(readiness.providers[expectedProvider]).toBe(true);
    expect(readiness.providerUsable[expectedProvider]).toBe(true);
    expect(readiness.canonicalOriginReady).toBe(true);
    expect(readiness.providerReady[expectedProvider]).toBe(true);
    // configurationReady is the product-level Google + Kakao gate. A live
    // smoke for one selected provider must not depend on the other provider.
    expect(readiness.configurationReady).toEqual(expect.any(Boolean));
    expect(readiness.operational).toBe(false);
    expect(readiness.backend).toMatchObject({
      ready: true,
      membershipAuthority: "postgres",
    });

    const identityResponse = await request.get("/api/backend/auth/me");
    expect(identityResponse.status()).toBe(200);
    const identity = await identityResponse.json() as {
      authenticated: boolean;
      user?: { tenantId?: string; status?: string };
      identityProvider?: { provider?: string };
    };
    expect(identity.authenticated).toBe(true);
    expect(identity.user?.tenantId).toBeTruthy();
    expect(identity.user?.status).toBe("active");

    const securityResponse = await request.get(
      `/api/auth/supabase/security?provider=${expectedProvider}`,
    );
    expect(securityResponse.status()).toBe(200);
    const security = await securityResponse.json() as {
      identities: Array<{ provider: string }>;
      factors: Array<{ factorType: string; status: string }>;
      assurance: { currentLevel: string | null };
      operationalForHighRiskActions: boolean;
      limitations: string[];
    };
    expect(
      security.identities.some(
        (identityItem) => identityItem.provider === expectedProvider,
      ),
    ).toBe(true);
    if (requireAal2) {
      expect(security.assurance.currentLevel).toBe("aal2");
      expect(
        security.factors.some(
          (factor) => factor.factorType === "totp" && factor.status === "verified",
        ),
      ).toBe(true);
      expect(security.operationalForHighRiskActions).toBe(true);
      expect(security.limitations).toEqual([]);
    }
  });

  test("other-session revocation is an explicit destructive smoke", async ({
    request,
    baseURL,
  }) => {
    test.skip(
      !revokeOtherSessions,
      "Set REALYTICS_LIVE_AUTH_REVOKE_OTHERS=1 to revoke other sessions.",
    );
    const response = await request.post("/api/auth/supabase/signout", {
      headers: { Origin: new URL(baseURL ?? "").origin },
      data: { scope: "others" },
    });
    expect(response.status()).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      signedOut: true,
      scope: "others",
    });
  });
});

type LiveIdentity = {
  userId: string;
  tenantId: string;
};

type LiveSecurity = {
  operationalForHighRiskActions: boolean;
  limitations: string[];
};

async function loadLiveActor(
  request: APIRequestContext,
  provider: string,
): Promise<{ identity: LiveIdentity; security: LiveSecurity }> {
  const [identityResponse, securityResponse] = await Promise.all([
    request.get("/api/backend/auth/me"),
    request.get(`/api/auth/supabase/security?provider=${provider}`),
  ]);
  expect(identityResponse.status()).toBe(200);
  expect(securityResponse.status()).toBe(200);
  const identityPayload = await identityResponse.json() as {
    authenticated?: boolean;
    user?: { userId?: string; tenantId?: string; status?: string };
    membershipAuthority?: { source?: string };
  };
  const security = await securityResponse.json() as LiveSecurity;
  expect(identityPayload.authenticated).toBe(true);
  expect(identityPayload.user?.status).toBe("active");
  expect(identityPayload.user?.userId).toBeTruthy();
  expect(identityPayload.user?.tenantId).toBeTruthy();
  expect(security.operationalForHighRiskActions, security.limitations.join(", ")).toBe(true);
  expect(security.limitations).toEqual([]);
  return {
    identity: {
      userId: identityPayload.user!.userId!,
      tenantId: identityPayload.user!.tenantId!,
    },
    security,
  };
}


test("distinct AAL2 maker/checker enforce final approval and publication", async ({
  browser,
  baseURL,
}) => {
  test.skip(
    process.env.REALYTICS_LIVE_AUTH_MAKER_CHECKER !== "1",
    "Set REALYTICS_LIVE_AUTH_MAKER_CHECKER=1 for the destructive maker-checker smoke.",
  );
  expect(liveEnabled).toBe(true);
  expect(
    process.env.REALYTICS_LIVE_AUTH_EXECUTE_FINAL_ACTIONS,
    "Explicitly set REALYTICS_LIVE_AUTH_EXECUTE_FINAL_ACTIONS=1.",
  ).toBe("1");
  expect(baseURL).toBeTruthy();

  const makerState = resolveStorageState(
    process.env.REALYTICS_LIVE_AUTH_MAKER_STORAGE_STATE ?? "",
  );
  const checkerState = resolveStorageState(
    process.env.REALYTICS_LIVE_AUTH_CHECKER_STORAGE_STATE ?? "",
  );
  expect(fs.existsSync(makerState), "Maker storage state is missing.").toBe(true);
  expect(fs.existsSync(checkerState), "Checker storage state is missing.").toBe(true);
  const makerProvider = process.env.REALYTICS_LIVE_AUTH_MAKER_PROVIDER ?? "";
  const checkerProvider = process.env.REALYTICS_LIVE_AUTH_CHECKER_PROVIDER ?? "";
  expect(["google", "kakao"]).toContain(makerProvider);
  expect(["google", "kakao"]).toContain(checkerProvider);

  const projectId = process.env.REALYTICS_LIVE_PF_PROJECT_ID ?? "";
  const runId = process.env.REALYTICS_LIVE_PF_RUN_ID ?? "";
  const reportId = process.env.REALYTICS_LIVE_PF_REPORT_ID ?? "";
  expect(projectId, "REALYTICS_LIVE_PF_PROJECT_ID is required.").not.toBe("");
  expect(runId, "REALYTICS_LIVE_PF_RUN_ID is required.").not.toBe("");
  expect(reportId, "REALYTICS_LIVE_PF_REPORT_ID is required.").not.toBe("");

  const makerContext = await browser.newContext({
    baseURL,
    storageState: makerState,
  });
  const checkerContext = await browser.newContext({
    baseURL,
    storageState: checkerState,
  });
  try {
    const maker = await loadLiveActor(makerContext.request, makerProvider);
    const checker = await loadLiveActor(checkerContext.request, checkerProvider);
    const gate = evaluateMakerCheckerOperationalGate({
      makerUserId: maker.identity.userId,
      checkerUserId: checker.identity.userId,
      makerTenantId: maker.identity.tenantId,
      checkerTenantId: checker.identity.tenantId,
      makerOperational: maker.security.operationalForHighRiskActions,
      checkerOperational: checker.security.operationalForHighRiskActions,
    });
    expect(gate.operational, gate.limitations.join(", ")).toBe(true);

    const runResponse = await makerContext.request.get(
      `/api/backend/projects/${projectId}/orchestrations/${runId}`,
    );
    expect(runResponse.status()).toBe(200);
    const run = await runResponse.json() as Record<string, unknown>;
    const runOptions = (
      run.options && typeof run.options === "object"
        ? run.options
        : {}
    ) as Record<string, unknown>;
    const recordedMaker = String(
      run.user_id
        ?? run.requestedBy
        ?? run.requested_by
        ?? runOptions.user_id
        ?? runOptions.requestedBy
        ?? runOptions.requested_by
        ?? "",
    );
    expect(recordedMaker).toBe(maker.identity.userId);
    expect(String(run.approvalStatus ?? "unapproved")).not.toBe("approved");

    const origin = new URL(baseURL!).origin;
    const makerApproval = await makerContext.request.post(
      `/api/backend/projects/${projectId}/orchestrations/${runId}/approve`,
      { headers: { Origin: origin }, data: { comment: "live maker denial smoke" } },
    );
    expect(makerApproval.status()).toBe(409);
    expect(JSON.stringify(await makerApproval.json())).toContain("Maker-checker violation");

    const checkerApproval = await checkerContext.request.post(
      `/api/backend/projects/${projectId}/orchestrations/${runId}/approve`,
      { headers: { Origin: origin }, data: { comment: "live AAL2 checker smoke" } },
    );
    expect(checkerApproval.status()).toBe(200);
    await expect(checkerApproval.json()).resolves.toMatchObject({
      approvalStatus: "approved",
      approval: { approved_by: checker.identity.userId },
    });

    const makerPublication = await makerContext.request.post(
      `/api/backend/projects/${projectId}/reports/${reportId}/publish`,
      { headers: { Origin: origin } },
    );
    expect(makerPublication.status()).toBe(409);
    expect(JSON.stringify(await makerPublication.json())).toContain("Maker-checker violation");

    const publication = await checkerContext.request.post(
      `/api/backend/projects/${projectId}/reports/${reportId}/publish`,
      { headers: { Origin: origin } },
    );
    expect(publication.status()).toBe(200);
    await expect(publication.json()).resolves.toMatchObject({
      status: "published",
      published_by: checker.identity.userId,
    });
  } finally {
    await Promise.all([makerContext.close(), checkerContext.close()]);
  }
});
