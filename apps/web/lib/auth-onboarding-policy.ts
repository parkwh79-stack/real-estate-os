/**
 * Public policy identifiers rendered by the signup and onboarding clients.
 *
 * They are identifiers only, never proof of acceptance. The signup BFF checks
 * the submitted values before it creates an Auth user, and FastAPI records the
 * authoritative acceptance again after email verification.
 */
export const REALYTICS_TERMS_VERSION = "realytics-terms-2026-07-30";
export const REALYTICS_PRIVACY_VERSION = "realytics-privacy-2026-07-30";

export type SignupPolicyConsent = {
  termsAccepted: true;
  privacyAccepted: true;
  termsVersion: string;
  privacyVersion: string;
};

export function hasCurrentSignupPolicyConsent(
  value: Record<string, unknown>,
): value is Record<string, unknown> & SignupPolicyConsent {
  return value.termsAccepted === true
    && value.privacyAccepted === true
    && value.termsVersion === REALYTICS_TERMS_VERSION
    && value.privacyVersion === REALYTICS_PRIVACY_VERSION;
}
