-- Normalized, immutable PF report publication authority.
--
-- Draft report bodies remain in ``reports.payload_json`` for compatibility,
-- but a report is final only when a matching row exists here.  The unique
-- tenant/project/report key is the database serialization point: identical
-- retries are idempotent and a different content/decision-package seal is a
-- conflict.  Runtime code never updates or deletes a publication row.

DO $realytics_reports_tenant_identity$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'reports'::regclass
           AND conname = 'uq_reports_tenant_project_report'
    ) THEN
        ALTER TABLE reports
            ADD CONSTRAINT uq_reports_tenant_project_report
            UNIQUE (tenant_id, project_id, report_id);
    END IF;
END;
$realytics_reports_tenant_identity$;

DO $realytics_report_lineage_tenant_identity$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'orchestrations'::regclass
           AND conname = 'uq_orchestrations_tenant_project_run'
    ) THEN
        ALTER TABLE orchestrations
            ADD CONSTRAINT uq_orchestrations_tenant_project_run
            UNIQUE (tenant_id, project_id, run_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid = 'project_snapshots'::regclass
           AND conname = 'uq_project_snapshots_tenant_project_snapshot'
    ) THEN
        ALTER TABLE project_snapshots
            ADD CONSTRAINT uq_project_snapshots_tenant_project_snapshot
            UNIQUE (tenant_id, project_id, snapshot_id);
    END IF;
END;
$realytics_report_lineage_tenant_identity$;

