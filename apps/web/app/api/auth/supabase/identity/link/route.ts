import type { Provider } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";
import {
  canonicalAuthRequestUrl,
  canonicalWebOrigin,
  preventAuthenticatedResponseCaching,
  safeInternalNextPath,
} from "../../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../../lib/supabase/server";

const PROVIDERS = new Set<Provider>(["google", "kakao"]);

export async function GET(request: NextRequest) {
  const provider = request.nextUrl.searchParams.get("provider") as Provider | null;
  if (!provider || !PROVIDERS.has(provider)) {
    return NextResponse.json({ error: "unsupported_identity_provider" }, { status: 400 });
  }
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
  const nextPath = safeInternalNextPath(
    request.nextUrl.searchParams.get("next"),
    "/settings",
  );
  const callback = new URL("/auth/callback", origin);
  callback.searchParams.set("next", nextPath);

  try {
    const supabase = await createSupabaseServerClient();
    const { data: claims, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !claims?.claims) {
      return NextResponse.redirect(`${origin}/login?error=login_required`);
    }
    const { data, error } = await supabase.auth.linkIdentity({
      provider,
      options: {
        redirectTo: callback.toString(),
        skipBrowserRedirect: true,
      },
    });
    if (error || !data.url) {
      return NextResponse.redirect(`${origin}/settings?security_error=identity_link_failed`);
    }
    const response = NextResponse.redirect(data.url);
    preventAuthenticatedResponseCaching(response);
    return response;
  } catch {
    return NextResponse.redirect(`${origin}/settings?security_error=identity_link_unavailable`);
  }
}
