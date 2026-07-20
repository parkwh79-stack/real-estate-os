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

export async function signOutSupabaseSession() {
  if (!supabaseConfigured()) return;
  await fetch("/api/auth/supabase/signout", {
    method: "POST",
    headers: authHeaders(),
  }).then(assertOk);
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
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : typeof body.error === "string"
          ? body.error
          : `Request failed with ${res.status}`;
    throw new Error(detail);
  }
  return res;
}
