# PostgreSQL runtime authority

Updated: 2026-07-30

This is the database-specific companion to
`docs/architecture/architecture-boundary.md`. That document owns the complete
Web/Auth/API/worker/object-storage boundary; this document owns the implemented
PostgreSQL tenant, RLS, role, and pool details.

The production request path resolves `issuer + subject` against
`tenant_memberships`. Token claims and the legacy JSON identity projection are
not authorization sources. A request with more than one active membership must
select a tenant explicitly; a request with no active membership fails closed.

For normalized PostgreSQL deployments, project CRUD uses
`PostgresProjectRepository`. The repository repeats the tenant predicate in
every statement, obtains IDs from the database sequence, stamps tenant and
state version server-side, and uses compare-and-swap updates. The old
`ProjectRepository` is retained only for SQLite/JSON local compatibility.

Every runtime transaction is acquired from a role-specific bounded pool. The
API process uses `api` and `identity` pools; the isolated PF worker uses only
`pf_worker`. A transaction sets `realytics.tenant_id`, the safe search path,
and statement/lock/idle timeouts with `SET LOCAL`. Migration connections are a
separate direct-connection path.

Migration `0013_core_tenant_rls.sql` enables and forces RLS on every
tenant-bearing core relation. Migration `0014` supplies the required
permissive base policy with an exact tenant predicate and matching `WITH
CHECK`; `0015` removes older duplicate domain policies. An unset or spoofed
tenant context cannot read or write tenant data. Principals, tenants,
memberships, users, and sessions remain global identity-directory relations;
they are read by the identity pool before a tenant-scoped transaction is
opened. The live inventory contains 83 RLS-protected business relations and
four identity bootstrap relations (`tenants`, `tenant_memberships`, `users`,
and `sessions`).

The hosted Supabase project has replayed migrations through
`20260727002700_report_publication_lineage_lookup_fix.sql`; both the Supabase
deployment ledger and the REALYTICS checksum ledger contain the normalized
report migrations. The post-apply advisor reports no blocking schema/RLS
error; Supabase Auth still warns that leaked-password protection is disabled.
Migration `0017` and `supabase/roles.sql` created a credential-free
`realytics_identity` role with exact identity table grants; its required and
forbidden privilege postflight passed in the same transaction. A controlled
Google-auth rehearsal temporarily enabled LOGIN with a short-lived credential,
but this is not the durable secret-manager deployment. Live
cross-tenant and two-session `SKIP LOCKED` worker claims passed. The remaining
deployment gate is activating separate API, identity, and PF-worker LOGIN
principals through the approved secret manager, injecting their role-scoped
DSNs, and running the Python pool checkout from the deployed network.

The API and identity repositories use those bounded pools today. The PF worker
entrypoint now waits until a durable job is claimed and constructs its
orchestration repository from that job's persisted `tenantId`; repository
configuration failures become secret-safe terminal job failures. This closes
the earlier tenantless repository construction defect. It does **not** complete
the worker database cutover: legacy asynchronous-job and Agent operational
storage functions still use the direct `db.postgres.connect()` compatibility
path and do not consistently establish the `pf_worker` pool's transaction-local
tenant context. Hardened PF/Agent worker deployment remains blocked until those
claim/update paths and an exact PF-worker role postflight are migrated and live
tested. Supplying an owner or migration DSN to make the compatibility path work
is forbidden.

The API pool now runs a read-only fail-closed preflight during hardened
staging/production startup. `REALYTICS_API_DATABASE_URL` must authenticate
directly as `REALYTICS_API_DATABASE_ROLE`; an administrator or migration DSN
cannot satisfy this check. The expected role must be `LOGIN`, must not be
superuser/CREATEDB/CREATEROLE/INHERIT/REPLICATION/BYPASSRLS or a member of
another role, must have `USAGE` but not `CREATE` on `realytics` and
`extensions`, and must match the exact project table/sequence privilege
manifest. Diagnostic output contains stable codes and object names, never a
DSN, password, or raw database exception. Run before deployment:

```powershell
python services/api-server/scripts/preflight_postgres_api_runtime.py
```

The identity pool now has an equivalent checkout gate for
`REALYTICS_IDENTITY_DATABASE_URL` and
`REALYTICS_IDENTITY_DATABASE_ROLE`. It verifies the actual `current_user` and
`session_user`, requires LOGIN with NOINHERIT/NOBYPASSRLS and no parent role,
requires schema USAGE without CREATE, and rejects ownership of the database or
runtime schema relations/functions. This is deliberately narrower than the API
privilege audit because pre-membership writes are constrained by exact column
grants plus principal-scoped FORCE RLS.

The local/hosted role is still `NOLOGIN` until an approved secret-manager
workflow creates and rotates its credential. Source code and migration grants
do not constitute a successful live runtime checkout.

## Normalized PF report publication

Production report drafts are inserted into tenant-scoped
`realytics.reports`. The JSON/SQLite `ReportRepository` remains an explicit
local compatibility lane; PostgreSQL deployments do not silently fall back to
it. A report is final only when an immutable row exists in
`realytics.report_publications`. A legacy `payload_json.status = 'published'`
value has no independent authority.

Migration `20260727002612_normalized_report_publication.sql` binds every
publication to the same tenant and project as its report, approved
orchestration run, and project Snapshot through composite foreign keys. It
enables and forces tenant RLS, rejects update/delete operations, and gives the
runtime role only `SELECT, INSERT` on publications. Browser-facing Supabase
roles receive no schema or table privileges.

The first hosted smoke exposed a PostgreSQL-specific defect in the original
trigger: dynamic PL/pgSQL `EXECUTE ... INTO` does not update `FOUND`, so valid
lineage was rejected as missing. The already-applied migration was not edited.
Follow-up migration
`20260727002700_report_publication_lineage_lookup_fix.sql` replaces the lookup
checks with explicit `GET DIAGNOSTICS ... ROW_COUNT` assertions. The corrected
function is active in Supabase and its source checksum is recorded in the
REALYTICS ledger.

The repository locks the report row for the short publish transaction and
uses the tenant/project/report primary key with `ON CONFLICT DO NOTHING` as the
serialization point. The request's draft content hash is its optimistic
concurrency token. Exact retries share the first authoritative publication;
different content, run/project lineage, Snapshot hash, or decision package
fails with a conflict. Reads and export creation recompute the report content
hash and compare it with the immutable publication seal, so later mutation of
the compatibility payload cannot be presented as a published report.

The HTTP publication workflow still owns the human policy gates: approved PF
run, freshly passing Approval Readiness, exact immutable Snapshot lineage,
recent AAL2, and a reviewer distinct from the PF requester. The database
repository owns tenant isolation, lineage constraints, content integrity,
idempotency, and concurrent serialization; neither layer substitutes for the
other.

The source and generated Supabase migrations are synchronized and both report
migrations are live. Two concurrent hosted inserts converged on one immutable
publication row; maker-as-publisher and post-publication report mutation were
rejected by the database. FORCE RLS, the exact tenant policy, runtime
`SELECT/INSERT` grants, forbidden `UPDATE/DELETE`, and zero `anon`/
`authenticated` access were inspected live. A behavioral RLS pass as the
`realytics_runtime` principal still requires the dedicated direct runtime DSN:
the Supabase management SQL connection is a `BYPASSRLS` administration role
and is intentionally not permitted to assume the runtime role. Run the
opt-in disposable-schema test in
`services/api-server/tests/test_parser_v2_postgres_live.py` with an isolated
`REALYTICS_TEST_DATABASE_URL` before declaring that final role-level Gate done.
