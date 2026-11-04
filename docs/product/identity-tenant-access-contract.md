# Identity, Tenant, and Access Contract

Updated: 2026-11-04
Status: Approved policy contract; email/password onboarding implemented; live onboarding/RLS rehearsal pending
Production identity provider: Supabase Auth email/password, with Google and Kakao as optional linked identities

## Password sign-in handoff (2026-11-04)

The password route verifies the session and resolves access before returning
the destination. The login form navigates from that response. The destination
then checks the cookie-backed session and workspace permission before showing
protected content. Account switching continues to wait for Supabase sign-out.

## Simple signup pilot (2026-11-03 owner direction)

The owner deferred organization invitations and approval during initial signup.
A verified account can enter the dashboard through a basic analysis workspace:
sign up/sign in -> prepare the server-selected workspace -> verify its tenant
selection -> dashboard. Existing email confirmation, identity verification,
tenant isolation and final financial/publication gates remain.

- The server-owned REALYTICS_SIMPLE_SIGNUP_ENABLED flag defaults closed and is
  enabled only for the existing Cloud Run pilot. GET /auth/workspaces/default
  advertises capability to a verified human without querying the database.
- POST accepts no client identity, tenant, role or redirect inputs. It uses the
  verified issuer/subject and existing API principal transaction. An active
  single membership is reused with its original role; multiple memberships keep
  the existing selection. Otherwise the server atomically creates a stable
  personal workspace, analyst membership and append-only creation event.
- Repeated/concurrent signup is idempotent. Guest/service tokens, disabled
  principals, revoked memberships and conflicting workspace records fail closed.
  No organization request is approved and no policy-consent record is invented.
- Personal workspaces are absent from organization search and cannot be targeted
  by organization access requests. Existing organization records and workflows
  remain available when the pilot flag is off and for their authorized admins.
- The BFF continues to verify Supabase cookies and the selected tenant. The
  browser never receives a bearer token, chooses a role or grants membership.
  Workspace preparation writes are not automatically retried; explicit retries
  are safe because the transaction is idempotent.
- No schema, password, role grant or identity-reader write is required.

This approved basic-workspace lane supersedes the organization-approval
prerequisite for this pilot's initial dashboard entry. It is not organization,
reviewer, platform-operator, legal or final-approval authority.

Email-first onboarding follow-up (2026-07-30): a user may create a Supabase
email/password account, confirm the email, and retain that authenticated session
without receiving tenant authority. The memberless principal can save a
versioned profile/consent record, accept an invitation, search the bounded
organization directory, or submit an existing/new organization access request.
Approval is a separate normalized PostgreSQL decision: a tenant administrator
may approve only an existing-organization request in that tenant, while a
migration-provisioned platform onboarding operator handles new-organization
requests. Decisions require recent AAL2, an expected request version, a human
reason, and a different maker/checker. Google and Kakao remain available as
explicitly linked or legacy sign-in identities; same-email identities are never
merged automatically. Public guest access is a static sample walkthrough only:
it creates no Supabase anonymous user and can call no tenant, project, upload,
analysis, or document API.

Implementation note (2026-07-22): the BFF now exposes secret-safe provider
readiness, explicit Google/Kakao identity linking, TOTP management, scoped
session revocation, and a short-lived HttpOnly invitation intent. Migration
`0016_identity_invitation_lifecycle.sql` and the FastAPI repository implement
hash-only invitation issue/revoke/accept with replay, expiry, binding, role and
membership checks. Hardened high-risk actions require recent AAL2. Migration
`0016` is live and both migration ledgers match. The credential-free
`realytics_identity` role and its exact grant audit are live through migration
`0017`, with matching ledgers and no pending migration. The hosted Supabase
project now has both Google and Kakao enabled. Google and Kakao have each
completed a real PKCE exchange and normalized tenant membership lookup.
Provider enablement alone remains insufficient evidence; the live checks below
define the operational sign-off boundary.

Follow-up implementation (2026-07-24): tenant administrators with
`user:manage` can issue, list, and revoke email-bound invitations in Settings.
The plaintext token remains in browser memory only for its one-time handoff and
is neither returned by list APIs nor written to browser storage. The
environment-gated `auth-live.spec.ts` harness consumes an ignored,
interactively authenticated Playwright storage state to verify a real provider,
PostgreSQL membership, linked identity, TOTP/AAL2, and optional other-session
revocation without committing provider credentials or session material.

