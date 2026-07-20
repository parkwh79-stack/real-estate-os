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

Migration `0013_core_tenant_rls.sql` enables and forces a restrictive policy
on every tenant-bearing core relation. The policy compares `tenant_id` with
the transaction-local setting and has a matching `WITH CHECK`, so an unset or
spoofed tenant context cannot read or write tenant data. Principals, tenants,
memberships, users, and sessions remain global identity-directory relations;
they are read by the identity pool before a tenant-scoped transaction is
opened.

The remaining release gate is live execution: apply the authoritative
migrations and generated Supabase wrappers to an isolated PostgreSQL/PostGIS
database, provision least-privilege role grants, then run cross-tenant,
multi-worker, revocation, and pool-budget smoke tests.
