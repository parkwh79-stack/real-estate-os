import "server-only";

import type { CookieOptions } from "@supabase/ssr";
import type { NextRequest, NextResponse } from "next/server";

export const BACKEND_ORIGIN =
  process.env.REALYTICS_API_ORIGIN || "http://127.0.0.1:8000";

export const REALYTICS_SESSION_COOKIE =
  process.env.NODE_ENV === "production"
    ? "__Host-realytics-session"
    : "realytics-session";

export function localPilotServerEnabled(): boolean {
  return (
    process.env.NODE_ENV !== "production"
    && process.env.REALYTICS_ENABLE_LOCAL_AUTH === "true"
  );
}

export function legacyOauthSessionExchangeEnabled(): boolean {
  return (
    localPilotServerEnabled()
    && process.env.REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS === "true"
  );
}

export function requestIsSameOrigin(request: NextRequest): boolean {
  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite === "cross-site") return false;

  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).origin === request.nextUrl.origin;
  } catch {
    return false;
  }
}

export function setRealyticsSessionCookie(
  response: NextResponse,
  sessionId: string,
  expiresAt?: string,
) {
  const expires = expiresAt ? new Date(expiresAt) : undefined;
  response.cookies.set(REALYTICS_SESSION_COOKIE, sessionId, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    ...(expires && !Number.isNaN(expires.getTime()) ? { expires } : {}),
  });
}

export function clearRealyticsSessionCookie(response: NextResponse) {
  response.cookies.set(REALYTICS_SESSION_COOKIE, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: new Date(0),
  });
}

export function preventAuthenticatedResponseCaching(response: NextResponse) {
  response.headers.set(
    "Cache-Control",
    "private, no-cache, no-store, must-revalidate, max-age=0",
  );
  response.headers.set("Pragma", "no-cache");
  response.headers.set("Expires", "0");
}

export function hardenedSupabaseCookieOptions(
  options: CookieOptions,
): CookieOptions {
  return {
    ...options,
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
  };
}

export async function realyticsAcceptsBearer(accessToken: string): Promise<boolean> {
  try {
    const response = await fetch(`${BACKEND_ORIGIN}/auth/me`, {
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    });
    if (!response.ok) return false;
    const identity = await response.json() as { authenticated?: boolean };
    return identity.authenticated === true;
  } catch {
    return false;
  }
}
