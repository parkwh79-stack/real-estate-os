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
  const providerUsable = {
    google: providers.google,
    // Hosted GoTrue currently asks Kakao for account_email by default. Keep
    // this provider fail-closed until a Biz App consent item has completed an
    // actual round trip; an enabled Supabase toggle alone is insufficient.
    kakao: providers.kakao
      && process.env.REALYTICS_KAKAO_OAUTH_SCOPE_VERIFIED === "true",
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
  const sharedBoundaryReady = Boolean(
    configured
      && settings
      && canonicalOriginReady
      && backendReady,
  );
  const providerReady = {
    google: sharedBoundaryReady && providerUsable.google,
    kakao: sharedBoundaryReady && providerUsable.kakao,
  };
  // Overall readiness intentionally remains stricter than a provider-specific
  // live smoke: the product-level gate requires every supported provider.
  const configurationReady = providerReady.google && providerReady.kakao;

  return {
    schemaVersion: "realytics-supabase-auth-readiness-v1",
    checkedAt: new Date().toISOString(),
    configured,
    settingsReachable: settings !== null,
    providers,
    providerUsable,
    providerReady,
    canonicalOriginReady,
    configurationReady,
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
    // Provider/JWKS configuration is necessary but does not prove that two
    // admitted users, verified factors, fresh AAL2 sessions, and maker-checker
    // separation exist. The authenticated security profile and destructive
    // live rehearsal own that evidence, so this public diagnostic stays
    // fail-closed instead of claiming operational readiness from settings.
    operational: false,
    limitations: [
      ...(!configured ? ["supabase_web_configuration_missing"] : []),
      ...(configured && !settings ? ["supabase_auth_settings_unreachable"] : []),
      ...(!providers.google ? ["google_provider_not_enabled"] : []),
      ...(!providers.kakao ? ["kakao_provider_not_enabled"] : []),
      ...(providers.kakao && !providerUsable.kakao
        ? ["kakao_account_email_scope_not_verified"]
        : []),
      ...(!canonicalOriginReady ? ["canonical_https_web_origin_not_configured"] : []),
      ...(!backendReady ? ["fastapi_oidc_membership_boundary_not_ready"] : []),
      ...(!configurationReady ? [] : ["real_user_aal2_maker_checker_smoke_required"]),
      ...(!configurationReady ? ["configuration_gate_incomplete"] : []),
    ],
  };
}
