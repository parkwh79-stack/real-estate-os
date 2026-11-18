import { NextResponse } from "next/server";
import { preventAuthenticatedResponseCaching } from "../../../../../lib/backend-session";
import { inspectSupabaseAuthReadiness } from "../../../../../lib/supabase/auth-readiness";

export async function GET() {
  const response = NextResponse.json(await inspectSupabaseAuthReadiness());
  preventAuthenticatedResponseCaching(response);
  return response;
}
