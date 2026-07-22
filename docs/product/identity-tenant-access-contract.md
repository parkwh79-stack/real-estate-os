# Identity, Tenant, and Access Contract

Updated: 2026-07-22
Status: Approved policy contract; controlled implementation present, deployed provider rehearsal blocked
Production identity provider: Supabase Auth with Google and Kakao

Implementation note (2026-07-22): the BFF now exposes secret-safe provider
readiness, explicit Google/Kakao identity linking, TOTP management, scoped
session revocation, and a short-lived HttpOnly invitation intent. Migration
`0016_identity_invitation_lifecycle.sql` and the FastAPI repository implement
hash-only invitation issue/revoke/accept with replay, expiry, binding, role and
membership checks. Hardened high-risk actions require recent AAL2. Migration
`0016` is live and both migration ledgers match. The credential-free
`realytics_identity` role and its exact grant audit are live through migration
`0017`, with matching ledgers and no pending migration. The role remains
NOLOGIN until an approved secret-manager workflow activates its credential and
injects the Identity DSN. The live Supabase settings check currently reports
both Google and Kakao disabled; this is not yet an operational sign-off.

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

An authenticated person is not automatically a tenant member, project reader,
reviewer, legal approver, or paying customer.

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

### 4.1 Google and Kakao

Google and Kakao are authentication methods behind Supabase Auth. REALYTICS does
not store provider passwords or reproduce their OAuth token exchange in
FastAPI.

### 4.2 No automatic email merge

Two identities must not be merged only because their emails match. An explicit
link operation requires:

1. authentication of the currently signed-in identity;
2. proof of control of the identity being linked;
3. collision check against existing principals and memberships;
4. confirmation of the surviving canonical principal;
5. append-only link audit event;
6. a reversible administrative recovery path.

Until the link succeeds, the identities remain separate. Membership is never
copied from one identity to another based only on email.

### 4.3 Provider unlink and last identity

The last usable sign-in identity cannot be removed without establishing a
replacement recovery path. Provider unlink, account recovery, and suspicious
sign-in handling must be auditable.

## 5. Tenant onboarding

Production onboarding is invite-first or administrator-approved. Personal
tenant fallback and just-in-time tenant creation are disabled.

Recommended lifecycle:

```text
invitation issued
  -> authenticated provider identity presented
  -> invitation scope and expiry verified
  -> principal registered or matched by iss + sub
  -> membership pending
  -> administrator/reviewer activation where required
  -> membership active
```

An invitation binds at least:

- tenant ID;
- intended email or external identity when known;
- proposed role;
- issuer, inviter, issued time, expiry, and single-use token hash;
- accepted principal `issuer + subject`;
- acceptance and activation audit events.

Invitation possession does not bypass provider authentication. Expired,
revoked, already-used, tenant-mismatched, or role-escalated invitations fail
closed.

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
- approval of waiver or terminal cure;
- assignment of `admin` or approval roles;
- delegation to external counsel;
- tenant-wide export or deletion;
- billing owner, payment method, or contract changes when billing is enabled;
- secret rotation or security-policy changes.

The server must validate the assurance level and freshness. A UI badge or a
client-provided timestamp is not proof. Exact freshness windows are a security
policy setting and must be approved before external production.

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
