import { NextRequest, NextResponse } from "next/server";
import {
  canonicalAuthRequestUrl,
  canonicalWebOrigin,
  normalizedAuthEmail,
  preventAuthenticatedResponseCaching,
  requestIsSameOrigin,
} from "../../../../../../lib/backend-session";
import {
  classifyAuthEmailFailure,
  publicAuthEmailFailureBody,
} from "../../../../../../lib/supabase/auth-email-errors";
import { createSupabaseServerClient } from "../../../../../../lib/supabase/server";

type ConfirmationPayload = {
  email?: unknown;
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

  const payload = await request.json().catch(() => null) as ConfirmationPayload | null;
  const email = normalizedAuthEmail(payload?.email);
  if (!email) {
    return authJson({ error: "invalid_authentication_input" }, 400);
  }

  try {
    const supabase = await createSupabaseServerClient();
    const callback = new URL("/auth/callback", origin);
    callback.searchParams.set("next", "/onboarding");
    const { error } = await supabase.auth.resend({
      type: "signup",
      email,
      options: { emailRedirectTo: callback.toString() },
    });
    if (error) {
      const failure = classifyAuthEmailFailure(
        error,
        "signup_confirmation_resend",
      );
      console.warn("Supabase signup confirmation resend was not accepted", {
        category: failure.category,
        providerCode: failure.providerCode,
        providerStatus: failure.providerStatus,
      });
      const publicFailure = publicAuthEmailFailureBody(failure);
      if (publicFailure && failure.publicStatus) {
        return authJson(publicFailure, failure.publicStatus);
      }
    }
    return acceptedConfirmationResponse();
  } catch {
    return authJson({ error: "auth_email_delivery_unavailable" }, 503);
  }
}

function acceptedConfirmationResponse() {
  // Supabase resend may intentionally hide user-not-found. This stable envelope
  // must remain identical for registered and unregistered addresses.
  return authJson({
    accepted: true,
    accountState: "undisclosed",
    deliveryState: "unconfirmed",
  }, 202);
}

function authJson(body: Record<string, unknown>, status = 200) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}
