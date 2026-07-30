import type { SupabaseClient } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";
import {
  acceptRealyticsInvitation,
  accessStateForIdentityResult,
  canonicalAuthRequestUrl,
  canonicalWebOrigin,
  clearInvitationIntentCookie,
  clearTenantSelectorCookie,
  normalizedAuthDisplayName,
  normalizedAuthEmail,
  preventAuthenticatedResponseCaching,
  REALYTICS_INVITATION_COOKIE,
  realyticsAcceptsBearer,
  requestIsSameOrigin,
  setTenantSelectorCookie,
  SUPABASE_PASSWORD_POLICY,
  type InvitationAcceptanceResult,
  type RealyticsAccessState,
  validNewSupabasePassword,
  validSupabaseLoginPassword,
} from "../../../../../lib/backend-session";
import {
  invitationIntentIsDeferred,
  shouldClearInvitationIntent,
} from "../../../../../lib/invitation-intent";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";

type PasswordPayload = {
  mode?: unknown;
  email?: unknown;
  password?: unknown;
  displayName?: unknown;
};

type AuthResolution = {
  accessState: RealyticsAccessState;
  invitation: InvitationAcceptanceResult | null;
};

export async function POST(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return authJson({ error: "cross_site_request_rejected" }, 403);
  }

  const origin = canonicalWebOrigin(request);
  if (!origin) {
    return authJson({ error: "canonical_web_origin_not_configured" }, 503);
  }
  if (canonicalAuthRequestUrl(request, origin)) {
    // A PKCE verifier written on an alternate host will not be available when
    // the confirmation link returns to the canonical callback host.
    return authJson({ error: "canonical_origin_required" }, 409);
  }

  const payload = (await request
    .json()
    .catch(() => null)) as PasswordPayload | null;
  if (payload?.mode !== "signup" && payload?.mode !== "login") {
    return authJson({ error: "invalid_authentication_input" }, 400);
  }
  const mode = payload.mode;
  const email = normalizedAuthEmail(payload.email);
  const password = typeof payload.password === "string" ? payload.password : "";
  const displayName = normalizedAuthDisplayName(payload.displayName);
  if (!email || !validSupabaseLoginPassword(password)) {
    return authJson({ error: "invalid_authentication_input" }, 400);
  }
  if (mode === "signup" && !validNewSupabasePassword(password)) {
    return authJson(
      {
        error: "weak_password_input",
        passwordPolicy: SUPABASE_PASSWORD_POLICY,
      },
      400,
    );
  }

  try {
    const supabase = await createSupabaseServerClient();
    if (mode === "signup") {
      const callback = new URL("/auth/callback", origin);
      callback.searchParams.set("next", "/onboarding");
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: callback.toString(),
          data: displayName ? { display_name: displayName } : undefined,
        },
      });
      if (error) {
        return authJson({ error: "authentication_failed" }, 400);
      }
      if (!data.session) {
        return authJson({
          authenticated: false,
          requiresEmailConfirmation: true,
          accessState: "onboarding_required",
          next: "/onboarding",
        });
      }
      return authenticatedResponse(
        request,
        supabase,
        data.session.access_token,
      );
    }

    const { data, error } = await supabase.auth.signInWithPassword({
      email,
      password,
    });
    if (error || !data.session?.access_token) {
      return authJson({ error: "authentication_failed" }, 400);
    }
    return authenticatedResponse(
      request,
      supabase,
      data.session.access_token,
    );
  } catch {
    return authJson({ error: "authentication_unavailable" }, 503);
  }
}

async function authenticatedResponse(
  request: NextRequest,
  supabase: SupabaseClient,
  accessToken: string,
) {
  const { data, error } = await supabase.auth.getClaims();
  if (error || !data?.claims) {
    await supabase.auth.signOut({ scope: "local" });
    return authJson({ error: "authentication_failed" }, 401);
  }
  const invitationToken = request.cookies.get(
    REALYTICS_INVITATION_COOKIE,
  )?.value;
  const resolution = await resolveRealyticsAccess(
    accessToken,
    invitationToken,
  );
  const next = resolution.accessState === "member"
    ? "/start"
    : "/onboarding";
  const response = authJson({
    authenticated: true,
    requiresEmailConfirmation: false,
    accessState: resolution.accessState,
    next,
    ...(resolution.invitation
      ? { invitation: resolution.invitation.state }
      : {}),
  });
  clearTenantSelectorCookie(response);
  if (
    resolution.invitation?.state === "accepted"
    && resolution.invitation.tenantId
  ) {
    setTenantSelectorCookie(response, resolution.invitation.tenantId);
  }
  if (
    resolution.invitation
    && shouldClearInvitationIntent(resolution.invitation.state)
  ) {
    clearInvitationIntentCookie(response);
  }
  return response;
}

async function resolveRealyticsAccess(
  accessToken: string,
  invitationToken: string | undefined,
): Promise<AuthResolution> {
  let invitation: InvitationAcceptanceResult | null = null;
  if (invitationToken) {
    invitation = await acceptRealyticsInvitation(accessToken, invitationToken);
    if (invitation.state === "authority_unavailable") {
      return { accessState: "authority_unavailable", invitation };
    }
    if (invitationIntentIsDeferred(invitation.state)) {
      return { accessState: "onboarding_required", invitation };
    }
  }
  const identityResult = await realyticsAcceptsBearer(
    accessToken,
    invitation?.state === "accepted" ? invitation.tenantId : null,
  );
  return {
    accessState: accessStateForIdentityResult(identityResult),
    invitation,
  };
}

function authJson(body: Record<string, unknown>, status = 200) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}
