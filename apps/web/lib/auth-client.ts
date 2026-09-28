import { supabaseConfigured } from "./supabase-client";
import { localPilotAuthEnabled } from "./auth-policy";
import { parseBackendErrorResponse } from "./backend-error";
import { safeIdentityLinkRedirectUrl } from "./supabase/identity-security";
import type { SignupPolicyConsent } from "./auth-onboarding-policy";
export {
  REALYTICS_PRIVACY_VERSION,
  REALYTICS_TERMS_VERSION,
} from "./auth-onboarding-policy";

// Client API calls always stay same-origin. The Next.js BFF verifies
// cookie-backed auth and adds the bearer/session header only on the
// server-to-server hop. Keeping this deterministic also prevents modules
// evaluated during SSR or tests from accidentally bypassing the BFF.
export const apiBase = "/api/backend";

export interface RealyticsUser {
  userId: string;
  tenantId?: string;
  email?: string;
  displayName?: string;
  role?: string;
  status?: string;
  metadata?: {
    issuer?: string;
    subject?: string;
    principalType?: string;
    [key: string]: unknown;
  };
}

export interface RealyticsSession {
  sessionId?: string;
  userId: string;
  tenantId?: string;
  provider?: string;
  host?: string;
  createdAt?: string;
  expiresAt?: string;
}

export interface StoredAuthSession {
  user: RealyticsUser;
  session: RealyticsSession;
  permissions: string[];
  authProvider?: "realytics" | "supabase";
}

export interface ResolvedIdentity {
  authenticated: boolean;
  user: RealyticsUser;
  session?: RealyticsSession;
  permissions?: string[];
  authSource?: string;
  identityProvider?: Record<string, unknown>;
  limitations?: string[];
}

export interface SupabaseAuthReadiness {
  schemaVersion: string;
  checkedAt: string;
  configured: boolean;
  settingsReachable: boolean;
  providers: { google: boolean; kakao: boolean; naver: boolean };
  providerUsable: { google: boolean; kakao: boolean; naver: boolean };
  providerReady: { google: boolean; kakao: boolean; naver: boolean };
  canonicalOriginReady: boolean;
  configurationReady: boolean;
  backend: {
    reachable: boolean;
    configured: boolean;
    mode: string | null;
    provider: string | null;
    issuerConfigured: boolean;
    jwksUrlConfigured: boolean;
    membershipAuthority: string | null;
    ready: boolean;
  };
  operational: boolean;
  limitations: string[];
}

export interface SupabaseSecurityProfile {
  schemaVersion: string;
  userId: string;
  requestedProvider: "google" | "kakao" | null;
  identities: Array<{
    identityId: string;
    provider: string;
    email: string | null;
    createdAt: string | null;
    lastSignInAt: string | null;
  }>;
  factors: Array<{
    factorId: string;
    factorType: string;
    status: string;
    friendlyName: string | null;
    createdAt: string;
    updatedAt: string;
  }>;
  assurance: {
    currentLevel: "aal1" | "aal2" | null;
    nextLevel: "aal1" | "aal2" | null;
    currentAuthenticationMethods: unknown[];
    recentAal2: {
      eligible: boolean;
      verifiedAt: string | null;
      limitation: "aal2_step_up_required" | "recent_aal2_required" | null;
    };
  };
  totpEnrollment: {
    eligible: boolean;
    accountNameSource: "verified_email" | null;
    limitation: "auth_email_required" | "verified_auth_email_required" | null;
  };
  operationalForHighRiskActions: boolean;
  limitations: string[];
}

export interface TotpEnrollment {
  schemaVersion: string;
  factorId: string;
  factorType: "totp";
  friendlyName: string | null;
  qrCode: string;
  secret: string;
  uri: string;
}

export type TenantInvitationRole =
  | "admin"
  | "reviewer"
  | "analyst"
  | "viewer"
  | "legal_reviewer"
  | "legal_counsel_approver"
  | "external_counsel"
  | "compliance_officer";

export interface TenantInvitation {
  invitationId: string;
  tenantId: string;
  role: TenantInvitationRole;
  status: "pending" | "accepted" | "revoked" | "expired";
  invitedEmail: string | null;
  invitedIssuer: string | null;
  invitedSubject: string | null;
  issuedByIssuer: string;
  issuedBySubject: string;
  issuedAt: string;
  expiresAt: string;
  acceptedByIssuer: string | null;
  acceptedBySubject: string | null;
  acceptedAt: string | null;
  revokedAt: string | null;
  revocationReason: string | null;
}

export interface IssuedTenantInvitation {
  invitation: TenantInvitation;
  token: string;
  tokenPolicy: "shown_once_never_persisted_in_plaintext";
}

export type TenantMembershipRole = TenantInvitationRole;

export interface TenantMembership {
  tenantId: string;
  issuer: string;
  subject: string;
  principalType: string;
  principalStatus: string;
  email: string | null;
  displayName: string | null;
  role: TenantMembershipRole;
  status: string;
  membershipVersion: number;
  updatedAt: string;
}

export interface TenantMembershipList {
  schemaVersion: "realytics-tenant-membership-list-v1";
  memberships: TenantMembership[];
}

