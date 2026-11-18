import { NextRequest, NextResponse } from "next/server";
import {
  acceptRealyticsInvitation,
  accessStateForIdentityResult,
  canonicalWebOrigin,
  clearInvitationIntentCookie,
  clearTenantSelectorCookie,
  preventAuthenticatedResponseCaching,
  REALYTICS_INVITATION_COOKIE,
  realyticsAcceptsBearer,
  safeInternalNextPath,
  setPasswordRecoveryIntentCookie,
  setTenantSelectorCookie,
  type InvitationAcceptanceResult,
} from "../../../lib/backend-session";
import {
  invitationIntentIsDeferred,
  shouldClearInvitationIntent,
} from "../../../lib/invitation-intent";
import { createSupabaseServerClient } from "../../../lib/supabase/server";
import { passwordRecoveryCallbackPlan } from "../../api/auth/supabase/password/recovery-contract";

export const maxDuration = 150;

export async function GET(request: NextRequest) {
  const code = request.nextUrl.searchParams.get("code");
  const providerError = request.nextUrl.searchParams.get("error");
  const nextPath = safeInternalNextPath(
    request.nextUrl.searchParams.get("next"),
  );
  const switchAccount = request.nextUrl.searchParams.get("switch") === "1";
  const passwordRecovery =
    request.nextUrl.searchParams.get("recovery") === "1";
  const origin = canonicalWebOrigin(request);
  const invitationToken = request.cookies.get(
    REALYTICS_INVITATION_COOKIE,
  )?.value;
  if (!origin) {
    return authJson({ error: "canonical_web_origin_not_configured" }, 503);
  }
  if (providerError) {
    const errorCode =
      providerError === "invalid_scope"
        ? "oauth_provider_scope_invalid"
        : providerError === "access_denied"
          ? "oauth_access_denied"
          : "oauth_provider_failed";
    return authRedirect(
      loginRetryUrl(origin, errorCode, nextPath, switchAccount),
    );
  }
  if (!code) {
    return authRedirect(
      loginRetryUrl(origin, "missing_oauth_code", nextPath, switchAccount),
    );
  }

  try {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (error) {
      return authRedirect(
        loginRetryUrl(origin, "oauth_code_exchange_failed", nextPath, switchAccount),
      );
    }
    const { data, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !data?.claims) {
      await supabase.auth.signOut({ scope: "local" });
      return authRedirect(
        loginRetryUrl(origin, "oauth_claims_invalid", nextPath, switchAccount),
      );
    }
    const { data: sessionData } = await supabase.auth.getSession();
    const accessToken = sessionData.session?.access_token;
    let invitation: InvitationAcceptanceResult | null = null;
    if (accessToken && invitationToken) {
      invitation = await acceptRealyticsInvitation(
        accessToken,
        invitationToken,
      );
    }
    if (!accessToken) {
      await supabase.auth.signOut({ scope: "local" });
      const response = authRedirect(
        loginRetryUrl(
          origin,
          "oauth_session_missing",
          nextPath,
          switchAccount,
        ),
      );
      clearInvitationIntentCookie(response);
      return response;
    }

    const invitationState = invitation?.state ?? null;
    const identityResult = await realyticsAcceptsBearer(
      accessToken,
      invitation?.state === "accepted" ? invitation.tenantId : null,
    );

    const accessState = invitationState === "authority_unavailable"
      ? "authority_unavailable"
      : invitationState && invitationIntentIsDeferred(invitationState)
        ? "onboarding_required"
        : accessStateForIdentityResult(identityResult);
    const recoveryPlan = passwordRecovery
      ? passwordRecoveryCallbackPlan(origin, nextPath, accessState)
      : null;
    const destination = recoveryPlan
      ? recoveryPlan.destination
      : accessState === "member"
        ? new URL(nextPath, origin)
        : new URL("/onboarding", origin);
    if (!passwordRecovery && accessState !== "member") {
      destination.searchParams.set("accessState", accessState);
      destination.searchParams.set("next", nextPath);
    }
    if (invitation) {
      destination.searchParams.set("invitation", invitation.state);
    }
    const response = authRedirect(destination);
    // A selector belongs to the identity that previously proved membership.
    // Never carry it across a fresh OAuth/session exchange, even when the new
    // account happens to use the same browser profile.
    clearTenantSelectorCookie(response);
    if (invitationState === "accepted" && invitation?.tenantId) {
      setTenantSelectorCookie(response, invitation.tenantId);
    }
    if (invitationState && shouldClearInvitationIntent(invitationState)) {
      clearInvitationIntentCookie(response);
    }
    if (recoveryPlan?.setRecoveryContextCookie) {
      setPasswordRecoveryIntentCookie(response);
    }
    return response;
  } catch {
    return authRedirect(
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

function authRedirect(destination: URL) {
  const response = NextResponse.redirect(destination);
  preventAuthenticatedResponseCaching(response);
  return response;
}

function authJson(body: Record<string, unknown>, status: number) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}
