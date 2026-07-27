import { NextRequest, NextResponse } from "next/server";
import {
  acceptRealyticsInvitation,
  canonicalWebOrigin,
  clearInvitationIntentCookie,
  preventAuthenticatedResponseCaching,
  REALYTICS_INVITATION_COOKIE,
  realyticsAcceptsBearer,
  safeInternalNextPath,
} from "../../../lib/backend-session";
import { loginErrorForIdentityResult } from "../../../lib/backend-identity-result";
import { createSupabaseServerClient } from "../../../lib/supabase/server";

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const providerError = request.nextUrl.searchParams.get("error");
  const nextPath = safeInternalNextPath(
    request.nextUrl.searchParams.get("next"),
  );
  const origin = canonicalWebOrigin(request);
  const invitationToken = request.cookies.get(
    REALYTICS_INVITATION_COOKIE,
  )?.value;
  if (!origin) {
    return NextResponse.json(
      { error: "canonical_web_origin_not_configured" },
      { status: 503 },
    );
  }
  if (providerError) {
    const errorCode =
      providerError === "invalid_scope"
        ? "oauth_provider_scope_invalid"
        : providerError === "access_denied"
          ? "oauth_access_denied"
          : "oauth_provider_failed";
    return NextResponse.redirect(`${origin}/login?error=${errorCode}`);
  }
  if (!code) {
    return NextResponse.redirect(`${origin}/login?error=missing_oauth_code`);
  }

  try {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) {
      return NextResponse.redirect(
        `${origin}/login?error=oauth_code_exchange_failed`,
      );
    }
    const { data, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !data?.claims) {
      await supabase.auth.signOut({ scope: "local" });
      return NextResponse.redirect(
        `${origin}/login?error=oauth_claims_invalid`,
      );
    }
    const { data: sessionData } = await supabase.auth.getSession();
    const accessToken = sessionData.session?.access_token;
    let identityResult = accessToken
      ? await realyticsAcceptsBearer(accessToken)
      : ({ status: "backend_unavailable" } as const);
    let invitationAccepted = false;
    if (
      identityResult.status === "not_provisioned" &&
      accessToken &&
      invitationToken
    ) {
      invitationAccepted = await acceptRealyticsInvitation(
        accessToken,
        invitationToken,
      );
      if (invitationAccepted) {
        identityResult = await realyticsAcceptsBearer(accessToken);
      }
    }
    if (!accessToken || identityResult.status !== "accepted") {
      await supabase.auth.signOut({ scope: "local" });
      const errorCode = accessToken
        ? loginErrorForIdentityResult(identityResult, {
            invitationPending: Boolean(invitationToken),
          })
        : "oauth_session_missing";
      const response = NextResponse.redirect(
        `${origin}/login?error=${errorCode ?? "backend_unavailable"}`,
      );
      clearInvitationIntentCookie(response);
      preventAuthenticatedResponseCaching(response);
      return response;
    }
    const destination = new URL(`${origin}${nextPath}`);
    if (invitationAccepted) {
      destination.searchParams.set("invitation", "accepted");
    }
    const response = NextResponse.redirect(destination);
    clearInvitationIntentCookie(response);
    preventAuthenticatedResponseCaching(response);
    return response;
  } catch {
    return NextResponse.redirect(`${origin}/login?error=oauth_not_configured`);
  }
}