export interface TenantMembershipRoleChange {
  schemaVersion: "realytics-tenant-membership-role-change-v1";
  membership: TenantMembership;
}

export interface ChangeTenantMembershipRoleInput {
  targetIssuer: string;
  targetSubject: string;
  expectedMembershipVersion: number;
  role: TenantMembershipRole;
  reason: string;
}

export type RealyticsAccessState =
  | "member"
  | "tenant_selection_required"
  | "onboarding_required"
  | "authority_unavailable";

export interface SupabasePasswordAuthResult {
  accessState: RealyticsAccessState;
  stored: StoredAuthSession | null;
  requiresEmailConfirmation: boolean;
  next: string;
}

export interface OnboardingProfile {
  displayName: string;
  phone: string | null;
  jobTitle: string | null;
  termsVersion: string;
  privacyVersion: string;
  profileVersion: number;
  updatedAt?: string | null;
}

export type OnboardingRequestStatus = "pending" | "approved" | "rejected";
export type OnboardingRequestType = "existing_organization" | "new_organization";

export interface OnboardingAccessRequest {
  requestId: string;
  requestType: OnboardingRequestType;
  organizationId: string | null;
  organizationName: string | null;
  businessRegistrationNumber: string | null;
  requestedRole: "analyst" | "viewer";
  message: string | null;
  status: OnboardingRequestStatus;
  requestVersion: number;
  decisionReason: string | null;
  submittedAt: string;
  decisionAt: string | null;
  updatedAt: string;
  requesterIssuer?: string | null;
  requesterSubject?: string | null;
  requesterEmail?: string | null;
  requesterDisplayName?: string | null;
  businessRegistrationVerified?: boolean | null;
  businessRegistrationEvidenceLocator?: string | null;
}

export interface OnboardingMembership {
  organizationId: string;
  organizationName: string;
  role: string;
  status: string;
  membershipVersion: number;
  organizationStatus: string;
  updatedAt: string;
}

export interface OnboardingStatus {
  schemaVersion?: string;
  accessState?: RealyticsAccessState;
  profile: OnboardingProfile | null;
  requests: OnboardingAccessRequest[];
  memberships?: OnboardingMembership[];
  invitation?: { status?: string; message?: string | null } | null;
  platformOperator?: boolean;
  policyVersions?: {
    termsVersion: string;
    privacyVersion: string;
  };
}

export interface OnboardingOrganization {
  organizationId: string;
  name: string;
}

export interface OnboardingOrganizationSearch {
  organizations: OnboardingOrganization[];
}

export interface DeferredInvitationAcceptance {
  schemaVersion: "realytics-deferred-invitation-accept-v1";
  invitation: "accepted";
  tenantId: string;
  next: "/start";
}

export interface ManualInvitationAcceptance {
  schemaVersion: "realytics-manual-invitation-accept-v1";
  invitation: "accepted";
  tenantId: string;
  next: "/start";
}

export interface AccessRequestList {
  schemaVersion?: string;
  requests: OnboardingAccessRequest[];
}

export class AuthClientRequestError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message: string,
    readonly reason: string | null = null,
  ) {
    super(message);
    this.name = "AuthClientRequestError";
  }
}

const ONBOARDING_REQUIRED_CODES = new Set([
  "principal_not_registered",
  "no_active_tenant_membership",
  "tenant_membership_not_found",
  "tenant_membership_inactive",
  "tenant_inactive",
  "invalid_membership_role",
  "identity_not_provisioned",
  "realytics_identity_not_provisioned",
]);

export function isOnboardingRequiredAuthError(error: unknown): boolean {
  return error instanceof AuthClientRequestError
    // Older deployed FastAPI identity boundaries returned 503 for an
    // otherwise healthy authority when the verified issuer/subject simply
    // had no principal or active membership row. Only the explicit domain
    // allow-list below is eligible for onboarding; an unknown 503 continues
    // to fail closed as an unavailable authority.
    && (error.status === 403 || error.status === 404 || error.status === 503)
    && ONBOARDING_REQUIRED_CODES.has(error.code);
}

export function isTenantSelectionRequiredAuthError(error: unknown): boolean {
  return error instanceof AuthClientRequestError
    && (error.status === 400 || error.status === 409)
    && error.code === "tenant_selection_required";
}

export function isBackendAuthorityUnavailableAuthError(error: unknown): boolean {
  return error instanceof AuthClientRequestError
    && error.status >= 500
    && !isOnboardingRequiredAuthError(error);
}

export class TenantMembershipRoleChangeError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message: string,
    readonly reason: string | null = null,
  ) {
    super(message);
    this.name = "TenantMembershipRoleChangeError";
  }
}

type TenantInvitationIssueBase = {
  role: TenantInvitationRole;
  expiresInHours?: number;
};

export type TenantInvitationIssueInput = TenantInvitationIssueBase & (
  | {
    invitedEmail: string;
    invitedIssuer?: never;
    invitedSubject?: never;
  }
  | {
    invitedEmail?: never;
    invitedIssuer: string;
    invitedSubject: string;
  }
);

