import { supabaseConfigured } from "./supabase-client";
import { localPilotAuthEnabled } from "./auth-policy";

// Browser calls stay same-origin. The Next.js BFF verifies cookie-backed auth
// and adds the bearer/session header only on the server-to-server hop.
export const apiBase =
  typeof window === "undefined" ? "http://127.0.0.1:8000" : "/api/backend";

export interface RealyticsUser {
  userId: string;
  tenantId?: string;
  email?: string;
  displayName?: string;
  role?: string;
  status?: string;
}

export interface RealyticsSession {
  sessionId?: string;
  userId: string;
  tenantId?: string;
  provider?: string;
  host?: string;
  createdAt?: string;
  expiresAt?: string;
}

export interface StoredAuthSession {
  user: RealyticsUser;
  session: RealyticsSession;
  permissions: string[];
  authProvider?: "realytics" | "supabase";
}

export interface ResolvedIdentity {
  authenticated: boolean;
  user: RealyticsUser;
  session?: RealyticsSession;
  permissions?: string[];
  authSource?: string;
  identityProvider?: Record<string, unknown>;
  limitations?: string[];
}

export interface SupabaseAuthReadiness {
  schemaVersion: string;
  checkedAt: string;
  configured: boolean;
  settingsReachable: boolean;
  providers: { google: boolean; kakao: boolean };
  providerUsable: { google: boolean; kakao: boolean };
  providerReady: { google: boolean; kakao: boolean };
  canonicalOriginReady: boolean;
  configurationReady: boolean;
  backend: {
    reachable: boolean;
    configured: boolean;
    mode: string | null;
    provider: string | null;
    issuerConfigured: boolean;
    jwksUrlConfigured: boolean;
    membershipAuthority: string | null;
    ready: boolean;
  };
  operational: boolean;
  limitations: string[];
}

export interface SupabaseSecurityProfile {
  schemaVersion: string;
  userId: string;
  requestedProvider: "google" | "kakao" | null;
  identities: Array<{
    identityId: string;
    provider: string;
    email: string | null;
    createdAt: string | null;
    lastSignInAt: string | null;
  }>;
  factors: Array<{
    factorId: string;
    factorType: string;
    status: string;
    friendlyName: string | null;
    createdAt: string;
    updatedAt: string;
  }>;
  assurance: {
    currentLevel: "aal1" | "aal2" | null;
    nextLevel: "aal1" | "aal2" | null;
    currentAuthenticationMethods: unknown[];
  };
  operationalForHighRiskActions: boolean;
  limitations: string[];
}

export interface TotpEnrollment {
  schemaVersion: string;
  factorId: string;
  factorType: "totp";
  friendlyName: string | null;
  qrCode: string;
  secret: string;
  uri: string;
}

export type TenantInvitationRole =
  | "admin"
  | "reviewer"
  | "analyst"
  | "viewer"
  | "legal_reviewer"
  | "legal_counsel_approver"
  | "external_counsel"
  | "compliance_officer";

export interface TenantInvitation {
  invitationId: string;
  tenantId: string;
  role: TenantInvitationRole;
  status: "pending" | "accepted" | "revoked" | "expired";
  invitedEmail: string | null;
  invitedIssuer: string | null;
  invitedSubject: string | null;
  issuedByIssuer: string;
  issuedBySubject: string;
  issuedAt: string;
  expiresAt: string;
  acceptedByIssuer: string | null;
  acceptedBySubject: string | null;
  acceptedAt: string | null;
  revokedAt: string | null;
  revocationReason: string | null;
}

export interface IssuedTenantInvitation {
  invitation: TenantInvitation;
  token: string;
  tokenPolicy: "shown_once_never_persisted_in_plaintext";
}

const LEGACY_STORAGE_KEY = "realytics.auth.session.v1";
const TRANSIENT_AI_SECRET_PREFIX = "realytics.ai.session.";
let inMemoryIdentity: StoredAuthSession | null = null;

