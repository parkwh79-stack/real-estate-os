export type AuthEmailOperation =
  | "signup"
  | "signup_confirmation_resend"
  | "password_recovery";

export type SupabaseAuthErrorLike = {
  code?: string;
  status?: number;
};

export type AuthEmailFailureCategory =
  | "rate_limited"
  | "configuration"
  | "delivery_unavailable"
  | "invalid_input"
  | "account_sensitive"
  | "provider_rejected";

export type AuthEmailFailure = {
  category: AuthEmailFailureCategory;
  providerCode: string;
  providerStatus: number | null;
  publicError: string | null;
  publicStatus: number | null;
  retryAfterSeconds?: number;
};

const CONFIGURATION_CODES = new Set([
  "email_address_not_authorized",
  "email_provider_disabled",
  "signup_disabled",
  "otp_disabled",
  "provider_disabled",
]);

const DELIVERY_UNAVAILABLE_CODES = new Set([
  "hook_timeout",
  "hook_timeout_after_retry",
  "request_timeout",
  "unexpected_failure",
]);

const ACCOUNT_SENSITIVE_CODES = new Set([
  "email_exists",
  "email_not_confirmed",
  "identity_not_found",
  "invalid_credentials",
  "user_already_exists",
  "user_not_found",
]);

/**
 * Converts Supabase Auth failures to a stable, secret-free operational class.
 * Raw provider messages must never cross the BFF boundary.
 *
 * Password recovery and resend deliberately hide account-sensitive results.
 * A per-address provider limit is also hidden for those two operations because
 * disclosing it could let a caller distinguish an existing account. IP/request
 * limits remain safe to expose because they describe the caller, not an account.
 */
export function classifyAuthEmailFailure(
  error: SupabaseAuthErrorLike,
  operation: AuthEmailOperation,
): AuthEmailFailure {
  const providerCode = typeof error.code === "string"
    ? error.code
    : "provider_error";
  const providerStatus = typeof error.status === "number"
    ? error.status
    : null;

  if (providerCode === "over_request_rate_limit") {
    return failure(
      "rate_limited",
      providerCode,
      providerStatus,
      "auth_email_rate_limited",
      429,
      60,
    );
  }
  if (providerCode === "over_email_send_rate_limit") {
    const mayDisclose = operation === "signup";
    return failure(
      "rate_limited",
      providerCode,
      providerStatus,
      mayDisclose ? "auth_email_rate_limited" : null,
      mayDisclose ? 429 : null,
      60,
    );
  }
  if (CONFIGURATION_CODES.has(providerCode)) {
    return failure(
      "configuration",
      providerCode,
      providerStatus,
      "auth_email_delivery_not_configured",
      503,
    );
  }
  if (DELIVERY_UNAVAILABLE_CODES.has(providerCode) || (providerStatus ?? 0) >= 500) {
    return failure(
      "delivery_unavailable",
      providerCode,
      providerStatus,
      operation === "password_recovery"
        ? null
        : "auth_email_delivery_unavailable",
      operation === "password_recovery" ? null : 503,
    );
  }
  if (providerCode === "email_address_invalid" || providerCode === "validation_failed") {
    return failure(
      "invalid_input",
      providerCode,
      providerStatus,
      "auth_email_address_invalid",
      400,
    );
  }
  if (ACCOUNT_SENSITIVE_CODES.has(providerCode)) {
    return failure(
      "account_sensitive",
      providerCode,
      providerStatus,
      null,
      null,
    );
  }
  return failure(
    "provider_rejected",
    providerCode,
    providerStatus,
    operation === "password_recovery" ? null : "auth_email_request_failed",
    operation === "password_recovery" ? null : 400,
  );
}

function failure(
  category: AuthEmailFailureCategory,
  providerCode: string,
  providerStatus: number | null,
  publicError: string | null,
  publicStatus: number | null,
  retryAfterSeconds?: number,
): AuthEmailFailure {
  return {
    category,
    providerCode,
    providerStatus,
    publicError,
    publicStatus,
    ...(retryAfterSeconds ? { retryAfterSeconds } : {}),
  };
}

export function publicAuthEmailFailureBody(
  failure: AuthEmailFailure,
): Record<string, unknown> | null {
  if (!failure.publicError || !failure.publicStatus) return null;
  return {
    error: failure.publicError,
    ...(failure.retryAfterSeconds
      ? { retryAfterSeconds: failure.retryAfterSeconds }
      : {}),
  };
}
