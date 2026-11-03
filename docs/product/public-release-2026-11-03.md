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
2. Main protection is configured: ten required checks including the aggregate
   CodeQL alert gate, bound to their GitHub publisher apps; current-base requirement,
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

## Hosted checks and Vercel setup follow-up

Uploaded security changes: `18ffb17`, followed by the narrowly scoped historical
comment false-positive fix `1c61be4`. PR #2 is ready for review, NOT merged.
That commit's secret scan, Web quality, PostgreSQL integration, cleanup
and CodeQL analysis jobs passed. However the CodeQL aggregate gate reported
39 findings (16 high, 23 medium) requiring triage, and full API tests reported
2 failed / 3,040 passed / 46 skipped. The failures are the native PDF table
locator tests in `test_pdf_enrichment_modules.py` and
`test_pdf_structure_enrichment.py`, which returned `available_no_tables`
instead of `completed`. Do not waive findings or weaken tests just to deploy.
The CodeQL findings are static-analysis candidates, not all confirmed exploits.

Per the owner's later request, followed https://vercel.com/get-started.md:
installed global CLI 65.10.0, verified the existing account and Hobby team,
installed the official Vercel Codex plugin in user scope, and added the global
shared MCP endpoint https://mcp.vercel.com with successful OAuth. The current
session subsequently loaded its MCP tools. Documentation search, team listing,
project access and environment metadata inspection passed after the owner
approved the real-estate-os2 team access request. CLI team access also passed.
Existing MCP entries and mutation approval settings were not replaced.

The existing realytics-web project uses Next.js, apps/web, Node 22 and the main
production branch. Only Preview configuration variables are currently present;
no REALYTICS_API_ORIGIN was returned. Preview deployment succeeded, but production
environment/actual API/worker hosting and runtime LOGIN remain unresolved. No
new cloud resource, paid subscription, production promotion or DB reset occurred.

## Remediation follow-up (2026-11-03)

- Reproduced both PDF failures with PyMuPDF 2.1 in an isolated dependency target.
  The new native API forwards `**kwargs` and defaults to layout-gated table
  detection. Explicitly disable that gate for this deterministic grid adapter,
  preserving legacy signatures and exact cell locators. Pin the tested library
  version; add decimal/negative/grouped-number and forwarding-wrapper regressions.
- Restrict the VWorld proxy to fixed-origin, validated coordinates/layers/bounds,
  prohibit upstream redirects, and bound request time. Encode project link IDs
  and enforce local sidebar links.
- Reject local storage root operations, parent/ambiguous/drive paths and resolved
  symlink escape. Reuse that boundary for archive-directory access; preserve
  existing valid POSIX colon-containing components while rejecting Windows ADS.
- Do not expose raw exception strings from Pod, Gate, scenario or worker results.
  Preserve failed/blocked statuses and existing structured findings. Pilot CLI
  details require an explicit output file instead of public console logs.
- Build legacy OAuth receiver URLs from the operator-configured origin with
  encoded query data and validated relative navigation; reject encoded external
  redirects, backslashes/control characters and invalid configured origins.
  Existing production restrictions on legacy/mock sessions remain unchanged.
- Focused PDF/storage checks: 42 passed before the wrapper regression was added;
  later overlapping security/OAuth/PDF/storage checks: 59 passed. Web contracts:
  11 passed; Web types and lint passed. The first local full run caught the old
  exception-message expectation; the stronger non-disclosure assertion now
  passes. Final combined focused API regression: **137 passed**; public-repository
  and cleanup safety: **13 passed**; Git whitespace check passed.
  That slow Windows full run was stopped; exact-head hosted full API,
  PostgreSQL, browser and security checks still govern merging.
- The owner confirmed **there is no existing API/Worker server**. Do not configure
  a fictitious API origin or promote the Web as a functioning production stack.
  Continue GitHub/Preview verification; obtaining hosting approval, runtime
  secrets, private storage and a deployed end-to-end rehearsal remains separate.
  No new paid resource is authorized. No additional Supabase migration is needed
  for these code-only fixes.

CodeQL findings have not been dismissed or waived. Re-scan the exact remediation
commit before judging which findings are fixed or still require investigation.

### First hosted remediation and nested-flow follow-through

Commit `20c8d81` passed the full API job (**3,080 passed, 46 skipped**), PostgreSQL integration, Web quality
(including build), cleanup safety and secret scan. Vercel deployment
`dpl_6LP6WxyUE6FmpUaSFnYuZbKNADP9` is READY for that exact commit; it is a Preview,
not a configured production API/Worker stack. Browser E2E also passed; the full
REALYTICS CI run 41584337148 completed successfully.

CodeQL reported zero JavaScript/TypeScript and Actions findings. Python analysis
2083208407 reported 21 remaining findings: 15 filesystem flows and six exception
flows. The previous complete analysis contained 41 findings (39 Python, two
JavaScript), while the PR aggregate originally counted 39 newly introduced ones.
These are different scopes, not contradictory totals.

Follow-through changes use `realpath` normalization and a directory-separator
boundary before returning a local path; root/drive/parent restrictions remain.
Sibling-prefix and symlink-to-root tests guard against incorrect prefix checks.
This is a runtime boundary, not a custom CodeQL suppression; see the
[official path-validation guidance](https://codeql.github.com/codeql-query-help/python/py-path-injection/).
Nested workflow, term annotation, report, metrics and recalculation errors now
retain stable failure codes without publishing exception text. Focused updated
storage/failure-integrity tests: 46 passed; the broader overlapping Pod/API group:
92 passed. All hosted checks must run again for
the final follow-through commit.

### Verified follow-through and release ancestry

For `b57cca7`, CodeQL analyses 2083233742 (Python), 2083229924 (JavaScript/TypeScript)
and 2083227326 (Actions) each report **zero findings**. No findings were dismissed.
API: **3,081 passed, 46 skipped**; the separate live PostgreSQL CI lane:
**60 passed**, with skips prohibited. Web quality, cleanup safety and secret scan
passed. Vercel Preview `dpl_81zdLdQXWmhs9T3qyoiQm3eYmkxT` is READY and its root
returned HTTP 200. This does not prove authenticated API/Worker operation.

The branch was one main merge commit behind: main `2bd7bac` and its already
included parent `1f766a7` have the identical tree `9b6bbc279ad3c6955a5e6f23a7dd731b44f49c1f`.
Integrate that ancestry into the feature branch without replacing existing code;
do not merge the feature into main or promote production while hosting is absent.
Also version the corrected native PDF module and adapter as **2.0.1** so new
provenance differs from existing 2.0.0 artifacts. Historical records stay intact.
Cross-version testing found that 1.27.2.3 also uses a keyword-forwarding wrapper:
inspect the actual delegated `fitz.table.find_tables` signature before passing
`use_layout`. Legacy runtime PDF tests: **17 passed**. Isolated 2.1 PDF/storage/
failure-integrity regression: **63 passed**. This prevents the compatibility fix
from disabling table extraction in an existing development virtual environment.
Re-run all checks for this integrated head. The temporary PyMuPDF 2.1 dependency
target used for local reproduction was removed; the regular virtual environment,
original uploads and runtime databases were left untouched.
