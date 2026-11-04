# Deployment Plan

Updated: 2026-11-04

Status: **Controlled implementation; not production-ready**

### Authentication latency check (2026-11-04)

The hosted login page responded in 0.93 seconds. Across 55 recent successful
Cloud Run `/auth/me` requests, median service time was 42 ms and the maximum was
43.73 seconds. A 43.70-second request on `realytics-api-00004-ldp` lined up with
the same instance starting 15 ms after arrival and startup probes succeeding
about 33 and 43 seconds later. These measurements cover separate endpoints.
Full interactive login and account-switch durations still need measurement.

Account switching waits for backend session cleanup and Supabase local
sign-out. Eight observed credential-cleanup DELETEs took up to 0.46 seconds
and returned errors. The timing sample cannot attribute a slow switch to
those DELETEs. Keep the revocation and workspace checks while investigating
startup with the existing scale-to-zero budget.

The password client now goes directly to the server-selected destination.
The destination independently verifies session and workspace access. The
optimized web build and 66 browser cases passed. The 56 authentication BFF
cases passed with the CI development-server configuration, which permits
loopback HTTP for the canonical-origin fixtures.

### Selected PDF page reading pilot (2026-11-04)

Cloud Build `29b0775e-2622-419d-a001-9f13f37d9bb8` used 425 audited API inputs,
including all 26 migrations. Its Linux image smoke test read Korean, English,
and a numeric value from a synthetic scan using the pinned OCR models.
Cloud Run `realytics-api-00004-ldp` is Ready at 100% traffic with image
`sha256:fa61aa6343fd4286fb192b1e3e1ecb7e49b8494b4acbb2e0e03877145cb936fb`.
Live readiness reported both PostgreSQL pools healthy. The two reading routes
are published and the anonymous page request returned 401.

