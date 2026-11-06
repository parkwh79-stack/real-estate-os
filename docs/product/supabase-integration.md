# Supabase integration

Status updated: 2026-09-28 documentation reconciliation.

Auth and migration integration code exists. The latest recorded hosted checks
are in [the 2026-09-15 runtime record](runtime-activation-progress-2026-09-15.md):
26 migration entries matched and the effective grant checks passed at that time.
The former 0001–0015 summary was an older snapshot. On 2026-09-28 the connected
project was ACTIVE_HEALTHY and its 26 migration versions/names matched the local
Supabase migration filenames. This read-only check did not revalidate SQL
checksums, effective grants or production behavior. R1 review uses the existing
project payload and requires no schema migration. API/Worker activation and deployed acceptance
remain separate from local product development; use the existing role and secret
boundaries below when that deployment is resumed.

## 1. Authentication path

```text
Browser
  -> Next.js Supabase Auth Route Handler
       email/password: /api/auth/supabase/password
       Google/Kakao PKCE: /api/auth/supabase/oauth -> /auth/callback
  -> request-scoped @supabase/ssr server client
  -> HttpOnly + Secure(production) + SameSite=Lax session cookies
  -> Next.js same-origin FastAPI BFF
  -> getClaims() signature/expiry verification
  -> getSession() access-token retrieval only after verified claims
  -> server-to-server Authorization: Bearer <token>
  -> FastAPI ES256/RS256 JWKS verification
  -> REALYTICS tenant/RBAC authorization
```

- `@supabase/ssr` is pinned to `0.12.3` and `@supabase/supabase-js` to
  `2.110.7`; the lockfile is committed.
- The browser never receives or stores a REALYTICS session ID, Supabase access
  token, or refresh token in `localStorage`/`sessionStorage` or application
  React state. Legacy `realytics.auth.session.v1` data is deleted on startup.
- Supabase password, OAuth start, PKCE code exchange, refresh, and logout all
  execute through request-scoped Next.js server clients. The BFF profile
  intentionally makes the SSR cookies HttpOnly, so browser-side Supabase Auth
  clients must not be reintroduced for session maintenance.
- OAuth redirects contain a short-lived PKCE authorization code, never a
  REALYTICS `session_id`. Google and Kakao must be enabled in Supabase Auth and
  `/auth/callback` must be in the redirect allow list.
- Production requires an explicit HTTPS `REALYTICS_WEB_ORIGIN`; OAuth start and
  callback return `503` instead of trusting a request/forwarded Host fallback
  when that canonical origin is missing or invalid.
- The browser uses only the Supabase URL and publishable key as public build
  configuration; the publishable key is consumed by server Route Handlers in
  this BFF profile.
- The publishable key never grants server administration or bypasses RLS.
- The FastAPI BFF strips caller-supplied `Authorization`,
  `X-REALYTICS-SESSION-ID`, `X-REALYTICS-USER-ID`, and tenant headers. It injects
  a bearer only after `getClaims()` succeeds. `getSession()` alone is never an
  authorization decision.
- Authenticated responses are `private, no-store`, and cross-site mutation
  requests are rejected before contacting FastAPI.
- FastAPI validates issuer, audience, expiry, algorithm, key type, and `kid`.
- JWKS is cached for ten minutes and refreshed once immediately when a rotated
  signing key is not found.
- Supabase `role=authenticated` is not treated as a REALYTICS role.
- Server-owned `app_metadata.realytics_tenant_id`,
  `app_metadata.realytics_role`, and
  `app_metadata.realytics_principal_type` are supported.
- For a self-service pilot, a user without shared-workspace metadata may receive
  an isolated tenant derived from the verified immutable Supabase `sub`.

User-controlled `user_metadata` is display-only and must never grant tenant or
role authority.

