-- REALYTICS Legal Intelligence governance hardening
--
-- This migration deliberately extends, rather than rewrites, 0008.  The
-- migration ledger checksum for an already-applied corpus schema must remain
-- immutable.

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;

-- ---------------------------------------------------------------------------
-- Citator review integrity
-- ---------------------------------------------------------------------------

ALTER TABLE legal_citations
    ADD COLUMN IF NOT EXISTS reviewed_by TEXT,
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS evidence_excerpt TEXT,
    ADD COLUMN IF NOT EXISTS supersedes_citation_id TEXT;

ALTER TABLE legal_citations
    ALTER COLUMN citation_status SET DEFAULT 'unknown';

ALTER TABLE legal_citations
    DROP CONSTRAINT IF EXISTS ck_legal_citation_status;
ALTER TABLE legal_citations
    ADD CONSTRAINT ck_legal_citation_status
        CHECK (citation_status IN (
            'unknown',
            'unresolved',
            'ambiguous',
            'resolved',
            'verified',
            'rejected',
            'superseded'
        ));

ALTER TABLE legal_citations
    DROP CONSTRAINT IF EXISTS ck_legal_citation_verified_review;
ALTER TABLE legal_citations
    ADD CONSTRAINT ck_legal_citation_verified_review
        CHECK (
            citation_status <> 'verified'
            OR (
                human_review_status = 'approved'
                AND NULLIF(btrim(reviewed_by), '') IS NOT NULL
                AND reviewed_at IS NOT NULL
                AND NULLIF(btrim(evidence_excerpt), '') IS NOT NULL
                AND evidence_excerpt_sha256 IS NOT NULL
            )
        ) NOT VALID;

ALTER TABLE legal_citations
    DROP CONSTRAINT IF EXISTS ck_legal_citation_review_actor;
ALTER TABLE legal_citations
    ADD CONSTRAINT ck_legal_citation_review_actor
        CHECK (
            human_review_status NOT IN ('approved', 'rejected')
            OR (
                NULLIF(btrim(reviewed_by), '') IS NOT NULL
                AND reviewed_at IS NOT NULL
            )
        ) NOT VALID;

ALTER TABLE legal_citations
    DROP CONSTRAINT IF EXISTS ck_legal_citation_review_status_exact;
ALTER TABLE legal_citations
    ADD CONSTRAINT ck_legal_citation_review_status_exact
        CHECK (
            NULLIF(btrim(metadata_json ->> 'reviewStatus'), '') IS NULL
            OR (
                metadata_json ->> 'reviewStatus' = 'approved'
                AND citation_status = 'verified'
                AND human_review_status = 'approved'
            )
            OR (
                metadata_json ->> 'reviewStatus' = 'rejected'
                AND citation_status = 'rejected'
                AND human_review_status = 'rejected'
            )
            OR (
                metadata_json ->> 'reviewStatus' = 'needs_revision'
                AND citation_status = 'unknown'
                AND human_review_status = 'pending'
            )
        ) NOT VALID;

ALTER TABLE legal_citations
    DROP CONSTRAINT IF EXISTS ck_legal_citation_excerpt_pair;
ALTER TABLE legal_citations
    ADD CONSTRAINT ck_legal_citation_excerpt_pair
        CHECK (
            (evidence_excerpt IS NULL AND evidence_excerpt_sha256 IS NULL)
            OR (
                NULLIF(btrim(evidence_excerpt), '') IS NOT NULL
                AND evidence_excerpt_sha256 =
                    encode(
                        extensions.digest(
                            convert_to(evidence_excerpt, 'UTF8'),
                            'sha256'
                        ),
                        'hex'
                    )
            )
        );

ALTER TABLE legal_citations
    DROP CONSTRAINT IF EXISTS fk_legal_citation_supersedes;
ALTER TABLE legal_citations
    ADD CONSTRAINT fk_legal_citation_supersedes
        FOREIGN KEY (supersedes_citation_id)
        REFERENCES legal_citations(legal_citation_id)
        ON DELETE RESTRICT;

ALTER TABLE legal_citations
    DROP CONSTRAINT IF EXISTS ck_legal_citation_not_self_superseding;
ALTER TABLE legal_citations
    ADD CONSTRAINT ck_legal_citation_not_self_superseding
        CHECK (
            supersedes_citation_id IS NULL
            OR supersedes_citation_id <> legal_citation_id
        );
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_citation_single_successor
    ON legal_citations(supersedes_citation_id)
    WHERE supersedes_citation_id IS NOT NULL;

ALTER TABLE legal_relation_assertions
    ADD COLUMN IF NOT EXISTS reviewed_by TEXT,
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS evidence_excerpt TEXT,
    ADD COLUMN IF NOT EXISTS evidence_excerpt_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS supersedes_assertion_id TEXT;

ALTER TABLE legal_relation_assertions
    ALTER COLUMN assertion_status SET DEFAULT 'unknown';

ALTER TABLE legal_relation_assertions
    DROP CONSTRAINT IF EXISTS ck_legal_relation_status;
ALTER TABLE legal_relation_assertions
    ADD CONSTRAINT ck_legal_relation_status
        CHECK (assertion_status IN (
            'unknown',
            'candidate',
            'verified',
            'rejected',
            'superseded'
        ));

ALTER TABLE legal_relation_assertions
    DROP CONSTRAINT IF EXISTS ck_legal_relation_verified_review;
ALTER TABLE legal_relation_assertions
    ADD CONSTRAINT ck_legal_relation_verified_review
        CHECK (
            assertion_status <> 'verified'
            OR (
                human_review_status = 'approved'
                AND NULLIF(btrim(reviewed_by), '') IS NOT NULL
                AND reviewed_at IS NOT NULL
                AND NULLIF(btrim(evidence_excerpt), '') IS NOT NULL
                AND evidence_excerpt_sha256 IS NOT NULL
            )
        ) NOT VALID;

ALTER TABLE legal_relation_assertions
    DROP CONSTRAINT IF EXISTS ck_legal_relation_interpretive_review;
ALTER TABLE legal_relation_assertions
    ADD CONSTRAINT ck_legal_relation_interpretive_review
        CHECK (
            relation_type NOT IN (
                'applies',
                'distinguishes',
                'follows',
                'interprets',
                'limits',
                'criticizes',
                'overrules_candidate'
            )
            OR assertion_status NOT IN ('verified')
            OR (
                human_review_status = 'approved'
                AND NULLIF(btrim(reviewed_by), '') IS NOT NULL
                AND reviewed_at IS NOT NULL
            )
        ) NOT VALID;

ALTER TABLE legal_relation_assertions
    DROP CONSTRAINT IF EXISTS ck_legal_relation_review_status_exact;
ALTER TABLE legal_relation_assertions
    ADD CONSTRAINT ck_legal_relation_review_status_exact
        CHECK (
            NULLIF(btrim(metadata_json ->> 'reviewStatus'), '') IS NULL
            OR (
                metadata_json ->> 'reviewStatus' = 'approved'
                AND assertion_status = 'verified'
                AND human_review_status = 'approved'
            )
            OR (
                metadata_json ->> 'reviewStatus' = 'rejected'
                AND assertion_status = 'rejected'
                AND human_review_status = 'rejected'
            )
            OR (
                metadata_json ->> 'reviewStatus' = 'needs_revision'
                AND assertion_status = 'unknown'
                AND human_review_status = 'pending'
            )
        ) NOT VALID;

ALTER TABLE legal_relation_assertions
    DROP CONSTRAINT IF EXISTS ck_legal_relation_excerpt_hash;
ALTER TABLE legal_relation_assertions
    ADD CONSTRAINT ck_legal_relation_excerpt_hash
        CHECK (
            evidence_excerpt_sha256 IS NULL
            OR evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
        );

ALTER TABLE legal_relation_assertions
    DROP CONSTRAINT IF EXISTS ck_legal_relation_excerpt_pair;
ALTER TABLE legal_relation_assertions
    ADD CONSTRAINT ck_legal_relation_excerpt_pair
        CHECK (
            (evidence_excerpt IS NULL AND evidence_excerpt_sha256 IS NULL)
            OR (
                NULLIF(btrim(evidence_excerpt), '') IS NOT NULL
                AND evidence_excerpt_sha256 =
                    encode(
                        extensions.digest(
                            convert_to(evidence_excerpt, 'UTF8'),
                            'sha256'
                        ),
                        'hex'
                    )
            )
        );

ALTER TABLE legal_relation_assertions
    DROP CONSTRAINT IF EXISTS fk_legal_relation_supersedes;
ALTER TABLE legal_relation_assertions
    ADD CONSTRAINT fk_legal_relation_supersedes
        FOREIGN KEY (supersedes_assertion_id)
        REFERENCES legal_relation_assertions(legal_relation_assertion_id)
        ON DELETE RESTRICT;

ALTER TABLE legal_relation_assertions
    DROP CONSTRAINT IF EXISTS ck_legal_relation_not_self_superseding;
