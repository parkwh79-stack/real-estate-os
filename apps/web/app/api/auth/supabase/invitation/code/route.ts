import { NextRequest, NextResponse } from "next/server";
import {
  acceptRealyticsInvitation,
  canonicalAuthRequestUrl,
  canonicalWebOrigin,
  clearInvitationIntentCookie,
  clearTenantSelectorCookie,
  preventAuthenticatedResponseCaching,
  requestIsSameOrigin,
  setTenantSelectorCookie,
} from "../../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../../lib/supabase/server";
import { verifyCurrentSupabaseSession } from "../../../../../../lib/supabase/verified-session";

const INVITATION_CODE_PATTERN = /^[A-Za-z0-9_-]{32,512}$/;

/**
 * Accepts a user-typed one-time invitation without persisting it in a URL,
 * cookie, browser storage, response, or log. The code exists only in this
 * request and the server-to-server FastAPI acceptance request.
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

  const invitationCode = await readInvitationCode(request);
  if (!invitationCode) {
    return authJson({ error: "invitation_code_invalid" }, 400);
  }

  try {
    const supabase = await createSupabaseServerClient();
    const verifiedSession = await verifyCurrentSupabaseSession(supabase);
    if (!verifiedSession.verified) {
      return authJson(
        { error: verifiedSession.error },
        verifiedSession.status,
      );
    }

    // The bearer is read only after both local JWT claims and the current
    // Auth user agree on the subject. FastAPI re-verifies it at its boundary.
    const { data: sessionData } = await supabase.auth.getSession();
    const accessToken = sessionData.session?.access_token;
    if (!accessToken) {
      return authJson({ error: "login_required" }, 401);
    }

    const result = await acceptRealyticsInvitation(
      accessToken,
      invitationCode,
    );
    if (result.state === "accepted" && result.tenantId) {
      const response = authJson({
        schemaVersion: "realytics-manual-invitation-accept-v1",
        invitation: "accepted",
        tenantId: result.tenantId,
        next: "/start",
      });
      clearTenantSelectorCookie(response);
      setTenantSelectorCookie(response, result.tenantId);
      // A manually accepted invitation supersedes any older deferred link.
      clearInvitationIntentCookie(response);
      return response;
    }

    if (
      result.state === "profile_required"
      || result.state === "policy_update_required"
    ) {
      return authJson({
        error: result.errorCode,
        invitation: result.state,
        ...(result.currentPolicyVersions
          ? { currentPolicyVersions: result.currentPolicyVersions }
          : {}),
      }, 409);
    }

    if (result.state === "authority_unavailable") {
      return authJson({
        error: result.errorCode ?? "identity_invitation_authority_unavailable",
        invitation: result.state,
      }, 503);
    }

    return authJson({
      error: result.errorCode ?? "invitation_accept_failed",
      invitation: "rejected",
    }, terminalFailureStatus(result.httpStatus));
  } catch {
    return authJson({
      error: "identity_invitation_authority_unavailable",
      invitation: "authority_unavailable",
    }, 503);
  }
}

async function readInvitationCode(request: NextRequest): Promise<string | null> {
  const payload = await request.json().catch(() => null) as unknown;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return null;
  }
  const keys = Object.keys(payload);
  if (keys.length !== 1 || keys[0] !== "code") {
    return null;
  }
  const code = (payload as { code?: unknown }).code;
  if (typeof code !== "string") {
    return null;
  }
  const normalized = code.trim();
  return INVITATION_CODE_PATTERN.test(normalized) ? normalized : null;
}

function terminalFailureStatus(status: number): number {
  return status >= 400 && status < 500 ? status : 409;
}

function authJson(body: Record<string, unknown>, status = 200) {
  const response = NextResponse.json(body, { status });
  preventAuthenticatedResponseCaching(response);
  return response;
}