Local tenant/user/session bootstrap is an explicit development/test feature. It
requires both `NEXT_PUBLIC_REALYTICS_ENABLE_LOCAL_AUTH=true` in the Web build
and `REALYTICS_ENABLE_LOCAL_AUTH=true` on the Web/API servers, and is
unconditionally disabled in a production Next.js build. The legacy
`/auth/kakao/session?session_id=...` compatibility exchange additionally
requires `REALYTICS_ENABLE_LEGACY_OAUTH_SESSIONS=true`; it returns 404 by
default and in production. Production social login uses Supabase PKCE only.

For a shared workspace, disable
`REALYTICS_IDP_PERSONAL_TENANT_FALLBACK` and issue the following claims through a
server-managed provisioning flow or a Supabase Custom Access Token Hook:

```text
realytics_tenant_id=<workspace-id>
realytics_role=admin|reviewer|analyst|viewer
realytics_principal_type=human
```

## 2. Private database boundary

REALYTICS application objects belong in the private `realytics` schema.
PostGIS belongs in the separate `extensions` schema. `realytics` must not be
added to `api.schemas` in `supabase/config.toml`; doing so would expose it
through the Supabase Data API. The API server accesses it only through its
least-privilege PostgreSQL role.

The deployment bootstrap establishes this layout before application DDL runs:

```sql
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;
CREATE SCHEMA IF NOT EXISTS realytics;
```

Migration and runtime connections intentionally use different paths. Rendered
deployment wrappers use:

```text
search_path=realytics,extensions,public,pg_temp
```

The final `public` entry is deployment-only compatibility; `current_schema()`
remains `realytics`. The runtime connection excludes `public`:

```text
search_path=realytics,extensions,pg_temp
```

Set the application schema contract explicitly in configuration:

```dotenv
REALYTICS_DATABASE_SCHEMA=realytics
REALYTICS_POSTGIS_SCHEMA=extensions
```

For PostgreSQL URI DSNs, URL-encode the distinct `options` values:

```text
migration: ?sslmode=require&options=-csearch_path%3Drealytics%2Cextensions%2Cpublic%2Cpg_temp
runtime:   ?sslmode=require&options=-csearch_path%3Drealytics%2Cextensions%2Cpg_temp
```

Do not rely on a role's or database's ambient default `search_path`. Explicitly
listing `pg_temp` last prevents PostgreSQL from implicitly searching the
temporary schema first. `pg_catalog` remains implicitly searched before the
listed schemas, while `current_schema()` remains `realytics`. The migration
runner and runtime readiness probe intentionally fail when `current_schema()`
is not `realytics`.

## 3. Connection choice

Use the exact host and username shown by the Supabase **Connect** panel; pooler
hosts and usernames are project/region specific.

| Connection                   |   Port | Intended use                                            | REALYTICS support                                      |
| ---------------------------- | -----: | ------------------------------------------------------- | ------------------------------------------------------ |
| Direct database              | `5432` | Migrations and administrative checks                    | Preferred when the runner has IPv6 connectivity        |
| Supavisor session pooler     | `5432` | Long-lived API/worker connections on IPv4-only networks | Supported runtime fallback after live tests            |
| Supavisor transaction pooler | `6543` | Short-lived/serverless transaction workloads            | Not approved for the current runtime or migration path |

The direct host is IPv6-oriented. If the deployment network cannot reach it,
use the session pooler on port `5432` for runtime traffic and an approved
IPv6-capable runner for migrations. Do not silently substitute transaction
pooling on port `6543`: the current repository, readiness checks, tenant-local
settings, and operational workflows have not been qualified against that
connection mode.

Example shapes only:

```dotenv
# Direct migration connection (IPv6)
REALYTICS_MIGRATION_DATABASE_URL=postgresql://postgres:<encoded-password>@db.<project-ref>.supabase.co:5432/postgres?sslmode=require&options=-csearch_path%3Drealytics%2Cextensions%2Cpublic%2Cpg_temp

# Session-pooler runtime connection (copy exact host/user from Connect)
REALYTICS_DATABASE_URL=postgresql://realytics_runtime.<project-ref>:<encoded-password>@<pooler-host>:5432/postgres?sslmode=require&options=-csearch_path%3Drealytics%2Cextensions%2Cpg_temp
```

