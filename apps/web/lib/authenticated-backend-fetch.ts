import "server-only";

import { cookies } from "next/headers";
import {
  BACKEND_ORIGIN,
  backendAuthorityConfigured,
  localPilotServerEnabled,
  normalizedTenantSelector,
  REALYTICS_SESSION_COOKIE,
  REALYTICS_TENANT_SELECTOR_COOKIE,
} from "./backend-session";
import {
  createSupabaseServerClient,
  supabaseServerConfigured,
} from "./supabase/server";
import { serverIssuedBackendRequestHeaders } from "./backend-request-headers";

function jsonFailure(status: number, error: string): Response {
  return Response.json(
    { error },
    {
      status,
      headers: {
        "Cache-Control": "private, no-cache, no-store, must-revalidate, max-age=0",
      },
    },
  );
}

/**
 * Authenticated server-only FastAPI boundary for Server Components and route
 * handlers. Caller-supplied identity headers are always discarded. Supabase
 * claims are verified before the access token is forwarded, while local pilot
 * sessions remain available only behind the explicit development flag.
 */
export async function fetchAuthenticatedBackend(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new TypeError("Backend path must be an origin-relative path.");
  }
  if (!backendAuthorityConfigured()) {
    return jsonFailure(503, "backend_unavailable");
  }

  const headers = serverIssuedBackendRequestHeaders(init.headers);

  const cookieStore = await cookies();
  let authenticated = false;
  if (supabaseServerConfigured()) {
    try {
      const supabase = await createSupabaseServerClient();
      const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
      if (!claimsError && claimsData?.claims) {
        const { data: sessionData } = await supabase.auth.getSession();
        const accessToken = sessionData.session?.access_token;
        if (accessToken) {
          headers.set("Authorization", `Bearer ${accessToken}`);
          authenticated = true;
          const tenantId = normalizedTenantSelector(
            cookieStore.get(REALYTICS_TENANT_SELECTOR_COOKIE)?.value,
          );
          if (tenantId) headers.set("X-REALYTICS-TENANT-ID", tenantId);
        }
      }
    } catch {
      return jsonFailure(503, "identity_authority_unavailable");
    }
  }

  if (!authenticated && localPilotServerEnabled()) {
    const sessionId = cookieStore.get(REALYTICS_SESSION_COOKIE)?.value;
    if (sessionId) {
      headers.set("X-REALYTICS-SESSION-ID", sessionId);
      authenticated = true;
    }
  }
  if (!authenticated) {
    return jsonFailure(401, "login_required");
  }

  return fetch(`${BACKEND_ORIGIN}${path}`, {
    ...init,
    headers,
    cache: init.cache ?? "no-store",
    signal: init.signal ?? AbortSignal.timeout(10_000),
  });
}
