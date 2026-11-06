-- Support tenant-scoped CalculationRun lists, exact references, and the
-- approval gate's latest-run-per-engine query without scanning another
-- tenant's rows or sorting an entire project history.

CREATE INDEX IF NOT EXISTS idx_calculation_runs_tenant_project_engine_latest
    ON calculation_runs (
        tenant_id,
        project_id,
        engine_key,
        created_at DESC,
        calculation_run_id DESC
    );

DO $realytics_calculation_run_index_postflight$
DECLARE
    index_definition TEXT;
BEGIN
    SELECT pg_get_indexdef(index_relation.oid)
      INTO index_definition
      FROM pg_class AS index_relation
      JOIN pg_namespace AS namespace
        ON namespace.oid = index_relation.relnamespace
     WHERE namespace.nspname = current_schema()
       AND index_relation.relname =
           'idx_calculation_runs_tenant_project_engine_latest';

    IF index_definition IS NULL
       OR regexp_replace(index_definition, '\s+', ' ', 'g') NOT LIKE
          '%(tenant_id, project_id, engine_key, created_at DESC, calculation_run_id DESC)%'
    THEN
        RAISE EXCEPTION
            'CalculationRun tenant/latest composite index is missing or invalid';
    END IF;
END
$realytics_calculation_run_index_postflight$;

COMMENT ON INDEX idx_calculation_runs_tenant_project_engine_latest IS
    'Tenant-first CalculationRun list and latest-per-engine read path.';
