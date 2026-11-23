import { resolveBackendOrigin } from "./backend-origin-contract";

export interface CliPublicConfig {
  schemaVersion: "realytics-cli-config-v1";
  clientId: "realytics-cli";
  apiOrigin: string;
  supabaseUrl: string;
  publishableKey: string;
  issuer: string;
  authMethods: ["password", "refresh_token"];
}

/** Only non-secret client bootstrap data; never return arbitrary environment keys. */
export function cliPublicConfig(env: {
  REALYTICS_API_ORIGIN?: string;
  NEXT_PUBLIC_SUPABASE_URL?: string;
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?: string;
}): CliPublicConfig | null {
  const apiOrigin = resolveBackendOrigin(env.REALYTICS_API_ORIGIN, "production");
  const supabaseUrl = resolveBackendOrigin(env.NEXT_PUBLIC_SUPABASE_URL, "production");
  const publishableKey = env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim();
  // A misnamed server secret or legacy service-role JWT must fail closed.
  if (!apiOrigin || !supabaseUrl || !publishableKey
    || !/^sb_publishable_[A-Za-z0-9_-]{10,512}$/.test(publishableKey)) return null;
  return {
    schemaVersion: "realytics-cli-config-v1",
    clientId: "realytics-cli",
    apiOrigin,
    supabaseUrl,
    publishableKey,
    issuer: `${supabaseUrl}/auth/v1`,
    authMethods: ["password", "refresh_token"],
  };
}
