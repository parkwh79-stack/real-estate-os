import { NextRequest, NextResponse } from "next/server";
import {
  preventAuthenticatedResponseCaching,
  realyticsAcceptsBearer,
} from "../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../lib/supabase/server";

function safeNextPath(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/start";
  return value === "/" || value === "/dashboard" ? "/start" : value;
}

function redirectOrigin(request: NextRequest): string | null {
  const configuredOrigin = process.env.REALYTICS_WEB_ORIGIN;
  if (configuredOrigin) {
    try {
      const parsed = new URL(configuredOrigin);
      if (process.env.NODE_ENV === "production" && parsed.protocol !== "https:") {
        return null;
      }
      return parsed.origin;
    } catch {
      return null;
    }
  }
  if (process.env.NODE_ENV === "production") return null;
  return request.nextUrl.origin;
}

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const nextPath = safeNextPath(request.nextUrl.searchParams.get("next"));
  const origin = redirectOrigin(request);
  if (!origin) {
    return NextResponse.json(
      { error: "canonical_web_origin_not_configured" },
      { status: 503 },
    );
  }
  if (!code) {
    return NextResponse.redirect(`${origin}/login?error=missing_oauth_code`);
  }

  try {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) {
      return NextResponse.redirect(`${origin}/login?error=oauth_code_exchange_failed`);
    }
    const { data, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !data?.claims) {
      await supabase.auth.signOut({ scope: "local" });
      return NextResponse.redirect(`${origin}/login?error=oauth_claims_invalid`);
    }
    const { data: sessionData } = await supabase.auth.getSession();
    const accessToken = sessionData.session?.access_token;
    if (!accessToken || !(await realyticsAcceptsBearer(accessToken))) {
      await supabase.auth.signOut({ scope: "local" });
      return NextResponse.redirect(`${origin}/login?error=identity_not_provisioned`);
    }
    const response = NextResponse.redirect(`${origin}${nextPath}`);
    preventAuthenticatedResponseCaching(response);
    return response;
  } catch {
    return NextResponse.redirect(`${origin}/login?error=oauth_not_configured`);
  }
}
