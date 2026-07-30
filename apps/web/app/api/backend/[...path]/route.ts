import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextRequest, NextResponse } from "next/server";
import { backendRequestAllowsAnonymous } from "../../../../lib/backend-access-policy";
import {
  sanitizedBackendResponseHeaders,
  serverIssuedBackendRequestHeaders,
} from "../../../../lib/backend-request-headers";
import {
  createBoundedBackendSignal,
  prepareBackendResponseBody,
  requiresBoundedSessionRedaction,
  streamingBackendRequestInit,
} from "../../../../lib/backend-proxy-contract";
import {
  BACKEND_ORIGIN,
  backendAuthorityConfigured,
  clearRealyticsSessionCookie,
  hardenedSupabaseCookieOptions,
  localPilotServerEnabled,
  normalizedTenantSelector,
  preventAuthenticatedResponseCaching,
  REALYTICS_SESSION_COOKIE,
  REALYTICS_TENANT_SELECTOR_COOKIE,
  requestIsSameOrigin,
  setRealyticsSessionCookie,
} from "../../../../lib/backend-session";

type PendingCookie = {
  name: string;
  value: string;
  options: CookieOptions;
};

function applyPendingAuthState(
  response: NextResponse,
  pendingCookies: PendingCookie[],
  pendingAuthHeaders: Headers,
): NextResponse {
  pendingAuthHeaders.forEach((value, name) => response.headers.set(name, value));
  pendingCookies.forEach(({ name, value, options }) => {
    response.cookies.set(
      name,
      value,
      hardenedSupabaseCookieOptions(options),
    );
  });
  return response;
}

async function proxy(request: NextRequest) {
  if (!backendAuthorityConfigured()) {
    const response = NextResponse.json(
      {
        error: "backend_unavailable",
        reason: "api_origin_not_configured",
      },
      { status: 503 },
    );
    preventAuthenticatedResponseCaching(response);
    return response;
  }

  const backendPath = request.nextUrl.pathname.replace(/^\/api\/backend/, "") || "/";
  const targetUrl = new URL(
    `${BACKEND_ORIGIN}${backendPath}${request.nextUrl.search}`,
  );
  // reviewScope is a BFF-only routing hint. FastAPI remains authoritative for
  // the platform-operator permission; this flag only prevents a remembered
  // Tenant selector from narrowing the operator's cross-Tenant review queue.
  const platformReviewQueue =
    backendPath === "/auth/access-requests"
    && targetUrl.searchParams.get("reviewScope") === "platform";
  targetUrl.searchParams.delete("reviewScope");
  const upstreamRequestId = crypto.randomUUID();

  const localBootstrapRequest =
    backendPath === "/auth/tenants"
    || backendPath === "/auth/users"
    || (backendPath === "/auth/session" && request.method === "POST");
  if (localBootstrapRequest && !localPilotServerEnabled()) {
    return NextResponse.json(
      { error: "local_pilot_auth_disabled" },
      { status: 404 },
    );
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method) && !requestIsSameOrigin(request)) {
    return NextResponse.json({ error: "cross_site_request_rejected" }, { status: 403 });
  }

  const target = targetUrl.toString();
  const headers = serverIssuedBackendRequestHeaders(
    request.headers,
    upstreamRequestId,
  );

  const pendingCookies: PendingCookie[] = [];
  const pendingAuthHeaders = new Headers();
  let verifiedSupabaseBearer: string | null = null;
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (supabaseUrl && supabaseKey) {
    const supabase = createServerClient(supabaseUrl, supabaseKey, {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet, headersToSet) {
          pendingCookies.push(...cookiesToSet);
          cookiesToSet.forEach(({ name, value }) => request.cookies.set(name, value));
          Object.entries(headersToSet).forEach(([name, value]) => {
            pendingAuthHeaders.set(name, value);
          });
        },
      },
    });
    const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
    if (!claimsError && claimsData?.claims) {
      // getSession is used only to retrieve the token for FastAPI forwarding,
      // after getClaims has verified the identity-bearing JWT.
      const { data: sessionData } = await supabase.auth.getSession();
      verifiedSupabaseBearer = sessionData.session?.access_token ?? null;
    }
  }

  const localSessionId = localPilotServerEnabled()
    ? request.cookies.get(REALYTICS_SESSION_COOKIE)?.value
    : undefined;
  if (verifiedSupabaseBearer) {
    headers.set("Authorization", `Bearer ${verifiedSupabaseBearer}`);
    const selectedTenantId = normalizedTenantSelector(
      request.cookies.get(REALYTICS_TENANT_SELECTOR_COOKIE)?.value,
    );
    if (selectedTenantId && !platformReviewQueue) {
      headers.set("X-REALYTICS-TENANT-ID", selectedTenantId);
    }
  } else if (localSessionId) {
    headers.set("X-REALYTICS-SESSION-ID", localSessionId);
  }
  if (
    !verifiedSupabaseBearer
    && !localSessionId
    && !backendRequestAllowsAnonymous(
      request.method,
      backendPath,
      localBootstrapRequest,
    )
  ) {
    const response = applyPendingAuthState(
      NextResponse.json(
        { error: "login_required" },
        { status: 401 },
      ),
      pendingCookies,
      pendingAuthHeaders,
    );
    preventAuthenticatedResponseCaching(response);
    return response;
  }

  const { signal, timeoutSignal } = createBoundedBackendSignal(request.signal);
  const init = streamingBackendRequestInit(
    request.method,
    headers,
    request.body,
    signal,
  );

  try {
    const response = await fetch(target, init);
    const responseHeaders = sanitizedBackendResponseHeaders(response.headers);
    responseHeaders.set("X-Request-Id", upstreamRequestId);

    const preparedBody = await prepareBackendResponseBody(
      response,
      requiresBoundedSessionRedaction(backendPath, request.method),
    );

    const nextResponse = applyPendingAuthState(
      new NextResponse(preparedBody.body, {
        status: response.status,
        headers: responseHeaders,
      }),
      pendingCookies,
      pendingAuthHeaders,
    );
    if (
      preparedBody.localSession
      && backendPath === "/auth/session"
      && request.method === "POST"
      && localPilotServerEnabled()
    ) {
      setRealyticsSessionCookie(
        nextResponse,
        preparedBody.localSession.sessionId,
        preparedBody.localSession.expiresAt,
      );
    }
    if (
      verifiedSupabaseBearer
      || (backendPath === "/auth/session/current" && request.method === "DELETE")
    ) {
      clearRealyticsSessionCookie(nextResponse);
    }
    preventAuthenticatedResponseCaching(nextResponse);
    return nextResponse;
  } catch (error) {
    const timedOut = timeoutSignal.aborted;
    console.error("REALYTICS backend proxy unavailable", {
      requestId: upstreamRequestId,
      backendPath,
      errorName: error instanceof Error ? error.name : "UnknownError",
    });
    const unavailable = applyPendingAuthState(
      NextResponse.json(
        {
          error: timedOut ? "backend_timeout" : "backend_unavailable",
          requestId: upstreamRequestId,
        },
        { status: timedOut ? 504 : 502 },
      ),
      pendingCookies,
      pendingAuthHeaders,
    );
    preventAuthenticatedResponseCaching(unavailable);
    return unavailable;
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
