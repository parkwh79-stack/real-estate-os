-- REALYTICS core tenant authority expansion.
--
-- This migration deliberately precedes the core RLS policy migration:
--   * issuer + subject identifies a globally verified principal;
--   * tenant_memberships, not token/user metadata, owns tenant role/status;
--   * projects owns the canonical tenant binding for project-scoped records;
--   * unresolved or contradictory legacy lineage aborts instead of fabricating
--     a default tenant.
--
-- RLS is intentionally not enabled here. Runtime transactions must first set a
-- database-validated tenant context; the following migration owns RLS rollout.

-- ---------------------------------------------------------------------------
-- Global verified principals and authoritative tenant memberships
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS principals (
    issuer                  TEXT NOT NULL,
    subject                 TEXT NOT NULL,
    principal_type          TEXT NOT NULL,
    status                  TEXT NOT NULL,
    email                   TEXT,
    display_name            TEXT,
    legacy_user_id          TEXT,
    verification_method     TEXT NOT NULL,
    first_verified_at       TIMESTAMPTZ NOT NULL,
    last_verified_at        TIMESTAMPTZ NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (issuer, subject),
    CONSTRAINT ck_principals_issuer_nonblank
        CHECK (btrim(issuer) <> ''),
    CONSTRAINT ck_principals_subject_nonblank
        CHECK (btrim(subject) <> ''),
    CONSTRAINT ck_principals_type
        CHECK (principal_type IN ('human', 'service', 'unknown')),
    CONSTRAINT ck_principals_status
        CHECK (status IN ('active', 'disabled', 'revoked')),
    CONSTRAINT ck_principals_verification_method_nonblank
        CHECK (btrim(verification_method) <> ''),
    CONSTRAINT ck_principals_verification_window
        CHECK (
            first_verified_at <= last_verified_at
            AND created_at <= updated_at
        )
);

CREATE INDEX IF NOT EXISTS idx_principals_legacy_user
    ON principals (legacy_user_id)
    WHERE legacy_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_principals_status_verified
    ON principals (status, last_verified_at DESC);

CREATE TABLE IF NOT EXISTS tenant_memberships (
    tenant_id           TEXT NOT NULL,
    issuer              TEXT NOT NULL,
    subject             TEXT NOT NULL,
    role                TEXT NOT NULL,
    status              TEXT NOT NULL,
    membership_version  BIGINT NOT NULL DEFAULT 1,
    membership_source   TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, issuer, subject),
    CONSTRAINT fk_tenant_memberships_tenant
        FOREIGN KEY (tenant_id)
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_tenant_memberships_principal
        FOREIGN KEY (issuer, subject)
        REFERENCES principals(issuer, subject) ON DELETE RESTRICT,
    CONSTRAINT ck_tenant_memberships_tenant_nonblank
        CHECK (btrim(tenant_id) <> ''),
    CONSTRAINT ck_tenant_memberships_issuer_nonblank
        CHECK (btrim(issuer) <> ''),
    CONSTRAINT ck_tenant_memberships_subject_nonblank
        CHECK (btrim(subject) <> ''),
    CONSTRAINT ck_tenant_memberships_role
        CHECK (
            role IN (
                'admin',
                'reviewer',
                'analyst',
                'viewer',
                'legal_reviewer',
                'legal_counsel_approver',
                'external_counsel',
                'compliance_officer'
            )
        ),
    CONSTRAINT ck_tenant_memberships_status
        CHECK (
            status IN (
                'active',
                'inactive',
                'suspended',
                'revoked',
                'pending'
            )
        ),
    CONSTRAINT ck_tenant_memberships_version_positive
        CHECK (membership_version > 0),
    CONSTRAINT ck_tenant_memberships_source_nonblank
        CHECK (btrim(membership_source) <> ''),
    CONSTRAINT ck_tenant_memberships_timestamp_order
        CHECK (created_at <= updated_at)
);

CREATE INDEX IF NOT EXISTS idx_tenant_memberships_principal_status
    ON tenant_memberships (issuer, subject, status, tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant_status_role
    ON tenant_memberships (tenant_id, status, role, issuer, subject);

COMMENT ON TABLE principals IS
    'Globally verified identities keyed only by signed-token issuer and subject.';
COMMENT ON TABLE tenant_memberships IS
    'Authoritative tenant role/status; request and user metadata are not authorization inputs.';
COMMENT ON COLUMN users.tenant_id IS
    'Legacy compatibility pointer only; tenant_memberships is authoritative and users may be multi-tenant.';
COMMENT ON COLUMN users.role IS
    'Legacy compatibility projection only; tenant_memberships.role is authoritative.';
COMMENT ON COLUMN sessions.tenant_id IS
    'Legacy session projection only; never an authoritative tenant membership.';

-- Existing tenants are accepted only when their identifiers are usable. No
-- migration path below inserts or derives a replacement/default tenant.
DO $tenant_identity_preflight$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM tenants
         WHERE tenant_id IS NULL OR btrim(tenant_id) = ''
    ) THEN
        RAISE EXCEPTION
            'Tenant authority expansion rejected blank tenant identifiers'
            USING HINT =
                'Repair the explicit tenant ledger before applying migration 0012.';
    END IF;
