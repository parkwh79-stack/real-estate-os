import {
  classifyRealyticsBearerIdentity,
  type RealyticsBearerIdentityResult,
} from "./backend-identity-result";
import { BACKEND_UPSTREAM_TIMEOUT_MS } from "./backend-proxy-contract";

const ONBOARDING_IDENTITY_ERRORS = new Set([
  "principal_not_registered",
  "principal_disabled",
  "principal_inactive",
  "no_active_tenant_membership",
  "tenant_membership_not_found",
  "tenant_membership_inactive",
  "tenant_inactive",
  "invalid_membership_role",
]);

/** One read-only membership lookup, including the API's scale-from-zero time. */
export async function fetchRealyticsBearerIdentity(
  backendOrigin: string,
  accessToken: string,
  selectedTenantId?: string | null,
): Promise<RealyticsBearerIdentityResult> {
  try {
    const response = await fetch(`${backendOrigin}/auth/me`, {
      method: "GET",
      cache: "no-store",
      redirect: "manual",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        ...(selectedTenantId
          ? { "X-REALYTICS-TENANT-ID": selectedTenantId }
          : {}),
      },
      signal: AbortSignal.timeout(BACKEND_UPSTREAM_TIMEOUT_MS),
    });
    const identity = (await response.json().catch(() => null)) as unknown;
    const classified = classifyRealyticsBearerIdentity(response.status, identity);
    const detail = identity && typeof identity === "object"
      ? (identity as { detail?: { error?: unknown } }).detail
      : null;
    // Retain older API revisions' explicit missing-membership response;
    // unknown failures still cannot grant access or become a new workspace.
    if (
      classified.status === "backend_unavailable"
      && typeof detail?.error === "string"
      && ONBOARDING_IDENTITY_ERRORS.has(detail.error)
    ) {
      return { status: "not_provisioned" };
    }
    return classified;
  } catch {
    return { status: "backend_unavailable" };
  }
}
