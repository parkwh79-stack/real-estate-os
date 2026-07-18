import type { Session as SupabaseSession } from "@supabase/supabase-js";
import { getSupabaseBrowserClient } from "./supabase-client";

// Browser calls stay same-origin so local browser security extensions do not
// block the API. Next.js forwards /api/backend/* to the FastAPI service.
export const apiBase = typeof window === "undefined" ? "http://127.0.0.1:8000" : "/api/backend";

export interface RealyticsUser {
  userId: string;
  tenantId?: string;
  email?: string;
  displayName?: string;
  role?: string;
  status?: string;
}

export interface RealyticsSession {
  sessionId: string;
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
  accessToken?: string;
  accessTokenExpiresAt?: number;
}

const STORAGE_KEY = "realytics.auth.session.v1";
const TRANSIENT_AI_SECRET_PREFIX = "realytics.ai.session.";

export interface ResolvedIdentity {
  authenticated: boolean;
  user: RealyticsUser;
  session?: RealyticsSession;
  permissions?: string[];
  authSource?: string;
  identityProvider?: Record<string, unknown>;
  limitations?: string[];
}

export function getStoredAuthSession(): StoredAuthSession | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as StoredAuthSession;
    if (!parsed.session?.sessionId || !parsed.user?.userId) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function setStoredAuthSession(
  value: StoredAuthSession,
  options: { notify?: boolean } = {},
) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  if (options.notify !== false) {
    window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
  }
}

export function clearStoredAuthSession() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
  clearTransientAiSecrets();
  window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
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

export function authHeaders(fallbackUserId = "web"): Record<string, string> {
  const stored = getStoredAuthSession();
  if (stored?.authProvider === "supabase" && stored.accessToken) {
    return {
      Authorization: `Bearer ${stored.accessToken}`,
      "X-REALYTICS-USER-ID": stored.user.userId,
      ...(stored.user.tenantId ? { "X-REALYTICS-TENANT-ID": stored.user.tenantId } : {}),
    };
  }
  if (stored?.session?.sessionId) {
    return {
      "X-REALYTICS-SESSION-ID": stored.session.sessionId,
      "X-REALYTICS-USER-ID": stored.user.userId,
      ...(stored.user.tenantId ? { "X-REALYTICS-TENANT-ID": stored.user.tenantId } : {}),
    };
  }
  return { "X-REALYTICS-USER-ID": fallbackUserId };
}

export async function signInWithSupabasePassword(input: {
  email: string;
  password: string;
}) {
  const client = getSupabaseBrowserClient();
  const { data, error } = await client.auth.signInWithPassword({
    email: input.email.trim(),
    password: input.password,
  });
  if (error) throw new Error(error.message);
  if (!data.session) throw new Error("Supabase 로그인 세션을 만들지 못했습니다.");
  return await persistSupabaseSession(data.session);
}

export async function signUpWithSupabasePassword(input: {
  email: string;
  password: string;
  displayName?: string;
}) {
  const client = getSupabaseBrowserClient();
  const { data, error } = await client.auth.signUp({
    email: input.email.trim(),
    password: input.password,
    options: {
      data: {
        display_name: input.displayName?.trim() || input.email.trim(),
      },
    },
  });
  if (error) throw new Error(error.message);
  if (!data.session) {
    return {
      stored: null,
      requiresEmailConfirmation: true,
    };
  }
  return {
    stored: await persistSupabaseSession(data.session),
    requiresEmailConfirmation: false,
  };
}

export async function refreshSupabaseStoredSession(
  stored: StoredAuthSession,
): Promise<StoredAuthSession | null> {
  if (stored.authProvider !== "supabase") return stored;
  const client = getSupabaseBrowserClient();
  const { data, error } = await client.auth.getSession();
  if (error || !data.session) return null;
  const refreshed: StoredAuthSession = {
    ...stored,
    accessToken: data.session.access_token,
    accessTokenExpiresAt: data.session.expires_at,
    session: {
      ...stored.session,
      expiresAt: data.session.expires_at
        ? new Date(data.session.expires_at * 1000).toISOString()
        : stored.session.expiresAt,
    },
  };
  setStoredAuthSession(refreshed, { notify: false });
  return refreshed;
}

export async function signOutSupabaseSession() {
  const client = getSupabaseBrowserClient();
  const { error } = await client.auth.signOut();
  if (error) throw new Error(error.message);
}