END;
$tenant_identity_preflight$;

DO $tenant_identity_constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'tenants'::regclass
           AND conname = 'ck_tenants_tenant_id_nonblank'
    ) THEN
        ALTER TABLE tenants
            ADD CONSTRAINT ck_tenants_tenant_id_nonblank
            CHECK (btrim(tenant_id) <> '') NOT VALID;
        ALTER TABLE tenants
            VALIDATE CONSTRAINT ck_tenants_tenant_id_nonblank;
    END IF;
END;
$tenant_identity_constraint$;

-- A legacy user is promoted only when the stored server-side record attests a
-- verified bearer path and the identity provider is an explicit Supabase Auth
-- issuer. Supabase principals use the signed JWT sub as user_id in the legacy
-- adapter. Local OAuth/session rows and ambiguous provider aliases remain
-- deliberately unpromoted.
INSERT INTO principals (
    issuer,
    subject,
    principal_type,
    status,
    email,
    display_name,
    legacy_user_id,
    verification_method,
    first_verified_at,
    last_verified_at,
    created_at,
    updated_at
)
SELECT
    regexp_replace(
        btrim(user_record.metadata_json ->> 'identityProvider'),
        '/+$',
        ''
    ) AS issuer,
    btrim(user_record.user_id) AS subject,
    CASE
        WHEN user_record.metadata_json ->> 'principalType'
            IN ('human', 'service', 'unknown')
            THEN user_record.metadata_json ->> 'principalType'
        ELSE 'unknown'
    END AS principal_type,
    CASE
        WHEN user_record.status = 'active' THEN 'active'
        WHEN user_record.status = 'revoked' THEN 'revoked'
        ELSE 'disabled'
    END AS status,
    user_record.email,
    user_record.display_name,
    user_record.user_id,
    'legacy_supabase_signed_bearer',
    user_record.created_at,
    user_record.updated_at,
    user_record.created_at,
    user_record.updated_at
FROM users AS user_record
WHERE jsonb_typeof(user_record.metadata_json) = 'object'
  AND user_record.metadata_json ->> 'authSource' = 'bearer_token'
  AND btrim(user_record.metadata_json ->> 'identityProvider')
        ~* '^https://[a-z0-9.-]+[.]supabase[.](co|in)/auth/v1/?$'
  AND user_record.user_id IS NOT NULL
  AND btrim(user_record.user_id) <> ''
  AND user_record.created_at IS NOT NULL
  AND user_record.updated_at IS NOT NULL
  AND user_record.status IN (
      'active',
      'inactive',
      'suspended',
      'revoked',
      'pending'
  )
ON CONFLICT (issuer, subject) DO NOTHING;

INSERT INTO tenant_memberships (
    tenant_id,
    issuer,
    subject,
    role,
    status,
    membership_version,
    membership_source,
    created_at,
    updated_at
)
SELECT
    btrim(user_record.tenant_id),
    principal.issuer,
    principal.subject,
    user_record.role,
    user_record.status,
    1,
    'legacy_verified_bearer_projection',
    user_record.created_at,
    user_record.updated_at
FROM users AS user_record
JOIN principals AS principal
  ON principal.legacy_user_id = user_record.user_id
 AND principal.issuer = regexp_replace(
        btrim(user_record.metadata_json ->> 'identityProvider'),
        '/+$',
        ''
    )
 AND principal.subject = btrim(user_record.user_id)
JOIN tenants AS tenant
  ON tenant.tenant_id = btrim(user_record.tenant_id)
WHERE jsonb_typeof(user_record.metadata_json) = 'object'
  AND user_record.metadata_json ->> 'authSource' = 'bearer_token'
  AND btrim(user_record.metadata_json ->> 'identityProvider')
        ~* '^https://[a-z0-9.-]+[.]supabase[.](co|in)/auth/v1/?$'
  AND user_record.tenant_id IS NOT NULL
  AND btrim(user_record.tenant_id) <> ''
  AND user_record.role IN (
      'admin',
      'reviewer',
      'analyst',
      'viewer',
      'legal_reviewer',
      'legal_counsel_approver',
      'external_counsel',
      'compliance_officer'
  )
  AND user_record.status IN (
      'active',
      'inactive',
      'suspended',
      'revoked',
      'pending'
  )
  AND user_record.created_at IS NOT NULL
  AND user_record.updated_at IS NOT NULL
