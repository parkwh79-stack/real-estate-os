-- REALYTICS database role bootstrap for Supabase.
--
-- Credentials never belong in this file. A fresh deployment creates the
-- application role as NOLOGIN; an approved secret-management workflow may
-- later assign a password and enable LOGIN without changing this definition.

DO $realytics_runtime_role$
DECLARE
    runtime_role_oid OID;
    unsafe_attributes BOOLEAN;
    inherited_roles TEXT;
BEGIN
    SELECT oid
    INTO runtime_role_oid
    FROM pg_roles
    WHERE rolname = 'realytics_runtime';

    IF runtime_role_oid IS NULL THEN
        CREATE ROLE realytics_runtime
            NOLOGIN
            NOSUPERUSER
            NOCREATEDB
            NOCREATEROLE
            NOINHERIT
            NOREPLICATION
            NOBYPASSRLS;
        SELECT oid
        INTO runtime_role_oid
        FROM pg_roles
        WHERE rolname = 'realytics_runtime';
    END IF;

    SELECT
        rolsuper
        OR rolcreatedb
        OR rolcreaterole
        OR rolinherit
        OR rolreplication
        OR rolbypassrls
    INTO unsafe_attributes
    FROM pg_roles
    WHERE oid = runtime_role_oid;

    IF unsafe_attributes THEN
        RAISE EXCEPTION
            'realytics_runtime must be NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT, NOREPLICATION, and NOBYPASSRLS';
    END IF;

    SELECT string_agg(parent.rolname, ', ' ORDER BY parent.rolname)
    INTO inherited_roles
    FROM pg_auth_members AS membership
    JOIN pg_roles AS parent ON parent.oid = membership.roleid
    WHERE membership.member = runtime_role_oid;

    IF inherited_roles IS NOT NULL THEN
        RAISE EXCEPTION
            'realytics_runtime must not be a member of other database roles: %',
            inherited_roles;
    END IF;
END
$realytics_runtime_role$;

ALTER ROLE realytics_runtime
    -- pg_catalog is implicitly searched first. Naming pg_temp last prevents
    -- the temporary schema from being implicitly searched before realytics.
    SET search_path TO realytics, extensions, pg_temp;
