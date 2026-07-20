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
  cacheResolvedIdentity,
  clearStoredAuthSession,
  purgeLegacyBrowserAuthStorage,
  resolveCurrentIdentity,
  signOutSupabaseSession,
  type RealyticsSession,
  type RealyticsUser,
} from "../../lib/auth-client";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  status: AuthStatus;
  user: RealyticsUser | null;
  session: RealyticsSession | null;
  permissions: string[];
  /** Re-run the cookie-backed session check. */
  refresh: () => void;
  /** Revoke the current provider session and clear in-memory identity. */
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Single source of truth for client-side authentication. The BFF reads the
 * cookie-backed session, validates it, and returns only non-secret identity.
 * Components consume `useAuth()` instead of re-implementing the check.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<RealyticsUser | null>(null);
  const [session, setSession] = useState<RealyticsSession | null>(null);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [authSource, setAuthSource] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  const logout = useCallback(async () => {
    try {
      await fetch(`${apiBase}/auth/ai-credentials/current`, {
        method: "DELETE",
        headers: authHeaders("web"),
      }).catch(() => undefined);
      if (authSource !== "bearer_token") {
        await fetch(`${apiBase}/auth/session/current`, {
          method: "DELETE",
          headers: authHeaders("web"),
        }).catch(() => undefined);
      }
      await signOutSupabaseSession();
    } finally {
      clearStoredAuthSession();
      setAuthSource(null);
    }
  }, [authSource]);

  useEffect(() => {
    purgeLegacyBrowserAuthStorage();
    let cancelled = false;
    resolveCurrentIdentity()
      .then((identity) => {
        if (cancelled) return;
        if (!identity.authenticated) {
          clearStoredAuthSession({ notify: false });
          setStatus("unauthenticated");
          setUser(null);
          setSession(null);
          setPermissions([]);
          setAuthSource(null);
          return;
        }
        const activeStored = cacheResolvedIdentity(identity);
        setUser(identity.user);
        setSession(activeStored.session);
        setPermissions(activeStored.permissions);
        setAuthSource(identity.authSource ?? null);
        setStatus("authenticated");
      })
      .catch(() => {
        if (cancelled) return;
        clearStoredAuthSession({ notify: false });
        setStatus("unauthenticated");
        setUser(null);
        setSession(null);
        setPermissions([]);
        setAuthSource(null);
      });

    return () => {
      cancelled = true;
    };
  }, [nonce]);

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
