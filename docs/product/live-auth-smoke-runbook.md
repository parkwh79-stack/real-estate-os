# Live Google/Kakao Auth Smoke Runbook

Updated: 2026-07-30

Status: Google PKCE, normalized membership, and TOTP AAL2 verified; Kakao PKCE
and normalized membership verified; a second independent AAL2 checker,
hardened PostgreSQL API runtime, cross-device revocation proof, and dedicated
maker-checker rehearsal remain

## Current hosted Gate status (2026-07-28)

### OAuth callback correction and provider-specific status

The first real Google round trip created the hosted Supabase identity, but the
local PKCE exchange initially failed because the browser started on
`localhost` while `REALYTICS_WEB_ORIGIN` pointed at `127.0.0.1`. The Web now
uses `http://localhost:3000` consistently and redirects an alternate Host to
the canonical origin before issuing any host-only PKCE or invitation cookie.
The follow-up Google round trip completed the PKCE exchange. An explicitly
approved first-admin bootstrap then created the exact issuer/subject principal,
the active `realytics-pilot` tenant membership, and its audit event. A fresh
Google round trip now reaches `/start`; `/settings` confirms the normalized
tenant and `admin` role returned by FastAPI. Email is display metadata only and
is not used to join or authorize the account.

The Kakao flow now requires both sides of the email-optional contract. GoTrue
appends `options.scopes` to its Kakao defaults, so the Web OAuth start instead
uses a server-controlled provider query override:
`scope=profile_nickname profile_image`. This removes `account_email` from the
generated Kakao authorization URL. The hosted provider is enabled with
`external_kakao_email_optional=true`, and that value was read back from the
Management API. A Kakao Biz App and `account_email` scope are therefore not a
login prerequisite. The Web runtime attests the deployment-specific hosted
readback with `REALYTICS_KAKAO_EMAIL_OPTIONAL_VERIFIED=true`; provider
enablement or email-optional configuration without the server-controlled
scope override is insufficient. Email remains optional profile metadata. The
verified JWT `iss + sub`, explicit invitation, and normalized PostgreSQL
membership remain authoritative for identity and tenant access.

The hosted project is **session-and-membership-proven for Google and Kakao**.
Google additionally has one verified TOTP factor and one active AAL2 session.
The scoped sign-out request preserved that current AAL2 session, but no second
active session existed at the time, so actual cross-device invalidation is not
yet claimed. Kakao was admitted through an explicit identity-bound invitation
and now resolves to an active normalized PostgreSQL membership.

The Kakao pilot identity intentionally has no provider email because the OAuth
scope excludes `account_email`. Hosted GoTrue cannot currently enroll TOTP for
an Auth user without an account name derived from a verified email. REALYTICS
therefore rejects that enrollment before calling GoTrue and reports
`verified_email_required_for_totp`. The system must not fabricate an email,
write `auth.users` directly, or restore automatic same-email linking merely to
pass this Gate.

The operational maker-checker Gate remains fail-closed until a second human
checker has a distinct `iss + sub`, an explicit membership, a unique verified
contact email suitable for TOTP enrollment, and a fresh AAL2 session. The
destructive rehearsal also requires a dedicated completed PF run and draft
report whose lineage/readiness checks pass. One operator controlling two OAuth
identities is not accepted as independent maker-checker evidence.

The current local API process has the dedicated Identity DSN, but it is not yet
an operating rehearsal for final approval: high-risk step-up enforcement, the
900-second freshness limit, PostgreSQL storage mode, and the authoritative API
runtime DSN are not configured in its local environment. Configure all four
and restart the API before capturing maker/checker states or mutating a PF run
or report.

Before starting that API, run the dedicated API database preflight with
`REALYTICS_MIGRATION_DATABASE_URL`, `REALYTICS_API_DATABASE_URL`, and
`REALYTICS_API_DATABASE_ROLE` supplied by the approved secret store:

```powershell
python services/api-server/scripts/preflight_postgres_api_runtime.py
```

An `ok=false` result blocks the rehearsal. In particular, `role_nologin`,
`wrong_login_role`, `unsafe_role_attributes`, missing project/sequence grants,
or forbidden grants must not be waived by substituting the Supabase management
connection. After startup, sign in as the intended provider identity, verify
`/auth/me` returns the PostgreSQL role/permissions, then verify a reviewer sees
assigned work but receives a structured 403 with
`requiredPermission=project:write` for new project creation.

For an administrator role correction, use Settings with a distinct AAL2
administrator. Select the exact target `issuer + subject`, record the reason,
and confirm the refreshed membership version and append-only audit event. Never
change the signed-in administrator's own role or promote a Kakao/Google account
by matching email.

The current identity database rehearsal uses the dedicated
`realytics_identity` role through the Supavisor session pooler. Its credential
is intentionally short-lived and held only by the running API process. Replace
it with an approved secret-manager reference and rotate it before treating the
runtime connection as durable production infrastructure.