/**
 * Compatibility accessor for client components that need a display actor ID.
 * It returns process memory only; no auth material is persisted by REALYTICS.
 */
export function getStoredAuthSession(): StoredAuthSession | null {
  return inMemoryIdentity;
}

export function setStoredAuthSession(
  value: StoredAuthSession,
  options: { notify?: boolean } = {},
) {
  inMemoryIdentity = value;
  if (typeof window !== "undefined" && options.notify !== false) {
    window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
  }
}

export function clearStoredAuthSession(options: { notify?: boolean } = {}) {
  inMemoryIdentity = null;
  if (typeof window === "undefined") return;
  purgeLegacyBrowserAuthStorage();
  clearTransientAiSecrets();
  if (options.notify !== false) {
    window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
  }
}

export function purgeLegacyBrowserAuthStorage() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(LEGACY_STORAGE_KEY);
}

export function clearTransientAiSecrets() {
  if (typeof window === "undefined") return;
  for (let i = window.localStorage.length - 1; i >= 0; i -= 1) {
    const key = window.localStorage.key(i);
    if (key?.startsWith(TRANSIENT_AI_SECRET_PREFIX)) {
      window.localStorage.removeItem(key);
    }
  }
  for (let i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
    const key = window.sessionStorage.key(i);
    if (key?.startsWith(TRANSIENT_AI_SECRET_PREFIX)) {
      window.sessionStorage.removeItem(key);
    }
  }
}

/**
 * Authentication headers are deliberately absent in browser code. The custom
 * marker identifies an intentional same-origin BFF call but carries no
 * authority; the BFF strips it before forwarding.
 */
export function authHeaders(fallbackUserId = "web"): Record<string, string> {
  void fallbackUserId;
  return { "X-REALYTICS-BFF": "same-origin" };
}

export async function signInWithSupabasePassword(input: {
  email: string;
  password: string;
}) {
  await fetch("/api/auth/supabase/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "login",
      email: input.email.trim(),
      password: input.password,
    }),
  }).then(assertOk);
  return await requireResolvedIdentity("Supabase 계정이 REALYTICS 사용자로 승인되지 않았습니다.");
}

export async function signUpWithSupabasePassword(input: {
  email: string;
  password: string;
  displayName?: string;
}) {
  const response = await fetch("/api/auth/supabase/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "signup",
      email: input.email.trim(),
      password: input.password,
      displayName: input.displayName?.trim(),
    }),
  }).then(assertOk);
  const result = await response.json() as {
    authenticated?: boolean;
    requiresEmailConfirmation?: boolean;
  };
  if (result.requiresEmailConfirmation || !result.authenticated) {
    return {
      stored: null,
      requiresEmailConfirmation: true,
    };
  }
  return {
    stored: await requireResolvedIdentity(
      "Supabase 계정이 REALYTICS 사용자로 승인되지 않았습니다.",
    ),
    requiresEmailConfirmation: false,
  };
}

export async function signOutSupabaseSession(
  scope: "local" | "global" | "others" = "local",
) {
  if (!supabaseConfigured()) return;
  await fetch("/api/auth/supabase/signout", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  }).then(assertOk);
}

export async function loadSupabaseAuthReadiness(): Promise<SupabaseAuthReadiness> {
  const response = await fetch("/api/auth/supabase/readiness", { cache: "no-store" })
    .then(assertOk);
  return await response.json() as SupabaseAuthReadiness;
}

export async function loadSupabaseSecurityProfile(): Promise<SupabaseSecurityProfile> {
  const response = await fetch("/api/auth/supabase/security", { cache: "no-store" })
    .then(assertOk);
  return await response.json() as SupabaseSecurityProfile;
}

