-- Verified-principal onboarding and governed tenant access requests.
--
-- These private-schema tables are never browser/Data API surfaces.  Before a
-- tenant membership exists, FastAPI sets only transaction-local verified
-- ``issuer + subject`` values through the API runtime pool.  FORCE RLS then
-- limits a principal to their own profile/request rows.  Tenant administrators
-- and explicitly provisioned platform operators receive review visibility only
-- through database-backed predicates; JWT/user metadata is not consulted.

CREATE TABLE IF NOT EXISTS onboarding_profiles (
    issuer                 TEXT NOT NULL,
    subject                TEXT NOT NULL,
    contact_email          TEXT,
    display_name           TEXT NOT NULL,
    phone                  TEXT,
    job_title              TEXT,
    terms_version          TEXT NOT NULL,
    privacy_version        TEXT NOT NULL,
    terms_accepted_at      TIMESTAMPTZ NOT NULL,
    privacy_accepted_at    TIMESTAMPTZ NOT NULL,
    profile_version        BIGINT NOT NULL DEFAULT 1,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (issuer, subject),
    CONSTRAINT ck_onboarding_profile_issuer_nonblank
        CHECK (btrim(issuer) <> ''),
    CONSTRAINT ck_onboarding_profile_subject_nonblank
        CHECK (btrim(subject) <> ''),
    CONSTRAINT ck_onboarding_profile_display_name_nonblank
        CHECK (btrim(display_name) <> ''),
    CONSTRAINT ck_onboarding_profile_terms_version_nonblank
        CHECK (btrim(terms_version) <> ''),
    CONSTRAINT ck_onboarding_profile_privacy_version_nonblank
        CHECK (btrim(privacy_version) <> ''),
    CONSTRAINT ck_onboarding_profile_version_positive
        CHECK (profile_version > 0),
    CONSTRAINT ck_onboarding_profile_timestamp_order
        CHECK (
            created_at <= updated_at
            AND terms_accepted_at <= updated_at
            AND privacy_accepted_at <= updated_at
        )
);

CREATE TABLE IF NOT EXISTS platform_onboarding_operators (
    issuer             TEXT NOT NULL,
    subject            TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active',
    operator_version   BIGINT NOT NULL DEFAULT 1,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (issuer, subject),
    CONSTRAINT fk_platform_onboarding_operator_principal
        FOREIGN KEY (issuer, subject)
        REFERENCES principals(issuer, subject) ON DELETE RESTRICT,
    CONSTRAINT ck_platform_onboarding_operator_status
        CHECK (status IN ('active', 'inactive', 'revoked')),
    CONSTRAINT ck_platform_onboarding_operator_version_positive
        CHECK (operator_version > 0),
    CONSTRAINT ck_platform_onboarding_operator_timestamp_order
        CHECK (created_at <= updated_at)
);