The readiness endpoint proves provider and backend-boundary configuration. The
authenticated security endpoint additionally reports
`operationalForHighRiskActions`; it is `true` only when the requested Google or
Kakao identity is linked, a verified MFA factor exists, and the current signed
Supabase session is `aal2`. FastAPI independently rechecks the verified JWT
`aal` claim and recent strong-factor timestamp. UI state, `user_metadata`, and
the presence of an MFA method label do not satisfy final approval or publish.

## Purpose

This runbook verifies the deployed authentication boundary without storing a
Google/Kakao password, OAuth code, refresh token, or Supabase session in Git.
The operator completes the real provider login interactively and exports a
short-lived Playwright storage state into the ignored `apps/web/.auth/`
directory. The automated portion then verifies provider readiness, the signed
session, PostgreSQL membership authority, tenant selection, linked identity,
MFA state, and optionally other-session revocation.

## Preconditions

- The selected provider is enabled in the hosted Supabase project.
- A production-grade Custom SMTP provider is enabled in Supabase Auth and its
  sender domain is verified. The built-in provider is not an acceptable pilot
  prerequisite: its delivery scope and rate limit are intentionally restricted
  and cannot prove delivery to customer addresses. Store SMTP credentials only
  in Supabase/Vault or the approved secret manager; never put them in Git,
  screenshots, test fixtures, or this runbook.
- Kakao OAuth start fixes the provider query to
  `scope=profile_nickname profile_image`, the resulting Kakao URL contains no
  `account_email`, and hosted Kakao Auth has
  `external_kakao_email_optional=true` verified before the Web runtime sets
  `REALYTICS_KAKAO_EMAIL_OPTIONAL_VERIFIED=true`.
- The provider callback is
  `https://iorpwxxzvryuxhraiuhs.supabase.co/auth/v1/callback`.
- The deployed Web callback URL is in the Supabase redirect allow-list.
- The test identity was admitted through a REALYTICS invitation and has an
  active PostgreSQL tenant membership.
- For AAL2 verification, the identity has a verified TOTP factor and the
  exported session was stepped up recently.
- `REALYTICS_IDENTITY_DATABASE_URL` points to the dedicated identity role, not
  the migration or API runtime role.

## Email signup, resend, and recovery smoke

Use two dedicated pilot inboxes: one address that has never been registered and
one pending, unconfirmed signup. Do not use an executive's or production
operator's primary account.

1. Submit email signup with the current displayed terms and privacy versions.
   Confirm the BFF receives `termsAccepted=true`, `privacyAccepted=true`, and
   both exact version identifiers. A missing consent must return
   `signup_policy_consent_required`;
   a stale version must return `signup_policy_version_mismatch` before Supabase
   is contacted.
2. Confirm the UI enters the privacy-preserving confirmation state. It must not
   say that the account was created or that delivery definitely succeeded.
3. Wait for the displayed cooldown, select **확인 메일 다시 보내기**, and
   confirm one additional signup confirmation message arrives. The callback
   must return to the canonical Web origin and then `/onboarding` through the
   existing SSR/PKCE exchange.
4. Request password recovery once for each pilot inbox. Both browser responses
   must use the same accepted envelope (`accountState=undisclosed`,
   `deliveryState=unconfirmed`). A caller must not be able to infer which inbox
   has an account from status, message, timing target, or response shape.
5. Open the recovery link once, set a policy-compliant password, and confirm the
   recovery session is locally signed out. Reusing the link must fail closed.

The public error contract exposes only account-neutral operational states:

- `auth_email_rate_limited`: caller/project request limit; retry after the
  displayed cooldown. A per-address recovery/resend limit remains hidden behind
  the accepted envelope because exposing it could enable account enumeration.
- `auth_email_delivery_not_configured`: email/password signup or Custom SMTP is
  disabled, or the built-in SMTP does not authorize the recipient. This blocks
  the pilot Gate.
- `auth_email_delivery_unavailable`: SMTP/Auth provider timeout or temporary
  outage. Retry once after the cooldown; repeated failure blocks the Gate.
- `auth_email_address_invalid`: syntactically/provider-invalid recipient; fix
  the input without echoing provider details.

For diagnosis, inspect Supabase **Auth logs** and the Vercel/Next.js BFF logs at
the same timestamp. REALYTICS logs only operation, normalized category,
provider error code, and HTTP status. They must not contain the email address,
password, confirmation token, PKCE verifier, access/refresh token, SMTP host
credential, or raw provider error message. Expected provider signals include
`over_request_rate_limit`, `over_email_send_rate_limit`,
`email_address_not_authorized`, `email_provider_disabled`, `request_timeout`,
and `unexpected_failure`. Record the normalized category and remediation only.

## Capture a short-lived signed browser state

From the repository root, open the deployed login page in Playwright codegen:

```powershell
npx playwright codegen --save-storage=apps/web/.auth/live-auth.json https://YOUR-WEB-ORIGIN/login
```

Complete Google or Kakao login in the opened browser, select the intended
tenant, perform TOTP verification when required, confirm that `/settings`
loads, and close the codegen browser. Treat the generated JSON as a secret. It
must remain under the ignored `.auth` directory and must be deleted after the
smoke.

## Run the non-destructive smoke