const LEGACY_STORAGE_KEY = "realytics.auth.session.v1";
const TRANSIENT_AI_SECRET_PREFIX = "realytics.ai.session.";
let inMemoryIdentity: StoredAuthSession | null = null;

/**
 * Compatibility accessor for client components that need a display actor ID.
 * It returns process memory only; no auth material is persisted by REALYTICS.
 */
export function getStoredAuthSession(): StoredAuthSession | null {
  return inMemoryIdentity;
}

export function setStoredAuthSession(
  value: StoredAuthSession,
  options: { notify?: boolean } = {},
) {
  inMemoryIdentity = value;
  if (typeof window !== "undefined" && options.notify !== false) {
    window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
  }
}

export function clearStoredAuthSession(options: { notify?: boolean } = {}) {
  inMemoryIdentity = null;
  if (typeof window === "undefined") return;
  purgeLegacyBrowserAuthStorage();
  clearTransientAiSecrets();
  if (options.notify !== false) {
    window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
  }
}

export function purgeLegacyBrowserAuthStorage() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(LEGACY_STORAGE_KEY);
}

export function clearTransientAiSecrets() {
  if (typeof window === "undefined") return;
  for (let i = window.localStorage.length - 1; i >= 0; i -= 1) {
    const key = window.localStorage.key(i);
    if (key?.startsWith(TRANSIENT_AI_SECRET_PREFIX)) {
      window.localStorage.removeItem(key);
    }
  }
  for (let i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
    const key = window.sessionStorage.key(i);
    if (key?.startsWith(TRANSIENT_AI_SECRET_PREFIX)) {
      window.sessionStorage.removeItem(key);
    }
  }
}

/**
 * Authentication headers are deliberately absent in browser code. The custom
 * marker identifies an intentional same-origin BFF call but carries no
 * authority; the BFF strips it before forwarding.
 */
export function authHeaders(fallbackUserId = "web"): Record<string, string> {
  void fallbackUserId;
  return { "X-REALYTICS-BFF": "same-origin" };
}

export async function signInWithSupabasePassword(input: {
  email: string;
  password: string;
}): Promise<SupabasePasswordAuthResult> {
  const response = await fetch("/api/auth/supabase/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "login",
      email: input.email.trim(),
      password: input.password,
    }),
  }).then(assertOk);
  const result = await response.json() as {
    accessState?: RealyticsAccessState;
    next?: string;
  };
  const accessState = result.accessState ?? "onboarding_required";
  return {
    accessState,
    stored: accessState === "member"
      ? await requireResolvedIdentity("REALYTICS 멤버십을 확인하지 못했습니다.")
      : null,
    requiresEmailConfirmation: false,
    next: result.next ?? (accessState === "member" ? "/start" : "/onboarding"),
  };
}

export async function signUpWithSupabasePassword(input: {
  email: string;
  password: string;
  displayName?: string;
} & SignupPolicyConsent): Promise<SupabasePasswordAuthResult> {
  const response = await fetch("/api/auth/supabase/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: "signup",
      email: input.email.trim(),
      password: input.password,
      displayName: input.displayName?.trim(),
      termsAccepted: input.termsAccepted,
      privacyAccepted: input.privacyAccepted,
      termsVersion: input.termsVersion,
      privacyVersion: input.privacyVersion,
    }),
  }).then(assertOk);
  const result = await response.json() as {
    authenticated?: boolean;
    requiresEmailConfirmation?: boolean;
    accessState?: RealyticsAccessState;
    next?: string;
  };
  if (result.requiresEmailConfirmation || !result.authenticated) {
    return {
      accessState: result.accessState ?? "onboarding_required",
      stored: null,
      requiresEmailConfirmation: true,
      next: result.next ?? "/onboarding",
    };
  }
  const accessState = result.accessState ?? "onboarding_required";
  return {
    accessState,
    stored: accessState === "member"
      ? await requireResolvedIdentity("REALYTICS 멤버십을 확인하지 못했습니다.")
      : null,
    requiresEmailConfirmation: false,
    next: result.next ?? (accessState === "member" ? "/start" : "/onboarding"),
  };
}

export async function requestSupabasePasswordRecovery(email: string) {
  await fetch("/api/auth/supabase/password/recovery", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "request", email: email.trim() }),
  }).then(assertOk);
}

export async function resendSupabaseSignupConfirmation(email: string) {
  await fetch("/api/auth/supabase/password/confirmation", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: email.trim() }),
  }).then(assertOk);
}

export async function updateSupabasePassword(password: string) {
  await fetch("/api/auth/supabase/password/recovery", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "complete", password }),
  }).then(assertOk);
}

export async function signOutSupabaseSession(
  scope: "local" | "global" | "others" = "local",
) {
  if (!supabaseConfigured()) return;
  await fetch("/api/auth/supabase/signout", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  }).then(assertOk);
}

export async function loadSupabaseAuthReadiness(): Promise<SupabaseAuthReadiness> {
  const response = await fetch("/api/auth/supabase/readiness", { cache: "no-store" })
    .then(assertOk);
  return await response.json() as SupabaseAuthReadiness;
}

export async function loadSupabaseSecurityProfile(): Promise<SupabaseSecurityProfile> {
  const response = await fetch("/api/auth/supabase/security", { cache: "no-store" })
    .then(assertOk);
  return await response.json() as SupabaseSecurityProfile;
}

