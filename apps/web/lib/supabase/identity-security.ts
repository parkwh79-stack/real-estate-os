export type LinkableIdentityProvider = "google" | "kakao";

export interface IdentityDescriptor {
  identityId: string;
  provider: string;
}

export type IdentityUnlinkLimitation =
  | "identity_not_found"
  | "last_identity_cannot_be_unlinked"
  | "primary_email_identity_cannot_be_unlinked"
  | "unsupported_identity_unlink";

export interface IdentityUnlinkEvaluation {
  eligible: boolean;
  limitation: IdentityUnlinkLimitation | null;
}

const PROVIDER_AUTHORIZATION_HOSTS: Record<LinkableIdentityProvider, ReadonlySet<string>> = {
  google: new Set(["accounts.google.com"]),
  kakao: new Set(["kauth.kakao.com"]),
};

const PROVIDER_AUTHORIZATION_PATHS: Record<LinkableIdentityProvider, ReadonlySet<string>> = {
  google: new Set(["/o/oauth2/auth", "/o/oauth2/v2/auth"]),
  kakao: new Set(["/oauth/authorize"]),
};

/**
 * REALYTICS treats email/password as the durable primary sign-in path. Only an
 * explicitly linked Google/Kakao identity can be removed, and Supabase's
 * two-identity minimum is enforced before contacting Auth.
 */
export function evaluateIdentityUnlink(
  identities: readonly IdentityDescriptor[],
  identityId: string,
): IdentityUnlinkEvaluation {
  const target = identities.find((identity) => identity.identityId === identityId);
  if (!target) return { eligible: false, limitation: "identity_not_found" };
  if (identities.length < 2) {
    return { eligible: false, limitation: "last_identity_cannot_be_unlinked" };
  }
  if (target.provider === "email") {
    return {
      eligible: false,
      limitation: "primary_email_identity_cannot_be_unlinked",
    };
  }
  if (target.provider !== "google" && target.provider !== "kakao") {
    return { eligible: false, limitation: "unsupported_identity_unlink" };
  }
  return { eligible: true, limitation: null };
}

/**
 * Supabase should return a provider authorization URL. Validate that response
 * before exposing it to browser navigation so a compromised/misconfigured
 * upstream response cannot become an open redirect.
 */
export function safeIdentityLinkRedirectUrl(
  provider: LinkableIdentityProvider,
  candidate: unknown,
): string | null {
  if (typeof candidate !== "string" || candidate.length > 4096) return null;

  let destination: URL;
  try {
    destination = new URL(candidate);
  } catch {
    return null;
  }
  if (
    destination.protocol !== "https:"
    || destination.username
    || destination.password
    || destination.port
    || destination.hash
  ) {
    return null;
  }

  const providerHostAllowed = PROVIDER_AUTHORIZATION_HOSTS[provider].has(
    destination.hostname.toLowerCase(),
  );
  const providerPathAllowed = PROVIDER_AUTHORIZATION_PATHS[provider].has(
    destination.pathname,
  );
  return providerHostAllowed && providerPathAllowed ? destination.toString() : null;
}
