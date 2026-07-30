import { NextRequest, NextResponse } from "next/server";
import {
  canonicalAuthRequestUrl,
  canonicalWebOrigin,
  clearPasswordRecoveryIntentCookie,
  normalizedAuthEmail,
  preventAuthenticatedResponseCaching,
  REALYTICS_PASSWORD_RECOVERY_COOKIE,
  requestIsSameOrigin,
  SUPABASE_PASSWORD_POLICY,
  validNewSupabasePassword,
} from "../../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../../lib/supabase/server";
import {
  classifyAuthEmailFailure,
  publicAuthEmailFailureBody,
} from "../../../../../../lib/supabase/auth-email-errors";
import { passwordRecoveryCallbackUrl } from "../recovery-contract";

type RecoveryPayload = {
  action?: unknown;
  email?: unknown;
  password?: unknown;
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
    return authJson({ error: "canonical_origin_required" }, 409);
  }
  const payload = await request.json().catch(() => null) as RecoveryPayload | null;
  if (payload?.action === "request") {
    return requestRecoveryEmail(payload, origin);
  }
  if (payload?.action === "complete") {
    return completePasswordRecovery(request, payload);
  }
  return authJson({ error: "invalid_password_recovery_input" }, 400);
}

async function requestRecoveryEmail(
  payload: RecoveryPayload,
  origin: string,
) {
  const email = normalizedAuthEmail(payload.email);
  if (!email) {
    return authJson({ error: "invalid_password_recovery_input" }, 400);
  }
  try {
    const supabase = await createSupabaseServerClient();
    const callback = passwordRecoveryCallbackUrl(origin);
    // Always return the same accepted envelope. Supabase intentionally hides
    // whether an account exists, and this BFF must not reintroduce enumeration.
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: callback.toString(),
    });
    if (error) {
      const failure = classifyAuthEmailFailure(error, "password_recovery");
      console.warn("Supabase password recovery request was not accepted", {
        category: failure.category,
        providerCode: failure.providerCode,
        providerStatus: failure.providerStatus,
      });
      const publicFailure = publicAuthEmailFailureBody(failure);
      if (publicFailure && failure.publicStatus) {
        return authJson(publicFailure, failure.publicStatus);
      }
    }
    return acceptedRecoveryResponse();
  } catch {
    return authJson({ error: "password_recovery_unavailable" }, 503);
  }
}

function acceptedRecoveryResponse() {
  // A success response means only that the request was accepted for privacy-
  // preserving processing. It never confirms whether an account exists or an
  // email was delivered.
  return authJson({
    accepted: true,
    accountState: "undisclosed",
    deliveryState: "unconfirmed",
  }, 202);
}

async function completePasswordRecovery(
  request: NextRequest,
  payload: RecoveryPayload,
) {
  if (
    request.cookies.get(REALYTICS_PASSWORD_RECOVERY_COOKIE)?.value
    !== "pending"
  ) {
    return authJson({ error: "password_recovery_context_required" }, 401);
  }
  if (!validNewSupabasePassword(payload.password)) {
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
    const { data, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !data?.claims) {
      return authJson({ error: "password_recovery_context_required" }, 401);
    }
    const { error } = await supabase.auth.updateUser({
      password: payload.password,
    });
    if (error) {
      return authJson({ error: "password_recovery_failed" }, 400);
    }
    const { error: signOutError } = await supabase.auth.signOut({
      scope: "local",
    });
    if (signOutError) {
      return authJson(
        { error: "password_recovery_session_cleanup_failed" },
        503,
      );
    }
    const response = authJson({ updated: true });
    clearPasswordRecoveryIntentCookie(response);
    return response;
  } catch {
    return authJson({ error: "password_recovery_unavailable" }, 503);
  }
}

function authJson(body: Record<string, unknown>, status = 200) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}