export async function loadOnboardingStatus(): Promise<OnboardingStatus> {
  const response = await fetch(`${apiBase}/auth/onboarding/status`, {
    cache: "no-store",
    headers: authHeaders(),
  }).then(assertOk);
  const body = await response.json() as Partial<OnboardingStatus> & {
    accessRequests?: OnboardingAccessRequest[];
  };
  return {
    ...body,
    profile: body.profile ?? null,
    requests: body.requests ?? body.accessRequests ?? [],
  };
}

export async function selectSupabaseTenant(tenantId: string): Promise<{
  selected: true;
  tenantId: string;
}> {
  const response = await fetch("/api/auth/supabase/tenant", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ tenantId: tenantId.trim() }),
  }).then(assertOk);
  return await response.json() as { selected: true; tenantId: string };
}

export async function saveOnboardingProfile(input: {
  displayName: string;
  phone?: string | null;
  jobTitle?: string | null;
  termsAccepted: true;
  privacyAccepted: true;
  termsVersion: string;
  privacyVersion: string;
  expectedProfileVersion: number;
}): Promise<OnboardingProfile> {
  const response = await fetch(`${apiBase}/auth/onboarding/profile`, {
    method: "PUT",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }).then(assertOk);
  const body = await response.json() as OnboardingProfile | { profile: OnboardingProfile };
  return "profile" in body ? body.profile : body;
}

export async function searchOnboardingOrganizations(
  query: string,
): Promise<OnboardingOrganization[]> {
  const response = await fetch(
    `${apiBase}/auth/onboarding/organizations?query=${encodeURIComponent(query.trim())}`,
    { cache: "no-store", headers: authHeaders() },
  ).then(assertOk);
  const body = await response.json() as
    | OnboardingOrganization[]
    | OnboardingOrganizationSearch;
  return Array.isArray(body) ? body : body.organizations ?? [];
}

export async function submitOnboardingRequest(input: {
  requestType: OnboardingRequestType;
  organizationId?: string;
  organizationName?: string;
  businessRegistrationNumber?: string | null;
  requestedRole?: "analyst" | "viewer";
  message?: string | null;
}): Promise<OnboardingAccessRequest> {
  const response = await fetch(`${apiBase}/auth/onboarding/requests`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }).then(assertOk);
  const body = await response.json() as
    | OnboardingAccessRequest
    | { request: OnboardingAccessRequest };
  return "request" in body ? body.request : body;
}

export async function resubmitOnboardingRequest(
  requestId: string,
  input: {
    expectedRequestVersion: number;
    requestedRole?: "analyst" | "viewer";
    message?: string | null;
    organizationName?: string;
    businessRegistrationNumber?: string | null;
  },
): Promise<OnboardingAccessRequest> {
  const response = await fetch(
    `${apiBase}/auth/onboarding/requests/${encodeURIComponent(requestId)}/resubmit`,
    {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  ).then(assertOk);
  const body = await response.json() as
    | OnboardingAccessRequest
    | { request: OnboardingAccessRequest };
  return "request" in body ? body.request : body;
}

export async function acceptPendingOnboardingInvitation(): Promise<DeferredInvitationAcceptance> {
  const response = await fetch("/api/auth/supabase/invitation/accept", {
    method: "POST",
    headers: authHeaders(),
  }).then(assertOk);
  return await response.json() as DeferredInvitationAcceptance;
}

export async function acceptOnboardingInvitationCode(
  code: string,
): Promise<ManualInvitationAcceptance> {
  const response = await fetch("/api/auth/supabase/invitation/code", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  }).then(assertOk);
  return await response.json() as ManualInvitationAcceptance;
}

export async function loadOnboardingAccessRequests(
  status: OnboardingRequestStatus | "all" = "pending",
  tenantId?: string | null,
  reviewScope: "tenant" | "platform" = "tenant",
): Promise<AccessRequestList> {
  const query = new URLSearchParams({ status });
  if (tenantId?.trim()) query.set("tenantId", tenantId.trim());
  if (reviewScope === "platform") query.set("reviewScope", "platform");
  const response = await fetch(
    `${apiBase}/auth/access-requests?${query.toString()}`,
    { cache: "no-store", headers: authHeaders() },
  ).then(assertOk);
  const body = await response.json() as
    | AccessRequestList
    | OnboardingAccessRequest[];
  return Array.isArray(body) ? { requests: body } : {
    ...body,
    requests: body.requests ?? [],
  };
}