The project URL and publishable key are not database credentials. Never commit
an access token, database password, full DSN, service-role key, or generated
secret. Store deployment and runtime credentials as separate secrets in the
deployment secret manager. URL-encode passwords before placing them in a URI.

## 4. Three principals, isolated lifecycle phases

| Role                   | Lifecycle                               | Allowed                                                               | Forbidden                                                |
| ---------------------- | --------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------- |
| Migration/deployment   | CI/CD or approved operator session only | Schema/extension DDL, policy DDL, migration ledgers, grants           | Serving API or worker traffic                            |
| `realytics_runtime`    | API serving only                        | Explicit runtime grants, tenant-scoped RLS, verified legal corpus read | Ownership, DDL, `BYPASSRLS`, broad inherited roles       |
| `realytics_legal_sync` | Isolated official legal corpus workers  | Allowlisted global legal tables의 `SELECT, INSERT`                    | Runtime tenant writes, update/delete, DDL, `BYPASSRLS`   |

The runtime role may be created safely as a credential-free bootstrap identity:

```sql
CREATE ROLE realytics_runtime
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
```

This allows deterministic grants and RLS validation without putting a password
in migration SQL. `LOGIN` and its credential are activated only through the
approved secret-manager/IAM provisioning stage. Do not embed
`ALTER ROLE ... PASSWORD ...` in a migration, committed file, command argument,
or shell history.

The serving process receives only `REALYTICS_DATABASE_URL`. It must never
receive `REALYTICS_MIGRATION_DATABASE_URL`.

The official legal corpus worker receives only
`REALYTICS_LEGAL_SYNC_DATABASE_URL`. The legal repository fails closed when
this DSN is missing and never falls back to the runtime or migration DSN.
Provision its credential-free role with:

```sql
CREATE ROLE realytics_legal_sync
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
```

The committed privilege manifest grants only `SELECT, INSERT` on the
allowlisted append-only corpus, checkpoint, completion-attestation, lineage,
and reconciliation tables. It explicitly audits that `UPDATE`, `DELETE`,
`TRUNCATE`, `REFERENCES`, `TRIGGER`, and schema `CREATE` are absent.

## 5. Supabase CLI and REALYTICS dual ledger

The canonical migration sources remain:

```text
services/api-server/db/migrations/*.sql
```

Do not hand-edit generated files under `supabase/migrations`. Render them:

```powershell
npm run supabase:migrations:render
npm run supabase:migrations:check
```

The renderer normalizes source SQL to LF and creates a deterministic wrapper for
each source migration. Each wrapper:

1. bootstraps the private schema/search path contract;
2. checks the filename and SHA-256 value in
   `realytics.schema_migrations`, failing before DDL on a mismatch;
3. executes the canonical migration SQL; and
4. records the REALYTICS filename/checksum only after that SQL succeeds.

This deliberately maintains two ledgers with different owners:

- Supabase CLI manages `supabase_migrations.schema_migrations` and decides which
  rendered wrappers `db push` must run.
- REALYTICS manages `realytics.schema_migrations` and verifies the immutable
  canonical filename/checksum contract used by application readiness.

The renderer never writes the Supabase CLI ledger. The Supabase CLI never
replaces the REALYTICS checksum ledger. CI runs the renderer with `--check`;
missing, stale, or manually edited wrappers fail without modifying files.

The repository pins Supabase CLI `2.109.1` in the database push scripts through
`npx`. Both push scripts use `--include-roles`, so the reviewed,
credential-free `supabase/roles.sql` bootstrap is part of the dry-run and apply
plan. The CLI is intentionally not added as a dev dependency until the lockfile
is updated in a dedicated dependency change.

## 6. Controlled apply sequence

Run from the repository root with a clean, reviewed migration diff:

```powershell
# 1. Rebuild and verify deterministic wrappers.
npm run supabase:migrations:render
npm run supabase:migrations:check
git diff --check

# 2. Authenticate/link outside CI when required.
npx --yes supabase@2.109.1 login
npx --yes supabase@2.109.1 link --project-ref <project-ref>

# 3. Review exactly what the linked database would receive.
npm run supabase:db:push:dry-run

# 4. Apply only after approving the dry-run output.
npm run supabase:db:push
```