Operational Gate follow-up (2026-07-28): final PF approval and final report
publication both require a signed `aal2` session in hardened mode. FastAPI uses
the verified strong-factor AMR timestamp for freshness and does not promote an
`aal1` token merely because it contains an OTP-like method label. The live
harness now supports two independent storage states, proves the recorded maker
cannot self-approve, and lets the distinct AAL2 checker approve and publish a
dedicated rehearsal artifact. Hosted Google now has a verified TOTP factor and
an active AAL2 session. A scoped other-session sign-out preserved the current
session, but no second session existed, so cross-device invalidation is not yet
claimed. Hosted Kakao completed PKCE, explicit invitation acceptance, and
active normalized membership. The destructive two-person Gate remains blocked
until a second independent human checker obtains a fresh AAL2 session, the API
is restarted with high-risk AAL2 enforcement and authoritative PostgreSQL
storage, and dedicated rehearsal run/report IDs are selected.

TOTP session follow-up (2026-07-27): a later OAuth login that returns to AAL1
can now step up with an already verified TOTP factor in Settings. The BFF
validates the factor/code shape, resolves the exact factor from Supabase's
verified TOTP list before challenge, calls `challengeAndVerify`, and then
requires the refreshed session to report `aal2`. An unverified, wrong-type, or
unknown factor fails before challenge. The existing-factor path never returns a
factor secret and clears the submitted one-time code after every outcome. New
factor enrollment and its QR/secret handoff remain a separate, explicit flow.

Kakao email-optional follow-up (2026-07-28): an identity without a verified
Auth email may authenticate and receive membership by exact `iss + sub`, but
hosted GoTrue cannot create a TOTP factor for that identity because its TOTP
account name is absent. The BFF must return
`verified_email_required_for_totp` without calling enrollment. A unique email
must be added and verified through the supported Auth confirmation flow before
TOTP is offered. REALYTICS must not synthesize or directly confirm an email,
and it must not use same-email matching to merge principals or copy tenant
authority.

Membership administration follow-up (2026-07-30): Settings reads the current
tenant's active and inactive membership directory from normalized PostgreSQL,
keyed by exact `issuer + subject`. A role change requires `user:manage`, a
recent server-verified AAL2 session, a distinct active human target principal, a human
reason, and the expected membership version. The repository repeats the tenant
predicate under the API tenant transaction, locks the target row, prevents
self-change and removal of the last active administrator, and atomically
appends `membership_role_changed`. OAuth email and provider metadata remain
display-only and never select the target. The identity pool stays read-only;
the tenant-scoped update uses the separately preflighted API runtime role.

## 1. Purpose

This document defines how an externally authenticated person becomes an
authorized REALYTICS principal inside one or more tenants. Authentication and
authorization are deliberately separate:

```text
Supabase Auth proves identity
  -> PostgreSQL resolves principal
  -> PostgreSQL resolves active tenant membership
  -> FastAPI applies permissions and domain approval policy
```

Authentication alone never grants a shared-organization role, reviewer/legal
approval or commercial entitlement. The enabled simple-signup lane above grants
only a server-recorded basic analysis membership before dashboard access.

## 2. Sources of truth

| Concern | Source of truth | Not authoritative |
|---|---|---|
| External identity | Signed JWT `iss + sub` | email, display name, request header |
| Supabase session | Supabase Auth session/token lifecycle | browser-created session object |
| REALYTICS principal status | PostgreSQL `principals` | `user_metadata` |
| Tenant membership | PostgreSQL `tenant_memberships` | JWT tenant claim, legacy `users.tenant_id` |
| Tenant role | PostgreSQL membership role | JWT role, OAuth provider profile |
| Project authorization | FastAPI permission policy plus project tenant lineage | UI visibility or route possession |
| Legal/PF approval authority | Domain role, maker-checker, delegation, evidence and gate rules | general tenant administration alone |
| Commercial entitlement | PostgreSQL entitlement projection | Auth subscription metadata or JWT claim |

## 3. Principal identity

The canonical external identity key is:

```text
principal_key = canonical_issuer + "::" + subject
```

