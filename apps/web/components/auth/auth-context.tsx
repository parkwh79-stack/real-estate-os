"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import {
  apiBase,
  authHeaders,
  clearStoredAuthSession,
  getStoredAuthSession,
  persistSupabaseSession,
  refreshSupabaseStoredSession,
  resolveCurrentIdentity,
  setStoredAuthSession,
  signOutSupabaseSession,
  type RealyticsSession,
  type RealyticsUser,
} from "../../lib/auth-client";
import {
  getSupabaseBrowserClient,
  supabaseConfigured,
} from "../../lib/supabase-client";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  status: AuthStatus;
  user: RealyticsUser | null;
  session: RealyticsSession | null;
  permissions: string[];
  /** Re-run the session check (e.g. after creating a session in settings). */
  refresh: () => void;
  /** Clear the stored session; the provider reacts via the change event. */
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Single source of truth for client-side authentication. Reads the stored
 * session once, validates it against the server, and re-checks whenever the
 * stored session changes (login/logout dispatch `realytics-auth-session-changed`).
 * Components consume `useAuth()` instead of re-implementing the check.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<RealyticsUser | null>(null);
  const [session, setSession] = useState<RealyticsSession | null>(null);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  const logout = useCallback(async () => {
    try {
      const stored = getStoredAuthSession();
      if (stored?.session?.sessionId) {
        await fetch(`${apiBase}/auth/ai-credentials/current`, {
          method: "DELETE",
          headers: authHeaders("web"),
        });
        if (stored.authProvider === "supabase") {
          await signOutSupabaseSession();
        } else {
          await fetch(`${apiBase}/auth/session/current`, {
            method: "DELETE",
            headers: authHeaders("web"),
          });
        }
      }
    } finally {
      // Always clear local state, even when the API is unavailable. The server
      // session expires independently and hardened deployments revoke at IdP.
      clearStoredAuthSession();
    }
  }, []);

  useEffect(() => {
    const stored = getStoredAuthSession();
    if (!stored?.session?.sessionId) {
      setStatus("unauthenticated");
      setUser(null);
      setSession(null);
      setPermissions([]);
      return;
    }

    // Optimistically surface the stored identity while the server validates it,
    // so authenticated reviewers don't see an empty flash on every navigation.
    setUser(stored.user);
    setSession(stored.session);
    setPermissions(stored.permissions ?? []);

    let cancelled = false;
    const validate = async () => {
      const activeStored = stored.authProvider === "supabase"
        ? await refreshSupabaseStoredSession(stored)
        : stored;
      if (!activeStored) {
        throw new Error("Supabase session expired.");
      }
      const identity = await resolveCurrentIdentity(
        activeStored.authProvider === "supabase"
          ? undefined
          : activeStored.session.sessionId,
        activeStored.accessToken,
      );
      return { activeStored, identity };
    };
    validate()
      .then(({ activeStored, identity }) => {
        if (cancelled) return;
        if (!identity.authenticated) {
          clearStoredAuthSession();
          setStatus("unauthenticated");
          setUser(null);
          setSession(null);
          setPermissions([]);
          return;
        }
        setUser(identity.user);
        setSession(identity.session ?? activeStored.session);
        setPermissions(identity.permissions ?? activeStored.permissions ?? []);
        setStoredAuthSession(
          {
            ...activeStored,
            user: identity.user,
            session: identity.session ?? activeStored.session,
            permissions: identity.permissions ?? activeStored.permissions ?? [],
          },
          { notify: false },
        );
        setStatus("authenticated");
      })
      .catch(() => {
        if (cancelled) return;
        // Validation failed (expired/invalid/unreachable): treat as logged out so
        // protected routes consistently fall back to the login page.
        clearStoredAuthSession();
        setStatus("unauthenticated");
        setUser(null);
        setSession(null);
        setPermissions([]);
      });

    return () => {
      cancelled = true;
    };
  }, [nonce]);

  useEffect(() => {
    if (!supabaseConfigured()) return;
    const client = getSupabaseBrowserClient();
    const { data } = client.auth.onAuthStateChange((_event, supabaseSession) => {
      const stored = getStoredAuthSession();
      if (!stored && supabaseSession) {
        void persistSupabaseSession(supabaseSession).catch(() => {
          // A valid Supabase session may still lack REALYTICS tenant/RBAC
          // authorization. Keep the app logged out when the API rejects it.
        });
        return;
      }
      if (stored?.authProvider !== "supabase") return;
      if (!supabaseSession) {
        clearStoredAuthSession();
        return;
      }
      if (stored.accessToken === supabaseSession.access_token) return;
      setStoredAuthSession({
        ...stored,
        accessToken: supabaseSession.access_token,
        accessTokenExpiresAt: supabaseSession.expires_at,
        session: {
          ...stored.session,
          expiresAt: supabaseSession.expires_at
            ? new Date(supabaseSession.expires_at * 1000).toISOString()
            : stored.session.expiresAt,
        },
      });
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    const onChange = () => refresh();
    window.addEventListener("realytics-auth-session-changed", onChange);
    return () => window.removeEventListener("realytics-auth-session-changed", onChange);
  }, [refresh]);

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, session, permissions, refresh, logout }),
    [status, user, session, permissions, refresh, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within <AuthProvider>");
  }
  return ctx;
}

/** Full-screen overlay shown while the session is being checked or before redirect. */
export function AuthFallback() {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-50 px-4 text-slate-950 dark:bg-slate-950 dark:text-white">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-xl dark:border-slate-800 dark:bg-slate-900">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-slate-100 dark:bg-slate-800">
          <span className="text-xl font-black text-slate-900 dark:text-white">RY</span>
        </div>
        <p className="mt-4 text-base font-bold">로그인이 필요합니다</p>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          안전한 접근을 위해 사용자 세션을 확인하고 있습니다.<br />
          잠시 후 로그인 페이지로 이동합니다.
        </p>
      </div>
    </div>
  );
}

/**
 * Gate a protected subtree. Renders children only when authenticated; otherwise
 * shows the fallback and redirects unauthenticated users to /login?next=<path>.
 */
export function RequireAuth({
  children,
  fallback,
}: {
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status !== "unauthenticated") return;
    router.replace("/login?next=/start");
  }, [status, router]);

  if (status !== "authenticated") {
    return <>{fallback ?? <AuthFallback />}</>;
  }
  return <>{children}</>;
}