export async function decideOnboardingAccessRequest(
  requestId: string,
  input: {
    decision: "approve" | "reject";
    expectedRequestVersion: number;
    assignedRole?: "admin" | "analyst" | "viewer";
    reason: string;
  },
): Promise<OnboardingAccessRequest> {
  const response = await fetch(
    `${apiBase}/auth/access-requests/${encodeURIComponent(requestId)}/decision`,
    {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  ).then(assertOk);
  const body = await response.json() as
    | OnboardingAccessRequest
    | { request: OnboardingAccessRequest };
  return "request" in body ? body.request : body;
}

export async function startSupabaseIdentityLink(
  provider: "google" | "kakao",
  nextPath = "/settings",
) {
  if (typeof window === "undefined") {
    throw new Error("Identity linking must start in the browser.");
  }
  const safeNext = nextPath.startsWith("/") && !nextPath.startsWith("//")
    ? nextPath
    : "/settings";
  const response = await fetch("/api/auth/supabase/identity/link", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ provider, next: safeNext }),
  }).then(assertOk);
  const body = await response.json() as { redirectUrl?: unknown };
  const redirectUrl = safeIdentityLinkRedirectUrl(
    provider,
    body.redirectUrl,
  );
  if (!redirectUrl) {
    throw new AuthClientRequestError(
      "identity_link_redirect_rejected",
      502,
      "계정 연결 주소를 안전하게 확인할 수 없습니다.",
    );
  }
  window.location.assign(redirectUrl);
}

async function postSupabaseSecurityAction<T>(body: Record<string, unknown>): Promise<T> {
  const response = await fetch("/api/auth/supabase/security", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(assertOk);
  return await response.json() as T;
}

export async function enrollSupabaseTotp(
  friendlyName = "REALYTICS Authenticator",
): Promise<TotpEnrollment> {
  return await postSupabaseSecurityAction<TotpEnrollment>({
    action: "mfa_enroll_totp",
    friendlyName,
  });
}

export async function verifySupabaseTotp(factorId: string, code: string) {
  return await postSupabaseSecurityAction<{ verified: boolean; assuranceLevel: "aal2" }>({
    action: "mfa_verify_totp",
    factorId,
    code,
  });
}

export async function reauthenticateSupabaseTotp(factorId: string, code: string) {
  return await postSupabaseSecurityAction<{
    schemaVersion: "realytics-mfa-reauthentication-v1";
    verified: true;
    assuranceLevel: "aal2";
  }>({
    action: "mfa_reauthenticate_totp",
    factorId,
    code,
  });
}

export async function unenrollSupabaseMfa(factorId: string) {
  return await postSupabaseSecurityAction<{ unenrolled: boolean; factorId: string }>({
    action: "mfa_unenroll",
    factorId,
  });
}

export async function unlinkSupabaseIdentity(identityId: string) {
  return await postSupabaseSecurityAction<{
    unlinked: boolean;
    identityId: string;
    provider: string;
  }>({
    action: "identity_unlink",
    identityId,
  });
}

export async function loadTenantInvitations(
  limit = 100,
): Promise<TenantInvitation[]> {
  const response = await fetch(
    `${apiBase}/auth/invitations?limit=${encodeURIComponent(String(limit))}`,
    { cache: "no-store", headers: authHeaders() },
  ).then(assertOk);
  const body = await response.json() as {
    invitations?: TenantInvitation[];
  };
  return body.invitations ?? [];
}

export async function loadTenantMemberships(
  limit = 100,
): Promise<TenantMembershipList> {
  const response = await fetch(
    `${apiBase}/auth/memberships?limit=${encodeURIComponent(String(limit))}`,
    { cache: "no-store", headers: authHeaders() },
  );
  if (!response.ok) {
    const error = await parseBackendErrorResponse(response);
    throw new Error(
      error.error === "identity_membership_administration_authority_unavailable"
        ? "테넌트 membership 권위 데이터베이스에 연결할 수 없습니다."
        : error.message ?? error.error ?? `Membership request failed with ${response.status}`,
    );
  }
  const body = await response.json() as TenantMembershipList;
  return {
    schemaVersion: body.schemaVersion,
    memberships: Array.isArray(body.memberships) ? body.memberships : [],
  };
}

export async function changeTenantMembershipRole(
  input: ChangeTenantMembershipRoleInput,
): Promise<TenantMembershipRoleChange> {
  const response = await fetch(`${apiBase}/auth/memberships/role`, {
    method: "PATCH",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      targetIssuer: input.targetIssuer,
      targetSubject: input.targetSubject,
      expectedMembershipVersion: input.expectedMembershipVersion,
      role: input.role,
      reason: input.reason.trim(),
    }),
  });
  if (!response.ok) {
    const error = await parseBackendErrorResponse(response);
    const code = error.error ?? "tenant_membership_role_change_failed";
    throw new TenantMembershipRoleChangeError(
      code,
      response.status,
      tenantMembershipRoleChangeErrorMessage(code),
      error.reason,
    );
  }
  return await response.json() as TenantMembershipRoleChange;
}