- `subject` must come from a verified signed token.
- Email is a profile and contact attribute only.
- Email changes do not change the principal key.
- Missing or private Kakao email does not prevent authentication, but it cannot
  grant membership or link another account.
- The same human may temporarily have more than one provider identity. Linking
  them is a reviewed directory action, not an automatic email match.
- A principal has `principal_type = human | service | unknown` and
  `status = active | disabled | revoked`.
- `unknown`, `disabled`, and `revoked` principals cannot perform protected
  actions.

## 4. Provider and account-linking policy

### 4.1 Email and password

Email/password is the default account bootstrap behind Supabase Auth.

- Hosted email confirmation is required before the onboarding session is
  accepted.
- The minimum password contract is 12 characters with lowercase, uppercase,
  digit, and symbol; the hosted Auth policy and BFF validate the same rule.
- Password recovery is a same-origin BFF flow with a short-lived HttpOnly
  recovery context. Responses must not disclose whether an account exists.
- Auth account creation is not principal registration, tenant membership, or
  commercial entitlement.
- Current terms and privacy versions are server-owned. The browser may render
  them, but PostgreSQL acceptance is recorded only after FastAPI validates the
  submitted versions against the server policy.

### 4.2 Google and Kakao

Google and Kakao are authentication methods behind Supabase Auth. REALYTICS does
not store provider passwords or reproduce their OAuth token exchange in
FastAPI. They are optional account-linking/sign-in methods and are not required
to complete the email-first membership application.

The public signup/login UI exposes only email/password until the provider live
gate is approved. An approved member may explicitly link Google or Kakao from
Settings behind recent TOTP AAL2; provider sign-in can be surfaced later
without changing tenant membership authority.

### 4.3 No automatic email merge

Two identities must not be merged only because their emails match. An explicit
link operation requires:

1. a cookie-backed current session verified by both signed claims and the Auth
   user endpoint, with matching subjects;
2. proof of control of the identity being linked;
3. collision check against existing principals and memberships;
4. confirmation of the surviving canonical principal;
5. append-only link audit event;
6. a reversible administrative recovery path.

Until the link succeeds, the identities remain separate. Membership is never
copied from one identity to another based only on email.

The browser cannot initiate linking through a side-effecting GET. It must show
an explicit confirmation and submit a same-origin POST to the Next.js BFF. The
BFF requires a timestamped TOTP AMR proof within the controlled five-minute
window, invokes Supabase manual linking server-side, and returns only a
provider authorization URL whose HTTPS host and authorization path match the
Google/Kakao allowlist. Caller-supplied `next` values are reduced
to safe internal paths and provider errors are mapped to stable, non-secret
codes. Existing OAuth sign-in remains a separate PKCE GET start flow.

### 4.4 Provider unlink and last identity

The email/password identity is the durable primary sign-in and recovery path
and cannot be unlinked in self-service. Only an explicitly linked Google or
Kakao identity can be removed, at least two identities must exist before the
request, and the BFF re-resolves the target from the verified current user.
Provider unlink, TOTP unenrollment, account recovery, and suspicious sign-in
handling require explicit confirmation and a fresh TOTP AAL2 proof and must be
auditable. TOTP unenrollment immediately refreshes the Auth session to remove
the stale AAL2 claim; a refresh failure revokes the local session and requires
login again.

## 5. Tenant onboarding

Production onboarding is invite-first or administrator-approved. Personal
tenant fallback, automatic OAuth-email matching, and unaudited just-in-time
tenant creation are disabled.

Recommended lifecycle:

```text
Supabase account created and email confirmed
  -> memberless verified iss + sub session
  -> invitation accepted, or profile/consent saved
  -> existing organization selected or new organization requested
  -> AAL2 distinct administrator/platform-operator decision
  -> principal and active membership created atomically
  -> protected tenant workspace available
```

The normalized onboarding states are separate from authentication:

```text
profile_required | application_required | pending | rejected | approved
```

- Rejected requests retain the decision reason and may be resubmitted only by
  compare-and-swap request version.
- Existing-organization approval cannot create or target another tenant.
- New-organization approval creates the tenant, principal, membership, request
  decision, and append-only events in one API-role transaction.
- The requester cannot approve their own request.
- A provider display name, email, or `user_metadata` value never grants a
  tenant role.
- The pre-membership identity role receives only exact self-onboarding table
  and column privileges under principal-scoped FORCE RLS.
