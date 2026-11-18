export const PASSWORD_RECOVERY_NEXT_PATH = "/login?mode=reset";

type RecoveryAccessState =
  | "member"
  | "tenant_selection_required"
  | "onboarding_required"
  | "authority_unavailable";

export function passwordRecoveryCallbackUrl(origin: string): URL {
  const callback = new URL("/auth/callback", origin);
  callback.searchParams.set("next", PASSWORD_RECOVERY_NEXT_PATH);
  callback.searchParams.set("recovery", "1");
  return callback;
}

export function passwordRecoveryDestination(
  origin: string,
  nextPath: string,
  accessState: RecoveryAccessState,
): URL {
  const destination = new URL(nextPath, origin);
  destination.searchParams.set("flow", "password_recovery");
  destination.searchParams.set("accessState", accessState);
  return destination;
}

export function passwordRecoveryCallbackPlan(
  origin: string,
  nextPath: string,
  accessState: RecoveryAccessState,
) {
  return {
    destination: passwordRecoveryDestination(
      origin,
      nextPath,
      accessState,
    ),
    setRecoveryContextCookie: true as const,
  };
}
