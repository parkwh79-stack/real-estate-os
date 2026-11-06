export type AuthIdentityProjection = {
  provider: string;
};

export type MfaFactorProjection = {
  factorType: string;
  status: string;
};

const APPROVED_OAUTH_PROVIDERS = new Set(["google", "kakao"]);

export function evaluateHighRiskAuthOperationalGate(input: {
  userId?: string | null;
  requestedProvider?: string | null;
  identities?: AuthIdentityProjection[];
  factors?: MfaFactorProjection[];
  currentLevel?: string | null;
}) {
  const userId = input.userId?.trim() ?? "";
  const requestedProvider = input.requestedProvider?.trim().toLowerCase() ?? "";
  const identities = input.identities ?? [];
  const factors = input.factors ?? [];
  const providerSupported = (
    !requestedProvider || APPROVED_OAUTH_PROVIDERS.has(requestedProvider)
  );
  const providerLinked = providerSupported && identities.some((identity) => (
    requestedProvider
      ? identity.provider === requestedProvider
      : APPROVED_OAUTH_PROVIDERS.has(identity.provider)
  ));
  const verifiedMfaFactor = factors.some((factor) => (
    ["totp", "phone"].includes(factor.factorType)
      && factor.status === "verified"
  ));
  const aal2 = input.currentLevel === "aal2";
  const limitations = [
    ...(!userId ? ["authenticated_user_missing"] : []),
    ...(!providerSupported ? ["unsupported_identity_provider"] : []),
    ...(providerSupported && !providerLinked ? [
      requestedProvider
        ? `${requestedProvider}_identity_not_linked`
        : "google_or_kakao_identity_not_linked",
    ] : []),
    ...(!verifiedMfaFactor ? ["verified_mfa_factor_missing"] : []),
    ...(!aal2 ? ["aal2_step_up_required"] : []),
  ];

  return {
    operational: limitations.length === 0,
    providerLinked,
    verifiedMfaFactor,
    aal2,
    limitations,
  };
}

export function evaluateMakerCheckerOperationalGate(input: {
  makerUserId?: string | null;
  checkerUserId?: string | null;
  makerTenantId?: string | null;
  checkerTenantId?: string | null;
  makerOperational?: boolean;
  checkerOperational?: boolean;
}) {
  const makerUserId = input.makerUserId?.trim() ?? "";
  const checkerUserId = input.checkerUserId?.trim() ?? "";
  const makerTenantId = input.makerTenantId?.trim() ?? "";
  const checkerTenantId = input.checkerTenantId?.trim() ?? "";
  const limitations = [
    ...(!makerUserId ? ["maker_user_missing"] : []),
    ...(!checkerUserId ? ["checker_user_missing"] : []),
    ...(makerUserId && checkerUserId && makerUserId === checkerUserId
      ? ["maker_checker_identity_not_separated"]
      : []),
    ...(!makerTenantId ? ["maker_tenant_membership_missing"] : []),
    ...(!checkerTenantId ? ["checker_tenant_membership_missing"] : []),
    ...(makerTenantId && checkerTenantId && makerTenantId !== checkerTenantId
      ? ["maker_checker_tenant_mismatch"]
      : []),
    ...(input.makerOperational !== true ? ["maker_aal2_not_ready"] : []),
    ...(input.checkerOperational !== true ? ["checker_aal2_not_ready"] : []),
  ];

  return {
    operational: limitations.length === 0,
    identitiesSeparated: Boolean(
      makerUserId && checkerUserId && makerUserId !== checkerUserId,
    ),
    tenantMatched: Boolean(
      makerTenantId && checkerTenantId && makerTenantId === checkerTenantId,
    ),
    limitations,
  };
}