```powershell
$env:REALYTICS_LIVE_AUTH_SMOKE='1'
$env:REALYTICS_E2E_BASE_URL='https://YOUR-WEB-ORIGIN'
$env:REALYTICS_LIVE_AUTH_STORAGE_STATE='apps/web/.auth/live-auth.json'
$env:REALYTICS_LIVE_AUTH_PROVIDER='google'
$env:REALYTICS_LIVE_AUTH_REQUIRE_AAL2='1'
npm --workspace web run test:e2e:auth-live
```

Use `kakao` for `REALYTICS_LIVE_AUTH_PROVIDER` when testing Kakao. Run the
complete smoke once for each provider.

When `REALYTICS_LIVE_AUTH_SMOKE=1`, a missing storage state, provider selector,
linked identity, verified MFA factor, or AAL2 session is a test failure. It is
not silently converted to a passing skip. The entire live suite remains ignored
only when live execution was not explicitly requested.

## Destructive AAL2 maker-checker approval/publication smoke

This Gate needs two distinct invited users in the same normalized PostgreSQL
tenant. Prepare an unapproved, completed PF Review run whose `requested_by`
matches the maker, and a draft orchestration-bound report whose Approval
Readiness and immutable snapshot lineage pass. Capture one fresh AAL2 browser
state per user; never copy one user's state to both files.

```powershell
npx playwright codegen --save-storage=apps/web/.auth/live-maker.json https://YOUR-WEB-ORIGIN/login
npx playwright codegen --save-storage=apps/web/.auth/live-checker.json https://YOUR-WEB-ORIGIN/login
```

The recommended rehearsal uses Google for one user and Kakao for the other so
both production providers are exercised, although the security property is the
distinct normalized principal, not provider diversity.

```powershell
$env:REALYTICS_LIVE_AUTH_SMOKE='1'
$env:REALYTICS_E2E_BASE_URL='https://YOUR-WEB-ORIGIN'
$env:REALYTICS_LIVE_AUTH_MAKER_CHECKER='1'
$env:REALYTICS_LIVE_AUTH_EXECUTE_FINAL_ACTIONS='1'
$env:REALYTICS_LIVE_AUTH_MAKER_STORAGE_STATE='apps/web/.auth/live-maker.json'
$env:REALYTICS_LIVE_AUTH_CHECKER_STORAGE_STATE='apps/web/.auth/live-checker.json'
$env:REALYTICS_LIVE_AUTH_MAKER_PROVIDER='google'
$env:REALYTICS_LIVE_AUTH_CHECKER_PROVIDER='kakao'
$env:REALYTICS_LIVE_PF_PROJECT_ID='PROJECT-ID'
$env:REALYTICS_LIVE_PF_RUN_ID='RUN-ID'
$env:REALYTICS_LIVE_PF_REPORT_ID='REPORT-ID'
npm --workspace web run test:e2e:auth-live
```

The harness fails before mutation unless both users are active, AAL2, linked to
the declared provider, members of the same tenant, and different principals;
it also confirms that the run records the maker. It then proves the maker's
approval and publication attempts are rejected, the checker can approve, and
the same checker can publish the immutable report. Because the last two calls change authoritative
workflow state, use a dedicated rehearsal run/report and retain only IDs,
hashes, timestamps, and pass/fail as audit evidence.

## Explicit other-session revocation

This step mutates session state and is deliberately disabled by default. Sign
in to a second browser/device first, then add:

```powershell
$env:REALYTICS_LIVE_AUTH_REVOKE_OTHERS='1'
npm --workspace web run test:e2e:auth-live
```

Confirm that the second browser is rejected or forced to refresh its expired
session while the current browser remains authenticated.

## Cleanup and evidence

1. Delete every captured state (`live-auth.json`, `live-maker.json`, and
   `live-checker.json`).
2. Revoke the temporary test invitation if it remains pending.
3. Record only provider, tenant, test timestamp, test commit, AAL result, and
   pass/fail. Never attach cookies, tokens, QR secrets, or the storage state.
4. A passing harness is operating evidence for the tested environment only;
   it does not replace periodic JWKS rotation, tenant RLS, or role audits.

## Implementation validation (2026-07-28)

- FastAPI assurance and final-action route contract: `11 passed`.
- Web fail-closed auth/maker-checker evaluator: `3 passed` in Chromium.
- Web `check-types` and full ESLint: passed with zero warnings.
- Hosted Google real-user PKCE + normalized membership: passed for the first
  approved pilot administrator.
- Hosted Google TOTP AAL2: one verified factor and one active AAL2 session;
  passed. The scoped other-session sign-out retained the current session, but
  no second session existed, so cross-device revocation remains unproven.
- Hosted Kakao configuration: server-controlled
  `scope=profile_nickname profile_image`, provider enablement, and
  email-optional setting verified. Real Kakao PKCE, invitation acceptance, and
  normalized active membership passed.
- Hosted Kakao TOTP AAL2: correctly blocked before GoTrue because the pilot
  Auth identity has no unique verified email/account name.
- Hosted maker-checker: blocked by the second independent human checker, that
  checker's fresh AAL2 session, hardened PostgreSQL API runtime configuration,
  and dedicated rehearsal run/report IDs.
