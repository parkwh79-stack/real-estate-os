import { NextRequest, NextResponse } from "next/server";
import {
  preventAuthenticatedResponseCaching,
  realyticsAcceptsBearer,
  requestIsSameOrigin,
} from "../../../../../lib/backend-session";
import { createSupabaseServerClient } from "../../../../../lib/supabase/server";

type PasswordPayload = {
  mode?: unknown;
  email?: unknown;
  password?: unknown;
  displayName?: unknown;
};

export async function POST(request: NextRequest) {
  if (!requestIsSameOrigin(request)) {
    return NextResponse.json({ error: "cross_site_request_rejected" }, { status: 403 });
  }

  const payload = await request.json().catch(() => null) as PasswordPayload | null;
  const mode = payload?.mode === "signup" ? "signup" : "login";
  const email = typeof payload?.email === "string" ? payload.email.trim() : "";
  const password = typeof payload?.password === "string" ? payload.password : "";
  const displayName =
    typeof payload?.displayName === "string" ? payload.displayName.trim() : "";
  if (!email || password.length < 6) {
    return NextResponse.json({ error: "invalid_credentials_input" }, { status: 400 });
  }

  try {
    const supabase = await createSupabaseServerClient();
    if (mode === "signup") {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          data: {
            display_name: displayName || email,
          },
        },
      });
      if (error) {
        return NextResponse.json({ error: "signup_failed" }, { status: 400 });
      }
      if (data.session) {
        const { data: claimsData, error: claimsError } =
          await supabase.auth.getClaims();
        const accessToken = data.session.access_token;
        if (
          claimsError
          || !claimsData?.claims
          || !(await realyticsAcceptsBearer(accessToken))
        ) {
          await supabase.auth.signOut({ scope: "local" });
          return NextResponse.json(
            { error: "realytics_identity_not_provisioned" },
            { status: 403 },
          );
        }
      }
      const response = NextResponse.json({
        authenticated: Boolean(data.session),
        requiresEmailConfirmation: !data.session,
      });
      preventAuthenticatedResponseCaching(response);
      return response;
    }

    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) {
      return NextResponse.json({ error: "invalid_login" }, { status: 401 });
    }
    const { data, error: claimsError } = await supabase.auth.getClaims();
    if (claimsError || !data?.claims) {
      await supabase.auth.signOut({ scope: "local" });
      return NextResponse.json({ error: "verified_claims_unavailable" }, { status: 401 });
    }
    const { data: sessionData } = await supabase.auth.getSession();
    const accessToken = sessionData.session?.access_token;
    if (!accessToken || !(await realyticsAcceptsBearer(accessToken))) {
      await supabase.auth.signOut({ scope: "local" });
      return NextResponse.json({ error: "realytics_identity_not_provisioned" }, { status: 403 });
    }
    const response = NextResponse.json({ authenticated: true });
    preventAuthenticatedResponseCaching(response);
    return response;
  } catch {
    return NextResponse.json({ error: "supabase_auth_not_configured" }, { status: 503 });
  }
}