function tenantMembershipRoleChangeErrorMessage(code: string): string {
  const messages: Record<string, string> = {
    step_up_required: "역할 변경에는 최근 TOTP AAL2 추가 인증이 필요합니다. 상단 인증 보안에서 현재 세션을 AAL2로 승격한 뒤 다시 시도해 주세요.",
    membership_version_conflict: "다른 관리자가 먼저 이 사용자의 역할을 변경했습니다. 최신 membership을 다시 불러왔으니 내용을 확인해 주세요.",
    membership_self_change_forbidden: "관리자는 자신의 역할을 직접 변경할 수 없습니다. 다른 AAL2 테넌트 관리자에게 요청해 주세요.",
    membership_role_unchanged: "선택한 역할이 현재 역할과 같습니다. 다른 역할을 선택해 주세요.",
    last_tenant_admin_role_change_forbidden: "마지막 테넌트 관리자의 관리자 역할은 제거할 수 없습니다. 다른 관리자를 먼저 지정해 주세요.",
    tenant_membership_not_active: "비활성 membership의 역할은 변경할 수 없습니다.",
    target_principal_not_active: "비활성 또는 폐기된 사용자 principal의 역할은 변경할 수 없습니다.",
    target_principal_not_human: "서비스 principal에는 사람용 테넌트 역할을 지정할 수 없습니다.",
    tenant_membership_not_found: "변경 대상 membership을 찾을 수 없습니다. 목록을 새로고침해 주세요.",
    identity_membership_administration_authority_unavailable: "테넌트 membership 권위 데이터베이스에 연결할 수 없습니다.",
  };
  return messages[code] ?? `테넌트 역할 변경에 실패했습니다. (${code})`;
}

export function configuredSupabaseAuthIssuer(): string | null {
  const projectUrl = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim();
  if (!projectUrl) return null;
  try {
    const parsed = new URL(projectUrl);
    if (parsed.protocol !== "https:" || !parsed.hostname) return null;
    return `${parsed.origin}${parsed.pathname.replace(/\/$/, "")}/auth/v1`;
  } catch {
    return null;
  }
}

function normalizeSupabaseInvitationIssuer(value: string): string {
  const issuer = value.trim().replace(/\/+$/, "");
  let parsed: URL;
  try {
    parsed = new URL(issuer);
  } catch {
    throw new Error("Supabase Auth issuer는 유효한 HTTPS URL이어야 합니다.");
  }
  if (
    parsed.protocol !== "https:"
    || !parsed.hostname
    || parsed.username
    || parsed.password
    || parsed.search
    || parsed.hash
    || parsed.pathname !== "/auth/v1"
  ) {
    throw new Error("Supabase Auth issuer는 /auth/v1로 끝나는 정확한 HTTPS issuer여야 합니다.");
  }
  const configuredIssuer = configuredSupabaseAuthIssuer();
  if (configuredIssuer && issuer !== configuredIssuer) {
    throw new Error("입력한 issuer가 현재 REALYTICS Supabase Auth issuer와 일치하지 않습니다.");
  }
  return issuer;
}

function normalizeSupabaseInvitationSubject(value: string): string {
  const subject = value.trim().toLowerCase();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(subject)) {
    throw new Error("Supabase Auth subject는 사용자 상세의 정확한 UUID여야 합니다.");
  }
  return subject;
}

export async function issueTenantInvitation(
  input: TenantInvitationIssueInput,
): Promise<IssuedTenantInvitation> {
  const invitedEmail = input.invitedEmail?.trim() ?? "";
  const invitedIssuer = input.invitedIssuer?.trim() ?? "";
  const invitedSubject = input.invitedSubject?.trim() ?? "";
  const hasEmailBinding = Boolean(invitedEmail);
  const hasAnyIdentityBinding = Boolean(invitedIssuer || invitedSubject);
  if (hasEmailBinding === hasAnyIdentityBinding) {
    throw new Error("이메일 또는 Supabase 신원 중 하나의 초대 방식만 선택해야 합니다.");
  }
  if (hasAnyIdentityBinding && (!invitedIssuer || !invitedSubject)) {
    throw new Error("Supabase 신원 초대에는 issuer와 subject가 모두 필요합니다.");
  }

  const response = await fetch(`${apiBase}/auth/invitations`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      role: input.role,
      ...(hasEmailBinding
        ? { invitedEmail }
        : {
          invitedIssuer: normalizeSupabaseInvitationIssuer(invitedIssuer),
          invitedSubject: normalizeSupabaseInvitationSubject(invitedSubject),
        }),
      expiresInHours: input.expiresInHours ?? 72,
    }),
  }).then(assertOk);
  return await response.json() as IssuedTenantInvitation;
}

export async function revokeTenantInvitation(
  invitationId: string,
  reason: string,
) {
  const response = await fetch(
    `${apiBase}/auth/invitations/${encodeURIComponent(invitationId)}/revoke`,
    {
      method: "POST",
      headers: { ...authHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason.trim() }),
    },
  ).then(assertOk);
  return await response.json() as {
    invitation: { invitationId: string; status: "revoked" };
  };
}

export async function signInWithSupabaseOAuth(
  provider: "google" | "kakao" | "naver",
  nextPath = "/start",
  options: { switchAccount?: boolean } = {},
) {
  if (typeof window === "undefined") {
    throw new Error("Social login must start in the browser.");
  }
  const safeNext =
    nextPath.startsWith("/") && !nextPath.startsWith("//") ? nextPath : "/start";
  const start = new URL("/api/auth/supabase/oauth", window.location.origin);
  start.searchParams.set("provider", provider);
  start.searchParams.set("next", safeNext);
  if (options.switchAccount) start.searchParams.set("switch", "1");
  window.location.assign(start.toString());
}