- At most one pending access request exists per verified principal. Organization
  search requires three characters, and a new-organization request requires a
  normalized ten-digit business registration number that cannot be reused by
  another pending or approved request.
- Submission seals the applicant display/contact fields, profile version,
  accepted policy versions, and a deterministic SHA-256 into the request row.
  Review cannot read another principal's live profile; approval rechecks both
  the current server policy and the sealed hash.

Migration `20260730122658_verified_principal_onboarding.sql` owns this
boundary. `status`, profile/consent, bounded organization search, submission,
and rejected-request resubmission run through the dedicated identity pool with
an empty tenant context and transaction-local verified `issuer + subject`.
This keeps signup usable when the separately governed API runtime login is not
yet activated. Review listing and approve/reject decisions run through the API
pool; an unavailable API DSN therefore blocks only review with
`onboarding_review_authority_unavailable`, never memberless profile or request
submission. A decision rechecks the active tenant-admin membership or explicit
`platform_onboarding_operators` row inside PostgreSQL. New-organization
approval requires the operator to assign the applicant as the first `admin`;
an existing-organization onboarding decision cannot grant `admin`. Tenant,
active human principal, membership, compare-and-swap decision, and append-only
events commit in the same transaction. Event lineage is checked against the
terminal request row, so approval events cannot precede the decision CAS. The
current consent identifiers are server configuration
`REALYTICS_TERMS_VERSION` and `REALYTICS_PRIVACY_VERSION`; stale or forged
client versions are rejected before an acceptance timestamp is written.

An invitation binds at least:

- tenant ID;
- intended email or external identity when known;
- proposed role;
- issuer, inviter, issued time, expiry, and single-use token hash;
- accepted principal `issuer + subject`;
- acceptance and activation audit events.

The administrator UI exposes two mutually exclusive issue modes. Email-bound
invitations send only `invitedEmail`. Identity-bound invitations send only the
exact Supabase Auth HTTPS issuer ending in `/auth/v1` and the target Auth user
UUID as `invitedIssuer + invitedSubject`. Identity-bound issue must never add,
infer, or fall back to an email address, and a same-email OAuth identity is not
an account-link or tenant-membership authority. Sensitive-role invitation
issuance continues to use the existing recent-AAL2 policy in either mode.

Invitation possession does not bypass provider authentication. Expired,
revoked, already-used, tenant-mismatched, or role-escalated invitations fail
closed. It also does not bypass onboarding consent: after invitation and
identity binding checks, the same principal-scoped identity transaction locks
the applicant's onboarding profile and verifies both server-current terms and
privacy versions before any principal or membership write. A missing profile
returns `onboarding_profile_required`; stale consent returns the corresponding
`terms_version_mismatch` or `privacy_version_mismatch` with current versions.

## 6. Membership and tenant selection

Membership status is one of:

```text
pending | active | inactive | suspended | revoked
```

- Only `active` memberships authorize tenant access.
- A principal with exactly one active membership may use it by default.
- A principal with multiple active memberships must select the tenant
  explicitly.
- A requested tenant not present in the live membership directory is rejected.
- Tenant choice is request context, not a permanent mutation of identity.
- Tenant switching clears project-scoped caches and re-fetches authorization.
- Disabling or revoking membership takes effect from the database on the next
  protected request even if the JWT is still otherwise valid.

## 7. Roles

Current tenant roles are:

| Role | Intended responsibility |
|---|---|
| `admin` | Tenant operations and user management; not automatically final legal counsel |
| `reviewer` | Project/risk/assumption review and report access |
| `analyst` | Project preparation, analysis, evidence and report work |
| `viewer` | Read-only project and report access |
| `legal_reviewer` | Legal evidence and review preparation |
| `legal_counsel_approver` | Final legal approval under maker-checker rules |
| `external_counsel` | Delegated, scoped and time-bounded legal work |
| `compliance_officer` | Compliance and controlled legal operations |

Roles are coarse-grained permission bundles. Domain gates may impose stronger
requirements than a role permission. For example, a legal approver still
cannot approve their own snapshot or operate outside an active delegation.

