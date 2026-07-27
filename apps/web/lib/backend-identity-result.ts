export type RealyticsBearerIdentityResult =
  | { status: "accepted" }
  | { status: "not_provisioned" }
  | { status: "tenant_selection_required" }
  | { status: "identity_authority_unavailable" }
  | { status: "backend_unavailable" };

type FastApiErrorEnvelope = {
  detail?: {
    error?: unknown;
  };
};

const NOT_PROVISIONED_ERRORS = new Set([
  "principal_not_registered",
  "principal_disabled",
  "principal_inactive",
  "no_active_tenant_membership",
  "tenant_membership_not_found",
  "tenant_membership_inactive",
  "tenant_inactive",
  "invalid_membership_role",
]);

const IDENTITY_AUTHORITY_ERRORS = new Set([
  "authentication_required",
  "identity_membership_authority_unavailable",
  "identity_authority_context_incomplete",
  "identity_authority_integrity_error",
]);

function fastApiErrorCode(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const detail = (payload as FastApiErrorEnvelope).detail;
  return typeof detail?.error === "string" ? detail.error : null;
}

/**
 * Convert the FastAPI identity boundary into a small, non-sensitive contract.
 * Unknown or malformed replies always fail closed as an unavailable boundary.
 */
export function classifyRealyticsBearerIdentity(
  httpStatus: number,
  payload: unknown,
): RealyticsBearerIdentityResult {
  if (httpStatus >= 200 && httpStatus < 300) {
    if (
      payload &&
      typeof payload === "object" &&
      (payload as { authenticated?: unknown }).authenticated === true
    ) {
      return { status: "accepted" };
    }
    if (
      payload &&
      typeof payload === "object" &&
      (payload as { authenticated?: unknown }).authenticated === false
    ) {
      return { status: "not_provisioned" };
    }
    return { status: "backend_unavailable" };
  }

  const errorCode = fastApiErrorCode(payload);
  if (httpStatus === 400 && errorCode === "tenant_selection_required") {
    return { status: "tenant_selection_required" };
  }
  if (
    httpStatus === 403 &&
    errorCode &&
    NOT_PROVISIONED_ERRORS.has(errorCode)
  ) {
    return { status: "not_provisioned" };
  }
  if (
    (httpStatus === 401 ||
      httpStatus === 400 ||
      httpStatus === 403 ||
      httpStatus === 503) &&
    errorCode &&
    IDENTITY_AUTHORITY_ERRORS.has(errorCode)
  ) {
    return { status: "identity_authority_unavailable" };
  }
  if (httpStatus === 401) {
    return { status: "identity_authority_unavailable" };
  }
  return { status: "backend_unavailable" };
}

export function loginErrorForIdentityResult(
  result: RealyticsBearerIdentityResult,
  options: { invitationPending?: boolean } = {},
): string | null {
  switch (result.status) {
    case "accepted":
      return null;
    case "not_provisioned":
      return options.invitationPending
        ? "invitation_accept_failed"
        : "identity_not_provisioned";
    case "tenant_selection_required":
      return "tenant_selection_required";
    case "identity_authority_unavailable":
      return "identity_authority_unavailable";
    case "backend_unavailable":
      return "backend_unavailable";
  }
}
