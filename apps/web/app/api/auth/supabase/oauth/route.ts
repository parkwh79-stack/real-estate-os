import type { Provider } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";
import {
  canonicalAuthRequestUrl,
  canonicalWebOrigin,
  preventAuthenticatedResponseCaching,
  safeInternalNextPath,
} from "../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";

const PROVIDERS = new Set<Provider>(["google", "kakao"]);
const KAKAO_PROVIDER_SCOPE_OVERRIDE = "profile_nickname profile_image";

export async function GET(request: NextRequest) {
  const provider = request.nextUrl.searchParams.get("provider") as Provider | null;
  if (!provider || !PROVIDERS.has(provider)) {
    return NextResponse.json({ error: "unsupported_oauth_provider" }, { status: 400 });
  }
  const nextPath = safeInternalNextPath(request.nextUrl.searchParams.get("next"));
  const switchAccount = request.nextUrl.searchParams.get("switch") === "1";
  const origin = canonicalWebOrigin(request);
  if (!origin) {
    return NextResponse.json(
      { error: "canonical_web_origin_not_configured" },
      { status: 503 },
    );
  }
  const canonicalRequest = canonicalAuthRequestUrl(request, origin);
  if (canonicalRequest) {
    const response = NextResponse.redirect(canonicalRequest);
    preventAuthenticatedResponseCaching(response);
    return response;
  }
  const callback = new URL("/auth/callback", origin);
  callback.searchParams.set("next", nextPath);
  if (switchAccount) callback.searchParams.set("switch", "1");

  try {
    const supabase = await createSupabaseServerClient();
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: callback.toString(),
        // GoTrue appends `options.scopes` to Kakao's provider defaults, which
        // would retain account_email. The later provider query parameter
        // replaces the OAuth scope instead, so keep this server-controlled.
        // Email is not an identity, membership, or authorization key.
        ...(provider === "google" && switchAccount
          ? { queryParams: { prompt: "select_account" } }
          : provider === "kakao"
            ? {
              queryParams: {
                scope: KAKAO_PROVIDER_SCOPE_OVERRIDE,
                ...(switchAccount ? { prompt: "login" } : {}),
              },
            }
            : {}),
      },
    });
    if (error || !data.url) {
      return NextResponse.redirect(
        loginRetryUrl(origin, "oauth_start_failed", nextPath, switchAccount),
      );
    }
    const response = NextResponse.redirect(data.url);
    preventAuthenticatedResponseCaching(response);
    return response;
  } catch {
    return NextResponse.redirect(
      loginRetryUrl(origin, "oauth_not_configured", nextPath, switchAccount),
    );
  }
}

function loginRetryUrl(
  origin: string,
  error: string,
  nextPath: string,
  switchAccount: boolean,
) {
  const destination = new URL("/login", origin);
  destination.searchParams.set("error", error);
  destination.searchParams.set("next", nextPath);
  if (switchAccount) destination.searchParams.set("switch", "1");
  return destination;
}
