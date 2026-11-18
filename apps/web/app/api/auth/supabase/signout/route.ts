import { NextRequest, NextResponse } from "next/server";
import {
  clearTenantSelectorCookie,
  preventAuthenticatedResponseCaching,
  requestIsSameOrigin,
} from "../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";

export async function POST(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return authJson({ error: "cross_site_request_rejected" }, { status: 403 });
  }
  const payload = await request.json().catch(() => null) as { scope?: unknown } | null;
  const requestedScope = payload?.scope;
  if (
    requestedScope !== undefined
    && requestedScope !== "local"
    && requestedScope !== "global"
    && requestedScope !== "others"
  ) {
    return authJson({ error: "invalid_signout_scope" }, { status: 400 });
  }
  const scope = requestedScope ?? "local";
  try {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.signOut({ scope });
    if (error) {
      return authJson({ error: "session_revocation_failed" }, { status: 503 });
    }
  } catch {
    // Do not report a successful account switch when the SSR adapter could
    // not confirm revocation and cookie clearing. The UI must remain on the
    // current identity and allow a retry instead of silently reusing it.
    return authJson({ error: "session_revocation_failed" }, { status: 503 });
  }
  const response = authJson({ signedOut: true, scope });
  if (scope !== "others") clearTenantSelectorCookie(response);
  return response;
}

function authJson(
  body: Record<string, unknown>,
  init?: { status?: number },
) {
  const response = NextResponse.json(body, init);
  preventAuthenticatedResponseCaching(response);
  return response;
}
