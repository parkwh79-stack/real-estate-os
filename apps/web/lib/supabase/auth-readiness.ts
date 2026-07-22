import "server-only";

import { BACKEND_ORIGIN } from "../backend-session";

type AuthSettings = {
  external?: Record<string, unknown>;
  disable_signup?: boolean;
  mailer_autoconfirm?: boolean;
  mfa_enabled?: boolean;
};

type IdpStatus = {
  configured?: boolean;
  enabled?: boolean;
  mode?: string;
  provider?: string;
  issuer?: string;
  jwksUrlConfigured?: boolean;
  membershipAuthority?: string;
};

function enabled(value: unknown): boolean {
  if (value === true) return true;
  if (typeof value === "string") return value.toLowerCase() === "true";
  if (value && typeof value === "object" && "enabled" in value) {
    return enabled((value as { enabled?: unknown }).enabled);
  }
  return false;
}

export function providerEnabled(
  settings: AuthSettings | null,
  provider: "google" | "kakao",
): boolean {
  return enabled(settings?.external?.[provider]);
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T | null> {
  try {
    const response = await fetch(url, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) return null;
    return await response.json() as T;
  } catch {
    return null;
  }
}

export async function inspectSupabaseAuthReadiness() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.replace(/\/$/, "");
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  const webOrigin = process.env.REALYTICS_WEB_ORIGIN;
  const configured = Boolean(url && publishableKey);

  const [settings, backend] = await Promise.all([
    configured
      ? fetchJson<AuthSettings>(`${url}/auth/v1/settings`, {
          headers: { apikey: publishableKey! },
        })
      : Promise.resolve(null),
    fetchJson<IdpStatus>(`${BACKEND_ORIGIN}/auth/idp/status`),
  ]);

  const providers = {
    google: providerEnabled(settings, "google"),
    kakao: providerEnabled(settings, "kakao"),
  };
  const backendReady = Boolean(
    (backend?.configured ?? backend?.enabled)
      && ["oidc", "jwks", "rs256"].includes(String(backend.mode || "").toLowerCase())
      && backend.jwksUrlConfigured
      && backend.membershipAuthority === "postgres",
  );
  const canonicalOriginReady = Boolean(
    webOrigin
      && (() => {
        try {
          const parsed = new URL(webOrigin);
          return process.env.NODE_ENV !== "production" || parsed.protocol === "https:";
        } catch {
          return false;
        }
      })(),
  );

  return {
    schemaVersion: "realytics-supabase-auth-readiness-v1",
    checkedAt: new Date().toISOString(),
    configured,
    settingsReachable: settings !== null,
    providers,
    canonicalOriginReady,
    backend: {
      reachable: backend !== null,
      configured: Boolean(backend?.configured ?? backend?.enabled),
      mode: backend?.mode ?? null,
      provider: backend?.provider ?? null,
      issuerConfigured: Boolean(backend?.issuer),
      jwksUrlConfigured: Boolean(backend?.jwksUrlConfigured),
      membershipAuthority: backend?.membershipAuthority ?? null,
      ready: backendReady,
    },
    operational: Boolean(
      configured
        && settings
        && providers.google
        && providers.kakao
        && canonicalOriginReady
        && backendReady,
    ),
    limitations: [
      ...(!configured ? ["supabase_web_configuration_missing"] : []),
      ...(configured && !settings ? ["supabase_auth_settings_unreachable"] : []),
      ...(!providers.google ? ["google_provider_not_enabled"] : []),
      ...(!providers.kakao ? ["kakao_provider_not_enabled"] : []),
      ...(!canonicalOriginReady ? ["canonical_https_web_origin_not_configured"] : []),
      ...(!backendReady ? ["fastapi_oidc_membership_boundary_not_ready"] : []),
      "real_user_callback_refresh_logout_smoke_required",
    ],
  };
}
