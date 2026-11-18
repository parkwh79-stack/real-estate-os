-- Least-privilege identity runtime role.
--
-- The role is created as NOLOGIN by supabase/roles.sql before this migration.
-- Credentials and LOGIN are deliberately outside Git and are activated only
-- by the approved secret-manager workflow after this grant audit succeeds.

DO $realytics_identity_role_preflight$
DECLARE
    unsafe_attributes BOOLEAN;
    inherited_roles TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'realytics_identity'
    ) THEN
        RAISE EXCEPTION
            'realytics_identity role must be created before migration 0017';
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
     WHERE rolname = 'realytics_identity';

    IF unsafe_attributes THEN
        RAISE EXCEPTION 'realytics_identity has unsafe role attributes';
    END IF;

    SELECT string_agg(parent.rolname, ', ' ORDER BY parent.rolname)
      INTO inherited_roles
      FROM pg_auth_members AS membership
      JOIN pg_roles AS member ON member.oid = membership.member
      JOIN pg_roles AS parent ON parent.oid = membership.roleid
     WHERE member.rolname = 'realytics_identity';

    IF inherited_roles IS NOT NULL THEN
        RAISE EXCEPTION
            'realytics_identity must not inherit database roles: %',
            inherited_roles;
    END IF;
END
$realytics_identity_role_preflight$;

REVOKE CREATE ON SCHEMA realytics FROM realytics_identity;
REVOKE ALL PRIVILEGES ON SCHEMA realytics FROM realytics_identity;
GRANT USAGE ON SCHEMA realytics TO realytics_identity;

REVOKE ALL PRIVILEGES ON TABLE realytics.principals
    FROM realytics_identity;
REVOKE ALL PRIVILEGES ON TABLE realytics.tenant_memberships
    FROM realytics_identity;
REVOKE ALL PRIVILEGES ON TABLE realytics.tenant_invitations
    FROM realytics_identity;
REVOKE ALL PRIVILEGES ON TABLE realytics.identity_invitation_token_index
    FROM realytics_identity;
REVOKE ALL PRIVILEGES ON TABLE realytics.tenant_identity_access_events
    FROM realytics_identity;

GRANT SELECT, INSERT ON TABLE realytics.principals
    TO realytics_identity;
GRANT UPDATE (
    email, display_name, last_verified_at, updated_at
) ON TABLE realytics.principals TO realytics_identity;

GRANT SELECT, INSERT ON TABLE realytics.tenant_memberships
    TO realytics_identity;
GRANT SELECT, INSERT, UPDATE ON TABLE realytics.tenant_invitations
    TO realytics_identity;
GRANT SELECT, INSERT, UPDATE
    ON TABLE realytics.identity_invitation_token_index
    TO realytics_identity;
GRANT SELECT, INSERT ON TABLE realytics.tenant_identity_access_events
    TO realytics_identity;

DO $realytics_identity_role_postflight$
DECLARE
    forbidden_privilege TEXT;
BEGIN
    IF NOT has_schema_privilege(
        'realytics_identity', 'realytics', 'USAGE'
    ) OR has_schema_privilege(
        'realytics_identity', 'realytics', 'CREATE'
    ) THEN
        RAISE EXCEPTION
            'realytics_identity schema privilege audit failed';
    END IF;

    IF NOT has_table_privilege(
        'realytics_identity', 'realytics.principals', 'SELECT'
    ) OR NOT has_table_privilege(
        'realytics_identity', 'realytics.principals', 'INSERT'
    ) OR NOT has_table_privilege(
        'realytics_identity', 'realytics.tenant_memberships', 'SELECT'
    ) OR NOT has_table_privilege(
        'realytics_identity', 'realytics.tenant_memberships', 'INSERT'
    ) OR NOT has_table_privilege(
        'realytics_identity', 'realytics.tenant_invitations', 'SELECT'
    ) OR NOT has_table_privilege(
        'realytics_identity', 'realytics.tenant_invitations', 'INSERT'
    ) OR NOT has_table_privilege(
        'realytics_identity', 'realytics.tenant_invitations', 'UPDATE'
    ) OR NOT has_table_privilege(
        'realytics_identity',
        'realytics.identity_invitation_token_index',
        'SELECT'
    ) OR NOT has_table_privilege(
        'realytics_identity',
        'realytics.identity_invitation_token_index',
        'INSERT'
    ) OR NOT has_table_privilege(
        'realytics_identity',
        'realytics.identity_invitation_token_index',
        'UPDATE'
    ) OR NOT has_table_privilege(
        'realytics_identity',
        'realytics.tenant_identity_access_events',
        'SELECT'
    ) OR NOT has_table_privilege(
        'realytics_identity',
        'realytics.tenant_identity_access_events',
        'INSERT'
    ) THEN
        RAISE EXCEPTION
            'realytics_identity required table privilege audit failed';
    END IF;

    FOREACH forbidden_privilege IN ARRAY ARRAY[
        'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'
    ]
    LOOP
        IF has_table_privilege(
            'realytics_identity',
            'realytics.principals',
            forbidden_privilege
        ) OR has_table_privilege(
            'realytics_identity',
            'realytics.tenant_memberships',
            forbidden_privilege
        ) OR has_table_privilege(
            'realytics_identity',
            'realytics.tenant_invitations',
            forbidden_privilege
        ) OR has_table_privilege(
            'realytics_identity',
            'realytics.identity_invitation_token_index',
            forbidden_privilege
        ) OR has_table_privilege(
            'realytics_identity',
            'realytics.tenant_identity_access_events',
            forbidden_privilege
        ) THEN
            RAISE EXCEPTION
                'realytics_identity forbidden table privilege: %',
                forbidden_privilege;
        END IF;
    END LOOP;

    IF has_table_privilege(
        'realytics_identity', 'realytics.principals', 'UPDATE'
    ) OR has_table_privilege(
        'realytics_identity', 'realytics.tenant_memberships', 'UPDATE'
    ) OR has_table_privilege(
        'realytics_identity',
        'realytics.tenant_identity_access_events',
        'UPDATE'
    ) THEN
        RAISE EXCEPTION
            'realytics_identity broad UPDATE privilege audit failed';
    END IF;
END
$realytics_identity_role_postflight$;

COMMENT ON TABLE realytics.identity_invitation_token_index IS
    'Identity-role-only token hash locator; never exposed through browser or Data API.';
