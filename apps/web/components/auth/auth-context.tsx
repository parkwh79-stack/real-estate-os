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
import { usePathname, useRouter } from "next/navigation";
import {
  apiBase,
  authHeaders,
  cacheResolvedIdentity,
  clearStoredAuthSession,
  clearTransientAiSecrets,
  isBackendAuthorityUnavailableAuthError,
  isOnboardingRequiredAuthError,
  isTenantSelectionRequiredAuthError,
  loadSupabaseSecurityProfile,
  purgeLegacyBrowserAuthStorage,
  resolveCurrentIdentity,
  signOutSupabaseSession,
  type RealyticsSession,
  type RealyticsUser,
} from "../../lib/auth-client";

export type AuthStatus =
  | "loading"
  | "authenticated"
  | "onboarding"
  | "tenant_selection"
  | "authority_unavailable"
  | "unauthenticated";

export interface AuthContextValue {
  status: AuthStatus;
  user: RealyticsUser | null;
  session: RealyticsSession | null;
  permissions: string[];
  /** Re-run the cookie-backed session check. */
  refresh: () => void;
  /** Revoke the current application session and clear in-memory identity. */
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Single source of truth for client-side authentication. The BFF reads the
 * cookie-backed session, validates it, and returns only non-secret identity.
 * Components consume `useAuth()` instead of re-implementing the check.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<RealyticsUser | null>(null);
  const [session, setSession] = useState<RealyticsSession | null>(null);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [authSource, setAuthSource] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => {
    setStatus((current) => current === "authority_unavailable" ? "loading" : current);
    setNonce((n) => n + 1);
  }, []);
  const logout = useCallback(async () => {
    let sessionRevoked = false;
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
      sessionRevoked = true;
    } finally {
      // Secret residue is never retained, including when revocation fails.
      // Identity state, however, must remain until the authoritative session
      // boundary confirms logout so callers cannot claim a false switch.
      clearTransientAiSecrets();
      if (sessionRevoked) {
        clearStoredAuthSession({ notify: false });
        setStatus("unauthenticated");
        setUser(null);
        setSession(null);
        setPermissions([]);
        setAuthSource(null);
      }
    }
  }, [authSource]);

  useEffect(() => {
    purgeLegacyBrowserAuthStorage();
    if (pathname?.startsWith("/guest")) {
      setStatus("unauthenticated");
      setUser(null);
      setSession(null);
      setPermissions([]);
      setAuthSource(null);
      return;
    }
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
      .catch(async (error) => {
        if (cancelled) return;
        const boundaryStatus: AuthStatus | null =
          isOnboardingRequiredAuthError(error)
            ? "onboarding"
            : isTenantSelectionRequiredAuthError(error)
              ? "tenant_selection"
              : isBackendAuthorityUnavailableAuthError(error)
                ? "authority_unavailable"
                : null;
        if (boundaryStatus) {
          try {
            const profile = await loadSupabaseSecurityProfile();
            if (cancelled) return;
            const email = profile.identities.find((identity) => identity.email)?.email ?? undefined;
            const onboardingUser: RealyticsUser = {
              userId: profile.userId,
              email,
              displayName: email?.split("@")[0],
              role: "unassigned",
              status: boundaryStatus,
              metadata: { principalType: "human" },
            };
            clearStoredAuthSession({ notify: false });
            setUser(onboardingUser);
            setSession({ userId: profile.userId, provider: "supabase", host: "web-bff" });
            setPermissions([]);
            setAuthSource("bearer_token");
            setStatus(boundaryStatus);
            return;
          } catch {
            // A missing/invalid Supabase cookie is genuinely unauthenticated.
          }
        }
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
  }, [nonce, pathname]);

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
        <p className="mt-4 text-base font-bold" role="status">로그인·워크스페이스 확인 중</p>
        <p className="mt-2 text-xs leading-5 text-slate-500">
          사용자 세션과 워크스페이스 권한을 확인하고 있습니다.<br />
          서버를 처음 시작할 때는 최대 2분 정도 걸릴 수 있습니다.
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
  const pathname = usePathname();

  useEffect(() => {
    if (
      status === "onboarding"
      || status === "tenant_selection"
      || status === "authority_unavailable"
    ) {
      router.replace("/onboarding");
    } else if (status === "unauthenticated") {
      const next = pathname?.startsWith("/") ? pathname : "/start";
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [status, router, pathname]);

  if (status !== "authenticated") {
    return <>{fallback ?? <AuthFallback />}</>;
  }
  return <>{children}</>;
}

/** Gate onboarding by Supabase session only; Tenant membership is optional. */
export function RequireSupabaseSession({
  children,
  fallback,
}: {
  children: ReactNode;
  fallback?: ReactNode;
}) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login?next=/onboarding");
    }
  }, [status, router]);

  if (
    status !== "authenticated"
    && status !== "onboarding"
    && status !== "tenant_selection"
    && status !== "authority_unavailable"
  ) {
    return <>{fallback ?? <AuthFallback />}</>;
  }
  return <>{children}</>;
}
