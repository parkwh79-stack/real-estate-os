import { NextRequest, NextResponse } from "next/server";
import {
  acceptRealyticsInvitation,
  canonicalAuthRequestUrl,
  canonicalWebOrigin,
  clearInvitationIntentCookie,
  clearTenantSelectorCookie,
  preventAuthenticatedResponseCaching,
  REALYTICS_INVITATION_COOKIE,
  requestIsSameOrigin,
  setTenantSelectorCookie,
} from "../../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../../lib/supabase/server";

/**
 * Completes a deferred invitation using only server-held authority:
 * Supabase cookies identify the user and the HttpOnly invitation cookie holds
 * the one-time token. The browser never receives or echoes the token.
 */
export async function POST(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return authJson({ error: "cross_site_request_rejected" }, 403);
  }

  const origin = canonicalWebOrigin(request);
  if (!origin) {
    return authJson({ error: "canonical_web_origin_not_configured" }, 503);
  }
  if (canonicalAuthRequestUrl(request, origin)) {
    return authJson({ error: "canonical_origin_required" }, 409);
  }

  const invitationToken = request.cookies.get(
    REALYTICS_INVITATION_COOKIE,
  )?.value;
  if (!invitationToken) {
    return authJson({
      error: "invitation_intent_missing",
      invitation: "not_pending",
      next: "/onboarding",
    }, 409);
  }

  try {
    const supabase = await createSupabaseServerClient();
    const { data: claimsData, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !claimsData?.claims) {
      return authJson({ error: "authentication_required" }, 401);
    }

    // getSession only retrieves the bearer after getClaims verified the
    // identity-bearing cookie. FastAPI performs the authoritative JWT and
    // issuer/subject checks again before touching membership rows.
    const { data: sessionData } = await supabase.auth.getSession();
    const accessToken = sessionData.session?.access_token;
    if (!accessToken) {
      return authJson({ error: "authentication_required" }, 401);
    }

    const result = await acceptRealyticsInvitation(
      accessToken,
      invitationToken,
    );
    if (result.state === "accepted" && result.tenantId) {
      const response = authJson({
        schemaVersion: "realytics-deferred-invitation-accept-v1",
        invitation: result.state,
        tenantId: result.tenantId,
        next: "/start",
      });
      clearTenantSelectorCookie(response);
      setTenantSelectorCookie(response, result.tenantId);
      clearInvitationIntentCookie(response);
      return response;
    }

    if (
      result.state === "profile_required"
      || result.state === "policy_update_required"
    ) {
      // The invitation remains pending in its HttpOnly cookie. Once the
      // profile/current policy snapshot is stored, this same endpoint can be
      // retried without exposing the token to React or browser storage.
      return authJson({
        error: result.errorCode,
        invitation: result.state,
        next: "/onboarding",
        ...(result.currentPolicyVersions
          ? { currentPolicyVersions: result.currentPolicyVersions }
          : {}),
      }, 409);
    }

    if (result.state === "authority_unavailable") {
      return authJson({
        error: result.errorCode ?? "identity_invitation_authority_unavailable",
        invitation: result.state,
        next: "/onboarding",
      }, 503);
    }

    const response = authJson({
      error: result.errorCode ?? "invitation_accept_failed",
      invitation: "rejected",
      next: "/onboarding",
    }, terminalFailureStatus(result.httpStatus));
    clearInvitationIntentCookie(response);
    return response;
  } catch {
    // An unknown failure is retryable and must not destroy the only server-side
    // copy of the pending invitation intent.
    return authJson({
      error: "identity_invitation_authority_unavailable",
      invitation: "authority_unavailable",
      next: "/onboarding",
    }, 503);
  }
}

function terminalFailureStatus(status: number): number {
  return status >= 400 && status < 500 ? status : 409;
}

function authJson(body: Record<string, unknown>, status = 200) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}
