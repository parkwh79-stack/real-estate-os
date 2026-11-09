import "server-only";

import type { SupabaseClient, User } from "@supabase/supabase-js";

export type VerifiedSupabaseSession =
  | {
      verified: true;
      user: User;
      subject: string;
    }
  | {
      verified: false;
      error: "login_required" | "verified_session_unavailable";
      status: 401 | 503;
    };

/**
 * `getClaims()` verifies the cookie JWT while `getUser()` asks Auth for the
 * current user. Requiring both, and matching their subjects, prevents a
 * recovery-sensitive mutation from trusting an unverified browser payload or
 * a stale/mismatched cookie alone.
 */
export async function verifyCurrentSupabaseSession(
  supabase: SupabaseClient,
): Promise<VerifiedSupabaseSession> {
  const [claimsResult, userResult] = await Promise.all([
    supabase.auth.getClaims(),
    supabase.auth.getUser(),
  ]);
  if (claimsResult.error || !claimsResult.data?.claims) {
    return { verified: false, error: "login_required", status: 401 };
  }
  if (userResult.error || !userResult.data.user) {
    return {
      verified: false,
      error: "verified_session_unavailable",
      status: 503,
    };
  }

  const subject = typeof claimsResult.data.claims.sub === "string"
    ? claimsResult.data.claims.sub
    : "";
  if (!subject || userResult.data.user.id !== subject) {
    return { verified: false, error: "login_required", status: 401 };
  }
  return {
    verified: true,
    user: userResult.data.user,
    subject,
  };
}
