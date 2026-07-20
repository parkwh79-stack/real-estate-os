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
  try {
    const supabase = await createSupabaseServerClient();
    await supabase.auth.signOut({ scope: "local" });
  } catch {
    // Clearing the browser-side cookie is handled by the SSR adapter when a
    // session exists. An already-expired/missing session remains signed out.
  }
  const response = NextResponse.json({ signedOut: true });
  preventAuthenticatedResponseCaching(response);
  return response;
}
