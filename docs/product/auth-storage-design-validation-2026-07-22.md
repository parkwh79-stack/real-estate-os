# Auth, Storage, and Design Foundation Validation — 2026-07-22

## Scope

This record covers the implementation sequence approved for the next coding
stage: Google/Kakao operating readiness, invitation/account-link/MFA/session
lifecycle, source-object quarantine/scan/retention, and the first shared design
system conversion.

## Implemented

- Secret-safe BFF readiness for Supabase Auth settings and FastAPI OIDC/JWKS /
  PostgreSQL membership authority.
- Explicit Google/Kakao identity link and unlink; last identity removal is
  blocked.
- TOTP enroll, verify, list and remove; scoped local/global/other-session
  sign-out.
- Hash-only invitation issue/list/revoke/accept with tenant RLS, append-only
  events, row locks, expiry/replay checks, identity/email binding, and
  server-owned role/membership creation.
- Invitation RLS uses a permissive tenant-equality base policy with FORCE RLS;
  unset or mismatched tenant context still returns no rows.
- Invitation OAuth intent stored only in a short-lived HttpOnly cookie; the
  token is not embedded in the provider callback URL.
- Hardened high-risk AAL2 step-up for final PF approval and controlled legal,
  waiver, cure, counsel and delegation decisions.
- Tenant/project source-object quarantine, SHA-256 check, ClamAV boundary,
  clean-only active promotion, and hardened S3 encryption/Object Lock gates.
- Semantic CSS/Tailwind tokens, controlled shared Button/Card, a financial
  metric primitive, and five independent status-axis components.

## Automated evidence

The following focused checks passed in the developer workspace:

```text
Python identity/storage/auth boundary tests: 57 passed in 20.98s
Python project/orchestration/legal approval tests: 124 passed in 866.21s
Post-fix invitation RLS/repository/router recheck: 10 passed in 19.38s
Identity role/grant and invitation regression: 35 passed in 19.88s
Follow-up API identity/invitation regression: 32 passed in 42.84s
Playwright Auth BFF + invitation UI desktop/mobile: 20 passed
Live Auth harness without storage state: 2 explicitly skipped, exit 0
Web/package type check: passed
Web/package lint: passed
Supabase migration wrapper parity check: passed
```

The affected Python regression total is 181 passed. A harmless Windows pytest
temporary-directory cleanup warning appeared after each suite had already
returned a successful pytest exit result; it does not change the test result.

## Live read-only readiness result

No secret values were returned or logged.

| Check | Result |
|---|---|
| Supabase project settings reachable outside the restricted test sandbox | yes |
| Google provider enabled | no |
| Kakao provider enabled | no |
| FastAPI OIDC mode | enabled |
| FastAPI JWKS configured | yes |
| Tenant isolation | strict |
| Membership authority after local hardening | PostgreSQL |
| Canonical local Web origin | configured |
| Supabase migration ledger | local/remote 0001-0017 match; pending none |
| Identity role grants | live; self-audit passed; role remains NOLOGIN |
| Role-scoped PostgreSQL runtime DSNs in local secret environment | missing |
| Real Google/Kakao callback/refresh/logout smoke | blocked |

Provider enablement requires the approved Google/Kakao client credentials and
exact Supabase callback registration. A publishable key cannot configure those
providers. PostgreSQL membership verification also requires the dedicated API
and identity DSNs; the application deliberately does not fall back to claims.

## Not deployed or not yet complete

- Secret-manager LOGIN credential activation and the dedicated Identity DSN.
  Migrations `0016` and `0017` are live; `realytics_identity` remains NOLOGIN.
- Real Google/Kakao accounts, callback allow-list, refresh, global revocation,
  provider collision/recovery and TOTP step-up rehearsal.
- Invitation email transport and administrative recovery workflow. The
  issue/list/revoke administrator UI is implemented.
- Live S3/MinIO bucket, IAM prefix denial, versioning, Object Lock, ClamAV,
  backup/restore, legal hold, rejected-object ledger, signed download and
  deletion receipt drills.
- Workflow-wide replacement of generic status pills and page-local colors,
  followed by accessibility and visual regression.

These items remain release gates; the controlled implementation is not a
production completion claim.
