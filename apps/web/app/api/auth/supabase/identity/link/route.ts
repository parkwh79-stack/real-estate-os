import type { Provider } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";
import {
  canonicalWebOrigin,
  preventAuthenticatedResponseCaching,
  requestIsSameOrigin,
  safeInternalNextPath,
} from "../../../../../../lib/backend-session";
import {
  safeIdentityLinkRedirectUrl,
  type LinkableIdentityProvider,
} from "../../../../../../lib/supabase/identity-security";
import { evaluateRecentAal2 } from "../../../../../../lib/supabase/mfa-security";
import { createSupabaseServerClient } from "../../../../../../lib/supabase/server";
import { verifyCurrentSupabaseSession } from "../../../../../../lib/supabase/verified-session";

const PROVIDERS = new Set<Provider>(["google", "kakao"]);

type LinkRequest = {
  provider?: unknown;
  next?: unknown;
};

function json(body: unknown, status = 200) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}

export async function POST(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return json({ error: "cross_site_request_rejected" }, 403);
  }

  const payload = await request.json().catch(() => null) as LinkRequest | null;
  const provider = typeof payload?.provider === "string"
    ? payload.provider.trim().toLowerCase() as Provider
    : null;
  if (!provider || !PROVIDERS.has(provider)) {
    return json({ error: "unsupported_identity_provider" }, 400);
  }
  const origin = canonicalWebOrigin(request);
  if (!origin) {
    return json({ error: "canonical_web_origin_not_configured" }, 503);
  }
  if (new URL(origin).host.toLowerCase() !== request.headers.get("host")?.toLowerCase()) {
    // Do not replay a state-changing POST across hosts. The login page already
    // points users at the configured canonical web origin.
    return json({ error: "canonical_auth_origin_required" }, 409);
  }
  const nextPath = safeInternalNextPath(
    typeof payload?.next === "string" ? payload.next : null,
    "/settings",
  );
  const callback = new URL("/auth/callback", origin);
  callback.searchParams.set("next", nextPath);

  try {
    const supabase = await createSupabaseServerClient();
    const verifiedSession = await verifyCurrentSupabaseSession(supabase);
    if (!verifiedSession.verified) {
      return json({ error: verifiedSession.error }, verifiedSession.status);
    }
    const assuranceResult = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
    if (assuranceResult.error) {
      return json({ error: "assurance_level_unavailable" }, 503);
    }
    const recentAal2 = evaluateRecentAal2(
      assuranceResult.data.currentLevel,
      assuranceResult.data.currentAuthenticationMethods,
    );
    if (!recentAal2.eligible) {
      return json({ error: recentAal2.limitation }, 403);
    }
    const { data, error } = await supabase.auth.linkIdentity({
      provider,
      options: {
        redirectTo: callback.toString(),
        skipBrowserRedirect: true,
      },
    });
    if (error || !data.url) {
      return json({ error: "identity_link_failed" }, 400);
    }
    const redirectUrl = safeIdentityLinkRedirectUrl(
      provider as LinkableIdentityProvider,
      data.url,
    );
    if (!redirectUrl) {
      return json({ error: "identity_link_redirect_rejected" }, 502);
    }
    return json({
      schemaVersion: "realytics-identity-link-start-v1",
      provider,
      redirectUrl,
    });
  } catch {
    return json({ error: "identity_link_unavailable" }, 503);
  }
}
