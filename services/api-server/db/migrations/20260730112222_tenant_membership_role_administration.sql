-- Make tenant identity access history physically append-only.
--
-- Runtime-role grants remain deployment-specific and are owned by
-- db/postgres_runtime_privileges.py.  This migration changes only the durable
-- schema invariant: tenant-scoped audit events use FORCE RLS and cannot be
-- updated or deleted, even by a mistakenly over-granted serving role.

ALTER TABLE tenant_identity_access_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_identity_access_events FORCE ROW LEVEL SECURITY;

-- Keep the tenant setting fail-closed while letting PostgreSQL evaluate it
-- once per statement instead of once per audit row. This also satisfies the
-- Supabase auth_rls_initplan performance advisory.
DROP POLICY IF EXISTS tenant_scope_tenant_identity_access_events
    ON tenant_identity_access_events;
CREATE POLICY tenant_scope_tenant_identity_access_events
    ON tenant_identity_access_events AS PERMISSIVE
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true), ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true), ''
            )
        )
    );

CREATE OR REPLACE FUNCTION identity_reject_append_only_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $realytics_identity_append_only$
BEGIN
    RAISE EXCEPTION
        'tenant_identity_access_events is append-only; create a new event'
        USING ERRCODE = '55000';
END;
$realytics_identity_append_only$;

REVOKE ALL ON FUNCTION identity_reject_append_only_mutation() FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_tenant_identity_access_events_append_only
    ON tenant_identity_access_events;
CREATE TRIGGER trg_tenant_identity_access_events_append_only
    BEFORE UPDATE OR DELETE ON tenant_identity_access_events
    FOR EACH ROW EXECUTE FUNCTION identity_reject_append_only_mutation();

DO $realytics_membership_audit_postflight$
DECLARE
    rls_enabled BOOLEAN;
    rls_forced BOOLEAN;
BEGIN
    SELECT relrowsecurity, relforcerowsecurity
      INTO rls_enabled, rls_forced
      FROM pg_class
     WHERE oid = 'tenant_identity_access_events'::regclass;
    IF NOT COALESCE(rls_enabled, FALSE)
       OR NOT COALESCE(rls_forced, FALSE) THEN
        RAISE EXCEPTION
            'tenant identity access events must use FORCE RLS';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_policies
         WHERE schemaname = current_schema()
           AND tablename = 'tenant_identity_access_events'
           AND policyname = 'tenant_scope_tenant_identity_access_events'
    ) THEN
        RAISE EXCEPTION
            'tenant identity access event tenant policy is missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_trigger
         WHERE tgrelid = 'tenant_identity_access_events'::regclass
           AND tgname = 'trg_tenant_identity_access_events_append_only'
           AND NOT tgisinternal
    ) THEN
        RAISE EXCEPTION
            'tenant identity access event append-only trigger is missing';
    END IF;
END
$realytics_membership_audit_postflight$;

COMMENT ON FUNCTION identity_reject_append_only_mutation() IS
    'Rejects UPDATE/DELETE of tenant identity audit history.';
