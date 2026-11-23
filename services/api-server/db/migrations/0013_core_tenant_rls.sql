-- Core tenant isolation for normalized PostgreSQL runtime traffic.
--
-- Migration 0012 established tenant_id and project lineage.  This migration
-- makes that column an authorization boundary: every tenant-bearing relation
-- is deny-by-default unless the transaction has explicitly set the
-- transaction-local realytics.tenant_id value.  Identity directory relations
-- remain global because they are the authority used to resolve memberships.

DO $realytics_core_rls$
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
            'CREATE POLICY %I ON %I AS RESTRICTIVE '
            'USING (tenant_id = NULLIF(current_setting(''realytics.tenant_id'', true), '''')) '
            'WITH CHECK (tenant_id = NULLIF(current_setting(''realytics.tenant_id'', true), ''''))',
            policy_name,
            relation_name
        );
    END LOOP;
END;
$realytics_core_rls$;

COMMENT ON SCHEMA realytics IS
    'Normalized REALYTICS authority. Tenant-bearing relations require transaction-local realytics.tenant_id.';
