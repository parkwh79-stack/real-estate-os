-- Fix authoritative report publication lineage lookup semantics.
--
-- PL/pgSQL dynamic EXECUTE does not update the special FOUND variable.  The
-- original trigger therefore treated every valid dynamic lookup as missing.
-- Capture ROW_COUNT explicitly after each EXECUTE so the fail-closed checks
-- distinguish a missing normalized record from a valid, sealed record.

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
    matched_rows BIGINT;
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
    GET DIAGNOSTICS matched_rows = ROW_COUNT;

    IF matched_rows <> 1 THEN
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
    GET DIAGNOSTICS matched_rows = ROW_COUNT;

    IF matched_rows <> 1 THEN
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
    GET DIAGNOSTICS matched_rows = ROW_COUNT;

    IF matched_rows <> 1 THEN
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