Do not pass a database password on a command line that will be retained in shell
history. Prefer the CLI's protected prompt locally and secret injection in CI.

After `db push`, independently verify the REALYTICS ledger and provision the
existing runtime role with the deployment DSN:

```powershell
$env:REALYTICS_DATABASE_SCHEMA = 'realytics'
$env:REALYTICS_POSTGIS_SCHEMA = 'extensions'
$env:REALYTICS_RUNTIME_DATABASE_ROLE = 'realytics_runtime'
$env:REALYTICS_LEGAL_SYNC_DATABASE_ROLE = 'realytics_legal_sync'
$env:REALYTICS_MIGRATION_DATABASE_URL = '<secret-injected migration DSN>'

python services/api-server/scripts/migrate_postgres.py
python services/api-server/scripts/provision_postgres_runtime.py
python services/api-server/scripts/provision_legal_sync_role.py

Remove-Item Env:REALYTICS_MIGRATION_DATABASE_URL
```

Only then activate the runtime credential through the secret manager and start
the API with:

```dotenv
REALYTICS_STORAGE_BACKEND=postgres
REALYTICS_PARSER_V2_PERSISTENCE=postgres_normalized
REALYTICS_DATABASE_SCHEMA=realytics
REALYTICS_POSTGIS_SCHEMA=extensions
REALYTICS_DATABASE_URL=<secret-injected least-privilege runtime DSN>
```

## 7. Required live evidence

Captured on 2026-07-20:

- PostGIS is installed in `extensions`.
- Supabase and REALYTICS migration ledgers contain 0001-0015.
- All 83 tenant-scoped business relations have enabled and forced RLS; the
  four global identity-directory bootstrap relations remain behind the
  identity boundary.
- Tenant A, tenant B, and unset-context rollback-only probes returned 1, 1,
  and 0 visible project rows respectively.
- Two concurrent `FOR UPDATE SKIP LOCKED` sessions claimed distinct jobs once;
  every smoke fixture was removed.
- Supabase security advisor returned no findings after migration 0015.
- The full API suite passed in isolated Windows process shards: 1,905 passed
  and 17 environment-gated tests skipped. Web typecheck and lint also passed.

The remote deployment is not complete until all of the following are captured:

1. PostGIS is installed in `extensions`, not `public` or `realytics`.
2. All application tables and `realytics.schema_migrations` are in `realytics`.
3. Both ledgers contain the expected migration versions; REALYTICS checksums
   match the canonical files.
4. The runtime role is non-owner, non-superuser, `NOINHERIT`, `NOREPLICATION`,
   and `NOBYPASSRLS`, with no schema `CREATE` or migration-ledger write
   privilege.
5. The legal-sync role has only the reviewed `SELECT, INSERT` manifest, cannot
   mutate existing legal rows, and connects only through
   `REALYTICS_LEGAL_SYNC_DATABASE_URL`.
6. Startup readiness passes using only the runtime DSN.
7. A missing tenant GUC fails closed, tenant A cannot read or write tenant B
   rows, and approved same-tenant operations succeed.
8. Multi-connection snapshot/outbox tests prove atomic rollback and the
   `pending -> running -> completed` worker lifecycle.
9. Legal corpus multi-worker tests prove checkpoint CAS, lease fencing,
   terminal completion attestation, and immutable raw-object reconciliation.

The detailed legal corpus procedure and evidence checklist are in
[`docs/engineering/legal-corpus-operations.md`](../engineering/legal-corpus-operations.md).

Run destructive or schema-creating integration tests only against a disposable
validation database/schema, never an active pilot or production dataset.

References:

- <https://supabase.com/docs/guides/auth/jwts>
- <https://supabase.com/docs/guides/database/connecting-to-postgres>
- <https://supabase.com/docs/guides/deployment/database-migrations>
- <https://supabase.com/docs/guides/auth/auth-hooks/custom-access-token-hook>