ON CONFLICT (tenant_id, issuer, subject) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Canonical project identity, atomic ID allocation, and optimistic CAS
-- ---------------------------------------------------------------------------

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS state_version BIGINT NOT NULL DEFAULT 1;

CREATE SEQUENCE IF NOT EXISTS project_id_sequence
    AS BIGINT
    MINVALUE 1
    START WITH 1
    INCREMENT BY 1
    NO CYCLE;

DO $project_id_sequence_sync$
DECLARE
    max_numeric_project_id BIGINT;
    sequence_last_value BIGINT;
    sequence_is_called BOOLEAN;
BEGIN
    SELECT max(project_id::BIGINT)
      INTO max_numeric_project_id
      FROM projects
     WHERE project_id ~ '^[0-9]{1,18}$';

    SELECT last_value, is_called
      INTO sequence_last_value, sequence_is_called
      FROM project_id_sequence;

    IF max_numeric_project_id IS NOT NULL
       AND (
            max_numeric_project_id > sequence_last_value
            OR (
                max_numeric_project_id = sequence_last_value
                AND NOT sequence_is_called
            )
       ) THEN
        PERFORM setval(
            'project_id_sequence'::regclass,
            max_numeric_project_id,
            TRUE
        );
    END IF;
END;
$project_id_sequence_sync$;

ALTER TABLE projects
    ALTER COLUMN project_id
    SET DEFAULT (nextval('project_id_sequence'::regclass))::TEXT;

-- Add tenant columns before discovering lineage. Newly added columns are NULL
-- and therefore cannot manufacture a project candidate.
ALTER TABLE reports ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE orchestrations ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE market_evidence ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE land_evidence ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE risk_registers ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE evidence_graphs ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE chief_pod_artifacts ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE pod_ai_reviews ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE agent_handoffs ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE agent_run_steps ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE recalculation_events ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE calculation_runs ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE deal_state_records ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE prompt_artifacts ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE assumption_overrides ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE restructuring_cures ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE glossary_term_candidates ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE project_snapshots ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE user_activity_logs ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE land_parcels ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE legal_reviews ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE permit_reviews ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE assumption_proposals ADD COLUMN IF NOT EXISTS tenant_id TEXT;

-- Collect existing normalized project/tenant lineage from every table in the
-- active REALYTICS schema. The migration never reads tenant values from a JSON
-- payload, request header, JWT user metadata, or a default setting.
CREATE TEMPORARY TABLE realytics_project_tenant_candidates (
    project_id   TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    source_table TEXT NOT NULL,
    PRIMARY KEY (project_id, tenant_id, source_table)
) ON COMMIT DROP;

DO $collect_project_tenant_lineage$
DECLARE
    schema_name TEXT := current_schema();
    source_table TEXT;
BEGIN
    FOR source_table IN
        SELECT column_inventory.table_name
          FROM information_schema.columns AS column_inventory
         WHERE column_inventory.table_schema = schema_name
           AND column_inventory.column_name IN ('project_id', 'tenant_id')
         GROUP BY column_inventory.table_name
        HAVING count(DISTINCT column_inventory.column_name) = 2
         ORDER BY column_inventory.table_name
    LOOP
        EXECUTE format(
            'INSERT INTO realytics_project_tenant_candidates '
            || '(project_id, tenant_id, source_table) '
            || 'SELECT DISTINCT btrim(project_id::TEXT), '
            || 'btrim(tenant_id::TEXT), %L FROM %I.%I '
            || 'WHERE project_id IS NOT NULL '
            || 'AND btrim(project_id::TEXT) <> '''' '
            || 'AND tenant_id IS NOT NULL '
            || 'AND btrim(tenant_id::TEXT) <> '''' '
            || 'ON CONFLICT DO NOTHING',
            source_table,
            schema_name,
            source_table
        );
    END LOOP;
END;
$collect_project_tenant_lineage$;

DO $reject_ambiguous_project_tenants$
DECLARE
    conflicting_projects TEXT;
BEGIN
    SELECT string_agg(conflict.project_id, ', ' ORDER BY conflict.project_id)
      INTO conflicting_projects
      FROM (
          SELECT candidate.project_id
            FROM realytics_project_tenant_candidates AS candidate
            JOIN projects AS project
              ON project.project_id = candidate.project_id
           GROUP BY candidate.project_id
          HAVING count(DISTINCT candidate.tenant_id) > 1
           ORDER BY candidate.project_id
           LIMIT 20
      ) AS conflict;

    IF conflicting_projects IS NOT NULL THEN
        RAISE EXCEPTION
            'Conflicting tenant lineage for project(s): %',
            conflicting_projects
            USING HINT =
                'Resolve the normalized tenant records; migration 0012 never chooses between tenants.';
    END IF;
END;
$reject_ambiguous_project_tenants$;

