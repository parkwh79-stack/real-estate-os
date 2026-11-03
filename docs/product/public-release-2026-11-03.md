# Public repository and hosted deployment checkpoint

Date: 2026-11-03
Status: release preparation; not production acceptance

## Owner decisions

- Apply the complete MIT License, including commercial reuse rights.
- Keep the already public business names, figures and review results; the owner
  confirmed their disclosure is approved. This is not approval to publish new
  private uploads, credentials or personal information.
- Preserve originals, calculation history and hosted business data.
- Keep draft export available and approved-purpose publication fail-closed.

## GitHub safeguards

- Add a complete MIT license, SECURITY.md, CONTRIBUTING.md and CODEOWNERS.
- Limit the existing CI token to contents:read.
- Add weekly dependency updates for npm, pip and GitHub Actions.
- Add CodeQL for Python, JavaScript/TypeScript and Actions.
- Add full-fetched-history Gitleaks with a pinned, checksum-verified binary,
  fully redacted output and no public scan-report artifact.
- Review the five historical detections individually: two synthetic BYOK test
  inputs, two discount-stress field identifiers and one TypeScript type name.
  Exceptions use exact commit/path/rule/line fingerprints, never whole files.
- The local history scan covered 224 commits and about 17.93 MB. The reviewed
  exception rerun reported no remaining findings. This is not proof against all
  possible secret formats or external forks, artifacts and caches.
- GitHub secret scanning and push protection were already enabled. Dependency
  security updates, vulnerability alerts and private reporting were enabled in
  this execution. GitHub secret-scanning alerts returned an empty list.

## Supabase verification

The existing project is ACTIVE_HEALTHY. All 26 hosted migration versions match
the local wrappers; all 26 canonical SQL checksums match the REALYTICS ledger.
The renderer's read-only check passes. No schema redeployment, reset or migration
history rewrite is necessary for this release.

The existing naver-userinfo Edge Function is active at version 1. Its public
endpoint deliberately accepts a Naver bearer token rather than a Supabase JWT;
the handler validates identity with Naver and does not use a database admin key.
Both deployed source files match the local files after line-ending normalization.
Live negative-request smoke checks returned GET without bearer = 401 and POST =
405, both with `Cache-Control: no-store, private`. No redeployment is necessary.
Actual Naver sign-in with a user account is a separate acceptance check.

Read-only inspection found 131 application tables, 92 with enabled and forced
RLS. This count alone is not an authorization audit: global/operational tables
have separate boundaries and must not receive blanket tenant policies. No public
business-schema table or storage bucket was returned by this inspection.

The API role realytics_runtime remains NOLOGIN; identity is a separate LOGIN
role. Neither has superuser, BYPASSRLS or INHERIT. No new passwords were created.
The security advisor reports leaked-password protection disabled. Its plan and
provider configuration need review before changing it; no paid upgrade is authorized.

## Remaining delivery gates

1. Verify the exact pushed commit's CI and security jobs, then merge PR #2 and
   confirm main's checks. Do not substitute a previous green run.
2. Main protection is configured: six required checks, current-base requirement,
   conversation resolution, admin enforcement and no force-push/deletion. PR
   review count is zero for the single-owner workflow; a second human approver
   is not silently invented. Private reporting and dependency alerts are enabled.
3. Supply an approved existing API/worker hosting target and secret-management
   destination. GitHub hosts source/CI, not the long-running FastAPI process.
4. Provision the API and PF-worker LOGIN credentials through that secret path;
   never enable owner/service-role access as a shortcut.
5. Choose an object-storage implementation satisfying the existing versioning,
   retention, malware-quarantine and private-download contract. An empty Supabase
   bucket is not a replacement for that contract.
6. Complete source-event calendar/month assumptions and save/reopen/recalculate
   acceptance in the existing input-review flow. Real loan terms remain subject
   to evidence and human review.
7. Resolve or explicitly track workbook errors and source-cost discrepancies;
   do not invent values merely to unblock the approval gate.
8. Rehearse real-user login, cross-tenant denial, upload/reanalysis, worker retry,
   draft export, approved-export denial and backup/restore on the deployed stack.

See [current execution plan](realytics-os-next-work-roadmap.md) and
[deployment contract](deployment-plan.md). No full production rollout should be
claimed until the applicable live gates above pass.

## Local verification

- Public-repository and cleanup safety: 13 passed. The initial restricted-shell
  run hit Windows Git-owner/encoding errors; UTF-8 in the normal user environment
  passed without changing cleanup behavior or global Git trust.
- Supabase/auth/deployment focused API regression: 37 passed.
- Node export/Naver/context contracts: 9 passed.
- Supabase migration wrapper check and Git whitespace check: passed.
- Full API, Web types/lint/build, PostgreSQL, browser E2E and new security jobs
  must be verified on the exact uploaded commit through CI before merge.
