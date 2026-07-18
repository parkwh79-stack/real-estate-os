# Production Hardening Record

Updated: 2026-07-15
Implementation lane: Level 2 Controlled Implementation moving toward Level 3 Production-Critical

## 1. Purpose

This record captures the remediation of findings that could otherwise make a prototype result look like a real PF review result. It does not declare the platform production-ready.

## 2. Completed Critical Changes

1. CalculationRun source locator persistence was repaired and covered by an approved-workbook E2E test.
2. Synthetic 100-unit product, KRW 100 million revenue, and KRW 10 million Senior tranche fallbacks were removed. Missing financial inputs now stop with `insufficient_input`.
3. The PNU API no longer returns a fixed Daegu parcel on missing keys or upstream errors. It returns `sourceUnavailable` and creates no evidence.
4. Cash Flow schedules now retain component-level provenance and report eligibility. Approved workbook values outrank explicit evidence; marked estimates are last.
5. LTV is separated by denominator and dated tranche cash flows can produce an XIRR-based all-in cost.
6. Staging and production profiles fail closed unless strict security and operational dependencies are configured.
7. PF asynchronous execution uses a durable persisted queue with cancellation, retry, lease recovery, and external worker mode.
8. BYOK secrets use a dedicated ingest endpoint and non-secret `credentialRef`; inline AI secrets are rejected and logout revocation is audited.
9. Source documents use an Object Storage repository with SHA-256 verification. Remote objects support secure download and PDF address mining.
10. Consensus patches are reviewable `assumption_proposals`. Approval creates a new immutable `project_versions` row instead of mutating the authoritative project silently.
11. Project jobs, assumption approval, and source-document delivery have been split from the monolithic project router.
12. CI now checks API tests, PostgreSQL migrations, web types, zero-warning lint, production build, and desktop/mobile login E2E.
13. Orchestration reads refresh from durable storage, allowing the API and UI to observe PF Review runs completed by a separate worker process without an API restart.

## 3. Runtime Profiles

Development may use SQLite, local Object Storage, permissive compatibility identity, and an in-process controlled worker.

Staging/production require:

- `REALYTICS_RBAC_MODE=strict`
- `REALYTICS_TENANT_ISOLATION_MODE=strict`
- OIDC issuer, audience, and JWKS URL
- PostgreSQL storage
- S3 Object Storage and bucket
- AWS Secrets Manager
- external PF review worker
- explicit HTTPS CORS origins

Startup fails when any required value is missing.

## 4. Remaining Production Blockers

- Run all migrations against an isolated live PostgreSQL/PostGIS database and prove tenant-scoped multi-worker behavior.
- Deploy S3/MinIO with IAM, retention, backup, restore, malware scanning, and presigned-download policy.
- Deploy the external worker and outbox consumer with monitoring, dead-letter handling, and runbooks.
- Reconcile stale local `default`/`legacy` outbox events whose referenced AgentRun or CalculationRun no longer exists; the pilot-tenant smoke events published successfully, but orphaned historical events correctly moved to dead letter.
- Configure the real OIDC provider and verify JWKS rotation, logout, tenant ownership, and role mapping.
- Provide approved cost timing and collection-allocation evidence for report-grade Cash Flow.
- Continue extracting calculation/evidence/report routes from `projects.py` without changing API contracts.
- Resolve residual moderate npm advisories only with upstream-compatible releases and Excel workbook regression tests.

## 5. Verification Record

Focused API suites cover assumption/version persistence, identity/BYOK, durable jobs, security profiles, Object Storage, address mining, calculations, approved schedules, and project feasibility.

The local runtime smoke on 2026-07-15 covered pilot login, project selection, an archived and classified feasibility workbook, and a PF Review leased and completed by the external worker. The durable orchestration read regression found during that rehearsal is now covered by an isolated SQLite cross-process visibility test. Loopback-host development assets are explicitly allowed so the in-app browser can hydrate during smoke tests. The async job lifecycle uses `queued` → `running` → `completed`; Agent Runtime outbox delivery separately uses `pending` → `processing` → `published` (or `dead_letter`).

Web verification covers:

- Next.js route type generation
- TypeScript
- zero-warning ESLint
- production build
- Playwright desktop Chromium login
- Playwright mobile Chromium login

Live PostgreSQL, S3, external worker, and organization IdP verification remain environment-dependent and are not represented as complete.