WITH resolved_project_tenants AS (
    SELECT
        candidate.project_id,
        min(candidate.tenant_id) AS tenant_id
    FROM realytics_project_tenant_candidates AS candidate
    GROUP BY candidate.project_id
    HAVING count(DISTINCT candidate.tenant_id) = 1
)
UPDATE projects AS project
   SET tenant_id = resolved.tenant_id
  FROM resolved_project_tenants AS resolved
 WHERE project.project_id = resolved.project_id
   AND (
        project.tenant_id IS NULL
        OR btrim(project.tenant_id) = ''
   );

DO $project_tenant_preflight$
DECLARE
    unresolved_projects TEXT;
    unknown_tenant_projects TEXT;
BEGIN
    SELECT string_agg(project_id, ', ' ORDER BY project_id)
      INTO unresolved_projects
      FROM (
          SELECT project_id
            FROM projects
           WHERE project_id IS NULL
              OR btrim(project_id) = ''
              OR tenant_id IS NULL
              OR btrim(tenant_id) = ''
           ORDER BY project_id
           LIMIT 20
      ) AS unresolved;

    IF unresolved_projects IS NOT NULL THEN
        RAISE EXCEPTION
            'Project tenant lineage is unresolved for project(s): %',
            unresolved_projects
            USING HINT =
                'Attach one explicit normalized tenant lineage per project before retrying.';
    END IF;

    SELECT string_agg(project.project_id, ', ' ORDER BY project.project_id)
      INTO unknown_tenant_projects
      FROM projects AS project
      LEFT JOIN tenants AS tenant
        ON tenant.tenant_id = project.tenant_id
     WHERE tenant.tenant_id IS NULL;

    IF unknown_tenant_projects IS NOT NULL THEN
        RAISE EXCEPTION
            'Project(s) reference tenant identifiers absent from tenants: %',
            unknown_tenant_projects
            USING HINT =
                'Provision the explicit tenant ledger; migration 0012 does not create tenants.';
    END IF;
END;
$project_tenant_preflight$;

ALTER TABLE projects ALTER COLUMN tenant_id SET NOT NULL;

DO $project_authority_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'projects'::regclass
           AND conname = 'ck_projects_project_id_nonblank'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT ck_projects_project_id_nonblank
            CHECK (btrim(project_id) <> '') NOT VALID;
        ALTER TABLE projects
            VALIDATE CONSTRAINT ck_projects_project_id_nonblank;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'projects'::regclass
           AND conname = 'ck_projects_tenant_id_nonblank'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT ck_projects_tenant_id_nonblank
            CHECK (btrim(tenant_id) <> '') NOT VALID;
        ALTER TABLE projects
            VALIDATE CONSTRAINT ck_projects_tenant_id_nonblank;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'projects'::regclass
           AND conname = 'ck_projects_state_version_positive'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT ck_projects_state_version_positive
            CHECK (state_version > 0) NOT VALID;
        ALTER TABLE projects
            VALIDATE CONSTRAINT ck_projects_state_version_positive;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'projects'::regclass
           AND conname = 'fk_projects_tenant'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT fk_projects_tenant
            FOREIGN KEY (tenant_id)
            REFERENCES tenants(tenant_id)
            ON DELETE RESTRICT
            NOT VALID;
        ALTER TABLE projects VALIDATE CONSTRAINT fk_projects_tenant;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'projects'::regclass
           AND conname = 'uq_projects_tenant_project'
    ) THEN
        ALTER TABLE projects
            ADD CONSTRAINT uq_projects_tenant_project
            UNIQUE (tenant_id, project_id);
    END IF;
END;
$project_authority_constraints$;

-- ---------------------------------------------------------------------------
-- Deterministic child/parent backfill
-- ---------------------------------------------------------------------------

DO $backfill_required_project_tables$
DECLARE
    schema_name TEXT := current_schema();
    table_name TEXT;
    invalid_lineage BOOLEAN;
    required_project_tables CONSTANT TEXT[] := ARRAY[
        'reports',
        'orchestrations',
        'market_evidence',
        'land_evidence',
        'risk_registers',
        'evidence_graphs',
        'chief_pod_artifacts',
        'pod_ai_reviews',
        'agent_handoffs',
        'recalculation_events',
        'calculation_runs',
        'deal_state_records',
        'prompt_artifacts',
        'assumption_overrides',
        'restructuring_cures',
        'project_snapshots',
        'legal_reviews',
        'permit_reviews',
        'assumption_proposals',
        'project_versions'
    ];