Bucket lifecycle read-back confirmed seven-day expiration eligibility for
`realytics/derived/pdf-reading/`, alongside the existing age-one intake prefix
and seven-day soft delete. The [page reading contract](smart-intake.md) describes
the source checks and processing limits. Hosted authenticated upload and page
reading remain acceptance checks. Web rollout and exact-commit CI are recorded
in [PR #2](https://github.com/parkwh79-stack/real-estate-os/pull/2).

### Upload transport pilot follow-up (2026-11-04)

The Vercel request-size incident is addressed by authenticated 3 MiB upload parts,
not a body-parser or timeout increase. [Smart Intake](smart-intake.md) owns the
64 MiB transport cap, one-hour expiry, integrity, actor/project scope and retry contract.
Cloud Build `23780c5d-609f-46c1-8538-b5e5bf2db3e4` succeeded from 420 inputs with
all 26 migrations and zero forbidden secret/runtime/test paths. Downloaded source
archive hashes matched all 420 current API files. Cloud Run
`realytics-api-00003-p85` is Ready at 100% traffic with API image
`sha256:04648b517be2a897b4ce815946c0e6102694d96f343dc50f7c4e4773762e5a26`.
The image-only update retained the existing secrets, scanner and min0/max1 settings.

Bucket lifecycle read-back confirms age=1 deletion only for
`realytics/intake-transfers/quarantine/`; no previous lifecycle rules existed.
Active original paths, object-level three-day retention and seven-day soft delete
were preserved. Lifecycle is asynchronous; the rule is not proof that any object
has already been deleted. No DB migration or new IAM grant was applied.

Local production-mode PC/mobile upload regression: 26 passed. API group 67,
archive/scanner/container group 12 and final targeted 10 passed (overlapping groups).
An 18,170,390-byte real sample PDF survived a local six-part cross-instance roundtrip
with unchanged SHA-256, size and modification time. This is not a real-user hosted
upload or live GCS/ClamAV/parser rehearsal. The web deployment and complete CI for
the pushed commit are recorded in [PR #2](https://github.com/parkwh79-stack/real-estate-os/pull/2).

Current execution scope (2026-09-28): build and validate the local user workflow
in [the product execution plan](realytics-os-next-work-roadmap.md). This document
is the external-deployment reference. Its AWS, secret-store and Worker setup is
not a prerequisite for implementing local input review, analysis or draft output.
The 2026-11-03 approval below permits a narrowly scoped Cloud Run API pilot;
other paid infrastructure remains deferred. Existing access controls still apply.

### Current execution decision: Cloud Run API pilot (2026-11-03)

The owner selected a **new Google Cloud project**, with **KRW 1,000/month as a
target budget**, accepting possible small overages. This is not authorization
for unlimited expenditure, always-on workers, additional paid databases, or
production promotion. Preserve the existing Vercel Web and Supabase Auth/private
PostgreSQL authority; do not migrate to Firestore or replace membership checks.

Cost controls to verify before serving traffic:

- Request-based Cloud Run billing, minimum instances zero, maximum instances one
  as an initial pilot limit; size memory and concurrency from measured startup
  and request behavior. Instance limits are not monetary spending limits.
- Configure a Cloud Run spend cap below the monthly target, plus a project-wide
  budget alert in the billing account currency. Spend caps use gross estimated
  costs, exclude credits/discounts, and are not enforced instantaneously. They
  cover the selected service, not Cloud Build, Artifact Registry, Secret Manager,
  storage or all networking charges. Never promise a KRW 1,000 hard ceiling.
- Review build/image retention, log volume, secret versions and network costs.
  Do not create a VPC connector, always-on worker pool, load balancer, Cloud SQL
  instance, or paid AI workload as a side effect of this API deployment.
- If the cap stops the API, preserve the explicit unavailable state; do not
  silently lift the cap or grant local/claim-based access to restore the UI.

The owner selected **3 days** for pilot source-document retention on 2026-11-03.
Prepare per-object governance/unlocked retention for clean, active originals;
do not lock the bucket or use irreversible compliance retention without separate
approval. This selection does not authorize automatic deletion after day 3,
deletion of existing local/Supabase evidence, or applying a production retention
standard to historical data. Quarantine is a separate lifecycle and must not
inherit an active-original lock that prevents normal quarantine cleanup. No
automatic deletion or irreversible retention lock has been configured.

Live progress (2026-11-03): the existing identity DSN connects successfully and
passes its dedicated-role preflight. The API role has now been activated through
the approved secret workflow, after its complete grant manifest passed; its
actual direct login also passes the API preflight. Provisioned
only in the approved project: runtime service account `realytics-api`, Docker
repository `pilot-api` in `asia-northeast1`, and private bucket
`realytics-pilot-evidence-294481672291` in the same region. Read-back confirms
project number 294481672291, public-access prevention, uniform access,
versioning, and per-object retention capability. Runtime bucket IAM excludes
retention override; BYOK lifecycle is restricted to its secret-name namespace.
An initial bucket IAM write failed during role propagation; the bounded retry
succeeded after verifying ownership. No original evidence has been uploaded.

An earlier attempted credential bootstrap was blocked before execution because its
planned stdout included a SCRAM verifier. No DB password was changed and no
secret payload was created. Owner was asked to supply a deployment-only
`REALYTICS_MIGRATION_DATABASE_URL` in the existing ignored API `.env`, never in
chat, so activation and secret upload can run without outputting credentials.
That owner connection must never be deployed to Cloud Run. Do not work around
this by using the identity role for business data or weakening preflights.
The owner subsequently supplied a working deployment-only DSN in the ignored
local file. Generated a separate high-entropy API password and kept its SCRAM
verifier entirely in process; stored only API/identity DSNs as Google secrets.
The administrator credential was not uploaded. Runtime has secret-scoped access.
Image build 44af7e6c-ee03-4123-bb04-e2895514fc3c succeeded from 418 reviewed files.
A one-task/zero-retry/180-second Cloud Run job tests dependencies using the real
runtime identity, without extending that identity's privileges. Its first run
found GCS truncates retainUntilTime to milliseconds: round upward to prevent
shortened retention and false receipt failures. Corrected focused tests: 51
passed. Replacement image and complete live probe are being verified; no Vercel
API origin has yet been applied. Temporary local impersonation validation was
unsuccessful and its binding was removed; no persistent TokenCreator grant was
added. One non-business diagnostic object remains under its three-day retention.

Live rollout result (2026-11-03): Cloud Run `realytics-api-00001-9dm` is Ready at
`https://realytics-api-obozektnwa-an.a.run.app`, using image digest
`sha256:47a6a1fc667e94334ecf6627236e83112bb8414d83972e09a23f8e2c2cc463c5`.
`/health/ready` returns 200 with API/identity pools both `ok`, PostgreSQL business
and membership authority, and GCS bytes. `/auth/idp/status` confirms asymmetric
OIDC/JWKS, strict isolation and no unsigned token fallback. Anonymous `/projects`
returns 403; an invalid bearer token at `/auth/me` returns 401. Anonymous
`/auth/me` intentionally returns 200 with authenticated=false and no permissions;
HTTP 200 alone is not proof of login.

The runtime-identity dependency job passed quarantine write/read/delete,
generation+ETag-bound range reads, Unlocked retention with deletion denied, and
session secret create/read/delete. Its first run found the millisecond receipt
issue; its second correctly rejected a diagnostic secret missing schemaVersion;
the corrected diagnostic passed in execution `realytics-dependency-check-6s7g6`.
The temporary job was deleted afterward. Three tiny non-business retention
probes remain under `operational-checks/` until their three-day holds expire;
no historical/user originals were touched. ClamAV's startup TCP probe passed;
an authenticated real-upload/EICAR drill is still not established.

Web preview branch `codex/parser-v2-postgres-runtime` now has
`REALYTICS_API_ORIGIN` set to the verified API. Vercel deployment
`dpl_2KeFhL8g4TAuUT8wRendDL9an1Zw` (commit `84009e4`) is READY. At
2026-11-03 08:00:56 UTC, the stable branch URL
`https://realytics-web-git-codex-parser-v2-postgr-05604e-real-estate-os2.vercel.app`
returned `/api/auth/supabase/readiness` with `configurationReady=true`, backend
`reachable/configured/ready=true`, PostgreSQL membership authority, and both
Google/Kakao `providerReady=true`. `operational=false` correctly remains until
real-user AAL2 maker/checker acceptance; Naver live verification is also pending.
Secret scan and CodeQL passed for this commit; API/browser CI was still running
when this evidence was recorded. No real user bearer/membership or cross-tenant browser E2E completion
is claimed: browser automation failed to start in this Windows environment.
Owner must finish the existing-cookie login acceptance check in the web UI.
Scale-to-zero is retained for the KRW 1,000 target; initial startup took about
46 seconds and may exceed the current 10-second web identity request timeout.
After a cold start, a retry can be required; no always-on instance was enabled.

Validation: full local API run had 3,138 passed, 44 skipped and one fixture
setup error from Windows socket exhaustion (WinError 10055), not an assertion.
The affected report module and calculation-basis neighbors passed on rerun
(35 tests). After the timestamp fix, focused GCS/object/archive/readiness tests
passed (51 tests). Web tsc/lint, pip check and diff check passed. Hosted CI for
the final commit remains a separate gate; do not describe this as a clean
single-pass full suite or a completed production approval.

The hardened profile now also supports native GCS with generation-bound reads,
CRC32C write receipts and per-object Unlocked/GOVERNANCE retention. Active clean
originals require explicit retention; quarantine remains unlocked for cleanup.
Live bucket IAM and retention checks remain required. Do not spoof AWS configuration,
select development mode on a public server, or inject a migration/owner DSN into
the API.

Deploy the API first only after its hardened startup and dedicated API/identity
database preflights pass. Wire the preview Web/BFF after HTTPS readiness and
negative-auth checks, then require real bearer/membership and cross-tenant
browser checks before declaring the login pilot accepted or promoting production.
An API-only pilot does **not** complete PDF parsing, Pod reruns or other queued
work. Keep that limitation explicit until separately deployed workers pass
their own least-privilege and durable-job checks. Existing production promotion
and evidence/approval gates remain in force.

Build from `services/api-server`, not the monorepo root. Its `.gcloudignore`
includes the existing `.dockerignore` policy and also excludes virtualenvs,
nested runtime data and test output logs. Docker exclusions alone do not prevent
source files from reaching the remote build archive. Before every source upload,
inspect `gcloud meta list-files-for-upload` from that directory, reject any secret
or runtime artifact, and confirm required migrations/source files remain present.
This local listing is not a deployment or authorization to upload business data.

Preparation evidence (2026-11-03): official SHA256-verified Google Cloud CLI
591.0.0 installed in an isolated user-local tools/config directory; no existing
CLI configuration or global PATH changed. The actual gcloud upload inventory
contains 416 files and all 26 canonical migrations, with zero matching secret,
runtime, test, virtualenv, database or log paths. Container/upload contract tests:
4 passed. After adding the Google secret adapter the inventory is 417 files,
again with zero forbidden paths. Google account authorization subsequently
succeeded. Created only the
new `realytics-api-pilot-20261103` project (`REALYTICS API Pilot`), initially with
automatic Cloud API enablement disabled. The owner then selected the existing
KRW account named `내 결제 계정`; read-back confirms `billingEnabled=true`.
Only Billing/Budget APIs were explicitly enabled for this setup. Created a
project-scoped monthly KRW 1,000 alert budget (including credits), with
50%/80%/100% current-spend notifications to billing/project recipients. Created
a separate Cloud Run-only KRW 800 gross-estimated monthly spend cap, initially
returned as `CONFIGURED`. It excludes credits, covers service
`152E-C115-5142` only, and does not guarantee the total bill stays below KRW 1,000.
No source upload, service account, secret payload, storage or Cloud Run workload
has been created yet. No pre-existing project or account-wide budget was changed.

#### Google session credential adapter

The existing `/auth/ai-credentials`, profile replacement and session-revocation
flows now support `REALYTICS_SECRET_BACKEND=google_secret_manager`. Set an explicit
`REALYTICS_GCP_PROJECT` and `REALYTICS_GCP_SECRET_LOCATION`; managed runtime ADC
provides credentials. Never generate/download a service-account key for Cloud Run.
The adapter uses unique `realytics-user-ai-<uuid>` names, an explicit replica
region, numeric pinned versions (not `latest`), payload CRC32C, bounded requests,
and the existing user/tenant/session envelope. It rejects cross-project/static
secret references before network access. No plaintext key is written to the
database, source tree or session-memory fallback by this backend.

Application TTL remains exact (up to 24 hours); automatic cloud deletion uses a
small buffer and a minimum 120 seconds to satisfy Google's minimum expiration
window. A failed version write attempts to remove its own new secret; cloud TTL
bounds an orphan if cleanup also fails. Expired credentials never resolve even
before cloud deletion occurs. Logout/replacement explicitly revokes the stored
reference through the existing profile workflow. Missing secrets differ from
permissions/network/integrity failures, whose provider details are not exposed.

The runtime identity must receive only the reviewed create/add/access/delete
permissions for this BYOK namespace; static API/identity DSNs need separate
secret-scoped access. Do not grant project-wide Secret Manager Admin to the API.
No live IAM or secret-provider smoke test has run yet, and this adapter does not
make GCS, ClamAV, PostgreSQL roles or Workers ready. Related offline secret/auth/
container tests: 73 passed; final focused rerun: 44 passed (overlapping tests,
not additive). Full local API regression: **3,120 passed, 44 skipped** in 742.31s.
Web `tsc --noEmit`, full lint, dependency `pip check`, and `git diff --check`
passed. A focused invocation from the monorepo root failed import collection;
rerunning from the documented API working directory passed without a code fix.
The local existing PDF environment remains unchanged; pinned-dependency hosted
CI/security checks must pass on the pushed revision. Skips and mocked Google
SDK transports are not evidence of live PostgreSQL/IAM/Cloud Run acceptance.

Official references: [Cloud Run billing settings](https://docs.cloud.google.com/run/docs/configuring/billing-settings),
[spend cap scope and limitations](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps),
[pricing](https://cloud.google.com/run/pricing).

| Environment | Purpose | Infrastructure | Deployment method | Status | Priority |
|---|---|---|---|---|---|
| Local | Development and deterministic regression | SQLite/Docker Compose | Manual | In progress | P0 |
| Isolated PostgreSQL | Migration, RLS, concurrency, backfill, and recovery proof | Disposable PostgreSQL/PostGIS with separate migration/runtime roles | Manual first, then CI | Required next | P0 |
| Staging | Pilot QA and shadow parity | Managed PostgreSQL/PostGIS, object storage, worker pool | CI/CD | Planned | P1 |
| Production | Tenant-cohort rollout | Managed HA services, backups, monitoring, secret manager | Approved CI/CD | Blocked | P2 |

## 1. Deployment boundary

The default local/pilot persistence remains the existing legacy path. Parser V2
normalized PostgreSQL persistence is opt-in and must not be interpreted as an
automatic data migration or production cutover.

Core deployment components are:

- containerized FastAPI API server and Next.js web client;
- PostgreSQL/PostGIS for the shared data plane;
- S3/MinIO-compatible versioned object storage for source documents;
- the existing runtime outbox dispatcher and external workers;
- managed secrets, monitoring, backup/restore, and malware quarantine services.

Object storage, production IAM, antivirus/CDR, live backup/restore, and a complete
multi-worker incident rehearsal remain deployment blockers.

### 1.1 Current Web Preview and external API contract

The Next.js Web/BFF has an active Vercel Preview deployment connected to the
current Git branch. Preview URLs are deployment outputs and are not committed
as application authority. Documentation and environment templates use only the
following placeholders:

```text
https://<branch>-<web-project>-<vercel-team>.vercel.app
https://<approved-production-web-domain>
```

The Web deployment must set the server-only variable below to the public HTTPS
origin of the matching FastAPI environment:

```text
REALYTICS_API_ORIGIN=https://<external-fastapi-origin>
```

It must never point to `localhost`, a container-private hostname, or a database
endpoint. Browser business requests remain same-origin calls to the Next.js
BFF. Only the BFF forwards a verified bearer token to FastAPI. FastAPI sets
`REALYTICS_CORS_ORIGINS` to the exact approved Web origins; wildcard credentialed
CORS is forbidden. Supabase Auth redirect allowlists are configured separately
and do not replace this API boundary.

### 1.2 Provider-neutral container process contract

One immutable Python 3.12 image may be reused, but each process has a distinct
command and secret set:

| Process | Command | Network | Database authority |
|---|---|---|---|
| Migration task | `python scripts/migrate_postgres.py` | No public listener | deployment-only direct DSN; exits after immutable migration checks |
| API service | `uvicorn main:app --host 0.0.0.0 --port "$PORT"` | public HTTPS only through a load balancer | dedicated `api,identity` pools |
| PF review worker | `python scripts/run_pf_review_worker.py` | no public listener | implemented dedicated `pf_worker` pool, tenant-bound claims and exact privilege checkout; deployed rehearsal remains |
| Agent outbox worker | `python scripts/run_agent_platform_worker.py` | no public listener | local/test compatibility only; hardened startup blocked pending dedicated role and tenant-scoped claim/update path |
| Legal corpus worker | `python scripts/run_legal_corpus_worker.py` | egress only | dedicated legal-sync role and immutable raw-object store |
| Coding Pod worker | `python scripts/run_coding_pod_worker.py` | egress to isolated executor only | operational job authority; no executor credentials in payloads |

`/health/live` proves only that the API process answers. `/health/ready` performs
secret-safe API and identity pool checkouts and reports the configured data,
membership, and object authority. S3 and ClamAV are deliberately labelled as
configuration-only checks in that response; live IAM, bucket, scanner, backup,
and restore drills remain separate release evidence. Container health checks
use liveness; the external load balancer should use readiness.

The image runs as a non-root user and accepts platform-provided `PORT`. The
service build context excludes `.env`, credentials, private keys, runtime state,
test artifacts, and local databases. Runtime secrets are injected by the
platform and never copied into an image layer.

### 1.3 Deferred external staging proposal: AWS ECS Fargate

Keep Vercel for Web/BFF and deploy the backend first as an isolated **staging**
ECS Fargate workload. This is the lowest-boundary-mismatch option because the
approved hardened profile already requires S3, KMS-backed encryption, AWS
Secrets Manager, private worker processes, and ClamAV. Use one task definition
revision per immutable image digest and separate ECS services/tasks for the API,
PF worker, Agent worker, legal worker, and scanner. Only the API attaches to an
HTTPS Application Load Balancer; workers stay private. Prefer task IAM roles
over static `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` environment variables.

Controlled staging sequence:

1. Push a commit-SHA image after supply-chain and secret-layer inspection.
2. Run the migration task once with only `REALYTICS_MIGRATION_DATABASE_URL`.
3. Run API/identity role preflights from the deployed VPC/network.
4. Start one API task, wait for `/health/ready`, and point the Vercel Preview
   `REALYTICS_API_ORIGIN` at the staging HTTPS origin.
5. Run login, membership, cross-tenant denial, project create/read, and upload
   quarantine smoke tests.
6. Start workers only after their dedicated claim/write paths pass the same
   least-privilege and multi-worker tests.
7. Scale API tasks only after the aggregate PostgreSQL connection budget is
   updated and observed.

No cloud service creation or secret provisioning is performed by this plan.

### 1.4 Current external-deployment blockers

- Dedicated LOGIN credentials for `realytics_runtime`, `realytics_identity`,
  and the PF worker are not present in the repository and must be issued and
  rotated through the approved secret workflow.
- The PF worker implements its dedicated DSN/pool, tenant-bound claim/update,
  exact privilege manifest and fail-closed checkout. Durable LOGIN credentials,
  deployment-network checkout and a full worker rehearsal remain release
  evidence; source code and isolated test coverage do not establish them.
- Agent outbox workers still use the operational compatibility path. Their
  production/staging entrypoint fails before importing or consuming work until
  a dedicated role and tenant-scoped claim/update contract are implemented.
  Never supply an owner/migration DSN or reuse PF worker grants as a workaround.
- A real versioned/Object-Lock S3 bucket, KMS/IAM policy, private ClamAV service,
  backup/restore evidence, and scanner failure drill remain required.
- `/health/ready` does not claim live S3 or ClamAV connectivity. The deployment
  pipeline must run explicit smoke commands before promotion.
- The repository has no checked-in ECS IaC/task-definition manifest yet. ECS
  resource creation must follow an approved infrastructure change rather than
  an ad hoc console-only production setup.

## 2. Immutable PostgreSQL migrations

Numbered SQL files in `services/api-server/db/migrations` are the schema source
of truth. The migration runner records each filename and SHA-256 checksum in the
active application schema's `schema_migrations` table. On Supabase that table is
`realytics.schema_migrations`. Checksums normalize host line endings, but
otherwise an applied migration is immutable. If a recorded checksum differs
from the repository copy, deployment fails and the change must be delivered as
a new numbered migration.

The runner holds a PostgreSQL advisory transaction lock while applying pending
migrations, so concurrent deploy jobs cannot independently advance the ledger.
Parser V2 uses:

- `0006_parser_v2_platform.sql` for 19 normalized, append-only tenant tables and
  their typed relationships;
- `0007_parser_v2_rls.sql` to enable and force row-level security on all 19
  Parser V2 tenant tables.

Apply migrations from the repository root with a deployment-only DSN:

```bash
REALYTICS_MIGRATION_DATABASE_URL='<migration-role DSN>' \
  python services/api-server/scripts/migrate_postgres.py
```

On PowerShell:

```powershell
$env:REALYTICS_MIGRATION_DATABASE_URL = '<migration-role DSN>'
python services/api-server/scripts/migrate_postgres.py
```

After creating a dedicated safe runtime role, apply the version-controlled
Parser V2 privilege manifest with the same deployment DSN:

```bash
REALYTICS_MIGRATION_DATABASE_URL='<migration-role DSN>' \
REALYTICS_RUNTIME_DATABASE_ROLE='realytics_runtime' \
  python services/api-server/scripts/provision_postgres_runtime.py
```

```powershell
$env:REALYTICS_MIGRATION_DATABASE_URL = '<migration-role DSN>'
$env:REALYTICS_RUNTIME_DATABASE_ROLE = 'realytics_runtime'
python services/api-server/scripts/provision_postgres_runtime.py
```

For Supabase set `REALYTICS_DATABASE_SCHEMA=realytics` and
`REALYTICS_POSTGIS_SCHEMA=extensions`. The migration DSN uses
`search_path=realytics,extensions,public,pg_temp`; the least-privilege runtime
DSN uses `search_path=realytics,extensions,pg_temp` and does not fall through to
`public`. Both paths therefore make `current_schema()` equal to `realytics`
while resolving PostGIS from `extensions`. Explicitly placing `pg_temp` last
prevents PostgreSQL from searching the temporary schema first; `pg_catalog`
remains implicitly searched before the listed schemas. The commands fail before
applying DDL or grants when `current_schema()` does not match. Role/schema names
are validated as simple PostgreSQL identifiers.

The provisioner rechecks migrations, takes the migration advisory lock, rejects
an owning or privileged runtime role, revokes `CREATE` on the application schema
from `PUBLIC`, and applies grants in one transaction. Do not provide
`REALYTICS_MIGRATION_DATABASE_URL` to the serving process.

### 2.1 Supabase CLI bridge

Supabase deployment keeps the canonical files above and deterministically
renders wrappers under `supabase/migrations`:

```powershell
npm run supabase:migrations:render
npm run supabase:migrations:check
```

Generated wrappers are never edited directly. They normalize canonical SQL to
LF, establish the private `realytics` schema and `extensions`-hosted PostGIS,
revoke unsafe schema creation, set the deployment search path, and guard the
REALYTICS filename/SHA-256 ledger before executing source SQL. A successful
wrapper records its checksum in `realytics.schema_migrations`.

Supabase CLI independently owns `supabase_migrations.schema_migrations`.
The renderer never writes that CLI ledger, and the CLI ledger does not replace
the REALYTICS checksum ledger. This dual-ledger bridge lets `db push` control
remote ordering while application startup continues to verify the canonical
REALYTICS checksums.

The controlled remote sequence is:

```powershell
npm run supabase:migrations:check
npm run supabase:db:push:dry-run
# Review the complete SQL/version plan and obtain deployment approval.
npm run supabase:db:push
```

The npm commands pin Supabase CLI `2.109.1` via `npx` and use
`--include-roles`, so the reviewed, password-free `supabase/roles.sql` bootstrap
is included in both dry-run and apply. They do not add an unlocked package
dependency. CI runs the renderer's read-only `--check` mode and fails when
generated output is absent or stale.

Use a direct Supabase connection on port `5432` for migration when the runner
supports IPv6. An IPv4-only application network may use the Supavisor **session**
pooler on port `5432` for long-lived runtime connections. The transaction
pooler on port `6543` is outside the qualified migration/runtime contract and
must not be substituted without dedicated repository, RLS, tenant-setting, and
multi-worker tests. See [Supabase integration](./supabase-integration.md) for
the full DSN and secret-handling contract.

## 3. Database role separation

Provision at least two roles:

| Role | Required capability | Prohibited capability |
|---|---|---|
| Migration/deployment | create/alter schema objects, create policies, update migration ledger, grant runtime privileges | application traffic |
| Runtime application | Bootstrap as `NOLOGIN`; activate `LOGIN` only with a secret-manager credential; keep `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, `NOREPLICATION`, `NOBYPASSRLS`, and only the manifest below | Parser V2 ownership, migration DDL, schema creation, inherited broad roles |

Create the runtime role through approved DBA/IAM automation before running the
provisioner. A safe bootstrap identity has no credential and cannot log in:

```sql
CREATE ROLE realytics_runtime
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
```

The grant/RLS audit can run against that `NOLOGIN` role. Activate `LOGIN` and
inject its credential only in the managed secret/IAM workflow after the audit
passes. Password creation/rotation must not be embedded in repository SQL,
committed environment files, command arguments, or shell history.

The Parser V2 privilege manifest is deliberately narrower than the complete
application role manifest:

| Object | Effective runtime privileges |
|---|---|
| `schema_migrations` | `SELECT` |
| `projects`, `calculation_runs` | at least `SELECT`; provisioning is additive because other application paths manage their write grants |
| Parser base/event tables read by the repository | `SELECT`, `INSERT` |
| `parser_validation_findings`, `parser_review_decision_conflicts`, `parser_snapshot_source_runs` | `INSERT` only |
| `parser_validation_finding_events` | none until its governed runtime workflow exists |
| `runtime_outbox_events` | `SELECT`, `INSERT`, `UPDATE` |

All 19 Parser tables explicitly deny effective `UPDATE`, `DELETE`, `TRUNCATE`,
`REFERENCES`, and `TRIGGER` privileges to the runtime role. The outbox denies
`DELETE`, `TRUNCATE`, `REFERENCES`, and `TRIGGER`; its `UPDATE` is required for
lease, retry, dead-letter, and published transitions. Parser IDs are text-based,
so this subsystem needs no sequence grant. The complete API role still needs a
separately reviewed allowlist for non-Parser project, Agent Platform, and legacy
compatibility tables; never replace that review with `GRANT ... ON ALL TABLES`.

`0007` uses the transaction-local PostgreSQL setting
`realytics.tenant_id`. Its policies compare every Parser V2 row's `tenant_id` to
that setting and fail closed when the setting is missing or empty. The normalized
repository sets it before any tenant-table query, verifies the project/tenant row,
and takes a tenant/project advisory transaction lock.

`runtime_outbox_events` is intentionally outside migration 0007; its existing
tenant-aware repository and dispatcher controls still apply. The runtime role
therefore needs the minimum DML privileges required for snapshot outbox delivery,
but must not gain broad schema ownership.

## 4. Read-only runtime readiness check

The normalized Parser V2 mode performs a startup readiness probe with the runtime
DSN. It does not apply DDL. Startup fails unless:

1. `schema_migrations` exists and contains every repository migration;
2. every recorded checksum matches the packaged SQL;
3. the current runtime role is `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`,
   `NOINHERIT`, and `NOBYPASSRLS`;
4. all 19 Parser V2 tables exist with RLS enabled and forced; and
5. none of those tables is owned by the runtime role;
6. schema usage and every required table privilege are present; and
7. no forbidden Parser, migration-ledger, or outbox privilege is effective
   through direct, `PUBLIC`, or inherited grants.

This deliberately separates “schema has been deployed” from “application may
serve Parser V2 traffic.” A failed readiness probe is a deployment failure, not a
signal to grant DDL privileges to the application.

## 5. Feature flags and startup configuration

Keep normalized persistence disabled during migration and backfill. After the
isolated database passes readiness checks, the serving process may opt in with:

```text
REALYTICS_STORAGE_BACKEND=postgres
REALYTICS_DATABASE_URL=<least-privilege runtime-role DSN>
REALYTICS_PARSER_V2_PERSISTENCE=postgres_normalized
```

The supported `REALYTICS_PG_*` connection values may be used instead of
`REALYTICS_DATABASE_URL`. Unset or set
`REALYTICS_PARSER_V2_PERSISTENCE=legacy` to retain the existing Parser V2 seam.
The application rejects `postgres_normalized` when the active storage backend is
not PostgreSQL or when no PostgreSQL connection is configured.

Persistence selection and approval authorization are independent. Review,
canonical-selection, and approved-snapshot routes remain default-off until all of
the following are deliberately configured:

```text
REALYTICS_PARSER_V2_REVIEW_PILOT_ENABLED=true
REALYTICS_PARSER_V2_REVIEW_PILOT_TENANT_ALLOWLIST=<explicit tenants>
REALYTICS_PARSER_V2_REVIEW_PILOT_USER_ALLOWLIST=<explicit users>
REALYTICS_PARSER_V2_REVIEW_PILOT_REQUIRE_MFA=true
```

Strict tenant isolation, RBAC, verified OIDC/JWKS bearer identity, and the MFA
claim check must also pass. Do not enable the review pilot merely to test database
connectivity.

## 6. Atomic Parser V2 write boundary

The opt-in repository uses INSERT-only immutable replay checks and a
tenant/project transaction lock. One rollback boundary covers:

- a superseding canonical selection and required prior-selection invalidation;
- dependent snapshot invalidations caused by candidate, conflict, review, or
  selection changes;
- an approved snapshot and its selection/source-run membership rows;
- required prior-snapshot invalidation; and
- the deterministic `parser.approved_snapshot.created` outbox record.

The outbox row is created as `pending` in the same transaction and is visible to
the existing dispatcher only after commit. An insert, membership, invalidation,
or outbox failure rolls back the group. This boundary does not yet prove a whole
external worker's parsing run, seal, review package, and delivery as one live
crash-safe workflow.

## 7. Isolated-environment verification order

1. Create disposable PostgreSQL/PostGIS and distinct migration/runtime roles.
2. Run the migration command with only the migration DSN.
3. Run the runtime-role provisioner and audit its positive and forbidden grants.
4. Start the API with the runtime DSN and normalized persistence enabled; confirm
   the read-only readiness probe passes.
5. Prove a missing tenant GUC returns no rows/rejects writes and that tenant A
   cannot read, reference, or mutate tenant B records.
6. Run competing reviewer, selection supersession, snapshot supersession, and
   duplicate-idempotency tests across multiple connections/workers.
7. Inject failures at selection membership, validity-event, snapshot membership,
   and outbox writes and prove complete rollback.
8. Dispatch a committed snapshot event and observe the operational lifecycle from
   `pending` through the worker states without duplicate downstream execution.
9. Replay/backfill legacy Parser V2 data, reconcile row counts and immutable
   hashes, and run a dual-read parity report.
10. Rehearse runtime rollback to `legacy`, database restore, and migration recovery
   before any tenant cutover.

## 8. Evidence not supplied by the current implementation

The default regression suite uses deterministic fake connections. An optional
`REALYTICS_TEST_DATABASE_URL` suite creates a disposable schema and a distinct
temporary role, applies the same provisioner, and exercises migrations, RLS,
effective grants, locking, append-only denial, and snapshot/outbox rollback.
When that URL is absent the tests skip, and even a passing disposable run does
not prove production IAM membership, network failure semantics, migration
duration, production data volume, or query performance.

No legacy Parser V2 backfill, dual-write, count/hash reconciliation, or production
rollback report is included. No production tenant should be switched to
`postgres_normalized` until those artifacts, backup/restore evidence, multi-worker
results, and an explicit rollout approval exist.


## Simple signup and authenticated-request recovery (2026-11-03)

Owner direction: defer institution onboarding and let a signed-up user enter the
dashboard. The pilot exposes the server-gated default-workspace capability,
performs one idempotent verified-principal POST, verifies its tenant through the
existing BFF, and opens /dashboard. No client role/tenant selection is trusted for
provisioning. New spaces receive analyst only; existing single memberships keep
their role. Pending institution requests, profiles and consent records stay
unchanged. Anonymous/service identities, disabled principals, revoked
memberships, duplicate creation and private-workspace organization requests are
covered by negative tests. Existing organization mode remains available when
the flag is disabled; it is not the initial pilot flow.

Actual Cloud Run request logs identified a separate login failure: authenticated
/auth/me returned HTTP 500 in about 0.1 seconds, after normalized membership
resolution called storage.upsert_user -> upsert_tenant -> legacy app_state DDL.
The narrow API role correctly denied CREATE on realytics. The fix uses an
in-memory user projection for normalized authorization and removes PostgreSQL
compatibility-activity writes. Claims/SQLite compatibility and normalized
project activity append remain. No CREATE grant, owner DSN, migration or
membership-approval bypass was introduced.

Scale-to-zero stays min0/max1. Read-only bearer identity lookup now shares the
existing bounded 120-second BFF budget instead of aborting at 10 seconds; its
three login/callback/tenant functions allow 150 seconds. Cache stays no-store,
redirects are not followed and no login/invitation write retry is added. The UI
describes checking/temporary unavailability without asserting every 5xx is a
PostgreSQL outage. Existing-session retry preserves the security boundary.

Local final backend group: 117 passed. The earlier 52-check authentication group
overlaps it. Combined desktop/mobile signup, organization-mode and invitation regression:
38 passed (including 12 new simple-signup cases); cold-start identity checks:
9 passed. Web types passed. The first full lint scanned this task's generated
cache, producing warnings only; after its removal the full source lint and
final default-path type check both passed.
Hosted exact-head results are recorded with the PR. Initial UI fixture/locator errors were fixed
before passing; no unverified real-user session is claimed. Real PostgreSQL
concurrent creation, isolation, revocation and rollback cases were added to the
existing no-skip CI lane.

Cloud Build fe2613b8-7845-4014-8fbb-ff5a3f2bbed3 succeeded from 419 reviewed API
inputs, all 26 canonical migrations, zero forbidden secret/runtime/test paths.
Image digest: sha256:916d6223e3fae0c55266c76f001cd15ec563caa3750b9853b3a7d32e7efc5a5b.
The pilot manifest enables REALYTICS_SIMPLE_SIGNUP_ENABLED without changing
secrets, service identity, storage, ClamAV or scaling. Verify the actual revision
and the same Vercel branch deployment after rollout. Browser automation in this
Windows task could not attach because its MXC security environment failed;
real existing-cookie acceptance therefore needs the owner's browser check.

Verification cleanup: recorded and removed this task's 122 generated files
(101,160,077 bytes), preserving original samples, the existing development cache
and active databases. No production membership rows were altered by local tests.
