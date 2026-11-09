import type { NextRequest } from "next/server";
import { refreshSupabaseSession } from "./lib/supabase/proxy";

export async function proxy(request: NextRequest) {
  return await refreshSupabaseSession(request);
}

export const config = {
  matcher: [
    "/((?!api/backend|api/auth|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
