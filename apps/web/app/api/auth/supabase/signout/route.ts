import { NextRequest, NextResponse } from "next/server";
import {
  preventAuthenticatedResponseCaching,
  requestIsSameOrigin,
} from "../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";

export async function POST(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return NextResponse.json({ error: "cross_site_request_rejected" }, { status: 403 });
  }
  const payload = await request.json().catch(() => null) as { scope?: unknown } | null;
  const requestedScope = payload?.scope;
  const scope = requestedScope === "global" || requestedScope === "others"
    ? requestedScope
    : "local";
  try {
    const supabase = await createSupabaseServerClient();
    const { error } = await supabase.auth.signOut({ scope });
    if (error) {
      return NextResponse.json({ error: "session_revocation_failed" }, { status: 503 });
    }
  } catch {
    // Clearing the browser-side cookie is handled by the SSR adapter when a
    // session exists. An already-expired/missing session remains signed out.
  }
  const response = NextResponse.json({ signedOut: true, scope });
  preventAuthenticatedResponseCaching(response);
  return response;
}
