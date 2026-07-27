export interface MfaFactorDescriptor {
  id: string;
  factor_type: string;
  status: string;
}

export interface TotpChallengeInput {
  factorId: string;
  code: string;
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