CREATE TABLE IF NOT EXISTS onboarding_principal_events (
    event_id        TEXT PRIMARY KEY,
    issuer          TEXT NOT NULL,
    subject         TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    event_payload   JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_onboarding_principal_event_identity_nonblank
        CHECK (btrim(issuer) <> '' AND btrim(subject) <> ''),
    CONSTRAINT ck_onboarding_principal_event_type
        CHECK (event_type IN ('onboarding_profile_saved')),
    CONSTRAINT ck_onboarding_principal_event_payload_object
        CHECK (jsonb_typeof(event_payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_onboarding_principal_events_identity
    ON onboarding_principal_events (
        issuer, subject, occurred_at DESC, event_id
    );

CREATE TABLE IF NOT EXISTS tenant_access_requests (
    request_id                     TEXT PRIMARY KEY,
    request_type                   TEXT NOT NULL,
    requester_issuer               TEXT NOT NULL,
    requester_subject              TEXT NOT NULL,
    requester_email_snapshot       TEXT,
    requester_display_name_snapshot TEXT NOT NULL,
    profile_version_snapshot       BIGINT NOT NULL,
    terms_version_snapshot         TEXT NOT NULL,
    privacy_version_snapshot       TEXT NOT NULL,
    profile_snapshot_hash          TEXT NOT NULL,
    target_tenant_id               TEXT,
    organization_name              TEXT,
    business_registration_number   TEXT,
    requested_role                 TEXT NOT NULL DEFAULT 'analyst',
    request_message                TEXT,
    status                         TEXT NOT NULL DEFAULT 'pending',
    request_version                BIGINT NOT NULL DEFAULT 1,
    submitted_at                   TIMESTAMPTZ NOT NULL,
    decision_by_issuer             TEXT,
    decision_by_subject            TEXT,
    decision_at                    TIMESTAMPTZ,
    decision_reason                TEXT,
    approved_tenant_id             TEXT,
    created_at                     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at                     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_tenant_access_request_target
        FOREIGN KEY (target_tenant_id)
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_tenant_access_request_approved_tenant
        FOREIGN KEY (approved_tenant_id)
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    CONSTRAINT ck_tenant_access_request_id_nonblank
        CHECK (btrim(request_id) <> ''),
    CONSTRAINT ck_tenant_access_request_type
        CHECK (request_type IN ('existing_organization', 'new_organization')),
    CONSTRAINT ck_tenant_access_request_requester_nonblank
        CHECK (
            btrim(requester_issuer) <> ''
            AND btrim(requester_subject) <> ''
        ),
    CONSTRAINT ck_tenant_access_request_profile_snapshot
        CHECK (
            btrim(requester_display_name_snapshot) <> ''
            AND profile_version_snapshot > 0
            AND btrim(terms_version_snapshot) <> ''
            AND btrim(privacy_version_snapshot) <> ''
            AND profile_snapshot_hash ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_tenant_access_request_target_shape
        CHECK (
            (
                request_type = 'existing_organization'
                AND target_tenant_id IS NOT NULL
                AND btrim(target_tenant_id) <> ''
                AND organization_name IS NULL
                AND business_registration_number IS NULL
            )
            OR
            (
                request_type = 'new_organization'
                AND target_tenant_id IS NULL
                AND organization_name IS NOT NULL
                AND btrim(organization_name) <> ''
                AND business_registration_number IS NOT NULL
                AND business_registration_number ~ '^[0-9]{10}$'
            )
        ),
    CONSTRAINT ck_tenant_access_request_role
        CHECK (requested_role IN ('analyst', 'viewer')),
    CONSTRAINT ck_tenant_access_request_status
        CHECK (status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT ck_tenant_access_request_version_positive
        CHECK (request_version > 0),
    CONSTRAINT ck_tenant_access_request_decision_shape
        CHECK (
            (
                status = 'pending'
                AND decision_by_issuer IS NULL
                AND decision_by_subject IS NULL
                AND decision_at IS NULL
                AND decision_reason IS NULL
                AND approved_tenant_id IS NULL
            )
            OR
            (
                status = 'rejected'
                AND decision_by_issuer IS NOT NULL
                AND decision_by_subject IS NOT NULL
                AND decision_at IS NOT NULL
                AND decision_reason IS NOT NULL
                AND btrim(decision_reason) <> ''
                AND approved_tenant_id IS NULL
            )
            OR
            (
                status = 'approved'
                AND decision_by_issuer IS NOT NULL
                AND decision_by_subject IS NOT NULL
                AND decision_at IS NOT NULL
                AND decision_reason IS NOT NULL
                AND btrim(decision_reason) <> ''
                AND approved_tenant_id IS NOT NULL
            )
        ),
    CONSTRAINT ck_tenant_access_request_timestamp_order
        CHECK (
            created_at <= updated_at
            AND submitted_at <= updated_at
            AND (decision_at IS NULL OR decision_at >= submitted_at)
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_access_request_pending_existing
    ON tenant_access_requests (
        requester_issuer, requester_subject, target_tenant_id
    )
    WHERE status = 'pending' AND request_type = 'existing_organization';

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_access_request_pending_new
    ON tenant_access_requests (
        requester_issuer, requester_subject, lower(btrim(organization_name))
    )
    WHERE status = 'pending' AND request_type = 'new_organization';

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_access_request_pending_principal
    ON tenant_access_requests (requester_issuer, requester_subject)
    WHERE status = 'pending';

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_access_request_business_active
    ON tenant_access_requests (business_registration_number)
    WHERE request_type = 'new_organization'
      AND status IN ('pending', 'approved');

CREATE INDEX IF NOT EXISTS idx_tenant_access_request_review_queue
    ON tenant_access_requests (
        status, request_type, target_tenant_id, submitted_at, request_id
    );

CREATE TABLE IF NOT EXISTS tenant_access_request_events (
    event_id               TEXT PRIMARY KEY,
    request_id             TEXT NOT NULL,
    event_type             TEXT NOT NULL,
    request_type           TEXT NOT NULL,
    requester_issuer       TEXT NOT NULL,
    requester_subject      TEXT NOT NULL,
    target_tenant_id       TEXT,
    actor_issuer           TEXT NOT NULL,
    actor_subject          TEXT NOT NULL,
    event_payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at            TIMESTAMPTZ NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT fk_tenant_access_request_event_request
        FOREIGN KEY (request_id)
        REFERENCES tenant_access_requests(request_id) ON DELETE RESTRICT,
    CONSTRAINT ck_tenant_access_request_event_type
        CHECK (
            event_type IN (
                'request_submitted', 'request_resubmitted',
                'request_approved', 'request_rejected', 'tenant_created',
                'principal_registered', 'membership_created'
            )
        ),
    CONSTRAINT ck_tenant_access_request_event_request_type
        CHECK (request_type IN ('existing_organization', 'new_organization')),
    CONSTRAINT ck_tenant_access_request_event_principals_nonblank
        CHECK (
            btrim(requester_issuer) <> ''
            AND btrim(requester_subject) <> ''
            AND btrim(actor_issuer) <> ''
            AND btrim(actor_subject) <> ''
        ),
    CONSTRAINT ck_tenant_access_request_event_payload_object
        CHECK (jsonb_typeof(event_payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_tenant_access_request_events_request
    ON tenant_access_request_events (request_id, occurred_at, event_id);

-- Principal-owned profile rows.
ALTER TABLE onboarding_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS onboarding_profile_principal_scope
    ON onboarding_profiles;
CREATE POLICY onboarding_profile_principal_scope
    ON onboarding_profiles AS PERMISSIVE
    USING (
        issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
    )
    WITH CHECK (
        issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
    );

ALTER TABLE onboarding_principal_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE onboarding_principal_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS onboarding_principal_event_scope
    ON onboarding_principal_events;
CREATE POLICY onboarding_principal_event_scope
    ON onboarding_principal_events AS PERMISSIVE
    USING (
        issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
    )
    WITH CHECK (
        issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
    );

-- Operators can inspect only their own active operator record.  Request queue
-- policies below use the same transaction-local principal pair.
ALTER TABLE platform_onboarding_operators ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_onboarding_operators FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS platform_onboarding_operator_self_scope
    ON platform_onboarding_operators;
CREATE POLICY platform_onboarding_operator_self_scope
    ON platform_onboarding_operators AS PERMISSIVE
    FOR SELECT
    USING (
        issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
    );

ALTER TABLE tenant_access_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_access_requests FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_access_request_governed_scope
    ON tenant_access_requests;
DROP POLICY IF EXISTS tenant_access_request_select_scope
    ON tenant_access_requests;
CREATE POLICY tenant_access_request_select_scope
    ON tenant_access_requests AS PERMISSIVE
    FOR SELECT
    USING (
        (
            requester_issuer = NULLIF(
                pg_catalog.current_setting(
                    'realytics.principal_issuer', true
                ), ''
            )
            AND requester_subject = NULLIF(
                pg_catalog.current_setting(
                    'realytics.principal_subject', true
                ), ''
            )
        )
        OR EXISTS (
            SELECT 1
              FROM tenant_memberships AS membership
             WHERE membership.tenant_id = target_tenant_id
               AND membership.issuer = NULLIF(
                    pg_catalog.current_setting(
                        'realytics.principal_issuer', true
                    ), ''
               )
               AND membership.subject = NULLIF(
                    pg_catalog.current_setting(
                        'realytics.principal_subject', true
                    ), ''
               )
               AND membership.role = 'admin'
               AND membership.status = 'active'
        )
        OR (
            request_type = 'new_organization'
            AND EXISTS (
                SELECT 1
                  FROM platform_onboarding_operators AS operator
                 WHERE operator.issuer = NULLIF(
                        pg_catalog.current_setting(
                            'realytics.principal_issuer', true
                        ), ''
                 )
                   AND operator.subject = NULLIF(
                        pg_catalog.current_setting(
                            'realytics.principal_subject', true
                        ), ''
                 )
                   AND operator.status = 'active'
            )
        )
    );

DROP POLICY IF EXISTS tenant_access_request_principal_insert
    ON tenant_access_requests;
CREATE POLICY tenant_access_request_principal_insert
    ON tenant_access_requests AS PERMISSIVE
    FOR INSERT
    WITH CHECK (
        requester_issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND requester_subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
        AND status = 'pending'
        AND request_version = 1
        AND decision_by_issuer IS NULL
        AND decision_by_subject IS NULL
        AND decision_at IS NULL
        AND decision_reason IS NULL
        AND approved_tenant_id IS NULL
    );

DROP POLICY IF EXISTS tenant_access_request_principal_resubmit
    ON tenant_access_requests;
CREATE POLICY tenant_access_request_principal_resubmit
    ON tenant_access_requests AS PERMISSIVE
    FOR UPDATE
    USING (
        requester_issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND requester_subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
        AND status = 'rejected'
    )
    WITH CHECK (
        requester_issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND requester_subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
        AND status = 'pending'
        AND decision_by_issuer IS NULL
        AND decision_by_subject IS NULL
        AND decision_at IS NULL
        AND decision_reason IS NULL
        AND approved_tenant_id IS NULL
    );

DROP POLICY IF EXISTS tenant_access_request_reviewer_decision
    ON tenant_access_requests;
CREATE POLICY tenant_access_request_reviewer_decision
    ON tenant_access_requests AS PERMISSIVE
    FOR UPDATE
    USING (
        status = 'pending'
        AND (
            EXISTS (
                SELECT 1
                  FROM tenant_memberships AS membership
                 WHERE membership.tenant_id = target_tenant_id
                   AND membership.issuer = NULLIF(
                        pg_catalog.current_setting(
                            'realytics.principal_issuer', true
                        ), ''
                   )
                   AND membership.subject = NULLIF(
                        pg_catalog.current_setting(
                            'realytics.principal_subject', true
                        ), ''
                   )
                   AND membership.role = 'admin'
                   AND membership.status = 'active'
            )
            OR (
                request_type = 'new_organization'
                AND EXISTS (
                    SELECT 1
                      FROM platform_onboarding_operators AS operator
                     WHERE operator.issuer = NULLIF(
                            pg_catalog.current_setting(
                                'realytics.principal_issuer', true
                            ), ''
                     )
                       AND operator.subject = NULLIF(
                            pg_catalog.current_setting(
                                'realytics.principal_subject', true
                            ), ''
                     )
                       AND operator.status = 'active'
                )
            )
        )
    )
    WITH CHECK (
        status IN ('approved', 'rejected')
        AND decision_by_issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND decision_by_subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
        AND NOT (
            requester_issuer = decision_by_issuer
            AND requester_subject = decision_by_subject
        )
        AND (
            EXISTS (
                SELECT 1
                  FROM tenant_memberships AS membership
                 WHERE membership.tenant_id = target_tenant_id
                   AND membership.issuer = decision_by_issuer
                   AND membership.subject = decision_by_subject
                   AND membership.role = 'admin'
                   AND membership.status = 'active'
            )
            OR (
                request_type = 'new_organization'
                AND EXISTS (
                    SELECT 1
                      FROM platform_onboarding_operators AS operator
                     WHERE operator.issuer = decision_by_issuer
                       AND operator.subject = decision_by_subject
                       AND operator.status = 'active'
                )
            )
        )
    );

ALTER TABLE tenant_access_request_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_access_request_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_access_request_event_governed_scope
    ON tenant_access_request_events;
DROP POLICY IF EXISTS tenant_access_request_event_select_scope
    ON tenant_access_request_events;
CREATE POLICY tenant_access_request_event_select_scope
    ON tenant_access_request_events AS PERMISSIVE
    FOR SELECT
    USING (
        (
            requester_issuer = NULLIF(
                pg_catalog.current_setting(
                    'realytics.principal_issuer', true
                ), ''
            )
            AND requester_subject = NULLIF(
                pg_catalog.current_setting(
                    'realytics.principal_subject', true
                ), ''
            )
        )
        OR EXISTS (
            SELECT 1
              FROM tenant_memberships AS membership
             WHERE membership.tenant_id = target_tenant_id
               AND membership.issuer = NULLIF(
                    pg_catalog.current_setting(
                        'realytics.principal_issuer', true
                    ), ''
               )
               AND membership.subject = NULLIF(
                    pg_catalog.current_setting(
                        'realytics.principal_subject', true
                    ), ''
               )
               AND membership.role = 'admin'
               AND membership.status = 'active'
        )
        OR (
            request_type = 'new_organization'
            AND EXISTS (
                SELECT 1
                  FROM platform_onboarding_operators AS operator
                 WHERE operator.issuer = NULLIF(
                        pg_catalog.current_setting(
                            'realytics.principal_issuer', true
                        ), ''
                 )
                   AND operator.subject = NULLIF(
                        pg_catalog.current_setting(
                            'realytics.principal_subject', true
                        ), ''
                 )
                   AND operator.status = 'active'
            )
        )
    );

DROP POLICY IF EXISTS tenant_access_request_event_principal_insert
    ON tenant_access_request_events;
CREATE POLICY tenant_access_request_event_principal_insert
    ON tenant_access_request_events AS PERMISSIVE
    FOR INSERT
    WITH CHECK (
        actor_issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND actor_subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
        AND requester_issuer = actor_issuer
        AND requester_subject = actor_subject
        AND event_type IN ('request_submitted', 'request_resubmitted')
    );

DROP POLICY IF EXISTS tenant_access_request_event_reviewer_insert
    ON tenant_access_request_events;
CREATE POLICY tenant_access_request_event_reviewer_insert
    ON tenant_access_request_events AS PERMISSIVE
    FOR INSERT
    WITH CHECK (
        actor_issuer = NULLIF(
            pg_catalog.current_setting('realytics.principal_issuer', true), ''
        )
        AND actor_subject = NULLIF(
            pg_catalog.current_setting('realytics.principal_subject', true), ''
        )
        AND NOT (
            requester_issuer = actor_issuer
            AND requester_subject = actor_subject
        )
        AND event_type IN (
            'request_approved', 'request_rejected', 'tenant_created',
            'principal_registered', 'membership_created'
        )
        AND (
            EXISTS (
                SELECT 1
                  FROM tenant_memberships AS membership
                 WHERE membership.tenant_id = target_tenant_id
                   AND membership.issuer = actor_issuer
                   AND membership.subject = actor_subject
                   AND membership.role = 'admin'
                   AND membership.status = 'active'
            )
            OR (
                request_type = 'new_organization'
                AND EXISTS (
                    SELECT 1
                      FROM platform_onboarding_operators AS operator
                     WHERE operator.issuer = actor_issuer
                       AND operator.subject = actor_subject
                       AND operator.status = 'active'
                )
            )
        )
    );

-- An event is accepted only when its duplicated lineage is byte-for-byte
-- consistent with the authoritative request row.  Decision-side events are
-- intentionally impossible until the request CAS has reached its terminal
-- status, preventing an append-only event from claiming an approval that did
-- not commit.
CREATE OR REPLACE FUNCTION onboarding_validate_request_event_lineage()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $realytics_onboarding_event_lineage$
DECLARE
    stored_request_type TEXT;
    stored_requester_issuer TEXT;
    stored_requester_subject TEXT;
    stored_target_tenant_id TEXT;
    stored_status TEXT;
    stored_decision_by_issuer TEXT;
    stored_decision_by_subject TEXT;
    stored_approved_tenant_id TEXT;
    lineage_row_count BIGINT;
BEGIN
    EXECUTE format(
        'SELECT request_type, requester_issuer, requester_subject, '
        '       target_tenant_id, status, decision_by_issuer, '
        '       decision_by_subject, approved_tenant_id '
        '  FROM %I.tenant_access_requests '
        ' WHERE request_id = $1',
        TG_TABLE_SCHEMA
    )
    INTO
        stored_request_type,
        stored_requester_issuer,
        stored_requester_subject,
        stored_target_tenant_id,
        stored_status,
        stored_decision_by_issuer,
        stored_decision_by_subject,
        stored_approved_tenant_id
    USING NEW.request_id;

    GET DIAGNOSTICS lineage_row_count = ROW_COUNT;
    IF lineage_row_count <> 1 THEN
        RAISE EXCEPTION 'tenant access request event parent is missing'
            USING ERRCODE = '23503',
                  CONSTRAINT = 'fk_tenant_access_request_event_request';
    END IF;

    IF NEW.request_type IS DISTINCT FROM stored_request_type
       OR NEW.requester_issuer IS DISTINCT FROM stored_requester_issuer
       OR NEW.requester_subject IS DISTINCT FROM stored_requester_subject THEN
        RAISE EXCEPTION 'tenant access request event lineage is invalid'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_tenant_access_request_event_lineage';
    END IF;

    IF NEW.event_type IN ('request_submitted', 'request_resubmitted') THEN
        IF stored_status <> 'pending'
           OR NEW.actor_issuer IS DISTINCT FROM stored_requester_issuer
           OR NEW.actor_subject IS DISTINCT FROM stored_requester_subject
           OR NEW.target_tenant_id IS DISTINCT FROM stored_target_tenant_id THEN
            RAISE EXCEPTION 'tenant access request self event is invalid'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_tenant_access_request_self_event';
        END IF;
    ELSIF NEW.event_type = 'request_rejected' THEN
        IF stored_status <> 'rejected'
           OR NEW.actor_issuer IS DISTINCT FROM stored_decision_by_issuer
           OR NEW.actor_subject IS DISTINCT FROM stored_decision_by_subject
           OR NEW.target_tenant_id IS DISTINCT FROM stored_target_tenant_id THEN
            RAISE EXCEPTION 'tenant access request rejection event is invalid'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_tenant_access_request_rejection_event';
        END IF;
    ELSE
        IF stored_status <> 'approved'
           OR NEW.actor_issuer IS DISTINCT FROM stored_decision_by_issuer
           OR NEW.actor_subject IS DISTINCT FROM stored_decision_by_subject
           OR NEW.target_tenant_id IS DISTINCT FROM stored_approved_tenant_id THEN
            RAISE EXCEPTION 'tenant access request approval event is invalid'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_tenant_access_request_approval_event';
        END IF;
    END IF;
    RETURN NEW;
END;
$realytics_onboarding_event_lineage$;

REVOKE ALL ON FUNCTION onboarding_validate_request_event_lineage()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_tenant_access_request_event_lineage
    ON tenant_access_request_events;
CREATE TRIGGER trg_tenant_access_request_event_lineage
    BEFORE INSERT ON tenant_access_request_events
    FOR EACH ROW EXECUTE FUNCTION onboarding_validate_request_event_lineage();

CREATE OR REPLACE FUNCTION onboarding_reject_append_only_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $realytics_onboarding_append_only$
BEGIN
    RAISE EXCEPTION
        'tenant_access_request_events is append-only; create a new event'
        USING ERRCODE = '55000';
END;
$realytics_onboarding_append_only$;

REVOKE ALL ON FUNCTION onboarding_reject_append_only_mutation() FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_tenant_access_request_events_append_only
    ON tenant_access_request_events;
CREATE TRIGGER trg_tenant_access_request_events_append_only
    BEFORE UPDATE OR DELETE ON tenant_access_request_events
    FOR EACH ROW EXECUTE FUNCTION onboarding_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_onboarding_principal_events_append_only
    ON onboarding_principal_events;
CREATE TRIGGER trg_onboarding_principal_events_append_only
    BEFORE UPDATE OR DELETE ON onboarding_principal_events
    FOR EACH ROW EXECUTE FUNCTION onboarding_reject_append_only_mutation();

-- The identity role is the only deployable pre-membership runtime in the
-- current environment.  Give it exact self-service columns; review authority,
-- tenant creation, principal registration and membership creation remain on
-- the separately provisioned API role.
REVOKE ALL PRIVILEGES ON TABLE onboarding_profiles
    FROM realytics_identity;
GRANT SELECT (
    issuer, subject, contact_email, display_name, phone, job_title,
    terms_version, privacy_version, terms_accepted_at, privacy_accepted_at,
    profile_version, updated_at
) ON TABLE onboarding_profiles TO realytics_identity;
GRANT INSERT (
    issuer, subject, contact_email, display_name, phone, job_title,
    terms_version, privacy_version, terms_accepted_at, privacy_accepted_at,
    profile_version, created_at, updated_at
) ON TABLE onboarding_profiles TO realytics_identity;
GRANT UPDATE (
    contact_email, display_name, phone, job_title, terms_version,
    privacy_version, terms_accepted_at, privacy_accepted_at,
    profile_version, updated_at
) ON TABLE onboarding_profiles TO realytics_identity;

REVOKE ALL PRIVILEGES ON TABLE onboarding_principal_events
    FROM realytics_identity;
GRANT INSERT (
    event_id, issuer, subject, event_type, event_payload, occurred_at
) ON TABLE onboarding_principal_events TO realytics_identity;

REVOKE ALL PRIVILEGES ON TABLE platform_onboarding_operators
    FROM realytics_identity;
GRANT SELECT (issuer, subject, status)
    ON TABLE platform_onboarding_operators TO realytics_identity;

REVOKE ALL PRIVILEGES ON TABLE tenant_access_requests
    FROM realytics_identity;
GRANT SELECT (
    request_id, request_type, requester_issuer, requester_subject,
    requester_email_snapshot, requester_display_name_snapshot,
    profile_version_snapshot, terms_version_snapshot,
    privacy_version_snapshot, profile_snapshot_hash,
    target_tenant_id, organization_name, business_registration_number,
    requested_role, request_message, status, request_version, submitted_at,
    decision_by_issuer, decision_by_subject, decision_at, decision_reason,
    approved_tenant_id, created_at, updated_at
) ON TABLE tenant_access_requests TO realytics_identity;
GRANT INSERT (
    request_id, request_type, requester_issuer, requester_subject,
    requester_email_snapshot, requester_display_name_snapshot,
    profile_version_snapshot, terms_version_snapshot,
    privacy_version_snapshot, profile_snapshot_hash,
    target_tenant_id, organization_name, business_registration_number,
    requested_role, request_message, status, request_version, submitted_at,
    created_at, updated_at
) ON TABLE tenant_access_requests TO realytics_identity;
GRANT UPDATE (
    requester_email_snapshot, requester_display_name_snapshot,
    profile_version_snapshot, terms_version_snapshot,
    privacy_version_snapshot, profile_snapshot_hash,
    organization_name, business_registration_number, requested_role,
    request_message, status, request_version, submitted_at,
    decision_by_issuer, decision_by_subject, decision_at, decision_reason,
    approved_tenant_id, updated_at
) ON TABLE tenant_access_requests TO realytics_identity;

REVOKE ALL PRIVILEGES ON TABLE tenant_access_request_events
    FROM realytics_identity;
GRANT INSERT (
    event_id, request_id, event_type, request_type,
    requester_issuer, requester_subject, target_tenant_id,
    actor_issuer, actor_subject, event_payload, occurred_at
) ON TABLE tenant_access_request_events TO realytics_identity;

-- Migration 20260728031948 intentionally hid descriptive tenant columns from
-- the identity role.  Verified onboarding now needs only the organization
-- name for bounded lookup and status rendering; metadata remains unavailable.
GRANT SELECT (tenant_id, name, status)
    ON TABLE tenants TO realytics_identity;

COMMENT ON TABLE onboarding_profiles IS
    'Verified-principal profile and explicit versioned terms/privacy consent; not an authorization source.';
COMMENT ON TABLE platform_onboarding_operators IS
    'Migration-owner-provisioned authority for approving new tenant creation.';
COMMENT ON TABLE onboarding_principal_events IS
    'Append-only verified-principal profile and consent audit without token or provider payload.';
COMMENT ON TABLE tenant_access_requests IS
    'CAS-governed access requests with immutable submitted profile/consent snapshot; approval creates normalized authority atomically.';
COMMENT ON TABLE tenant_access_request_events IS
    'Append-only onboarding request submission, resubmission, and decision audit.';