BEGIN
    FOREACH table_name IN ARRAY required_project_tables
    LOOP
        EXECUTE format(
            'UPDATE %I.%I AS child '
            || 'SET tenant_id = project.tenant_id '
            || 'FROM projects AS project '
            || 'WHERE child.project_id = project.project_id '
            || 'AND (child.tenant_id IS NULL OR btrim(child.tenant_id) = '''')',
            schema_name,
            table_name
        );

        EXECUTE format(
            'SELECT EXISTS ('
            || 'SELECT 1 FROM %I.%I AS child '
            || 'LEFT JOIN projects AS project '
            || 'ON project.project_id = child.project_id '
            || 'WHERE child.project_id IS NULL '
            || 'OR btrim(child.project_id) = '''' '
            || 'OR project.project_id IS NULL '
            || 'OR child.tenant_id IS NULL '
            || 'OR btrim(child.tenant_id) = '''' '
            || 'OR child.tenant_id IS DISTINCT FROM project.tenant_id'
            || ')',
            schema_name,
            table_name
        ) INTO invalid_lineage;

        IF invalid_lineage THEN
            RAISE EXCEPTION
                'Unresolved or conflicting tenant/project lineage in table %',
                table_name
                USING HINT =
                    'Repair the explicit project or parent relationship before applying migration 0012.';
        END IF;

        EXECUTE format(
            'ALTER TABLE %I.%I ALTER COLUMN tenant_id SET NOT NULL',
            schema_name,
            table_name
        );
    END LOOP;
END;
$backfill_required_project_tables$;

-- Async jobs are tenant-owned even when a future tenant-level job has no
-- project. Existing rows must provide either an explicit tenant or a project
-- whose canonical tenant can be followed.
UPDATE async_jobs AS job
   SET tenant_id = project.tenant_id
  FROM projects AS project
 WHERE job.project_id = project.project_id
   AND (
        job.tenant_id IS NULL
        OR btrim(job.tenant_id) = ''
   );

DO $async_job_tenant_preflight$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM async_jobs AS job
          LEFT JOIN tenants AS tenant
            ON tenant.tenant_id = job.tenant_id
          LEFT JOIN projects AS project
            ON project.project_id = job.project_id
         WHERE job.tenant_id IS NULL
            OR btrim(job.tenant_id) = ''
            OR tenant.tenant_id IS NULL
            OR (
                job.project_id IS NOT NULL
                AND (
                    btrim(job.project_id) = ''
                    OR project.project_id IS NULL
                    OR job.tenant_id IS DISTINCT FROM project.tenant_id
                )
            )
    ) THEN
        RAISE EXCEPTION
            'Async job tenant lineage is unresolved or conflicts with its project'
            USING HINT =
                'Persist an explicit tenant or valid project relationship; no default is assigned.';
    END IF;
END;
$async_job_tenant_preflight$;

ALTER TABLE async_jobs ALTER COLUMN tenant_id SET NOT NULL;

-- Agent runs already carried tenant_id. Validate it as a parent authority, then
-- derive each step from its immutable run parent rather than a client payload.
DO $agent_run_tenant_preflight$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM agent_runs AS run
          LEFT JOIN tenants AS tenant
            ON tenant.tenant_id = run.tenant_id
          LEFT JOIN projects AS project
            ON project.project_id = run.project_id
         WHERE btrim(run.tenant_id) = ''
            OR tenant.tenant_id IS NULL
            OR (
                run.project_id IS NOT NULL
                AND (
                    btrim(run.project_id) = ''
                    OR project.project_id IS NULL
                    OR run.tenant_id IS DISTINCT FROM project.tenant_id
                )
            )
    ) THEN
        RAISE EXCEPTION
            'Agent run tenant lineage is unresolved or conflicts with its project';
    END IF;
END;
$agent_run_tenant_preflight$;

UPDATE agent_run_steps AS step
   SET tenant_id = run.tenant_id,
       project_id = COALESCE(step.project_id, run.project_id)
  FROM agent_runs AS run
 WHERE run.run_id = step.run_id
   AND (
        step.tenant_id IS NULL
        OR btrim(step.tenant_id) = ''
        OR (
            step.project_id IS NULL
            AND run.project_id IS NOT NULL
        )
   );

DO $agent_run_step_tenant_preflight$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM agent_run_steps AS step
          LEFT JOIN agent_runs AS run
            ON run.run_id = step.run_id
          LEFT JOIN projects AS project
            ON project.project_id = step.project_id
         WHERE run.run_id IS NULL
            OR step.tenant_id IS NULL
            OR btrim(step.tenant_id) = ''
            OR step.tenant_id IS DISTINCT FROM run.tenant_id
            OR (
                step.project_id IS NOT NULL
                AND (
                    btrim(step.project_id) = ''
                    OR project.project_id IS NULL
                    OR step.tenant_id IS DISTINCT FROM project.tenant_id
                    OR step.project_id IS DISTINCT FROM run.project_id
                )
            )
    ) THEN
        RAISE EXCEPTION
            'Agent run step tenant/project lineage conflicts with its parent run';
    END IF;
END;
$agent_run_step_tenant_preflight$;

ALTER TABLE agent_run_steps ALTER COLUMN tenant_id SET NOT NULL;

-- These stores permit genuinely global rows. A project-bound row, however,
-- must resolve to the project's tenant and can never carry a blank identifier.
DO $backfill_optional_project_tables$
DECLARE
    schema_name TEXT := current_schema();
    table_name TEXT;
    invalid_lineage BOOLEAN;
    optional_project_tables CONSTANT TEXT[] := ARRAY[
        'glossary_term_candidates',
        'land_parcels'
    ];
BEGIN
    FOREACH table_name IN ARRAY optional_project_tables
    LOOP
        EXECUTE format(
            'UPDATE %I.%I AS child '
            || 'SET tenant_id = project.tenant_id '
            || 'FROM projects AS project '
            || 'WHERE child.project_id = project.project_id '
            || 'AND (child.tenant_id IS NULL OR btrim(child.tenant_id) = '''')',
            schema_name,
            table_name
        );

        EXECUTE format(
            'SELECT EXISTS ('
            || 'SELECT 1 FROM %I.%I AS child '
            || 'LEFT JOIN projects AS project '
            || 'ON project.project_id = child.project_id '
            || 'LEFT JOIN tenants AS tenant '
            || 'ON tenant.tenant_id = child.tenant_id '
            || 'WHERE ('
            || 'child.project_id IS NOT NULL AND ('
            || 'btrim(child.project_id) = '''' '
            || 'OR project.project_id IS NULL '
            || 'OR child.tenant_id IS NULL '
            || 'OR btrim(child.tenant_id) = '''' '
            || 'OR child.tenant_id IS DISTINCT FROM project.tenant_id'
            || ')) OR ('
            || 'child.tenant_id IS NOT NULL AND ('
            || 'btrim(child.tenant_id) = '''' OR tenant.tenant_id IS NULL'
            || '))'
            || ')',
            schema_name,
            table_name
        ) INTO invalid_lineage;

        IF invalid_lineage THEN
            RAISE EXCEPTION
                'Unresolved or conflicting optional tenant/project lineage in table %',
                table_name;
        END IF;
    END LOOP;
END;
$backfill_optional_project_tables$;

-- user_activity_logs.project_id may be an attempted/nonexistent request
-- locator (for example, a denied 404) rather than a relational project owner.
-- Backfill only known projects and retain unresolved locators for audit. A
-- later normalized audit-event contract can bind verified principal/tenant
-- context without deleting historical security evidence.
UPDATE user_activity_logs AS activity
   SET tenant_id = project.tenant_id
  FROM projects AS project
 WHERE activity.project_id = project.project_id
   AND (
        activity.tenant_id IS NULL
        OR btrim(activity.tenant_id) = ''
   );

DO $user_activity_tenant_preflight$
DECLARE
    unresolved_locator_count BIGINT;
BEGIN
    IF EXISTS (
        SELECT 1
          FROM user_activity_logs AS activity
          LEFT JOIN tenants AS tenant
            ON tenant.tenant_id = activity.tenant_id
         WHERE activity.tenant_id IS NOT NULL
           AND (
                btrim(activity.tenant_id) = ''
                OR tenant.tenant_id IS NULL
           )
    ) THEN
        RAISE EXCEPTION
            'User activity logs contain a nonblank tenant absent from tenants';
    END IF;

    SELECT count(*)
      INTO unresolved_locator_count
      FROM user_activity_logs AS activity
      LEFT JOIN projects AS project
        ON project.project_id = activity.project_id
     WHERE activity.project_id IS NOT NULL
       AND project.project_id IS NULL
       AND activity.tenant_id IS NULL;

    IF unresolved_locator_count > 0 THEN
        RAISE NOTICE
            'Retaining % user activity project locator(s) without tenant binding',
            unresolved_locator_count;
    END IF;
END;
$user_activity_tenant_preflight$;

-- ---------------------------------------------------------------------------
-- Tenant-first indexes and validated relational constraints
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_tenant_run
    ON agent_runs (tenant_id, run_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_tenant_project_run
    ON agent_runs (tenant_id, project_id, run_id);

DO $core_tenant_constraints$
DECLARE
    schema_name TEXT := current_schema();
    table_name TEXT;
    constraint_name TEXT;
    required_project_tables CONSTANT TEXT[] := ARRAY[
        'reports',
        'orchestrations',
        'market_evidence',
        'land_evidence',
        'risk_registers',
        'evidence_graphs',
        'chief_pod_artifacts',
        'pod_ai_reviews',
        'agent_handoffs',
        'recalculation_events',
        'calculation_runs',
        'deal_state_records',
        'prompt_artifacts',
        'assumption_overrides',
        'restructuring_cures',
        'project_snapshots',
        'legal_reviews',
        'permit_reviews',
        'assumption_proposals',
        'project_versions'
    ];
    optional_project_tables CONSTANT TEXT[] := ARRAY[
        'glossary_term_candidates',
        'land_parcels'
    ];
BEGIN
    FOREACH table_name IN ARRAY required_project_tables
    LOOP
        constraint_name := 'ck_' || table_name || '_tenant_nonblank';
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = format('%I.%I', schema_name, table_name)::regclass
               AND conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I '
                || 'CHECK (btrim(tenant_id) <> '''') NOT VALID',
                schema_name,
                table_name,
                constraint_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
                schema_name,
                table_name,
                constraint_name
            );
        END IF;

        constraint_name := 'ck_' || table_name || '_project_nonblank';
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = format('%I.%I', schema_name, table_name)::regclass
               AND conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I '
                || 'CHECK (btrim(project_id) <> '''') NOT VALID',
                schema_name,
                table_name,
                constraint_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
                schema_name,
                table_name,
                constraint_name
            );
        END IF;

        constraint_name := 'fk_' || table_name || '_tenant';
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = format('%I.%I', schema_name, table_name)::regclass
               AND conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I '
                || 'FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) '
                || 'ON DELETE RESTRICT NOT VALID',
                schema_name,
                table_name,
                constraint_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
                schema_name,
                table_name,
                constraint_name
            );
        END IF;

        constraint_name := 'fk_' || table_name || '_tenant_project';
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = format('%I.%I', schema_name, table_name)::regclass
               AND conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I '
                || 'FOREIGN KEY (tenant_id, project_id) '
                || 'REFERENCES projects(tenant_id, project_id) '
                || 'ON DELETE RESTRICT NOT VALID',
                schema_name,
                table_name,
                constraint_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
                schema_name,
                table_name,
                constraint_name
            );
        END IF;

        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.%I '
            || '(tenant_id, project_id)',
            'idx_' || table_name || '_tenant_project',
            schema_name,
            table_name
        );
    END LOOP;

    FOREACH table_name IN ARRAY optional_project_tables
    LOOP
        constraint_name := 'ck_' || table_name || '_tenant_optional_nonblank';
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = format('%I.%I', schema_name, table_name)::regclass
               AND conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I '
                || 'CHECK (tenant_id IS NULL OR btrim(tenant_id) <> '''') '
                || 'NOT VALID',
                schema_name,
                table_name,
                constraint_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
                schema_name,
                table_name,
                constraint_name
            );
        END IF;

        constraint_name := 'ck_' || table_name || '_project_requires_tenant';
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = format('%I.%I', schema_name, table_name)::regclass
               AND conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I '
                || 'CHECK (project_id IS NULL OR ('
                || 'btrim(project_id) <> '''' AND tenant_id IS NOT NULL'
                || ')) NOT VALID',
                schema_name,
                table_name,
                constraint_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
                schema_name,
                table_name,
                constraint_name
            );
        END IF;

        constraint_name := 'fk_' || table_name || '_tenant';
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = format('%I.%I', schema_name, table_name)::regclass
               AND conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I '
                || 'FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) '
                || 'ON DELETE RESTRICT NOT VALID',
                schema_name,
                table_name,
                constraint_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
                schema_name,
                table_name,
                constraint_name
            );
        END IF;

        constraint_name := 'fk_' || table_name || '_tenant_project';
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid = format('%I.%I', schema_name, table_name)::regclass
               AND conname = constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I '
                || 'FOREIGN KEY (tenant_id, project_id) '
                || 'REFERENCES projects(tenant_id, project_id) '
                || 'ON DELETE RESTRICT NOT VALID',
                schema_name,
                table_name,
                constraint_name
            );
            EXECUTE format(
                'ALTER TABLE %I.%I VALIDATE CONSTRAINT %I',
                schema_name,
                table_name,
                constraint_name
            );
        END IF;

        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.%I '
            || '(tenant_id, project_id)',
            'idx_' || table_name || '_tenant_project',
            schema_name,
            table_name
        );
    END LOOP;
END;
$core_tenant_constraints$;

DO $user_activity_tenant_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'user_activity_logs'::regclass
           AND conname = 'ck_user_activity_logs_tenant_optional_nonblank'
    ) THEN
        ALTER TABLE user_activity_logs
            ADD CONSTRAINT ck_user_activity_logs_tenant_optional_nonblank
            CHECK (tenant_id IS NULL OR btrim(tenant_id) <> '') NOT VALID;
        ALTER TABLE user_activity_logs
            VALIDATE CONSTRAINT
                ck_user_activity_logs_tenant_optional_nonblank;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'user_activity_logs'::regclass
           AND conname = 'fk_user_activity_logs_tenant'
    ) THEN
        ALTER TABLE user_activity_logs
            ADD CONSTRAINT fk_user_activity_logs_tenant
            FOREIGN KEY (tenant_id)
            REFERENCES tenants(tenant_id)
            ON DELETE RESTRICT
            NOT VALID;
        ALTER TABLE user_activity_logs
            VALIDATE CONSTRAINT fk_user_activity_logs_tenant;
    END IF;
END;
$user_activity_tenant_constraints$;

CREATE INDEX IF NOT EXISTS idx_user_activity_logs_tenant_project
    ON user_activity_logs (tenant_id, project_id, created_at DESC);

DO $runtime_parent_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'async_jobs'::regclass
           AND conname = 'ck_async_jobs_tenant_nonblank'
    ) THEN
        ALTER TABLE async_jobs
            ADD CONSTRAINT ck_async_jobs_tenant_nonblank
            CHECK (btrim(tenant_id) <> '') NOT VALID;
        ALTER TABLE async_jobs
            VALIDATE CONSTRAINT ck_async_jobs_tenant_nonblank;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'async_jobs'::regclass
           AND conname = 'fk_async_jobs_tenant'
    ) THEN
        ALTER TABLE async_jobs
            ADD CONSTRAINT fk_async_jobs_tenant
            FOREIGN KEY (tenant_id)
            REFERENCES tenants(tenant_id)
            ON DELETE RESTRICT
            NOT VALID;
        ALTER TABLE async_jobs VALIDATE CONSTRAINT fk_async_jobs_tenant;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'async_jobs'::regclass
           AND conname = 'fk_async_jobs_tenant_project'
    ) THEN
        ALTER TABLE async_jobs
            ADD CONSTRAINT fk_async_jobs_tenant_project
            FOREIGN KEY (tenant_id, project_id)
            REFERENCES projects(tenant_id, project_id)
            ON DELETE RESTRICT
            NOT VALID;
        ALTER TABLE async_jobs
            VALIDATE CONSTRAINT fk_async_jobs_tenant_project;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'agent_runs'::regclass
           AND conname = 'ck_agent_runs_tenant_nonblank'
    ) THEN
        ALTER TABLE agent_runs
            ADD CONSTRAINT ck_agent_runs_tenant_nonblank
            CHECK (btrim(tenant_id) <> '') NOT VALID;
        ALTER TABLE agent_runs
            VALIDATE CONSTRAINT ck_agent_runs_tenant_nonblank;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'agent_runs'::regclass
           AND conname = 'fk_agent_runs_tenant'
    ) THEN
        ALTER TABLE agent_runs
            ADD CONSTRAINT fk_agent_runs_tenant
            FOREIGN KEY (tenant_id)
            REFERENCES tenants(tenant_id)
            ON DELETE RESTRICT
            NOT VALID;
        ALTER TABLE agent_runs VALIDATE CONSTRAINT fk_agent_runs_tenant;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'agent_runs'::regclass
           AND conname = 'fk_agent_runs_tenant_project'
    ) THEN
        ALTER TABLE agent_runs
            ADD CONSTRAINT fk_agent_runs_tenant_project
            FOREIGN KEY (tenant_id, project_id)
            REFERENCES projects(tenant_id, project_id)
            ON DELETE RESTRICT
            NOT VALID;
        ALTER TABLE agent_runs
            VALIDATE CONSTRAINT fk_agent_runs_tenant_project;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'agent_run_steps'::regclass
           AND conname = 'fk_agent_run_steps_tenant_run'
    ) THEN
        ALTER TABLE agent_run_steps
            ADD CONSTRAINT fk_agent_run_steps_tenant_run
            FOREIGN KEY (tenant_id, run_id)
            REFERENCES agent_runs(tenant_id, run_id)
            ON DELETE CASCADE
            NOT VALID;
        ALTER TABLE agent_run_steps
            VALIDATE CONSTRAINT fk_agent_run_steps_tenant_run;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'agent_run_steps'::regclass
           AND conname = 'fk_agent_run_steps_tenant_project_run'
    ) THEN
        ALTER TABLE agent_run_steps
            ADD CONSTRAINT fk_agent_run_steps_tenant_project_run
            FOREIGN KEY (tenant_id, project_id, run_id)
            REFERENCES agent_runs(tenant_id, project_id, run_id)
            ON DELETE CASCADE
            NOT VALID;
        ALTER TABLE agent_run_steps
            VALIDATE CONSTRAINT fk_agent_run_steps_tenant_project_run;
    END IF;
END;
$runtime_parent_constraints$;

CREATE INDEX IF NOT EXISTS idx_async_jobs_tenant_project_claim
    ON async_jobs (
        tenant_id,
        project_id,
        status,
        lease_expires_at,
        created_at
    );
CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_project_updated
    ON agent_runs (tenant_id, project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_run_steps_tenant_project_run
    ON agent_run_steps (tenant_id, project_id, run_id, sequence_no);

COMMENT ON COLUMN projects.state_version IS
    'Optimistic concurrency token; writers update with WHERE state_version = expected.';
COMMENT ON SEQUENCE project_id_sequence IS
    'Database-authoritative atomic allocator for legacy numeric text project IDs.';
