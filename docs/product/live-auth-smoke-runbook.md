# Live Google/Kakao Auth Smoke Runbook

Updated: 2026-07-27

Status: Google PKCE and normalized membership verified; interactive TOTP,
second-user maker-checker, and Kakao scope remediation remain

## Current hosted Gate status (2026-07-27)

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

Kakao reaches the hosted Supabase callback but the provider currently returns
`invalid_scope: account_email`. Hosted GoTrue requests that scope by default,
so the Kakao app must be converted to a Biz App and its `account_email` consent
item enabled (or a future provider version must be proven to omit the scope).
Until one real round trip succeeds,
`REALYTICS_KAKAO_OAUTH_SCOPE_VERIFIED=false` keeps the Kakao button unavailable
while Google remains usable. Supabase provider enablement alone is not treated
as operating evidence.

The hosted project is **Google session-and-membership-ready; Kakao is
scope-blocked** for the destructive smoke. Both providers are enabled in
Supabase Auth, but enablement is not operational proof. Google completed a real
PKCE exchange; Kakao still needs the external consent correction described
above. The first principal and membership exist; remaining absence signals are
a verified MFA factor/session for that user and a second invited principal for
maker-checker. The operational Gate remains fail-closed until TOTP is verified
and two distinct users hold fresh AAL2 sessions.

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
- The provider callback is
  `https://iorpwxxzvryuxhraiuhs.supabase.co/auth/v1/callback`.
- The deployed Web callback URL is in the Supabase redirect allow-list.
- The test identity was admitted through a REALYTICS invitation and has an
  active PostgreSQL tenant membership.
- For AAL2 verification, the identity has a verified TOTP factor and the
  exported session was stepped up recently.
- `REALYTICS_IDENTITY_DATABASE_URL` points to the dedicated identity role, not
  the migration or API runtime role.

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

## Implementation validation (2026-07-27)

- FastAPI assurance and final-action route contract: `11 passed`.
- Web fail-closed auth/maker-checker evaluator: `3 passed` in Chromium.
- Web `check-types` and full ESLint: passed with zero warnings.
- Hosted Google real-user PKCE + normalized membership: passed for the first
  approved pilot administrator.
- Hosted TOTP AAL2: enrollment UI reached; user-entered verification is still
  pending and is not claimed as passed.
- Hosted maker-checker and Kakao: blocked by the second invited principal and
  Kakao `account_email` consent respectively.
