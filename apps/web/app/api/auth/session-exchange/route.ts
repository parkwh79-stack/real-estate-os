import { NextRequest, NextResponse } from "next/server";
import {
  BACKEND_ORIGIN,
  legacyOauthSessionExchangeEnabled,
  preventAuthenticatedResponseCaching,
  requestIsSameOrigin,
  setRealyticsSessionCookie,
} from "../../../../lib/backend-session";

type ExchangePayload = {
  sessionId?: unknown;
};

export async function POST(request: NextRequest) {
  if (!legacyOauthSessionExchangeEnabled()) {
    return NextResponse.json(
      { error: "legacy_oauth_session_exchange_disabled" },
      { status: 404 },
    );
  }
  if (!requestIsSameOrigin(request)) {
    return NextResponse.json({ error: "cross_site_request_rejected" }, { status: 403 });
  }

  const payload = await request.json().catch(() => null) as ExchangePayload | null;
  const sessionId =
    typeof payload?.sessionId === "string" ? payload.sessionId.trim() : "";
  if (!sessionId || sessionId.length > 512) {
    return NextResponse.json({ error: "invalid_session_exchange" }, { status: 400 });
  }

  // PROTOTYPE-ONLY: This compatibility exchange is restricted to development
  // and test. Production social login uses Supabase PKCE and /auth/callback.
  const backendResponse = await fetch(`${BACKEND_ORIGIN}/auth/me`, {
    cache: "no-store",
    headers: {
      "X-REALYTICS-SESSION-ID": sessionId,
    },
  });
  const identity = await backendResponse.json().catch(() => null) as {
    authenticated?: boolean;
    session?: { sessionId?: string; expiresAt?: string };
  } | null;
  if (!backendResponse.ok || !identity?.authenticated) {
    return NextResponse.json({ error: "invalid_session_exchange" }, { status: 401 });
  }

  const expiresAt = identity.session?.expiresAt;
  if (identity.session) delete identity.session.sessionId;
  const response = NextResponse.json(identity);
  setRealyticsSessionCookie(response, sessionId, expiresAt);
  preventAuthenticatedResponseCaching(response);
  return response;
}