export async function createLocalPilotSession(input: {
  tenantId: string;
  tenantName: string;
  userId: string;
  email?: string;
  displayName: string;
  role: string;
}) {
  if (!localPilotAuthEnabled()) {
    throw new Error("Local pilot authentication is disabled.");
  }
  await fetch(`${apiBase}/auth/tenants`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      tenantId: input.tenantId,
      name: input.tenantName,
      status: "active",
    }),
  }).then(assertOk);

  await fetch(`${apiBase}/auth/users`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      userId: input.userId,
      tenantId: input.tenantId,
      email: input.email,
      displayName: input.displayName,
      role: input.role,
      status: "active",
    }),
  }).then(assertOk);

  await createOpaqueLocalSession({
    userId: input.userId,
    tenantId: input.tenantId,
  });
  return await requireResolvedIdentity("로컬 파일럿 세션을 확인하지 못했습니다.");
}

export async function loginLocalPilotSession(input: {
  tenantId: string;
  userId: string;
}) {
  if (!localPilotAuthEnabled()) {
    throw new Error("Local pilot authentication is disabled.");
  }
  const usersResponse = await fetch(
    `${apiBase}/auth/users?tenant_id=${encodeURIComponent(input.tenantId)}&limit=1000`,
    { cache: "no-store", headers: authHeaders() },
  ).then(assertOk);
  const usersBody = await usersResponse.json() as { users?: RealyticsUser[] };
  const existingUser = (usersBody.users ?? []).find(
    (user) => user.userId === input.userId,
  );
  if (!existingUser) {
    throw new Error("등록된 사용자를 찾지 못했습니다. 먼저 회원가입을 진행해 주세요.");
  }

  await createOpaqueLocalSession({
    userId: existingUser.userId,
    tenantId: existingUser.tenantId,
  });
  return await requireResolvedIdentity("로컬 파일럿 세션을 확인하지 못했습니다.");
}

export async function resolveCurrentIdentity(): Promise<ResolvedIdentity> {
  const res = await fetch(`${apiBase}/auth/me`, {
    cache: "no-store",
    headers: authHeaders(),
  }).then(assertOk);
  return await res.json() as ResolvedIdentity;
}

export async function exchangeOauthSession(sessionId: string) {
  const response = await fetch("/api/auth/session-exchange", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessionId }),
  }).then(assertOk);
  const identity = await response.json() as ResolvedIdentity;
  return cacheResolvedIdentity(identity);
}

export function cacheResolvedIdentity(identity: ResolvedIdentity): StoredAuthSession {
  if (!identity.authenticated || !identity.user?.userId) {
    throw new Error("인증된 REALYTICS 사용자를 확인하지 못했습니다.");
  }
  const session: RealyticsSession = identity.session ?? {
    userId: identity.user.userId,
    tenantId: identity.user.tenantId,
    provider: identity.authSource === "bearer_token" ? "supabase" : "realytics",
    host: "web-bff",
  };
  const stored: StoredAuthSession = {
    authProvider:
      identity.authSource === "bearer_token" ? "supabase" : "realytics",
    user: identity.user,
    session,
    permissions: identity.permissions ?? [],
  };
  setStoredAuthSession(stored, { notify: false });
  return stored;
}

async function requireResolvedIdentity(message: string) {
  const identity = await resolveCurrentIdentity();
  if (!identity.authenticated) throw new Error(message);
  const stored = cacheResolvedIdentity(identity);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("realytics-auth-session-changed"));
  }
  return stored;
}

async function createOpaqueLocalSession(input: {
  userId: string;
  tenantId?: string;
}) {
  await fetch(`${apiBase}/auth/session`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({
      userId: input.userId,
      tenantId: input.tenantId,
      provider: "local-pilot",
      host: "web-bff",
    }),
  }).then(assertOk);
}

async function assertOk(res: Response) {
  if (!res.ok) {
    const body = await res.json().catch(() => ({})) as Record<string, unknown>;
    const detail = body.detail && typeof body.detail === "object"
      ? body.detail as Record<string, unknown>
      : null;
    const code = [detail?.error, detail?.code, body.error]
      .find((value): value is string => typeof value === "string")
      ?? "request_failed";
    const message = [detail?.message, typeof body.detail === "string" ? body.detail : null, body.message]
      .find((value): value is string => typeof value === "string")
      ?? authClientErrorMessage(code, res.status);
    const reason = [detail?.reason, body.reason]
      .find((value): value is string => typeof value === "string")
      ?? null;
    throw new AuthClientRequestError(code, res.status, message, reason);
  }
  return res;
}