export function startSupabaseIdentityLink(
  provider: "google" | "kakao",
  nextPath = "/settings",
) {
  if (typeof window === "undefined") {
    throw new Error("Identity linking must start in the browser.");
  }
  const safeNext = nextPath.startsWith("/") && !nextPath.startsWith("//")
    ? nextPath
    : "/settings";
  const start = new URL(
    "/api/auth/supabase/identity/link",
    window.location.origin,
  );
  start.searchParams.set("provider", provider);
  start.searchParams.set("next", safeNext);
  window.location.assign(start.toString());
}

async function postSupabaseSecurityAction<T>(body: Record<string, unknown>): Promise<T> {
  const response = await fetch("/api/auth/supabase/security", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(assertOk);
  return await response.json() as T;
}

export async function enrollSupabaseTotp(
  friendlyName = "REALYTICS Authenticator",
): Promise<TotpEnrollment> {
  return await postSupabaseSecurityAction<TotpEnrollment>({
    action: "mfa_enroll_totp",
    friendlyName,
  });
}

export async function verifySupabaseTotp(factorId: string, code: string) {
  return await postSupabaseSecurityAction<{ verified: boolean; assuranceLevel: "aal2" }>({
    action: "mfa_verify_totp",
    factorId,
    code,
  });
}

export async function reauthenticateSupabaseTotp(factorId: string, code: string) {
  return await postSupabaseSecurityAction<{
    schemaVersion: "realytics-mfa-reauthentication-v1";
    verified: true;
    assuranceLevel: "aal2";
  }>({
    action: "mfa_reauthenticate_totp",
    factorId,
    code,
  });
}

export async function unenrollSupabaseMfa(factorId: string) {
  return await postSupabaseSecurityAction<{ unenrolled: boolean; factorId: string }>({
    action: "mfa_unenroll",
    factorId,
  });
}

export async function unlinkSupabaseIdentity(identityId: string) {
  return await postSupabaseSecurityAction<{
    unlinked: boolean;
    identityId: string;
    provider: string;
  }>({
    action: "identity_unlink",
    identityId,
  });
}

export async function loadTenantInvitations(
  limit = 100,
): Promise<TenantInvitation[]> {
  const response = await fetch(
    `${apiBase}/auth/invitations?limit=${encodeURIComponent(String(limit))}`,
    { cache: "no-store", headers: authHeaders() },
  ).then(assertOk);
  const body = await response.json() as {
    invitations?: TenantInvitation[];
  };
  return body.invitations ?? [];
}

export async function issueTenantInvitation(input: {
  role: TenantInvitationRole;
  invitedEmail?: string;
  expiresInHours?: number;
}): Promise<IssuedTenantInvitation> {
  const response = await fetch(`${apiBase}/auth/invitations`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      role: input.role,
      invitedEmail: input.invitedEmail?.trim() || undefined,
      expiresInHours: input.expiresInHours ?? 72,
    }),
  }).then(assertOk);
  return await response.json() as IssuedTenantInvitation;
}

export async function revokeTenantInvitation(
  invitationId: string,
  reason: string,
) {
  const response = await fetch(
    `${apiBase}/auth/invitations/${encodeURIComponent(invitationId)}/revoke`,
    {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason.trim() }),
    },
  ).then(assertOk);
  return await response.json() as {
    invitation: { invitationId: string; status: "revoked" };
  };
}

export async function signInWithSupabaseOAuth(
  provider: "google" | "kakao",
  nextPath = "/start",
) {
  if (typeof window === "undefined") {
    throw new Error("Social login must start in the browser.");
  }
  const safeNext =
    nextPath.startsWith("/") && !nextPath.startsWith("//") ? nextPath : "/start";
  const start = new URL("/api/auth/supabase/oauth", window.location.origin);
  start.searchParams.set("provider", provider);
  start.searchParams.set("next", safeNext);
  window.location.assign(start.toString());
}

