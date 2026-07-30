import "server-only";

import type { CookieOptions } from "@supabase/ssr";
import type { NextRequest, NextResponse } from "next/server";
import {
  classifyRealyticsBearerIdentity,
  type RealyticsBearerIdentityResult,
} from "./backend-identity-result";
import {
  classifyInvitationAcceptanceResponse,
  type InvitationAcceptanceResult,
} from "./invitation-intent";
import { resolveBackendOrigin } from "./backend-origin-contract";
export { safeInternalNextPath } from "./safe-navigation";
export type { InvitationAcceptanceResult } from "./invitation-intent";

export const BACKEND_ORIGIN = resolveBackendOrigin(
  process.env.REALYTICS_API_ORIGIN,
  process.env.NODE_ENV,
) ?? "";

/**
 * Local development may use the loopback FastAPI default. A deployed Web/BFF
 * must receive an explicit API origin; otherwise it fails closed instead of
 * spending every request on an unreachable localhost connection.
 */
export function backendAuthorityConfigured(): boolean {
  return Boolean(BACKEND_ORIGIN);
}

export const REALYTICS_SESSION_COOKIE =
  process.env.NODE_ENV === "production"
    ? "__Host-realytics-session"
    : "realytics-session";

export const REALYTICS_INVITATION_COOKIE =
  process.env.NODE_ENV === "production"
    ? "__Host-realytics-invitation"
    : "realytics-invitation";

export const REALYTICS_PASSWORD_RECOVERY_COOKIE =
  process.env.NODE_ENV === "production"
    ? "__Host-realytics-password-recovery"
    : "realytics-password-recovery";

export const REALYTICS_TENANT_SELECTOR_COOKIE =
  process.env.NODE_ENV === "production"
    ? "__Host-realytics-tenant"
    : "realytics-tenant";

export type RealyticsAccessState =
  | "member"
  | "tenant_selection_required"
  | "onboarding_required"
  | "authority_unavailable";

export const SUPABASE_PASSWORD_POLICY = Object.freeze({
  minLength: 12,
  maxLength: 128,
  requiresLowercase: true,
  requiresUppercase: true,
  requiresDigit: true,
  requiresSymbol: true,
});

const AUTH_EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAX_AUTH_EMAIL_LENGTH = 254;
const MAX_AUTH_DISPLAY_NAME_LENGTH = 120;
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

export function normalizedAuthEmail(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const email = value.trim().toLowerCase();
  if (
    !email
    || email.length > MAX_AUTH_EMAIL_LENGTH
    || !AUTH_EMAIL_PATTERN.test(email)
    || hasAsciiControlCharacter(email)
  ) {
    return null;
  }
  return email;
}

export function normalizedAuthDisplayName(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const displayName = value.trim();
  if (
    !displayName
    || displayName.length > MAX_AUTH_DISPLAY_NAME_LENGTH
    || hasAsciiControlCharacter(displayName)
  ) {
    return null;
  }
  return displayName;
}

export function validSupabaseLoginPassword(value: unknown): value is string {
  return Boolean(
    typeof value === "string"
    && value
    && value.length <= SUPABASE_PASSWORD_POLICY.maxLength
    && !value.includes("\u0000"),
  );
}

export function validNewSupabasePassword(value: unknown): value is string {
  return Boolean(
    validSupabaseLoginPassword(value)
    && value.length >= SUPABASE_PASSWORD_POLICY.minLength
    && /[a-z]/.test(value)
    && /[A-Z]/.test(value)
    && /[0-9]/.test(value)
    && /[^A-Za-z0-9]/.test(value)
  );
}

function hasAsciiControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint <= 31 || codePoint === 127;
  });
}

export function localPilotServerEnabled(): boolean {
  return (
    process.env.NODE_ENV !== "production" &&
    process.env.REALYTICS_ENABLE_LOCAL_AUTH === "true"
  );
}

