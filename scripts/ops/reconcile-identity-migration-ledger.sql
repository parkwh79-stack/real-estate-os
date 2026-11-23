-- Repair an application-ledger omission for SQL already applied by Supabase.
-- This is metadata reconciliation, not a schema migration or a grant replay.
-- Guard the exact historical SQL, source checksum, and concurrent migrator.
DO $reconcile_identity_ledger$
DECLARE
    applied_statements TEXT[];
    recorded_checksum TEXT;
BEGIN
    PERFORM set_config('lock_timeout', '10s', true);
    PERFORM pg_advisory_xact_lock(7731590012);
    SELECT statements INTO applied_statements
      FROM supabase_migrations.schema_migrations
     WHERE version = '20260728031948'
       AND name = 'identity_tenants_select_only'
     FOR SHARE;

    IF array_length(applied_statements, 1) IS DISTINCT FROM 1
       OR encode(sha256(convert_to(btrim(
           replace(replace(applied_statements[1], E'\r\n', E'\n'), E'\r', E'\n'),
           E' \t\r\n'), 'UTF8')), 'hex') IS DISTINCT FROM
          '69d20ef4d8759cd79e6c7d82a96a59c9846249e573477b66cfb98599e5978163'
    THEN
        RAISE EXCEPTION 'Applied identity SQL does not match the verified source';
    END IF;

    INSERT INTO realytics.schema_migrations(version, checksum)
    VALUES (
        '20260728031948_identity_tenants_select_only.sql',
        'e7add84c8154bc969f2eb189384b5525dced87d809e71323f1fe58ad208e9ea9'
    ) ON CONFLICT (version) DO NOTHING;

    SELECT checksum INTO recorded_checksum FROM realytics.schema_migrations
     WHERE version = '20260728031948_identity_tenants_select_only.sql';
    IF recorded_checksum IS DISTINCT FROM
       'e7add84c8154bc969f2eb189384b5525dced87d809e71323f1fe58ad208e9ea9'
    THEN
        RAISE EXCEPTION 'Existing source checksum differs; reconciliation rolled back';
    END IF;
END
$reconcile_identity_ledger$;
