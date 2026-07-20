-- Repair the base tenant policy created by migration 0013.
--
-- PostgreSQL combines permissive policies with OR and restrictive policies
-- with AND, but at least one permissive policy must grant a row before any
-- restrictive policy can refine it.  Migration 0013 created only restrictive
-- tenant policies, which correctly denied cross-tenant access but also denied
-- the selected tenant.  Keep 0013 immutable and replace only its named policy.

DO $realytics_core_rls_permissive_base$
DECLARE
    relation_name TEXT;
    policy_name TEXT;
BEGIN
    FOR relation_name IN
        SELECT columns.table_name
          FROM information_schema.columns AS columns
         WHERE columns.table_schema = current_schema()
           AND columns.column_name = 'tenant_id'
           AND columns.table_name NOT IN (
               'principals', 'tenants', 'tenant_memberships',
               'users', 'sessions'
           )
         GROUP BY columns.table_name
         ORDER BY columns.table_name
    LOOP
        policy_name := 'tenant_scope_' || relation_name;

        EXECUTE format(
            'ALTER TABLE %I ENABLE ROW LEVEL SECURITY',
            relation_name
        );
        EXECUTE format(
            'ALTER TABLE %I FORCE ROW LEVEL SECURITY',
            relation_name
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS %I ON %I',
            policy_name,
            relation_name
        );
        EXECUTE format(
            'CREATE POLICY %I ON %I AS PERMISSIVE '
            'USING (tenant_id = NULLIF(current_setting(''realytics.tenant_id'', true), '''')) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''realytics.tenant_id'', true), ''''))',
            policy_name,
            relation_name
        );
    END LOOP;
END;
$realytics_core_rls_permissive_base$;
