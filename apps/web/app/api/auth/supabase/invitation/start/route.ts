import { NextRequest, NextResponse } from "next/server";
import {
  canonicalWebOrigin,
  preventAuthenticatedResponseCaching,
  setInvitationIntentCookie,
} from "../../../../../../lib/backend-session";

const TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,512}$/;

export async function GET(request: NextRequest) {
  const origin = canonicalWebOrigin(request);
  if (!origin) {
    return NextResponse.json(
      { error: "canonical_web_origin_not_configured" },
      { status: 503 },
    );
  }
  const token = request.nextUrl.searchParams.get("token") || "";
  if (!TOKEN_PATTERN.test(token)) {
    return NextResponse.redirect(`${origin}/login?error=invitation_invalid`);
  }
  const response = NextResponse.redirect(`${origin}/login?invitation=pending`);
  setInvitationIntentCookie(response, token);
  preventAuthenticatedResponseCaching(response);
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
