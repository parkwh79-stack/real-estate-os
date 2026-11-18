export type InvitationAcceptanceState =
  | "accepted"
  | "profile_required"
  | "policy_update_required"
  | "rejected"
  | "authority_unavailable";

export type InvitationPolicyVersions = {
  termsVersion: string;
  privacyVersion: string;
};

export type InvitationAcceptanceResult = {
  state: InvitationAcceptanceState;
  errorCode: string | null;
  httpStatus: number;
  tenantId: string | null;
  currentPolicyVersions: InvitationPolicyVersions | null;
};

const PROFILE_REQUIRED_ERRORS = new Set([
  "onboarding_profile_required",
  "onboarding_profile_snapshot_missing",
]);

const POLICY_UPDATE_ERRORS = new Set([
  "terms_version_mismatch",
  "privacy_version_mismatch",
  "onboarding_policy_reacceptance_required",
]);

/**
 * Reduces the FastAPI invitation response to a token-free browser contract.
 * No backend payload is forwarded wholesale because it may grow privileged
 * fields over time; only the accepted Tenant and current policy versions are
 * allow-listed.
 */
export function classifyInvitationAcceptanceResponse(
  httpStatus: number,
  payload: unknown,
): InvitationAcceptanceResult {
  if (httpStatus >= 200 && httpStatus < 300) {
    const tenantId = acceptedTenantId(payload);
    if (!tenantId) {
      return {
        state: "authority_unavailable",
        errorCode: "invitation_acceptance_response_invalid",
        httpStatus: 502,
        tenantId: null,
        currentPolicyVersions: null,
      };
    }
    return {
      state: "accepted",
      errorCode: null,
      httpStatus,
      tenantId,
      currentPolicyVersions: null,
    };
  }

  const errorCode = backendErrorCode(payload) || "invitation_accept_failed";
  const currentPolicyVersions = policyVersions(payload);
  if (PROFILE_REQUIRED_ERRORS.has(errorCode)) {
    return {
      state: "profile_required",
      errorCode,
      httpStatus,
      tenantId: null,
      currentPolicyVersions,
    };
  }
  if (POLICY_UPDATE_ERRORS.has(errorCode)) {
    return {
      state: "policy_update_required",
      errorCode,
      httpStatus,
      tenantId: null,
      currentPolicyVersions,
    };
  }
  if (httpStatus >= 500) {
    return {
      state: "authority_unavailable",
      errorCode,
      httpStatus,
      tenantId: null,
      currentPolicyVersions,
    };
  }
  return {
    state: "rejected",
    errorCode,
    httpStatus,
    tenantId: null,
    currentPolicyVersions,
  };
}

export function invitationIntentIsDeferred(
  state: InvitationAcceptanceState,
): boolean {
  return state === "profile_required" || state === "policy_update_required";
}

export function shouldClearInvitationIntent(
  state: InvitationAcceptanceState,
): boolean {
  return state === "accepted" || state === "rejected";
}

function acceptedTenantId(payload: unknown): string | null {
  const root = objectValue(payload);
  const invitation = objectValue(root?.invitation);
  return safeTenantId(invitation?.tenantId);
}

function backendErrorCode(payload: unknown): string | null {
  const root = objectValue(payload);
  const detail = objectValue(root?.detail);
  for (const value of [detail?.error, detail?.code, root?.error]) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function policyVersions(payload: unknown): InvitationPolicyVersions | null {
  const root = objectValue(payload);
  const detail = objectValue(root?.detail);
  const versions = objectValue(
    detail?.currentPolicyVersions ?? root?.currentPolicyVersions,
  );
  const termsVersion = safePolicyVersion(versions?.termsVersion);
  const privacyVersion = safePolicyVersion(versions?.privacyVersion);
  return termsVersion && privacyVersion
    ? { termsVersion, privacyVersion }
    : null;
}

function safeTenantId(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const tenantId = value.trim();
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(tenantId)
    ? tenantId
    : null;
}

function safePolicyVersion(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const version = value.trim();
  return version && version.length <= 160 && !containsControlCharacter(version)
    ? version
    : null;
}

function containsControlCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const codePoint = character.codePointAt(0) ?? 0;
    return codePoint <= 31 || codePoint === 127;
  });
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}
