export interface MfaFactorDescriptor {
  id: string;
  factor_type: string;
  status: string;
}

export interface TotpChallengeInput {
  factorId: string;
  code: string;
}

export interface AuthenticationMethodReference {
  method?: unknown;
  timestamp?: unknown;
}

export interface RecentAal2Evaluation {
  eligible: boolean;
  verifiedAt: string | null;
  limitation: "aal2_step_up_required" | "recent_aal2_required" | null;
}

export const HIGH_RISK_AAL2_MAX_AGE_SECONDS = 5 * 60;

const MAX_CLOCK_SKEW_SECONDS = 60;

export interface TotpEnrollmentEligibility {
  eligible: boolean;
  accountNameSource: "verified_email" | null;
  limitation: "auth_email_required" | "verified_auth_email_required" | null;
}

export function evaluateTotpEnrollmentEligibility(user: {
  email?: string | null;
  email_confirmed_at?: string | null;
}): TotpEnrollmentEligibility {
  const email = typeof user.email === "string" ? user.email.trim() : "";
  if (!email) {
    return {
      eligible: false,
      accountNameSource: null,
      limitation: "auth_email_required",
    };
  }
  if (!user.email_confirmed_at) {
    return {
      eligible: false,
      accountNameSource: null,
      limitation: "verified_auth_email_required",
    };
  }
  return {
    eligible: true,
    accountNameSource: "verified_email",
    limitation: null,
  };
}

export function parseTotpChallengeInput(input: {
  factorId: unknown;
  code: unknown;
}): TotpChallengeInput | null {
  const factorId = typeof input.factorId === "string" ? input.factorId.trim() : "";
  const code = typeof input.code === "string" ? input.code.trim() : "";
  if (!/^[A-Za-z0-9._:-]{1,160}$/.test(factorId) || !/^\d{6,8}$/.test(code)) {
    return null;
  }
  return { factorId, code };
}

export function findVerifiedTotpFactor<T extends MfaFactorDescriptor>(
  factors: readonly T[],
  factorId: string,
): T | null {
  return factors.find((factor) => (
    factor.id === factorId
      && factor.factor_type === "totp"
      && factor.status === "verified"
  )) ?? null;
}

/**
 * A long-lived `aal2` claim is insufficient for recovery-sensitive account
 * changes. Supabase includes an AMR timestamp for the TOTP verification that
 * promoted the current session; require that proof to be both present and
 * recent. RFC-8176 string-only AMR values deliberately fail closed because
 * they do not carry a verifiable timestamp.
 */
export function evaluateRecentAal2(
  currentLevel: unknown,
  authenticationMethods: readonly (AuthenticationMethodReference | string)[],
  nowEpochSeconds = Math.floor(Date.now() / 1000),
  maxAgeSeconds = HIGH_RISK_AAL2_MAX_AGE_SECONDS,
): RecentAal2Evaluation {
  if (currentLevel !== "aal2") {
    return {
      eligible: false,
      verifiedAt: null,
      limitation: "aal2_step_up_required",
    };
  }

  const latestTotpTimestamp = authenticationMethods.reduce<number | null>(
    (latest, entry) => {
      if (
        typeof entry === "string"
        || entry.method !== "totp"
        || typeof entry.timestamp !== "number"
        || !Number.isSafeInteger(entry.timestamp)
        || entry.timestamp <= 0
      ) {
        return latest;
      }
      return latest === null || entry.timestamp > latest ? entry.timestamp : latest;
    },
    null,
  );

  if (latestTotpTimestamp === null) {
    return {
      eligible: false,
      verifiedAt: null,
      limitation: "recent_aal2_required",
    };
  }

  const ageSeconds = nowEpochSeconds - latestTotpTimestamp;
  if (
    ageSeconds < -MAX_CLOCK_SKEW_SECONDS
    || ageSeconds > maxAgeSeconds
  ) {
    return {
      eligible: false,
      verifiedAt: new Date(latestTotpTimestamp * 1000).toISOString(),
      limitation: "recent_aal2_required",
    };
  }

  return {
    eligible: true,
    verifiedAt: new Date(latestTotpTimestamp * 1000).toISOString(),
    limitation: null,
  };
}
