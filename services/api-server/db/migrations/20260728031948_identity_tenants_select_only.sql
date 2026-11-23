-- Restrict the identity runtime to read-only tenant-directory access.
--
-- Tenant creation and lifecycle changes are administrative control-plane
-- operations. OAuth callback and membership lookup only join tenant_id and
-- read status; the identity runtime must never read descriptive/metadata
-- columns or mutate the authoritative tenant record.

REVOKE ALL PRIVILEGES ON TABLE realytics.tenants
    FROM realytics_identity;
REVOKE SELECT (
    tenant_id, name, status, created_at, updated_at, metadata_json
) ON TABLE realytics.tenants FROM realytics_identity;
GRANT SELECT (tenant_id, status) ON TABLE realytics.tenants
    TO realytics_identity;

DO $realytics_identity_tenants_postflight$
DECLARE
    forbidden_column TEXT;
    forbidden_privilege TEXT;
BEGIN
    IF has_table_privilege(
        'realytics_identity', 'realytics.tenants', 'SELECT'
    ) OR NOT has_column_privilege(
        'realytics_identity', 'realytics.tenants', 'tenant_id', 'SELECT'
    ) OR NOT has_column_privilege(
        'realytics_identity', 'realytics.tenants', 'status', 'SELECT'
    ) THEN
        RAISE EXCEPTION
            'realytics_identity tenant status privilege audit failed';
    END IF;

    FOREACH forbidden_column IN ARRAY ARRAY[
        'name', 'created_at', 'updated_at', 'metadata_json'
    ]
    LOOP
        IF has_column_privilege(
            'realytics_identity',
            'realytics.tenants',
            forbidden_column,
            'SELECT'
        ) THEN
            RAISE EXCEPTION
                'realytics_identity forbidden tenant column: %',
                forbidden_column;
        END IF;
    END LOOP;

    FOREACH forbidden_privilege IN ARRAY ARRAY[
        'INSERT',
        'UPDATE',
        'DELETE',
        'TRUNCATE',
        'REFERENCES',
        'TRIGGER'
    ]
    LOOP
        IF has_table_privilege(
            'realytics_identity',
            'realytics.tenants',
            forbidden_privilege
        ) THEN
            RAISE EXCEPTION
                'realytics_identity forbidden privilege on realytics.tenants: %',
                forbidden_privilege;
        END IF;
    END LOOP;
END
$realytics_identity_tenants_postflight$;
