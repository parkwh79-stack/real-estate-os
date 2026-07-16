# Deployment Plan

Updated: 2026-07-15

Status: **Controlled implementation; not production-ready**

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

## 2. Immutable PostgreSQL migrations

Numbered SQL files in `services/api-server/db/migrations` are the schema source of
truth. The migration runner records each filename and SHA-256 checksum in
`schema_migrations`. Checksums normalize host line endings, but otherwise an
applied migration is immutable. If a recorded checksum differs from the
repository copy, deployment fails and the change must be delivered as a new
numbered migration.

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

Set `REALYTICS_DATABASE_SCHEMA` only when the deployment uses a schema other
than `public`, and configure both migration and runtime DSNs so their first
`search_path` entry is that same schema. The commands fail before applying DDL
or grants when `current_schema()` does not match. Role/schema names are validated
as simple PostgreSQL identifiers.
The provisioner rechecks migrations, takes the migration advisory lock, rejects
an owning or privileged runtime role, revokes `CREATE` on the application schema
from `PUBLIC`, and applies grants in one transaction. Do not provide
`REALYTICS_MIGRATION_DATABASE_URL` to the serving process.

## 3. Database role separation

Provision at least two roles:

| Role | Required capability | Prohibited capability |
|---|---|---|
| Migration/deployment | create/alter schema objects, create policies, update migration ledger, grant runtime privileges | application traffic |
| Runtime application | `LOGIN`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, `NOBYPASSRLS`, and only the manifest below | Parser V2 ownership, migration DDL, schema creation, inherited broad roles |

Create the runtime role through approved DBA/IAM automation before running the
provisioner. For a passwordless/IAM-managed baseline, the role attributes are:

```sql
CREATE ROLE realytics_runtime
    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
```

Credential creation/rotation remains in the managed secret/IAM workflow and
must not be embedded in repository SQL or shell history.

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
