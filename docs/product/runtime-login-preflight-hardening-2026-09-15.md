# Runtime login and privilege preflight

Updated: 2026-09-15

Hardened API startup now checks the complete existing normalized runtime
table/column privilege manifest, required project sequence and legal helper
execution grants, and database/schema/relation/function ownership. Missing
`calculation_runs`, `runtime_outbox_events`, `async_jobs`, or Agent ledger writes
can no longer pass merely because project CRUD works. Named-role inspection
uses a bounded number of catalogue queries for the complete table/column
surface. It checks effective privileges, including PUBLIC and column-only ACLs.
The deployment CLI checks both the migration-side inventory and the actual API
connection, so an identically named role in a different database cannot produce
a false successful preflight.

The isolated PF worker now requires `REALYTICS_PF_WORKER_DATABASE_ROLE` alongside
its dedicated URL and explicit tenant. `current_user` and `session_user` must
both be that role. LOGIN is required; SUPERUSER, CREATEDB, CREATEROLE, INHERIT,
REPLICATION, BYPASSRLS, parent role membership, schema CREATE, and ownership are
forbidden. A worker cannot reuse the configured API/identity role. Failed
preflight closes the pool and prevents the worker loop from starting.

The worker role example is `realytics_pf_worker`; this change does not create
or activate it. The API remains `realytics_runtime` and the pre-membership
identity role remains `realytics_identity`. All three must use distinct direct
logins, provisioned by the deployment's secret workflow. `SET ROLE` through a
migration connection is not a successful runtime-login checkout.

## Separate PF execution manifest

`db/postgres_pf_worker_privileges.py` defines a deterministic grant renderer and
the preflight allow/deny contract. Do not apply the API provisioner to this role.

| Surface | Worker permission |
|---|---|
| Projects, approved calculation/Deal State, existing Parser/legal evidence | Read only where the existing API has an evidence read contract |
| Async queue, Agent run/step/usage ledger, outbox | SELECT / INSERT / UPDATE |
| Orchestration and automatic risk/evidence/market/proposal artifacts | Required INSERT/upsert operations |
| Draft reports and project Snapshots | SELECT / INSERT |
| User activity audit | INSERT only |
| Parser human review/canonical/snapshot approval records | No writes |
| Legal governance approval/delegation records | No writes |
| Final report publications | No access |
| Identity, membership, invitations, onboarding, project ID sequence | No access |

PF inspection also rejects effective table or column grants on every private
schema relation outside the allowlist, including tables added after this code.
The renderer clears only the dedicated worker role's table/sequence ACLs before
reapplying its manifest; it does not revoke shared PUBLIC access. Any unsafe
PUBLIC grant therefore fails preflight and requires a separate deliberate fix.

Readiness reads use the active request/worker transaction factory with an exact
tenant match. Calculation and normalized legal evidence checks no longer open
the API pool or a direct deployment connection inside a PF worker. The current
`assumption_overrides` rows are read from normalized PostgreSQL using tenant and
project predicates, with SELECT-only grants for both API and PF roles. Expired
overrides remain ineligible; `app_state` JSON is not an authority fallback.

The renderer contains no passwords, LOGIN activation, membership grants, or
ownership transfer. A deployment administrator must first inspect role
attributes and ownership, then execute the rendered statements transactionally
and run `assert_pf_worker_runtime_connection` through the actual dedicated
worker login. The `REALYTICS_PF_WORKER_DATABASE_ROLE` value is the exact database
role name, not the Supavisor username suffix.

Runtime transaction search paths explicitly place `pg_temp` last, preserving
the migration role's temporary-object shadowing defense. Database TEMPORARY
revocation remains a separate shared-database deployment choice.

## Validation and remaining activation gate

Focused local preflight/pool/provisioner/entrypoint and readiness checks passed:
**200 tests**.
The optional disposable-PostgreSQL test additionally creates a unique temporary
LOGIN, applies only its worker renderer, connects directly using that LOGIN,
checks tenant isolation and denied approval/identity/input writes, and proves
that adding a column-only identity grant makes startup fail. It also checks
unlisted column grants, reprovisioning, and unsafe PUBLIC access. It runs only with
`REALYTICS_TEST_DATABASE_URL`, never an application DSN.

**TODO-SDD:** The existing `orchestrations` and `assumption_proposals` upserts
share status/review/payload columns with human review workflows. Table-level
worker DML alone does not enforce the distinction between an automatic draft
and a human decision inside those payloads. A database-enforced write boundary
and a full worker rehearsal under the exact role are required before activating
the worker. This preflight change does not claim that unresolved boundary is
production-ready and does not grant access to dedicated approval tables to
bypass it.

External activation still requires dedicated secrets, an actual deployed-network
checkout, real object storage/scanner/restore verification, and independent
human approval evidence. Unit tests and the disposable database rehearsal do
not substitute for those checks.

The disposable PostgreSQL CI at `e1b06e3` subsequently passed all **56 checks
with zero skips**, including the direct worker-login and privilege-drift test.
Hosted API grant alignment and the existing identity login were then verified;
the API remains NOLOGIN and no worker role was activated. See
[hosted verification](runtime-activation-progress-2026-09-15.md).
