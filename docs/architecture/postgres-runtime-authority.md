# PostgreSQL runtime authority

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

The hosted Supabase project has replayed migrations through `0015`; live
cross-tenant and two-session `SKIP LOCKED` worker claims passed. The remaining
deployment gate is provisioning separate API, identity, and PF-worker login
principals with rotated secrets, applying their least-privilege grants, and
running the Python pool checkout from the deployed network.
