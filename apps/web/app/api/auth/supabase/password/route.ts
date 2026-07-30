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
import {
  REALYTICS_PRIVACY_VERSION,
  REALYTICS_TERMS_VERSION,
} from "../../../../../lib/auth-onboarding-policy";
import {
  classifyAuthEmailFailure,
  publicAuthEmailFailureBody,
} from "../../../../../lib/supabase/auth-email-errors";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";

type PasswordPayload = {
  mode?: unknown;
  email?: unknown;
  password?: unknown;
  displayName?: unknown;
  termsAccepted?: unknown;
  privacyAccepted?: unknown;
  termsVersion?: unknown;
  privacyVersion?: unknown;
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
  if (
    mode === "signup"
    && (payload.termsAccepted !== true || payload.privacyAccepted !== true)
  ) {
    return authJson({ error: "signup_policy_consent_required" }, 400);
  }
  if (
    mode === "signup"
    && (
      payload.termsVersion !== REALYTICS_TERMS_VERSION
      || payload.privacyVersion !== REALYTICS_PRIVACY_VERSION
    )
  ) {
    return authJson(
      {
        error: "signup_policy_version_mismatch",
        policyVersions: {
          termsVersion: REALYTICS_TERMS_VERSION,
          privacyVersion: REALYTICS_PRIVACY_VERSION,
        },
      },
      409,
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
        const failure = classifyAuthEmailFailure(error, "signup");
        logAuthEmailFailure("signup", failure);
        const publicFailure = publicAuthEmailFailureBody(failure);
        if (publicFailure && failure.publicStatus) {
          return authJson(publicFailure, failure.publicStatus);
        }
        // Supabase intentionally obfuscates existing-account signup results.
        // Keep the same public envelope when a provider code could reveal that
        // an address already has an identity.
        return pendingConfirmationResponse();
      }
      if (!data.session) {
        return pendingConfirmationResponse();
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

function pendingConfirmationResponse() {
  return authJson({
    authenticated: false,
    requiresEmailConfirmation: true,
    accessState: "onboarding_required",
    next: "/onboarding",
  }, 202);
}

function logAuthEmailFailure(
  operation: string,
  failure: ReturnType<typeof classifyAuthEmailFailure>,
) {
  console.warn("Supabase Auth email operation was not accepted", {
    operation,
    category: failure.category,
    providerCode: failure.providerCode,
    providerStatus: failure.providerStatus,
  });
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