export async function createLocalPilotSession(input: {
  tenantId: string;
  tenantName: string;
  userId: string;
  email?: string;
  displayName: string;
  role: string;
}) {
  if (!localPilotAuthEnabled()) {
    throw new Error("Local pilot authentication is disabled.");
  }
  await fetch(`${apiBase}/auth/tenants`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      tenantId: input.tenantId,
      name: input.tenantName,
      status: "active",
    }),
  }).then(assertOk);

  await fetch(`${apiBase}/auth/users`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      userId: input.userId,
      tenantId: input.tenantId,
      email: input.email,
      displayName: input.displayName,
      role: input.role,
      status: "active",
    }),
  }).then(assertOk);

  await createOpaqueLocalSession({
    userId: input.userId,
    tenantId: input.tenantId,
  });
  return await requireResolvedIdentity("로컬 파일럿 세션을 확인하지 못했습니다.");
}

export async function loginLocalPilotSession(input: {
  tenantId: string;
  userId: string;
}) {
  if (!localPilotAuthEnabled()) {
    throw new Error("Local pilot authentication is disabled.");
  }
  const usersResponse = await fetch(
    `${apiBase}/auth/users?tenant_id=${encodeURIComponent(input.tenantId)}&limit=1000`,
    { cache: "no-store", headers: authHeaders() },
  ).then(assertOk);
  const usersBody = await usersResponse.json() as { users?: RealyticsUser[] };
  const existingUser = (usersBody.users ?? []).find(
    (user) => user.userId === input.userId,
  );
  if (!existingUser) {
    throw new Error("등록된 사용자를 찾지 못했습니다. 먼저 회원가입을 진행해 주세요.");
  }

  await createOpaqueLocalSession({
    userId: existingUser.userId,
    tenantId: existingUser.tenantId,
  });
  return await requireResolvedIdentity("로컬 파일럿 세션을 확인하지 못했습니다.");
}

export async function resolveCurrentIdentity(): Promise<ResolvedIdentity> {
  const res = await fetch(`${apiBase}/auth/me`, {
    cache: "no-store",
    headers: authHeaders(),
  }).then(assertOk);
  return await res.json() as ResolvedIdentity;
}

export async function exchangeOauthSession(sessionId: string) {
  const response = await fetch("/api/auth/session-exchange", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionId }),
  }).then(assertOk);
  const identity = await response.json() as ResolvedIdentity;
  return cacheResolvedIdentity(identity);
}

export function cacheResolvedIdentity(identity: ResolvedIdentity): StoredAuthSession {
  if (!identity.authenticated || !identity.user?.userId) {
    throw new Error("인증된 REALYTICS 사용자를 확인하지 못했습니다.");
  }
  const session: RealyticsSession = identity.session ?? {
    userId: identity.user.userId,
    tenantId: identity.user.tenantId,
    provider: identity.authSource === "bearer_token" ? "supabase" : "realytics",
    host: "web-bff",
  };
  const stored: StoredAuthSession = {
    authProvider:
      identity.authSource === "bearer_token" ? "supabase" : "realytics",
    user: identity.user,
    session,
    permissions: identity.permissions ?? [],
  };
  setStoredAuthSession(stored, { notify: false });
  return stored;
}

async function requireResolvedIdentity(message: string) {
  const identity = await resolveCurrentIdentity();
  if (!identity.authenticated) throw new Error(message);
  const stored = cacheResolvedIdentity(identity);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
  }
  return stored;
}

async function createOpaqueLocalSession(input: {
  userId: string;
  tenantId?: string;
}) {
  await fetch(`${apiBase}/auth/session`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      userId: input.userId,
      tenantId: input.tenantId,
      provider: "local-pilot",
      host: "web-bff",
    }),
  }).then(assertOk);
}

async function assertOk(res: Response) {
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as {
      detail?: unknown;
      error?: unknown;
    };
    const nestedError = (
      body.detail
      && typeof body.detail === "object"
      && "error" in body.detail
      && typeof body.detail.error === "string"
    ) ? body.detail.error : null;
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : nestedError
          ? nestedError
        : typeof body.error === "string"
          ? body.error
          : `Request failed with ${res.status}`;
    throw new Error(detail);
  }
  return res;
}
