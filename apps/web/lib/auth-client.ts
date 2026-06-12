export const apiBase = "http://127.0.0.1:8000";

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
}

const STORAGE_KEY = "realytics.auth.session.v1";

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

export function setStoredAuthSession(value: StoredAuthSession) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
}

export function clearStoredAuthSession() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
}

export function authHeaders(fallbackUserId = "web"): Record<string, string> {
  const stored = getStoredAuthSession();
  if (stored?.session?.sessionId) {
    return {
      "X-REALYTICS-SESSION-ID": stored.session.sessionId,
      "X-REALYTICS-USER-ID": stored.user.userId,
    };
  }
  return { "X-REALYTICS-USER-ID": fallbackUserId };
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

export async function resolveCurrentIdentity(sessionId?: string) {
  const headers: Record<string, string> = {};
  const resolvedSessionId = sessionId ?? getStoredAuthSession()?.session.sessionId;
  if (resolvedSessionId) headers["X-REALYTICS-SESSION-ID"] = resolvedSessionId;
  const res = await fetch(`${apiBase}/auth/me`, { cache: "no-store", headers }).then(assertOk);
  return await res.json() as {
    authenticated: boolean;
    user: RealyticsUser;
    session?: RealyticsSession;
    permissions?: string[];
    authSource?: string;
    identityProvider?: Record<string, unknown>;
    limitations?: string[];
  };
}

async function assertOk(res: Response) {
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed with ${res.status}`);
  }
  return res;
}