export function legacyOauthSessionExchangeEnabled(): boolean {
  return (
    localPilotServerEnabled() &&
    process.env.REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS === "true"
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

export function canonicalWebOrigin(request: NextRequest): string | null {
  const configured = process.env.REALYTICS_WEB_ORIGIN;
  if (configured) {
    try {
      const parsed = new URL(configured);
      if (
        process.env.NODE_ENV === "production" &&
        parsed.protocol !== "https:"
      ) {
        return null;
      }
      return parsed.origin;
    } catch {
      return null;
    }
  }
  if (process.env.NODE_ENV === "production") return null;
  return request.nextUrl.origin;
}

export function canonicalAuthRequestUrl(
  request: NextRequest,
  canonicalOrigin: string,
): URL | null {
  const canonicalHost = new URL(canonicalOrigin).host.toLowerCase();
  const requestHost = request.headers.get("host")?.toLowerCase();
  if (requestHost === canonicalHost) return null;

  // PKCE verifier and invitation cookies are host-only. Canonicalize the
  // request before either cookie is issued so localhost/127.0.0.1 (or an
  // alternate production host) can never split the OAuth start and callback.
  // Next.js can normalize request.nextUrl to the configured dev host, so the
  // actual HTTP Host header is the comparison boundary here. The redirect
  // target remains the independently configured canonical origin.
  const destination = new URL(request.nextUrl.pathname, canonicalOrigin);
  destination.search = request.nextUrl.search;
  return destination;
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

export function setInvitationIntentCookie(
  response: NextResponse,
  token: string,
) {
  response.cookies.set(REALYTICS_INVITATION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 30 * 60,
  });
}

export function clearInvitationIntentCookie(response: NextResponse) {
  response.cookies.set(REALYTICS_INVITATION_COOKIE, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: new Date(0),
  });
}

export function setPasswordRecoveryIntentCookie(response: NextResponse) {
  response.cookies.set(REALYTICS_PASSWORD_RECOVERY_COOKIE, "pending", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 10 * 60,
  });
}

export function clearPasswordRecoveryIntentCookie(response: NextResponse) {
  response.cookies.set(REALYTICS_PASSWORD_RECOVERY_COOKIE, "", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    expires: new Date(0),
  });
}

export function normalizedTenantSelector(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const tenantId = value.trim();
  if (
    !tenantId
    || tenantId.length > 128
    || hasAsciiControlCharacter(tenantId)
    || !/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(tenantId)
  ) {
    return null;
  }
  return tenantId;
}

export function setTenantSelectorCookie(
  response: NextResponse,
  tenantId: string,
) {
  response.cookies.set(REALYTICS_TENANT_SELECTOR_COOKIE, tenantId, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 12 * 60 * 60,
  });
}

export function clearTenantSelectorCookie(response: NextResponse) {
  response.cookies.set(REALYTICS_TENANT_SELECTOR_COOKIE, "", {
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

export async function realyticsAcceptsBearer(
  accessToken: string,
  tenantId?: string | null,
): Promise<RealyticsBearerIdentityResult> {
  if (!backendAuthorityConfigured()) {
    return { status: "backend_unavailable" };
  }
  try {
    const selectedTenantId = normalizedTenantSelector(tenantId);
    const response = await fetch(`${BACKEND_ORIGIN}/auth/me`, {
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        ...(selectedTenantId
          ? { "X-REALYTICS-TENANT-ID": selectedTenantId }
          : {}),
      },
      signal: AbortSignal.timeout(10_000),
    });
    const identity = (await response.json().catch(() => null)) as unknown;
    const classified = classifyRealyticsBearerIdentity(
      response.status,
      identity,
    );
    // Some deployed FastAPI revisions used 503 while the identity DSN was
    // healthy but the issuer/subject had no membership row. Preserve those
    // explicit domain errors as onboarding. Unknown 503s and the dedicated
    // authority-unavailable codes remain fail-closed as an unavailable store.
    if (
      classified.status === "backend_unavailable"
      && ONBOARDING_IDENTITY_ERRORS.has(backendIdentityErrorCode(identity))
    ) {
      return { status: "not_provisioned" };
    }
    return classified;
  } catch {
    return { status: "backend_unavailable" };
  }
}

function backendIdentityErrorCode(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const detail = (payload as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return "";
  const error = (detail as { error?: unknown }).error;
  return typeof error === "string" ? error : "";
}

export function accessStateForIdentityResult(
  result: RealyticsBearerIdentityResult,
): RealyticsAccessState {
  switch (result.status) {
    case "accepted":
      return "member";
    case "tenant_selection_required":
      return "tenant_selection_required";
    case "not_provisioned":
      return "onboarding_required";
    case "identity_authority_unavailable":
    case "backend_unavailable":
      return "authority_unavailable";
  }
}

export async function acceptRealyticsInvitation(
  accessToken: string,
  token: string,
): Promise<InvitationAcceptanceResult> {
  if (!backendAuthorityConfigured()) {
    return {
      state: "authority_unavailable",
      errorCode: "identity_invitation_authority_unavailable",
      httpStatus: 503,
      tenantId: null,
      currentPolicyVersions: null,
    };
  }
  try {
    const response = await fetch(`${BACKEND_ORIGIN}/auth/invitations/accept`, {
      method: "POST",
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ token }),
      signal: AbortSignal.timeout(10_000),
    });
    const payload = await response.json().catch(() => null) as unknown;
    return classifyInvitationAcceptanceResponse(response.status, payload);
  } catch {
    return {
      state: "authority_unavailable",
      errorCode: "identity_invitation_authority_unavailable",
      httpStatus: 503,
      tenantId: null,
      currentPolicyVersions: null,
    };
  }
}