`reviewer` is intentionally not a project creator. When its permission bundle
lacks `project:write`, the Web must keep assigned/recent work readable while
disabling new-analysis controls and file inputs. A denied create/upload request
must show the server's exact `requiredPermission`, current role, reason, and
remediation. UI visibility is convenience only; FastAPI permission checks and
PostgreSQL tenant/RLS enforcement remain authoritative.

## 8. Session and BFF contract

- Browser sessions use hardened HttpOnly, Secure-in-production, SameSite
  cookies managed by request-scoped Supabase SSR clients.
- Access and refresh tokens are not stored in `localStorage` or exposed to
  client application state.
- The Next.js BFF rejects cross-site mutations and strips caller-supplied
  authorization and REALYTICS identity headers.
- The BFF calls `getClaims()` before forwarding a bearer token to FastAPI.
- FastAPI independently verifies signature, issuer, audience, expiry, and
  not-before rules through JWKS/OIDC configuration.
- Production has no fallback to unsigned, HS256 pilot, mock OAuth, raw identity
  headers, or local REALYTICS sessions.
- Authenticated responses are non-cacheable across users and tenants.

## 9. MFA and step-up authentication

OAuth sign-in alone is not sufficient for the highest-impact actions. A recent
step-up proof is required for operations designated high risk, including:

- final legal approval or rejection;
- final PF approval where policy requires;
- final PF report publication;
- approval of waiver or terminal cure;
- assignment of `admin` or approval roles;
- delegation to external counsel;
- tenant-wide export or deletion;
- billing owner, payment method, or contract changes when billing is enabled;
- secret rotation or security-policy changes;
- linking or unlinking an external login identity;
- removing a TOTP factor.

The server must validate the signed `aal=aal2` claim and freshness. When a
strong-factor AMR event timestamp is present, it is the step-up time; token
refresh timestamps do not extend the window. A UI badge, a method label without
`aal2`, or a client-provided timestamp is not proof. Exact freshness windows
are a security policy setting and must be approved before external production.
The current controlled account-recovery boundary uses five minutes and fails
closed when Supabase supplies only string AMR methods without timestamps.

For a user who already has a verified TOTP factor, each later AAL1 session must
select one of that user's verified TOTP factors, complete a new challenge, and
receive an AAL2 session before a high-risk operation. Factor IDs are selectors,
not proof. Existing-factor reauthentication must not return the enrollment
secret, persist the one-time code, or treat a successful HTTP response as AAL2
without checking the resulting assurance level.

## 10. Maker-checker and delegation

- The creator/preparer and final approver must be different human principals
  for controlled legal and approval artifacts.
- A service principal cannot satisfy a human approval requirement.
- Tenant `admin` manages membership and operations but does not inherit
  `legal_counsel_approver` authority.
- External counsel acts only under an explicit tenant/project/scope/period
  delegation.
- Delegation creation, use, revocation, and expiry are append-only audit events.
- Revocation is rechecked at decision time.

## 11. Service principals and workers

Service identities are separate from human identities.

- They use `principal_type=service` and dedicated credentials.
- They cannot be assigned a human-facing role merely to reuse an endpoint.
- Worker credentials are scoped to required DB roles, object prefixes, queues,
  and operations.
- Jobs do not contain an end-user refresh token.
- User attribution and service execution attribution are both preserved: the
  initiating human and executing service principal are distinct audit fields.

## 12. Logout, revocation, suspension, and deletion

Logout has two layers:

1. clear the local browser session safely;
2. revoke the applicable Supabase session scope when the user or administrator
   requests server-side revocation.

For suspected compromise, offboarding, or account deletion:

1. suspend/revoke the REALYTICS principal or membership first;
2. revoke active Auth sessions;
3. rotate affected credentials and delegations;
4. preserve required audit/legal-hold records;
5. only then delete or anonymize the provider account according to retention
   policy.

Deleting an Auth user alone is not treated as immediate authorization
revocation; the live PostgreSQL principal and membership checks remain the
fail-closed control.

## 13. Authorization errors

Errors are stable, non-secret-bearing machine codes. Relevant examples include:

