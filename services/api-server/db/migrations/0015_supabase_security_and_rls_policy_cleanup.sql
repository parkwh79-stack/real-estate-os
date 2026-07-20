-- Supabase security-advisor cleanup after the unified tenant policy rollout.

DO $realytics_rls_auto_enable_acl_cleanup$
BEGIN
    IF pg_catalog.to_regprocedure('public.rls_auto_enable()') IS NOT NULL THEN
        EXECUTE
            'REVOKE ALL ON FUNCTION public.rls_auto_enable() '
            'FROM PUBLIC, anon, authenticated, service_role';
    END IF;
END;
$realytics_rls_auto_enable_acl_cleanup$;

-- Migrations 0007/0008 created domain-specific tenant policies. Migration
-- 0014 now supplies the same tenant predicate to every tenant-bearing table,
-- so retaining the old permissive policies only evaluates the same predicate
-- twice. Remove the named duplicates after confirming the unified policy is
-- present on the same relation.
DO $realytics_duplicate_tenant_policy_cleanup$
DECLARE
    policy_row RECORD;
BEGIN
    FOR policy_row IN
        SELECT namespace.nspname AS schema_name,
               relation.relname AS table_name,
               policy.polname AS policy_name
          FROM pg_catalog.pg_policy AS policy
          JOIN pg_catalog.pg_class AS relation
            ON relation.oid = policy.polrelid
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = relation.relnamespace
         WHERE namespace.nspname = current_schema()
           AND policy.polname IN (
               'parser_v2_tenant_isolation',
               'legal_tenant_isolation'
           )
           AND EXISTS (
               SELECT 1
                 FROM pg_catalog.pg_policy AS unified
                WHERE unified.polrelid = policy.polrelid
                  AND unified.polname =
                      'tenant_scope_' || relation.relname
           )
    LOOP
        EXECUTE format(
            'DROP POLICY %I ON %I.%I',
            policy_row.policy_name,
            policy_row.schema_name,
            policy_row.table_name
        );
    END LOOP;
END;
$realytics_duplicate_tenant_policy_cleanup$;
