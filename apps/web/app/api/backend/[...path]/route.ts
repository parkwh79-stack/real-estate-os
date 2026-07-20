import { createServerClient, type CookieOptions } from "@supabase/ssr";
import { NextRequest, NextResponse } from "next/server";
import {
  BACKEND_ORIGIN,
  clearRealyticsSessionCookie,
  hardenedSupabaseCookieOptions,
  localPilotServerEnabled,
  preventAuthenticatedResponseCaching,
  REALYTICS_SESSION_COOKIE,
  requestIsSameOrigin,
  setRealyticsSessionCookie,
} from "../../../../lib/backend-session";

type PendingCookie = {
  name: string;
  value: string;
  options: CookieOptions;
};

async function proxy(request: NextRequest) {
  const backendPath = request.nextUrl.pathname.replace(/^\/api\/backend/, "") || "/";

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

  const target = `${BACKEND_ORIGIN}${backendPath}${request.nextUrl.search}`;
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  headers.delete("cookie");
  headers.delete("authorization");
  headers.delete("x-realytics-session-id");
  headers.delete("x-realytics-user-id");
  headers.delete("x-realytics-tenant-id");
  headers.delete("x-realytics-bff");

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
  } else if (localSessionId) {
    headers.set("X-REALYTICS-SESSION-ID", localSessionId);
  }

  const init: RequestInit = {
    method: request.method,
    headers,
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  try {
    const response = await fetch(target, init);
    const responseHeaders = new Headers(response.headers);
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("content-length");
    responseHeaders.delete("set-cookie");
    pendingAuthHeaders.forEach((value, name) => responseHeaders.set(name, value));

    let responseBody = await response.arrayBuffer();
    let localSession:
      | { sessionId: string; expiresAt?: string }
      | null = null;
    if (
      response.ok
      && response.headers.get("content-type")?.includes("application/json")
      && (
        backendPath === "/auth/me"
        || (backendPath === "/auth/session" && request.method === "POST")
      )
    ) {
      const parsed = JSON.parse(new TextDecoder().decode(responseBody)) as {
        session?: { sessionId?: string; expiresAt?: string };
      };
      if (parsed.session?.sessionId) {
        localSession = {
          sessionId: parsed.session.sessionId,
          expiresAt: parsed.session.expiresAt,
        };
        delete parsed.session.sessionId;
      }
      responseBody = new TextEncoder().encode(JSON.stringify(parsed)).buffer;
    }

    const nextResponse = new NextResponse(responseBody, {
      status: response.status,
      headers: responseHeaders,
    });
    pendingCookies.forEach(({ name, value, options }) => {
      nextResponse.cookies.set(
        name,
        value,
        hardenedSupabaseCookieOptions(options),
      );
    });
    if (
      localSession
      && backendPath === "/auth/session"
      && request.method === "POST"
      && localPilotServerEnabled()
    ) {
      setRealyticsSessionCookie(
        nextResponse,
        localSession.sessionId,
        localSession.expiresAt,
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
    return NextResponse.json(
      { error: "backend_unavailable", detail: error instanceof Error ? error.message : "Backend request failed" },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