ALTER TABLE legal_relation_assertions
    ADD CONSTRAINT ck_legal_relation_not_self_superseding
        CHECK (
            supersedes_assertion_id IS NULL
            OR supersedes_assertion_id <> legal_relation_assertion_id
        );
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_relation_single_successor
    ON legal_relation_assertions(supersedes_assertion_id)
    WHERE supersedes_assertion_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS legal_citation_review_events (
    legal_citation_review_event_id TEXT PRIMARY KEY,
    legal_citation_id              TEXT NOT NULL
        REFERENCES legal_citations(legal_citation_id) ON DELETE RESTRICT,
    review_status                  TEXT NOT NULL,
    reviewer_id                    TEXT NOT NULL,
    reviewed_at                    TIMESTAMPTZ NOT NULL,
    reason                         TEXT NOT NULL,
    evidence_excerpt_sha256        TEXT,
    supersedes_review_event_id     TEXT,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_citation_id),
    CONSTRAINT fk_legal_citation_review_supersedes
        FOREIGN KEY (supersedes_review_event_id)
        REFERENCES legal_citation_review_events(
            legal_citation_review_event_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_citation_review_status
        CHECK (review_status IN ('approved', 'rejected', 'needs_revision')),
    CONSTRAINT ck_legal_citation_review_actor
        CHECK (
            NULLIF(btrim(reviewer_id), '') IS NOT NULL
            AND NULLIF(btrim(reason), '') IS NOT NULL
        ),
    CONSTRAINT ck_legal_citation_review_excerpt_hash
        CHECK (
            evidence_excerpt_sha256 IS NULL
            OR evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_citation_review_not_self_superseding
        CHECK (
            supersedes_review_event_id IS NULL
            OR supersedes_review_event_id
                <> legal_citation_review_event_id
        ),
    CONSTRAINT ck_legal_citation_review_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_citation_review_events_citation
    ON legal_citation_review_events(legal_citation_id, reviewed_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_legal_citation_review_single_successor
    ON legal_citation_review_events(supersedes_review_event_id)
    WHERE supersedes_review_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS legal_relation_review_events (
    legal_relation_review_event_id TEXT PRIMARY KEY,
    legal_relation_assertion_id    TEXT NOT NULL
        REFERENCES legal_relation_assertions(
            legal_relation_assertion_id
        )
        ON DELETE RESTRICT,
    review_status                  TEXT NOT NULL,
    reviewer_id                    TEXT NOT NULL,
    reviewed_at                    TIMESTAMPTZ NOT NULL,
    reason                         TEXT NOT NULL,
    evidence_excerpt_sha256        TEXT,
    supersedes_review_event_id     TEXT,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_relation_assertion_id),
    CONSTRAINT fk_legal_relation_review_supersedes
        FOREIGN KEY (supersedes_review_event_id)
        REFERENCES legal_relation_review_events(
            legal_relation_review_event_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_relation_review_status
        CHECK (review_status IN ('approved', 'rejected', 'needs_revision')),
    CONSTRAINT ck_legal_relation_review_actor
        CHECK (
            NULLIF(btrim(reviewer_id), '') IS NOT NULL
            AND NULLIF(btrim(reason), '') IS NOT NULL
        ),
    CONSTRAINT ck_legal_relation_review_excerpt_hash
        CHECK (
            evidence_excerpt_sha256 IS NULL
            OR evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_relation_review_not_self_superseding
        CHECK (
            supersedes_review_event_id IS NULL
            OR supersedes_review_event_id
                <> legal_relation_review_event_id
        ),
    CONSTRAINT ck_legal_relation_review_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_relation_review_events_assertion
    ON legal_relation_review_events(
        legal_relation_assertion_id,
        reviewed_at DESC
    );
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_legal_relation_review_single_successor
    ON legal_relation_review_events(supersedes_review_event_id)
    WHERE supersedes_review_event_id IS NOT NULL;

CREATE OR REPLACE FUNCTION legal_assert_citator_review_event()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_citator_review_event$
DECLARE
    review_status TEXT;
    event_valid BOOLEAN;
    successor_valid BOOLEAN;
    review_chain_valid BOOLEAN;
    reviewed_record_id TEXT;
    delegation_id TEXT;
    delegation_valid BOOLEAN;
BEGIN
    review_status := NULLIF(
        btrim(NEW.metadata_json ->> 'reviewStatus'),
        ''
    );
    delegation_id := NULLIF(
        btrim(NEW.metadata_json ->> 'legalApprovalDelegationId'),
        ''
    );
    IF review_status IS NULL THEN
        IF TG_TABLE_NAME = 'legal_citations' THEN
            IF (
                NEW.citation_status IN ('verified', 'rejected')
                OR NEW.human_review_status IN ('approved', 'rejected')
                OR NEW.supersedes_citation_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'final Citator record requires reviewStatus and a review event'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_legal_citator_review_event';
            END IF;
        ELSE
            IF (
                NEW.assertion_status IN ('verified', 'rejected')
                OR NEW.human_review_status IN ('approved', 'rejected')
                OR NEW.supersedes_assertion_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'final Citator record requires reviewStatus and a review event'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_legal_citator_review_event';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    IF review_status NOT IN ('approved', 'rejected', 'needs_revision') THEN
        RAISE EXCEPTION
            'Citator reviewStatus is not recognized'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_citator_review_status_exact';
    END IF;

    IF TG_TABLE_NAME = 'legal_citations' THEN
        IF NOT (
            (
                review_status = 'approved'
                AND NEW.citation_status = 'verified'
                AND NEW.human_review_status = 'approved'
            )
            OR (
                review_status = 'rejected'
                AND NEW.citation_status = 'rejected'
                AND NEW.human_review_status = 'rejected'
            )
            OR (
                review_status = 'needs_revision'
                AND NEW.citation_status = 'unknown'
                AND NEW.human_review_status = 'pending'
            )
        ) OR NEW.asserted_at IS DISTINCT FROM NEW.reviewed_at THEN
            RAISE EXCEPTION
                'citation reviewStatus does not exactly match stored statuses'
                USING ERRCODE = '23514',
                      CONSTRAINT =
                          'ck_legal_citation_review_status_exact';
        END IF;
    ELSE
        IF NOT (
            (
                review_status = 'approved'
                AND NEW.assertion_status = 'verified'
                AND NEW.human_review_status = 'approved'
            )
            OR (
                review_status = 'rejected'
                AND NEW.assertion_status = 'rejected'
                AND NEW.human_review_status = 'rejected'
            )
            OR (
                review_status = 'needs_revision'
                AND NEW.assertion_status = 'unknown'
                AND NEW.human_review_status = 'pending'
            )
        ) OR NEW.asserted_at IS DISTINCT FROM NEW.reviewed_at THEN
            RAISE EXCEPTION
                'relation reviewStatus does not exactly match stored statuses'
                USING ERRCODE = '23514',
                      CONSTRAINT =
                          'ck_legal_relation_review_status_exact';
        END IF;
    END IF;

    IF TG_TABLE_NAME = 'legal_citations' THEN
        reviewed_record_id := NEW.legal_citation_id;
        IF NEW.supersedes_citation_id IS NULL THEN
            RAISE EXCEPTION
                'reviewed citation % must supersede a candidate',
                NEW.legal_citation_id
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_citator_review_event';
        END IF;
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_citations AS predecessor '
            'WHERE predecessor.legal_citation_id = $1 '
            'AND predecessor.citation_status IN ('
            '''unknown'', ''unresolved'', ''ambiguous'', ''resolved'') '
            'AND predecessor.source_resource_id '
            'IS NOT DISTINCT FROM $2 '
            'AND predecessor.source_version_id '
            'IS NOT DISTINCT FROM $3 '
            'AND predecessor.source_provision_id '
            'IS NOT DISTINCT FROM $4 '
            'AND predecessor.cited_identifier_text '
            'IS NOT DISTINCT FROM $5 '
            'AND predecessor.target_resource_id '
            'IS NOT DISTINCT FROM $6 '
            'AND predecessor.target_version_id '
            'IS NOT DISTINCT FROM $7 '
            'AND predecessor.target_provision_id '
            'IS NOT DISTINCT FROM $8 '
            'AND predecessor.assertion_method '
            'IS NOT DISTINCT FROM $9 '
            'AND predecessor.algorithm_version '
            'IS NOT DISTINCT FROM $10 '
            'AND predecessor.confidence IS NOT DISTINCT FROM $11 '
            'AND predecessor.asserted_at <= $12'
            ')',
            TG_TABLE_SCHEMA
        )
        INTO successor_valid
        USING
            NEW.supersedes_citation_id,
            NEW.source_resource_id,
            NEW.source_version_id,
            NEW.source_provision_id,
            NEW.cited_identifier_text,
            NEW.target_resource_id,
            NEW.target_version_id,
            NEW.target_provision_id,
            NEW.assertion_method,
            NEW.algorithm_version,
            NEW.confidence,
            NEW.reviewed_at;
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_citation_review_events AS event '
            'WHERE event.legal_citation_id = $1 '
            'AND event.review_status = $2 '
            'AND event.reviewer_id = $3 '
            'AND event.reviewed_at = $4 '
            'AND event.evidence_excerpt_sha256 '
            'IS NOT DISTINCT FROM $5'
            ')',
            TG_TABLE_SCHEMA
        )
        INTO event_valid
        USING
            NEW.legal_citation_id,
            review_status,
            NEW.reviewed_by,
            NEW.reviewed_at,
            NEW.evidence_excerpt_sha256;
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_citation_review_events AS event '
            'WHERE event.legal_citation_id = $1 '
            'AND event.metadata_json ->> ''sourceCitationId'' = $2 '
            'AND event.metadata_json ->> ''tenantId'' = $3 '
            'AND event.metadata_json ->> ''projectId'' = $4 '
            'AND event.metadata_json ->> ''reviewerRole'' = $5 '
            'AND NULLIF(event.metadata_json ->> '
            '''legalApprovalDelegationId'', '''') '
            'IS NOT DISTINCT FROM $6 '
            'AND ('
            '(event.supersedes_review_event_id IS NULL '
            'AND NOT EXISTS ('
            'SELECT 1 FROM %I.legal_citation_review_events AS prior '
            'WHERE prior.legal_citation_id = $2'
            ')) '
            'OR event.supersedes_review_event_id = ('
            'SELECT latest.legal_citation_review_event_id '
            'FROM %I.legal_citation_review_events AS latest '
            'WHERE latest.legal_citation_id = $2 '
            'AND latest.reviewed_at <= event.reviewed_at '
            'ORDER BY latest.reviewed_at DESC, '
            'latest.legal_citation_review_event_id DESC '
            'LIMIT 1'
            ')'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO review_chain_valid
        USING
            NEW.legal_citation_id,
            NEW.supersedes_citation_id,
            NEW.metadata_json ->> 'tenantId',
            NEW.metadata_json ->> 'projectId',
            NEW.metadata_json ->> 'reviewerRole',
            delegation_id;
    ELSE
        reviewed_record_id := NEW.legal_relation_assertion_id;
        IF NEW.supersedes_assertion_id IS NULL THEN
            RAISE EXCEPTION
                'reviewed relation assertion % must supersede a candidate',
                NEW.legal_relation_assertion_id
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_citator_review_event';
        END IF;
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_relation_assertions AS predecessor '
            'WHERE predecessor.legal_relation_assertion_id = $1 '
            'AND predecessor.assertion_status IN ('
            '''unknown'', ''candidate'') '
            'AND predecessor.subject_resource_id '
            'IS NOT DISTINCT FROM $2 '
            'AND predecessor.object_resource_id '
            'IS NOT DISTINCT FROM $3 '
            'AND predecessor.relation_type IS NOT DISTINCT FROM $4 '
            'AND predecessor.source_version_id '
            'IS NOT DISTINCT FROM $5 '
            'AND predecessor.source_provision_id '
            'IS NOT DISTINCT FROM $6 '
            'AND predecessor.assertion_method '
            'IS NOT DISTINCT FROM $7 '
            'AND predecessor.algorithm_version '
            'IS NOT DISTINCT FROM $8 '
            'AND predecessor.confidence IS NOT DISTINCT FROM $9 '
            'AND predecessor.as_of_date IS NOT DISTINCT FROM $10 '
            'AND predecessor.asserted_at <= $11'
            ')',
            TG_TABLE_SCHEMA
        )
        INTO successor_valid
        USING
            NEW.supersedes_assertion_id,
            NEW.subject_resource_id,
            NEW.object_resource_id,
            NEW.relation_type,
            NEW.source_version_id,
            NEW.source_provision_id,
            NEW.assertion_method,
            NEW.algorithm_version,
            NEW.confidence,
            NEW.as_of_date,
            NEW.reviewed_at;
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_relation_review_events AS event '
            'WHERE event.legal_relation_assertion_id = $1 '
            'AND event.review_status = $2 '
            'AND event.reviewer_id = $3 '
            'AND event.reviewed_at = $4 '
            'AND event.evidence_excerpt_sha256 '
            'IS NOT DISTINCT FROM $5'
            ')',
            TG_TABLE_SCHEMA
        )
        INTO event_valid
        USING
            NEW.legal_relation_assertion_id,
            review_status,
            NEW.reviewed_by,
            NEW.reviewed_at,
            NEW.evidence_excerpt_sha256;
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_relation_review_events AS event '
            'WHERE event.legal_relation_assertion_id = $1 '
            'AND event.metadata_json ->> ''sourceAssertionId'' = $2 '
            'AND event.metadata_json ->> ''tenantId'' = $3 '
            'AND event.metadata_json ->> ''projectId'' = $4 '
            'AND event.metadata_json ->> ''reviewerRole'' = $5 '
            'AND NULLIF(event.metadata_json ->> '
            '''legalApprovalDelegationId'', '''') '
            'IS NOT DISTINCT FROM $6 '
            'AND ('
            '(event.supersedes_review_event_id IS NULL '
            'AND NOT EXISTS ('
            'SELECT 1 FROM %I.legal_relation_review_events AS prior '
            'WHERE prior.legal_relation_assertion_id = $2'
            ')) '
            'OR event.supersedes_review_event_id = ('
            'SELECT latest.legal_relation_review_event_id '
            'FROM %I.legal_relation_review_events AS latest '
            'WHERE latest.legal_relation_assertion_id = $2 '
            'AND latest.reviewed_at <= event.reviewed_at '
            'ORDER BY latest.reviewed_at DESC, '
            'latest.legal_relation_review_event_id DESC '
            'LIMIT 1'
            ')'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO review_chain_valid
        USING
            NEW.legal_relation_assertion_id,
            NEW.supersedes_assertion_id,
            NEW.metadata_json ->> 'tenantId',
            NEW.metadata_json ->> 'projectId',
            NEW.metadata_json ->> 'reviewerRole',
            delegation_id;
    END IF;

    IF review_status IN ('approved', 'rejected') AND (
        NEW.metadata_json ->> 'reviewerRole'
    ) NOT IN ('legal_counsel_approver', 'external_counsel') THEN
        event_valid := FALSE;
    END IF;
    IF NEW.metadata_json ->> 'reviewerRole' = 'external_counsel'
       AND delegation_id IS NULL THEN
        event_valid := FALSE;
    END IF;
    IF delegation_id IS NOT NULL THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS delegation '
            'WHERE delegation.legal_approval_delegation_id = $1 '
            'AND delegation.tenant_id = $2 '
            'AND delegation.project_id = $3 '
            'AND delegation.grantee_id = $4 '
            'AND delegation.grantee_role = $5 '
            'AND delegation.scope = ''citator_review'' '
            'AND delegation.status = ''active'' '
            'AND delegation.valid_from <= $6 '
            'AND delegation.valid_until >= $6 '
            'AND NOT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS successor '
            'WHERE successor.tenant_id = delegation.tenant_id '
            'AND successor.project_id = delegation.project_id '
            'AND successor.delegation_key = delegation.delegation_key '
            'AND successor.delegation_version > '
            'delegation.delegation_version '
            'AND successor.created_at <= $6'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO delegation_valid
        USING
            delegation_id,
            NEW.metadata_json ->> 'tenantId',
            NEW.metadata_json ->> 'projectId',
            NEW.reviewed_by,
            NEW.metadata_json ->> 'reviewerRole',
            NEW.reviewed_at;
        IF NOT COALESCE(delegation_valid, FALSE) THEN
            event_valid := FALSE;
        END IF;
    END IF;

    IF NOT COALESCE(event_valid, FALSE)
       OR NOT COALESCE(successor_valid, FALSE)
       OR NOT COALESCE(review_chain_valid, FALSE) THEN
        RAISE EXCEPTION
            'Citator successor % has an invalid predecessor or review event',
            reviewed_record_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_citator_review_event';
    END IF;
    RETURN NEW;
END;
$legal_citator_review_event$;
REVOKE ALL ON FUNCTION legal_assert_citator_review_event() FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_legal_citation_review_event
    ON legal_citations;
CREATE CONSTRAINT TRIGGER ctrg_legal_citation_review_event
    AFTER INSERT ON legal_citations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_citator_review_event();

DROP TRIGGER IF EXISTS ctrg_legal_relation_review_event
    ON legal_relation_assertions;
CREATE CONSTRAINT TRIGGER ctrg_legal_relation_review_event
    AFTER INSERT ON legal_relation_assertions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_citator_review_event();

CREATE OR REPLACE FUNCTION legal_assert_citator_review_event_target()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_citator_review_event_target$
DECLARE
    target_valid BOOLEAN := FALSE;
    delegation_id TEXT;
    delegation_valid BOOLEAN;
BEGIN
    delegation_id := NULLIF(
        btrim(NEW.metadata_json ->> 'legalApprovalDelegationId'),
        ''
    );

    IF NEW.reviewed_at >
       statement_timestamp() + INTERVAL '5 minutes' THEN
        RAISE EXCEPTION
            'Citator review event timestamp is in the future'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_citator_review_event_target';
    END IF;

    IF TG_TABLE_NAME = 'legal_citation_review_events' THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_citations AS reviewed '
            'JOIN %I.legal_citations AS predecessor '
            'ON predecessor.legal_citation_id = $6 '
            'WHERE reviewed.legal_citation_id = $1 '
            'AND reviewed.supersedes_citation_id = $6 '
            'AND predecessor.citation_status IN ('
            '''unknown'', ''unresolved'', ''ambiguous'', ''resolved'') '
            'AND reviewed.source_resource_id '
            'IS NOT DISTINCT FROM predecessor.source_resource_id '
            'AND reviewed.source_version_id '
            'IS NOT DISTINCT FROM predecessor.source_version_id '
            'AND reviewed.source_provision_id '
            'IS NOT DISTINCT FROM predecessor.source_provision_id '
            'AND reviewed.cited_identifier_text '
            'IS NOT DISTINCT FROM predecessor.cited_identifier_text '
            'AND reviewed.target_resource_id '
            'IS NOT DISTINCT FROM predecessor.target_resource_id '
            'AND reviewed.target_version_id '
            'IS NOT DISTINCT FROM predecessor.target_version_id '
            'AND reviewed.target_provision_id '
            'IS NOT DISTINCT FROM predecessor.target_provision_id '
            'AND reviewed.assertion_method '
            'IS NOT DISTINCT FROM predecessor.assertion_method '
            'AND reviewed.algorithm_version '
            'IS NOT DISTINCT FROM predecessor.algorithm_version '
            'AND reviewed.confidence '
            'IS NOT DISTINCT FROM predecessor.confidence '
            'AND reviewed.metadata_json ->> ''reviewStatus'' = $2 '
            'AND reviewed.reviewed_by = $3 '
            'AND reviewed.reviewed_at = $4 '
            'AND reviewed.asserted_at = $4 '
            'AND reviewed.evidence_excerpt_sha256 '
            'IS NOT DISTINCT FROM $5 '
            'AND predecessor.asserted_at <= $4 '
            'AND NULLIF(btrim($8), '''') IS NOT NULL '
            'AND NULLIF(btrim($9), '''') IS NOT NULL '
            'AND NULLIF(btrim($10), '''') IS NOT NULL '
            'AND reviewed.metadata_json ->> ''tenantId'' = $8 '
            'AND reviewed.metadata_json ->> ''projectId'' = $9 '
            'AND reviewed.metadata_json ->> ''reviewerRole'' = $10 '
            'AND NULLIF(reviewed.metadata_json ->> '
            '''legalApprovalDelegationId'', '''') '
            'IS NOT DISTINCT FROM $11 '
            'AND ('
            '($2 = ''approved'' '
            'AND reviewed.citation_status = ''verified'' '
            'AND reviewed.human_review_status = ''approved'') '
            'OR ($2 = ''rejected'' '
            'AND reviewed.citation_status = ''rejected'' '
            'AND reviewed.human_review_status = ''rejected'') '
            'OR ($2 = ''needs_revision'' '
            'AND reviewed.citation_status = ''unknown'' '
            'AND reviewed.human_review_status = ''pending'')'
            ') '
            'AND ('
            '($7 IS NULL AND NOT EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_citation_review_events AS prior '
            'WHERE prior.legal_citation_id = $6'
            ')) '
            'OR $7 = ('
            'SELECT latest.legal_citation_review_event_id '
            'FROM %I.legal_citation_review_events AS latest '
            'WHERE latest.legal_citation_id = $6 '
            'AND latest.reviewed_at <= $4 '
            'ORDER BY latest.reviewed_at DESC, '
            'latest.legal_citation_review_event_id DESC '
            'LIMIT 1'
            ')'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO target_valid
        USING
            NEW.legal_citation_id,
            NEW.review_status,
            NEW.reviewer_id,
            NEW.reviewed_at,
            NEW.evidence_excerpt_sha256,
            NEW.metadata_json ->> 'sourceCitationId',
            NEW.supersedes_review_event_id,
            NEW.metadata_json ->> 'tenantId',
            NEW.metadata_json ->> 'projectId',
            NEW.metadata_json ->> 'reviewerRole',
            delegation_id;
    ELSIF TG_TABLE_NAME = 'legal_relation_review_events' THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_relation_assertions AS reviewed '
            'JOIN %I.legal_relation_assertions AS predecessor '
            'ON predecessor.legal_relation_assertion_id = $6 '
            'WHERE reviewed.legal_relation_assertion_id = $1 '
            'AND reviewed.supersedes_assertion_id = $6 '
            'AND predecessor.assertion_status IN ('
            '''unknown'', ''candidate'') '
            'AND reviewed.subject_resource_id '
            'IS NOT DISTINCT FROM predecessor.subject_resource_id '
            'AND reviewed.object_resource_id '
            'IS NOT DISTINCT FROM predecessor.object_resource_id '
            'AND reviewed.relation_type '
            'IS NOT DISTINCT FROM predecessor.relation_type '
            'AND reviewed.source_version_id '
            'IS NOT DISTINCT FROM predecessor.source_version_id '
            'AND reviewed.source_provision_id '
            'IS NOT DISTINCT FROM predecessor.source_provision_id '
            'AND reviewed.assertion_method '
            'IS NOT DISTINCT FROM predecessor.assertion_method '
            'AND reviewed.algorithm_version '
            'IS NOT DISTINCT FROM predecessor.algorithm_version '
            'AND reviewed.confidence '
            'IS NOT DISTINCT FROM predecessor.confidence '
            'AND reviewed.as_of_date '
            'IS NOT DISTINCT FROM predecessor.as_of_date '
            'AND reviewed.metadata_json ->> ''reviewStatus'' = $2 '
            'AND reviewed.reviewed_by = $3 '
            'AND reviewed.reviewed_at = $4 '
            'AND reviewed.asserted_at = $4 '
            'AND reviewed.evidence_excerpt_sha256 '
            'IS NOT DISTINCT FROM $5 '
            'AND predecessor.asserted_at <= $4 '
            'AND NULLIF(btrim($8), '''') IS NOT NULL '
            'AND NULLIF(btrim($9), '''') IS NOT NULL '
            'AND NULLIF(btrim($10), '''') IS NOT NULL '
            'AND reviewed.metadata_json ->> ''tenantId'' = $8 '
            'AND reviewed.metadata_json ->> ''projectId'' = $9 '
            'AND reviewed.metadata_json ->> ''reviewerRole'' = $10 '
            'AND NULLIF(reviewed.metadata_json ->> '
            '''legalApprovalDelegationId'', '''') '
            'IS NOT DISTINCT FROM $11 '
            'AND ('
            '($2 = ''approved'' '
            'AND reviewed.assertion_status = ''verified'' '
            'AND reviewed.human_review_status = ''approved'') '
            'OR ($2 = ''rejected'' '
            'AND reviewed.assertion_status = ''rejected'' '
            'AND reviewed.human_review_status = ''rejected'') '
            'OR ($2 = ''needs_revision'' '
            'AND reviewed.assertion_status = ''unknown'' '
            'AND reviewed.human_review_status = ''pending'')'
            ') '
            'AND ('
            '($7 IS NULL AND NOT EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_relation_review_events AS prior '
            'WHERE prior.legal_relation_assertion_id = $6'
            ')) '
            'OR $7 = ('
            'SELECT latest.legal_relation_review_event_id '
            'FROM %I.legal_relation_review_events AS latest '
            'WHERE latest.legal_relation_assertion_id = $6 '
            'AND latest.reviewed_at <= $4 '
            'ORDER BY latest.reviewed_at DESC, '
            'latest.legal_relation_review_event_id DESC '
            'LIMIT 1'
            ')'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO target_valid
        USING
            NEW.legal_relation_assertion_id,
            NEW.review_status,
            NEW.reviewer_id,
            NEW.reviewed_at,
            NEW.evidence_excerpt_sha256,
            NEW.metadata_json ->> 'sourceAssertionId',
            NEW.supersedes_review_event_id,
            NEW.metadata_json ->> 'tenantId',
            NEW.metadata_json ->> 'projectId',
            NEW.metadata_json ->> 'reviewerRole',
            delegation_id;
    END IF;

    IF NEW.review_status IN ('approved', 'rejected')
       AND NEW.metadata_json ->> 'reviewerRole'
           NOT IN ('legal_counsel_approver', 'external_counsel') THEN
        target_valid := FALSE;
    END IF;
    IF NEW.metadata_json ->> 'reviewerRole' = 'external_counsel'
       AND delegation_id IS NULL THEN
        target_valid := FALSE;
    END IF;

    IF delegation_id IS NOT NULL THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_approval_delegations AS delegation '
            'WHERE delegation.legal_approval_delegation_id = $1 '
            'AND delegation.tenant_id = $2 '
            'AND delegation.project_id = $3 '
            'AND delegation.grantee_id = $4 '
            'AND delegation.grantee_role = $5 '
            'AND delegation.scope = ''citator_review'' '
            'AND delegation.status = ''active'' '
            'AND delegation.valid_from <= $6 '
            'AND delegation.valid_until >= $6 '
            'AND NOT EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_approval_delegations AS successor '
            'WHERE successor.tenant_id = delegation.tenant_id '
            'AND successor.project_id = delegation.project_id '
            'AND successor.delegation_key = delegation.delegation_key '
            'AND successor.delegation_version > '
            'delegation.delegation_version '
            'AND successor.created_at <= $6'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO delegation_valid
        USING
            delegation_id,
            NEW.metadata_json ->> 'tenantId',
            NEW.metadata_json ->> 'projectId',
            NEW.reviewer_id,
            NEW.metadata_json ->> 'reviewerRole',
            NEW.reviewed_at;
        IF NOT COALESCE(delegation_valid, FALSE) THEN
            target_valid := FALSE;
        END IF;
    END IF;

    IF NOT COALESCE(target_valid, FALSE) THEN
        RAISE EXCEPTION
            'Citator review event has no exact reviewed successor chain'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_citator_review_event_target';
    END IF;
    RETURN NEW;
END;
$legal_citator_review_event_target$;
REVOKE ALL ON FUNCTION legal_assert_citator_review_event_target()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_legal_citation_review_event_target
    ON legal_citation_review_events;
CREATE CONSTRAINT TRIGGER ctrg_legal_citation_review_event_target
    AFTER INSERT ON legal_citation_review_events
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_citator_review_event_target();

DROP TRIGGER IF EXISTS ctrg_legal_relation_review_event_target
    ON legal_relation_review_events;
CREATE CONSTRAINT TRIGGER ctrg_legal_relation_review_event_target
    AFTER INSERT ON legal_relation_review_events
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_citator_review_event_target();

-- ---------------------------------------------------------------------------
-- Maker-checker and delegated legal approval
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS legal_approval_delegations (
    legal_approval_delegation_id   TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    delegation_key                 TEXT NOT NULL,
    delegation_version             INTEGER NOT NULL,
    grantor_id                     TEXT NOT NULL,
    grantor_role                   TEXT NOT NULL,
    grantee_id                     TEXT NOT NULL,
    grantee_role                   TEXT NOT NULL,
    scope                          TEXT NOT NULL,
    valid_from                     TIMESTAMPTZ NOT NULL,
    valid_until                    TIMESTAMPTZ NOT NULL,
    status                         TEXT NOT NULL DEFAULT 'active',
    reason                         TEXT NOT NULL,
    supersedes_delegation_id       TEXT,
    created_at                     TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_approval_delegation_id,
        tenant_id,
        project_id
    ),
    UNIQUE (
        tenant_id,
        project_id,
        delegation_key,
        delegation_version
    ),
    CONSTRAINT fk_legal_approval_delegation_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT ck_legal_approval_delegation_actors
        CHECK (
            NULLIF(btrim(grantor_id), '') IS NOT NULL
            AND NULLIF(btrim(grantee_id), '') IS NOT NULL
            AND grantor_id <> grantee_id
        ),
    CONSTRAINT ck_legal_approval_delegation_version
        CHECK (delegation_version >= 1),
    CONSTRAINT ck_legal_approval_delegation_grantor_role
        CHECK (grantor_role = 'legal_counsel_approver'),
    CONSTRAINT ck_legal_approval_delegation_role
        CHECK (grantee_role IN (
            'legal_counsel_approver',
            'external_counsel'
        )),
    CONSTRAINT ck_legal_approval_delegation_scope
        CHECK (scope IN (
            'approval_gate',
            'citator_review',
            'counsel_opinion',
            'waiver_approval',
            'matter_resolution'
        )),
    CONSTRAINT ck_legal_approval_delegation_period
        CHECK (valid_until > valid_from),
    CONSTRAINT ck_legal_approval_delegation_status
        CHECK (status IN ('active', 'revoked', 'expired', 'consumed')),
    CONSTRAINT fk_legal_approval_delegation_supersedes
        FOREIGN KEY (
            supersedes_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_approval_delegation_not_self_superseding
        CHECK (
            supersedes_delegation_id IS NULL
            OR supersedes_delegation_id <>
                legal_approval_delegation_id
        ),
    CONSTRAINT ck_legal_approval_delegation_reason
        CHECK (NULLIF(btrim(reason), '') IS NOT NULL),
    CONSTRAINT ck_legal_approval_delegation_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_approval_delegations_active
    ON legal_approval_delegations(
        tenant_id,
        project_id,
        grantee_id,
        valid_until
    )
    WHERE status = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_approval_delegation_successor
    ON legal_approval_delegations(supersedes_delegation_id)
    WHERE supersedes_delegation_id IS NOT NULL;

CREATE OR REPLACE FUNCTION legal_assert_approval_delegation_chain()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_approval_delegation_chain$
DECLARE
    predecessor RECORD;
    predecessor_count INTEGER := 0;
    root_creator TEXT;
    snapshot_creator TEXT;
BEGIN
    IF NEW.created_at < statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.created_at > statement_timestamp() + INTERVAL '5 minutes'
       OR NEW.created_at >= NEW.valid_until THEN
        RAISE EXCEPTION
            'legal approval delegation has an invalid server-time window'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_approval_delegation_chain';
    END IF;

    IF NEW.supersedes_delegation_id IS NULL THEN
        IF NEW.delegation_version <> 1 OR NEW.status <> 'active' THEN
            RAISE EXCEPTION
                'a root legal approval delegation must be active version 1'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_approval_delegation_chain';
        END IF;
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT predecessor.* '
        'FROM %I.legal_approval_delegations AS predecessor '
        'WHERE predecessor.legal_approval_delegation_id = $1 '
        'AND predecessor.tenant_id = $2 '
        'AND predecessor.project_id = $3',
        TG_TABLE_SCHEMA
    )
    INTO predecessor
    USING
        NEW.supersedes_delegation_id,
        NEW.tenant_id,
        NEW.project_id;
    GET DIAGNOSTICS predecessor_count = ROW_COUNT;

    IF predecessor_count <> 1 THEN
        RAISE EXCEPTION
            'legal approval delegation predecessor is missing or out of scope'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_approval_delegation_chain';
    END IF;

    IF NEW.delegation_key IS DISTINCT FROM predecessor.delegation_key
       OR NEW.delegation_version <> predecessor.delegation_version + 1
       OR NEW.grantor_id IS DISTINCT FROM predecessor.grantor_id
       OR NEW.grantor_role IS DISTINCT FROM predecessor.grantor_role
       OR NEW.grantee_id IS DISTINCT FROM predecessor.grantee_id
       OR NEW.grantee_role IS DISTINCT FROM predecessor.grantee_role
       OR NEW.scope IS DISTINCT FROM predecessor.scope
       OR NEW.valid_from IS DISTINCT FROM predecessor.valid_from
       OR NEW.valid_until IS DISTINCT FROM predecessor.valid_until
       OR NEW.created_at < predecessor.created_at
       OR NEW.status NOT IN ('revoked', 'expired', 'consumed')
    THEN
        RAISE EXCEPTION
            'legal approval delegation successor breaks its authority chain'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_approval_delegation_chain';
    END IF;
    RETURN NEW;
END;
$legal_approval_delegation_chain$;
REVOKE ALL ON FUNCTION legal_assert_approval_delegation_chain()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_legal_approval_delegation_chain
    ON legal_approval_delegations;
CREATE CONSTRAINT TRIGGER ctrg_legal_approval_delegation_chain
    AFTER INSERT ON legal_approval_delegations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_approval_delegation_chain();

ALTER TABLE legal_review_decisions
    ADD COLUMN IF NOT EXISTS legal_approval_delegation_id TEXT,
    ADD COLUMN IF NOT EXISTS legal_review_id TEXT,
    ADD COLUMN IF NOT EXISTS legal_review_version_id TEXT,
    ADD COLUMN IF NOT EXISTS legal_review_version INTEGER,
    ADD COLUMN IF NOT EXISTS legal_review_projection_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS decision_version INTEGER,
    ADD COLUMN IF NOT EXISTS supersedes_legal_review_decision_id TEXT,
    ADD COLUMN IF NOT EXISTS
        expected_predecessor_legal_review_decision_id TEXT,
    ADD COLUMN IF NOT EXISTS predecessor_expectation_supplied BOOLEAN
        NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ;

-- Existing 0008 decisions are immutable.  Metadata that merely resembles a
-- normalized binding is deliberately not copied into typed authority fields:
-- those legacy rows remain unversioned and therefore cannot clear Approval
-- Readiness.  A counsel approver must append a fresh normalized decision.
COMMENT ON COLUMN legal_review_decisions.decision_version IS
    'DB-issued approval-gate chain version; NULL marks non-authoritative legacy or non-gate rows.';
COMMENT ON COLUMN
    legal_review_decisions.supersedes_legal_review_decision_id IS
    'Exact immediately preceding decision in the DB-issued approval-gate chain.';

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS fk_legal_review_decision_supersedes_scope;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT fk_legal_review_decision_supersedes_scope
        FOREIGN KEY (
            supersedes_legal_review_decision_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_review_decisions(
            legal_review_decision_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT;

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS
        fk_legal_review_decision_expected_predecessor_scope;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT
        fk_legal_review_decision_expected_predecessor_scope
        FOREIGN KEY (
            expected_predecessor_legal_review_decision_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_review_decisions(
            legal_review_decision_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT;

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS ck_legal_review_decision_version_chain;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT ck_legal_review_decision_version_chain
        CHECK (
            decision_scope <> 'approval_gate'
            OR decision_type NOT IN (
                'approve',
                'reject',
                'request_changes',
                'waive_with_conditions'
            )
            OR (
                decision_version > 0
                AND recorded_at IS NOT NULL
                AND predecessor_expectation_supplied
                AND (
                    (
                        decision_version = 1
                        AND supersedes_legal_review_decision_id IS NULL
                    )
                    OR (
                        decision_version > 1
                        AND supersedes_legal_review_decision_id IS NOT NULL
                    )
                )
                AND (
                    expected_predecessor_legal_review_decision_id IS NULL
                    OR expected_predecessor_legal_review_decision_id =
                        supersedes_legal_review_decision_id
                )
            )
        ) NOT VALID;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_legal_review_decision_chain_version
    ON legal_review_decisions(
        tenant_id,
        project_id,
        legal_review_id,
        decision_scope,
        decision_version
    )
    WHERE decision_version IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_legal_review_decision_single_successor
    ON legal_review_decisions(supersedes_legal_review_decision_id)
    WHERE supersedes_legal_review_decision_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_legal_review_decision_chain_leaf
    ON legal_review_decisions(
        tenant_id,
        project_id,
        legal_review_id,
        decision_scope,
        decision_version DESC
    )
    WHERE decision_version IS NOT NULL;

CREATE OR REPLACE FUNCTION legal_prepare_review_decision_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_prepare_review_decision$
DECLARE
    predecessor_id TEXT;
    predecessor_version INTEGER;
BEGIN
    IF (
        NEW.decision_scope <> 'approval_gate'
        OR NEW.decision_type NOT IN (
            'approve',
            'reject',
            'request_changes',
            'waive_with_conditions'
        )
    ) THEN
        RETURN NEW;
    END IF;

    IF NULLIF(btrim(NEW.legal_review_id), '') IS NULL THEN
        RAISE EXCEPTION
            'approval-gate decision % requires typed legal_review_id',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_review_decision_review_id';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            NEW.tenant_id || E'\x1f'
            || NEW.project_id || E'\x1f'
            || NEW.legal_review_id || E'\x1f'
            || NEW.decision_scope,
            0
        )
    );

    EXECUTE pg_catalog.format(
        'SELECT predecessor.legal_review_decision_id, '
        'predecessor.decision_version '
        'FROM %1$I.legal_review_decisions AS predecessor '
        'WHERE predecessor.tenant_id = $1 '
        'AND predecessor.project_id = $2 '
        'AND predecessor.legal_review_id = $3 '
        'AND predecessor.decision_scope = $4 '
        'AND predecessor.decision_type IN ('
        '''approve'', ''reject'', ''request_changes'', '
        '''waive_with_conditions'') '
        'AND predecessor.decision_version IS NOT NULL '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %1$I.legal_review_decisions AS successor '
        'WHERE successor.supersedes_legal_review_decision_id = '
        'predecessor.legal_review_decision_id'
        ') '
        'ORDER BY predecessor.decision_version DESC '
        'LIMIT 1 FOR UPDATE OF predecessor',
        TG_TABLE_SCHEMA
    )
    INTO predecessor_id, predecessor_version
    USING
        NEW.tenant_id,
        NEW.project_id,
        NEW.legal_review_id,
        NEW.decision_scope;

    IF NEW.predecessor_expectation_supplied IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION
            'approval-gate decision requires an explicit predecessor expectation'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_expected_predecessor';
    END IF;

    IF NEW.expected_predecessor_legal_review_decision_id IS DISTINCT
        FROM predecessor_id
    THEN
        RAISE EXCEPTION
            'approval-gate decision predecessor changed; reload before deciding'
            USING ERRCODE = '40001',
                  CONSTRAINT =
                      'ck_legal_review_decision_expected_predecessor';
    END IF;

    NEW.decision_version := COALESCE(predecessor_version, 0) + 1;
    NEW.supersedes_legal_review_decision_id := predecessor_id;
    NEW.recorded_at := statement_timestamp();
    NEW.decided_at := NEW.recorded_at;
    RETURN NEW;
END;
$legal_prepare_review_decision$;
REVOKE ALL ON FUNCTION legal_prepare_review_decision_insert()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_legal_prepare_review_decision_insert
    ON legal_review_decisions;
CREATE TRIGGER trg_legal_prepare_review_decision_insert
    BEFORE INSERT ON legal_review_decisions
    FOR EACH ROW
    EXECUTE FUNCTION legal_prepare_review_decision_insert();

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS ck_legal_review_decision_review_id;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT ck_legal_review_decision_review_id
        CHECK (
            decision_scope <> 'approval_gate'
            OR decision_type NOT IN (
                'approve',
                'reject',
                'waive_with_conditions'
            )
            OR NULLIF(btrim(legal_review_id), '') IS NOT NULL
        ) NOT VALID;

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS ck_legal_review_decision_projection_binding;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT ck_legal_review_decision_projection_binding
        CHECK (
            decision_scope <> 'approval_gate'
            OR decision_type NOT IN (
                'approve',
                'reject',
                'waive_with_conditions'
            )
            OR (
                NULLIF(btrim(legal_review_version_id), '') IS NOT NULL
                AND legal_review_version > 0
                AND legal_review_projection_sha256 ~ '^[0-9a-f]{64}$'
            )
        ) NOT VALID;

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS fk_legal_review_decision_delegation;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT fk_legal_review_decision_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT;

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS ck_legal_review_decision_human_actor;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT ck_legal_review_decision_human_actor
        CHECK (
            NULLIF(btrim(reviewer_id), '') IS NOT NULL
            AND reviewer_role IN (
                'legal_reviewer',
                'legal_counsel_approver',
                'external_counsel',
                'compliance_officer'
            )
        ) NOT VALID;

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS ck_legal_review_final_decision_role;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT ck_legal_review_final_decision_role
        CHECK (
            decision_type NOT IN (
                'approve',
                'reject',
                'waive_with_conditions'
            )
            OR reviewer_role IN (
                'legal_counsel_approver',
                'external_counsel'
            )
        ) NOT VALID;

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS ck_legal_review_external_delegation;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT ck_legal_review_external_delegation
        CHECK (
            reviewer_role <> 'external_counsel'
            OR decision_type NOT IN (
                'approve',
                'reject',
                'waive_with_conditions'
            )
            OR legal_approval_delegation_id IS NOT NULL
        ) NOT VALID;

CREATE OR REPLACE FUNCTION legal_assert_maker_checker()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_maker_checker$
DECLARE
    snapshot_creator TEXT;
    snapshot_legal_review_id TEXT;
    delegation_valid BOOLEAN;
BEGIN
    IF NEW.decision_type NOT IN (
        'approve',
        'reject',
        'waive_with_conditions'
    ) THEN
        RETURN NEW;
    END IF;

    IF (
        NEW.decision_scope = 'approval_gate'
        AND NEW.legal_evidence_snapshot_id IS NULL
    ) THEN
        RAISE EXCEPTION
            'final approval-gate decision % requires an evidence snapshot',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_review_maker_checker';
    END IF;

    IF (
        NEW.decision_scope = 'approval_gate'
        AND NULLIF(btrim(NEW.legal_review_id), '') IS NULL
    ) THEN
        RAISE EXCEPTION
            'final approval-gate decision % requires legalReviewId lineage',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_review_maker_checker';
    END IF;

    IF NEW.legal_evidence_snapshot_id IS NOT NULL THEN
        EXECUTE pg_catalog.format(
            'SELECT snapshot.created_by, snapshot.legal_review_id '
            'FROM %I.legal_evidence_snapshots AS snapshot '
            'WHERE snapshot.legal_evidence_snapshot_id = $1 '
            'AND snapshot.tenant_id = $2 '
            'AND snapshot.project_id = $3',
            TG_TABLE_SCHEMA
        )
        INTO snapshot_creator, snapshot_legal_review_id
        USING
            NEW.legal_evidence_snapshot_id,
            NEW.tenant_id,
            NEW.project_id;

        IF snapshot_creator IS NULL THEN
            RAISE EXCEPTION
                'final legal decision % requires a same-scope evidence snapshot',
                NEW.legal_review_decision_id
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_review_maker_checker';
        END IF;

        IF snapshot_creator = NEW.reviewer_id THEN
            RAISE EXCEPTION
                'legal evidence maker % cannot approve or finally dispose its own snapshot',
                NEW.reviewer_id
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_review_maker_checker';
        END IF;

        IF (
            NEW.decision_scope = 'approval_gate'
            AND snapshot_legal_review_id IS DISTINCT FROM
                NEW.legal_review_id
        ) THEN
            RAISE EXCEPTION
                'legal decision % does not match snapshot review lineage',
                NEW.legal_review_decision_id
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_review_maker_checker';
        END IF;
    END IF;

    IF NEW.legal_approval_delegation_id IS NOT NULL THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS delegation '
            'WHERE delegation.legal_approval_delegation_id = $1 '
            'AND delegation.tenant_id = $2 '
            'AND delegation.project_id = $3 '
            'AND delegation.grantee_id = $4 '
            'AND delegation.grantee_role = $5 '
            'AND delegation.status = ''active'' '
            'AND delegation.created_at <= $6 '
            'AND delegation.valid_from <= $6 '
            'AND delegation.valid_until >= $6 '
            'AND delegation.scope = $7 '
            'AND NOT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS successor '
            'WHERE successor.tenant_id = delegation.tenant_id '
            'AND successor.project_id = delegation.project_id '
            'AND successor.delegation_key = delegation.delegation_key '
            'AND successor.delegation_version > '
            'delegation.delegation_version '
            'AND successor.created_at <= $6'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO delegation_valid
        USING
            NEW.legal_approval_delegation_id,
            NEW.tenant_id,
            NEW.project_id,
            NEW.reviewer_id,
            NEW.reviewer_role,
            NEW.decided_at,
            NEW.decision_scope;

        IF NOT delegation_valid THEN
            RAISE EXCEPTION
                'legal decision % references an invalid or expired delegation',
                NEW.legal_review_decision_id
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_review_delegation';
        END IF;
    END IF;

    RETURN NEW;
END;
$legal_maker_checker$;
REVOKE ALL ON FUNCTION legal_assert_maker_checker() FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_legal_review_maker_checker
    ON legal_review_decisions;
CREATE CONSTRAINT TRIGGER ctrg_legal_review_maker_checker
    AFTER INSERT ON legal_review_decisions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_maker_checker();

-- ---------------------------------------------------------------------------
-- Versioned required-claim policy
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS legal_required_claim_sets (
    legal_required_claim_set_id   TEXT PRIMARY KEY,
    tenant_id                     TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                    TEXT NOT NULL,
    claim_set_key                 TEXT NOT NULL,
    claim_set_version             INTEGER NOT NULL,
    review_scope                  TEXT NOT NULL DEFAULT 'approval_gate',
    status                        TEXT NOT NULL DEFAULT 'draft',
    claim_set_sha256              TEXT NOT NULL,
    created_by                    TEXT NOT NULL,
    created_at                    TIMESTAMPTZ NOT NULL,
    approved_by                   TEXT,
    approver_role                 TEXT,
    legal_approval_delegation_id  TEXT,
    approved_at                   TIMESTAMPTZ,
    supersedes_claim_set_id       TEXT,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_required_claim_set_id,
        tenant_id,
        project_id
    ),
    UNIQUE (
        tenant_id,
        project_id,
        claim_set_key,
        claim_set_version
    ),
    CONSTRAINT fk_legal_required_claim_set_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_required_claim_set_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_required_claim_set_supersedes
        FOREIGN KEY (
            supersedes_claim_set_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_required_claim_sets(
            legal_required_claim_set_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_required_claim_set_key
        CHECK (
            NULLIF(btrim(claim_set_key), '') IS NOT NULL
            AND NULLIF(btrim(created_by), '') IS NOT NULL
        ),
    CONSTRAINT ck_legal_required_claim_set_version
        CHECK (claim_set_version >= 1),
    CONSTRAINT ck_legal_required_claim_set_scope
        CHECK (review_scope IN (
            'approval_gate',
            'report_eligibility',
            'counsel_opinion'
        )),
    CONSTRAINT ck_legal_required_claim_set_status
        CHECK (status IN ('draft', 'approved', 'rejected', 'superseded')),
    CONSTRAINT ck_legal_required_claim_set_hash
        CHECK (claim_set_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_required_claim_set_approval
        CHECK (
            status <> 'approved'
            OR (
                NULLIF(btrim(approved_by), '') IS NOT NULL
                AND approver_role IN (
                    'legal_counsel_approver',
                    'external_counsel'
                )
                AND approved_at IS NOT NULL
                AND approved_by <> created_by
                AND (
                    (
                        approver_role = 'external_counsel'
                        AND legal_approval_delegation_id IS NOT NULL
                    )
                    OR (
                        approver_role = 'legal_counsel_approver'
                        AND legal_approval_delegation_id IS NULL
                    )
                )
            )
        ),
    CONSTRAINT ck_legal_required_claim_set_not_self_superseding
        CHECK (
            supersedes_claim_set_id IS NULL
            OR supersedes_claim_set_id <> legal_required_claim_set_id
        ),
    CONSTRAINT ck_legal_required_claim_set_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);

CREATE TABLE IF NOT EXISTS legal_required_claims (
    legal_required_claim_id       TEXT PRIMARY KEY,
    tenant_id                     TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                    TEXT NOT NULL,
    legal_required_claim_set_id   TEXT NOT NULL,
    claim_key                     TEXT NOT NULL,
    claim_text_sha256             TEXT NOT NULL,
    description                   TEXT,
    ordinal                       INTEGER NOT NULL,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_required_claim_id,
        tenant_id,
        project_id
    ),
    UNIQUE (legal_required_claim_set_id, claim_key),
    UNIQUE (legal_required_claim_set_id, claim_text_sha256),
    UNIQUE (legal_required_claim_set_id, ordinal),
    CONSTRAINT fk_legal_required_claim_set_scope
        FOREIGN KEY (
            legal_required_claim_set_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_required_claim_sets(
            legal_required_claim_set_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_required_claim_key
        CHECK (NULLIF(btrim(claim_key), '') IS NOT NULL),
    CONSTRAINT ck_legal_required_claim_hash
        CHECK (claim_text_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_required_claim_ordinal
        CHECK (ordinal >= 1),
    CONSTRAINT ck_legal_required_claim_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_required_claim_sets_active
    ON legal_required_claim_sets(
        tenant_id,
        project_id,
        claim_set_key,
        claim_set_version DESC
    );
CREATE INDEX IF NOT EXISTS idx_legal_required_claims_set
    ON legal_required_claims(
        tenant_id,
        project_id,
        legal_required_claim_set_id,
        ordinal
    );
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_required_claim_set_root
    ON legal_required_claim_sets(
        tenant_id,
        project_id,
        claim_set_key
    )
    WHERE supersedes_claim_set_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_required_claim_set_successor
    ON legal_required_claim_sets(supersedes_claim_set_id)
    WHERE supersedes_claim_set_id IS NOT NULL;

CREATE OR REPLACE FUNCTION legal_assert_required_claim_set_chain()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_required_claim_set_chain$
DECLARE
    predecessor RECORD;
    predecessor_count INTEGER := 0;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            pg_catalog.concat_ws(
                ':',
                'legal-required-claim-set',
                NEW.tenant_id,
                NEW.project_id,
                NEW.claim_set_key
            ),
            0
        )
    );

    IF NEW.supersedes_claim_set_id IS NULL THEN
        IF NEW.claim_set_version <> 1
           OR NEW.status <> 'draft'
           OR NEW.approved_by IS NOT NULL
           OR NEW.approver_role IS NOT NULL
           OR NEW.legal_approval_delegation_id IS NOT NULL
           OR NEW.approved_at IS NOT NULL
        THEN
            RAISE EXCEPTION
                'required claim policy root must be an unapproved version-1 draft'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_required_claim_set_chain';
        END IF;
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT predecessor.* '
        'FROM %I.legal_required_claim_sets AS predecessor '
        'WHERE predecessor.legal_required_claim_set_id = $1 '
        'AND predecessor.tenant_id = $2 '
        'AND predecessor.project_id = $3 '
        'AND predecessor.claim_set_key = $4 '
        'FOR SHARE',
        TG_TABLE_SCHEMA
    )
    INTO predecessor
    USING
        NEW.supersedes_claim_set_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.claim_set_key;
    GET DIAGNOSTICS predecessor_count = ROW_COUNT;

    IF predecessor_count <> 1
       OR NEW.claim_set_version <> predecessor.claim_set_version + 1
       OR NEW.review_scope IS DISTINCT FROM predecessor.review_scope
       OR NEW.created_at < predecessor.created_at
       OR (
           NEW.status = 'draft'
           AND predecessor.status <> 'approved'
       )
       OR (
           NEW.status IN ('approved', 'rejected')
           AND predecessor.status <> 'draft'
       )
       OR (
           NEW.status = 'superseded'
           AND predecessor.status <> 'approved'
       )
       OR NEW.status NOT IN (
           'draft',
           'approved',
           'rejected',
           'superseded'
       )
    THEN
        RAISE EXCEPTION
            'required claim policy must follow its immediate same-scope predecessor'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_required_claim_set_chain';
    END IF;

    IF NEW.status IN ('approved', 'rejected', 'superseded')
       AND (
           NEW.claim_set_sha256 IS DISTINCT FROM
               predecessor.claim_set_sha256
           OR NEW.created_by IS DISTINCT FROM predecessor.created_by
           OR NEW.created_at IS DISTINCT FROM predecessor.created_at
       )
    THEN
        RAISE EXCEPTION
            'required claim policy review successor changed sealed draft content'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_required_claim_set_chain';
    END IF;

    RETURN NEW;
END;
$legal_required_claim_set_chain$;
REVOKE ALL ON FUNCTION legal_assert_required_claim_set_chain()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_legal_required_claim_set_chain
    ON legal_required_claim_sets;
CREATE TRIGGER trg_legal_required_claim_set_chain
    BEFORE INSERT ON legal_required_claim_sets
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_required_claim_set_chain();

CREATE OR REPLACE FUNCTION legal_assert_required_claim_set_complete()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_required_claim_set_complete$
DECLARE
    target_claim_set_id TEXT;
    stored_hash TEXT;
    computed_hash TEXT;
    claim_count BIGINT;
    claim_status TEXT;
BEGIN
    target_claim_set_id := CASE
        WHEN TG_TABLE_NAME = 'legal_required_claim_sets'
            THEN NEW.legal_required_claim_set_id
        ELSE NEW.legal_required_claim_set_id
    END;

    EXECUTE pg_catalog.format(
        'SELECT claim_set.status, claim_set.claim_set_sha256, '
        'COUNT(claim.legal_required_claim_id), '
        'encode(extensions.digest(convert_to('
        'string_agg(claim.claim_key || '':'' || claim.claim_text_sha256, '
        'E''\n'' ORDER BY claim.ordinal, claim.claim_key), '
        '''UTF8''), ''sha256''), ''hex'') '
        'FROM %I.legal_required_claim_sets AS claim_set '
        'LEFT JOIN %I.legal_required_claims AS claim '
        'ON claim.legal_required_claim_set_id = '
        'claim_set.legal_required_claim_set_id '
        'AND claim.tenant_id = claim_set.tenant_id '
        'AND claim.project_id = claim_set.project_id '
        'WHERE claim_set.legal_required_claim_set_id = $1 '
        'GROUP BY claim_set.status, claim_set.claim_set_sha256',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO claim_status, stored_hash, claim_count, computed_hash
    USING target_claim_set_id;

    IF claim_status = 'approved' AND (
        claim_count < 1
        OR computed_hash IS DISTINCT FROM stored_hash
    ) THEN
        RAISE EXCEPTION
            'approved required claim set % is empty or hash-mismatched',
            target_claim_set_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_required_claim_set_complete';
    END IF;
    RETURN NEW;
END;
$legal_required_claim_set_complete$;
REVOKE ALL ON FUNCTION legal_assert_required_claim_set_complete()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_legal_required_claim_set_complete
    ON legal_required_claim_sets;
CREATE CONSTRAINT TRIGGER ctrg_legal_required_claim_set_complete
    AFTER INSERT ON legal_required_claim_sets
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_required_claim_set_complete();

DROP TRIGGER IF EXISTS ctrg_legal_required_claim_membership_complete
    ON legal_required_claims;
CREATE CONSTRAINT TRIGGER ctrg_legal_required_claim_membership_complete
    AFTER INSERT ON legal_required_claims
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_required_claim_set_complete();

ALTER TABLE legal_evidence_snapshots
    ADD COLUMN IF NOT EXISTS legal_required_claim_set_id TEXT,
    ADD COLUMN IF NOT EXISTS required_claim_set_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS legal_review_id TEXT;

-- 0008 snapshots are already append-only.  Do not promote a metadata string
-- into normalized review/policy authority by mutating a sealed snapshot.
-- Legacy rows with NULL typed bindings remain Gate-ineligible; callers must
-- append a newly sealed snapshot under this migration's typed contract.
COMMENT ON COLUMN legal_evidence_snapshots.legal_review_id IS
    'Typed normalized review lineage; NULL legacy snapshots require resealing before Approval Readiness.';

ALTER TABLE legal_evidence_snapshots
    DROP CONSTRAINT IF EXISTS fk_legal_evidence_required_claim_set;
ALTER TABLE legal_evidence_snapshots
    ADD CONSTRAINT fk_legal_evidence_required_claim_set
        FOREIGN KEY (
            legal_required_claim_set_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_required_claim_sets(
            legal_required_claim_set_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT;

ALTER TABLE legal_evidence_snapshots
    DROP CONSTRAINT IF EXISTS ck_legal_evidence_required_claim_set;
ALTER TABLE legal_evidence_snapshots
    ADD CONSTRAINT ck_legal_evidence_required_claim_set
        CHECK (
            NOT gate_eligible
            OR (
                NULLIF(btrim(legal_review_id), '') IS NOT NULL
                AND
                legal_required_claim_set_id IS NOT NULL
                AND required_claim_set_sha256 ~ '^[0-9a-f]{64}$'
            )
        ) NOT VALID;

CREATE OR REPLACE FUNCTION legal_assert_snapshot_required_claim_policy()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_snapshot_required_claim_policy$
DECLARE
    policy_valid BOOLEAN;
BEGIN
    IF NOT NEW.gate_eligible THEN
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT EXISTS ('
        'SELECT 1 '
        'FROM %I.legal_required_claim_sets AS claim_set '
        'WHERE claim_set.legal_required_claim_set_id = $1 '
        'AND claim_set.tenant_id = $2 '
        'AND claim_set.project_id = $3 '
        'AND claim_set.review_scope = ''approval_gate'' '
        'AND claim_set.status = ''approved'' '
        'AND claim_set.legal_required_claim_set_id = ('
        'SELECT latest.legal_required_claim_set_id '
        'FROM %I.legal_required_claim_sets AS latest '
        'WHERE latest.tenant_id = claim_set.tenant_id '
        'AND latest.project_id = claim_set.project_id '
        'AND latest.review_scope = ''approval_gate'' '
        'AND latest.status = ''approved'' '
        'ORDER BY latest.approved_at DESC, '
        'latest.claim_set_version DESC, '
        'latest.legal_required_claim_set_id DESC LIMIT 1'
        ') '
        'AND claim_set.claim_set_sha256 = $4 '
        'AND claim_set.approved_by <> claim_set.created_by '
        'AND claim_set.approved_at IS NOT NULL '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_required_claim_sets AS successor '
        'WHERE successor.tenant_id = claim_set.tenant_id '
        'AND successor.project_id = claim_set.project_id '
        'AND successor.claim_set_key = claim_set.claim_set_key '
        'AND successor.claim_set_version > claim_set.claim_set_version '
        'AND successor.status IN (''approved'', ''superseded'')'
        ') '
        'AND EXISTS ('
        'SELECT 1 FROM %I.legal_required_claims AS required_claim '
        'WHERE required_claim.legal_required_claim_set_id = '
        'claim_set.legal_required_claim_set_id'
        ') '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_required_claims AS required_claim '
        'WHERE required_claim.legal_required_claim_set_id = '
        'claim_set.legal_required_claim_set_id '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshot_items AS item '
        'WHERE item.legal_evidence_snapshot_id = $5 '
        'AND item.tenant_id = $2 '
        'AND item.project_id = $3 '
        'AND item.claim_text_sha256 = '
        'required_claim.claim_text_sha256 '
        'AND item.citation_verification_status = ''verified'' '
        'AND item.verified_at IS NOT NULL'
        ')'
        ')'
        ')',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO policy_valid
    USING
        NEW.legal_required_claim_set_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.required_claim_set_sha256,
        NEW.legal_evidence_snapshot_id;

    IF NOT policy_valid THEN
        RAISE EXCEPTION
            'gate-eligible snapshot % does not satisfy the active required-claim policy',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_snapshot_required_claim_policy';
    END IF;
    RETURN NEW;
END;
$legal_snapshot_required_claim_policy$;
REVOKE ALL ON FUNCTION legal_assert_snapshot_required_claim_policy()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_legal_snapshot_required_claim_policy
    ON legal_evidence_snapshots;
CREATE CONSTRAINT TRIGGER ctrg_legal_snapshot_required_claim_policy
    AFTER INSERT ON legal_evidence_snapshots
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_snapshot_required_claim_policy();

-- ---------------------------------------------------------------------------
-- Tenant-native legal matter, waiver, opinion, annotation, and analysis data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_legal_matters (
    project_legal_matter_id        TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    matter_key                     TEXT NOT NULL,
    matter_version                 INTEGER NOT NULL,
    title                          TEXT NOT NULL,
    matter_type                    TEXT NOT NULL,
    status                         TEXT NOT NULL DEFAULT 'open',
    severity                       TEXT NOT NULL DEFAULT 'medium',
    owner_user_id                  TEXT,
    due_at                         TIMESTAMPTZ,
    opened_at                      TIMESTAMPTZ NOT NULL,
    closed_at                      TIMESTAMPTZ,
    supersedes_matter_id           TEXT,
    created_by                     TEXT NOT NULL,
    created_at                     TIMESTAMPTZ NOT NULL,
    legal_evidence_snapshot_id     TEXT,
    resolution_reason              TEXT,
    resolved_by                    TEXT,
    resolver_role                  TEXT,
    legal_approval_delegation_id   TEXT,
    resolved_at                    TIMESTAMPTZ,
    legal_waiver_id                TEXT,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        project_legal_matter_id,
        tenant_id,
        project_id
    ),
    UNIQUE (tenant_id, project_id, matter_key, matter_version),
    CONSTRAINT fk_project_legal_matter_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_matter_supersedes
        FOREIGN KEY (
            supersedes_matter_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_matters(
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_matter_snapshot
        FOREIGN KEY (
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_matter_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_legal_matter_text
        CHECK (
            NULLIF(btrim(matter_key), '') IS NOT NULL
            AND NULLIF(btrim(title), '') IS NOT NULL
            AND NULLIF(btrim(created_by), '') IS NOT NULL
        ),
    CONSTRAINT ck_project_legal_matter_version
        CHECK (matter_version >= 1),
    CONSTRAINT ck_project_legal_matter_type
        CHECK (matter_type IN (
            'statutory',
            'permit',
            'land_title',
            'contract',
            'security',
            'trust',
            'dispute',
            'compliance',
            'other'
        )),
    CONSTRAINT ck_project_legal_matter_status
        CHECK (status IN (
            'open',
            'under_review',
            'blocked',
            'resolved',
            'waived',
            'closed',
            'superseded'
        )),
    CONSTRAINT ck_project_legal_matter_severity
        CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_project_legal_matter_close
        CHECK (
            status NOT IN ('resolved', 'closed', 'superseded')
            OR closed_at IS NOT NULL
        ),
    CONSTRAINT ck_project_legal_matter_not_self_superseding
        CHECK (
            supersedes_matter_id IS NULL
            OR supersedes_matter_id <> project_legal_matter_id
        ),
    CONSTRAINT ck_project_legal_matter_terminal_authority
        CHECK (
            (
                status IN ('resolved', 'waived', 'closed', 'superseded')
                AND legal_evidence_snapshot_id IS NOT NULL
                AND NULLIF(btrim(resolution_reason), '') IS NOT NULL
                AND NULLIF(btrim(resolved_by), '') IS NOT NULL
                AND resolver_role IN (
                    'legal_counsel_approver',
                    'external_counsel'
                )
                AND resolved_at IS NOT NULL
                AND resolved_by = created_by
                AND (
                    (
                        resolver_role = 'external_counsel'
                        AND legal_approval_delegation_id IS NOT NULL
                    )
                    OR (
                        resolver_role = 'legal_counsel_approver'
                        AND legal_approval_delegation_id IS NULL
                    )
                )
                AND (
                    (status = 'waived' AND legal_waiver_id IS NOT NULL)
                    OR (status <> 'waived' AND legal_waiver_id IS NULL)
                )
            )
            OR (
                status NOT IN ('resolved', 'waived', 'closed', 'superseded')
                AND legal_evidence_snapshot_id IS NULL
                AND resolution_reason IS NULL
                AND resolved_by IS NULL
                AND resolver_role IS NULL
                AND legal_approval_delegation_id IS NULL
                AND resolved_at IS NULL
                AND legal_waiver_id IS NULL
            )
        ),
    CONSTRAINT ck_project_legal_matter_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_project_legal_matters_queue
    ON project_legal_matters(
        tenant_id,
        project_id,
        status,
        severity,
        opened_at DESC
    );
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_legal_matter_root
    ON project_legal_matters(
        tenant_id,
        project_id,
        matter_key
    )
    WHERE supersedes_matter_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_legal_matter_single_successor
    ON project_legal_matters(supersedes_matter_id)
    WHERE supersedes_matter_id IS NOT NULL;

CREATE OR REPLACE FUNCTION legal_assert_project_legal_matter_chain()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_project_matter_chain$
DECLARE
    predecessor RECORD;
    predecessor_count INTEGER := 0;
BEGIN
    -- ``created_at`` is the authoritative governance-event time.  It is
    -- assigned by PostgreSQL so a caller cannot backdate a matter mutation
    -- and preserve an approval decision that predates the mutation.
    NEW.created_at := statement_timestamp();
    IF NEW.status IN ('resolved', 'waived', 'closed', 'superseded') THEN
        NEW.resolved_at := NEW.created_at;
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            pg_catalog.concat_ws(
                ':',
                'project-legal-matter',
                NEW.tenant_id,
                NEW.project_id,
                NEW.matter_key
            ),
            0
        )
    );

    IF NEW.opened_at > NEW.created_at
       OR (
           NEW.closed_at IS NOT NULL
           AND (
               NEW.closed_at < NEW.opened_at
               OR NEW.closed_at > NEW.created_at
           )
       )
       OR (
           NEW.status NOT IN ('resolved', 'closed', 'superseded')
           AND NEW.closed_at IS NOT NULL
       )
    THEN
        RAISE EXCEPTION
            'legal matter has an invalid server-time lifecycle'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_legal_matter_chain';
    END IF;

    IF NEW.supersedes_matter_id IS NULL THEN
        IF NEW.matter_version <> 1
           OR NEW.status NOT IN ('open', 'under_review', 'blocked')
           OR NEW.closed_at IS NOT NULL
        THEN
            RAISE EXCEPTION
                'legal matter root must be an active version-1 record'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_project_legal_matter_chain';
        END IF;
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT predecessor.* '
        'FROM %I.project_legal_matters AS predecessor '
        'WHERE predecessor.project_legal_matter_id = $1 '
        'AND predecessor.tenant_id = $2 '
        'AND predecessor.project_id = $3 '
        'AND predecessor.matter_key = $4 '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.project_legal_matters AS successor '
        'WHERE successor.tenant_id = predecessor.tenant_id '
        'AND successor.project_id = predecessor.project_id '
        'AND successor.supersedes_matter_id = '
        'predecessor.project_legal_matter_id'
        ') '
        'FOR UPDATE OF predecessor',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO predecessor
    USING
        NEW.supersedes_matter_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.matter_key;
    GET DIAGNOSTICS predecessor_count = ROW_COUNT;

    IF predecessor_count <> 1
       OR NEW.matter_version <> predecessor.matter_version + 1
       OR NEW.matter_type IS DISTINCT FROM predecessor.matter_type
       OR NEW.opened_at IS DISTINCT FROM predecessor.opened_at
       OR (
           predecessor.status = 'open'
           AND NEW.status NOT IN (
               'open',
               'under_review',
               'blocked',
               'resolved',
               'waived',
               'closed',
               'superseded'
           )
       )
       OR (
           predecessor.status = 'under_review'
           AND NEW.status NOT IN (
               'open',
               'under_review',
               'blocked',
               'resolved',
               'waived',
               'closed',
               'superseded'
           )
       )
       OR (
           predecessor.status = 'blocked'
           AND NEW.status NOT IN (
               'under_review',
               'blocked',
               'resolved',
               'waived',
               'closed',
               'superseded'
           )
       )
       OR (
           predecessor.status = 'waived'
           AND NEW.status NOT IN (
               'under_review',
               'blocked',
               'waived',
               'resolved',
               'closed',
               'superseded'
           )
       )
       OR (
           predecessor.status = 'resolved'
           AND NEW.status NOT IN ('resolved', 'closed', 'superseded')
       )
       OR (
           predecessor.status = 'closed'
           AND NEW.status <> 'superseded'
       )
       OR predecessor.status = 'superseded'
    THEN
        RAISE EXCEPTION
            'legal matter must follow the immediate leaf with an allowed transition'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_legal_matter_chain';
    END IF;

    IF NEW.status IN ('resolved', 'waived', 'closed', 'superseded') THEN
        EXECUTE pg_catalog.format(
            'SELECT root.created_by, snapshot.created_by '
            'FROM %I.project_legal_matters AS root '
            'JOIN %I.legal_evidence_snapshots AS snapshot '
            'ON snapshot.legal_evidence_snapshot_id = $5 '
            'AND snapshot.tenant_id = $2 '
            'AND snapshot.project_id = $3 '
            'WHERE root.tenant_id = $2 '
            'AND root.project_id = $3 '
            'AND root.matter_key = $4 '
            'AND root.matter_version = 1',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO root_creator, snapshot_creator
        USING
            NEW.supersedes_matter_id,
            NEW.tenant_id,
            NEW.project_id,
            NEW.matter_key,
            NEW.legal_evidence_snapshot_id;

        IF NEW.resolved_by IS DISTINCT FROM NEW.created_by
           OR lower(btrim(NEW.resolved_by)) =
                  lower(btrim(predecessor.created_by))
           OR lower(btrim(NEW.resolved_by)) =
                  lower(btrim(root_creator))
           OR lower(btrim(NEW.resolved_by)) =
                  lower(btrim(snapshot_creator))
        THEN
            RAISE EXCEPTION
                'terminal legal matter violates maker-checker separation'
                USING ERRCODE = '23514',
                      CONSTRAINT =
                          'ck_project_legal_matter_maker_checker';
        END IF;
    END IF;

    RETURN NEW;
END;
$legal_project_matter_chain$;
REVOKE ALL ON FUNCTION legal_assert_project_legal_matter_chain()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_project_legal_matter_chain
    ON project_legal_matters;
CREATE TRIGGER trg_project_legal_matter_chain
    BEFORE INSERT ON project_legal_matters
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_project_legal_matter_chain();

CREATE OR REPLACE FUNCTION legal_try_timestamptz(value TEXT)
RETURNS TIMESTAMPTZ
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog, pg_temp
AS $legal_try_timestamptz$
BEGIN
    IF NULLIF(btrim(value), '') IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN value::TIMESTAMPTZ;
EXCEPTION
    WHEN data_exception THEN
        RETURN NULL;
END;
$legal_try_timestamptz$;
REVOKE ALL ON FUNCTION legal_try_timestamptz(TEXT) FROM PUBLIC;

CREATE TABLE IF NOT EXISTS legal_waivers (
    legal_waiver_id                TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    project_legal_matter_id        TEXT NOT NULL,
    legal_evidence_snapshot_id     TEXT NOT NULL,
    waiver_version                 INTEGER NOT NULL,
    status                         TEXT NOT NULL DEFAULT 'proposed',
    reason                         TEXT NOT NULL,
    conditions_json                JSONB NOT NULL,
    valid_from                     TIMESTAMPTZ,
    valid_until                    TIMESTAMPTZ NOT NULL,
    cure_status                    TEXT NOT NULL DEFAULT 'not_started',
    cure_evidence_snapshot_id      TEXT,
    proposed_by                    TEXT NOT NULL,
    proposed_at                    TIMESTAMPTZ NOT NULL,
    recorded_at                    TIMESTAMPTZ NOT NULL
        DEFAULT statement_timestamp(),
    approved_by                    TEXT,
    approver_role                  TEXT,
    legal_approval_delegation_id   TEXT,
    approved_at                    TIMESTAMPTZ,
    cure_reviewed_by               TEXT,
    cure_reviewer_role             TEXT,
    cure_reviewed_at               TIMESTAMPTZ,
    cure_reason                    TEXT,
    cure_condition_outcomes_json   JSONB NOT NULL DEFAULT '[]'::JSONB,
    cure_approval_delegation_id    TEXT,
    supersedes_waiver_id           TEXT,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_waiver_id, tenant_id, project_id),
    UNIQUE (
        tenant_id,
        project_id,
        project_legal_matter_id,
        waiver_version
    ),
    CONSTRAINT fk_legal_waiver_matter
        FOREIGN KEY (
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_matters(
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_waiver_snapshot
        FOREIGN KEY (
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_waiver_cure_snapshot
        FOREIGN KEY (
            cure_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_waiver_approval_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_waiver_cure_delegation
        FOREIGN KEY (
            cure_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_waiver_supersedes
        FOREIGN KEY (supersedes_waiver_id, tenant_id, project_id)
        REFERENCES legal_waivers(
            legal_waiver_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_waiver_version
        CHECK (waiver_version >= 1),
    CONSTRAINT ck_legal_waiver_status
        CHECK (status IN (
            'proposed',
            'approved',
            'rejected',
            'expired',
            'cured',
            'revoked',
            'superseded'
        )),
    CONSTRAINT ck_legal_waiver_reason
        CHECK (
            NULLIF(btrim(reason), '') IS NOT NULL
            AND NULLIF(btrim(proposed_by), '') IS NOT NULL
        ),
    CONSTRAINT ck_legal_waiver_conditions
        CHECK (
            jsonb_typeof(conditions_json) = 'array'
            AND jsonb_array_length(conditions_json) > 0
        ),
    CONSTRAINT ck_legal_waiver_period
        CHECK (
            valid_until > proposed_at
            AND (valid_from IS NULL OR valid_until > valid_from)
        ),
    CONSTRAINT ck_legal_waiver_cure
        CHECK (cure_status IN (
            'not_started',
            'in_progress',
            'evidence_submitted',
            'verified',
            'failed',
            'not_applicable'
        )),
    CONSTRAINT ck_legal_waiver_status_cure_exact
        CHECK (
            (status = 'proposed' AND cure_status = 'not_started')
            OR (
                status = 'approved'
                AND cure_status IN (
                    'not_started',
                    'in_progress',
                    'evidence_submitted'
                )
            )
            OR (status = 'cured' AND cure_status = 'verified')
            OR (status = 'revoked' AND cure_status = 'failed')
            OR (
                status IN ('rejected', 'expired', 'superseded')
                AND cure_status = 'not_applicable'
            )
        ),
    CONSTRAINT ck_legal_waiver_approval
        CHECK (
            status NOT IN ('approved', 'cured', 'revoked')
            OR (
                NULLIF(btrim(approved_by), '') IS NOT NULL
                AND approver_role IN (
                    'legal_counsel_approver',
                    'external_counsel'
                )
                AND approved_at IS NOT NULL
                AND approved_by <> proposed_by
                AND valid_from IS NOT NULL
                AND (
                    (
                        approver_role = 'external_counsel'
                        AND legal_approval_delegation_id IS NOT NULL
                    )
                    OR (
                        approver_role = 'legal_counsel_approver'
                        AND legal_approval_delegation_id IS NULL
                    )
                )
            )
        ),
    CONSTRAINT ck_legal_waiver_cure_evidence
        CHECK (
            cure_status NOT IN ('evidence_submitted', 'verified')
            OR cure_evidence_snapshot_id IS NOT NULL
        ),
    CONSTRAINT ck_legal_waiver_cure_review
        CHECK (
            (
                cure_status = 'not_started'
                AND cure_reviewed_by IS NULL
                AND cure_reviewer_role IS NULL
                AND cure_reviewed_at IS NULL
                AND cure_reason IS NULL
                AND cure_condition_outcomes_json = '[]'::jsonb
                AND cure_approval_delegation_id IS NULL
            )
            OR (
                cure_status <> 'not_started'
                AND NULLIF(btrim(cure_reviewed_by), '') IS NOT NULL
                AND cure_reviewer_role IN (
                    'legal_reviewer',
                    'legal_counsel_approver',
                    'external_counsel'
                )
                AND cure_reviewed_at IS NOT NULL
                AND NULLIF(btrim(cure_reason), '') IS NOT NULL
                AND jsonb_typeof(cure_condition_outcomes_json) = 'array'
                AND (
                    cure_status NOT IN ('verified', 'failed')
                    OR (
                        jsonb_array_length(
                            cure_condition_outcomes_json
                        ) > 0
                        AND cure_evidence_snapshot_id IS NOT NULL
                        AND cure_reviewer_role IN (
                            'legal_counsel_approver',
                            'external_counsel'
                        )
                    )
                )
                AND (
                    (
                        cure_reviewer_role = 'external_counsel'
                        AND cure_approval_delegation_id IS NOT NULL
                    )
                    OR (
                        cure_reviewer_role <> 'external_counsel'
                        AND cure_approval_delegation_id IS NULL
                    )
                )
            )
        ),
    CONSTRAINT ck_legal_waiver_not_self_superseding
        CHECK (
            supersedes_waiver_id IS NULL
            OR supersedes_waiver_id <> legal_waiver_id
        ),
    CONSTRAINT ck_legal_waiver_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_waivers_active
    ON legal_waivers(
        tenant_id,
        project_id,
        status,
        valid_until
    );
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_waiver_single_successor
    ON legal_waivers(supersedes_waiver_id)
    WHERE supersedes_waiver_id IS NOT NULL;

ALTER TABLE project_legal_matters
    DROP CONSTRAINT IF EXISTS fk_project_legal_matter_waiver;
ALTER TABLE project_legal_matters
    ADD CONSTRAINT fk_project_legal_matter_waiver
        FOREIGN KEY (
            legal_waiver_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_waivers(
            legal_waiver_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED;

CREATE OR REPLACE FUNCTION legal_assert_terminal_matter_waiver()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_terminal_matter_waiver$
DECLARE
    binding_valid BOOLEAN := FALSE;
BEGIN
    IF NEW.status <> 'waived' THEN
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT EXISTS ('
        'SELECT 1 FROM %I.legal_waivers AS waiver '
        'WHERE waiver.legal_waiver_id = $1 '
        'AND waiver.tenant_id = $2 '
        'AND waiver.project_id = $3 '
        'AND waiver.project_legal_matter_id = $4 '
        'AND waiver.legal_evidence_snapshot_id = $5 '
        'AND waiver.status = ''approved'' '
        'AND waiver.cure_status IN '
        '(''not_started'', ''in_progress'', ''evidence_submitted'') '
        'AND waiver.valid_from <= $6 '
        'AND waiver.valid_until > $6 '
        'AND waiver.approved_by = $7 '
        'AND waiver.approver_role = $8 '
        'AND waiver.legal_approval_delegation_id '
        'IS NOT DISTINCT FROM $9 '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_waivers AS successor '
        'WHERE successor.tenant_id = waiver.tenant_id '
        'AND successor.project_id = waiver.project_id '
        'AND successor.supersedes_waiver_id = waiver.legal_waiver_id'
        ') '
        'AND NOT EXISTS ('
        'SELECT 1 FROM jsonb_array_elements('
        'waiver.cure_condition_outcomes_json'
        ') AS outcome '
        'WHERE outcome ->> ''status'' = ''failed'''
        ')'
        ')',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO binding_valid
    USING
        NEW.legal_waiver_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.project_legal_matter_id,
        NEW.legal_evidence_snapshot_id,
        NEW.resolved_at,
        NEW.resolved_by,
        NEW.resolver_role,
        NEW.legal_approval_delegation_id;

    IF NOT COALESCE(binding_valid, FALSE) THEN
        RAISE EXCEPTION
            'waived matter requires the current approved same-snapshot waiver'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_legal_matter_waiver';
    END IF;
    RETURN NEW;
END;
$legal_terminal_matter_waiver$;
REVOKE ALL ON FUNCTION legal_assert_terminal_matter_waiver()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_terminal_matter_waiver
    ON project_legal_matters;
CREATE CONSTRAINT TRIGGER ctrg_terminal_matter_waiver
    AFTER INSERT ON project_legal_matters
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_terminal_matter_waiver();

CREATE TABLE IF NOT EXISTS counsel_opinions (
    counsel_opinion_id             TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    project_legal_matter_id        TEXT NOT NULL,
    legal_evidence_snapshot_id     TEXT NOT NULL,
    opinion_version                INTEGER NOT NULL,
    status                         TEXT NOT NULL DEFAULT 'draft',
    object_locator                 TEXT NOT NULL,
    content_sha256                 TEXT NOT NULL,
    privileged                     BOOLEAN NOT NULL DEFAULT TRUE,
    authored_by                    TEXT NOT NULL,
    authored_at                    TIMESTAMPTZ NOT NULL,
    approved_by                    TEXT,
    approver_role                  TEXT,
    legal_approval_delegation_id   TEXT,
    approved_at                    TIMESTAMPTZ,
    supersedes_opinion_id          TEXT,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (counsel_opinion_id, tenant_id, project_id),
    UNIQUE (
        tenant_id,
        project_id,
        project_legal_matter_id,
        opinion_version
    ),
    CONSTRAINT fk_counsel_opinion_matter
        FOREIGN KEY (
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_matters(
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_counsel_opinion_snapshot
        FOREIGN KEY (
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_counsel_opinion_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_counsel_opinion_supersedes
        FOREIGN KEY (supersedes_opinion_id, tenant_id, project_id)
        REFERENCES counsel_opinions(
            counsel_opinion_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_counsel_opinion_version
        CHECK (opinion_version >= 1),
    CONSTRAINT ck_counsel_opinion_status
        CHECK (status IN (
            'draft',
            'issued',
            'approved',
            'withdrawn',
            'superseded'
        )),
    CONSTRAINT ck_counsel_opinion_locator
        CHECK (
            NULLIF(btrim(object_locator), '') IS NOT NULL
            AND object_locator !~* '([?&](token|secret|password|key)=)'
        ),
    CONSTRAINT ck_counsel_opinion_hash
        CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_counsel_opinion_actor
        CHECK (NULLIF(btrim(authored_by), '') IS NOT NULL),
    CONSTRAINT ck_counsel_opinion_approval
        CHECK (
            status <> 'approved'
            OR (
                NULLIF(btrim(approved_by), '') IS NOT NULL
                AND approver_role IN (
                    'legal_counsel_approver',
                    'external_counsel'
                )
                AND approved_at IS NOT NULL
                AND approved_by <> authored_by
                AND (
                    (
                        approver_role = 'external_counsel'
                        AND legal_approval_delegation_id IS NOT NULL
                    )
                    OR (
                        approver_role = 'legal_counsel_approver'
                        AND legal_approval_delegation_id IS NULL
                    )
                )
            )
        ),
    CONSTRAINT ck_counsel_opinion_not_self_superseding
        CHECK (
            supersedes_opinion_id IS NULL
            OR supersedes_opinion_id <> counsel_opinion_id
        ),
    CONSTRAINT ck_counsel_opinion_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_counsel_opinions_matter
    ON counsel_opinions(
        tenant_id,
        project_id,
        project_legal_matter_id,
        opinion_version DESC
    );
CREATE UNIQUE INDEX IF NOT EXISTS uq_counsel_opinion_root
    ON counsel_opinions(
        tenant_id,
        project_id,
        project_legal_matter_id
    )
    WHERE supersedes_opinion_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_counsel_opinion_single_successor
    ON counsel_opinions(supersedes_opinion_id)
    WHERE supersedes_opinion_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS legal_analysis_versions (
    legal_analysis_version_id      TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    project_legal_matter_id        TEXT NOT NULL,
    legal_evidence_snapshot_id     TEXT NOT NULL,
    analysis_kind                  TEXT NOT NULL,
    analysis_version               INTEGER NOT NULL,
    object_locator                 TEXT NOT NULL,
    content_sha256                 TEXT NOT NULL,
    input_snapshot_sha256          TEXT NOT NULL,
    model_id                       TEXT,
    prompt_sha256                  TEXT,
    analysis_only                  BOOLEAN NOT NULL DEFAULT TRUE,
    report_eligible                BOOLEAN NOT NULL DEFAULT FALSE,
    human_review_status            TEXT NOT NULL DEFAULT 'pending',
    reviewed_by                    TEXT,
    reviewer_role                  TEXT,
    legal_approval_delegation_id   TEXT,
    reviewed_at                    TIMESTAMPTZ,
    created_by                     TEXT NOT NULL,
    created_at                     TIMESTAMPTZ NOT NULL,
    supersedes_analysis_version_id TEXT,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_analysis_version_id,
        tenant_id,
        project_id
    ),
    UNIQUE (
        tenant_id,
        project_id,
        project_legal_matter_id,
        analysis_kind,
        analysis_version
    ),
    CONSTRAINT fk_legal_analysis_matter
        FOREIGN KEY (
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_matters(
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_analysis_snapshot
        FOREIGN KEY (
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_analysis_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_analysis_supersedes
        FOREIGN KEY (
            supersedes_analysis_version_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_analysis_versions(
            legal_analysis_version_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_analysis_kind
        CHECK (analysis_kind IN (
            'ai_summary',
            'deterministic_analysis',
            'counsel_summary'
        )),
    CONSTRAINT ck_legal_analysis_version
        CHECK (analysis_version >= 1),
    CONSTRAINT ck_legal_analysis_locator
        CHECK (
            NULLIF(btrim(object_locator), '') IS NOT NULL
            AND object_locator !~* '([?&](token|secret|password|key)=)'
        ),
    CONSTRAINT ck_legal_analysis_hashes
        CHECK (
            content_sha256 ~ '^[0-9a-f]{64}$'
            AND input_snapshot_sha256 ~ '^[0-9a-f]{64}$'
            AND (
                prompt_sha256 IS NULL
                OR prompt_sha256 ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT ck_legal_analysis_review
        CHECK (
            human_review_status IN ('pending', 'approved', 'rejected')
            AND (
                (
                    human_review_status = 'pending'
                    AND reviewed_by IS NULL
                    AND reviewer_role IS NULL
                    AND legal_approval_delegation_id IS NULL
                    AND reviewed_at IS NULL
                    AND analysis_only
                    AND NOT report_eligible
                )
                OR (
                    human_review_status <> 'pending'
                    AND NULLIF(btrim(reviewed_by), '') IS NOT NULL
                    AND reviewer_role IN (
                        'legal_reviewer',
                        'legal_counsel_approver',
                        'external_counsel'
                    )
                    AND (
                        (
                            reviewer_role = 'external_counsel'
                            AND legal_approval_delegation_id IS NOT NULL
                        )
                        OR (
                            reviewer_role <> 'external_counsel'
                            AND legal_approval_delegation_id IS NULL
                        )
                    )
                    AND reviewed_at IS NOT NULL
                    AND reviewed_by <> created_by
                    AND (
                        human_review_status <> 'rejected'
                        OR analysis_only
                    )
                )
            )
        ),
    CONSTRAINT ck_legal_analysis_report_eligibility
        CHECK (
            NOT report_eligible
            OR (
                NOT analysis_only
                AND human_review_status = 'approved'
                AND reviewer_role IN (
                    'legal_counsel_approver',
                    'external_counsel'
                )
            )
        ),
    CONSTRAINT ck_legal_analysis_not_self_superseding
        CHECK (
            supersedes_analysis_version_id IS NULL
            OR supersedes_analysis_version_id <> legal_analysis_version_id
        ),
    CONSTRAINT ck_legal_analysis_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_analysis_versions_matter
    ON legal_analysis_versions(
        tenant_id,
        project_id,
        project_legal_matter_id,
        analysis_kind,
        analysis_version DESC
    );
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_analysis_root
    ON legal_analysis_versions(
        tenant_id,
        project_id,
        project_legal_matter_id,
        analysis_kind
    )
    WHERE supersedes_analysis_version_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_analysis_single_successor
    ON legal_analysis_versions(supersedes_analysis_version_id)
    WHERE supersedes_analysis_version_id IS NOT NULL;

CREATE OR REPLACE FUNCTION legal_assert_counsel_opinion_chain()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_counsel_opinion_chain$
DECLARE
    predecessor RECORD;
    predecessor_count INTEGER := 0;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            pg_catalog.concat_ws(
                ':',
                'counsel-opinion',
                NEW.tenant_id,
                NEW.project_id,
                NEW.project_legal_matter_id
            ),
            0
        )
    );

    IF NEW.supersedes_opinion_id IS NULL THEN
        IF NEW.opinion_version <> 1
           OR NEW.status NOT IN ('draft', 'issued')
           OR NEW.approved_by IS NOT NULL
           OR NEW.approver_role IS NOT NULL
           OR NEW.legal_approval_delegation_id IS NOT NULL
           OR NEW.approved_at IS NOT NULL
        THEN
            RAISE EXCEPTION
                'counsel opinion root must be an unapproved version-1 draft or issue'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_counsel_opinion_chain';
        END IF;
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT predecessor.* '
        'FROM %I.counsel_opinions AS predecessor '
        'WHERE predecessor.counsel_opinion_id = $1 '
        'AND predecessor.tenant_id = $2 '
        'AND predecessor.project_id = $3 '
        'AND predecessor.project_legal_matter_id = $4 '
        'FOR SHARE',
        TG_TABLE_SCHEMA
    )
    INTO predecessor
    USING
        NEW.supersedes_opinion_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.project_legal_matter_id;
    GET DIAGNOSTICS predecessor_count = ROW_COUNT;

    IF predecessor_count <> 1
       OR NEW.opinion_version <> predecessor.opinion_version + 1
       OR NEW.authored_at < predecessor.authored_at
       OR NEW.status NOT IN ('draft', 'issued', 'approved')
    THEN
        RAISE EXCEPTION
            'counsel opinion must follow its immediate same-matter predecessor'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_counsel_opinion_chain';
    END IF;

    IF NEW.status = 'approved' THEN
        IF predecessor.status NOT IN ('draft', 'issued')
           OR NEW.legal_evidence_snapshot_id IS DISTINCT FROM
                predecessor.legal_evidence_snapshot_id
           OR NEW.object_locator IS DISTINCT FROM predecessor.object_locator
           OR NEW.content_sha256 IS DISTINCT FROM predecessor.content_sha256
           OR NEW.privileged IS DISTINCT FROM predecessor.privileged
           OR NEW.authored_by IS DISTINCT FROM predecessor.authored_by
           OR NEW.authored_at IS DISTINCT FROM predecessor.authored_at
           OR (
                NEW.metadata_json - 'legalApprovalDelegationId'
                IS DISTINCT FROM
                predecessor.metadata_json - 'legalApprovalDelegationId'
           )
           OR (
                NEW.legal_approval_delegation_id IS NULL
                AND NEW.metadata_json ? 'legalApprovalDelegationId'
           )
           OR (
                NEW.legal_approval_delegation_id IS NOT NULL
                AND NEW.metadata_json ->> 'legalApprovalDelegationId'
                    IS DISTINCT FROM NEW.legal_approval_delegation_id
           )
        THEN
            RAISE EXCEPTION
                'counsel opinion approval changed sealed authored content'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_counsel_opinion_chain';
        END IF;
    ELSIF NEW.approved_by IS NOT NULL
          OR NEW.approver_role IS NOT NULL
          OR NEW.legal_approval_delegation_id IS NOT NULL
          OR NEW.approved_at IS NOT NULL
    THEN
        RAISE EXCEPTION
            'unreviewed counsel opinion content cannot carry approval fields'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_counsel_opinion_chain';
    END IF;

    RETURN NEW;
END;
$legal_counsel_opinion_chain$;
REVOKE ALL ON FUNCTION legal_assert_counsel_opinion_chain()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_counsel_opinion_chain
    ON counsel_opinions;
CREATE TRIGGER trg_counsel_opinion_chain
    BEFORE INSERT ON counsel_opinions
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_counsel_opinion_chain();

CREATE OR REPLACE FUNCTION legal_assert_analysis_version_chain()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_analysis_version_chain$
DECLARE
    predecessor RECORD;
    predecessor_count INTEGER := 0;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            pg_catalog.concat_ws(
                ':',
                'legal-analysis',
                NEW.tenant_id,
                NEW.project_id,
                NEW.project_legal_matter_id,
                NEW.analysis_kind
            ),
            0
        )
    );

    IF NEW.supersedes_analysis_version_id IS NULL THEN
        IF NEW.analysis_version <> 1
           OR NEW.human_review_status <> 'pending'
           OR NOT NEW.analysis_only
           OR NEW.report_eligible
        THEN
            RAISE EXCEPTION
                'legal analysis root must be a pending analysis-only version 1'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_analysis_version_chain';
        END IF;
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT predecessor.* '
        'FROM %I.legal_analysis_versions AS predecessor '
        'WHERE predecessor.legal_analysis_version_id = $1 '
        'AND predecessor.tenant_id = $2 '
        'AND predecessor.project_id = $3 '
        'AND predecessor.project_legal_matter_id = $4 '
        'AND predecessor.analysis_kind = $5 '
        'FOR SHARE',
        TG_TABLE_SCHEMA
    )
    INTO predecessor
    USING
        NEW.supersedes_analysis_version_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.project_legal_matter_id,
        NEW.analysis_kind;
    GET DIAGNOSTICS predecessor_count = ROW_COUNT;

    IF predecessor_count <> 1
       OR NEW.analysis_version <> predecessor.analysis_version + 1
       OR NEW.created_at < predecessor.created_at
    THEN
        RAISE EXCEPTION
            'legal analysis must follow its immediate same-kind predecessor'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_analysis_version_chain';
    END IF;

    IF NEW.human_review_status IN ('approved', 'rejected') THEN
        IF predecessor.human_review_status <> 'pending'
           OR NEW.legal_evidence_snapshot_id IS DISTINCT FROM
                predecessor.legal_evidence_snapshot_id
           OR NEW.object_locator IS DISTINCT FROM predecessor.object_locator
           OR NEW.content_sha256 IS DISTINCT FROM predecessor.content_sha256
           OR NEW.input_snapshot_sha256 IS DISTINCT FROM
                predecessor.input_snapshot_sha256
           OR NEW.model_id IS DISTINCT FROM predecessor.model_id
           OR NEW.prompt_sha256 IS DISTINCT FROM predecessor.prompt_sha256
           OR NEW.created_by IS DISTINCT FROM predecessor.created_by
           OR NEW.created_at IS DISTINCT FROM predecessor.created_at
           OR (
                NEW.metadata_json
                    - 'reviewReason'
                    - 'legalApprovalDelegationId'
                IS DISTINCT FROM
                predecessor.metadata_json
                    - 'reviewReason'
                    - 'legalApprovalDelegationId'
           )
           OR (
                NEW.legal_approval_delegation_id IS NULL
                AND NEW.metadata_json ? 'legalApprovalDelegationId'
           )
           OR (
                NEW.legal_approval_delegation_id IS NOT NULL
                AND NEW.metadata_json ->> 'legalApprovalDelegationId'
                    IS DISTINCT FROM NEW.legal_approval_delegation_id
           )
        THEN
            RAISE EXCEPTION
                'legal analysis review changed sealed generated content'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_analysis_version_chain';
        END IF;
    ELSIF NEW.human_review_status <> 'pending'
          OR NOT NEW.analysis_only
          OR NEW.report_eligible
          OR NEW.reviewed_by IS NOT NULL
          OR NEW.reviewer_role IS NOT NULL
          OR NEW.legal_approval_delegation_id IS NOT NULL
          OR NEW.reviewed_at IS NOT NULL
    THEN
        RAISE EXCEPTION
            'new legal analysis content must re-enter pending review'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_analysis_version_chain';
    END IF;

    RETURN NEW;
END;
$legal_analysis_version_chain$;
REVOKE ALL ON FUNCTION legal_assert_analysis_version_chain()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_legal_analysis_version_chain
    ON legal_analysis_versions;
CREATE TRIGGER trg_legal_analysis_version_chain
    BEFORE INSERT ON legal_analysis_versions
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_analysis_version_chain();

CREATE OR REPLACE FUNCTION legal_assert_governance_causal_time()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_governance_causal_time$
DECLARE
    lower_bound TIMESTAMPTZ :=
        statement_timestamp() - INTERVAL '5 minutes';
    upper_bound TIMESTAMPTZ :=
        statement_timestamp() + INTERVAL '5 minutes';
BEGIN
    IF TG_TABLE_NAME = 'legal_required_claim_sets' THEN
        IF NEW.created_at > upper_bound
           OR (
                NEW.status = 'approved'
                AND (
                    NEW.approved_at < NEW.created_at
                    OR NEW.approved_at < lower_bound
                    OR NEW.approved_at > upper_bound
                )
           )
        THEN
            RAISE EXCEPTION
                'required-claim approval has an invalid causal timestamp'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_causal_time';
        END IF;
    ELSIF TG_TABLE_NAME = 'counsel_opinions' THEN
        IF NEW.authored_at > upper_bound
           OR (
                NEW.status = 'approved'
                AND (
                    NEW.approved_at < NEW.authored_at
                    OR NEW.approved_at < lower_bound
                    OR NEW.approved_at > upper_bound
                )
           )
        THEN
            RAISE EXCEPTION
                'counsel-opinion approval has an invalid causal timestamp'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_causal_time';
        END IF;
    ELSIF TG_TABLE_NAME = 'legal_analysis_versions' THEN
        IF NEW.created_at > upper_bound
           OR (
                NEW.human_review_status IN ('approved', 'rejected')
                AND (
                    NEW.reviewed_at < NEW.created_at
                    OR NEW.reviewed_at < lower_bound
                    OR NEW.reviewed_at > upper_bound
                )
           )
        THEN
            RAISE EXCEPTION
                'legal-analysis review has an invalid causal timestamp'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_causal_time';
        END IF;
    END IF;
    RETURN NEW;
END;
$legal_governance_causal_time$;
REVOKE ALL ON FUNCTION legal_assert_governance_causal_time()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_legal_required_claim_set_causal_time
    ON legal_required_claim_sets;
CREATE TRIGGER trg_legal_required_claim_set_causal_time
    BEFORE INSERT ON legal_required_claim_sets
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_causal_time();

DROP TRIGGER IF EXISTS trg_counsel_opinion_causal_time
    ON counsel_opinions;
CREATE TRIGGER trg_counsel_opinion_causal_time
    BEFORE INSERT ON counsel_opinions
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_causal_time();

DROP TRIGGER IF EXISTS trg_legal_analysis_causal_time
    ON legal_analysis_versions;
CREATE TRIGGER trg_legal_analysis_causal_time
    BEFORE INSERT ON legal_analysis_versions
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_causal_time();

CREATE OR REPLACE FUNCTION legal_assert_waiver_lifecycle()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_waiver_lifecycle$
DECLARE
    transition_kind TEXT;
    predecessor_valid BOOLEAN := FALSE;
    evidence_binding_valid BOOLEAN := TRUE;
BEGIN
    -- This immutable insertion time, rather than any caller-supplied business
    -- timestamp, invalidates an older approval decision after a waiver event.
    NEW.recorded_at := statement_timestamp();

    IF NEW.status = 'proposed'
       AND NEW.cure_status = 'not_started' THEN
        IF NEW.waiver_version <> 1
           OR NEW.supersedes_waiver_id IS NOT NULL
           OR NEW.proposed_at >
              statement_timestamp() + INTERVAL '5 minutes'
           OR NEW.valid_from IS NOT NULL
           OR NEW.approved_by IS NOT NULL
           OR NEW.approver_role IS NOT NULL
           OR NEW.legal_approval_delegation_id IS NOT NULL
           OR NEW.approved_at IS NOT NULL
        THEN
            RAISE EXCEPTION
                'initial waiver proposal must be version 1 with no predecessor or approval'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_waiver_lifecycle';
        END IF;
        RETURN NEW;
    ELSIF NEW.status = 'approved'
          AND NEW.metadata_json ->> 'transitionType' =
              'matter_binding' THEN
        transition_kind := 'matter_binding';
    ELSIF NEW.status = 'approved'
          AND NEW.cure_status = 'not_started' THEN
        transition_kind := 'approval';
    ELSIF NEW.status = 'approved'
          AND NEW.cure_status = 'in_progress' THEN
        transition_kind := 'in_progress';
    ELSIF NEW.status = 'approved'
          AND NEW.cure_status = 'evidence_submitted' THEN
        transition_kind := 'evidence_submitted';
    ELSIF (
        NEW.status = 'cured'
        AND NEW.cure_status = 'verified'
    ) OR (
        NEW.status = 'revoked'
        AND NEW.cure_status = 'failed'
    ) THEN
        transition_kind := 'terminal';
    ELSE
        RAISE EXCEPTION
            'unsupported waiver status/cure transition'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_waiver_lifecycle';
    END IF;

    IF NEW.supersedes_waiver_id IS NULL
       OR NEW.waiver_version <= 1
       OR NEW.approved_at IS NULL
       OR NEW.valid_from IS NULL
       OR NEW.approved_at < NEW.proposed_at
       OR NEW.valid_from < NEW.approved_at
       OR (
           transition_kind = 'approval'
           AND (
               NEW.approved_at <
                  statement_timestamp() - INTERVAL '5 minutes'
               OR NEW.approved_at >
                  statement_timestamp() + INTERVAL '5 minutes'
           )
       )
       OR lower(btrim(NEW.approved_by)) =
          lower(btrim(NEW.proposed_by))
       OR (
           transition_kind IN (
               'in_progress',
               'evidence_submitted',
               'terminal'
           )
           AND (
               NEW.cure_reviewed_at IS NULL
               OR NEW.cure_reviewed_at < NEW.approved_at
               OR NEW.cure_reviewed_at >= NEW.valid_until
               OR NEW.cure_reviewed_at <
                  statement_timestamp() - INTERVAL '5 minutes'
               OR NEW.cure_reviewed_at >
                  statement_timestamp() + INTERVAL '5 minutes'
           )
       )
    THEN
        RAISE EXCEPTION
            'waiver transition has an invalid predecessor or causal timestamp'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_waiver_lifecycle';
    END IF;

    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'legal-waiver:' || NEW.supersedes_waiver_id,
            0
        )
    );

    EXECUTE pg_catalog.format(
        'SELECT EXISTS ('
        'SELECT 1 FROM %I.legal_waivers AS predecessor '
        'WHERE predecessor.legal_waiver_id = $1 '
        'AND predecessor.tenant_id = $2 '
        'AND predecessor.project_id = $3 '
        'AND predecessor.waiver_version = $5 - 1 '
        'AND predecessor.legal_evidence_snapshot_id = $6 '
        'AND predecessor.reason = $7 '
        'AND predecessor.conditions_json = $8 '
        'AND predecessor.valid_until = $9 '
        'AND predecessor.proposed_by = $10 '
        'AND predecessor.proposed_at = $11 '
        'AND ('
        '($19 = ''approval'' '
        'AND predecessor.project_legal_matter_id = $4 '
        'AND predecessor.status = ''proposed'' '
        'AND predecessor.cure_status = ''not_started'' '
        'AND predecessor.approved_by IS NULL '
        'AND predecessor.approved_at IS NULL '
        'AND predecessor.proposed_at <= $16) '
        'OR '
        '($19 = ''in_progress'' '
        'AND predecessor.project_legal_matter_id = $4 '
        'AND predecessor.status = ''approved'' '
        'AND predecessor.cure_status = ''not_started'' '
        'AND predecessor.valid_from IS NOT DISTINCT FROM $12 '
        'AND predecessor.approved_by IS NOT DISTINCT FROM $13 '
        'AND predecessor.approver_role IS NOT DISTINCT FROM $14 '
        'AND predecessor.legal_approval_delegation_id '
        'IS NOT DISTINCT FROM $15 '
        'AND predecessor.approved_at IS NOT DISTINCT FROM $16 '
        'AND $18 >= predecessor.approved_at) '
        'OR '
        '($19 = ''evidence_submitted'' '
        'AND predecessor.project_legal_matter_id = $4 '
        'AND predecessor.status = ''approved'' '
        'AND predecessor.cure_status = ''in_progress'' '
        'AND predecessor.valid_from IS NOT DISTINCT FROM $12 '
        'AND predecessor.approved_by IS NOT DISTINCT FROM $13 '
        'AND predecessor.approver_role IS NOT DISTINCT FROM $14 '
        'AND predecessor.legal_approval_delegation_id '
        'IS NOT DISTINCT FROM $15 '
        'AND predecessor.approved_at IS NOT DISTINCT FROM $16 '
        'AND predecessor.cure_reviewed_at IS NOT NULL '
        'AND $18 >= predecessor.cure_reviewed_at '
        'AND $17 IS NOT NULL) '
        'OR '
        '($19 = ''terminal'' '
        'AND predecessor.project_legal_matter_id = $4 '
        'AND predecessor.status = ''approved'' '
        'AND predecessor.cure_status = ''evidence_submitted'' '
        'AND predecessor.valid_from IS NOT DISTINCT FROM $12 '
        'AND predecessor.approved_by IS NOT DISTINCT FROM $13 '
        'AND predecessor.approver_role IS NOT DISTINCT FROM $14 '
        'AND predecessor.legal_approval_delegation_id '
        'IS NOT DISTINCT FROM $15 '
        'AND predecessor.approved_at IS NOT DISTINCT FROM $16 '
        'AND predecessor.cure_reviewed_at IS NOT NULL '
        'AND predecessor.cure_evidence_snapshot_id '
        'IS NOT DISTINCT FROM $22 '
        'AND $18 >= predecessor.cure_reviewed_at '
        'AND lower(btrim(predecessor.cure_reviewed_by)) '
        '<> lower(btrim($17))) '
        'OR '
        '($19 = ''matter_binding'' '
        'AND predecessor.status = ''approved'' '
        'AND predecessor.cure_status = $21 '
        'AND predecessor.valid_from IS NOT DISTINCT FROM $12 '
        'AND predecessor.approved_by IS NOT DISTINCT FROM $13 '
        'AND predecessor.approver_role IS NOT DISTINCT FROM $14 '
        'AND predecessor.legal_approval_delegation_id '
        'IS NOT DISTINCT FROM $15 '
        'AND predecessor.approved_at IS NOT DISTINCT FROM $16 '
        'AND predecessor.cure_evidence_snapshot_id '
        'IS NOT DISTINCT FROM $22 '
        'AND predecessor.cure_reviewed_by '
        'IS NOT DISTINCT FROM $23 '
        'AND predecessor.cure_reviewer_role '
        'IS NOT DISTINCT FROM $24 '
        'AND predecessor.cure_reviewed_at '
        'IS NOT DISTINCT FROM $25 '
        'AND predecessor.cure_reason '
        'IS NOT DISTINCT FROM $26 '
        'AND predecessor.cure_condition_outcomes_json '
        '= $27 '
        'AND EXISTS ('
        'SELECT 1 FROM %I.project_legal_matters AS matter '
        'WHERE matter.project_legal_matter_id = $4 '
        'AND matter.tenant_id = $2 '
        'AND matter.project_id = $3 '
        'AND matter.supersedes_matter_id = '
        'predecessor.project_legal_matter_id '
        'AND matter.status = ''waived'' '
        'AND matter.legal_waiver_id = $20 '
        'AND matter.legal_evidence_snapshot_id = $6 '
        'AND matter.resolved_by = $13 '
        'AND matter.resolver_role = $14 '
        'AND matter.legal_approval_delegation_id '
        'IS NOT DISTINCT FROM $15'
        '))'
        ')'
        ')',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO predecessor_valid
    USING
        NEW.supersedes_waiver_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.project_legal_matter_id,
        NEW.waiver_version,
        NEW.legal_evidence_snapshot_id,
        NEW.reason,
        NEW.conditions_json,
        NEW.valid_until,
        NEW.proposed_by,
        NEW.proposed_at,
        NEW.valid_from,
        NEW.approved_by,
        NEW.approver_role,
        NEW.legal_approval_delegation_id,
        NEW.approved_at,
        NEW.cure_reviewed_by,
        NEW.cure_reviewed_at,
        transition_kind,
        NEW.legal_waiver_id,
        NEW.cure_status,
        NEW.cure_evidence_snapshot_id,
        NEW.cure_reviewed_by,
        NEW.cure_reviewer_role,
        NEW.cure_reviewed_at,
        NEW.cure_reason,
        NEW.cure_condition_outcomes_json;

    IF NOT COALESCE(predecessor_valid, FALSE) THEN
        RAISE EXCEPTION
            'waiver transition must supersede the immediate same-scope predecessor'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_waiver_lifecycle';
    END IF;

    IF NEW.status = 'approved'
       AND EXISTS (
           SELECT 1
             FROM jsonb_array_elements(
                      NEW.cure_condition_outcomes_json
                  ) AS outcome
            WHERE outcome ->> 'status' = 'failed'
       )
    THEN
        RAISE EXCEPTION
            'failed waiver condition outcome cannot remain approval-valid'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_waiver_failed_outcome';
    END IF;

    IF NEW.cure_status IN ('evidence_submitted', 'verified') THEN
        EXECUTE pg_catalog.format(
            'SELECT NOT EXISTS ('
            'SELECT 1 FROM jsonb_array_elements($1) AS outcome '
            'WHERE outcome ->> ''status'' = ''satisfied'' '
            'AND ('
            'jsonb_typeof(outcome -> ''evidenceItemIds'') <> ''array'' '
            'OR jsonb_array_length(outcome -> ''evidenceItemIds'') = 0 '
            'OR jsonb_array_length(outcome -> ''evidenceItemIds'') <> ('
            'SELECT COUNT(DISTINCT evidence_id.value) '
            'FROM jsonb_array_elements_text('
            'outcome -> ''evidenceItemIds'''
            ') AS evidence_id(value)'
            ') '
            'OR EXISTS ('
            'SELECT 1 FROM jsonb_array_elements_text('
            'outcome -> ''evidenceItemIds'''
            ') AS evidence_id(value) '
            'WHERE NOT EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_evidence_snapshot_items AS item '
            'JOIN %I.legal_resource_versions AS version '
            'ON version.legal_resource_version_id = '
            'item.legal_resource_version_id '
            'AND version.legal_resource_id = item.legal_resource_id '
            'AND version.source_fetch_id = item.source_fetch_id '
            'JOIN %I.legal_resources AS resource '
            'ON resource.legal_resource_id = item.legal_resource_id '
            'AND resource.provider_id = version.provider_id '
            'JOIN %I.legal_resource_version_lineages AS lineage '
            'ON lineage.legal_resource_version_id = '
            'version.legal_resource_version_id '
            'AND lineage.legal_resource_id = version.legal_resource_id '
            'AND lineage.validated_at <= statement_timestamp() '
            'JOIN %I.legal_source_fetches AS source_fetch '
            'ON source_fetch.source_fetch_id = item.source_fetch_id '
            'AND source_fetch.provider_id = version.provider_id '
            'AND source_fetch.fetch_status IN '
            '(''succeeded'', ''not_modified'') '
            'AND source_fetch.fetched_at <= statement_timestamp() '
            'JOIN %I.legal_source_providers AS provider '
            'ON provider.provider_id = version.provider_id '
            'AND provider.provider_status = ''active'' '
            'JOIN %I.legal_provisions AS provision '
            'ON provision.legal_provision_id = item.legal_provision_id '
            'AND provision.legal_resource_version_id = '
            'item.legal_resource_version_id '
            'AND provision.legal_resource_id = item.legal_resource_id '
            'AND provision.source_locator = item.exact_locator '
            'WHERE item.legal_evidence_item_id = evidence_id.value '
            'AND item.legal_evidence_snapshot_id = $2 '
            'AND item.tenant_id = $3 '
            'AND item.project_id = $4 '
            'AND item.citation_verification_status = ''verified'' '
            'AND item.verified_at IS NOT NULL '
            'AND item.verified_at <= statement_timestamp() '
            'AND version.verification_status IN '
            '(''source_verified'', ''schema_verified'') '
            'AND version.verified_at IS NOT NULL '
            'AND version.verified_at <= statement_timestamp()'
            ')'
            ')'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO evidence_binding_valid
        USING
            NEW.cure_condition_outcomes_json,
            NEW.cure_evidence_snapshot_id,
            NEW.tenant_id,
            NEW.project_id;

        IF NOT COALESCE(evidence_binding_valid, FALSE) THEN
            RAISE EXCEPTION
                'satisfied waiver outcomes require verified exact/current cure evidence items'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_waiver_cure_evidence_items';
        END IF;
    END IF;

    RETURN NEW;
END;
$legal_waiver_lifecycle$;
REVOKE ALL ON FUNCTION legal_assert_waiver_lifecycle() FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_legal_waiver_lifecycle ON legal_waivers;
CREATE TRIGGER trg_legal_waiver_lifecycle
    BEFORE INSERT ON legal_waivers
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_waiver_lifecycle();

CREATE OR REPLACE FUNCTION legal_assert_governance_delegation()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_governance_delegation$
DECLARE
    actor_id TEXT;
    actor_role TEXT;
    delegation_id TEXT;
    delegation_scope TEXT;
    authority_at TIMESTAMPTZ;
    delegation_valid BOOLEAN;
    cure_delegation_valid BOOLEAN;
BEGIN
    IF TG_TABLE_NAME = 'project_legal_matters'
       AND NEW.status IN (
           'resolved',
           'waived',
           'closed',
           'superseded'
       ) THEN
        actor_id := NEW.resolved_by;
        actor_role := NEW.resolver_role;
        delegation_id := NEW.legal_approval_delegation_id;
        delegation_scope := 'matter_resolution';
        authority_at := NEW.resolved_at;
    ELSIF TG_TABLE_NAME = 'legal_required_claim_sets'
       AND NEW.status = 'approved' THEN
        actor_id := NEW.approved_by;
        actor_role := NEW.approver_role;
        delegation_id := NEW.legal_approval_delegation_id;
        delegation_scope := 'approval_gate';
        authority_at := NEW.approved_at;
    ELSIF TG_TABLE_NAME = 'legal_waivers'
       AND NEW.status IN ('approved', 'cured', 'revoked') THEN
        actor_id := NEW.approved_by;
        actor_role := NEW.approver_role;
        delegation_id := NEW.legal_approval_delegation_id;
        delegation_scope := 'waiver_approval';
        authority_at := NEW.approved_at;
    ELSIF TG_TABLE_NAME = 'counsel_opinions'
       AND NEW.status = 'approved' THEN
        actor_id := NEW.approved_by;
        actor_role := NEW.approver_role;
        delegation_id := NEW.legal_approval_delegation_id;
        delegation_scope := 'counsel_opinion';
        authority_at := NEW.approved_at;
    ELSIF TG_TABLE_NAME = 'legal_analysis_versions'
       AND NEW.human_review_status IN ('approved', 'rejected') THEN
        actor_id := NEW.reviewed_by;
        actor_role := NEW.reviewer_role;
        delegation_id := NEW.legal_approval_delegation_id;
        delegation_scope := 'approval_gate';
        authority_at := NEW.reviewed_at;
    ELSE
        RETURN NEW;
    END IF;

    IF delegation_id IS NOT NULL THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS delegation '
            'WHERE delegation.legal_approval_delegation_id = $1 '
            'AND delegation.tenant_id = $2 '
            'AND delegation.project_id = $3 '
            'AND delegation.grantee_id = $4 '
            'AND delegation.grantee_role = $5 '
            'AND delegation.scope = $6 '
            'AND delegation.status = ''active'' '
            'AND delegation.created_at <= $7 '
            'AND delegation.valid_from <= $7 '
            'AND delegation.valid_until >= $7 '
            'AND NOT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS successor '
            'WHERE successor.tenant_id = delegation.tenant_id '
            'AND successor.project_id = delegation.project_id '
            'AND successor.delegation_key = delegation.delegation_key '
            'AND successor.delegation_version > '
            'delegation.delegation_version '
            'AND successor.created_at <= $7'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO delegation_valid
        USING
            delegation_id,
            NEW.tenant_id,
            NEW.project_id,
            actor_id,
            actor_role,
            delegation_scope,
            authority_at;
        IF NOT COALESCE(delegation_valid, FALSE) THEN
            RAISE EXCEPTION
                'governance row references invalid delegated authority'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_delegation';
        END IF;
    END IF;

    IF TG_TABLE_NAME = 'legal_waivers'
       AND NEW.cure_status <> 'not_started'
       AND NEW.cure_approval_delegation_id IS NOT NULL THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS delegation '
            'WHERE delegation.legal_approval_delegation_id = $1 '
            'AND delegation.tenant_id = $2 '
            'AND delegation.project_id = $3 '
            'AND delegation.grantee_id = $4 '
            'AND delegation.grantee_role = $5 '
            'AND delegation.scope = ''waiver_approval'' '
            'AND delegation.status = ''active'' '
            'AND delegation.created_at <= $6 '
            'AND delegation.valid_from <= $6 '
            'AND delegation.valid_until >= $6 '
            'AND NOT EXISTS ('
            'SELECT 1 FROM %I.legal_approval_delegations AS successor '
            'WHERE successor.tenant_id = delegation.tenant_id '
            'AND successor.project_id = delegation.project_id '
            'AND successor.delegation_key = delegation.delegation_key '
            'AND successor.delegation_version > '
            'delegation.delegation_version '
            'AND successor.created_at <= $6'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO cure_delegation_valid
        USING
            NEW.cure_approval_delegation_id,
            NEW.tenant_id,
            NEW.project_id,
            NEW.cure_reviewed_by,
            NEW.cure_reviewer_role,
            NEW.cure_reviewed_at;
        IF NOT COALESCE(cure_delegation_valid, FALSE) THEN
            RAISE EXCEPTION
                'waiver cure references invalid delegated authority'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_delegation';
        END IF;
    END IF;

    RETURN NEW;
END;
$legal_governance_delegation$;
REVOKE ALL ON FUNCTION legal_assert_governance_delegation()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_legal_required_claim_delegation
    ON legal_required_claim_sets;
CREATE CONSTRAINT TRIGGER ctrg_legal_required_claim_delegation
    AFTER INSERT ON legal_required_claim_sets
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_delegation();

DROP TRIGGER IF EXISTS ctrg_project_legal_matter_delegation
    ON project_legal_matters;
CREATE CONSTRAINT TRIGGER ctrg_project_legal_matter_delegation
    AFTER INSERT ON project_legal_matters
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_delegation();

DROP TRIGGER IF EXISTS ctrg_legal_waiver_delegation
    ON legal_waivers;
CREATE CONSTRAINT TRIGGER ctrg_legal_waiver_delegation
    AFTER INSERT ON legal_waivers
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_delegation();

DROP TRIGGER IF EXISTS ctrg_counsel_opinion_delegation
    ON counsel_opinions;
CREATE CONSTRAINT TRIGGER ctrg_counsel_opinion_delegation
    AFTER INSERT ON counsel_opinions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_delegation();

DROP TRIGGER IF EXISTS ctrg_legal_analysis_delegation
    ON legal_analysis_versions;
CREATE CONSTRAINT TRIGGER ctrg_legal_analysis_delegation
    AFTER INSERT ON legal_analysis_versions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_delegation();

CREATE OR REPLACE FUNCTION legal_assert_governance_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_governance_snapshot$
DECLARE
    snapshot_id TEXT;
    expected_snapshot_sha256 TEXT;
    require_gate_eligible BOOLEAN := FALSE;
    snapshot_valid BOOLEAN;
    cure_snapshot_valid BOOLEAN;
    cure_snapshot_creator TEXT;
    waiver_condition_outcomes_valid BOOLEAN;
    predecessor_cure_actor TEXT;
BEGIN
    IF TG_TABLE_NAME = 'legal_waivers' THEN
        SELECT
            NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(NEW.conditions_json)
                    AS condition_entry
                WHERE jsonb_typeof(condition_entry) <> 'object'
                   OR NULLIF(
                        btrim(condition_entry ->> 'code'),
                        ''
                    ) IS NULL
                   OR NULLIF(
                        btrim(condition_entry ->> 'description'),
                        ''
                    ) IS NULL
            )
            AND jsonb_array_length(NEW.conditions_json) = (
                SELECT count(DISTINCT condition_entry ->> 'code')
                FROM jsonb_array_elements(NEW.conditions_json)
                    AS condition_entry
            )
            AND NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    NEW.cure_condition_outcomes_json
                ) AS outcome_entry
                WHERE jsonb_typeof(outcome_entry) <> 'object'
                   OR NULLIF(
                        btrim(outcome_entry ->> 'code'),
                        ''
                    ) IS NULL
                   OR outcome_entry ->> 'status' NOT IN (
                        'in_progress',
                        'satisfied',
                        'failed'
                    )
            )
            AND jsonb_array_length(
                NEW.cure_condition_outcomes_json
            ) = (
                SELECT count(DISTINCT outcome_entry ->> 'code')
                FROM jsonb_array_elements(
                    NEW.cure_condition_outcomes_json
                ) AS outcome_entry
            )
            AND NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(
                    NEW.cure_condition_outcomes_json
                ) AS outcome_entry
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(NEW.conditions_json)
                        AS condition_entry
                    WHERE condition_entry ->> 'code' =
                        outcome_entry ->> 'code'
                )
            )
            AND (
                NEW.cure_status NOT IN ('verified', 'failed')
                OR (
                    NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(NEW.conditions_json)
                            AS condition_entry
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(
                                NEW.cure_condition_outcomes_json
                            ) AS outcome_entry
                            WHERE outcome_entry ->> 'code' =
                                condition_entry ->> 'code'
                        )
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(
                            NEW.cure_condition_outcomes_json
                        ) AS outcome_entry
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(
                                NEW.conditions_json
                            ) AS condition_entry
                            WHERE condition_entry ->> 'code' =
                                outcome_entry ->> 'code'
                        )
                    )
                    AND (
                        NEW.cure_status <> 'verified'
                        OR NOT EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(
                                NEW.cure_condition_outcomes_json
                            ) AS outcome_entry
                            WHERE outcome_entry ->> 'status' <>
                                'satisfied'
                        )
                    )
                    AND (
                        NEW.cure_status <> 'failed'
                        OR EXISTS (
                            SELECT 1
                            FROM jsonb_array_elements(
                                NEW.cure_condition_outcomes_json
                            ) AS outcome_entry
                            WHERE outcome_entry ->> 'status' = 'failed'
                        )
                    )
                )
            )
        INTO waiver_condition_outcomes_valid;

        IF NOT COALESCE(waiver_condition_outcomes_valid, FALSE) THEN
            RAISE EXCEPTION
                'waiver cure outcomes do not match waiver conditions'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_waiver_condition_outcomes';
        END IF;
    END IF;

    IF TG_TABLE_NAME = 'project_legal_matters'
       AND NEW.status IN (
           'resolved',
           'waived',
           'closed',
           'superseded'
       ) THEN
        snapshot_id := NEW.legal_evidence_snapshot_id;
        require_gate_eligible := TRUE;
    ELSIF TG_TABLE_NAME = 'legal_waivers'
       AND NEW.status IN ('approved', 'cured', 'revoked') THEN
        snapshot_id := NEW.legal_evidence_snapshot_id;
    ELSIF TG_TABLE_NAME = 'counsel_opinions'
       AND NEW.status = 'approved' THEN
        snapshot_id := NEW.legal_evidence_snapshot_id;
        require_gate_eligible := TRUE;
    ELSIF TG_TABLE_NAME = 'legal_analysis_versions'
       AND NEW.report_eligible THEN
        snapshot_id := NEW.legal_evidence_snapshot_id;
        expected_snapshot_sha256 := NEW.input_snapshot_sha256;
        require_gate_eligible := TRUE;
    ELSE
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshots AS snapshot '
        'WHERE snapshot.legal_evidence_snapshot_id = $1 '
        'AND snapshot.tenant_id = $2 '
        'AND snapshot.project_id = $3 '
        'AND snapshot.citation_verification_status = ''verified'' '
        'AND NOT snapshot.is_stale '
        'AND ($5 IS NULL OR snapshot.snapshot_sha256 = $5) '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshots AS successor '
        'WHERE successor.tenant_id = snapshot.tenant_id '
        'AND successor.project_id = snapshot.project_id '
        'AND (successor.supersedes_snapshot_id = '
        'snapshot.legal_evidence_snapshot_id '
        'OR successor.snapshot_version > snapshot.snapshot_version)'
        ') '
        'AND EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshot_items AS item '
        'WHERE item.legal_evidence_snapshot_id = '
        'snapshot.legal_evidence_snapshot_id '
        'AND item.tenant_id = snapshot.tenant_id '
        'AND item.project_id = snapshot.project_id '
        'AND item.citation_verification_status = ''verified'' '
        'AND item.verified_at IS NOT NULL '
        'AND item.verified_at <= statement_timestamp()'
        ') '
        'AND NOT EXISTS ('
        'SELECT 1 '
        'FROM %I.legal_evidence_snapshot_items AS lineage_item '
        'LEFT JOIN %I.legal_resource_versions AS lineage_version '
        'ON lineage_version.legal_resource_version_id = '
        'lineage_item.legal_resource_version_id '
        'AND lineage_version.legal_resource_id = '
        'lineage_item.legal_resource_id '
        'LEFT JOIN %I.legal_source_fetches AS lineage_fetch '
        'ON lineage_fetch.source_fetch_id = lineage_item.source_fetch_id '
        'AND lineage_fetch.provider_id = lineage_version.provider_id '
        'LEFT JOIN %I.legal_source_providers AS lineage_provider '
        'ON lineage_provider.provider_id = lineage_version.provider_id '
        'LEFT JOIN %I.legal_provisions AS lineage_provision '
        'ON lineage_provision.legal_provision_id = '
        'lineage_item.legal_provision_id '
        'AND lineage_provision.legal_resource_version_id = '
        'lineage_item.legal_resource_version_id '
        'AND lineage_provision.legal_resource_id = '
        'lineage_item.legal_resource_id '
        'WHERE lineage_item.legal_evidence_snapshot_id = '
        'snapshot.legal_evidence_snapshot_id '
        'AND lineage_item.tenant_id = snapshot.tenant_id '
        'AND lineage_item.project_id = snapshot.project_id '
        'AND ('
        'lineage_item.citation_verification_status <> ''verified'' '
        'OR lineage_item.verified_at IS NULL '
        'OR lineage_item.verified_at > statement_timestamp() '
        'OR snapshot.freshness_policy_version <> '
        '''legal-freshness-v1'' '
        'OR lineage_item.verified_at + INTERVAL ''7 days'' < '
        'statement_timestamp() '
        'OR lineage_version.legal_resource_version_id IS NULL '
        'OR lineage_version.verification_status NOT IN '
        '(''source_verified'', ''schema_verified'') '
        'OR lineage_version.verified_at IS NULL '
        'OR lineage_version.verified_at > statement_timestamp() '
        'OR lineage_version.source_fetch_id IS DISTINCT FROM '
        'lineage_item.source_fetch_id '
        'OR lineage_fetch.source_fetch_id IS NULL '
        'OR lineage_fetch.fetch_status NOT IN '
        '(''succeeded'', ''not_modified'') '
        'OR lineage_provider.provider_id IS NULL '
        'OR lineage_provider.provider_status <> ''active'' '
        'OR lineage_provision.legal_provision_id IS NULL '
        'OR btrim(lineage_item.exact_locator) = '''' '
        'OR lineage_provision.source_locator IS DISTINCT FROM '
        'lineage_item.exact_locator '
        'OR (lineage_version.effective_from IS NOT NULL '
        'AND lineage_version.effective_from > snapshot.as_of_date) '
        'OR (lineage_version.effective_to IS NOT NULL '
        'AND lineage_version.effective_to < snapshot.as_of_date) '
        'OR (lineage_version.valid_time_start IS NOT NULL '
        'AND lineage_version.valid_time_start > snapshot.as_of_date) '
        'OR (lineage_version.valid_time_end IS NOT NULL '
        'AND lineage_version.valid_time_end < snapshot.as_of_date) '
        'OR EXISTS ('
        'SELECT 1 '
        'FROM %I.legal_resource_versions AS replacement '
        'JOIN %I.legal_source_fetches AS replacement_fetch '
        'ON replacement_fetch.source_fetch_id = '
        'replacement.source_fetch_id '
        'AND replacement_fetch.provider_id = replacement.provider_id '
        'JOIN %I.legal_source_providers AS replacement_provider '
        'ON replacement_provider.provider_id = replacement.provider_id '
        'WHERE replacement.legal_resource_id = '
        'lineage_item.legal_resource_id '
        'AND replacement.version_sequence > '
        'lineage_version.version_sequence '
        'AND replacement.verification_status IN '
        '(''source_verified'', ''schema_verified'') '
        'AND replacement.verified_at IS NOT NULL '
        'AND replacement.verified_at <= statement_timestamp() '
        'AND replacement_fetch.fetch_status IN '
        '(''succeeded'', ''not_modified'') '
        'AND replacement_provider.provider_status = ''active'' '
        'AND (replacement.supersedes_version_id = '
        'lineage_version.legal_resource_version_id '
        'OR ('
        '(replacement.effective_from IS NULL '
        'OR replacement.effective_from <= snapshot.as_of_date) '
        'AND (replacement.effective_to IS NULL '
        'OR replacement.effective_to >= snapshot.as_of_date) '
        'AND (replacement.valid_time_start IS NULL '
        'OR replacement.valid_time_start <= snapshot.as_of_date) '
        'AND (replacement.valid_time_end IS NULL '
        'OR replacement.valid_time_end >= snapshot.as_of_date)'
        ')'
        ')'
        ')'
        ')'
        ') '
        'AND (NOT $4 OR ('
        'snapshot.gate_eligible '
        'AND NOT snapshot.has_open_conflicts '
        'AND EXISTS ('
        'SELECT 1 FROM %I.legal_required_claim_sets AS claim_set '
        'WHERE claim_set.legal_required_claim_set_id = '
        'snapshot.legal_required_claim_set_id '
        'AND claim_set.tenant_id = snapshot.tenant_id '
        'AND claim_set.project_id = snapshot.project_id '
        'AND claim_set.status = ''approved'' '
        'AND claim_set.review_scope = ''approval_gate'' '
        'AND claim_set.claim_set_sha256 = '
        'snapshot.required_claim_set_sha256 '
        'AND claim_set.legal_required_claim_set_id = ('
        'SELECT latest.legal_required_claim_set_id '
        'FROM %I.legal_required_claim_sets AS latest '
        'WHERE latest.tenant_id = snapshot.tenant_id '
        'AND latest.project_id = snapshot.project_id '
        'AND latest.review_scope = ''approval_gate'' '
        'AND latest.status = ''approved'' '
        'ORDER BY latest.approved_at DESC, '
        'latest.claim_set_version DESC, '
        'latest.legal_required_claim_set_id DESC LIMIT 1'
        ') '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_required_claims AS required_claim '
        'WHERE required_claim.legal_required_claim_set_id = '
        'claim_set.legal_required_claim_set_id '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.legal_evidence_snapshot_items AS covered '
        'WHERE covered.legal_evidence_snapshot_id = '
        'snapshot.legal_evidence_snapshot_id '
        'AND covered.tenant_id = snapshot.tenant_id '
        'AND covered.project_id = snapshot.project_id '
        'AND covered.claim_text_sha256 = '
        'required_claim.claim_text_sha256 '
        'AND covered.citation_verification_status = ''verified'' '
        'AND covered.verified_at IS NOT NULL'
        ')'
        ')'
        ')'
        '))'
        ')',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO snapshot_valid
    USING
        snapshot_id,
        NEW.tenant_id,
        NEW.project_id,
        require_gate_eligible,
        expected_snapshot_sha256;

    IF NOT COALESCE(snapshot_valid, FALSE) THEN
        RAISE EXCEPTION
            'governance approval references stale or ineligible evidence'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_governance_snapshot';
    END IF;

    IF TG_TABLE_NAME = 'legal_waivers'
       AND NEW.cure_status IN ('verified', 'failed') THEN
        EXECUTE pg_catalog.format(
            'SELECT EXISTS ('
            'SELECT 1 FROM %I.legal_evidence_snapshots AS snapshot '
            'WHERE snapshot.legal_evidence_snapshot_id = $1 '
            'AND snapshot.tenant_id = $2 '
            'AND snapshot.project_id = $3 '
            'AND snapshot.gate_eligible '
            'AND snapshot.citation_verification_status = ''verified'' '
            'AND NOT snapshot.has_open_conflicts '
            'AND NOT snapshot.is_stale '
            'AND NOT EXISTS ('
            'SELECT 1 FROM %I.legal_evidence_snapshots AS successor '
            'WHERE successor.tenant_id = snapshot.tenant_id '
            'AND successor.project_id = snapshot.project_id '
            'AND (successor.supersedes_snapshot_id = '
            'snapshot.legal_evidence_snapshot_id '
            'OR successor.snapshot_version > snapshot.snapshot_version)'
            ') '
            'AND EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_evidence_snapshot_items AS item '
            'WHERE item.legal_evidence_snapshot_id = '
            'snapshot.legal_evidence_snapshot_id '
            'AND item.tenant_id = snapshot.tenant_id '
            'AND item.project_id = snapshot.project_id '
            'AND item.citation_verification_status = ''verified'' '
            'AND item.verified_at IS NOT NULL '
            'AND item.verified_at <= statement_timestamp()'
            ') '
            'AND NOT EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_evidence_snapshot_items AS lineage_item '
            'LEFT JOIN %I.legal_resource_versions AS lineage_version '
            'ON lineage_version.legal_resource_version_id = '
            'lineage_item.legal_resource_version_id '
            'AND lineage_version.legal_resource_id = '
            'lineage_item.legal_resource_id '
            'LEFT JOIN %I.legal_source_fetches AS lineage_fetch '
            'ON lineage_fetch.source_fetch_id = '
            'lineage_item.source_fetch_id '
            'AND lineage_fetch.provider_id = '
            'lineage_version.provider_id '
            'LEFT JOIN %I.legal_source_providers AS lineage_provider '
            'ON lineage_provider.provider_id = '
            'lineage_version.provider_id '
            'LEFT JOIN %I.legal_provisions AS lineage_provision '
            'ON lineage_provision.legal_provision_id = '
            'lineage_item.legal_provision_id '
            'AND lineage_provision.legal_resource_version_id = '
            'lineage_item.legal_resource_version_id '
            'AND lineage_provision.legal_resource_id = '
            'lineage_item.legal_resource_id '
            'WHERE lineage_item.legal_evidence_snapshot_id = '
            'snapshot.legal_evidence_snapshot_id '
            'AND lineage_item.tenant_id = snapshot.tenant_id '
            'AND lineage_item.project_id = snapshot.project_id '
            'AND ('
            'lineage_item.citation_verification_status <> ''verified'' '
            'OR lineage_item.verified_at IS NULL '
            'OR lineage_item.verified_at > statement_timestamp() '
            'OR snapshot.freshness_policy_version <> '
            '''legal-freshness-v1'' '
            'OR lineage_item.verified_at + INTERVAL ''7 days'' < '
            'statement_timestamp() '
            'OR lineage_version.legal_resource_version_id IS NULL '
            'OR lineage_version.verification_status NOT IN '
            '(''source_verified'', ''schema_verified'') '
            'OR lineage_version.verified_at IS NULL '
            'OR lineage_version.verified_at > statement_timestamp() '
            'OR lineage_version.source_fetch_id IS DISTINCT FROM '
            'lineage_item.source_fetch_id '
            'OR lineage_fetch.source_fetch_id IS NULL '
            'OR lineage_fetch.fetch_status NOT IN '
            '(''succeeded'', ''not_modified'') '
            'OR lineage_provider.provider_id IS NULL '
            'OR lineage_provider.provider_status <> ''active'' '
            'OR lineage_provision.legal_provision_id IS NULL '
            'OR btrim(lineage_item.exact_locator) = '''' '
            'OR lineage_provision.source_locator IS DISTINCT FROM '
            'lineage_item.exact_locator '
            'OR (lineage_version.effective_from IS NOT NULL '
            'AND lineage_version.effective_from > snapshot.as_of_date) '
            'OR (lineage_version.effective_to IS NOT NULL '
            'AND lineage_version.effective_to < snapshot.as_of_date) '
            'OR (lineage_version.valid_time_start IS NOT NULL '
            'AND lineage_version.valid_time_start > snapshot.as_of_date) '
            'OR (lineage_version.valid_time_end IS NOT NULL '
            'AND lineage_version.valid_time_end < snapshot.as_of_date) '
            'OR EXISTS ('
            'SELECT 1 '
            'FROM %I.legal_resource_versions AS replacement '
            'JOIN %I.legal_source_fetches AS replacement_fetch '
            'ON replacement_fetch.source_fetch_id = '
            'replacement.source_fetch_id '
            'AND replacement_fetch.provider_id = replacement.provider_id '
            'JOIN %I.legal_source_providers AS replacement_provider '
            'ON replacement_provider.provider_id = '
            'replacement.provider_id '
            'WHERE replacement.legal_resource_id = '
            'lineage_item.legal_resource_id '
            'AND replacement.version_sequence > '
            'lineage_version.version_sequence '
            'AND replacement.verification_status IN '
            '(''source_verified'', ''schema_verified'') '
            'AND replacement.verified_at IS NOT NULL '
            'AND replacement.verified_at <= statement_timestamp() '
            'AND replacement_fetch.fetch_status IN '
            '(''succeeded'', ''not_modified'') '
            'AND replacement_provider.provider_status = ''active'' '
            'AND (replacement.supersedes_version_id = '
            'lineage_version.legal_resource_version_id '
            'OR ('
            '(replacement.effective_from IS NULL '
            'OR replacement.effective_from <= snapshot.as_of_date) '
            'AND (replacement.effective_to IS NULL '
            'OR replacement.effective_to >= snapshot.as_of_date) '
            'AND (replacement.valid_time_start IS NULL '
            'OR replacement.valid_time_start <= snapshot.as_of_date) '
            'AND (replacement.valid_time_end IS NULL '
            'OR replacement.valid_time_end >= snapshot.as_of_date)'
            ')'
            ')'
            ')'
            ')'
            ')'
            ')',
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO cure_snapshot_valid
        USING
            NEW.cure_evidence_snapshot_id,
            NEW.tenant_id,
            NEW.project_id;
        IF NOT COALESCE(cure_snapshot_valid, FALSE) THEN
            RAISE EXCEPTION
                'terminal waiver cure requires current verified evidence'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_snapshot';
        END IF;

        EXECUTE pg_catalog.format(
            'SELECT snapshot.created_by '
            'FROM %I.legal_evidence_snapshots AS snapshot '
            'WHERE snapshot.legal_evidence_snapshot_id = $1 '
            'AND snapshot.tenant_id = $2 '
            'AND snapshot.project_id = $3',
            TG_TABLE_SCHEMA
        )
        INTO cure_snapshot_creator
        USING
            NEW.cure_evidence_snapshot_id,
            NEW.tenant_id,
            NEW.project_id;
        IF cure_snapshot_creator IS NOT NULL
           AND lower(btrim(cure_snapshot_creator)) =
               lower(btrim(NEW.cure_reviewed_by)) THEN
            RAISE EXCEPTION
                'waiver cure evidence maker cannot verify its own snapshot'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_snapshot';
        END IF;

        EXECUTE pg_catalog.format(
            'SELECT predecessor.cure_reviewed_by '
            'FROM %I.legal_waivers AS predecessor '
            'WHERE predecessor.legal_waiver_id = $1 '
            'AND predecessor.tenant_id = $2 '
            'AND predecessor.project_id = $3',
            TG_TABLE_SCHEMA
        )
        INTO predecessor_cure_actor
        USING
            NEW.supersedes_waiver_id,
            NEW.tenant_id,
            NEW.project_id;
        IF predecessor_cure_actor IS NOT NULL
           AND lower(btrim(predecessor_cure_actor)) =
               lower(btrim(NEW.cure_reviewed_by)) THEN
            RAISE EXCEPTION
                'waiver cure maker cannot verify its own transition'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_legal_governance_snapshot';
        END IF;
    END IF;

    RETURN NEW;
END;
$legal_governance_snapshot$;
REVOKE ALL ON FUNCTION legal_assert_governance_snapshot()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_project_legal_matter_snapshot
    ON project_legal_matters;
CREATE CONSTRAINT TRIGGER ctrg_project_legal_matter_snapshot
    AFTER INSERT ON project_legal_matters
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_snapshot();

DROP TRIGGER IF EXISTS ctrg_legal_waiver_snapshot
    ON legal_waivers;
CREATE CONSTRAINT TRIGGER ctrg_legal_waiver_snapshot
    AFTER INSERT ON legal_waivers
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_snapshot();

DROP TRIGGER IF EXISTS ctrg_counsel_opinion_snapshot
    ON counsel_opinions;
CREATE CONSTRAINT TRIGGER ctrg_counsel_opinion_snapshot
    AFTER INSERT ON counsel_opinions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_snapshot();

DROP TRIGGER IF EXISTS ctrg_legal_analysis_snapshot
    ON legal_analysis_versions;
CREATE CONSTRAINT TRIGGER ctrg_legal_analysis_snapshot
    AFTER INSERT ON legal_analysis_versions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_governance_snapshot();

CREATE TABLE IF NOT EXISTS project_legal_annotations (
    project_legal_annotation_id    TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    project_legal_matter_id        TEXT NOT NULL,
    legal_resource_id              TEXT NOT NULL
        REFERENCES legal_resources(legal_resource_id) ON DELETE RESTRICT,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT,
    exact_locator                  TEXT NOT NULL,
    annotation_text_sha256         TEXT NOT NULL,
    annotation_type                TEXT NOT NULL,
    status                         TEXT NOT NULL DEFAULT 'active',
    created_by                     TEXT NOT NULL,
    created_at                     TIMESTAMPTZ NOT NULL,
    supersedes_annotation_id       TEXT,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        project_legal_annotation_id,
        tenant_id,
        project_id
    ),
    CONSTRAINT fk_project_legal_annotation_matter
        FOREIGN KEY (
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_matters(
            project_legal_matter_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_annotation_version
        FOREIGN KEY (
            legal_resource_version_id,
            legal_resource_id
        )
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_annotation_provision
        FOREIGN KEY (
            legal_provision_id,
            legal_resource_version_id
        )
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_annotation_supersedes
        FOREIGN KEY (
            supersedes_annotation_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_annotations(
            project_legal_annotation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_legal_annotation_locator
        CHECK (NULLIF(btrim(exact_locator), '') IS NOT NULL),
    CONSTRAINT ck_project_legal_annotation_hash
        CHECK (annotation_text_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_project_legal_annotation_type
        CHECK (annotation_type IN (
            'issue',
            'interpretation',
            'counsel_note',
            'cure_evidence',
            'cross_reference'
        )),
    CONSTRAINT ck_project_legal_annotation_status
        CHECK (status IN ('active', 'resolved', 'superseded')),
    CONSTRAINT ck_project_legal_annotation_actor
        CHECK (NULLIF(btrim(created_by), '') IS NOT NULL),
    CONSTRAINT ck_project_legal_annotation_not_self_superseding
        CHECK (
            supersedes_annotation_id IS NULL
            OR supersedes_annotation_id <> project_legal_annotation_id
        ),
    CONSTRAINT ck_project_legal_annotation_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_project_legal_annotations_matter
    ON project_legal_annotations(
        tenant_id,
        project_id,
        project_legal_matter_id,
        created_at DESC
    );

CREATE TABLE IF NOT EXISTS tenant_legal_term_aliases (
    tenant_legal_term_alias_id     TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    term_id                        TEXT NOT NULL
        REFERENCES glossary_terms(term_id) ON DELETE RESTRICT,
    alias_version                  INTEGER NOT NULL,
    alias_text                     TEXT NOT NULL,
    normalized_alias               TEXT NOT NULL,
    status                         TEXT NOT NULL DEFAULT 'candidate',
    proposed_by                    TEXT NOT NULL,
    proposed_at                    TIMESTAMPTZ NOT NULL,
    reviewed_by                    TEXT,
    reviewed_at                    TIMESTAMPTZ,
    supersedes_alias_id            TEXT,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        tenant_legal_term_alias_id,
        tenant_id
    ),
    UNIQUE (tenant_id, normalized_alias, alias_version),
    CONSTRAINT fk_tenant_legal_term_alias_supersedes
        FOREIGN KEY (supersedes_alias_id, tenant_id)
        REFERENCES tenant_legal_term_aliases(
            tenant_legal_term_alias_id,
            tenant_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_tenant_legal_term_alias_text
        CHECK (
            NULLIF(btrim(alias_text), '') IS NOT NULL
            AND NULLIF(btrim(normalized_alias), '') IS NOT NULL
            AND NULLIF(btrim(proposed_by), '') IS NOT NULL
        ),
    CONSTRAINT ck_tenant_legal_term_alias_version
        CHECK (alias_version >= 1),
    CONSTRAINT ck_tenant_legal_term_alias_status
        CHECK (status IN (
            'candidate',
            'approved',
            'rejected',
            'superseded'
        )),
    CONSTRAINT ck_tenant_legal_term_alias_review
        CHECK (
            status = 'candidate'
            OR (
                NULLIF(btrim(reviewed_by), '') IS NOT NULL
                AND reviewed_at IS NOT NULL
            )
        ),
    CONSTRAINT ck_tenant_legal_term_alias_not_self_superseding
        CHECK (
            supersedes_alias_id IS NULL
            OR supersedes_alias_id <> tenant_legal_term_alias_id
        ),
    CONSTRAINT ck_tenant_legal_term_alias_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_tenant_legal_term_aliases_term
    ON tenant_legal_term_aliases(tenant_id, term_id, status);

CREATE TABLE IF NOT EXISTS tenant_legal_term_alias_review_events (
    tenant_legal_term_alias_review_event_id TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    tenant_legal_term_alias_id     TEXT NOT NULL,
    review_status                  TEXT NOT NULL,
    reviewer_id                    TEXT NOT NULL,
    reviewed_at                    TIMESTAMPTZ NOT NULL,
    reason                         TEXT NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT fk_tenant_legal_term_review_alias
        FOREIGN KEY (tenant_legal_term_alias_id, tenant_id)
        REFERENCES tenant_legal_term_aliases(
            tenant_legal_term_alias_id,
            tenant_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_tenant_legal_term_review_status
        CHECK (review_status IN ('approved', 'rejected', 'needs_revision')),
    CONSTRAINT ck_tenant_legal_term_review_actor
        CHECK (
            NULLIF(btrim(reviewer_id), '') IS NOT NULL
            AND NULLIF(btrim(reason), '') IS NOT NULL
        ),
    CONSTRAINT ck_tenant_legal_term_review_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);

-- ---------------------------------------------------------------------------
-- Legal glossary source and relationship extensions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS legal_term_source_links (
    legal_term_source_link_id      TEXT PRIMARY KEY,
    term_id                        TEXT NOT NULL
        REFERENCES glossary_terms(term_id) ON DELETE RESTRICT,
    legal_resource_id              TEXT NOT NULL
        REFERENCES legal_resources(legal_resource_id) ON DELETE RESTRICT,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT,
    link_type                      TEXT NOT NULL,
    exact_locator                  TEXT NOT NULL,
    evidence_excerpt_sha256        TEXT NOT NULL,
    review_status                  TEXT NOT NULL DEFAULT 'candidate',
    reviewed_by                    TEXT,
    reviewed_at                    TIMESTAMPTZ,
    asserted_at                    TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT fk_legal_term_source_version
        FOREIGN KEY (
            legal_resource_version_id,
            legal_resource_id
        )
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_term_source_provision
        FOREIGN KEY (
            legal_provision_id,
            legal_resource_version_id
        )
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_term_source_link_type
        CHECK (link_type IN (
            'official_definition',
            'usage',
            'interpretation',
            'cross_reference'
        )),
    CONSTRAINT ck_legal_term_source_locator
        CHECK (NULLIF(btrim(exact_locator), '') IS NOT NULL),
    CONSTRAINT ck_legal_term_source_hash
        CHECK (evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_term_source_review_status
        CHECK (review_status IN ('candidate', 'approved', 'rejected')),
    CONSTRAINT ck_legal_term_source_review
        CHECK (
            review_status = 'candidate'
            OR (
                NULLIF(btrim(reviewed_by), '') IS NOT NULL
                AND reviewed_at IS NOT NULL
            )
        ),
    CONSTRAINT ck_legal_term_source_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_term_source_links_term
    ON legal_term_source_links(term_id, review_status, asserted_at DESC);

CREATE TABLE IF NOT EXISTS legal_term_relations (
    legal_term_relation_id         TEXT PRIMARY KEY,
    subject_term_id                TEXT NOT NULL
        REFERENCES glossary_terms(term_id) ON DELETE RESTRICT,
    object_term_id                 TEXT NOT NULL
        REFERENCES glossary_terms(term_id) ON DELETE RESTRICT,
    relation_type                  TEXT NOT NULL,
    assertion_method               TEXT NOT NULL,
    confidence                     DOUBLE PRECISION,
    review_status                  TEXT NOT NULL DEFAULT 'candidate',
    reviewed_by                    TEXT,
    reviewed_at                    TIMESTAMPTZ,
    supersedes_relation_id         TEXT,
    asserted_at                    TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT fk_legal_term_relation_supersedes
        FOREIGN KEY (supersedes_relation_id)
        REFERENCES legal_term_relations(legal_term_relation_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_term_relation_type
        CHECK (relation_type IN (
            'synonym',
            'broader',
            'narrower',
            'related',
            'conflicts_with',
            'supersedes'
        )),
    CONSTRAINT ck_legal_term_relation_method
        CHECK (assertion_method IN (
            'official_structured',
            'deterministic_extract',
            'machine_inferred',
            'human_asserted'
        )),
    CONSTRAINT ck_legal_term_relation_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0.0 AND confidence <= 1.0)
        ),
    CONSTRAINT ck_legal_term_relation_review_status
        CHECK (review_status IN (
            'candidate',
            'approved',
            'rejected',
            'superseded'
        )),
    CONSTRAINT ck_legal_term_relation_review
        CHECK (
            review_status = 'candidate'
            OR (
                NULLIF(btrim(reviewed_by), '') IS NOT NULL
                AND reviewed_at IS NOT NULL
            )
        ),
    CONSTRAINT ck_legal_term_relation_no_self
        CHECK (
            subject_term_id <> object_term_id
            OR relation_type = 'supersedes'
        ),
    CONSTRAINT ck_legal_term_relation_not_self_superseding
        CHECK (
            supersedes_relation_id IS NULL
            OR supersedes_relation_id <> legal_term_relation_id
        ),
    CONSTRAINT ck_legal_term_relation_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_term_relations_subject
    ON legal_term_relations(subject_term_id, relation_type, review_status);

-- ---------------------------------------------------------------------------
-- Tenant-native Legal Pod review projections
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_legal_review_versions (
    review_version_id               TEXT PRIMARY KEY,
    tenant_id                       TEXT NOT NULL,
    project_id                      TEXT NOT NULL,
    review_id                       TEXT NOT NULL,
    review_version                  INTEGER NOT NULL,
    status                          TEXT NOT NULL,
    review_level                    TEXT NOT NULL,
    evidence_status                 TEXT NOT NULL,
    evidence_locator_count          INTEGER NOT NULL,
    verified_evidence_count         INTEGER NOT NULL,
    verified_reference_count        INTEGER NOT NULL,
    verified_citation_count         INTEGER NOT NULL,
    required_claim_count            INTEGER NOT NULL,
    verified_claim_count            INTEGER NOT NULL,
    claim_coverage_complete         BOOLEAN NOT NULL,
    open_conflict_count             INTEGER NOT NULL,
    evidence_stale                  BOOLEAN NOT NULL,
    citation_verification_status    TEXT NOT NULL,
    legal_evidence_snapshot_id      TEXT
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id
        )
        ON DELETE RESTRICT,
    snapshot_version                INTEGER,
    snapshot_sha256                 TEXT,
    snapshot_gate_eligible          BOOLEAN NOT NULL DEFAULT FALSE,
    required_claim_set_id           TEXT
        REFERENCES legal_required_claim_sets(
            legal_required_claim_set_id
        )
        ON DELETE RESTRICT,
    required_claim_set_sha256       TEXT,
    parser_status                   TEXT NOT NULL,
    parser_module_key               TEXT,
    parser_module_version           TEXT,
    parser_input_sha256              TEXT,
    parser_output_sha256             TEXT,
    parser_failure_reason           TEXT,
    analysis_only                   BOOLEAN NOT NULL DEFAULT TRUE,
    report_eligible                 BOOLEAN NOT NULL DEFAULT FALSE,
    human_review_status             TEXT NOT NULL DEFAULT 'pending',
    record_locator_json             JSONB NOT NULL,
    evidence_json                   JSONB NOT NULL,
    parser_json                     JSONB NOT NULL,
    display_json                    JSONB NOT NULL DEFAULT '{}'::JSONB,
    projection_hash_contract        TEXT NOT NULL,
    projection_sha256               TEXT NOT NULL,
    supersedes_review_version_id    TEXT,
    created_by                      TEXT NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL,
    CONSTRAINT fk_project_legal_review_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_project_legal_review_version
        UNIQUE (tenant_id, project_id, review_id, review_version),
    CONSTRAINT uq_project_legal_review_version_scope
        UNIQUE (review_version_id, tenant_id, project_id),
    CONSTRAINT fk_project_legal_review_supersedes
        FOREIGN KEY (supersedes_review_version_id)
        REFERENCES project_legal_review_versions(review_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_legal_review_version_chain
        CHECK (
            (review_version = 1 AND supersedes_review_version_id IS NULL)
            OR (
                review_version > 1
                AND supersedes_review_version_id IS NOT NULL
            )
        ),
    CONSTRAINT ck_project_legal_review_counts
        CHECK (
            evidence_locator_count >= 0
            AND verified_evidence_count >= 0
            AND verified_reference_count >= 0
            AND verified_citation_count >= 0
            AND required_claim_count >= 0
            AND verified_claim_count >= 0
            AND open_conflict_count >= 0
            AND verified_claim_count <= required_claim_count
        ),
    CONSTRAINT ck_project_legal_review_hashes
        CHECK (
            projection_hash_contract = 'pg-jsonb-row-v1'
            AND projection_sha256 ~ '^[0-9a-f]{64}$'
            AND (
                snapshot_sha256 IS NULL
                OR snapshot_sha256 ~ '^[0-9a-f]{64}$'
            )
            AND (
                required_claim_set_sha256 IS NULL
                OR required_claim_set_sha256 ~ '^[0-9a-f]{64}$'
            )
            AND (
                parser_input_sha256 IS NULL
                OR parser_input_sha256 ~ '^[0-9a-f]{64}$'
            )
            AND (
                parser_output_sha256 IS NULL
                OR parser_output_sha256 ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT ck_project_legal_review_parser_status
        CHECK (parser_status IN (
            'not_applicable',
            'not_available',
            'unverified',
            'verified',
            'failed'
        )),
    CONSTRAINT ck_project_legal_review_status
        CHECK (status IN (
            'clear',
            'caution',
            'blocking_issue',
            'failed',
            'not_assessable'
        )),
    CONSTRAINT ck_project_legal_review_evidence_status
        CHECK (evidence_status IN (
            'missing',
            'weak',
            'partial',
            'verified',
            'complete',
            'unverified',
            'failed',
            'unavailable'
        )),
    CONSTRAINT ck_project_legal_review_citation_status
        CHECK (citation_verification_status IN (
            'not_verified',
            'not_requested',
            'unverified',
            'conflicted',
            'partial',
            'verified',
            'failed',
            'stale'
        )),
    CONSTRAINT ck_project_legal_review_human_status
        CHECK (human_review_status IN (
            'pending',
            'approved',
            'counsel_approved',
            'rejected',
            'needs_revision',
            'waived'
        )),
    CONSTRAINT ck_project_legal_review_json
        CHECK (
            jsonb_typeof(record_locator_json) = 'object'
            AND jsonb_typeof(evidence_json) = 'object'
            AND COALESCE(
                jsonb_typeof(evidence_json -> 'evidenceLocators') = 'array',
                FALSE
            )
            AND evidence_locator_count =
                jsonb_array_length(evidence_json -> 'evidenceLocators')
            AND jsonb_typeof(parser_json) = 'object'
            AND jsonb_typeof(display_json) = 'object'
        ),
    CONSTRAINT ck_project_legal_review_report_eligibility
        CHECK (
            NOT report_eligible
            OR (
                NOT analysis_only
                AND human_review_status IN (
                    'approved',
                    'counsel_approved'
                )
            )
        ),
    CONSTRAINT ck_project_legal_review_snapshot_pair
        CHECK (
            legal_evidence_snapshot_id IS NOT NULL
            OR (
                snapshot_version IS NULL
                AND snapshot_sha256 IS NULL
                AND NOT snapshot_gate_eligible
                AND required_claim_set_id IS NULL
                AND required_claim_set_sha256 IS NULL
            )
        ),
    CONSTRAINT ck_project_legal_review_gate_contract
        CHECK (
            NOT snapshot_gate_eligible
            OR (
                legal_evidence_snapshot_id IS NOT NULL
                AND snapshot_version > 0
                AND snapshot_sha256 IS NOT NULL
                AND required_claim_set_id IS NOT NULL
                AND required_claim_set_sha256 IS NOT NULL
                AND citation_verification_status = 'verified'
                AND NOT evidence_stale
                AND open_conflict_count = 0
                AND verified_reference_count > 0
                AND verified_citation_count > 0
                AND required_claim_count > 0
                AND verified_claim_count = required_claim_count
                AND claim_coverage_complete
            )
        ),
    CONSTRAINT ck_project_legal_review_actor
        CHECK (
            NULLIF(btrim(created_by), '') IS NOT NULL
            AND lower(btrim(created_by)) NOT IN (
                'anonymous',
                'unknown',
                'system'
            )
        )
);
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_project_legal_review_single_successor
    ON project_legal_review_versions(supersedes_review_version_id)
    WHERE supersedes_review_version_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_project_legal_review_latest
    ON project_legal_review_versions(
        tenant_id,
        project_id,
        review_id,
        review_version DESC,
        created_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_project_legal_review_snapshot
    ON project_legal_review_versions(
        tenant_id,
        project_id,
        legal_evidence_snapshot_id
    )
    WHERE legal_evidence_snapshot_id IS NOT NULL;

ALTER TABLE legal_review_decisions
    DROP CONSTRAINT IF EXISTS fk_legal_review_decision_projection_scope;
ALTER TABLE legal_review_decisions
    ADD CONSTRAINT fk_legal_review_decision_projection_scope
        FOREIGN KEY (
            legal_review_version_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_review_versions(
            review_version_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS idx_legal_review_decisions_projection
    ON legal_review_decisions(
        tenant_id,
        project_id,
        legal_review_version_id,
        decided_at DESC
    )
    WHERE legal_review_version_id IS NOT NULL;

CREATE OR REPLACE FUNCTION
    legal_assert_review_decision_projection_binding()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_review_decision_projection$
DECLARE
    latest_projection_id TEXT;
    latest_projection_version INTEGER;
    latest_projection_hash_contract TEXT;
    latest_projection_sha256 TEXT;
    recomputed_projection_sha256 TEXT;
    latest_snapshot_id TEXT;
    latest_projection_created_at TIMESTAMPTZ;
    latest_snapshot_created_at TIMESTAMPTZ;
BEGIN
    IF (
        NEW.decision_scope <> 'approval_gate'
        OR NEW.decision_type NOT IN (
            'approve',
            'reject',
            'waive_with_conditions'
        )
    ) THEN
        RETURN NEW;
    END IF;

    IF (
        NULLIF(btrim(NEW.legal_review_id), '') IS NULL
        OR NULLIF(btrim(NEW.legal_review_version_id), '') IS NULL
        OR NEW.legal_review_version IS NULL
        OR NEW.legal_review_projection_sha256 IS NULL
    ) THEN
        RAISE EXCEPTION
            'final legal decision % requires normalized projection binding',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_projection_binding';
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT projection.review_version_id, '
        'projection.review_version, '
        'projection.projection_hash_contract, '
        'projection.projection_sha256, '
        'CASE WHEN projection.projection_hash_contract = '
        '''pg-jsonb-row-v1'' THEN encode('
        'extensions.digest(convert_to(('
        'to_jsonb(projection) - ''projection_sha256'' '
        '- ''projection_hash_contract'')::TEXT, ''UTF8''), '
        '''sha256''), ''hex'') ELSE NULL END, '
        'projection.legal_evidence_snapshot_id, projection.created_at, '
        'snapshot.created_at '
        'FROM %I.project_legal_review_versions AS projection '
        'LEFT JOIN %I.legal_evidence_snapshots AS snapshot '
        'ON snapshot.legal_evidence_snapshot_id = '
        'projection.legal_evidence_snapshot_id '
        'AND snapshot.tenant_id = projection.tenant_id '
        'AND snapshot.project_id = projection.project_id '
        'WHERE projection.tenant_id = $1 '
        'AND projection.project_id = $2 '
        'AND projection.review_id = $3 '
        'ORDER BY projection.review_version DESC, '
        'projection.created_at DESC, projection.review_version_id DESC '
        'LIMIT 1 FOR SHARE',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO
        latest_projection_id,
        latest_projection_version,
        latest_projection_hash_contract,
        latest_projection_sha256,
        recomputed_projection_sha256,
        latest_snapshot_id,
        latest_projection_created_at,
        latest_snapshot_created_at
    USING NEW.tenant_id, NEW.project_id, NEW.legal_review_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'final legal decision % has no normalized legal review projection',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'fk_legal_review_decision_projection_scope';
    END IF;

    IF (
        NEW.legal_review_version_id IS DISTINCT FROM latest_projection_id
        OR NEW.legal_review_version IS DISTINCT FROM
            latest_projection_version
        OR latest_projection_hash_contract IS DISTINCT FROM
            'pg-jsonb-row-v1'
        OR NEW.legal_review_projection_sha256 IS DISTINCT FROM
            latest_projection_sha256
        OR latest_projection_sha256 IS DISTINCT FROM
            recomputed_projection_sha256
    ) THEN
        RAISE EXCEPTION
            'legal review projection changed before decision % was committed',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_projection_current';
    END IF;

    IF NEW.legal_evidence_snapshot_id IS DISTINCT FROM latest_snapshot_id THEN
        RAISE EXCEPTION
            'legal decision % projection and evidence snapshot differ',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_projection_snapshot';
    END IF;

    IF (
        latest_projection_created_at IS NULL
        OR NEW.decided_at < latest_projection_created_at
    ) THEN
        RAISE EXCEPTION
            'legal decision % predates its normalized review projection',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_projection_time';
    END IF;

    IF (
        latest_snapshot_created_at IS NULL
        OR NEW.decided_at < latest_snapshot_created_at
    ) THEN
        RAISE EXCEPTION
            'legal decision % predates its evidence snapshot',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_snapshot_time';
    END IF;

    IF NEW.decided_at NOT BETWEEN
        statement_timestamp() - INTERVAL '5 minutes'
        AND statement_timestamp() + INTERVAL '5 minutes'
    THEN
        RAISE EXCEPTION
            'legal decision % timestamp is outside the database clock window',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_review_decision_statement_time';
    END IF;

    RETURN NEW;
END;
$legal_review_decision_projection$;
REVOKE ALL ON FUNCTION
    legal_assert_review_decision_projection_binding()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_legal_review_decision_projection_binding
    ON legal_review_decisions;
CREATE CONSTRAINT TRIGGER
    ctrg_legal_review_decision_projection_binding
    AFTER INSERT ON legal_review_decisions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_review_decision_projection_binding();

CREATE TABLE IF NOT EXISTS project_permit_review_versions (
    review_version_id               TEXT PRIMARY KEY,
    tenant_id                       TEXT NOT NULL,
    project_id                      TEXT NOT NULL,
    review_id                       TEXT NOT NULL,
    review_version                  INTEGER NOT NULL,
    status                          TEXT NOT NULL,
    review_level                    TEXT NOT NULL,
    evidence_status                 TEXT NOT NULL,
    evidence_locator_count          INTEGER NOT NULL,
    verified_evidence_count         INTEGER NOT NULL,
    verified_reference_count        INTEGER NOT NULL,
    verified_citation_count         INTEGER NOT NULL,
    required_claim_count            INTEGER NOT NULL,
    verified_claim_count            INTEGER NOT NULL,
    claim_coverage_complete         BOOLEAN NOT NULL,
    open_conflict_count             INTEGER NOT NULL,
    evidence_stale                  BOOLEAN NOT NULL,
    citation_verification_status    TEXT NOT NULL,
    legal_evidence_snapshot_id      TEXT
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id
        )
        ON DELETE RESTRICT,
    snapshot_version                INTEGER,
    snapshot_sha256                 TEXT,
    snapshot_gate_eligible          BOOLEAN NOT NULL DEFAULT FALSE,
    required_claim_set_id           TEXT
        REFERENCES legal_required_claim_sets(
            legal_required_claim_set_id
        )
        ON DELETE RESTRICT,
    required_claim_set_sha256       TEXT,
    parser_status                   TEXT NOT NULL,
    parser_module_key               TEXT,
    parser_module_version           TEXT,
    parser_input_sha256              TEXT,
    parser_output_sha256             TEXT,
    parser_failure_reason           TEXT,
    analysis_only                   BOOLEAN NOT NULL DEFAULT TRUE,
    report_eligible                 BOOLEAN NOT NULL DEFAULT FALSE,
    human_review_status             TEXT NOT NULL DEFAULT 'pending',
    record_locator_json             JSONB NOT NULL,
    evidence_json                   JSONB NOT NULL,
    parser_json                     JSONB NOT NULL,
    display_json                    JSONB NOT NULL DEFAULT '{}'::JSONB,
    projection_hash_contract        TEXT NOT NULL,
    projection_sha256               TEXT NOT NULL,
    supersedes_review_version_id    TEXT,
    created_by                      TEXT NOT NULL,
    created_at                      TIMESTAMPTZ NOT NULL,
    CONSTRAINT fk_project_permit_review_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id)
        ON DELETE RESTRICT,
    CONSTRAINT uq_project_permit_review_version
        UNIQUE (tenant_id, project_id, review_id, review_version),
    CONSTRAINT fk_project_permit_review_supersedes
        FOREIGN KEY (supersedes_review_version_id)
        REFERENCES project_permit_review_versions(review_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_permit_review_version_chain
        CHECK (
            (review_version = 1 AND supersedes_review_version_id IS NULL)
            OR (
                review_version > 1
                AND supersedes_review_version_id IS NOT NULL
            )
        ),
    CONSTRAINT ck_project_permit_review_counts
        CHECK (
            evidence_locator_count >= 0
            AND verified_evidence_count >= 0
            AND verified_reference_count >= 0
            AND verified_citation_count >= 0
            AND required_claim_count >= 0
            AND verified_claim_count >= 0
            AND open_conflict_count >= 0
            AND verified_claim_count <= required_claim_count
        ),
    CONSTRAINT ck_project_permit_review_hashes
        CHECK (
            projection_hash_contract = 'pg-jsonb-row-v1'
            AND projection_sha256 ~ '^[0-9a-f]{64}$'
            AND (
                snapshot_sha256 IS NULL
                OR snapshot_sha256 ~ '^[0-9a-f]{64}$'
            )
            AND (
                required_claim_set_sha256 IS NULL
                OR required_claim_set_sha256 ~ '^[0-9a-f]{64}$'
            )
            AND (
                parser_input_sha256 IS NULL
                OR parser_input_sha256 ~ '^[0-9a-f]{64}$'
            )
            AND (
                parser_output_sha256 IS NULL
                OR parser_output_sha256 ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT ck_project_permit_review_parser_status
        CHECK (parser_status IN (
            'not_applicable',
            'not_available',
            'unverified',
            'verified',
            'failed'
        )),
    CONSTRAINT ck_project_permit_review_status
        CHECK (status IN (
            'clear',
            'caution',
            'blocking',
            'pending_evidence',
            'failed'
        )),
    CONSTRAINT ck_project_permit_review_evidence_status
        CHECK (evidence_status IN (
            'missing',
            'weak',
            'partial',
            'verified',
            'complete',
            'unverified',
            'failed',
            'unavailable'
        )),
    CONSTRAINT ck_project_permit_review_citation_status
        CHECK (citation_verification_status IN (
            'not_verified',
            'not_requested',
            'unverified',
            'conflicted',
            'partial',
            'verified',
            'failed',
            'stale'
        )),
    CONSTRAINT ck_project_permit_review_human_status
        CHECK (human_review_status IN (
            'pending',
            'approved',
            'counsel_approved',
            'rejected',
            'needs_revision',
            'waived'
        )),
    CONSTRAINT ck_project_permit_review_json
        CHECK (
            jsonb_typeof(record_locator_json) = 'object'
            AND jsonb_typeof(evidence_json) = 'object'
            AND COALESCE(
                jsonb_typeof(evidence_json -> 'evidenceLocators') = 'array',
                FALSE
            )
            AND evidence_locator_count =
                jsonb_array_length(evidence_json -> 'evidenceLocators')
            AND jsonb_typeof(parser_json) = 'object'
            AND jsonb_typeof(display_json) = 'object'
        ),
    CONSTRAINT ck_project_permit_review_report_eligibility
        CHECK (
            NOT report_eligible
            OR (
                NOT analysis_only
                AND human_review_status IN (
                    'approved',
                    'counsel_approved'
                )
            )
        ),
    CONSTRAINT ck_project_permit_review_snapshot_pair
        CHECK (
            legal_evidence_snapshot_id IS NOT NULL
            OR (
                snapshot_version IS NULL
                AND snapshot_sha256 IS NULL
                AND NOT snapshot_gate_eligible
                AND required_claim_set_id IS NULL
                AND required_claim_set_sha256 IS NULL
            )
        ),
    CONSTRAINT ck_project_permit_review_gate_contract
        CHECK (
            NOT snapshot_gate_eligible
            OR (
                legal_evidence_snapshot_id IS NOT NULL
                AND snapshot_version > 0
                AND snapshot_sha256 IS NOT NULL
                AND required_claim_set_id IS NOT NULL
                AND required_claim_set_sha256 IS NOT NULL
                AND citation_verification_status = 'verified'
                AND NOT evidence_stale
                AND open_conflict_count = 0
                AND verified_reference_count > 0
                AND verified_citation_count > 0
                AND required_claim_count > 0
                AND verified_claim_count = required_claim_count
                AND claim_coverage_complete
            )
        ),
    CONSTRAINT ck_project_permit_review_actor
        CHECK (
            NULLIF(btrim(created_by), '') IS NOT NULL
            AND lower(btrim(created_by)) NOT IN (
                'anonymous',
                'unknown',
                'system'
            )
        )
);
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_project_permit_review_single_successor
    ON project_permit_review_versions(supersedes_review_version_id)
    WHERE supersedes_review_version_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_project_permit_review_latest
    ON project_permit_review_versions(
        tenant_id,
        project_id,
        review_id,
        review_version DESC,
        created_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_project_permit_review_snapshot
    ON project_permit_review_versions(
        tenant_id,
        project_id,
        legal_evidence_snapshot_id
    )
    WHERE legal_evidence_snapshot_id IS NOT NULL;

CREATE OR REPLACE FUNCTION legal_prepare_review_projection_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_prepare_review_projection$
DECLARE
    predecessor_id TEXT;
    predecessor_version INTEGER;
    projection_row_json JSONB;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            TG_TABLE_NAME || E'\x1f'
            || NEW.tenant_id || E'\x1f'
            || NEW.project_id || E'\x1f'
            || NEW.review_id,
            0
        )
    );

    EXECUTE pg_catalog.format(
        'SELECT predecessor.review_version_id, '
        'predecessor.review_version '
        'FROM %I.%I AS predecessor '
        'WHERE predecessor.tenant_id = $1 '
        'AND predecessor.project_id = $2 '
        'AND predecessor.review_id = $3 '
        'AND NOT EXISTS ('
        'SELECT 1 FROM %I.%I AS successor '
        'WHERE successor.supersedes_review_version_id = '
        'predecessor.review_version_id'
        ') '
        'ORDER BY predecessor.review_version DESC '
        'LIMIT 1 FOR UPDATE OF predecessor',
        TG_TABLE_SCHEMA,
        TG_TABLE_NAME,
        TG_TABLE_SCHEMA,
        TG_TABLE_NAME
    )
    INTO predecessor_id, predecessor_version
    USING NEW.tenant_id, NEW.project_id, NEW.review_id;

    IF (
        NEW.review_version IS DISTINCT FROM
            COALESCE(predecessor_version, 0) + 1
        OR NEW.supersedes_review_version_id IS DISTINCT FROM predecessor_id
    ) THEN
        RAISE EXCEPTION
            'review projection predecessor changed; rebuild before appending'
            USING ERRCODE = '40001',
                  CONSTRAINT =
                      'ck_review_projection_expected_predecessor';
    END IF;
    NEW.created_at := statement_timestamp();
    NEW.projection_hash_contract := 'pg-jsonb-row-v1';
    projection_row_json := to_jsonb(NEW)
        - 'projection_sha256'
        - 'projection_hash_contract';
    NEW.projection_sha256 := encode(
        extensions.digest(
            convert_to(projection_row_json::TEXT, 'UTF8'),
            'sha256'
        ),
        'hex'
    );
    RETURN NEW;
END;
$legal_prepare_review_projection$;
REVOKE ALL ON FUNCTION legal_prepare_review_projection_insert()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS trg_project_legal_review_prepare_insert
    ON project_legal_review_versions;
CREATE TRIGGER trg_project_legal_review_prepare_insert
    BEFORE INSERT ON project_legal_review_versions
    FOR EACH ROW
    EXECUTE FUNCTION legal_prepare_review_projection_insert();

DROP TRIGGER IF EXISTS trg_project_permit_review_prepare_insert
    ON project_permit_review_versions;
CREATE TRIGGER trg_project_permit_review_prepare_insert
    BEFORE INSERT ON project_permit_review_versions
    FOR EACH ROW
    EXECUTE FUNCTION legal_prepare_review_projection_insert();

CREATE OR REPLACE FUNCTION legal_assert_review_projection_snapshot()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_review_projection_snapshot$
DECLARE
    referenced_snapshot RECORD;
    predecessor RECORD;
BEGIN
    IF NEW.review_version > 1 THEN
        EXECUTE pg_catalog.format(
            'SELECT tenant_id, project_id, review_id, review_version '
            'FROM %I.%I WHERE review_version_id = $1',
            TG_TABLE_SCHEMA,
            TG_TABLE_NAME
        )
        INTO predecessor
        USING NEW.supersedes_review_version_id;
        IF NOT FOUND
           OR predecessor.tenant_id IS DISTINCT FROM NEW.tenant_id
           OR predecessor.project_id IS DISTINCT FROM NEW.project_id
           OR predecessor.review_id IS DISTINCT FROM NEW.review_id
           OR predecessor.review_version <> NEW.review_version - 1
        THEN
            RAISE EXCEPTION
                'review projection predecessor is outside the same tenant/project/review chain';
        END IF;
    END IF;

    IF NEW.legal_evidence_snapshot_id IS NULL THEN
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        'SELECT snapshot.snapshot_version, snapshot.snapshot_sha256, '
        'snapshot.gate_eligible, '
        'snapshot.legal_required_claim_set_id, '
        'snapshot.required_claim_set_sha256, '
        'snapshot.legal_review_id, '
        'snapshot.citation_verification_status, '
        'snapshot.has_open_conflicts, snapshot.is_stale, '
        'EXISTS ('
        '  SELECT 1 FROM %I.legal_evidence_snapshots AS successor '
        '  WHERE successor.tenant_id = snapshot.tenant_id '
        '    AND successor.project_id = snapshot.project_id '
        '    AND ('
        '      successor.supersedes_snapshot_id = '
        '        snapshot.legal_evidence_snapshot_id '
        '      OR successor.snapshot_version > snapshot.snapshot_version'
        '    )'
        ') AS is_superseded '
        'FROM %I.legal_evidence_snapshots AS snapshot '
        'WHERE snapshot.legal_evidence_snapshot_id = $1 '
        '  AND snapshot.tenant_id = $2 '
        '  AND snapshot.project_id = $3 '
        'FOR SHARE OF snapshot',
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO referenced_snapshot
    USING
        NEW.legal_evidence_snapshot_id,
        NEW.tenant_id,
        NEW.project_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'review projection references a snapshot outside its tenant/project scope';
    END IF;
    IF TG_TABLE_NAME = 'project_legal_review_versions'
       AND referenced_snapshot.legal_review_id IS DISTINCT FROM NEW.review_id
    THEN
        RAISE EXCEPTION
            'legal review projection review_id does not match the evidence snapshot';
    END IF;
    IF referenced_snapshot.snapshot_version IS DISTINCT FROM NEW.snapshot_version
       OR referenced_snapshot.snapshot_sha256 IS DISTINCT FROM
              NEW.snapshot_sha256
       OR referenced_snapshot.gate_eligible IS DISTINCT FROM
              NEW.snapshot_gate_eligible
       OR referenced_snapshot.legal_required_claim_set_id IS DISTINCT FROM
              NEW.required_claim_set_id
       OR referenced_snapshot.required_claim_set_sha256 IS DISTINCT FROM
              NEW.required_claim_set_sha256
    THEN
        RAISE EXCEPTION
            'review projection snapshot identity, policy, hash, or gate state changed';
    END IF;
    IF NEW.snapshot_gate_eligible AND (
        referenced_snapshot.citation_verification_status <> 'verified'
        OR referenced_snapshot.has_open_conflicts
        OR referenced_snapshot.is_stale
        OR referenced_snapshot.is_superseded
    ) THEN
        RAISE EXCEPTION
            'review projection references an ineligible or superseded snapshot';
    END IF;
    RETURN NEW;
END;
$legal_review_projection_snapshot$;
REVOKE ALL ON FUNCTION legal_assert_review_projection_snapshot()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS ctrg_project_legal_review_snapshot
    ON project_legal_review_versions;
CREATE CONSTRAINT TRIGGER ctrg_project_legal_review_snapshot
    AFTER INSERT ON project_legal_review_versions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_review_projection_snapshot();
DROP TRIGGER IF EXISTS ctrg_project_permit_review_snapshot
    ON project_permit_review_versions;
CREATE CONSTRAINT TRIGGER ctrg_project_permit_review_snapshot
    AFTER INSERT ON project_permit_review_versions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_review_projection_snapshot();

-- Provider operational streams intentionally exclude query text, response
-- bodies, URLs, and credentials.  They are immutable diagnostic metadata; any
-- replayable raw object is retained behind a separately governed object-store
-- reference and review workflow.
CREATE TABLE IF NOT EXISTS legal_provider_quarantine (
    legal_provider_quarantine_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    target TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_legal_provider_quarantine_payload_sha256
        CHECK (payload_sha256 ~ '^sha256:[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS idx_legal_provider_quarantine_provider_time
    ON legal_provider_quarantine(provider_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS legal_provider_dead_letters (
    legal_provider_dead_letter_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    target TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (legal_provider_dead_letter_id, provider_id),
    CONSTRAINT ck_legal_provider_dead_letter_attempts
        CHECK (attempts >= 1)
);
CREATE INDEX IF NOT EXISTS idx_legal_provider_dead_letters_provider_time
    ON legal_provider_dead_letters(provider_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS legal_provider_metrics (
    legal_provider_metric_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    outcome TEXT NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    rate_limited BOOLEAN NOT NULL DEFAULT FALSE,
    local_hit BOOLEAN,
    freshness TEXT,
    tags_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT ck_legal_provider_metric_latency
        CHECK (latency_ms >= 0),
    CONSTRAINT ck_legal_provider_metric_attempts
        CHECK (attempts >= 0),
    CONSTRAINT ck_legal_provider_metric_tags
        CHECK (jsonb_typeof(tags_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_provider_metrics_provider_time
    ON legal_provider_metrics(provider_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_legal_provider_metrics_outcome_time
    ON legal_provider_metrics(operation, outcome, created_at DESC);

-- The provider/object worker must register an exact versioned object-store
-- receipt before this dead-letter becomes replayable.  This catalog is
-- tenant-native, append-only, and deliberately has no public HTTP write path.
-- The row attests the object-store response; enforcement of Object Lock and
-- IAM remains an external deployment control.
CREATE TABLE IF NOT EXISTS legal_provider_replay_objects (
    legal_provider_replay_object_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL,
    legal_provider_dead_letter_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    object_locator TEXT NOT NULL,
    object_locator_sha256 TEXT NOT NULL,
    object_version_id TEXT NOT NULL,
    object_sha256 TEXT NOT NULL,
    object_lock_mode TEXT NOT NULL,
    retention_until TIMESTAMPTZ NOT NULL,
    object_store_receipt_sha256 TEXT NOT NULL,
    registered_by TEXT NOT NULL,
    registered_db_principal TEXT NOT NULL DEFAULT session_user,
    registered_at TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL,
    UNIQUE (
        legal_provider_replay_object_id,
        tenant_id,
        project_id,
        legal_provider_dead_letter_id,
        provider_id
    ),
    UNIQUE (
        tenant_id,
        legal_provider_dead_letter_id,
        idempotency_key
    ),
    UNIQUE (
        provider_id,
        object_locator_sha256,
        object_version_id
    ),
    CONSTRAINT fk_legal_provider_replay_object_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_provider_replay_object_dead_letter
        FOREIGN KEY (legal_provider_dead_letter_id, provider_id)
        REFERENCES legal_provider_dead_letters(
            legal_provider_dead_letter_id,
            provider_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_provider_replay_object_locator_sha256
        CHECK (
            object_locator_sha256 =
                'sha256:' || encode(
                    extensions.digest(
                        convert_to(object_locator, 'UTF8'),
                        'sha256'
                    ),
                    'hex'
                )
        ),
    CONSTRAINT ck_legal_provider_replay_object_locator
        CHECK (
            object_locator ~*
                '^(s3|minio|object|quarantine)://[^/?#]+/[^?#]+$'
            AND position('@' IN object_locator) = 0
            AND lower(
                substring(object_locator FROM '[^/]+$')
            ) IN (
                lower(legal_provider_dead_letter_id),
                lower(legal_provider_dead_letter_id) || '.json'
            )
        ),
    CONSTRAINT ck_legal_provider_replay_object_version
        CHECK (
            NULLIF(btrim(object_version_id), '') IS NOT NULL
            AND char_length(object_version_id) <= 512
            AND object_version_id !~ '[[:cntrl:][:space:]]'
        ),
    CONSTRAINT ck_legal_provider_replay_object_sha256
        CHECK (object_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_provider_replay_object_lock_mode
        CHECK (object_lock_mode IN ('governance', 'compliance')),
    CONSTRAINT ck_legal_provider_replay_object_retention
        CHECK (retention_until > registered_at),
    CONSTRAINT ck_legal_provider_replay_object_receipt_sha256
        CHECK (
            object_store_receipt_sha256 ~
                '^sha256:[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_provider_replay_object_actor
        CHECK (
            NULLIF(btrim(registered_by), '') IS NOT NULL
            AND NULLIF(btrim(registered_db_principal), '') IS NOT NULL
            AND lower(btrim(registered_by))
                NOT IN ('anonymous', 'system', 'unknown')
        ),
    CONSTRAINT ck_legal_provider_replay_object_idempotency
        CHECK (
            idempotency_key ~
                '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'
        )
);
CREATE INDEX IF NOT EXISTS idx_legal_provider_replay_objects_scope
    ON legal_provider_replay_objects(
        tenant_id,
        project_id,
        legal_provider_dead_letter_id,
        retention_until DESC
    );

-- Quarantine, dead-letter, and metric rows above are deliberately global,
-- privacy-safe provider operations metadata.  Replay authority is different:
-- it is always bound to one tenant/project, one cataloged object receipt, and
-- a separately authenticated maker-checker approval event.
CREATE TABLE IF NOT EXISTS legal_provider_replay_approval_events (
    legal_provider_replay_approval_event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL,
    legal_provider_dead_letter_id TEXT NOT NULL
        REFERENCES legal_provider_dead_letters(
            legal_provider_dead_letter_id
        )
        ON DELETE RESTRICT,
    legal_provider_replay_object_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    approved_object_locator TEXT NOT NULL,
    approved_object_locator_sha256 TEXT NOT NULL,
    approved_object_version_id TEXT NOT NULL,
    approved_object_sha256 TEXT NOT NULL,
    approved_object_retention_until TIMESTAMPTZ NOT NULL,
    object_store_receipt_sha256 TEXT NOT NULL,
    approved_requester_id TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approver_role TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL,
    supersedes_approval_event_id TEXT,
    UNIQUE (
        legal_provider_replay_approval_event_id,
        tenant_id,
        project_id,
        legal_provider_dead_letter_id
    ),
    UNIQUE (
        tenant_id,
        legal_provider_dead_letter_id,
        idempotency_key
    ),
    UNIQUE (supersedes_approval_event_id),
    CONSTRAINT fk_legal_provider_replay_approval_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_provider_replay_approval_object
        FOREIGN KEY (
            legal_provider_replay_object_id,
            tenant_id,
            project_id,
            legal_provider_dead_letter_id,
            provider_id
        )
        REFERENCES legal_provider_replay_objects(
            legal_provider_replay_object_id,
            tenant_id,
            project_id,
            legal_provider_dead_letter_id,
            provider_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_provider_replay_approval_supersedes
        FOREIGN KEY (
            supersedes_approval_event_id,
            tenant_id,
            project_id,
            legal_provider_dead_letter_id
        )
        REFERENCES legal_provider_replay_approval_events(
            legal_provider_replay_approval_event_id,
            tenant_id,
            project_id,
            legal_provider_dead_letter_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_provider_replay_approval_object_sha256
        CHECK (approved_object_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_provider_replay_approval_object_version
        CHECK (
            NULLIF(btrim(approved_object_version_id), '') IS NOT NULL
        ),
    CONSTRAINT ck_legal_provider_replay_approval_receipt_sha256
        CHECK (
            object_store_receipt_sha256 ~
                '^sha256:[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_provider_replay_approval_locator_sha256
        CHECK (
            approved_object_locator_sha256 =
                'sha256:' || encode(
                    extensions.digest(
                        convert_to(approved_object_locator, 'UTF8'),
                        'sha256'
                    ),
                    'hex'
                )
        ),
    CONSTRAINT ck_legal_provider_replay_approval_object_locator
        CHECK (
            approved_object_locator ~*
                '^(s3|minio|object|quarantine)://[^/?#]+/[^?#]+$'
            AND position('@' IN approved_object_locator) = 0
            AND lower(
                substring(approved_object_locator FROM '[^/]+$')
            ) IN (
                lower(legal_provider_dead_letter_id),
                lower(legal_provider_dead_letter_id) || '.json'
            )
        ),
    CONSTRAINT ck_legal_provider_replay_approval_actor
        CHECK (
            NULLIF(btrim(approved_requester_id), '') IS NOT NULL
            AND NULLIF(btrim(approved_by), '') IS NOT NULL
            AND lower(btrim(approved_requester_id))
                <> lower(btrim(approved_by))
        ),
    CONSTRAINT ck_legal_provider_replay_approval_role
        CHECK (approver_role = 'compliance_officer'),
    CONSTRAINT ck_legal_provider_replay_approval_period
        CHECK (
            expires_at > approved_at
            AND expires_at <= approved_at + INTERVAL '24 hours'
            AND approved_object_retention_until >= expires_at
        ),
    CONSTRAINT ck_legal_provider_replay_approval_idempotency
        CHECK (
            idempotency_key ~
                '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'
        ),
    CONSTRAINT ck_legal_provider_replay_approval_not_self_superseding
        CHECK (
            supersedes_approval_event_id IS NULL
            OR supersedes_approval_event_id
                <> legal_provider_replay_approval_event_id
        )
);
CREATE INDEX IF NOT EXISTS idx_legal_provider_replay_approvals_scope
    ON legal_provider_replay_approval_events(
        tenant_id,
        project_id,
        legal_provider_dead_letter_id,
        approved_requester_id,
        approved_at DESC
    );

CREATE TABLE IF NOT EXISTS legal_provider_replay_requests (
    legal_provider_replay_request_id TEXT PRIMARY KEY,
    legal_provider_replay_approval_event_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL,
    legal_provider_dead_letter_id TEXT NOT NULL
        REFERENCES legal_provider_dead_letters(
            legal_provider_dead_letter_id
        )
        ON DELETE RESTRICT,
    legal_provider_replay_object_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    approved_object_locator TEXT NOT NULL,
    approved_object_version_id TEXT NOT NULL,
    approved_object_sha256 TEXT NOT NULL,
    approved_object_retention_until TIMESTAMPTZ NOT NULL,
    object_store_receipt_sha256 TEXT NOT NULL,
    object_reference_approved_by TEXT NOT NULL,
    object_reference_approved_at TIMESTAMPTZ NOT NULL,
    idempotency_key TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    UNIQUE (legal_provider_replay_approval_event_id),
    UNIQUE (
        tenant_id,
        legal_provider_dead_letter_id,
        idempotency_key
    ),
    CONSTRAINT fk_legal_provider_replay_request_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_provider_replay_request_object
        FOREIGN KEY (
            legal_provider_replay_object_id,
            tenant_id,
            project_id,
            legal_provider_dead_letter_id,
            provider_id
        )
        REFERENCES legal_provider_replay_objects(
            legal_provider_replay_object_id,
            tenant_id,
            project_id,
            legal_provider_dead_letter_id,
            provider_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_provider_replay_request_approval
        FOREIGN KEY (
            legal_provider_replay_approval_event_id,
            tenant_id,
            project_id,
            legal_provider_dead_letter_id
        )
        REFERENCES legal_provider_replay_approval_events(
            legal_provider_replay_approval_event_id,
            tenant_id,
            project_id,
            legal_provider_dead_letter_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_provider_replay_object_sha256
        CHECK (approved_object_sha256 ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_provider_replay_request_object_version
        CHECK (
            NULLIF(btrim(approved_object_version_id), '') IS NOT NULL
        ),
    CONSTRAINT ck_legal_provider_replay_request_receipt_sha256
        CHECK (
            object_store_receipt_sha256 ~
                '^sha256:[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_provider_replay_object_locator
        CHECK (
            approved_object_locator ~*
                '^(s3|minio|object|quarantine)://[^/?#]+/[^?#]+$'
            AND position('@' IN approved_object_locator) = 0
            AND lower(
                substring(approved_object_locator FROM '[^/]+$')
            ) IN (
                lower(legal_provider_dead_letter_id),
                lower(legal_provider_dead_letter_id) || '.json'
            )
        ),
    CONSTRAINT ck_legal_provider_replay_actor
        CHECK (
            NULLIF(btrim(object_reference_approved_by), '') IS NOT NULL
            AND NULLIF(btrim(requested_by), '') IS NOT NULL
            AND lower(btrim(object_reference_approved_by))
                <> lower(btrim(requested_by))
        ),
    CONSTRAINT ck_legal_provider_replay_requester_role
        CHECK (requester_role IN ('admin', 'compliance_officer')),
    CONSTRAINT ck_legal_provider_replay_request_time
        CHECK (
            requested_at >= object_reference_approved_at
            AND approved_object_retention_until > requested_at
        ),
    CONSTRAINT ck_legal_provider_replay_idempotency
        CHECK (
            idempotency_key ~
                '^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$'
        )
);
CREATE INDEX IF NOT EXISTS idx_legal_provider_replay_requests_dlq
    ON legal_provider_replay_requests(
        legal_provider_dead_letter_id,
        requested_at DESC
    );

CREATE TABLE IF NOT EXISTS legal_provider_replay_events (
    legal_provider_replay_event_id TEXT PRIMARY KEY,
    legal_provider_replay_request_id TEXT NOT NULL
        REFERENCES legal_provider_replay_requests(
            legal_provider_replay_request_id
        )
        ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    outcome_code TEXT,
    executor_reference TEXT,
    recorded_by TEXT NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    UNIQUE (legal_provider_replay_request_id, sequence_no),
    CONSTRAINT ck_legal_provider_replay_event_sequence
        CHECK (sequence_no >= 1),
    CONSTRAINT ck_legal_provider_replay_event_type
        CHECK (
            event_type IN (
                'requested',
                'running',
                'completed',
                'failed'
            )
        ),
    CONSTRAINT ck_legal_provider_replay_event_terminal
        CHECK (
            (
                event_type IN ('requested', 'running')
                AND outcome_code IS NULL
            )
            OR (
                event_type IN ('completed', 'failed')
                AND NULLIF(btrim(outcome_code), '') IS NOT NULL
            )
        ),
    CONSTRAINT ck_legal_provider_replay_event_actor
        CHECK (NULLIF(btrim(recorded_by), '') IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_legal_provider_replay_events_request
    ON legal_provider_replay_events(
        legal_provider_replay_request_id,
        sequence_no DESC
    );

CREATE OR REPLACE FUNCTION legal_assert_provider_replay_object_receipt()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_provider_replay_object$
BEGIN
    IF NEW.registered_at < statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.registered_at > statement_timestamp() + INTERVAL '5 minutes'
       OR NEW.retention_until <= statement_timestamp()
    THEN
        RAISE EXCEPTION
            'replay object receipt timestamp or retention is invalid'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_object_server_time';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'dead-letter:' || NEW.legal_provider_dead_letter_id,
            0
        )
    );
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            concat_ws(
                ':',
                'replay-object',
                NEW.provider_id,
                NEW.object_locator_sha256,
                NEW.object_version_id
            ),
            0
        )
    );
    RETURN NEW;
END;
$legal_provider_replay_object$;
REVOKE ALL ON FUNCTION legal_assert_provider_replay_object_receipt()
    FROM PUBLIC;

CREATE TRIGGER trg_legal_provider_replay_object_receipt
    BEFORE INSERT ON legal_provider_replay_objects
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_provider_replay_object_receipt();

CREATE OR REPLACE FUNCTION legal_assert_provider_replay_approval_current()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_provider_replay_approval$
DECLARE
    previous_approval RECORD;
    previous_count INTEGER := 0;
    has_current BOOLEAN := FALSE;
    has_successor BOOLEAN := FALSE;
    has_consumption BOOLEAN := FALSE;
    catalog RECORD;
    catalog_count INTEGER := 0;
BEGIN
    IF NEW.approved_at < statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.approved_at > statement_timestamp() + INTERVAL '5 minutes'
       OR NEW.expires_at <= statement_timestamp()
    THEN
        RAISE EXCEPTION
            'provider replay approval timestamp is outside the server window'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_approval_server_time';
    END IF;

    -- A superseding approval and a replay request both compete to consume the
    -- previous approval.  Lock that approval first, then the dead-letter, in
    -- the same order as the request path.
    IF NEW.supersedes_approval_event_id IS NOT NULL THEN
        PERFORM pg_advisory_xact_lock(
            hashtextextended(
                'legal-provider-replay-approval:'
                    || NEW.supersedes_approval_event_id,
                0
            )
        );
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'dead-letter:' || NEW.legal_provider_dead_letter_id,
            0
        )
    );

    EXECUTE format(
        $sql$
        SELECT
            replay_object.object_locator,
            replay_object.object_locator_sha256,
            replay_object.object_version_id,
            replay_object.object_sha256,
            replay_object.retention_until,
            replay_object.object_store_receipt_sha256,
            replay_object.registered_at
        FROM %I.legal_provider_replay_objects AS replay_object
        WHERE replay_object.legal_provider_replay_object_id = $1
          AND replay_object.tenant_id = $2
          AND replay_object.project_id = $3
          AND replay_object.legal_provider_dead_letter_id = $4
          AND replay_object.provider_id = $5
        $sql$,
        TG_TABLE_SCHEMA
    )
    INTO catalog
    USING
        NEW.legal_provider_replay_object_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.legal_provider_dead_letter_id,
        NEW.provider_id;
    GET DIAGNOSTICS catalog_count = ROW_COUNT;

    IF catalog_count <> 1
       OR catalog.object_locator <> NEW.approved_object_locator
       OR catalog.object_locator_sha256
            <> NEW.approved_object_locator_sha256
       OR catalog.object_version_id <> NEW.approved_object_version_id
       OR catalog.object_sha256 <> NEW.approved_object_sha256
       OR catalog.retention_until
            <> NEW.approved_object_retention_until
       OR catalog.object_store_receipt_sha256
            <> NEW.object_store_receipt_sha256
       OR catalog.registered_at > NEW.approved_at
       OR catalog.retention_until < NEW.expires_at
       OR catalog.retention_until <= statement_timestamp()
    THEN
        RAISE EXCEPTION
            'replay approval requires an exact current catalog object receipt'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_approval_object_binding';
    END IF;

    -- Serialize the application and direct-SQL approval paths on the same
    -- tenant/project/dead-letter/requester scope.  The repository takes the
    -- same aggregate lock before it performs its friendlier preflight checks.
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            concat_ws(
                ':',
                'legal-provider-replay-approval',
                NEW.tenant_id,
                NEW.project_id,
                NEW.legal_provider_dead_letter_id,
                lower(btrim(NEW.approved_requester_id))
            ),
            0
        )
    );

    IF NEW.supersedes_approval_event_id IS NULL THEN
        EXECUTE format(
            $sql$
            SELECT EXISTS (
                SELECT 1
                FROM %1$I.legal_provider_replay_approval_events
                    AS current_approval
                WHERE current_approval.tenant_id = $1
                  AND current_approval.project_id = $2
                  AND current_approval.legal_provider_dead_letter_id = $3
                  AND lower(btrim(
                      current_approval.approved_requester_id
                  )) = lower(btrim($4))
                  AND current_approval.expires_at > $5
                  AND NOT EXISTS (
                      SELECT 1
                      FROM %1$I.legal_provider_replay_approval_events
                          AS successor
                      WHERE successor.supersedes_approval_event_id =
                          current_approval
                              .legal_provider_replay_approval_event_id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM %1$I.legal_provider_replay_requests AS consumed
                      WHERE consumed
                          .legal_provider_replay_approval_event_id =
                          current_approval
                              .legal_provider_replay_approval_event_id
                  )
            )
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO has_current
        USING
            NEW.tenant_id,
            NEW.project_id,
            NEW.legal_provider_dead_letter_id,
            NEW.approved_requester_id,
            NEW.approved_at;

        IF has_current THEN
            RAISE EXCEPTION
                'current unconsumed replay approval requires explicit supersession'
                USING ERRCODE = '23514',
                      CONSTRAINT =
                          'ck_legal_provider_replay_approval_current';
        END IF;
        RETURN NEW;
    END IF;

    EXECUTE format(
        $sql$
        SELECT previous.approved_requester_id, previous.expires_at
        FROM %I.legal_provider_replay_approval_events AS previous
        WHERE previous.legal_provider_replay_approval_event_id = $1
          AND previous.tenant_id = $2
          AND previous.project_id = $3
          AND previous.legal_provider_dead_letter_id = $4
        $sql$,
        TG_TABLE_SCHEMA
    )
    INTO previous_approval
    USING
        NEW.supersedes_approval_event_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.legal_provider_dead_letter_id;
    GET DIAGNOSTICS previous_count = ROW_COUNT;

    EXECUTE format(
        $sql$
        SELECT
            EXISTS (
                SELECT 1
                FROM %1$I.legal_provider_replay_approval_events AS successor
                WHERE successor.supersedes_approval_event_id = $1
            ),
            EXISTS (
                SELECT 1
                FROM %1$I.legal_provider_replay_requests AS consumed
                WHERE consumed.legal_provider_replay_approval_event_id = $1
            )
        $sql$,
        TG_TABLE_SCHEMA
    )
    INTO has_successor, has_consumption
    USING NEW.supersedes_approval_event_id;

    IF previous_count <> 1
       OR lower(btrim(previous_approval.approved_requester_id))
            <> lower(btrim(NEW.approved_requester_id))
       OR previous_approval.expires_at <= NEW.approved_at
       OR has_successor
       OR has_consumption
    THEN
        RAISE EXCEPTION
            'superseded replay approval must be current, unconsumed, unexpired, and same-scope'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_approval_supersession';
    END IF;

    RETURN NEW;
END;
$legal_provider_replay_approval$;
REVOKE ALL ON FUNCTION legal_assert_provider_replay_approval_current()
    FROM PUBLIC;

CREATE TRIGGER trg_legal_provider_replay_approval_current
    BEFORE INSERT ON legal_provider_replay_approval_events
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_provider_replay_approval_current();

CREATE OR REPLACE FUNCTION legal_assert_provider_replay_request_binding()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_provider_replay_request$
DECLARE
    approval RECORD;
    approval_count INTEGER := 0;
    has_successor BOOLEAN := FALSE;
    has_consumption BOOLEAN := FALSE;
    catalog RECORD;
    catalog_count INTEGER := 0;
BEGIN
    IF NEW.requested_at < statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.requested_at > statement_timestamp() + INTERVAL '5 minutes'
    THEN
        RAISE EXCEPTION
            'provider replay request timestamp is outside the server window'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_request_server_time';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'legal-provider-replay-approval:'
                || NEW.legal_provider_replay_approval_event_id,
            0
        )
    );

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'dead-letter:' || NEW.legal_provider_dead_letter_id,
            0
        )
    );

    EXECUTE format(
        $sql$
        SELECT
            candidate.approved_requester_id,
            candidate.approved_by,
            candidate.approved_at,
            candidate.expires_at,
            candidate.legal_provider_replay_object_id,
            candidate.provider_id,
            candidate.approved_object_locator,
            candidate.approved_object_version_id,
            candidate.approved_object_sha256,
            candidate.approved_object_retention_until,
            candidate.object_store_receipt_sha256
        FROM %I.legal_provider_replay_approval_events AS candidate
        WHERE candidate.legal_provider_replay_approval_event_id = $1
          AND candidate.tenant_id = $2
          AND candidate.project_id = $3
          AND candidate.legal_provider_dead_letter_id = $4
        $sql$,
        TG_TABLE_SCHEMA
    )
    INTO approval
    USING
        NEW.legal_provider_replay_approval_event_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.legal_provider_dead_letter_id;
    GET DIAGNOSTICS approval_count = ROW_COUNT;

    EXECUTE format(
        $sql$
        SELECT
            replay_object.object_locator,
            replay_object.object_version_id,
            replay_object.object_sha256,
            replay_object.retention_until,
            replay_object.object_store_receipt_sha256
        FROM %I.legal_provider_replay_objects AS replay_object
        WHERE replay_object.legal_provider_replay_object_id = $1
          AND replay_object.tenant_id = $2
          AND replay_object.project_id = $3
          AND replay_object.legal_provider_dead_letter_id = $4
          AND replay_object.provider_id = $5
        $sql$,
        TG_TABLE_SCHEMA
    )
    INTO catalog
    USING
        NEW.legal_provider_replay_object_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.legal_provider_dead_letter_id,
        NEW.provider_id;
    GET DIAGNOSTICS catalog_count = ROW_COUNT;

    EXECUTE format(
        $sql$
        SELECT
            EXISTS (
                SELECT 1
                FROM %1$I.legal_provider_replay_approval_events AS successor
                WHERE successor.supersedes_approval_event_id = $1
            ),
            EXISTS (
                SELECT 1
                FROM %1$I.legal_provider_replay_requests AS consumed
                WHERE consumed.legal_provider_replay_approval_event_id = $1
            )
        $sql$,
        TG_TABLE_SCHEMA
    )
    INTO has_successor, has_consumption
    USING NEW.legal_provider_replay_approval_event_id;

    IF approval_count <> 1
       OR approval.expires_at <= NEW.requested_at
       OR lower(btrim(approval.approved_requester_id))
            <> lower(btrim(NEW.requested_by))
       OR lower(btrim(approval.approved_by))
            = lower(btrim(NEW.requested_by))
       OR approval.approved_object_locator
            <> NEW.approved_object_locator
       OR approval.legal_provider_replay_object_id
            <> NEW.legal_provider_replay_object_id
       OR approval.provider_id <> NEW.provider_id
       OR approval.approved_object_version_id
            <> NEW.approved_object_version_id
       OR approval.approved_object_sha256
            <> NEW.approved_object_sha256
       OR approval.approved_object_retention_until
            <> NEW.approved_object_retention_until
       OR approval.object_store_receipt_sha256
            <> NEW.object_store_receipt_sha256
       OR approval.approved_by
            <> NEW.object_reference_approved_by
       OR approval.approved_at
            <> NEW.object_reference_approved_at
       OR catalog_count <> 1
       OR catalog.object_locator <> NEW.approved_object_locator
       OR catalog.object_version_id <> NEW.approved_object_version_id
       OR catalog.object_sha256 <> NEW.approved_object_sha256
       OR catalog.retention_until
            <> NEW.approved_object_retention_until
       OR catalog.object_store_receipt_sha256
            <> NEW.object_store_receipt_sha256
       OR catalog.retention_until <= statement_timestamp()
       OR has_successor
       OR has_consumption
    THEN
        RAISE EXCEPTION
            'replay request is not bound to a current approved immutable object'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_request_binding';
    END IF;

    RETURN NEW;
END;
$legal_provider_replay_request$;
REVOKE ALL ON FUNCTION legal_assert_provider_replay_request_binding()
    FROM PUBLIC;

CREATE TRIGGER trg_legal_provider_replay_request_binding
    BEFORE INSERT ON legal_provider_replay_requests
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_provider_replay_request_binding();

CREATE OR REPLACE FUNCTION legal_assert_provider_replay_catalog_binding()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_provider_replay_catalog$
DECLARE
    binding_valid BOOLEAN := FALSE;
BEGIN
    IF TG_TABLE_NAME = 'legal_provider_replay_approval_events' THEN
        EXECUTE format(
            $sql$
            SELECT EXISTS (
                SELECT 1
                FROM %I.legal_provider_replay_objects AS replay_object
                WHERE replay_object.legal_provider_replay_object_id = $1
                  AND replay_object.tenant_id = $2
                  AND replay_object.project_id = $3
                  AND replay_object.legal_provider_dead_letter_id = $4
                  AND replay_object.provider_id = $5
                  AND replay_object.object_locator = $6
                  AND replay_object.object_locator_sha256 = $7
                  AND replay_object.object_version_id = $8
                  AND replay_object.object_sha256 = $9
                  AND replay_object.retention_until = $10
                  AND replay_object.object_store_receipt_sha256 = $11
                  AND replay_object.retention_until >= $12
                  AND replay_object.registered_at <= $13
            )
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO binding_valid
        USING
            NEW.legal_provider_replay_object_id,
            NEW.tenant_id,
            NEW.project_id,
            NEW.legal_provider_dead_letter_id,
            NEW.provider_id,
            NEW.approved_object_locator,
            NEW.approved_object_locator_sha256,
            NEW.approved_object_version_id,
            NEW.approved_object_sha256,
            NEW.approved_object_retention_until,
            NEW.object_store_receipt_sha256,
            NEW.expires_at,
            NEW.approved_at;
    ELSIF TG_TABLE_NAME = 'legal_provider_replay_requests' THEN
        EXECUTE format(
            $sql$
            SELECT EXISTS (
                SELECT 1
                FROM %I.legal_provider_replay_objects AS replay_object
                WHERE replay_object.legal_provider_replay_object_id = $1
                  AND replay_object.tenant_id = $2
                  AND replay_object.project_id = $3
                  AND replay_object.legal_provider_dead_letter_id = $4
                  AND replay_object.provider_id = $5
                  AND replay_object.object_locator = $6
                  AND replay_object.object_version_id = $7
                  AND replay_object.object_sha256 = $8
                  AND replay_object.retention_until = $9
                  AND replay_object.object_store_receipt_sha256 = $10
                  AND replay_object.retention_until > $11
            )
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO binding_valid
        USING
            NEW.legal_provider_replay_object_id,
            NEW.tenant_id,
            NEW.project_id,
            NEW.legal_provider_dead_letter_id,
            NEW.provider_id,
            NEW.approved_object_locator,
            NEW.approved_object_version_id,
            NEW.approved_object_sha256,
            NEW.approved_object_retention_until,
            NEW.object_store_receipt_sha256,
            NEW.requested_at;
    END IF;

    IF NOT binding_valid THEN
        RAISE EXCEPTION
            'replay row lost its exact immutable catalog object binding'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_catalog_binding';
    END IF;
    RETURN NEW;
END;
$legal_provider_replay_catalog$;
REVOKE ALL ON FUNCTION legal_assert_provider_replay_catalog_binding()
    FROM PUBLIC;

CREATE CONSTRAINT TRIGGER ctrg_legal_provider_replay_approval_catalog
    AFTER INSERT ON legal_provider_replay_approval_events
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_provider_replay_catalog_binding();
CREATE CONSTRAINT TRIGGER ctrg_legal_provider_replay_request_catalog
    AFTER INSERT ON legal_provider_replay_requests
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_provider_replay_catalog_binding();

CREATE OR REPLACE FUNCTION legal_assert_provider_replay_event_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_provider_replay_event$
DECLARE
    request_requested_at TIMESTAMPTZ;
    request_requested_by TEXT;
    previous_sequence INTEGER;
    previous_event_type TEXT;
    previous_recorded_at TIMESTAMPTZ;
BEGIN
    IF NEW.recorded_at < statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.recorded_at > statement_timestamp() + INTERVAL '5 minutes'
    THEN
        RAISE EXCEPTION
            'provider replay event timestamp is outside the server window'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_event_server_time';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'replay:' || NEW.legal_provider_replay_request_id,
            0
        )
    );

    EXECUTE format(
        $sql$
        SELECT request.requested_at, request.requested_by
        FROM %I.legal_provider_replay_requests AS request
        JOIN %I.legal_provider_replay_objects AS replay_object
          ON replay_object.legal_provider_replay_object_id =
             request.legal_provider_replay_object_id
         AND replay_object.tenant_id = request.tenant_id
         AND replay_object.project_id = request.project_id
         AND replay_object.legal_provider_dead_letter_id =
             request.legal_provider_dead_letter_id
         AND replay_object.provider_id = request.provider_id
         AND replay_object.object_locator =
             request.approved_object_locator
         AND replay_object.object_version_id =
             request.approved_object_version_id
         AND replay_object.object_sha256 =
             request.approved_object_sha256
         AND replay_object.retention_until =
             request.approved_object_retention_until
         AND replay_object.object_store_receipt_sha256 =
             request.object_store_receipt_sha256
        WHERE request.legal_provider_replay_request_id = $1
          AND (
              $2
              OR replay_object.retention_until > statement_timestamp()
          )
        $sql$,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO request_requested_at, request_requested_by
    USING
        NEW.legal_provider_replay_request_id,
        NEW.event_type IN ('completed', 'failed');

    IF request_requested_at IS NULL THEN
        RAISE EXCEPTION
            'replay event must reference a tenant-visible replay request'
            USING ERRCODE = '23503',
                  CONSTRAINT =
                      'fk_legal_provider_replay_event_request';
    END IF;

    EXECUTE format(
        $sql$
        SELECT event.sequence_no, event.event_type, event.recorded_at
        FROM %I.legal_provider_replay_events AS event
        WHERE event.legal_provider_replay_request_id = $1
        ORDER BY event.sequence_no DESC
        LIMIT 1
        $sql$,
        TG_TABLE_SCHEMA
    )
    INTO previous_sequence, previous_event_type, previous_recorded_at
    USING NEW.legal_provider_replay_request_id;

    IF previous_sequence IS NULL THEN
        IF NEW.sequence_no <> 1
           OR NEW.event_type <> 'requested'
           OR NEW.recorded_at < request_requested_at
           OR lower(btrim(NEW.recorded_by))
                <> lower(btrim(request_requested_by))
        THEN
            RAISE EXCEPTION
                'first replay event must be requester-authored requested sequence 1'
                USING ERRCODE = '23514',
                      CONSTRAINT =
                          'ck_legal_provider_replay_event_initial';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.sequence_no <> previous_sequence + 1
       OR NEW.recorded_at < previous_recorded_at
       OR NOT (
           (
               previous_event_type = 'requested'
               AND NEW.event_type = 'running'
           )
           OR (
               previous_event_type = 'running'
               AND NEW.event_type IN ('completed', 'failed')
           )
       )
    THEN
        RAISE EXCEPTION
            'invalid replay event sequence, timestamp, or state transition'
            USING ERRCODE = '23514',
                  CONSTRAINT =
                      'ck_legal_provider_replay_event_transition';
    END IF;

    RETURN NEW;
END;
$legal_provider_replay_event$;
REVOKE ALL ON FUNCTION legal_assert_provider_replay_event_transition()
    FROM PUBLIC;

CREATE TRIGGER trg_legal_provider_replay_event_transition
    BEFORE INSERT ON legal_provider_replay_events
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_provider_replay_event_transition();

-- ---------------------------------------------------------------------------
-- Append-only and tenant isolation
-- ---------------------------------------------------------------------------

CREATE TRIGGER trg_legal_citation_review_events_append_only
    BEFORE UPDATE OR DELETE ON legal_citation_review_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_relation_review_events_append_only
    BEFORE UPDATE OR DELETE ON legal_relation_review_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_approval_delegations_append_only
    BEFORE UPDATE OR DELETE ON legal_approval_delegations
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_required_claim_sets_append_only
    BEFORE UPDATE OR DELETE ON legal_required_claim_sets
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_required_claims_append_only
    BEFORE UPDATE OR DELETE ON legal_required_claims
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_project_legal_matters_append_only
    BEFORE UPDATE OR DELETE ON project_legal_matters
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_waivers_append_only
    BEFORE UPDATE OR DELETE ON legal_waivers
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_counsel_opinions_append_only
    BEFORE UPDATE OR DELETE ON counsel_opinions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_analysis_versions_append_only
    BEFORE UPDATE OR DELETE ON legal_analysis_versions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_project_legal_annotations_append_only
    BEFORE UPDATE OR DELETE ON project_legal_annotations
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_tenant_legal_term_aliases_append_only
    BEFORE UPDATE OR DELETE ON tenant_legal_term_aliases
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_tenant_legal_term_alias_review_events_append_only
    BEFORE UPDATE OR DELETE ON tenant_legal_term_alias_review_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_project_legal_review_versions_append_only
    BEFORE UPDATE OR DELETE ON project_legal_review_versions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_project_permit_review_versions_append_only
    BEFORE UPDATE OR DELETE ON project_permit_review_versions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_term_source_links_append_only
    BEFORE UPDATE OR DELETE ON legal_term_source_links
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_term_relations_append_only
    BEFORE UPDATE OR DELETE ON legal_term_relations
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_provider_quarantine_append_only
    BEFORE UPDATE OR DELETE ON legal_provider_quarantine
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_provider_dead_letters_append_only
    BEFORE UPDATE OR DELETE ON legal_provider_dead_letters
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_provider_metrics_append_only
    BEFORE UPDATE OR DELETE ON legal_provider_metrics
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_provider_replay_objects_append_only
    BEFORE UPDATE OR DELETE ON legal_provider_replay_objects
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_provider_replay_approval_events_append_only
    BEFORE UPDATE OR DELETE ON legal_provider_replay_approval_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_provider_replay_requests_append_only
    BEFORE UPDATE OR DELETE ON legal_provider_replay_requests
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_legal_provider_replay_events_append_only
    BEFORE UPDATE OR DELETE ON legal_provider_replay_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

ALTER TABLE legal_approval_delegations ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_approval_delegations FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON legal_approval_delegations
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_required_claim_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_required_claim_sets FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON legal_required_claim_sets
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_required_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_required_claims FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON legal_required_claims
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE project_legal_matters ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_legal_matters FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON project_legal_matters
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_waivers ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_waivers FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON legal_waivers
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE counsel_opinions ENABLE ROW LEVEL SECURITY;
ALTER TABLE counsel_opinions FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON counsel_opinions
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_analysis_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_analysis_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON legal_analysis_versions
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE project_legal_annotations ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_legal_annotations FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON project_legal_annotations
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE tenant_legal_term_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_legal_term_aliases FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation ON tenant_legal_term_aliases
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE tenant_legal_term_alias_review_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_legal_term_alias_review_events FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON tenant_legal_term_alias_review_events
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE project_legal_review_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_legal_review_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON project_legal_review_versions
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE project_permit_review_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_permit_review_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON project_permit_review_versions
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

-- Provider quarantine/dead-letter/metric rows remain global operational
-- metadata by design.  Approval and replay lifecycle rows are tenant-native:
-- the operations principal must set realytics.tenant_id for every transaction,
-- and FORCE RLS prevents it from observing or appending another tenant's rows.
ALTER TABLE legal_provider_replay_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_provider_replay_objects FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON legal_provider_replay_objects
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_provider_replay_approval_events
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_provider_replay_approval_events
    FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON legal_provider_replay_approval_events
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_provider_replay_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_provider_replay_requests FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON legal_provider_replay_requests
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_provider_replay_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_provider_replay_events FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON legal_provider_replay_events
    FOR ALL
    USING (
        EXISTS (
            SELECT 1
            FROM legal_provider_replay_requests AS replay_request
            WHERE replay_request.legal_provider_replay_request_id =
                legal_provider_replay_events
                    .legal_provider_replay_request_id
              AND replay_request.tenant_id = NULLIF(
                  current_setting('realytics.tenant_id', true),
                  ''
              )
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1
            FROM legal_provider_replay_requests AS replay_request
            WHERE replay_request.legal_provider_replay_request_id =
                legal_provider_replay_events
                    .legal_provider_replay_request_id
              AND replay_request.tenant_id = NULLIF(
                  current_setting('realytics.tenant_id', true),
                  ''
              )
        )
    );

REVOKE ALL ON TABLE
    legal_citation_review_events,
    legal_relation_review_events,
    legal_approval_delegations,
    legal_required_claim_sets,
    legal_required_claims,
    project_legal_matters,
    legal_waivers,
    counsel_opinions,
    legal_analysis_versions,
    project_legal_annotations,
    tenant_legal_term_aliases,
    tenant_legal_term_alias_review_events,
    project_legal_review_versions,
    project_permit_review_versions,
    legal_term_source_links,
    legal_term_relations,
    legal_provider_quarantine,
    legal_provider_dead_letters,
    legal_provider_metrics,
    legal_provider_replay_objects,
    legal_provider_replay_approval_events,
    legal_provider_replay_requests,
    legal_provider_replay_events
FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Tenant/project Citator review overlay
-- ---------------------------------------------------------------------------
--
-- legal_citations and legal_relation_assertions are institution-wide Corpus
-- facts.  Project review never creates a successor in those global tables:
-- the decision, its version chain and its event chain live only in the
-- tenant-native overlay below.

CREATE TABLE IF NOT EXISTS project_legal_citation_review_versions (
    project_legal_citation_review_version_id TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    legal_citation_id              TEXT NOT NULL
        REFERENCES legal_citations(legal_citation_id) ON DELETE RESTRICT,
    legal_evidence_snapshot_id     TEXT NOT NULL,
    legal_evidence_item_id         TEXT NOT NULL,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT NOT NULL,
    source_fetch_id                TEXT NOT NULL,
    provider_id                    TEXT NOT NULL,
    evidence_exact_locator         TEXT NOT NULL,
    bound_excerpt_sha256           TEXT NOT NULL,
    bound_provision_sha256         TEXT NOT NULL,
    review_version                 INTEGER NOT NULL,
    review_status                  TEXT NOT NULL,
    effective_citation_status      TEXT NOT NULL,
    human_review_status            TEXT NOT NULL,
    maker_id                       TEXT NOT NULL,
    reviewer_id                    TEXT NOT NULL,
    reviewer_role                  TEXT NOT NULL,
    reviewed_at                    TIMESTAMPTZ NOT NULL,
    reason                         TEXT NOT NULL,
    evidence_excerpt               TEXT,
    evidence_excerpt_sha256        TEXT,
    legal_approval_delegation_id   TEXT,
    supersedes_review_version_id   TEXT,
    created_at                     TIMESTAMPTZ NOT NULL
        DEFAULT statement_timestamp(),
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        project_legal_citation_review_version_id,
        tenant_id,
        project_id
    ),
    UNIQUE (
        tenant_id,
        project_id,
        legal_citation_id,
        review_version
    ),
    CONSTRAINT fk_project_citation_review_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_snapshot
        FOREIGN KEY (
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_item
        FOREIGN KEY (
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshot_items(
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_version_source
        FOREIGN KEY (legal_resource_version_id)
        REFERENCES legal_resource_versions(legal_resource_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_fetch_provider
        FOREIGN KEY (source_fetch_id, provider_id)
        REFERENCES legal_source_fetches(source_fetch_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_provider
        FOREIGN KEY (provider_id)
        REFERENCES legal_source_providers(provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_supersedes
        FOREIGN KEY (
            supersedes_review_version_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_citation_review_versions(
            project_legal_citation_review_version_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_citation_review_version
        CHECK (review_version >= 1),
    CONSTRAINT ck_project_citation_review_binding
        CHECK (
            btrim(evidence_exact_locator) <> ''
            AND bound_excerpt_sha256 ~ '^[0-9a-f]{64}$'
            AND bound_provision_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_project_citation_review_status
        CHECK (review_status IN (
            'approved', 'rejected', 'needs_revision'
        )),
    CONSTRAINT ck_project_citation_review_status_exact
        CHECK (
            (
                review_status = 'approved'
                AND effective_citation_status = 'verified'
                AND human_review_status = 'approved'
            )
            OR (
                review_status = 'rejected'
                AND effective_citation_status = 'rejected'
                AND human_review_status = 'rejected'
            )
            OR (
                review_status = 'needs_revision'
                AND effective_citation_status = 'unknown'
                AND human_review_status = 'pending'
            )
        ),
    CONSTRAINT ck_project_citation_review_actor
        CHECK (
            NULLIF(btrim(maker_id), '') IS NOT NULL
            AND NULLIF(btrim(reviewer_id), '') IS NOT NULL
            AND lower(btrim(maker_id)) <> lower(btrim(reviewer_id))
            AND reviewer_role IN (
                'legal_reviewer',
                'legal_counsel_approver',
                'external_counsel'
            )
            AND (
                review_status = 'needs_revision'
                OR reviewer_role IN (
                    'legal_counsel_approver',
                    'external_counsel'
                )
            )
            AND (
                reviewer_role <> 'external_counsel'
                OR legal_approval_delegation_id IS NOT NULL
            )
            AND NULLIF(btrim(reason), '') IS NOT NULL
        ),
    CONSTRAINT ck_project_citation_review_excerpt
        CHECK (
            (
                evidence_excerpt IS NULL
                AND evidence_excerpt_sha256 IS NULL
            )
            OR (
                NULLIF(btrim(evidence_excerpt), '') IS NOT NULL
                AND evidence_excerpt_sha256 =
                    encode(
                        extensions.digest(
                            convert_to(evidence_excerpt, 'UTF8'),
                            'sha256'
                        ),
                        'hex'
                    )
            )
        ),
    CONSTRAINT ck_project_citation_review_approval_evidence
        CHECK (
            review_status <> 'approved'
            OR (
                NULLIF(btrim(evidence_excerpt), '') IS NOT NULL
                AND evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT ck_project_citation_review_not_self_superseding
        CHECK (
            supersedes_review_version_id IS NULL
            OR supersedes_review_version_id
                <> project_legal_citation_review_version_id
        ),
    CONSTRAINT ck_project_citation_review_metadata
        CHECK (
            jsonb_typeof(metadata_json) = 'object'
            AND NOT metadata_json ?| ARRAY[
                'tenantId',
                'projectId',
                'sourceCitationId',
                'reviewerId',
                'reviewerRole',
                'reviewStatus',
                'legalApprovalDelegationId',
                'makerId',
                'reviewVersion',
                'snapshotId',
                'snapshotVersion',
                'snapshotSha256',
                'snapshotAsOfDate',
                'evidenceItemId',
                'resourceVersionId',
                'provisionId',
                'exactLocator',
                'sourceFetchId',
                'providerId',
                'excerptSha256',
                'provisionSha256',
                'verifiedAt',
                'sourceAuthority',
                'authorityTier',
                'freshnessPolicyVersion',
                'currentEvidenceBinding',
                'evidenceBinding',
                'reviewBindingCurrent',
                'objectEvidenceBinding',
                'objectEvidenceItemId',
                'objectResourceVersionId',
                'objectProvisionId',
                'objectExactLocator',
                'objectSourceFetchId',
                'objectProviderId',
                'objectExcerptSha256',
                'objectProvisionSha256'
            ]
        )
);
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_project_legal_citation_review_single_successor
    ON project_legal_citation_review_versions(
        supersedes_review_version_id
    )
    WHERE supersedes_review_version_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_project_legal_citation_review_latest
    ON project_legal_citation_review_versions(
        tenant_id,
        project_id,
        legal_citation_id,
        review_version DESC
    );

CREATE TABLE IF NOT EXISTS project_legal_relation_review_versions (
    project_legal_relation_review_version_id TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    legal_relation_assertion_id    TEXT NOT NULL
        REFERENCES legal_relation_assertions(
            legal_relation_assertion_id
        )
        ON DELETE RESTRICT,
    legal_evidence_snapshot_id     TEXT NOT NULL,
    legal_evidence_item_id         TEXT NOT NULL,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT NOT NULL,
    source_fetch_id                TEXT NOT NULL,
    provider_id                    TEXT NOT NULL,
    evidence_exact_locator         TEXT NOT NULL,
    bound_excerpt_sha256           TEXT NOT NULL,
    bound_provision_sha256         TEXT NOT NULL,
    object_legal_evidence_item_id  TEXT,
    object_legal_resource_version_id TEXT,
    object_legal_provision_id      TEXT,
    object_source_fetch_id         TEXT,
    object_provider_id             TEXT,
    object_evidence_exact_locator  TEXT,
    object_bound_excerpt_sha256    TEXT,
    object_bound_provision_sha256  TEXT,
    review_version                 INTEGER NOT NULL,
    review_status                  TEXT NOT NULL,
    effective_assertion_status     TEXT NOT NULL,
    human_review_status            TEXT NOT NULL,
    maker_id                       TEXT NOT NULL,
    reviewer_id                    TEXT NOT NULL,
    reviewer_role                  TEXT NOT NULL,
    reviewed_at                    TIMESTAMPTZ NOT NULL,
    reason                         TEXT NOT NULL,
    evidence_excerpt               TEXT,
    evidence_excerpt_sha256        TEXT,
    legal_approval_delegation_id   TEXT,
    supersedes_review_version_id   TEXT,
    created_at                     TIMESTAMPTZ NOT NULL
        DEFAULT statement_timestamp(),
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        project_legal_relation_review_version_id,
        tenant_id,
        project_id
    ),
    UNIQUE (
        tenant_id,
        project_id,
        legal_relation_assertion_id,
        review_version
    ),
    CONSTRAINT fk_project_relation_review_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_snapshot
        FOREIGN KEY (
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_item
        FOREIGN KEY (
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshot_items(
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_version_source
        FOREIGN KEY (legal_resource_version_id)
        REFERENCES legal_resource_versions(legal_resource_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_fetch_provider
        FOREIGN KEY (source_fetch_id, provider_id)
        REFERENCES legal_source_fetches(source_fetch_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_provider
        FOREIGN KEY (provider_id)
        REFERENCES legal_source_providers(provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_object_item
        FOREIGN KEY (
            object_legal_evidence_item_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshot_items(
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_object_version
        FOREIGN KEY (object_legal_resource_version_id)
        REFERENCES legal_resource_versions(legal_resource_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_object_provision
        FOREIGN KEY (
            object_legal_provision_id,
            object_legal_resource_version_id
        )
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_object_fetch
        FOREIGN KEY (object_source_fetch_id, object_provider_id)
        REFERENCES legal_source_fetches(source_fetch_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_object_provider
        FOREIGN KEY (object_provider_id)
        REFERENCES legal_source_providers(provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_supersedes
        FOREIGN KEY (
            supersedes_review_version_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_relation_review_versions(
            project_legal_relation_review_version_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_relation_review_version
        CHECK (review_version >= 1),
    CONSTRAINT ck_project_relation_review_binding
        CHECK (
            btrim(evidence_exact_locator) <> ''
            AND bound_excerpt_sha256 ~ '^[0-9a-f]{64}$'
            AND bound_provision_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_project_relation_review_object_binding
        CHECK (
            (
                object_legal_evidence_item_id IS NULL
                AND object_legal_resource_version_id IS NULL
                AND object_legal_provision_id IS NULL
                AND object_source_fetch_id IS NULL
                AND object_provider_id IS NULL
                AND object_evidence_exact_locator IS NULL
                AND object_bound_excerpt_sha256 IS NULL
                AND object_bound_provision_sha256 IS NULL
            )
            OR (
                object_legal_evidence_item_id IS NOT NULL
                AND object_legal_resource_version_id IS NOT NULL
                AND object_legal_provision_id IS NOT NULL
                AND object_source_fetch_id IS NOT NULL
                AND object_provider_id IS NOT NULL
                AND NULLIF(
                    btrim(object_evidence_exact_locator),
                    ''
                ) IS NOT NULL
                AND object_bound_excerpt_sha256 ~ '^[0-9a-f]{64}$'
                AND object_bound_provision_sha256 ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT ck_project_relation_review_verified_object_binding
        CHECK (
            review_status <> 'approved'
            OR object_legal_evidence_item_id IS NOT NULL
        ),
    CONSTRAINT ck_project_relation_review_status
        CHECK (review_status IN (
            'approved', 'rejected', 'needs_revision'
        )),
    CONSTRAINT ck_project_relation_review_status_exact
        CHECK (
            (
                review_status = 'approved'
                AND effective_assertion_status = 'verified'
                AND human_review_status = 'approved'
            )
            OR (
                review_status = 'rejected'
                AND effective_assertion_status = 'rejected'
                AND human_review_status = 'rejected'
            )
            OR (
                review_status = 'needs_revision'
                AND effective_assertion_status = 'unknown'
                AND human_review_status = 'pending'
            )
        ),
    CONSTRAINT ck_project_relation_review_actor
        CHECK (
            NULLIF(btrim(maker_id), '') IS NOT NULL
            AND NULLIF(btrim(reviewer_id), '') IS NOT NULL
            AND lower(btrim(maker_id)) <> lower(btrim(reviewer_id))
            AND reviewer_role IN (
                'legal_reviewer',
                'legal_counsel_approver',
                'external_counsel'
            )
            AND (
                review_status = 'needs_revision'
                OR reviewer_role IN (
                    'legal_counsel_approver',
                    'external_counsel'
                )
            )
            AND (
                reviewer_role <> 'external_counsel'
                OR legal_approval_delegation_id IS NOT NULL
            )
            AND NULLIF(btrim(reason), '') IS NOT NULL
        ),
    CONSTRAINT ck_project_relation_review_excerpt
        CHECK (
            (
                evidence_excerpt IS NULL
                AND evidence_excerpt_sha256 IS NULL
            )
            OR (
                NULLIF(btrim(evidence_excerpt), '') IS NOT NULL
                AND evidence_excerpt_sha256 =
                    encode(
                        extensions.digest(
                            convert_to(evidence_excerpt, 'UTF8'),
                            'sha256'
                        ),
                        'hex'
                    )
            )
        ),
    CONSTRAINT ck_project_relation_review_approval_evidence
        CHECK (
            review_status <> 'approved'
            OR (
                NULLIF(btrim(evidence_excerpt), '') IS NOT NULL
                AND evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT ck_project_relation_review_not_self_superseding
        CHECK (
            supersedes_review_version_id IS NULL
            OR supersedes_review_version_id
                <> project_legal_relation_review_version_id
        ),
    CONSTRAINT ck_project_relation_review_metadata
        CHECK (
            jsonb_typeof(metadata_json) = 'object'
            AND NOT metadata_json ?| ARRAY[
                'tenantId',
                'projectId',
                'sourceAssertionId',
                'reviewerId',
                'reviewerRole',
                'reviewStatus',
                'legalApprovalDelegationId',
                'makerId',
                'reviewVersion',
                'snapshotId',
                'snapshotVersion',
                'snapshotSha256',
                'snapshotAsOfDate',
                'evidenceItemId',
                'resourceVersionId',
                'provisionId',
                'exactLocator',
                'sourceFetchId',
                'providerId',
                'excerptSha256',
                'provisionSha256',
                'verifiedAt',
                'sourceAuthority',
                'authorityTier',
                'freshnessPolicyVersion',
                'currentEvidenceBinding',
                'evidenceBinding',
                'reviewBindingCurrent',
                'objectEvidenceBinding',
                'objectEvidenceItemId',
                'objectResourceVersionId',
                'objectProvisionId',
                'objectExactLocator',
                'objectSourceFetchId',
                'objectProviderId',
                'objectExcerptSha256',
                'objectProvisionSha256'
            ]
        )
);
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_project_legal_relation_review_single_successor
    ON project_legal_relation_review_versions(
        supersedes_review_version_id
    )
    WHERE supersedes_review_version_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_project_legal_relation_review_latest
    ON project_legal_relation_review_versions(
        tenant_id,
        project_id,
        legal_relation_assertion_id,
        review_version DESC
    );

CREATE TABLE IF NOT EXISTS project_legal_citation_review_events (
    project_legal_citation_review_event_id TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL,
    project_id                     TEXT NOT NULL,
    project_legal_citation_review_version_id TEXT NOT NULL,
    legal_citation_id              TEXT NOT NULL,
    legal_evidence_snapshot_id     TEXT NOT NULL,
    legal_evidence_item_id         TEXT NOT NULL,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT NOT NULL,
    source_fetch_id                TEXT NOT NULL,
    provider_id                    TEXT NOT NULL,
    evidence_exact_locator         TEXT NOT NULL,
    bound_excerpt_sha256           TEXT NOT NULL,
    bound_provision_sha256         TEXT NOT NULL,
    event_sequence                 INTEGER NOT NULL,
    review_status                  TEXT NOT NULL,
    reviewer_id                    TEXT NOT NULL,
    reviewer_role                  TEXT NOT NULL,
    reviewed_at                    TIMESTAMPTZ NOT NULL,
    reason                         TEXT NOT NULL,
    evidence_excerpt_sha256        TEXT,
    legal_approval_delegation_id   TEXT,
    supersedes_review_event_id     TEXT,
    recorded_at                    TIMESTAMPTZ NOT NULL
        DEFAULT statement_timestamp(),
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        project_legal_citation_review_event_id,
        tenant_id,
        project_id
    ),
    UNIQUE (project_legal_citation_review_version_id),
    CONSTRAINT fk_project_citation_review_event_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_version
        FOREIGN KEY (
            project_legal_citation_review_version_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_citation_review_versions(
            project_legal_citation_review_version_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_source
        FOREIGN KEY (legal_citation_id)
        REFERENCES legal_citations(legal_citation_id) ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_snapshot
        FOREIGN KEY (
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_item
        FOREIGN KEY (
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshot_items(
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_version_source
        FOREIGN KEY (legal_resource_version_id)
        REFERENCES legal_resource_versions(legal_resource_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_fetch_provider
        FOREIGN KEY (source_fetch_id, provider_id)
        REFERENCES legal_source_fetches(source_fetch_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_provider
        FOREIGN KEY (provider_id)
        REFERENCES legal_source_providers(provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_citation_review_event_supersedes
        FOREIGN KEY (
            supersedes_review_event_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_citation_review_events(
            project_legal_citation_review_event_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_citation_review_event_sequence
        CHECK (event_sequence >= 1),
    CONSTRAINT ck_project_citation_review_event_binding
        CHECK (
            btrim(evidence_exact_locator) <> ''
            AND bound_excerpt_sha256 ~ '^[0-9a-f]{64}$'
            AND bound_provision_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_project_citation_review_event_status
        CHECK (review_status IN (
            'approved', 'rejected', 'needs_revision'
        )),
    CONSTRAINT ck_project_citation_review_event_actor
        CHECK (
            NULLIF(btrim(reviewer_id), '') IS NOT NULL
            AND reviewer_role IN (
                'legal_reviewer',
                'legal_counsel_approver',
                'external_counsel'
            )
            AND NULLIF(btrim(reason), '') IS NOT NULL
        ),
    CONSTRAINT ck_project_citation_review_event_hash
        CHECK (
            evidence_excerpt_sha256 IS NULL
            OR evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_project_citation_review_event_not_self_superseding
        CHECK (
            supersedes_review_event_id IS NULL
            OR supersedes_review_event_id
                <> project_legal_citation_review_event_id
        ),
    CONSTRAINT ck_project_citation_review_event_metadata
        CHECK (
            jsonb_typeof(metadata_json) = 'object'
            AND NOT metadata_json ?| ARRAY[
                'tenantId',
                'projectId',
                'sourceCitationId',
                'reviewerId',
                'reviewerRole',
                'reviewStatus',
                'legalApprovalDelegationId',
                'makerId',
                'reviewVersion',
                'snapshotId',
                'snapshotVersion',
                'snapshotSha256',
                'snapshotAsOfDate',
                'evidenceItemId',
                'resourceVersionId',
                'provisionId',
                'exactLocator',
                'sourceFetchId',
                'providerId',
                'excerptSha256',
                'provisionSha256',
                'verifiedAt',
                'sourceAuthority',
                'authorityTier',
                'freshnessPolicyVersion',
                'currentEvidenceBinding',
                'evidenceBinding',
                'reviewBindingCurrent'
            ]
        )
);
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_project_legal_citation_review_event_single_successor
    ON project_legal_citation_review_events(supersedes_review_event_id)
    WHERE supersedes_review_event_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS project_legal_relation_review_events (
    project_legal_relation_review_event_id TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL,
    project_id                     TEXT NOT NULL,
    project_legal_relation_review_version_id TEXT NOT NULL,
    legal_relation_assertion_id    TEXT NOT NULL,
    legal_evidence_snapshot_id     TEXT NOT NULL,
    legal_evidence_item_id         TEXT NOT NULL,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT NOT NULL,
    source_fetch_id                TEXT NOT NULL,
    provider_id                    TEXT NOT NULL,
    evidence_exact_locator         TEXT NOT NULL,
    bound_excerpt_sha256           TEXT NOT NULL,
    bound_provision_sha256         TEXT NOT NULL,
    object_legal_evidence_item_id  TEXT,
    object_legal_resource_version_id TEXT,
    object_legal_provision_id      TEXT,
    object_source_fetch_id         TEXT,
    object_provider_id             TEXT,
    object_evidence_exact_locator  TEXT,
    object_bound_excerpt_sha256    TEXT,
    object_bound_provision_sha256  TEXT,
    event_sequence                 INTEGER NOT NULL,
    review_status                  TEXT NOT NULL,
    reviewer_id                    TEXT NOT NULL,
    reviewer_role                  TEXT NOT NULL,
    reviewed_at                    TIMESTAMPTZ NOT NULL,
    reason                         TEXT NOT NULL,
    evidence_excerpt_sha256        TEXT,
    legal_approval_delegation_id   TEXT,
    supersedes_review_event_id     TEXT,
    recorded_at                    TIMESTAMPTZ NOT NULL
        DEFAULT statement_timestamp(),
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        project_legal_relation_review_event_id,
        tenant_id,
        project_id
    ),
    UNIQUE (project_legal_relation_review_version_id),
    CONSTRAINT fk_project_relation_review_event_project
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_version
        FOREIGN KEY (
            project_legal_relation_review_version_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_relation_review_versions(
            project_legal_relation_review_version_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_source
        FOREIGN KEY (legal_relation_assertion_id)
        REFERENCES legal_relation_assertions(
            legal_relation_assertion_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_snapshot
        FOREIGN KEY (
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_item
        FOREIGN KEY (
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshot_items(
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_version_source
        FOREIGN KEY (legal_resource_version_id)
        REFERENCES legal_resource_versions(legal_resource_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_fetch_provider
        FOREIGN KEY (source_fetch_id, provider_id)
        REFERENCES legal_source_fetches(source_fetch_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_provider
        FOREIGN KEY (provider_id)
        REFERENCES legal_source_providers(provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_object_item
        FOREIGN KEY (
            object_legal_evidence_item_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_evidence_snapshot_items(
            legal_evidence_item_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_object_version
        FOREIGN KEY (object_legal_resource_version_id)
        REFERENCES legal_resource_versions(legal_resource_version_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_object_provision
        FOREIGN KEY (
            object_legal_provision_id,
            object_legal_resource_version_id
        )
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_object_fetch
        FOREIGN KEY (object_source_fetch_id, object_provider_id)
        REFERENCES legal_source_fetches(source_fetch_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_object_provider
        FOREIGN KEY (object_provider_id)
        REFERENCES legal_source_providers(provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_delegation
        FOREIGN KEY (
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        REFERENCES legal_approval_delegations(
            legal_approval_delegation_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_relation_review_event_supersedes
        FOREIGN KEY (
            supersedes_review_event_id,
            tenant_id,
            project_id
        )
        REFERENCES project_legal_relation_review_events(
            project_legal_relation_review_event_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_relation_review_event_sequence
        CHECK (event_sequence >= 1),
    CONSTRAINT ck_project_relation_review_event_binding
        CHECK (
            btrim(evidence_exact_locator) <> ''
            AND bound_excerpt_sha256 ~ '^[0-9a-f]{64}$'
            AND bound_provision_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_project_relation_review_event_object_binding
        CHECK (
            (
                object_legal_evidence_item_id IS NULL
                AND object_legal_resource_version_id IS NULL
                AND object_legal_provision_id IS NULL
                AND object_source_fetch_id IS NULL
                AND object_provider_id IS NULL
                AND object_evidence_exact_locator IS NULL
                AND object_bound_excerpt_sha256 IS NULL
                AND object_bound_provision_sha256 IS NULL
            )
            OR (
                object_legal_evidence_item_id IS NOT NULL
                AND object_legal_resource_version_id IS NOT NULL
                AND object_legal_provision_id IS NOT NULL
                AND object_source_fetch_id IS NOT NULL
                AND object_provider_id IS NOT NULL
                AND NULLIF(
                    btrim(object_evidence_exact_locator),
                    ''
                ) IS NOT NULL
                AND object_bound_excerpt_sha256 ~ '^[0-9a-f]{64}$'
                AND object_bound_provision_sha256 ~ '^[0-9a-f]{64}$'
            )
        ),
    CONSTRAINT ck_project_relation_review_event_verified_object
        CHECK (
            review_status <> 'approved'
            OR object_legal_evidence_item_id IS NOT NULL
        ),
    CONSTRAINT ck_project_relation_review_event_status
        CHECK (review_status IN (
            'approved', 'rejected', 'needs_revision'
        )),
    CONSTRAINT ck_project_relation_review_event_actor
        CHECK (
            NULLIF(btrim(reviewer_id), '') IS NOT NULL
            AND reviewer_role IN (
                'legal_reviewer',
                'legal_counsel_approver',
                'external_counsel'
            )
            AND NULLIF(btrim(reason), '') IS NOT NULL
        ),
    CONSTRAINT ck_project_relation_review_event_hash
        CHECK (
            evidence_excerpt_sha256 IS NULL
            OR evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_project_relation_review_event_not_self_superseding
        CHECK (
            supersedes_review_event_id IS NULL
            OR supersedes_review_event_id
                <> project_legal_relation_review_event_id
        ),
    CONSTRAINT ck_project_relation_review_event_metadata
        CHECK (
            jsonb_typeof(metadata_json) = 'object'
            AND NOT metadata_json ?| ARRAY[
                'tenantId',
                'projectId',
                'sourceAssertionId',
                'reviewerId',
                'reviewerRole',
                'reviewStatus',
                'legalApprovalDelegationId',
                'makerId',
                'reviewVersion',
                'snapshotId',
                'snapshotVersion',
                'snapshotSha256',
                'snapshotAsOfDate',
                'evidenceItemId',
                'resourceVersionId',
                'provisionId',
                'exactLocator',
                'sourceFetchId',
                'providerId',
                'excerptSha256',
                'provisionSha256',
                'verifiedAt',
                'sourceAuthority',
                'authorityTier',
                'freshnessPolicyVersion',
                'currentEvidenceBinding',
                'evidenceBinding',
                'reviewBindingCurrent',
                'objectEvidenceBinding',
                'objectEvidenceItemId',
                'objectResourceVersionId',
                'objectProvisionId',
                'objectExactLocator',
                'objectSourceFetchId',
                'objectProviderId',
                'objectExcerptSha256',
                'objectProvisionSha256'
            ]
        )
);
CREATE UNIQUE INDEX IF NOT EXISTS
    uq_project_legal_relation_review_event_single_successor
    ON project_legal_relation_review_events(supersedes_review_event_id)
    WHERE supersedes_review_event_id IS NOT NULL;

CREATE OR REPLACE FUNCTION legal_assert_project_citator_review_version()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_project_citator_review_version$
DECLARE
    source_id TEXT;
    source_maker TEXT;
    source_asserted_at TIMESTAMPTZ;
    source_resource_id TEXT;
    source_version_id TEXT;
    source_provision_id TEXT;
    source_object_resource_id TEXT;
    project_scope_valid BOOLEAN := FALSE;
    object_scope_valid BOOLEAN := TRUE;
    latest_version_id TEXT;
    latest_version INTEGER;
    delegation_valid BOOLEAN;
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM NULLIF(
        current_setting('realytics.tenant_id', true),
        ''
    ) THEN
        RAISE EXCEPTION
            'Citator review tenant does not match transaction tenant'
            USING ERRCODE = '42501',
                  CONSTRAINT = 'ck_project_citator_review_tenant';
    END IF;

    IF NEW.reviewed_at < statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.reviewed_at > statement_timestamp() + INTERVAL '5 minutes'
       OR NEW.created_at <
            statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.created_at >
            statement_timestamp() + INTERVAL '5 minutes'
    THEN
        RAISE EXCEPTION
            'Citator review timestamps must be DB statement time +/- 5 minutes'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_citator_review_time';
    END IF;

    IF TG_TABLE_NAME = 'project_legal_citation_review_versions' THEN
        source_id := NEW.legal_citation_id;
        PERFORM pg_advisory_xact_lock(
            hashtextextended(
                'project-citator:'
                    || NEW.tenant_id || ':'
                    || NEW.project_id || ':citation:'
                    || source_id,
                0
            )
        );
        EXECUTE format(
            $sql$
            SELECT
                COALESCE(
                    NULLIF(btrim(citation.metadata_json ->> 'assertedBy'), ''),
                    NULLIF(btrim(citation.metadata_json ->> 'createdBy'), ''),
                    NULLIF(btrim(citation.metadata_json ->> 'makerId'), ''),
                    CASE
                        WHEN citation.assertion_method <> 'human_asserted'
                            THEN 'official_corpus'
                        ELSE NULL
                    END
                ),
                citation.asserted_at,
                citation.source_resource_id,
                citation.source_version_id,
                citation.source_provision_id
            FROM %I.legal_citations AS citation
            WHERE citation.legal_citation_id = $1
              AND citation.citation_status IN (
                    'unknown', 'unresolved', 'ambiguous', 'resolved'
              )
              AND NULLIF(
                    btrim(citation.metadata_json ->> 'reviewStatus'),
                    ''
              ) IS NULL
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO
            source_maker,
            source_asserted_at,
            source_resource_id,
            source_version_id,
            source_provision_id
        USING source_id;
        EXECUTE format(
            $sql$
            SELECT
                review.project_legal_citation_review_version_id,
                review.review_version
            FROM %I.project_legal_citation_review_versions AS review
            WHERE review.tenant_id = $1
              AND review.project_id = $2
              AND review.legal_citation_id = $3
            ORDER BY review.review_version DESC
            LIMIT 1
            FOR UPDATE
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO latest_version_id, latest_version
        USING NEW.tenant_id, NEW.project_id, source_id;
    ELSIF TG_TABLE_NAME = 'project_legal_relation_review_versions' THEN
        source_id := NEW.legal_relation_assertion_id;
        PERFORM pg_advisory_xact_lock(
            hashtextextended(
                'project-citator:'
                    || NEW.tenant_id || ':'
                    || NEW.project_id || ':relation:'
                    || source_id,
                0
            )
        );
        EXECUTE format(
            $sql$
            SELECT
                COALESCE(
                    NULLIF(btrim(relation.metadata_json ->> 'assertedBy'), ''),
                    NULLIF(btrim(relation.metadata_json ->> 'createdBy'), ''),
                    NULLIF(btrim(relation.metadata_json ->> 'makerId'), ''),
                    CASE
                        WHEN relation.assertion_method <> 'human_asserted'
                            THEN 'official_corpus'
                        ELSE NULL
                    END
                ),
                relation.asserted_at,
                relation.subject_resource_id,
                relation.source_version_id,
                relation.source_provision_id,
                relation.object_resource_id
            FROM %I.legal_relation_assertions AS relation
            WHERE relation.legal_relation_assertion_id = $1
              AND relation.assertion_status IN ('unknown', 'candidate')
              AND NULLIF(
                    btrim(relation.metadata_json ->> 'reviewStatus'),
                    ''
              ) IS NULL
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO
            source_maker,
            source_asserted_at,
            source_resource_id,
            source_version_id,
            source_provision_id,
            source_object_resource_id
        USING source_id;
        EXECUTE format(
            $sql$
            SELECT
                review.project_legal_relation_review_version_id,
                review.review_version
            FROM %I.project_legal_relation_review_versions AS review
            WHERE review.tenant_id = $1
              AND review.project_id = $2
              AND review.legal_relation_assertion_id = $3
            ORDER BY review.review_version DESC
            LIMIT 1
            FOR UPDATE
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO latest_version_id, latest_version
        USING NEW.tenant_id, NEW.project_id, source_id;
    ELSE
        RAISE EXCEPTION 'unsupported project Citator review table';
    END IF;

    -- A review is valid only when its server-selected binding is the exact
    -- item in the latest non-superseded project snapshot and the candidate,
    -- provider fetch, resource version, lineage, provision and locator all
    -- still agree at DB statement time.
    EXECUTE format(
        $sql$
        SELECT EXISTS (
            SELECT 1
            FROM %I.legal_evidence_snapshots AS snapshot
            JOIN %I.legal_evidence_snapshot_items AS item
              ON item.tenant_id = snapshot.tenant_id
             AND item.project_id = snapshot.project_id
             AND item.legal_evidence_snapshot_id =
                 snapshot.legal_evidence_snapshot_id
             AND item.legal_evidence_item_id = $4
             AND item.legal_resource_id = $12
             AND item.legal_resource_version_id = $5
             AND item.legal_provision_id = $6
             AND item.source_fetch_id = $7
             AND item.exact_locator = $9
             AND item.excerpt_sha256 = $10
            JOIN %I.legal_resource_versions AS version
              ON version.legal_resource_version_id =
                 item.legal_resource_version_id
             AND version.legal_resource_id = item.legal_resource_id
             AND version.source_fetch_id = item.source_fetch_id
             AND version.provider_id = $8
            JOIN %I.legal_resources AS resource
              ON resource.legal_resource_id = item.legal_resource_id
             AND resource.provider_id = version.provider_id
            JOIN %I.legal_source_fetches AS source_fetch
              ON source_fetch.source_fetch_id = item.source_fetch_id
             AND source_fetch.provider_id = version.provider_id
            JOIN %I.legal_source_providers AS provider
              ON provider.provider_id = version.provider_id
            JOIN %I.legal_resource_version_lineages AS lineage
              ON lineage.legal_resource_version_id =
                 version.legal_resource_version_id
             AND lineage.legal_resource_id = version.legal_resource_id
            JOIN %I.legal_provisions AS provision
              ON provision.legal_provision_id = item.legal_provision_id
             AND provision.legal_resource_version_id =
                 item.legal_resource_version_id
             AND provision.legal_resource_id = item.legal_resource_id
            WHERE snapshot.tenant_id = $1
              AND snapshot.project_id = $2
              AND snapshot.legal_evidence_snapshot_id = $3
              AND $5 = $13
              AND $6 = $14
              AND snapshot.created_at <= statement_timestamp()
              AND snapshot.as_of_date <= CURRENT_DATE
              AND snapshot.is_stale = FALSE
              AND snapshot.freshness_policy_version =
                  'legal-freshness-v1'
              AND snapshot.citation_verification_status IN (
                    'partial', 'verified'
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM %I.legal_evidence_snapshots AS newer
                    WHERE newer.tenant_id = snapshot.tenant_id
                      AND newer.project_id = snapshot.project_id
                      AND (
                            newer.snapshot_version >
                                snapshot.snapshot_version
                            OR newer.supersedes_snapshot_id =
                                snapshot.legal_evidence_snapshot_id
                      )
              )
              AND item.citation_verification_status = 'verified'
              AND item.verified_at IS NOT NULL
              AND item.verified_at <= statement_timestamp()
              AND item.verified_at + INTERVAL '7 days' >=
                  statement_timestamp()
              AND version.verification_status IN (
                    'source_verified', 'schema_verified'
              )
              AND version.verified_at IS NOT NULL
              AND version.verified_at <= statement_timestamp()
              AND source_fetch.fetch_status IN ('succeeded', 'not_modified')
              AND source_fetch.fetched_at <= statement_timestamp()
              AND provider.provider_status = 'active'
              AND lineage.validated_at <= statement_timestamp()
              AND provision.source_locator = item.exact_locator
              AND provision.provision_sha256 = $11
              AND provision.provision_sha256 =
                  encode(
                      extensions.digest(
                          convert_to(provision.body_text, 'UTF8'),
                          'sha256'
                      ),
                      'hex'
                  )
              AND (
                    version.effective_from IS NULL
                    OR version.effective_from <= snapshot.as_of_date
              )
              AND (
                    version.effective_to IS NULL
                    OR version.effective_to >= snapshot.as_of_date
              )
              AND (
                    version.valid_time_start IS NULL
                    OR version.valid_time_start <= snapshot.as_of_date
              )
              AND (
                    version.valid_time_end IS NULL
                    OR version.valid_time_end >= snapshot.as_of_date
              )
              AND (
                    $15 <> 'approved'
                    OR (
                        $16 IS NOT NULL
                        AND $17 = item.excerpt_sha256
                        AND position($16 in provision.body_text) > 0
                    )
              )
              AND NOT EXISTS (
                    SELECT 1
                    FROM %I.legal_resource_versions AS replacement
                    JOIN %I.legal_source_fetches AS replacement_fetch
                      ON replacement_fetch.source_fetch_id =
                         replacement.source_fetch_id
                     AND replacement_fetch.provider_id =
                         replacement.provider_id
                    JOIN %I.legal_source_providers AS replacement_provider
                      ON replacement_provider.provider_id =
                         replacement.provider_id
                    WHERE replacement.legal_resource_id =
                          version.legal_resource_id
                      AND replacement.version_sequence >
                          version.version_sequence
                      AND replacement.verification_status IN (
                            'source_verified', 'schema_verified'
                      )
                      AND replacement.verified_at IS NOT NULL
                      AND replacement.verified_at <= statement_timestamp()
                      AND replacement_fetch.fetch_status IN (
                            'succeeded', 'not_modified'
                      )
                      AND replacement_provider.provider_status = 'active'
                      AND (
                            replacement.supersedes_version_id =
                                version.legal_resource_version_id
                            OR (
                                (
                                    replacement.effective_from IS NULL
                                    OR replacement.effective_from <=
                                       snapshot.as_of_date
                                )
                                AND (
                                    replacement.effective_to IS NULL
                                    OR replacement.effective_to >=
                                       snapshot.as_of_date
                                )
                                AND (
                                    replacement.valid_time_start IS NULL
                                    OR replacement.valid_time_start <=
                                       snapshot.as_of_date
                                )
                                AND (
                                    replacement.valid_time_end IS NULL
                                    OR replacement.valid_time_end >=
                                       snapshot.as_of_date
                                )
                            )
                      )
              )
        )
        $sql$,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA,
        TG_TABLE_SCHEMA
    )
    INTO project_scope_valid
    USING
        NEW.tenant_id,
        NEW.project_id,
        NEW.legal_evidence_snapshot_id,
        NEW.legal_evidence_item_id,
        NEW.legal_resource_version_id,
        NEW.legal_provision_id,
        NEW.source_fetch_id,
        NEW.provider_id,
        NEW.evidence_exact_locator,
        NEW.bound_excerpt_sha256,
        NEW.bound_provision_sha256,
        source_resource_id,
        source_version_id,
        source_provision_id,
        NEW.review_status,
        NEW.evidence_excerpt,
        NEW.evidence_excerpt_sha256;

    IF TG_TABLE_NAME = 'project_legal_relation_review_versions'
       AND NEW.review_status = 'approved' THEN
        EXECUTE format(
            $sql$
            SELECT EXISTS (
                SELECT 1
                FROM %I.legal_evidence_snapshots AS snapshot
                JOIN %I.legal_evidence_snapshot_items AS item
                  ON item.tenant_id = snapshot.tenant_id
                 AND item.project_id = snapshot.project_id
                 AND item.legal_evidence_snapshot_id =
                     snapshot.legal_evidence_snapshot_id
                 AND item.legal_evidence_item_id = $4
                 AND item.legal_resource_id = $12
                 AND item.legal_resource_version_id = $5
                 AND item.legal_provision_id = $6
                 AND item.source_fetch_id = $7
                 AND item.exact_locator = $9
                 AND item.excerpt_sha256 = $10
                JOIN %I.legal_resource_versions AS version
                  ON version.legal_resource_version_id =
                     item.legal_resource_version_id
                 AND version.legal_resource_id = item.legal_resource_id
                 AND version.source_fetch_id = item.source_fetch_id
                 AND version.provider_id = $8
                JOIN %I.legal_resources AS resource
                  ON resource.legal_resource_id = item.legal_resource_id
                 AND resource.provider_id = version.provider_id
                JOIN %I.legal_source_fetches AS source_fetch
                  ON source_fetch.source_fetch_id = item.source_fetch_id
                 AND source_fetch.provider_id = version.provider_id
                JOIN %I.legal_source_providers AS provider
                  ON provider.provider_id = version.provider_id
                JOIN %I.legal_resource_version_lineages AS lineage
                  ON lineage.legal_resource_version_id =
                     version.legal_resource_version_id
                 AND lineage.legal_resource_id = version.legal_resource_id
                JOIN %I.legal_provisions AS provision
                  ON provision.legal_provision_id = item.legal_provision_id
                 AND provision.legal_resource_version_id =
                     item.legal_resource_version_id
                 AND provision.legal_resource_id = item.legal_resource_id
                WHERE snapshot.tenant_id = $1
                  AND snapshot.project_id = $2
                  AND snapshot.legal_evidence_snapshot_id = $3
                  AND snapshot.created_at <= statement_timestamp()
                  AND snapshot.as_of_date <= CURRENT_DATE
                  AND snapshot.is_stale = FALSE
                  AND snapshot.freshness_policy_version =
                      'legal-freshness-v1'
                  AND snapshot.citation_verification_status IN (
                      'partial', 'verified'
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM %I.legal_evidence_snapshots AS newer
                      WHERE newer.tenant_id = snapshot.tenant_id
                        AND newer.project_id = snapshot.project_id
                        AND (
                            newer.snapshot_version >
                                snapshot.snapshot_version
                            OR newer.supersedes_snapshot_id =
                                snapshot.legal_evidence_snapshot_id
                        )
                  )
                  AND item.citation_verification_status = 'verified'
                  AND item.verified_at IS NOT NULL
                  AND item.verified_at <= statement_timestamp()
                  AND item.verified_at + INTERVAL '7 days' >=
                      statement_timestamp()
                  AND version.verification_status IN (
                      'source_verified', 'schema_verified'
                  )
                  AND version.verified_at IS NOT NULL
                  AND version.verified_at <= statement_timestamp()
                  AND source_fetch.fetch_status IN (
                      'succeeded', 'not_modified'
                  )
                  AND source_fetch.fetched_at <= statement_timestamp()
                  AND provider.provider_status = 'active'
                  AND lineage.validated_at <= statement_timestamp()
                  AND provision.source_locator = item.exact_locator
                  AND provision.provision_sha256 = $11
                  AND provision.provision_sha256 =
                      encode(
                          extensions.digest(
                              convert_to(provision.body_text, 'UTF8'),
                              'sha256'
                          ),
                          'hex'
                      )
                  AND (
                      version.effective_from IS NULL
                      OR version.effective_from <= snapshot.as_of_date
                  )
                  AND (
                      version.effective_to IS NULL
                      OR version.effective_to >= snapshot.as_of_date
                  )
                  AND (
                      version.valid_time_start IS NULL
                      OR version.valid_time_start <= snapshot.as_of_date
                  )
                  AND (
                      version.valid_time_end IS NULL
                      OR version.valid_time_end >= snapshot.as_of_date
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM %I.legal_resource_versions AS replacement
                      JOIN %I.legal_source_fetches AS replacement_fetch
                        ON replacement_fetch.source_fetch_id =
                           replacement.source_fetch_id
                       AND replacement_fetch.provider_id =
                           replacement.provider_id
                      JOIN %I.legal_source_providers AS replacement_provider
                        ON replacement_provider.provider_id =
                           replacement.provider_id
                      WHERE replacement.legal_resource_id =
                            version.legal_resource_id
                        AND replacement.version_sequence >
                            version.version_sequence
                        AND replacement.verification_status IN (
                            'source_verified', 'schema_verified'
                        )
                        AND replacement.verified_at IS NOT NULL
                        AND replacement.verified_at <= statement_timestamp()
                        AND replacement_fetch.fetch_status IN (
                            'succeeded', 'not_modified'
                        )
                        AND replacement_provider.provider_status = 'active'
                        AND (
                            replacement.supersedes_version_id =
                                version.legal_resource_version_id
                            OR (
                                (
                                    replacement.effective_from IS NULL
                                    OR replacement.effective_from <=
                                       snapshot.as_of_date
                                )
                                AND (
                                    replacement.effective_to IS NULL
                                    OR replacement.effective_to >=
                                       snapshot.as_of_date
                                )
                                AND (
                                    replacement.valid_time_start IS NULL
                                    OR replacement.valid_time_start <=
                                       snapshot.as_of_date
                                )
                                AND (
                                    replacement.valid_time_end IS NULL
                                    OR replacement.valid_time_end >=
                                       snapshot.as_of_date
                                )
                            )
                        )
                  )
            )
            $sql$,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO object_scope_valid
        USING
            NEW.tenant_id,
            NEW.project_id,
            NEW.legal_evidence_snapshot_id,
            NEW.object_legal_evidence_item_id,
            NEW.object_legal_resource_version_id,
            NEW.object_legal_provision_id,
            NEW.object_source_fetch_id,
            NEW.object_provider_id,
            NEW.object_evidence_exact_locator,
            NEW.object_bound_excerpt_sha256,
            NEW.object_bound_provision_sha256,
            source_object_resource_id;
    END IF;

    IF source_maker IS NULL
       OR source_asserted_at IS NULL
       OR source_resource_id IS NULL
       OR source_version_id IS NULL
       OR source_provision_id IS NULL
       OR (
            TG_TABLE_NAME = 'project_legal_relation_review_versions'
            AND NEW.review_status = 'approved'
            AND (
                source_object_resource_id IS NULL
                OR NOT COALESCE(object_scope_valid, FALSE)
            )
       )
       OR source_asserted_at > NEW.reviewed_at
       OR NOT COALESCE(project_scope_valid, FALSE)
    THEN
        RAISE EXCEPTION
            'Citator review source is not an eligible project Corpus candidate'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_citator_review_source';
    END IF;

    IF NEW.maker_id IS DISTINCT FROM source_maker
       OR lower(btrim(NEW.reviewer_id)) = lower(btrim(source_maker))
    THEN
        RAISE EXCEPTION
            'Citator maker-checker separation failed'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_citator_review_maker_checker';
    END IF;

    IF latest_version_id IS NULL THEN
        IF NEW.review_version <> 1
           OR NEW.supersedes_review_version_id IS NOT NULL
        THEN
            RAISE EXCEPTION
                'first project Citator review must be version 1'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_project_citator_review_chain';
        END IF;
    ELSIF NEW.review_version <> latest_version + 1
       OR NEW.supersedes_review_version_id IS DISTINCT FROM latest_version_id
    THEN
        RAISE EXCEPTION
            'project Citator review must supersede the exact latest version'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_citator_review_chain';
    END IF;

    IF NEW.reviewer_role = 'external_counsel'
       AND NEW.legal_approval_delegation_id IS NULL
    THEN
        RAISE EXCEPTION
            'external counsel Citator review requires delegation'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_citator_review_delegation';
    END IF;

    IF NEW.legal_approval_delegation_id IS NOT NULL THEN
        EXECUTE format(
            $sql$
            SELECT EXISTS (
                SELECT 1
                FROM %I.legal_approval_delegations AS delegation
                WHERE delegation.legal_approval_delegation_id = $1
                  AND delegation.tenant_id = $2
                  AND delegation.project_id = $3
                  AND delegation.grantee_id = $4
                  AND delegation.grantee_role = $5
                  AND delegation.scope = 'citator_review'
                  AND delegation.status = 'active'
                  AND delegation.created_at <= $6
                  AND delegation.valid_from <= $6
                  AND delegation.valid_until >= $6
                  AND NOT EXISTS (
                        SELECT 1
                        FROM %I.legal_approval_delegations AS successor
                        WHERE successor.tenant_id = delegation.tenant_id
                          AND successor.project_id = delegation.project_id
                          AND successor.delegation_key =
                              delegation.delegation_key
                          AND successor.delegation_version >
                              delegation.delegation_version
                          AND successor.created_at <= $6
                  )
            )
            $sql$,
            TG_TABLE_SCHEMA,
            TG_TABLE_SCHEMA
        )
        INTO delegation_valid
        USING
            NEW.legal_approval_delegation_id,
            NEW.tenant_id,
            NEW.project_id,
            NEW.reviewer_id,
            NEW.reviewer_role,
            NEW.reviewed_at;
        IF NOT COALESCE(delegation_valid, FALSE) THEN
            RAISE EXCEPTION
                'Citator review delegation is not valid at reviewedAt'
                USING ERRCODE = '23514',
                      CONSTRAINT =
                          'ck_project_citator_review_delegation';
        END IF;
    END IF;

    RETURN NEW;
END;
$legal_project_citator_review_version$;
REVOKE ALL ON FUNCTION legal_assert_project_citator_review_version()
    FROM PUBLIC;

CREATE TRIGGER trg_project_legal_citation_review_version
    BEFORE INSERT ON project_legal_citation_review_versions
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_project_citator_review_version();
CREATE TRIGGER trg_project_legal_relation_review_version
    BEFORE INSERT ON project_legal_relation_review_versions
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_project_citator_review_version();

CREATE OR REPLACE FUNCTION legal_assert_project_citator_review_event()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_project_citator_review_event$
DECLARE
    version_source_id TEXT;
    version_snapshot_id TEXT;
    version_evidence_item_id TEXT;
    version_resource_version_id TEXT;
    version_provision_id TEXT;
    version_source_fetch_id TEXT;
    version_provider_id TEXT;
    version_exact_locator TEXT;
    version_bound_excerpt_sha256 TEXT;
    version_bound_provision_sha256 TEXT;
    version_object_evidence_item_id TEXT;
    version_object_resource_version_id TEXT;
    version_object_provision_id TEXT;
    version_object_source_fetch_id TEXT;
    version_object_provider_id TEXT;
    version_object_exact_locator TEXT;
    version_object_bound_excerpt_sha256 TEXT;
    version_object_bound_provision_sha256 TEXT;
    version_sequence INTEGER;
    version_status TEXT;
    version_reviewer_id TEXT;
    version_reviewer_role TEXT;
    version_reviewed_at TIMESTAMPTZ;
    version_reason TEXT;
    version_excerpt_hash TEXT;
    version_delegation_id TEXT;
    version_predecessor_id TEXT;
    version_metadata JSONB;
    predecessor_event_id TEXT;
BEGIN
    IF NEW.tenant_id IS DISTINCT FROM NULLIF(
        current_setting('realytics.tenant_id', true),
        ''
    ) THEN
        RAISE EXCEPTION
            'Citator review event tenant does not match transaction tenant'
            USING ERRCODE = '42501',
                  CONSTRAINT = 'ck_project_citator_review_event_tenant';
    END IF;
    IF NEW.reviewed_at < statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.reviewed_at > statement_timestamp() + INTERVAL '5 minutes'
       OR NEW.recorded_at <
            statement_timestamp() - INTERVAL '5 minutes'
       OR NEW.recorded_at >
            statement_timestamp() + INTERVAL '5 minutes'
    THEN
        RAISE EXCEPTION
            'Citator review event timestamps must be DB time +/- 5 minutes'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_citator_review_event_time';
    END IF;

    IF TG_TABLE_NAME = 'project_legal_citation_review_events' THEN
        EXECUTE format(
            $sql$
            SELECT
                review.legal_citation_id,
                review.legal_evidence_snapshot_id,
                review.legal_evidence_item_id,
                review.legal_resource_version_id,
                review.legal_provision_id,
                review.source_fetch_id,
                review.provider_id,
                review.evidence_exact_locator,
                review.bound_excerpt_sha256,
                review.bound_provision_sha256,
                review.review_version,
                review.review_status,
                review.reviewer_id,
                review.reviewer_role,
                review.reviewed_at,
                review.reason,
                review.evidence_excerpt_sha256,
                review.legal_approval_delegation_id,
                review.supersedes_review_version_id,
                review.metadata_json
            FROM %I.project_legal_citation_review_versions AS review
            WHERE review.project_legal_citation_review_version_id = $1
              AND review.tenant_id = $2
              AND review.project_id = $3
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO
            version_source_id,
            version_snapshot_id,
            version_evidence_item_id,
            version_resource_version_id,
            version_provision_id,
            version_source_fetch_id,
            version_provider_id,
            version_exact_locator,
            version_bound_excerpt_sha256,
            version_bound_provision_sha256,
            version_sequence,
            version_status,
            version_reviewer_id,
            version_reviewer_role,
            version_reviewed_at,
            version_reason,
            version_excerpt_hash,
            version_delegation_id,
            version_predecessor_id,
            version_metadata
        USING
            NEW.project_legal_citation_review_version_id,
            NEW.tenant_id,
            NEW.project_id;
        IF version_predecessor_id IS NOT NULL THEN
            EXECUTE format(
                $sql$
                SELECT event.project_legal_citation_review_event_id
                FROM %I.project_legal_citation_review_events AS event
                WHERE event.project_legal_citation_review_version_id = $1
                  AND event.tenant_id = $2
                  AND event.project_id = $3
                $sql$,
                TG_TABLE_SCHEMA
            )
            INTO predecessor_event_id
            USING
                version_predecessor_id,
                NEW.tenant_id,
                NEW.project_id;
        END IF;
        IF NEW.legal_citation_id IS DISTINCT FROM version_source_id THEN
            version_source_id := NULL;
        END IF;
    ELSIF TG_TABLE_NAME = 'project_legal_relation_review_events' THEN
        EXECUTE format(
            $sql$
            SELECT
                review.legal_relation_assertion_id,
                review.legal_evidence_snapshot_id,
                review.legal_evidence_item_id,
                review.legal_resource_version_id,
                review.legal_provision_id,
                review.source_fetch_id,
                review.provider_id,
                review.evidence_exact_locator,
                review.bound_excerpt_sha256,
                review.bound_provision_sha256,
                review.object_legal_evidence_item_id,
                review.object_legal_resource_version_id,
                review.object_legal_provision_id,
                review.object_source_fetch_id,
                review.object_provider_id,
                review.object_evidence_exact_locator,
                review.object_bound_excerpt_sha256,
                review.object_bound_provision_sha256,
                review.review_version,
                review.review_status,
                review.reviewer_id,
                review.reviewer_role,
                review.reviewed_at,
                review.reason,
                review.evidence_excerpt_sha256,
                review.legal_approval_delegation_id,
                review.supersedes_review_version_id,
                review.metadata_json
            FROM %I.project_legal_relation_review_versions AS review
            WHERE review.project_legal_relation_review_version_id = $1
              AND review.tenant_id = $2
              AND review.project_id = $3
            $sql$,
            TG_TABLE_SCHEMA
        )
        INTO
            version_source_id,
            version_snapshot_id,
            version_evidence_item_id,
            version_resource_version_id,
            version_provision_id,
            version_source_fetch_id,
            version_provider_id,
            version_exact_locator,
            version_bound_excerpt_sha256,
            version_bound_provision_sha256,
            version_object_evidence_item_id,
            version_object_resource_version_id,
            version_object_provision_id,
            version_object_source_fetch_id,
            version_object_provider_id,
            version_object_exact_locator,
            version_object_bound_excerpt_sha256,
            version_object_bound_provision_sha256,
            version_sequence,
            version_status,
            version_reviewer_id,
            version_reviewer_role,
            version_reviewed_at,
            version_reason,
            version_excerpt_hash,
            version_delegation_id,
            version_predecessor_id,
            version_metadata
        USING
            NEW.project_legal_relation_review_version_id,
            NEW.tenant_id,
            NEW.project_id;
        IF version_predecessor_id IS NOT NULL THEN
            EXECUTE format(
                $sql$
                SELECT event.project_legal_relation_review_event_id
                FROM %I.project_legal_relation_review_events AS event
                WHERE event.project_legal_relation_review_version_id = $1
                  AND event.tenant_id = $2
                  AND event.project_id = $3
                $sql$,
                TG_TABLE_SCHEMA
            )
            INTO predecessor_event_id
            USING
                version_predecessor_id,
                NEW.tenant_id,
                NEW.project_id;
        END IF;
        IF NEW.legal_relation_assertion_id
            IS DISTINCT FROM version_source_id
        THEN
            version_source_id := NULL;
        END IF;
    ELSE
        RAISE EXCEPTION 'unsupported project Citator event table';
    END IF;

    IF version_source_id IS NULL
       OR NEW.legal_evidence_snapshot_id
            IS DISTINCT FROM version_snapshot_id
       OR NEW.legal_evidence_item_id
            IS DISTINCT FROM version_evidence_item_id
       OR NEW.legal_resource_version_id
            IS DISTINCT FROM version_resource_version_id
       OR NEW.legal_provision_id
            IS DISTINCT FROM version_provision_id
       OR NEW.source_fetch_id IS DISTINCT FROM version_source_fetch_id
       OR NEW.provider_id IS DISTINCT FROM version_provider_id
       OR NEW.evidence_exact_locator
            IS DISTINCT FROM version_exact_locator
       OR NEW.bound_excerpt_sha256
            IS DISTINCT FROM version_bound_excerpt_sha256
       OR NEW.bound_provision_sha256
            IS DISTINCT FROM version_bound_provision_sha256
       OR (
            TG_TABLE_NAME = 'project_legal_relation_review_events'
            AND (
                NEW.object_legal_evidence_item_id
                    IS DISTINCT FROM version_object_evidence_item_id
                OR NEW.object_legal_resource_version_id
                    IS DISTINCT FROM version_object_resource_version_id
                OR NEW.object_legal_provision_id
                    IS DISTINCT FROM version_object_provision_id
                OR NEW.object_source_fetch_id
                    IS DISTINCT FROM version_object_source_fetch_id
                OR NEW.object_provider_id
                    IS DISTINCT FROM version_object_provider_id
                OR NEW.object_evidence_exact_locator
                    IS DISTINCT FROM version_object_exact_locator
                OR NEW.object_bound_excerpt_sha256
                    IS DISTINCT FROM
                        version_object_bound_excerpt_sha256
                OR NEW.object_bound_provision_sha256
                    IS DISTINCT FROM
                        version_object_bound_provision_sha256
            )
       )
       OR NEW.event_sequence IS DISTINCT FROM version_sequence
       OR NEW.review_status IS DISTINCT FROM version_status
       OR NEW.reviewer_id IS DISTINCT FROM version_reviewer_id
       OR NEW.reviewer_role IS DISTINCT FROM version_reviewer_role
       OR NEW.reviewed_at IS DISTINCT FROM version_reviewed_at
       OR NEW.reason IS DISTINCT FROM version_reason
       OR NEW.evidence_excerpt_sha256
            IS DISTINCT FROM version_excerpt_hash
       OR NEW.legal_approval_delegation_id
            IS DISTINCT FROM version_delegation_id
       OR NEW.supersedes_review_event_id
            IS DISTINCT FROM predecessor_event_id
       OR NEW.metadata_json IS DISTINCT FROM version_metadata
    THEN
        RAISE EXCEPTION
            'Citator review event does not exactly mirror its review version'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_citator_review_event_exact';
    END IF;
    RETURN NEW;
END;
$legal_project_citator_review_event$;
REVOKE ALL ON FUNCTION legal_assert_project_citator_review_event()
    FROM PUBLIC;

CREATE TRIGGER trg_project_legal_citation_review_event
    BEFORE INSERT ON project_legal_citation_review_events
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_project_citator_review_event();
CREATE TRIGGER trg_project_legal_relation_review_event
    BEFORE INSERT ON project_legal_relation_review_events
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_project_citator_review_event();

CREATE OR REPLACE FUNCTION legal_assert_project_citator_review_event_pair()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_project_citator_review_event_pair$
DECLARE
    event_exists BOOLEAN := FALSE;
BEGIN
    IF TG_TABLE_NAME = 'project_legal_citation_review_versions' THEN
        EXECUTE format(
            'SELECT EXISTS ('
            'SELECT 1 '
            'FROM %I.project_legal_citation_review_events AS event '
            'WHERE event.project_legal_citation_review_version_id = $1 '
            'AND event.tenant_id = $2 '
            'AND event.project_id = $3'
            ')',
            TG_TABLE_SCHEMA
        )
        INTO event_exists
        USING
            NEW.project_legal_citation_review_version_id,
            NEW.tenant_id,
            NEW.project_id;
    ELSIF TG_TABLE_NAME = 'project_legal_relation_review_versions' THEN
        EXECUTE format(
            'SELECT EXISTS ('
            'SELECT 1 '
            'FROM %I.project_legal_relation_review_events AS event '
            'WHERE event.project_legal_relation_review_version_id = $1 '
            'AND event.tenant_id = $2 '
            'AND event.project_id = $3'
            ')',
            TG_TABLE_SCHEMA
        )
        INTO event_exists
        USING
            NEW.project_legal_relation_review_version_id,
            NEW.tenant_id,
            NEW.project_id;
    END IF;
    IF NOT COALESCE(event_exists, FALSE) THEN
        RAISE EXCEPTION
            'project Citator review version requires one exact review event'
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_citator_review_event_pair';
    END IF;
    RETURN NEW;
END;
$legal_project_citator_review_event_pair$;
REVOKE ALL ON FUNCTION legal_assert_project_citator_review_event_pair()
    FROM PUBLIC;

CREATE CONSTRAINT TRIGGER ctrg_project_legal_citation_review_event_pair
    AFTER INSERT ON project_legal_citation_review_versions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_project_citator_review_event_pair();
CREATE CONSTRAINT TRIGGER ctrg_project_legal_relation_review_event_pair
    AFTER INSERT ON project_legal_relation_review_versions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_project_citator_review_event_pair();

CREATE TRIGGER trg_project_legal_citation_review_versions_append_only
    BEFORE UPDATE OR DELETE ON project_legal_citation_review_versions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_project_legal_relation_review_versions_append_only
    BEFORE UPDATE OR DELETE ON project_legal_relation_review_versions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_project_legal_citation_review_events_append_only
    BEFORE UPDATE OR DELETE ON project_legal_citation_review_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();
CREATE TRIGGER trg_project_legal_relation_review_events_append_only
    BEFORE UPDATE OR DELETE ON project_legal_relation_review_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

ALTER TABLE project_legal_citation_review_versions
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_legal_citation_review_versions
    FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON project_legal_citation_review_versions
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE project_legal_relation_review_versions
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_legal_relation_review_versions
    FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON project_legal_relation_review_versions
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE project_legal_citation_review_events
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_legal_citation_review_events
    FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON project_legal_citation_review_events
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE project_legal_relation_review_events
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_legal_relation_review_events
    FORCE ROW LEVEL SECURITY;
CREATE POLICY legal_tenant_isolation
    ON project_legal_relation_review_events
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

REVOKE ALL ON TABLE
    project_legal_citation_review_versions,
    project_legal_relation_review_versions,
    project_legal_citation_review_events,
    project_legal_relation_review_events
FROM PUBLIC;

-- Project review principals have no write path to the institution-wide Corpus.
-- This explicit revoke complements the dedicated-role manifest and prevents a
-- future PUBLIC grant from reviving the legacy global-successor path.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE
    legal_citations,
    legal_relation_assertions,
    legal_citation_review_events,
    legal_relation_review_events
FROM PUBLIC;
