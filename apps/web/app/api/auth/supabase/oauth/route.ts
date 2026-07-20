import type { Provider } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";
import { preventAuthenticatedResponseCaching } from "../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";

const PROVIDERS = new Set<Provider>(["google", "kakao"]);

function safeNextPath(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/start";
  return value === "/" || value === "/dashboard" ? "/start" : value;
}

function publicOrigin(request: NextRequest): string | null {
  const configured = process.env.REALYTICS_WEB_ORIGIN;
  if (configured) {
    try {
      const parsed = new URL(configured);
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
  const provider = request.nextUrl.searchParams.get("provider") as Provider | null;
  if (!provider || !PROVIDERS.has(provider)) {
    return NextResponse.json({ error: "unsupported_oauth_provider" }, { status: 400 });
  }
  const nextPath = safeNextPath(request.nextUrl.searchParams.get("next"));
  const origin = publicOrigin(request);
  if (!origin) {
    return NextResponse.json(
      { error: "canonical_web_origin_not_configured" },
      { status: 503 },
    );
  }
  const callback = new URL("/auth/callback", origin);
  callback.searchParams.set("next", nextPath);

  try {
    const supabase = await createSupabaseServerClient();
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: callback.toString(),
      },
    });
    if (error || !data.url) {
      return NextResponse.redirect(`${origin}/login?error=oauth_start_failed`);
    }
    const response = NextResponse.redirect(data.url);
    preventAuthenticatedResponseCaching(response);
    return response;
  } catch {
    return NextResponse.redirect(`${origin}/login?error=oauth_not_configured`);
  }
}