- `principal_not_registered`
- `principal_disabled`
- `principal_inactive`
- `tenant_membership_not_found`
- `tenant_membership_inactive`
- `no_active_tenant_membership`
- `tenant_selection_required`
- `invalid_membership_role`
- `identity_membership_authority_unavailable`
- `identity_membership_administration_authority_unavailable`
- `membership_version_conflict`
- `membership_self_change_forbidden`
- `membership_role_unchanged`
- `last_tenant_admin_role_change_forbidden`
- `target_principal_not_active`
- `target_principal_not_human`
- `onboarding_authority_unavailable`
- `onboarding_review_authority_unavailable`
- `onboarding_profile_version_conflict`
- `onboarding_policy_reacceptance_required`
- `onboarding_profile_snapshot_integrity_error`
- `terms_version_mismatch`
- `privacy_version_mismatch`
- `organization_business_registration_conflict`
- `tenant_access_request_pending`
- `tenant_access_request_version_conflict`
- `tenant_access_request_self_approval_forbidden`
- `new_organization_initial_admin_required`
- `existing_organization_admin_assignment_forbidden`
- `tenant_admin_authority_required`
- `tenant_admin_selector_required`
- `platform_operator_authority_required`
- `step_up_required`
- `maker_checker_violation`
- `delegation_missing_or_inactive`

Authentication failures return 401; authenticated but unauthorized requests
return 403; ambiguous multi-tenant selection returns a client-correctable 400;
authority-store outage returns 503 and never falls back to claims.

## 14. Required audit events

At minimum, persist:

- OAuth start, callback success/failure category, login and logout;
- session refresh failure and administrator revocation;
- principal registration, disable, enable, revoke, merge and unlink;
- invitation issue, resend, accept, expire and revoke;
- membership create, role change, status change and tenant switch;
- MFA enrollment, recovery and step-up result without storing secrets;
- protected-action denial reason;
- maker-checker and delegation decisions;
- tenant export and deletion requests.

Tokens, authorization codes, cookies, secrets, full provider payloads, and
sensitive document values are never recorded.

## 15. Production acceptance tests

1. Real Google and Kakao users complete login on the deployed canonical origin.
2. Callback allow-lists reject alternate or downgrade origins.
3. Cookies are HttpOnly, Secure, appropriately SameSite, rotated, and absent
   from browser storage.
4. Forged tenant/role headers and `user_metadata` cannot change access.
5. Missing, inactive, suspended, and revoked memberships fail closed.
6. Multiple active memberships require tenant selection.
7. Cross-tenant project, document, report, audit, and worker access is denied.
8. Expired token, JWKS rotation, logout, session revocation, and membership
   revocation are exercised.
9. Account-link collision and invitation replay are rejected.
10. MFA and maker-checker gates reject stale, same-maker, service-principal, and
    revoked-delegation attempts.
11. A reviewer can reopen assigned work but cannot create/upload a new project;
    the 403 identifies `project:write` without leaking database details.
12. A distinct AAL2 administrator can change a target membership by
    `issuer + subject`; self-change, stale CAS, inactive target, and last-admin
    demotion are rejected and every successful change has one audit event.
13. A confirmed but memberless bearer can save only the current server-owned
    consent versions and can read/write only its own onboarding rows when the
    API runtime DSN is absent but the identity DSN is healthy. Invitation
    acceptance cannot create a principal or membership until that same
    principal has a profile with both current policy versions.
14. Existing-organization approval is limited to an active administrator of
    that exact tenant; multi-admin-tenant actors must select a tenant.
15. New-organization approval is limited to an active platform operator and
    requires explicit first-admin assignment while proving atomic
    tenant/principal/membership/request/audit creation, sealed-profile policy
    integrity, business-number uniqueness, stale CAS rejection, AAL2, and
    maker-checker enforcement.
16. Hardened startup rejects an identity DSN unless it authenticates directly
    as the configured LOGIN role with NOINHERIT/NOBYPASSRLS, no parent role,
    no database/schema/relation/function ownership, schema USAGE, and no schema
    CREATE.

## 16. Legacy paths

The following are development/test compatibility only and not production
identity architecture:

- FastAPI-owned Google/Kakao OAuth callbacks;
- browser `localStorage` REALYTICS sessions;
- pilot mock OAuth identities;
- HS256 shared-secret production verification;
- raw user/tenant/role request headers;
- JWT tenant or role claims as membership authority;
- automatic personal tenant fallback.

See `docs/product/social-login-setup.md` and
`docs/product/identity-provider-integration.md` for the legacy notice. The
runtime boundary is defined by this document and
`docs/architecture/architecture-boundary.md`.