export async function createLocalPilotSession(input: {
  tenantId: string;
  tenantName: string;
  userId: string;
  email?: string;
  displayName: string;
  role: string;
}) {
  await fetch(`${apiBase}/auth/tenants`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      tenantId: input.tenantId,
      name: input.tenantName,
      status: "active",
    }),
  }).then(assertOk);

  await fetch(`${apiBase}/auth/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      userId: input.userId,
      tenantId: input.tenantId,
      email: input.email,
      displayName: input.displayName,
      role: input.role,
      status: "active",
    }),
  }).then(assertOk);

  const sessionResponse = await fetch(`${apiBase}/auth/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      userId: input.userId,
      tenantId: input.tenantId,
      provider: "local-pilot",
      host: "web",
    }),
  }).then(assertOk);

  const sessionBody = await sessionResponse.json() as {
    user: RealyticsUser;
    session: RealyticsSession;
  };
  const me = await resolveCurrentIdentity(sessionBody.session.sessionId);
  const stored: StoredAuthSession = {
    user: sessionBody.user,
    session: sessionBody.session,
    permissions: me.permissions ?? [],
  };
  setStoredAuthSession(stored);
  return stored;
}

export async function loginLocalPilotSession(input: {
  tenantId: string;
  userId: string;
}) {
  const usersResponse = await fetch(
    `${apiBase}/auth/users?tenant_id=${encodeURIComponent(input.tenantId)}&limit=1000`,
    { cache: "no-store" },
  ).then(assertOk);
  const usersBody = await usersResponse.json() as { users?: RealyticsUser[] };
  const existingUser = (usersBody.users ?? []).find((user) => user.userId === input.userId);
  if (!existingUser) {
    throw new Error("등록된 사용자를 찾지 못했습니다. 먼저 회원가입을 진행해 주세요.");
  }

  const sessionResponse = await fetch(`${apiBase}/auth/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      userId: existingUser.userId,
      tenantId: existingUser.tenantId,
      provider: "local-pilot",
      host: "web",
    }),
  }).then(assertOk);

  const sessionBody = await sessionResponse.json() as {
    user: RealyticsUser;
    session: RealyticsSession;
  };
  const me = await resolveCurrentIdentity(sessionBody.session.sessionId);
  const stored: StoredAuthSession = {
    user: sessionBody.user,
    session: sessionBody.session,
    permissions: me.permissions ?? [],
  };
  setStoredAuthSession(stored);
  return stored;
}


export async function resolveCurrentIdentity(
  sessionId?: string,
  bearerToken?: string,
): Promise<ResolvedIdentity> {
  const headers: Record<string, string> = {};
  const stored = getStoredAuthSession();
  const resolvedBearerToken = bearerToken
    ?? (stored?.authProvider === "supabase" ? stored.accessToken : undefined);
  if (resolvedBearerToken) {
    headers.Authorization = `Bearer ${resolvedBearerToken}`;
  } else {
    const resolvedSessionId = sessionId ?? stored?.session.sessionId;
    if (resolvedSessionId) headers["X-REALYTICS-SESSION-ID"] = resolvedSessionId;
  }
  const res = await fetch(`${apiBase}/auth/me`, { cache: "no-store", headers }).then(assertOk);
  return await res.json() as ResolvedIdentity;
}

export async function persistSupabaseSession(
  supabaseSession: SupabaseSession,
): Promise<StoredAuthSession> {
  const identity = await resolveCurrentIdentity(undefined, supabaseSession.access_token);
  if (!identity.authenticated) {
    throw new Error("Supabase 계정이 REALYTICS 사용자로 승인되지 않았습니다.");
  }
  const stored: StoredAuthSession = {
    authProvider: "supabase",
    accessToken: supabaseSession.access_token,
    accessTokenExpiresAt: supabaseSession.expires_at,
    user: identity.user,
    session: {
      sessionId: `supabase:${supabaseSession.user.id}`,
      userId: identity.user.userId,
      tenantId: identity.user.tenantId,
      provider: "supabase",
      host: "web",
      createdAt: new Date().toISOString(),
      expiresAt: supabaseSession.expires_at
        ? new Date(supabaseSession.expires_at * 1000).toISOString()
        : undefined,
    },
    permissions: identity.permissions ?? [],
  };
  setStoredAuthSession(stored);
  return stored;
}

async function assertOk(res: Response) {
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed with ${res.status}`);
  }
  return res;
}