CREATE TABLE IF NOT EXISTS report_publications (
    tenant_id                    TEXT NOT NULL,
    project_id                   TEXT NOT NULL,
    report_id                    TEXT NOT NULL,
    report_version               INTEGER NOT NULL,
    orchestration_run_id         TEXT NOT NULL,
    project_snapshot_id          TEXT NOT NULL,
    project_snapshot_hash        TEXT NOT NULL,
    content_sha256               TEXT NOT NULL,
    decision_package_sha256      TEXT NOT NULL,
    idempotency_key              TEXT NOT NULL,
    decision_package_json        JSONB NOT NULL,
    published_by                 TEXT NOT NULL,
    published_at                 TIMESTAMPTZ NOT NULL,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, project_id, report_id),
    CONSTRAINT fk_report_publications_report
        FOREIGN KEY (tenant_id, project_id, report_id)
        REFERENCES reports(tenant_id, project_id, report_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_report_publications_orchestration
        FOREIGN KEY (tenant_id, project_id, orchestration_run_id)
        REFERENCES orchestrations(tenant_id, project_id, run_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_report_publications_snapshot
        FOREIGN KEY (tenant_id, project_id, project_snapshot_id)
        REFERENCES project_snapshots(tenant_id, project_id, snapshot_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_report_publications_tenant_nonblank
        CHECK (btrim(tenant_id) <> ''),
    CONSTRAINT ck_report_publications_project_nonblank
        CHECK (btrim(project_id) <> ''),
    CONSTRAINT ck_report_publications_report_nonblank
        CHECK (btrim(report_id) <> ''),
    CONSTRAINT ck_report_publications_version_positive
        CHECK (report_version > 0),
    CONSTRAINT ck_report_publications_run_nonblank
        CHECK (btrim(orchestration_run_id) <> ''),
    CONSTRAINT ck_report_publications_snapshot_nonblank
        CHECK (btrim(project_snapshot_id) <> ''),
    CONSTRAINT ck_report_publications_actor_nonblank
        CHECK (btrim(published_by) <> ''),
    CONSTRAINT ck_report_publications_snapshot_hash
        CHECK (project_snapshot_hash ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_report_publications_content_hash
        CHECK (content_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_report_publications_decision_hash
        CHECK (decision_package_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_report_publications_idempotency_key
        CHECK (idempotency_key ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_report_publications_decision_object
        CHECK (jsonb_typeof(decision_package_json) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_report_publications_tenant_project_published
    ON report_publications (tenant_id, project_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_report_publications_tenant_run
    ON report_publications (tenant_id, project_id, orchestration_run_id);
CREATE INDEX IF NOT EXISTS idx_report_publications_tenant_snapshot
    ON report_publications (tenant_id, project_id, project_snapshot_id);

CREATE OR REPLACE FUNCTION report_publication_validate_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $realytics_report_publication_validate$
DECLARE
    stored_report_version INTEGER;
    stored_report_run_id TEXT;
    stored_report_content_sha256 TEXT;
    stored_run_project_id TEXT;
    stored_run_id TEXT;
    stored_run_approval_status TEXT;
    stored_run_approver TEXT;
    stored_run_maker TEXT;
    stored_snapshot_project_id TEXT;
    stored_snapshot_run_id TEXT;
    stored_snapshot_hash TEXT;
BEGIN
    IF NEW.decision_package_json->>'projectId'
            IS DISTINCT FROM NEW.project_id
       OR NEW.decision_package_json->>'orchestrationRunId'
            IS DISTINCT FROM NEW.orchestration_run_id
       OR NEW.decision_package_json->>'projectSnapshotId'
            IS DISTINCT FROM NEW.project_snapshot_id
       OR NEW.decision_package_json->>'projectSnapshotHash'
            IS DISTINCT FROM NEW.project_snapshot_hash
       OR lower(COALESCE(
            NEW.decision_package_json->>'approvalStatus',
            ''
       )) <> 'approved' THEN
        RAISE EXCEPTION
            'report publication decision-package lineage is invalid'
            USING ERRCODE = '23514';
    END IF;

    EXECUTE format(
        'SELECT version, '
        '       NULLIF(payload_json->>''run_id'', ''''), '
        '       NULLIF(payload_json #>> ''{metadata,content_sha256}'', '''') '
        '  FROM %I.reports '
        ' WHERE tenant_id = $1 AND project_id = $2 AND report_id = $3',
        TG_TABLE_SCHEMA
    )
    INTO
        stored_report_version,
        stored_report_run_id,
        stored_report_content_sha256
    USING NEW.tenant_id, NEW.project_id, NEW.report_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'normalized report lineage is missing'
            USING ERRCODE = '23503';
    END IF;
    IF stored_report_version IS DISTINCT FROM NEW.report_version
       OR stored_report_run_id IS DISTINCT FROM NEW.orchestration_run_id
       OR stored_report_content_sha256 IS DISTINCT FROM NEW.content_sha256 THEN
        RAISE EXCEPTION
            'report publication does not match the normalized report seal'
            USING ERRCODE = '23514';
    END IF;

    EXECUTE format(
        'SELECT NULLIF(payload_json->>''project_id'', ''''), '
        '       NULLIF(payload_json->>''run_id'', ''''), '
        '       NULLIF(payload_json->>''approvalStatus'', ''''), '
        '       COALESCE('
        '           NULLIF(payload_json #>> ''{approval,approved_by}'', ''''), '
        '           NULLIF(payload_json #>> ''{approval,approvedBy}'', '''')'
        '       ), '
        '       COALESCE('
        '           NULLIF(payload_json->>''user_id'', ''''), '
        '           NULLIF(payload_json->>''requestedBy'', ''''), '
        '           NULLIF(payload_json->>''requested_by'', ''''), '
        '           NULLIF(payload_json #>> ''{options,requestedBy}'', ''''), '
        '           NULLIF(payload_json #>> ''{options,requested_by}'', '''')'
        '       ) '
        '  FROM %I.orchestrations '
        ' WHERE tenant_id = $1 AND project_id = $2 AND run_id = $3',
        TG_TABLE_SCHEMA
    )
    INTO
        stored_run_project_id,
        stored_run_id,
        stored_run_approval_status,
        stored_run_approver,
        stored_run_maker
    USING NEW.tenant_id, NEW.project_id, NEW.orchestration_run_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'normalized approved orchestration is missing'
            USING ERRCODE = '23503';
    END IF;
    IF stored_run_project_id IS DISTINCT FROM NEW.project_id
       OR stored_run_id IS DISTINCT FROM NEW.orchestration_run_id
       OR lower(COALESCE(stored_run_approval_status, '')) <> 'approved'
       OR stored_run_approver IS NULL
       OR NEW.decision_package_json->>'approvedBy'
            IS DISTINCT FROM stored_run_approver THEN
        RAISE EXCEPTION
            'report publication requires the exact normalized approved orchestration'
            USING ERRCODE = '55000';
    END IF;
    IF stored_run_maker IS NOT NULL
       AND lower(stored_run_maker) NOT IN ('system', 'anonymous', 'unknown')
       THEN
        IF lower(stored_run_maker) = lower(stored_run_approver) THEN
            RAISE EXCEPTION
                'orchestration maker and approver must be different principals'
                USING ERRCODE = '55000';
        END IF;
        IF lower(stored_run_maker) = lower(NEW.published_by) THEN
            RAISE EXCEPTION
                'report publication maker and checker must be different principals'
                USING ERRCODE = '55000';
        END IF;
    END IF;

    EXECUTE format(
        'SELECT NULLIF(payload_json->>''projectId'', ''''), '
        '       NULLIF(payload_json->>''runId'', ''''), '
        '       NULLIF(payload_json->>''snapshotHash'', '''') '
        '  FROM %I.project_snapshots '
        ' WHERE tenant_id = $1 AND project_id = $2 AND snapshot_id = $3',
        TG_TABLE_SCHEMA
    )
    INTO
        stored_snapshot_project_id,
        stored_snapshot_run_id,
        stored_snapshot_hash
    USING NEW.tenant_id, NEW.project_id, NEW.project_snapshot_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'normalized project snapshot lineage is missing'
            USING ERRCODE = '23503';
    END IF;
    IF stored_snapshot_project_id IS DISTINCT FROM NEW.project_id
       OR stored_snapshot_run_id IS DISTINCT FROM NEW.orchestration_run_id
       OR stored_snapshot_hash IS DISTINCT FROM NEW.project_snapshot_hash THEN
        RAISE EXCEPTION
            'report publication does not match the normalized project snapshot seal'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$realytics_report_publication_validate$;

REVOKE ALL ON FUNCTION report_publication_validate_insert()
    FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS trg_report_publications_validate_insert
    ON report_publications;
CREATE TRIGGER trg_report_publications_validate_insert
    BEFORE INSERT ON report_publications
    FOR EACH ROW
    EXECUTE FUNCTION report_publication_validate_insert();

ALTER TABLE report_publications ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_publications FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_scope_report_publications
    ON report_publications;
CREATE POLICY tenant_scope_report_publications
    ON report_publications
    AS PERMISSIVE
    USING (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    )
    WITH CHECK (
        tenant_id = (
            SELECT NULLIF(
                pg_catalog.current_setting('realytics.tenant_id', true),
                ''
            )
        )
    );

CREATE OR REPLACE FUNCTION report_publication_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $realytics_report_publication_immutable$
BEGIN
    RAISE EXCEPTION
        'report_publications is append-only; create a new report version instead'
        USING ERRCODE = '55000';
END;
$realytics_report_publication_immutable$;

REVOKE ALL ON FUNCTION report_publication_reject_mutation()
    FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS trg_report_publications_immutable
    ON report_publications;
CREATE TRIGGER trg_report_publications_immutable
    BEFORE UPDATE OR DELETE ON report_publications
    FOR EACH ROW
    EXECUTE FUNCTION report_publication_reject_mutation();

CREATE OR REPLACE FUNCTION published_report_reject_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $realytics_published_report_immutable$
DECLARE
    publication_exists BOOLEAN;
BEGIN
    EXECUTE format(
        'SELECT EXISTS ('
        '    SELECT 1 FROM %I.report_publications '
        '     WHERE tenant_id = $1 AND project_id = $2 AND report_id = $3'
        ')',
        TG_TABLE_SCHEMA
    )
    INTO publication_exists
    USING OLD.tenant_id, OLD.project_id, OLD.report_id;

    IF publication_exists THEN
        RAISE EXCEPTION
            'published report is immutable; create a new report version instead'
            USING ERRCODE = '55000';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$realytics_published_report_immutable$;

REVOKE ALL ON FUNCTION published_report_reject_mutation()
    FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS trg_published_reports_immutable ON reports;
CREATE TRIGGER trg_published_reports_immutable
    BEFORE UPDATE OR DELETE ON reports
    FOR EACH ROW
    EXECUTE FUNCTION published_report_reject_mutation();

COMMENT ON TABLE report_publications IS
    'Immutable normalized authority for final PF report publication; JSON draft status alone is non-authoritative.';
COMMENT ON COLUMN report_publications.idempotency_key IS
    'Canonical SHA-256 over report identity, content seal, and decision-package seal.';