function authClientErrorMessage(code: string, status: number): string {
  const messages: Record<string, string> = {
    authentication_failed: "이메일 또는 비밀번호를 확인해 주세요.",
    authentication_unavailable: "인증 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    auth_email_address_invalid: "메일을 받을 수 있는 올바른 이메일 주소를 입력해 주세요.",
    auth_email_delivery_not_configured: "현재 인증 메일 발송 설정을 사용할 수 없습니다. 관리자에게 Custom SMTP 설정을 요청해 주세요.",
    auth_email_delivery_unavailable: "인증 메일 제공자가 일시적으로 응답하지 않습니다. 잠시 후 다시 시도해 주세요.",
    auth_email_rate_limited: "인증 메일 요청이 너무 많습니다. 잠시 기다린 뒤 다시 시도해 주세요.",
    auth_email_request_failed: "인증 메일을 요청하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    signup_policy_consent_required: "가입하려면 현재 이용약관과 개인정보 처리방침에 모두 동의해야 합니다.",
    signup_policy_version_mismatch: "가입 정책이 갱신되었습니다. 최신 약관과 개인정보 처리방침을 다시 확인해 주세요.",
    password_policy_not_satisfied: "비밀번호는 12자 이상이어야 합니다.",
    weak_password_input: "비밀번호는 12~128자이며 영문 대문자·소문자·숫자·기호를 각각 한 글자 이상 포함해야 합니다.",
    password_recovery_context_required: "비밀번호 재설정 링크가 만료되었거나 유효하지 않습니다. 새 링크를 요청해 주세요.",
    password_recovery_unavailable: "비밀번호 재설정 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    password_recovery_failed: "비밀번호를 변경하지 못했습니다. 재설정 링크를 다시 요청해 주세요.",
    password_recovery_session_cleanup_failed: "비밀번호는 변경되었지만 복구 세션을 안전하게 종료하지 못했습니다. 브라우저를 닫고 다시 로그인해 주세요.",
    onboarding_profile_version_conflict: "다른 화면에서 프로필이 변경되었습니다. 최신 정보를 다시 불러와 확인해 주세요.",
    onboarding_profile_required: "초대를 수락하기 전에 프로필과 현재 정책 동의를 저장해 주세요.",
    access_request_version_conflict: "다른 작업자가 먼저 신청 상태를 변경했습니다. 최신 상태를 다시 불러와 확인해 주세요.",
    policy_version_mismatch: "이용약관 또는 개인정보 처리방침이 개정되었습니다. 최신 내용을 다시 확인하고 동의해 주세요.",
    terms_version_mismatch: "이용약관이 개정되었습니다. 최신 내용을 다시 확인하고 동의해 주세요.",
    privacy_version_mismatch: "개인정보 처리방침이 개정되었습니다. 최신 내용을 다시 확인하고 동의해 주세요.",
    invitation_code_invalid: "초대 코드 형식을 확인해 주세요. 코드는 주소가 아닌 코드 값만 입력해야 합니다.",
    invitation_intent_missing: "보류 중인 초대를 찾을 수 없습니다. 관리자가 보낸 초대 링크를 다시 열어 주세요.",
    invitation_expired: "초대가 만료되었습니다. 기관 관리자에게 새 초대를 요청해 주세요.",
    invitation_identity_mismatch: "이 초대는 현재 로그인 계정에 발급된 초대가 아닙니다. 올바른 계정으로 다시 로그인해 주세요.",
    invitation_email_mismatch: "초대 이메일과 현재 인증된 이메일이 일치하지 않습니다. 올바른 계정으로 다시 로그인해 주세요.",
    invitation_not_pending: "이미 사용되었거나 취소된 초대입니다. 기관 관리자에게 초대 상태를 확인해 주세요.",
    tenant_membership_already_exists: "이미 해당 워크스페이스의 멤버입니다. 워크스페이스 선택 화면에서 접속해 주세요.",
    identity_invitation_authority_unavailable: "초대 권한 데이터베이스를 확인할 수 없습니다. 초대는 보존되었으니 잠시 후 다시 시도해 주세요.",
    invitation_acceptance_response_invalid: "초대는 처리되었지만 Tenant 응답을 검증하지 못했습니다. 관리자에게 문의해 주세요.",
    invitation_accept_failed: "초대를 수락하지 못했습니다. 초대 상태를 확인하거나 새 초대를 요청해 주세요.",
    step_up_required: "이 작업에는 TOTP AAL2 추가 인증이 필요합니다.",
    aal2_step_up_required: "이 작업 전에 TOTP AAL2 추가 인증을 완료해 주세요.",
    recent_aal2_required: "최근 TOTP 추가 인증이 필요합니다. 새 코드를 입력한 뒤 다시 시도해 주세요.",
    primary_email_identity_cannot_be_unlinked: "기본 이메일 로그인 수단은 연결 해제할 수 없습니다.",
    last_identity_cannot_be_unlinked: "마지막 로그인 수단은 연결 해제할 수 없습니다.",
    unsupported_identity_unlink: "Google 또는 Kakao 연결만 해제할 수 있습니다.",
    identity_link_redirect_rejected: "계정 연결 주소를 안전하게 확인할 수 없습니다.",
    identity_link_failed: "외부 계정 연결을 시작하지 못했습니다.",
    verified_session_unavailable: "현재 로그인 세션을 확인할 수 없습니다. 다시 로그인해 주세요.",
    mfa_unenrolled_reauthentication_required: "MFA 요소는 해제되었으며 기존 AAL2 세션도 종료했습니다. 다시 로그인해 주세요.",
    access_request_self_approval_forbidden: "자신의 가입 신청은 직접 승인할 수 없습니다.",
    platform_operator_required: "신규 기관 신청은 플랫폼 운영자만 승인할 수 있습니다.",
  };
  return messages[code] ?? `요청을 처리하지 못했습니다. (HTTP ${status})`;
}
