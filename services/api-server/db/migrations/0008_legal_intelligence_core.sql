-- REALYTICS Legal Intelligence Core.
--
-- Authority and transport are deliberately separate:
--   * official providers remain the source authorities;
--   * local PostgreSQL is a verified, reproducible read model;
--   * MCP is an optional transport and never becomes source authority.
--
-- Official/public corpus tables are global and append-only. Project analysis,
-- findings, evidence packages, and human decisions are tenant scoped and use
-- fail-closed PostgreSQL RLS. Raw provider responses remain immutable objects
-- in object storage; this schema stores only sanitized locators and hashes.

CREATE SCHEMA IF NOT EXISTS extensions;
REVOKE CREATE ON SCHEMA extensions FROM PUBLIC;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;

DO $legal_pg_trgm_schema$
DECLARE
    installed_schema TEXT;
BEGIN
    SELECT namespace.nspname
      INTO installed_schema
      FROM pg_extension AS extension
      JOIN pg_namespace AS namespace
        ON namespace.oid = extension.extnamespace
     WHERE extension.extname = 'pg_trgm';

    IF installed_schema IS DISTINCT FROM 'extensions' THEN
        RAISE EXCEPTION
            'pg_trgm must be installed in the extensions schema (found: %)',
            COALESCE(installed_schema, '<not installed>');
    END IF;
END;
$legal_pg_trgm_schema$;

-- pgvector is intentionally not installed here. Semantic artifacts record
-- model, dimensionality, and immutable content identity so a later, separately
-- qualified migration can add a vector column/index where pgvector is present.

-- ---------------------------------------------------------------------------
-- Global source authority and immutable synchronization ledger
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legal_source_providers (
    provider_id                    TEXT PRIMARY KEY,
    provider_key                   TEXT NOT NULL,
    provider_revision              INTEGER NOT NULL,
    source_authority               TEXT NOT NULL,
    authority_tier                 TEXT NOT NULL,
    provider_kind                  TEXT NOT NULL,
    canonical_base_url             TEXT,
    terms_version                  TEXT,
    adapter_version                TEXT NOT NULL,
    provider_status                TEXT NOT NULL,
    supersedes_provider_id         TEXT,
    configured_by                  TEXT NOT NULL,
    configured_at                  TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (provider_key, provider_revision),
    UNIQUE (provider_id, provider_key),
    CONSTRAINT fk_legal_provider_supersedes_same_key
        FOREIGN KEY (supersedes_provider_id, provider_key)
        REFERENCES legal_source_providers(provider_id, provider_key)
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_provider_identity
        CHECK (
            btrim(provider_id) <> ''
            AND btrim(provider_key) <> ''
            AND btrim(source_authority) <> ''
            AND btrim(adapter_version) <> ''
        ),
    CONSTRAINT ck_legal_provider_revision
        CHECK (provider_revision >= 1),
    CONSTRAINT ck_legal_provider_authority_tier
        CHECK (authority_tier IN (
            'official_primary',
            'official_secondary'
        )),
    CONSTRAINT ck_legal_provider_kind
        CHECK (provider_kind IN (
            'official_api',
            'official_bulk',
            'court_api',
            'regulator_api',
            'verified_manual_import'
        )),
    CONSTRAINT ck_legal_provider_status
        CHECK (provider_status IN ('active', 'superseded', 'retired')),
    CONSTRAINT ck_legal_provider_not_self_superseding
        CHECK (
            supersedes_provider_id IS NULL
            OR supersedes_provider_id <> provider_id
        ),
    CONSTRAINT ck_legal_provider_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_source_providers_current
    ON legal_source_providers(provider_key, provider_revision DESC);

CREATE TABLE IF NOT EXISTS legal_sync_runs (
    sync_run_id                    TEXT PRIMARY KEY,
    provider_id                   TEXT NOT NULL
        REFERENCES legal_source_providers(provider_id) ON DELETE RESTRICT,
    sync_kind                     TEXT NOT NULL,
    requested_scope               TEXT NOT NULL,
    requested_scope_hash          TEXT NOT NULL,
    checkpoint_before             TEXT,
    page_hash                     TEXT NOT NULL,
    page_record_count             INTEGER NOT NULL,
    idempotency_key               TEXT NOT NULL,
    input_hash                    TEXT NOT NULL,
    requested_by                  TEXT NOT NULL,
    requested_at                  TIMESTAMPTZ NOT NULL,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (sync_run_id, provider_id),
    UNIQUE (
        sync_run_id, provider_id, requested_scope_hash, requested_scope
    ),
    UNIQUE (provider_id, idempotency_key),
    CONSTRAINT ck_legal_sync_kind
        CHECK (sync_kind IN (
            'initial',
            'incremental',
            'backfill',
            'gap_fill',
            'verification'
        )),
    CONSTRAINT ck_legal_sync_input_hash
        CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_sync_scope_hash
        CHECK (requested_scope_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_sync_page_hash
        CHECK (page_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_sync_page_record_count
        CHECK (page_record_count >= 1),
    CONSTRAINT ck_legal_sync_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_sync_runs_provider_requested
    ON legal_sync_runs(provider_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_legal_sync_runs_scope_requested
    ON legal_sync_runs(
        provider_id, requested_scope_hash, requested_at DESC
    );

CREATE TABLE IF NOT EXISTS legal_sync_run_events (
    sync_event_id                  TEXT PRIMARY KEY,
    sync_run_id                    TEXT NOT NULL,
    provider_id                   TEXT NOT NULL,
    sequence_number               INTEGER NOT NULL,
    from_status                   TEXT,
    to_status                     TEXT NOT NULL,
    checkpoint_after              TEXT,
    records_seen                  BIGINT,
    records_written               BIGINT,
    error_code                    TEXT,
    occurred_at                   TIMESTAMPTZ NOT NULL,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (sync_run_id, sequence_number),
    CONSTRAINT fk_legal_sync_event_run
        FOREIGN KEY (sync_run_id, provider_id)
        REFERENCES legal_sync_runs(sync_run_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_sync_event_sequence
        CHECK (sequence_number >= 1),
    CONSTRAINT ck_legal_sync_event_from_status
        CHECK (
            from_status IS NULL
            OR from_status IN (
                'pending', 'running', 'completed', 'failed', 'cancelled'
            )
        ),
    CONSTRAINT ck_legal_sync_event_to_status
        CHECK (to_status IN (
            'pending', 'running', 'completed', 'failed', 'cancelled'
        )),
    CONSTRAINT ck_legal_sync_event_counts
        CHECK (
            (records_seen IS NULL OR records_seen >= 0)
            AND (records_written IS NULL OR records_written >= 0)
        ),
    CONSTRAINT ck_legal_sync_event_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_sync_events_latest
    ON legal_sync_run_events(sync_run_id, sequence_number DESC);

CREATE TABLE IF NOT EXISTS legal_sync_scope_lease_events (
    lease_event_id                 TEXT PRIMARY KEY,
    provider_id                   TEXT NOT NULL
        REFERENCES legal_source_providers(provider_id) ON DELETE RESTRICT,
    requested_scope               TEXT NOT NULL,
    requested_scope_hash          TEXT NOT NULL,
    event_sequence                INTEGER NOT NULL,
    lease_id                      TEXT NOT NULL,
    fencing_token                 BIGINT NOT NULL,
    sync_run_id                   TEXT NOT NULL,
    lease_owner_id                TEXT NOT NULL,
    event_type                    TEXT NOT NULL,
    occurred_at                   TIMESTAMPTZ NOT NULL,
    expires_at                    TIMESTAMPTZ NOT NULL,
    prior_lease_event_id          TEXT,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        provider_id, requested_scope_hash, event_sequence
    ),
    UNIQUE (
        lease_event_id, provider_id, requested_scope_hash
    ),
    CONSTRAINT fk_legal_sync_lease_run
        FOREIGN KEY (
            sync_run_id, provider_id, requested_scope_hash, requested_scope
        )
        REFERENCES legal_sync_runs(
            sync_run_id, provider_id, requested_scope_hash, requested_scope
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_sync_lease_prior_event
        FOREIGN KEY (
            prior_lease_event_id, provider_id, requested_scope_hash
        )
        REFERENCES legal_sync_scope_lease_events(
            lease_event_id, provider_id, requested_scope_hash
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_sync_lease_scope_hash
        CHECK (requested_scope_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_sync_lease_sequence
        CHECK (event_sequence >= 1),
    CONSTRAINT ck_legal_sync_lease_fencing_token
        CHECK (fencing_token >= 1),
    CONSTRAINT ck_legal_sync_lease_event_type
        CHECK (event_type IN ('acquired', 'renewed', 'released')),
    CONSTRAINT ck_legal_sync_lease_time
        CHECK (
            (
                event_type IN ('acquired', 'renewed')
                AND expires_at > occurred_at
            )
            OR (
                event_type = 'released'
                AND expires_at = occurred_at
            )
        ),
    CONSTRAINT ck_legal_sync_lease_identity
        CHECK (
            btrim(requested_scope) <> ''
            AND btrim(lease_id) <> ''
            AND btrim(lease_owner_id) <> ''
        ),
    CONSTRAINT ck_legal_sync_lease_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_sync_scope_lease_latest
    ON legal_sync_scope_lease_events(
        provider_id, requested_scope_hash, event_sequence DESC
    );
CREATE INDEX IF NOT EXISTS idx_legal_sync_scope_lease_run
    ON legal_sync_scope_lease_events(
        sync_run_id, event_sequence DESC
    );

CREATE TABLE IF NOT EXISTS legal_sync_checkpoint_events (
    checkpoint_event_id            TEXT PRIMARY KEY,
    provider_id                   TEXT NOT NULL
        REFERENCES legal_source_providers(provider_id) ON DELETE RESTRICT,
    requested_scope               TEXT NOT NULL,
    requested_scope_hash          TEXT NOT NULL,
    checkpoint_sequence           BIGINT NOT NULL,
    sync_run_id                   TEXT NOT NULL,
    lease_id                      TEXT NOT NULL,
    fencing_token                 BIGINT NOT NULL,
    checkpoint_before             TEXT,
    checkpoint_after              TEXT,
    page_hash                     TEXT NOT NULL,
    page_record_count             INTEGER NOT NULL,
    occurred_at                   TIMESTAMPTZ NOT NULL,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        provider_id, requested_scope_hash, checkpoint_sequence
    ),
    UNIQUE (sync_run_id, provider_id),
    CONSTRAINT fk_legal_sync_checkpoint_run
        FOREIGN KEY (
            sync_run_id, provider_id, requested_scope_hash, requested_scope
        )
        REFERENCES legal_sync_runs(
            sync_run_id, provider_id, requested_scope_hash, requested_scope
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_sync_checkpoint_scope_hash
        CHECK (requested_scope_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_sync_checkpoint_sequence
        CHECK (checkpoint_sequence >= 1),
    CONSTRAINT ck_legal_sync_checkpoint_fencing_token
        CHECK (fencing_token >= 1),
    CONSTRAINT ck_legal_sync_checkpoint_page_hash
        CHECK (page_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_sync_checkpoint_record_count
        CHECK (page_record_count >= 1),
    CONSTRAINT ck_legal_sync_checkpoint_identity
        CHECK (
            btrim(requested_scope) <> ''
            AND btrim(lease_id) <> ''
        ),
    CONSTRAINT ck_legal_sync_checkpoint_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object'),
    CONSTRAINT ck_legal_sync_checkpoint_exact_manifest
        CHECK (
            btrim(
                COALESCE(metadata_json ->> 'sourceFetchId', '')
            ) <> ''
            AND jsonb_typeof(
                metadata_json -> 'legalResourceVersionIds'
            ) = 'array'
            AND jsonb_array_length(
                metadata_json -> 'legalResourceVersionIds'
            ) = page_record_count
        )
);
CREATE INDEX IF NOT EXISTS idx_legal_sync_checkpoint_latest
    ON legal_sync_checkpoint_events(
        provider_id, requested_scope_hash, checkpoint_sequence DESC
    );

CREATE TABLE IF NOT EXISTS legal_corpus_scope_completion_attestations (
    completion_attestation_id      TEXT PRIMARY KEY,
    provider_id                   TEXT NOT NULL
        REFERENCES legal_source_providers(provider_id) ON DELETE RESTRICT,
    requested_scope               TEXT NOT NULL,
    requested_scope_hash          TEXT NOT NULL,
    first_checkpoint_sequence     BIGINT NOT NULL,
    last_checkpoint_sequence      BIGINT NOT NULL,
    terminal_checkpoint           TEXT NOT NULL,
    page_count                    INTEGER NOT NULL,
    record_count                  BIGINT NOT NULL,
    resource_count                BIGINT NOT NULL,
    page_hashes_json              JSONB NOT NULL,
    manifest_sha256               TEXT NOT NULL,
    started_at                    TIMESTAMPTZ NOT NULL,
    completed_at                  TIMESTAMPTZ NOT NULL,
    attested_by                   TEXT NOT NULL,
    verification_status           TEXT NOT NULL DEFAULT 'verified',
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        completion_attestation_id,
        provider_id,
        requested_scope_hash
    ),
    UNIQUE (
        provider_id,
        requested_scope_hash,
        last_checkpoint_sequence
    ),
    CONSTRAINT fk_legal_completion_first_checkpoint
        FOREIGN KEY (
            provider_id,
            requested_scope_hash,
            first_checkpoint_sequence
        )
        REFERENCES legal_sync_checkpoint_events(
            provider_id,
            requested_scope_hash,
            checkpoint_sequence
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_completion_last_checkpoint
        FOREIGN KEY (
            provider_id,
            requested_scope_hash,
            last_checkpoint_sequence
        )
        REFERENCES legal_sync_checkpoint_events(
            provider_id,
            requested_scope_hash,
            checkpoint_sequence
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_completion_scope
        CHECK (
            btrim(requested_scope) <> ''
            AND requested_scope_hash ~ '^[0-9a-f]{64}$'
            AND btrim(terminal_checkpoint) <> ''
            AND btrim(attested_by) <> ''
        ),
    CONSTRAINT ck_legal_completion_sequence
        CHECK (
            first_checkpoint_sequence >= 1
            AND last_checkpoint_sequence >= first_checkpoint_sequence
        ),
    CONSTRAINT ck_legal_completion_counts
        CHECK (
            page_count >= 1
            AND record_count >= 1
            AND resource_count >= 1
            AND resource_count <= record_count
        ),
    CONSTRAINT ck_legal_completion_page_hashes
        CHECK (
            jsonb_typeof(page_hashes_json) = 'array'
            AND jsonb_array_length(page_hashes_json) = page_count
        ),
    CONSTRAINT ck_legal_completion_manifest
        CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_completion_time
        CHECK (completed_at >= started_at),
    CONSTRAINT ck_legal_completion_status
        CHECK (verification_status = 'verified'),
    CONSTRAINT ck_legal_completion_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_corpus_completion_latest
    ON legal_corpus_scope_completion_attestations(
        provider_id, requested_scope_hash, last_checkpoint_sequence DESC
    );

CREATE TABLE IF NOT EXISTS legal_source_fetches (
    source_fetch_id                TEXT PRIMARY KEY,
    provider_id                   TEXT NOT NULL,
    sync_run_id                   TEXT,
    transport                     TEXT NOT NULL,
    endpoint_key                  TEXT NOT NULL,
    sanitized_source_locator      TEXT NOT NULL,
    request_fingerprint           TEXT NOT NULL,
    response_status               INTEGER,
    fetch_status                  TEXT NOT NULL,
    raw_object_key                TEXT,
    raw_object_version            TEXT,
    raw_sha256                    TEXT,
    raw_size_bytes                BIGINT,
    response_content_type         TEXT,
    provider_etag                 TEXT,
    provider_last_modified        TEXT,
    fetched_at                    TIMESTAMPTZ NOT NULL,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (source_fetch_id, provider_id),
    UNIQUE (provider_id, request_fingerprint, fetched_at),
    CONSTRAINT fk_legal_source_fetch_provider
        FOREIGN KEY (provider_id)
        REFERENCES legal_source_providers(provider_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_source_fetch_sync_run
        FOREIGN KEY (sync_run_id, provider_id)
        REFERENCES legal_sync_runs(sync_run_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_source_fetch_transport
        CHECK (transport IN (
            'direct_api',
            'bulk_download',
            'self_hosted_mcp',
            'remote_mcp',
            'manual_verified_import'
        )),
    CONSTRAINT ck_legal_source_fetch_status
        CHECK (fetch_status IN (
            'succeeded',
            'not_modified',
            'partial',
            'failed',
            'quarantined'
        )),
    CONSTRAINT ck_legal_source_fetch_response_status
        CHECK (
            response_status IS NULL
            OR response_status BETWEEN 100 AND 599
        ),
    CONSTRAINT ck_legal_source_fetch_status_response_semantics
        CHECK (
            (
                fetch_status <> 'succeeded'
                OR COALESCE(
                    response_status BETWEEN 200 AND 299,
                    FALSE
                )
            )
            AND (
                fetch_status <> 'not_modified'
                OR (
                    COALESCE(response_status = 304, FALSE)
                    AND (
                        btrim(COALESCE(provider_etag, '')) <> ''
                        OR btrim(
                            COALESCE(provider_last_modified, '')
                        ) <> ''
                    )
                )
            )
        ),
    CONSTRAINT ck_legal_source_fetch_request_hash
        CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_source_fetch_raw_hash
        CHECK (
            raw_sha256 IS NULL
            OR raw_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_source_fetch_raw_size
        CHECK (raw_size_bytes IS NULL OR raw_size_bytes >= 0),
    CONSTRAINT ck_legal_source_fetch_raw_object_tuple
        CHECK (
            (
                raw_object_key IS NULL
                AND raw_object_version IS NULL
                AND raw_sha256 IS NULL
                AND raw_size_bytes IS NULL
            )
            OR (
                btrim(COALESCE(raw_object_key, '')) <> ''
                AND btrim(COALESCE(raw_object_version, '')) <> ''
                AND raw_sha256 IS NOT NULL
                AND raw_size_bytes IS NOT NULL
            )
        ),
    CONSTRAINT ck_legal_source_fetch_success_has_immutable_object
        CHECK (
            fetch_status NOT IN (
                'succeeded',
                'not_modified',
                'partial'
            )
            OR (
                btrim(COALESCE(raw_object_key, '')) <> ''
                AND btrim(COALESCE(raw_object_version, '')) <> ''
                AND raw_sha256 IS NOT NULL
                AND raw_size_bytes IS NOT NULL
            )
        ),
    CONSTRAINT ck_legal_source_fetch_sanitized_locator
        CHECK (
            btrim(sanitized_source_locator) <> ''
            AND sanitized_source_locator !~* '([?&](oc|key|token|secret|password)=)'
        ),
    CONSTRAINT ck_legal_source_fetch_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_source_fetches_provider_time
    ON legal_source_fetches(provider_id, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_legal_source_fetches_sync
    ON legal_source_fetches(sync_run_id, fetched_at)
    WHERE sync_run_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS legal_raw_object_reconciliation_runs (
    reconciliation_run_id         TEXT PRIMARY KEY,
    object_prefix                TEXT NOT NULL,
    start_cursor                 TEXT,
    next_cursor                  TEXT,
    started_at                   TIMESTAMPTZ NOT NULL,
    completed_at                 TIMESTAMPTZ NOT NULL,
    reconciliation_status       TEXT NOT NULL,
    pages_scanned               INTEGER NOT NULL,
    objects_scanned             BIGINT NOT NULL,
    inventory_manifest_sha256   TEXT NOT NULL,
    status_counts_json          JSONB NOT NULL,
    metadata_json               JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT ck_legal_raw_reconciliation_prefix
        CHECK (
            object_prefix LIKE 'legal-corpus/raw/%'
            AND object_prefix !~ '(^|/)\.\.(/|$)'
        ),
    CONSTRAINT ck_legal_raw_reconciliation_status
        CHECK (
            reconciliation_status IN ('completed', 'partial')
            AND (
                reconciliation_status <> 'completed'
                OR next_cursor IS NULL
            )
            AND (
                reconciliation_status <> 'partial'
                OR btrim(COALESCE(next_cursor, '')) <> ''
            )
        ),
    CONSTRAINT ck_legal_raw_reconciliation_counts
        CHECK (pages_scanned >= 1 AND objects_scanned >= 0),
    CONSTRAINT ck_legal_raw_reconciliation_manifest
        CHECK (inventory_manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_raw_reconciliation_time
        CHECK (completed_at >= started_at),
    CONSTRAINT ck_legal_raw_reconciliation_status_counts
        CHECK (jsonb_typeof(status_counts_json) = 'object'),
    CONSTRAINT ck_legal_raw_reconciliation_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);

CREATE TABLE IF NOT EXISTS legal_raw_object_reconciliation_items (
    reconciliation_item_id        TEXT PRIMARY KEY,
    reconciliation_run_id         TEXT NOT NULL
        REFERENCES legal_raw_object_reconciliation_runs(
            reconciliation_run_id
        )
        ON DELETE RESTRICT,
    sequence_number              INTEGER NOT NULL,
    raw_object_key              TEXT NOT NULL,
    raw_object_version          TEXT NOT NULL,
    raw_sha256                  TEXT NOT NULL,
    raw_size_bytes              BIGINT NOT NULL,
    object_created_at           TIMESTAMPTZ NOT NULL,
    observation_status          TEXT NOT NULL,
    source_fetch_id             TEXT
        REFERENCES legal_source_fetches(source_fetch_id)
        ON DELETE RESTRICT,
    sync_run_id                 TEXT,
    sync_status                 TEXT,
    retention_until             TIMESTAMPTZ,
    observed_at                 TIMESTAMPTZ NOT NULL,
    metadata_json               JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (reconciliation_run_id, sequence_number),
    UNIQUE (
        reconciliation_run_id,
        raw_object_key,
        raw_object_version
    ),
    CONSTRAINT ck_legal_raw_reconciliation_item_sequence
        CHECK (sequence_number >= 1),
    CONSTRAINT ck_legal_raw_reconciliation_item_identity
        CHECK (
            raw_object_key LIKE 'legal-corpus/raw/%'
            AND raw_object_key !~ '(^|/)\.\.(/|$)'
            AND btrim(raw_object_version) <> ''
            AND raw_sha256 ~ '^[0-9a-f]{64}$'
            AND raw_size_bytes >= 0
        ),
    CONSTRAINT ck_legal_raw_reconciliation_observation_status
        CHECK (observation_status IN (
            'referenced',
            'active_sync',
            'grace_period',
            'orphan_preserved',
            'manual_disposition_required'
        )),
    CONSTRAINT ck_legal_raw_reconciliation_sync_status
        CHECK (
            sync_status IS NULL
            OR sync_status IN (
                'pending',
                'running',
                'completed',
                'failed',
                'cancelled'
            )
        ),
    CONSTRAINT ck_legal_raw_reconciliation_retention
        CHECK (
            observation_status NOT IN (
                'orphan_preserved',
                'manual_disposition_required'
            )
            OR (
                retention_until IS NOT NULL
                AND retention_until > observed_at
            )
        ),
    CONSTRAINT ck_legal_raw_reconciliation_item_metadata
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_raw_reconciliation_status
    ON legal_raw_object_reconciliation_items(
        observation_status, observed_at DESC
    );

-- ---------------------------------------------------------------------------
-- Global immutable legal corpus
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legal_resources (
    legal_resource_id              TEXT PRIMARY KEY,
    provider_id                   TEXT NOT NULL,
    resource_type                 TEXT NOT NULL,
    official_identifier           TEXT NOT NULL,
    jurisdiction_code             TEXT NOT NULL DEFAULT 'KR',
    issuing_authority             TEXT NOT NULL,
    identity_hash                 TEXT NOT NULL,
    cross_revision_key            TEXT GENERATED ALWAYS AS (
        'official-resource:v1:' || identity_hash
    ) STORED,
    first_observed_at             TIMESTAMPTZ NOT NULL,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_resource_id, provider_id),
    UNIQUE (legal_resource_id, cross_revision_key),
    UNIQUE (cross_revision_key),
    UNIQUE (
        provider_id,
        resource_type,
        official_identifier,
        jurisdiction_code
    ),
    CONSTRAINT fk_legal_resource_provider
        FOREIGN KEY (provider_id)
        REFERENCES legal_source_providers(provider_id) ON DELETE RESTRICT,
    CONSTRAINT ck_legal_resource_type
        CHECK (resource_type IN (
            'statute',
            'decree',
            'ministerial_rule',
            'administrative_rule',
            'local_ordinance',
            'precedent',
            'constitutional_decision',
            'legal_interpretation',
            'committee_decision',
            'official_term',
            'official_form',
            'official_appendix'
        )),
    CONSTRAINT ck_legal_resource_identity
        CHECK (
            btrim(official_identifier) <> ''
            AND btrim(jurisdiction_code) <> ''
            AND btrim(issuing_authority) <> ''
        ),
    CONSTRAINT ck_legal_resource_identity_hash
        CHECK (identity_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_resource_cross_revision_key
        CHECK (
            cross_revision_key
            ~ '^official-resource:v1:[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_resource_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_resources_exact_identifier
    ON legal_resources(resource_type, official_identifier);
CREATE INDEX IF NOT EXISTS idx_legal_resources_identifier_trgm
    ON legal_resources
    USING GIN (official_identifier extensions.gin_trgm_ops);

-- A provider revision is an acquisition/configuration identity, not a legal
-- resource identity.  Each revision-specific alias is therefore cross-walked
-- to the stable official resource key.  The first source fetch is retained as
-- immutable evidence of when that mapping was first observed.
CREATE TABLE IF NOT EXISTS legal_resource_identity_crosswalks (
    legal_resource_crosswalk_id    TEXT PRIMARY KEY,
    legal_resource_id              TEXT NOT NULL,
    provider_id                    TEXT NOT NULL,
    first_observed_source_fetch_id TEXT NOT NULL,
    provider_resource_type         TEXT NOT NULL,
    provider_official_identifier   TEXT NOT NULL,
    provider_jurisdiction_code     TEXT NOT NULL DEFAULT 'KR',
    cross_revision_key             TEXT NOT NULL,
    observed_at                    TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        provider_id,
        provider_resource_type,
        provider_official_identifier,
        provider_jurisdiction_code
    ),
    CONSTRAINT fk_legal_resource_crosswalk_resource
        FOREIGN KEY (legal_resource_id, cross_revision_key)
        REFERENCES legal_resources(
            legal_resource_id,
            cross_revision_key
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_resource_crosswalk_provider
        FOREIGN KEY (provider_id)
        REFERENCES legal_source_providers(provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_resource_crosswalk_fetch
        FOREIGN KEY (first_observed_source_fetch_id, provider_id)
        REFERENCES legal_source_fetches(source_fetch_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_resource_crosswalk_type
        CHECK (provider_resource_type IN (
            'statute',
            'decree',
            'ministerial_rule',
            'administrative_rule',
            'local_ordinance',
            'precedent',
            'constitutional_decision',
            'legal_interpretation',
            'committee_decision',
            'official_term',
            'official_form',
            'official_appendix'
        )),
    CONSTRAINT ck_legal_resource_crosswalk_identity
        CHECK (
            btrim(provider_official_identifier) <> ''
            AND btrim(provider_jurisdiction_code) <> ''
            AND cross_revision_key
                ~ '^official-resource:v1:[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_resource_crosswalk_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_resource_crosswalks_resource
    ON legal_resource_identity_crosswalks(
        legal_resource_id,
        provider_id
    );

CREATE TABLE IF NOT EXISTS legal_resource_versions (
    legal_resource_version_id      TEXT PRIMARY KEY,
    legal_resource_id              TEXT NOT NULL,
    provider_id                    TEXT NOT NULL,
    source_fetch_id                TEXT NOT NULL,
    official_version_identifier    TEXT NOT NULL,
    version_sequence               INTEGER NOT NULL,
    canonical_title                TEXT NOT NULL,
    alternative_titles_text        TEXT NOT NULL DEFAULT '',
    publication_date               DATE,
    promulgation_date              DATE,
    effective_from                 DATE,
    effective_to                   DATE,
    valid_time_start               DATE,
    valid_time_end                 DATE,
    observed_at                    TIMESTAMPTZ NOT NULL,
    fetched_at                     TIMESTAMPTZ NOT NULL,
    verified_at                    TIMESTAMPTZ,
    verification_status            TEXT NOT NULL,
    normalized_sha256              TEXT NOT NULL,
    parser_schema_version          TEXT NOT NULL,
    supersedes_version_id          TEXT,
    search_document                TSVECTOR GENERATED ALWAYS AS (
        to_tsvector(
            'simple'::regconfig,
            COALESCE(canonical_title, '')
            || ' '
            || COALESCE(alternative_titles_text, '')
        )
    ) STORED,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_resource_version_id, legal_resource_id),
    UNIQUE (source_fetch_id, legal_resource_version_id),
    UNIQUE (
        legal_resource_id,
        official_version_identifier,
        version_sequence
    ),
    UNIQUE (legal_resource_id, version_sequence),
    CONSTRAINT fk_legal_version_resource
        FOREIGN KEY (legal_resource_id)
        REFERENCES legal_resources(legal_resource_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_version_source_fetch
        FOREIGN KEY (source_fetch_id, provider_id)
        REFERENCES legal_source_fetches(source_fetch_id, provider_id)
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_version_supersedes_same_resource
        FOREIGN KEY (supersedes_version_id, legal_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_version_sequence
        CHECK (version_sequence >= 1),
    CONSTRAINT ck_legal_version_title
        CHECK (
            btrim(canonical_title) <> ''
            AND btrim(official_version_identifier) <> ''
        ),
    CONSTRAINT ck_legal_version_verification
        CHECK (verification_status IN (
            'unverified',
            'source_verified',
            'schema_verified',
            'conflicted',
            'withdrawn'
        )),
    CONSTRAINT ck_legal_version_hash
        CHECK (normalized_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_version_effective_range
        CHECK (
            effective_to IS NULL
            OR effective_from IS NULL
            OR effective_to >= effective_from
        ),
    CONSTRAINT ck_legal_version_valid_time_range
        CHECK (
            valid_time_end IS NULL
            OR valid_time_start IS NULL
            OR valid_time_end >= valid_time_start
        ),
    CONSTRAINT ck_legal_version_system_time
        CHECK (
            fetched_at >= observed_at
            AND (verified_at IS NULL OR verified_at >= fetched_at)
        ),
    CONSTRAINT ck_legal_version_not_self_superseding
        CHECK (
            supersedes_version_id IS NULL
            OR supersedes_version_id <> legal_resource_version_id
        ),
    CONSTRAINT ck_legal_version_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_versions_resource_time
    ON legal_resource_versions(
        legal_resource_id,
        effective_from DESC,
        observed_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_legal_versions_as_of
    ON legal_resource_versions(valid_time_start, valid_time_end);
CREATE INDEX IF NOT EXISTS idx_legal_versions_title_trgm
    ON legal_resource_versions
    USING GIN (canonical_title extensions.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_legal_versions_full_text
    ON legal_resource_versions USING GIN (search_document);

-- Effective-time lineage is intentionally separate from
-- supersedes_version_id.  The latter is the immutable ingestion/correction
-- chain; this edge records the provider-declared legal predecessor and the
-- result of fail-closed overlap/gap validation.
CREATE TABLE IF NOT EXISTS legal_resource_version_lineages (
    legal_resource_version_lineage_id TEXT PRIMARY KEY,
    legal_resource_id                 TEXT NOT NULL,
    legal_resource_version_id         TEXT NOT NULL,
    effective_predecessor_version_id  TEXT,
    lineage_type                      TEXT NOT NULL,
    continuity_status                 TEXT NOT NULL,
    validation_policy                 TEXT NOT NULL,
    predecessor_effective_to          DATE,
    successor_effective_from          DATE,
    derived_predecessor_effective_to  DATE,
    gap_days                          INTEGER NOT NULL DEFAULT 0,
    validated_at                      TIMESTAMPTZ NOT NULL,
    metadata_json                     JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_resource_version_id),
    CONSTRAINT fk_legal_version_lineage_successor
        FOREIGN KEY (
            legal_resource_version_id,
            legal_resource_id
        )
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_version_lineage_predecessor
        FOREIGN KEY (
            effective_predecessor_version_id,
            legal_resource_id
        )
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_version_lineage_type
        CHECK (lineage_type IN (
            'initial',
            'source_correction',
            'effective_successor'
        )),
    CONSTRAINT ck_legal_version_lineage_continuity
        CHECK (continuity_status IN (
            'initial',
            'not_applicable',
            'same_window_correction',
            'contiguous',
            'open_end_closed',
            'documented_gap'
        )),
    CONSTRAINT ck_legal_version_lineage_policy
        CHECK (validation_policy IN (
            'strict_contiguous',
            'allow_documented_gap',
            'not_applicable'
        )),
    CONSTRAINT ck_legal_version_lineage_predecessor
        CHECK (
            (
                lineage_type = 'initial'
                AND effective_predecessor_version_id IS NULL
            )
            OR (
                lineage_type <> 'initial'
                AND effective_predecessor_version_id IS NOT NULL
                AND effective_predecessor_version_id
                    <> legal_resource_version_id
            )
        ),
    CONSTRAINT ck_legal_version_lineage_gap
        CHECK (
            gap_days >= 0
            AND (
                continuity_status = 'documented_gap'
                OR gap_days = 0
            )
            AND (
                continuity_status <> 'documented_gap'
                OR (
                    validation_policy = 'allow_documented_gap'
                    AND gap_days > 0
                    AND btrim(
                        COALESCE(metadata_json ->> 'gapReason', '')
                    ) <> ''
                )
            )
        ),
    CONSTRAINT ck_legal_version_lineage_derived_end
        CHECK (
            continuity_status = 'open_end_closed'
            OR derived_predecessor_effective_to IS NULL
        ),
    CONSTRAINT ck_legal_version_lineage_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_version_lineages_predecessor
    ON legal_resource_version_lineages(
        legal_resource_id,
        effective_predecessor_version_id
    )
    WHERE effective_predecessor_version_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_legal_version_lineages_successor_edge
    ON legal_resource_version_lineages(
        legal_resource_id,
        effective_predecessor_version_id
    )
    WHERE lineage_type = 'effective_successor';

CREATE OR REPLACE FUNCTION legal_validate_resource_version_lineage()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $legal_effective_lineage$
DECLARE
    successor          legal_resource_versions%ROWTYPE;
    predecessor        legal_resource_versions%ROWTYPE;
    successor_start    DATE;
    predecessor_start  DATE;
    predecessor_end    DATE;
    calculated_gap     INTEGER;
    declared_official  TEXT;
BEGIN
    SELECT *
      INTO successor
      FROM legal_resource_versions
     WHERE legal_resource_version_id =
           NEW.legal_resource_version_id
       AND legal_resource_id = NEW.legal_resource_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'effective lineage successor version is absent'
            USING
                ERRCODE = '23514',
                CONSTRAINT = 'ck_legal_version_effective_lineage';
    END IF;
    IF (
        successor.verified_at IS NOT NULL
        AND NEW.validated_at < successor.verified_at
    ) THEN
        RAISE EXCEPTION
            'effective lineage predates version verification'
            USING
                ERRCODE = '23514',
                CONSTRAINT = 'ck_legal_version_effective_lineage';
    END IF;

    declared_official = NULLIF(
        btrim(
            COALESCE(
                NEW.metadata_json
                    ->> 'declaredPredecessorOfficialVersionIdentifier',
                ''
            )
        ),
        ''
    );
    IF NEW.lineage_type = 'initial' THEN
        IF (
            successor.version_sequence <> 1
            OR NEW.effective_predecessor_version_id IS NOT NULL
            OR declared_official IS NOT NULL
        ) THEN
            RAISE EXCEPTION
                'initial version has an invalid predecessor'
                USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_legal_version_effective_lineage';
        END IF;
        RETURN NEW;
    END IF;

    SELECT *
      INTO predecessor
      FROM legal_resource_versions
     WHERE legal_resource_version_id =
           NEW.effective_predecessor_version_id
       AND legal_resource_id = NEW.legal_resource_id;
    IF (
        NOT FOUND
        OR predecessor.version_sequence
           <> successor.version_sequence - 1
    ) THEN
        RAISE EXCEPTION
            'effective predecessor is not the immediately prior version'
            USING
                ERRCODE = '23514',
                CONSTRAINT = 'ck_legal_version_effective_lineage';
    END IF;

    IF NEW.lineage_type = 'source_correction' THEN
        IF (
            successor.official_version_identifier
                <> predecessor.official_version_identifier
            OR declared_official IS NOT NULL
               AND declared_official
                   <> predecessor.official_version_identifier
            OR successor.effective_from
                IS DISTINCT FROM predecessor.effective_from
            OR successor.effective_to
                IS DISTINCT FROM predecessor.effective_to
            OR successor.valid_time_start
                IS DISTINCT FROM predecessor.valid_time_start
            OR successor.valid_time_end
                IS DISTINCT FROM predecessor.valid_time_end
            OR NEW.continuity_status <> 'same_window_correction'
        ) THEN
            RAISE EXCEPTION
                'source correction rewrites its official time window'
                USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_legal_version_effective_lineage';
        END IF;
        RETURN NEW;
    END IF;

    IF (
        declared_official IS DISTINCT FROM
            predecessor.official_version_identifier
        OR successor.official_version_identifier =
           predecessor.official_version_identifier
    ) THEN
        RAISE EXCEPTION
            'effective successor has an invalid declared predecessor'
            USING
                ERRCODE = '23514',
                CONSTRAINT = 'ck_legal_version_effective_lineage';
    END IF;
    IF NEW.validation_policy = 'not_applicable' THEN
        IF NEW.continuity_status <> 'not_applicable' THEN
            RAISE EXCEPTION
                'not-applicable lineage has an invalid status'
                USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_legal_version_effective_lineage';
        END IF;
        RETURN NEW;
    END IF;

    successor_start = COALESCE(
        successor.effective_from,
        successor.valid_time_start
    );
    predecessor_start = COALESCE(
        predecessor.effective_from,
        predecessor.valid_time_start
    );
    predecessor_end = COALESCE(
        predecessor.effective_to,
        predecessor.valid_time_end
    );
    IF (
        successor_start IS NULL
        OR predecessor_start IS NULL
        OR successor_start <= predecessor_start
    ) THEN
        RAISE EXCEPTION
            'effective successor overlaps or has indeterminate dates'
            USING
                ERRCODE = '23514',
                CONSTRAINT = 'ck_legal_version_effective_lineage';
    END IF;

    IF predecessor_end IS NULL THEN
        IF (
            NEW.continuity_status <> 'open_end_closed'
            OR NEW.successor_effective_from
                IS DISTINCT FROM successor_start
            OR NEW.derived_predecessor_effective_to
                IS DISTINCT FROM successor_start - 1
            OR NEW.gap_days <> 0
        ) THEN
            RAISE EXCEPTION
                'open predecessor closure is inconsistent'
                USING
                    ERRCODE = '23514',
                    CONSTRAINT = 'ck_legal_version_effective_lineage';
        END IF;
        RETURN NEW;
    END IF;

    IF successor_start <= predecessor_end THEN
        RAISE EXCEPTION
            'effective successor overlaps its predecessor'
            USING
                ERRCODE = '23514',
                CONSTRAINT = 'ck_legal_version_effective_lineage';
    END IF;
    calculated_gap = successor_start - (predecessor_end + 1);
    IF (
        NEW.predecessor_effective_to
            IS DISTINCT FROM predecessor_end
        OR NEW.successor_effective_from
            IS DISTINCT FROM successor_start
        OR (
            calculated_gap = 0
            AND (
                NEW.continuity_status <> 'contiguous'
                OR NEW.gap_days <> 0
            )
        )
        OR (
            calculated_gap > 0
            AND (
                NEW.validation_policy <> 'allow_documented_gap'
                OR NEW.continuity_status <> 'documented_gap'
                OR NEW.gap_days <> calculated_gap
            )
        )
    ) THEN
        RAISE EXCEPTION
            'effective successor contains an overlap or undocumented gap'
            USING
                ERRCODE = '23514',
                CONSTRAINT = 'ck_legal_version_effective_lineage';
    END IF;
    RETURN NEW;
END;
$legal_effective_lineage$;

DROP TRIGGER IF EXISTS trg_legal_validate_resource_version_lineage
    ON legal_resource_version_lineages;
CREATE TRIGGER trg_legal_validate_resource_version_lineage
    BEFORE INSERT ON legal_resource_version_lineages
    FOR EACH ROW
    EXECUTE FUNCTION legal_validate_resource_version_lineage();

CREATE TABLE IF NOT EXISTS legal_provisions (
    legal_provision_id             TEXT PRIMARY KEY,
    legal_resource_version_id      TEXT NOT NULL,
    legal_resource_id              TEXT NOT NULL,
    parent_provision_id            TEXT,
    provision_type                 TEXT NOT NULL,
    provision_identifier           TEXT NOT NULL,
    display_path                   TEXT NOT NULL,
    ordinal_path                   INTEGER[] NOT NULL DEFAULT ARRAY[]::INTEGER[],
    heading_text                   TEXT,
    body_text                      TEXT NOT NULL,
    provision_sha256               TEXT NOT NULL,
    source_locator                 TEXT NOT NULL,
    search_document                TSVECTOR GENERATED ALWAYS AS (
        to_tsvector(
            'simple'::regconfig,
            COALESCE(heading_text, '')
            || ' '
            || COALESCE(body_text, '')
        )
    ) STORED,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_provision_id,
        legal_resource_version_id
    ),
    UNIQUE (
        legal_resource_version_id,
        provision_identifier
    ),
    CONSTRAINT fk_legal_provision_version_resource
        FOREIGN KEY (legal_resource_version_id, legal_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_provision_parent_same_version
        FOREIGN KEY (parent_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_provision_type
        CHECK (provision_type IN (
            'preamble',
            'chapter',
            'section',
            'article',
            'paragraph',
            'item',
            'subitem',
            'addendum',
            'appendix',
            'form',
            'decision_section',
            'holding',
            'reasoning'
        )),
    CONSTRAINT ck_legal_provision_identity
        CHECK (
            btrim(provision_identifier) <> ''
            AND btrim(display_path) <> ''
            AND btrim(source_locator) <> ''
        ),
    CONSTRAINT ck_legal_provision_hash
        CHECK (provision_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_provision_ordinal_path
        CHECK (array_position(ordinal_path, NULL) IS NULL),
    CONSTRAINT ck_legal_provision_not_self_parent
        CHECK (
            parent_provision_id IS NULL
            OR parent_provision_id <> legal_provision_id
        ),
    CONSTRAINT ck_legal_provision_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_provisions_version_path
    ON legal_provisions(legal_resource_version_id, ordinal_path);
CREATE INDEX IF NOT EXISTS idx_legal_provisions_identifier_trgm
    ON legal_provisions
    USING GIN (provision_identifier extensions.gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_legal_provisions_full_text
    ON legal_provisions USING GIN (search_document);

CREATE TABLE IF NOT EXISTS legal_citations (
    legal_citation_id              TEXT PRIMARY KEY,
    source_resource_id             TEXT NOT NULL
        REFERENCES legal_resources(legal_resource_id) ON DELETE RESTRICT,
    source_version_id              TEXT NOT NULL,
    source_provision_id            TEXT,
    cited_identifier_text          TEXT NOT NULL,
    target_resource_id             TEXT,
    target_version_id              TEXT,
    target_provision_id            TEXT,
    citation_status                TEXT NOT NULL,
    assertion_method               TEXT NOT NULL,
    algorithm_version              TEXT,
    confidence                     DOUBLE PRECISION,
    human_review_status            TEXT NOT NULL,
    evidence_excerpt_sha256        TEXT,
    asserted_at                    TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT fk_legal_citation_source_version
        FOREIGN KEY (source_version_id, source_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_citation_source_provision
        FOREIGN KEY (source_provision_id, source_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_citation_target_version
        FOREIGN KEY (target_version_id, target_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_citation_target_provision
        FOREIGN KEY (target_provision_id, target_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_citation_status
        CHECK (citation_status IN (
            'unresolved', 'ambiguous', 'resolved', 'verified', 'rejected'
        )),
    CONSTRAINT ck_legal_citation_method
        CHECK (assertion_method IN (
            'official_structured',
            'deterministic_extract',
            'machine_inferred',
            'human_asserted'
        )),
    CONSTRAINT ck_legal_citation_human_review
        CHECK (human_review_status IN (
            'not_required', 'pending', 'approved', 'rejected'
        )),
    CONSTRAINT ck_legal_citation_inference_requires_review
        CHECK (
            assertion_method <> 'machine_inferred'
            OR human_review_status <> 'not_required'
        ),
    CONSTRAINT ck_legal_citation_target_shape
        CHECK (
            (target_version_id IS NULL OR target_resource_id IS NOT NULL)
            AND (target_provision_id IS NULL OR target_version_id IS NOT NULL)
        ),
    CONSTRAINT ck_legal_citation_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0.0 AND confidence <= 1.0)
        ),
    CONSTRAINT ck_legal_citation_excerpt_hash
        CHECK (
            evidence_excerpt_sha256 IS NULL
            OR evidence_excerpt_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_citation_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_citations_source
    ON legal_citations(source_resource_id, source_version_id);
CREATE INDEX IF NOT EXISTS idx_legal_citations_target
    ON legal_citations(target_resource_id, target_version_id)
    WHERE target_resource_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_legal_citations_unresolved
    ON legal_citations(citation_status, asserted_at)
    WHERE citation_status IN ('unresolved', 'ambiguous');

CREATE TABLE IF NOT EXISTS legal_relation_assertions (
    legal_relation_assertion_id    TEXT PRIMARY KEY,
    subject_resource_id            TEXT NOT NULL
        REFERENCES legal_resources(legal_resource_id) ON DELETE RESTRICT,
    object_resource_id             TEXT NOT NULL
        REFERENCES legal_resources(legal_resource_id) ON DELETE RESTRICT,
    relation_type                  TEXT NOT NULL,
    source_version_id              TEXT NOT NULL,
    source_provision_id            TEXT,
    assertion_method               TEXT NOT NULL,
    algorithm_version              TEXT,
    confidence                     DOUBLE PRECISION,
    human_review_status            TEXT NOT NULL DEFAULT 'pending',
    assertion_status               TEXT NOT NULL DEFAULT 'candidate',
    as_of_date                     DATE NOT NULL,
    asserted_at                    TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT fk_legal_relation_source_version
        FOREIGN KEY (source_version_id, subject_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_relation_source_provision
        FOREIGN KEY (source_provision_id, source_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_relation_type
        CHECK (relation_type IN (
            'cites',
            'applies',
            'interprets',
            'distinguishes',
            'follows',
            'limits',
            'criticizes',
            'overrules_candidate',
            'supersedes'
        )),
    CONSTRAINT ck_legal_relation_method
        CHECK (assertion_method IN (
            'official_structured',
            'deterministic_extract',
            'machine_inferred',
            'human_asserted'
        )),
    CONSTRAINT ck_legal_relation_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0.0 AND confidence <= 1.0)
        ),
    CONSTRAINT ck_legal_relation_human_review
        CHECK (human_review_status IN (
            'pending', 'approved', 'rejected'
        )),
    CONSTRAINT ck_legal_relation_status
        CHECK (assertion_status IN (
            'candidate', 'verified', 'rejected', 'superseded'
        )),
    CONSTRAINT ck_legal_relation_no_self_edge
        CHECK (
            subject_resource_id <> object_resource_id
            OR relation_type = 'supersedes'
        ),
    CONSTRAINT ck_legal_relation_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_relation_subject
    ON legal_relation_assertions(
        subject_resource_id,
        relation_type,
        as_of_date DESC
    );
CREATE INDEX IF NOT EXISTS idx_legal_relation_object
    ON legal_relation_assertions(
        object_resource_id,
        relation_type,
        as_of_date DESC
    );

-- The institution-wide glossary remains the canonical term/alias authority.
-- Legal tables add versioned definitions and exact official provision links;
-- they do not create a second alias registry.
CREATE TABLE IF NOT EXISTS legal_term_definitions (
    legal_term_definition_id       TEXT PRIMARY KEY,
    term_id                        TEXT NOT NULL
        REFERENCES glossary_terms(term_id) ON DELETE RESTRICT,
    definition_version             INTEGER NOT NULL,
    definition_scope               TEXT NOT NULL,
    definition_text                TEXT NOT NULL,
    legal_resource_id              TEXT NOT NULL,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT,
    definition_sha256              TEXT NOT NULL,
    valid_from                     DATE,
    valid_to                       DATE,
    observed_at                    TIMESTAMPTZ NOT NULL,
    supersedes_definition_id       TEXT,
    review_status                  TEXT NOT NULL,
    reviewed_by                    TEXT,
    reviewed_at                    TIMESTAMPTZ,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_term_definition_id, term_id),
    UNIQUE (term_id, definition_scope, definition_version),
    CONSTRAINT fk_legal_term_definition_version
        FOREIGN KEY (legal_resource_version_id, legal_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_term_definition_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_term_definition_supersedes_same_term
        FOREIGN KEY (supersedes_definition_id, term_id)
        REFERENCES legal_term_definitions(
            legal_term_definition_id,
            term_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_term_definition_version
        CHECK (definition_version >= 1),
    CONSTRAINT ck_legal_term_definition_scope
        CHECK (definition_scope IN (
            'official',
            'industry_practice',
            'realytics_standard'
        )),
    CONSTRAINT ck_legal_term_definition_text
        CHECK (btrim(definition_text) <> ''),
    CONSTRAINT ck_legal_term_definition_hash
        CHECK (definition_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_term_definition_dates
        CHECK (
            valid_to IS NULL
            OR valid_from IS NULL
            OR valid_to >= valid_from
        ),
    CONSTRAINT ck_legal_term_definition_review
        CHECK (review_status IN (
            'candidate', 'approved', 'rejected', 'superseded'
        )),
    CONSTRAINT ck_legal_term_definition_review_actor
        CHECK (
            review_status = 'candidate'
            OR (
                btrim(COALESCE(reviewed_by, '')) <> ''
                AND reviewed_at IS NOT NULL
            )
        ),
    CONSTRAINT ck_legal_term_definition_not_self_superseding
        CHECK (
            supersedes_definition_id IS NULL
            OR supersedes_definition_id <> legal_term_definition_id
        ),
    CONSTRAINT ck_legal_term_definition_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_term_definitions_lookup
    ON legal_term_definitions(term_id, definition_scope, valid_from DESC);
CREATE INDEX IF NOT EXISTS idx_legal_term_definitions_text_trgm
    ON legal_term_definitions
    USING GIN (definition_text extensions.gin_trgm_ops);

CREATE TABLE IF NOT EXISTS legal_term_provision_links (
    legal_term_provision_link_id   TEXT PRIMARY KEY,
    term_id                        TEXT NOT NULL
        REFERENCES glossary_terms(term_id) ON DELETE RESTRICT,
    legal_resource_id              TEXT NOT NULL
        REFERENCES legal_resources(legal_resource_id) ON DELETE RESTRICT,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT NOT NULL,
    relation_type                  TEXT NOT NULL,
    assertion_method               TEXT NOT NULL,
    confidence                     DOUBLE PRECISION,
    review_status                  TEXT NOT NULL,
    asserted_at                    TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    CONSTRAINT fk_legal_term_link_version
        FOREIGN KEY (legal_resource_version_id, legal_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_term_link_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_term_link_relation
        CHECK (relation_type IN (
            'defined_by', 'mentioned_in', 'interpreted_by', 'applies_to'
        )),
    CONSTRAINT ck_legal_term_link_method
        CHECK (assertion_method IN (
            'official_structured',
            'deterministic_extract',
            'machine_inferred',
            'human_asserted'
        )),
    CONSTRAINT ck_legal_term_link_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0.0 AND confidence <= 1.0)
        ),
    CONSTRAINT ck_legal_term_link_review
        CHECK (review_status IN (
            'candidate', 'approved', 'rejected'
        )),
    CONSTRAINT ck_legal_term_link_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_term_links_term
    ON legal_term_provision_links(term_id, review_status);
CREATE INDEX IF NOT EXISTS idx_legal_term_links_provision
    ON legal_term_provision_links(
        legal_resource_version_id,
        legal_provision_id
    );

CREATE TABLE IF NOT EXISTS legal_semantic_artifacts (
    legal_semantic_artifact_id     TEXT PRIMARY KEY,
    legal_resource_id              TEXT NOT NULL,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT,
    embedding_model                TEXT NOT NULL,
    embedding_model_version        TEXT NOT NULL,
    embedding_dimensions           INTEGER NOT NULL,
    embedding_sha256               TEXT NOT NULL,
    embedding_object_key           TEXT NOT NULL,
    generated_at                   TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_resource_version_id,
        legal_provision_id,
        embedding_model,
        embedding_model_version
    ),
    CONSTRAINT fk_legal_semantic_version
        FOREIGN KEY (legal_resource_version_id, legal_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_semantic_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_semantic_dimensions
        CHECK (embedding_dimensions >= 1),
    CONSTRAINT ck_legal_semantic_hash
        CHECK (embedding_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_semantic_identity
        CHECK (
            btrim(embedding_model) <> ''
            AND btrim(embedding_model_version) <> ''
            AND btrim(embedding_object_key) <> ''
        ),
    CONSTRAINT ck_legal_semantic_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
COMMENT ON TABLE legal_semantic_artifacts IS
    'Immutable embedding identities only. A later qualified pgvector migration may add vector storage and indexes without changing source provenance.';

-- ---------------------------------------------------------------------------
-- Tenant/project legal analysis overlay
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS legal_query_runs (
    legal_query_run_id             TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    query_text                     TEXT NOT NULL,
    query_hash                     TEXT NOT NULL,
    as_of_date                     DATE NOT NULL,
    data_classification            TEXT NOT NULL DEFAULT 'project_confidential',
    requested_transports           TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    freshness_policy_version       TEXT NOT NULL,
    ranking_policy_version         TEXT NOT NULL,
    idempotency_key                TEXT NOT NULL,
    requested_by                   TEXT NOT NULL,
    requested_at                   TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_query_run_id, tenant_id, project_id),
    UNIQUE (tenant_id, project_id, idempotency_key),
    CONSTRAINT fk_legal_query_project_scope
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT ck_legal_query_text
        CHECK (btrim(query_text) <> ''),
    CONSTRAINT ck_legal_query_hash
        CHECK (query_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_query_classification
        CHECK (data_classification IN (
            'public',
            'internal',
            'project_confidential',
            'legally_privileged'
        )),
    CONSTRAINT ck_legal_query_transports
        CHECK (
            array_position(requested_transports, NULL) IS NULL
            AND requested_transports <@ ARRAY[
                'local_db',
                'direct_api',
                'self_hosted_mcp',
                'remote_mcp'
            ]::TEXT[]
        ),
    CONSTRAINT ck_legal_query_remote_transport_classification
        CHECK (
            NOT ('remote_mcp' = ANY(requested_transports))
            OR data_classification IN ('public', 'internal')
        ),
    CONSTRAINT ck_legal_query_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_query_runs_project
    ON legal_query_runs(tenant_id, project_id, requested_at DESC);

CREATE TABLE IF NOT EXISTS legal_query_run_events (
    legal_query_event_id           TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    legal_query_run_id             TEXT NOT NULL,
    sequence_number                INTEGER NOT NULL,
    from_status                    TEXT,
    to_status                      TEXT NOT NULL,
    provider_id                    TEXT
        REFERENCES legal_source_providers(provider_id) ON DELETE RESTRICT,
    transport                     TEXT,
    output_hash                   TEXT,
    error_code                    TEXT,
    occurred_at                   TIMESTAMPTZ NOT NULL,
    metadata_json                 JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_query_run_id, sequence_number),
    CONSTRAINT fk_legal_query_event_project_scope
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_query_event_run_scope
        FOREIGN KEY (legal_query_run_id, tenant_id, project_id)
        REFERENCES legal_query_runs(
            legal_query_run_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_query_event_sequence
        CHECK (sequence_number >= 1),
    CONSTRAINT ck_legal_query_event_from_status
        CHECK (
            from_status IS NULL
            OR from_status IN (
                'pending', 'running', 'completed', 'failed', 'cancelled'
            )
        ),
    CONSTRAINT ck_legal_query_event_to_status
        CHECK (to_status IN (
            'pending', 'running', 'completed', 'failed', 'cancelled'
        )),
    CONSTRAINT ck_legal_query_event_transport
        CHECK (
            transport IS NULL
            OR transport IN (
                'local_db',
                'direct_api',
                'self_hosted_mcp',
                'remote_mcp'
            )
        ),
    CONSTRAINT ck_legal_query_event_output_hash
        CHECK (
            output_hash IS NULL
            OR output_hash ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT ck_legal_query_event_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_query_events_latest
    ON legal_query_run_events(
        tenant_id,
        project_id,
        legal_query_run_id,
        sequence_number DESC
    );

CREATE TABLE IF NOT EXISTS project_legal_resource_links (
    project_legal_resource_link_id TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    legal_resource_id              TEXT NOT NULL
        REFERENCES legal_resources(legal_resource_id) ON DELETE RESTRICT,
    legal_resource_version_id      TEXT,
    legal_provision_id             TEXT,
    relation_type                  TEXT NOT NULL,
    claim_key                      TEXT,
    source_context                 TEXT NOT NULL,
    link_status                    TEXT NOT NULL DEFAULT 'candidate',
    supersedes_link_id             TEXT,
    linked_by                      TEXT NOT NULL,
    linked_at                      TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        project_legal_resource_link_id,
        tenant_id,
        project_id
    ),
    CONSTRAINT fk_project_legal_link_project_scope
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_link_version
        FOREIGN KEY (legal_resource_version_id, legal_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_link_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_project_legal_link_supersedes_scope
        FOREIGN KEY (supersedes_link_id, tenant_id, project_id)
        REFERENCES project_legal_resource_links(
            project_legal_resource_link_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_project_legal_link_relation
        CHECK (relation_type IN (
            'governs',
            'supports',
            'contradicts',
            'requires_permit',
            'creates_covenant',
            'creates_security',
            'creates_condition_precedent',
            'changes_feasibility',
            'applies_to_parcel',
            'counsel_reference'
        )),
    CONSTRAINT ck_project_legal_link_source_context
        CHECK (source_context IN (
            'official_corpus',
            'tenant_document',
            'human_research',
            'machine_retrieval'
        )),
    CONSTRAINT ck_project_legal_link_status
        CHECK (link_status IN (
            'candidate', 'verified', 'rejected', 'superseded'
        )),
    CONSTRAINT ck_project_legal_link_provision_shape
        CHECK (
            legal_provision_id IS NULL
            OR legal_resource_version_id IS NOT NULL
        ),
    CONSTRAINT ck_project_legal_link_not_self_superseding
        CHECK (
            supersedes_link_id IS NULL
            OR supersedes_link_id <> project_legal_resource_link_id
        ),
    CONSTRAINT ck_project_legal_link_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_project_legal_links_project
    ON project_legal_resource_links(
        tenant_id,
        project_id,
        relation_type,
        linked_at DESC
    );
CREATE INDEX IF NOT EXISTS idx_project_legal_links_resource
    ON project_legal_resource_links(legal_resource_id, legal_resource_version_id);

CREATE TABLE IF NOT EXISTS legal_findings (
    legal_finding_id               TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    legal_query_run_id             TEXT,
    finding_key                    TEXT NOT NULL,
    finding_kind                   TEXT NOT NULL,
    finding_text                   TEXT NOT NULL,
    finding_text_sha256            TEXT NOT NULL,
    severity                       TEXT NOT NULL,
    confidence                     DOUBLE PRECISION,
    analysis_only                  BOOLEAN NOT NULL DEFAULT TRUE,
    report_eligible                BOOLEAN NOT NULL DEFAULT FALSE,
    requires_counsel_review        BOOLEAN NOT NULL DEFAULT TRUE,
    created_by                     TEXT NOT NULL,
    created_at                     TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (legal_finding_id, tenant_id, project_id),
    UNIQUE (tenant_id, project_id, finding_key, finding_text_sha256),
    CONSTRAINT fk_legal_finding_project_scope
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_finding_query_scope
        FOREIGN KEY (legal_query_run_id, tenant_id, project_id)
        REFERENCES legal_query_runs(
            legal_query_run_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_finding_kind
        CHECK (finding_kind IN (
            'machine_extraction',
            'ai_analysis',
            'deterministic_rule',
            'human_analysis',
            'counsel_opinion'
        )),
    CONSTRAINT ck_legal_finding_text
        CHECK (btrim(finding_key) <> '' AND btrim(finding_text) <> ''),
    CONSTRAINT ck_legal_finding_hash
        CHECK (finding_text_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_finding_severity
        CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    CONSTRAINT ck_legal_finding_confidence
        CHECK (
            confidence IS NULL
            OR (confidence >= 0.0 AND confidence <= 1.0)
        ),
    CONSTRAINT ck_legal_finding_ai_is_preliminary
        CHECK (
            finding_kind NOT IN ('machine_extraction', 'ai_analysis')
            OR (
                analysis_only
                AND NOT report_eligible
                AND requires_counsel_review
            )
        ),
    CONSTRAINT ck_legal_finding_analysis_not_reportable
        CHECK (NOT analysis_only OR NOT report_eligible),
    CONSTRAINT ck_legal_finding_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_findings_project
    ON legal_findings(tenant_id, project_id, severity, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_legal_findings_counsel_queue
    ON legal_findings(tenant_id, project_id, created_at)
    WHERE requires_counsel_review;

CREATE TABLE IF NOT EXISTS legal_evidence_snapshots (
    legal_evidence_snapshot_id     TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    legal_query_run_id             TEXT,
    snapshot_version               INTEGER NOT NULL,
    as_of_date                     DATE NOT NULL,
    freshness_policy_version       TEXT NOT NULL,
    snapshot_sha256                TEXT NOT NULL,
    citation_verification_status   TEXT NOT NULL,
    has_open_conflicts             BOOLEAN NOT NULL DEFAULT TRUE,
    is_stale                       BOOLEAN NOT NULL DEFAULT TRUE,
    gate_eligible                  BOOLEAN NOT NULL DEFAULT FALSE,
    supersedes_snapshot_id         TEXT,
    created_by                     TEXT NOT NULL,
    created_at                     TIMESTAMPTZ NOT NULL,
    creation_txid                  BIGINT NOT NULL
        DEFAULT pg_catalog.txid_current(),
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_evidence_snapshot_id,
        tenant_id,
        project_id
    ),
    UNIQUE (tenant_id, project_id, snapshot_version),
    CONSTRAINT fk_legal_evidence_snapshot_project_scope
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_evidence_snapshot_query_scope
        FOREIGN KEY (legal_query_run_id, tenant_id, project_id)
        REFERENCES legal_query_runs(
            legal_query_run_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_evidence_snapshot_supersedes_scope
        FOREIGN KEY (supersedes_snapshot_id, tenant_id, project_id)
        REFERENCES legal_evidence_snapshots(
            legal_evidence_snapshot_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_evidence_snapshot_version
        CHECK (snapshot_version >= 1),
    CONSTRAINT ck_legal_evidence_snapshot_hash
        CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_evidence_snapshot_citations
        CHECK (citation_verification_status IN (
            'unverified', 'partial', 'verified', 'conflicted'
        )),
    CONSTRAINT ck_legal_evidence_snapshot_gate
        CHECK (
            NOT gate_eligible
            OR (
                citation_verification_status = 'verified'
                AND NOT has_open_conflicts
                AND NOT is_stale
            )
        ),
    CONSTRAINT ck_legal_evidence_snapshot_not_self_superseding
        CHECK (
            supersedes_snapshot_id IS NULL
            OR supersedes_snapshot_id <> legal_evidence_snapshot_id
        ),
    CONSTRAINT ck_legal_evidence_snapshot_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_evidence_snapshots_project
    ON legal_evidence_snapshots(
        tenant_id,
        project_id,
        snapshot_version DESC
    );

CREATE TABLE IF NOT EXISTS legal_evidence_snapshot_items (
    legal_evidence_item_id         TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    legal_evidence_snapshot_id     TEXT NOT NULL,
    legal_finding_id               TEXT,
    legal_resource_id              TEXT NOT NULL
        REFERENCES legal_resources(legal_resource_id) ON DELETE RESTRICT,
    legal_resource_version_id      TEXT NOT NULL,
    legal_provision_id             TEXT,
    source_fetch_id                TEXT NOT NULL
        REFERENCES legal_source_fetches(source_fetch_id) ON DELETE RESTRICT,
    exact_locator                  TEXT NOT NULL,
    claim_text_sha256              TEXT NOT NULL,
    excerpt_sha256                 TEXT NOT NULL,
    citation_verification_status   TEXT NOT NULL,
    verified_at                    TIMESTAMPTZ,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_evidence_item_id,
        tenant_id,
        project_id
    ),
    CONSTRAINT fk_legal_evidence_item_project_scope
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_evidence_item_snapshot_scope
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
    CONSTRAINT fk_legal_evidence_item_finding_scope
        FOREIGN KEY (legal_finding_id, tenant_id, project_id)
        REFERENCES legal_findings(
            legal_finding_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_evidence_item_version
        FOREIGN KEY (legal_resource_version_id, legal_resource_id)
        REFERENCES legal_resource_versions(
            legal_resource_version_id,
            legal_resource_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_evidence_item_fetch_version
        FOREIGN KEY (source_fetch_id, legal_resource_version_id)
        REFERENCES legal_resource_versions(
            source_fetch_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_evidence_item_provision
        FOREIGN KEY (legal_provision_id, legal_resource_version_id)
        REFERENCES legal_provisions(
            legal_provision_id,
            legal_resource_version_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT ck_legal_evidence_item_locator
        CHECK (btrim(exact_locator) <> ''),
    CONSTRAINT ck_legal_evidence_item_claim_hash
        CHECK (claim_text_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_evidence_item_excerpt_hash
        CHECK (excerpt_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_legal_evidence_item_verification
        CHECK (citation_verification_status IN (
            'unverified', 'verified', 'stale', 'conflicted'
        )),
    CONSTRAINT ck_legal_evidence_item_verified_at
        CHECK (
            citation_verification_status <> 'verified'
            OR verified_at IS NOT NULL
        ),
    CONSTRAINT ck_legal_evidence_item_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_evidence_items_snapshot
    ON legal_evidence_snapshot_items(
        tenant_id,
        project_id,
        legal_evidence_snapshot_id
    );
CREATE INDEX IF NOT EXISTS idx_legal_evidence_items_resource
    ON legal_evidence_snapshot_items(
        legal_resource_id,
        legal_resource_version_id,
        legal_provision_id
    );

CREATE TABLE IF NOT EXISTS legal_review_decisions (
    legal_review_decision_id       TEXT PRIMARY KEY,
    tenant_id                      TEXT NOT NULL
        REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    project_id                     TEXT NOT NULL,
    legal_finding_id               TEXT,
    legal_evidence_snapshot_id     TEXT,
    decision_type                  TEXT NOT NULL,
    decision_scope                 TEXT NOT NULL,
    decision_reason                TEXT NOT NULL,
    reviewer_id                    TEXT NOT NULL,
    reviewer_role                  TEXT NOT NULL,
    decided_at                     TIMESTAMPTZ NOT NULL,
    metadata_json                  JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE (
        legal_review_decision_id,
        tenant_id,
        project_id
    ),
    CONSTRAINT fk_legal_review_decision_project_scope
        FOREIGN KEY (project_id, tenant_id)
        REFERENCES projects(project_id, tenant_id) ON DELETE RESTRICT,
    CONSTRAINT fk_legal_review_decision_finding_scope
        FOREIGN KEY (legal_finding_id, tenant_id, project_id)
        REFERENCES legal_findings(
            legal_finding_id,
            tenant_id,
            project_id
        )
        ON DELETE RESTRICT,
    CONSTRAINT fk_legal_review_decision_snapshot_scope
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
    CONSTRAINT ck_legal_review_decision_target
        CHECK (
            legal_finding_id IS NOT NULL
            OR legal_evidence_snapshot_id IS NOT NULL
        ),
    CONSTRAINT ck_legal_review_decision_type
        CHECK (decision_type IN (
            'approve',
            'reject',
            'request_changes',
            'waive_with_conditions',
            'supersede'
        )),
    CONSTRAINT ck_legal_review_decision_scope
        CHECK (decision_scope IN (
            'evidence_quality',
            'report_eligibility',
            'approval_gate',
            'counsel_opinion'
        )),
    CONSTRAINT ck_legal_review_decision_human_actor
        CHECK (
            btrim(reviewer_id) <> ''
            AND reviewer_role IN (
                'legal_reviewer',
                'external_counsel',
                'compliance_officer',
                'tenant_admin'
            )
        ),
    CONSTRAINT ck_legal_review_decision_reason
        CHECK (btrim(decision_reason) <> ''),
    CONSTRAINT ck_legal_review_decision_metadata_object
        CHECK (jsonb_typeof(metadata_json) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_legal_review_decisions_finding
    ON legal_review_decisions(
        tenant_id,
        project_id,
        legal_finding_id,
        decided_at DESC
    )
    WHERE legal_finding_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_legal_review_decisions_snapshot
    ON legal_review_decisions(
        tenant_id,
        project_id,
        legal_evidence_snapshot_id,
        decided_at DESC
    )
    WHERE legal_evidence_snapshot_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Deferred legal approval invariants
-- ---------------------------------------------------------------------------
-- Evidence packages are assembled as a snapshot row followed by its item rows.
-- A deferred constraint trigger lets that transaction shape remain valid while
-- preventing an empty or partially verified gate package from committing.
CREATE OR REPLACE FUNCTION legal_stamp_evidence_snapshot_transaction()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_snapshot_transaction$
BEGIN
    -- Callers cannot forge the transaction boundary used to seal item
    -- membership. The database always stamps the actual creating transaction.
    NEW.creation_txid := pg_catalog.txid_current();
    RETURN NEW;
END;
$legal_snapshot_transaction$;

DROP TRIGGER IF EXISTS trg_legal_evidence_snapshot_creation_txid
    ON legal_evidence_snapshots;
CREATE TRIGGER trg_legal_evidence_snapshot_creation_txid
    BEFORE INSERT ON legal_evidence_snapshots
    FOR EACH ROW
    EXECUTE FUNCTION legal_stamp_evidence_snapshot_transaction();

CREATE OR REPLACE FUNCTION legal_assert_gate_eligible_snapshot_items()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_gate_snapshot_items$
DECLARE
    has_evidence_items BOOLEAN;
    has_invalid_evidence_items BOOLEAN;
    actual_citation_count BIGINT;
    actual_claim_count BIGINT;
    has_invalid_lineage BOOLEAN;
BEGIN
    IF NOT NEW.gate_eligible THEN
        RETURN NEW;
    END IF;

    IF (
        NEW.metadata_json -> 'coverageComplete'
        IS DISTINCT FROM 'true'::JSONB
    ) THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % requires complete coverage',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_coverage';
    END IF;

    IF (
        jsonb_typeof(
            NEW.metadata_json -> 'expectedCitationCount'
        ) IS DISTINCT FROM 'number'
        OR jsonb_typeof(
            NEW.metadata_json -> 'sealedCitationCount'
        ) IS DISTINCT FROM 'number'
    ) THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % requires numeric citation coverage counts',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_coverage';
    END IF;

    IF (
        (NEW.metadata_json ->> 'expectedCitationCount')::NUMERIC < 1
        OR (
            (NEW.metadata_json ->> 'expectedCitationCount')::NUMERIC
            <> TRUNC(
                (NEW.metadata_json ->> 'expectedCitationCount')::NUMERIC
            )
        )
        OR (
            (NEW.metadata_json ->> 'sealedCitationCount')::NUMERIC
            <> TRUNC(
                (NEW.metadata_json ->> 'sealedCitationCount')::NUMERIC
            )
        )
        OR (
            (NEW.metadata_json ->> 'sealedCitationCount')::NUMERIC
            <> (NEW.metadata_json ->> 'expectedCitationCount')::NUMERIC
        )
    ) THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % requires complete positive integer citation coverage',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_coverage';
    END IF;

    IF (
        NEW.metadata_json -> 'claimCoverageComplete'
        IS DISTINCT FROM 'true'::JSONB
    ) THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % requires complete claim coverage',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_claim_coverage';
    END IF;

    IF (
        jsonb_typeof(
            NEW.metadata_json -> 'expectedClaimCount'
        ) IS DISTINCT FROM 'number'
        OR jsonb_typeof(
            NEW.metadata_json -> 'verifiedClaimCount'
        ) IS DISTINCT FROM 'number'
    ) THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % requires numeric claim coverage counts',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_claim_coverage';
    END IF;

    IF (
        (NEW.metadata_json ->> 'expectedClaimCount')::NUMERIC < 1
        OR (
            (NEW.metadata_json ->> 'expectedClaimCount')::NUMERIC
            <> TRUNC(
                (NEW.metadata_json ->> 'expectedClaimCount')::NUMERIC
            )
        )
        OR (NEW.metadata_json ->> 'verifiedClaimCount')::NUMERIC < 0
        OR (
            (NEW.metadata_json ->> 'verifiedClaimCount')::NUMERIC
            <> TRUNC(
                (NEW.metadata_json ->> 'verifiedClaimCount')::NUMERIC
            )
        )
        OR (
            (NEW.metadata_json ->> 'verifiedClaimCount')::NUMERIC
            <> (NEW.metadata_json ->> 'expectedClaimCount')::NUMERIC
        )
    ) THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % requires complete integer claim coverage',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_claim_coverage';
    END IF;

    EXECUTE pg_catalog.format(
        $snapshot_items$
        SELECT EXISTS (
            SELECT 1
              FROM %I.legal_evidence_snapshot_items AS item
             WHERE item.legal_evidence_snapshot_id = $1
               AND item.tenant_id = $2
               AND item.project_id = $3
        )
        $snapshot_items$,
        TG_TABLE_SCHEMA
    )
    INTO has_evidence_items
    USING
        NEW.legal_evidence_snapshot_id,
        NEW.tenant_id,
        NEW.project_id;

    IF NOT has_evidence_items THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % must contain evidence',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_items';
    END IF;

    EXECUTE pg_catalog.format(
        $invalid_snapshot_items$
        SELECT EXISTS (
            SELECT 1
              FROM %I.legal_evidence_snapshot_items AS item
             WHERE item.legal_evidence_snapshot_id = $1
               AND item.tenant_id = $2
               AND item.project_id = $3
               AND (
                   item.citation_verification_status <> 'verified'
                   OR item.verified_at IS NULL
               )
        )
        $invalid_snapshot_items$,
        TG_TABLE_SCHEMA
    )
    INTO has_invalid_evidence_items
    USING
        NEW.legal_evidence_snapshot_id,
        NEW.tenant_id,
        NEW.project_id;

    IF has_invalid_evidence_items THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % contains unverified evidence',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_items';
    END IF;

    -- Do not trust caller-supplied coverage counts or an item's own verified
    -- flag. Gate evidence must resolve to an exact provision in an independently
    -- verified corpus version produced by a successful immutable fetch.
    EXECUTE pg_catalog.format(
        $snapshot_item_lineage$
        SELECT
            COUNT(
                DISTINCT (
                    item.legal_resource_id,
                    item.legal_resource_version_id,
                    item.legal_provision_id
                )
            ),
            COUNT(DISTINCT item.claim_text_sha256),
            COALESCE(
                BOOL_OR(
                    item.legal_provision_id IS NULL
                    OR resource_version.legal_resource_version_id IS NULL
                    OR resource_version.verification_status NOT IN (
                        'source_verified',
                        'schema_verified'
                    )
                    OR resource_version.verified_at IS NULL
                    OR resource_version.source_fetch_id
                        IS DISTINCT FROM item.source_fetch_id
                    OR source_fetch.source_fetch_id IS NULL
                    OR source_fetch.fetch_status NOT IN (
                        'succeeded',
                        'not_modified'
                    )
                    OR provision.legal_provision_id IS NULL
                ),
                FALSE
            )
          FROM %1$I.legal_evidence_snapshot_items AS item
          LEFT JOIN %1$I.legal_resource_versions AS resource_version
            ON resource_version.legal_resource_version_id =
                   item.legal_resource_version_id
           AND resource_version.legal_resource_id = item.legal_resource_id
          LEFT JOIN %1$I.legal_source_fetches AS source_fetch
            ON source_fetch.source_fetch_id = item.source_fetch_id
          LEFT JOIN %1$I.legal_provisions AS provision
            ON provision.legal_provision_id = item.legal_provision_id
           AND provision.legal_resource_version_id =
                   item.legal_resource_version_id
           AND provision.legal_resource_id = item.legal_resource_id
         WHERE item.legal_evidence_snapshot_id = $1
           AND item.tenant_id = $2
           AND item.project_id = $3
        $snapshot_item_lineage$,
        TG_TABLE_SCHEMA
    )
    INTO
        actual_citation_count,
        actual_claim_count,
        has_invalid_lineage
    USING
        NEW.legal_evidence_snapshot_id,
        NEW.tenant_id,
        NEW.project_id;

    IF has_invalid_lineage THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % requires exact-provision verified corpus lineage',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_lineage';
    END IF;

    IF (
        actual_citation_count::NUMERIC
            <> (NEW.metadata_json ->> 'sealedCitationCount')::NUMERIC
        OR actual_citation_count::NUMERIC
            <> (NEW.metadata_json ->> 'expectedCitationCount')::NUMERIC
        OR actual_claim_count::NUMERIC
            <> (NEW.metadata_json ->> 'verifiedClaimCount')::NUMERIC
        OR actual_claim_count::NUMERIC
            <> (NEW.metadata_json ->> 'expectedClaimCount')::NUMERIC
    ) THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % coverage counts do not match distinct evidence items (citations %, claims %)',
            NEW.legal_evidence_snapshot_id,
            actual_citation_count,
            actual_claim_count
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_gate_item_counts';
    END IF;

    RETURN NEW;
END;
$legal_gate_snapshot_items$;

DROP TRIGGER IF EXISTS ctrg_legal_evidence_snapshot_gate_items
    ON legal_evidence_snapshots;
CREATE CONSTRAINT TRIGGER ctrg_legal_evidence_snapshot_gate_items
    AFTER INSERT ON legal_evidence_snapshots
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_gate_eligible_snapshot_items();

-- Append-only snapshots may receive their items later in the creating
-- transaction. Once that transaction commits, their evidence membership is
-- immutable regardless of a later item's verification status.
CREATE OR REPLACE FUNCTION legal_assert_gate_eligible_item_verified()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_gate_item_verified$
DECLARE
    parent_gate_eligible BOOLEAN;
    parent_creation_txid BIGINT;
BEGIN
    EXECUTE pg_catalog.format(
        $parent_snapshot$
        SELECT
            snapshot.gate_eligible,
            snapshot.creation_txid
          FROM %I.legal_evidence_snapshots AS snapshot
         WHERE snapshot.legal_evidence_snapshot_id = $1
           AND snapshot.tenant_id = $2
           AND snapshot.project_id = $3
        $parent_snapshot$,
        TG_TABLE_SCHEMA
    )
    INTO
        parent_gate_eligible,
        parent_creation_txid
    USING
        NEW.legal_evidence_snapshot_id,
        NEW.tenant_id,
        NEW.project_id;

    IF parent_creation_txid IS NULL THEN
        RAISE EXCEPTION
            'legal evidence snapshot % is not visible in the item scope',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23503',
                  CONSTRAINT = 'fk_legal_evidence_item_snapshot_scope';
    END IF;

    IF parent_creation_txid <> pg_catalog.txid_current() THEN
        RAISE EXCEPTION
            'legal evidence snapshot % is sealed and its item membership is immutable',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_snapshot_items_immutable';
    END IF;

    IF NOT parent_gate_eligible THEN
        RETURN NEW;
    END IF;

    IF (
        NEW.citation_verification_status <> 'verified'
        OR NEW.verified_at IS NULL
    ) THEN
        RAISE EXCEPTION
            'gate-eligible legal evidence snapshot % cannot contain unverified evidence',
            NEW.legal_evidence_snapshot_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_evidence_item_gate_verified';
    END IF;

    RETURN NEW;
END;
$legal_gate_item_verified$;

DROP TRIGGER IF EXISTS ctrg_legal_evidence_item_gate_verified
    ON legal_evidence_snapshot_items;
CREATE CONSTRAINT TRIGGER ctrg_legal_evidence_item_gate_verified
    AFTER INSERT ON legal_evidence_snapshot_items
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_gate_eligible_item_verified();

-- An approval is authoritative only when it is pinned to the same tenant and
-- project evidence package and that package is fully verified, conflict free,
-- non-stale, and non-empty at transaction commit.
CREATE OR REPLACE FUNCTION legal_assert_approval_snapshot_eligible()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_approval_snapshot_eligible$
DECLARE
    snapshot_is_eligible BOOLEAN;
BEGIN
    IF NEW.decision_type <> 'approve' THEN
        RETURN NEW;
    END IF;

    IF NEW.legal_evidence_snapshot_id IS NULL THEN
        RAISE EXCEPTION
            'approve legal review decision % requires an evidence snapshot',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_review_approval_snapshot';
    END IF;

    IF (
        jsonb_typeof(
            NEW.metadata_json -> 'legalReviewId'
        ) IS DISTINCT FROM 'string'
        OR NULLIF(
            btrim(NEW.metadata_json ->> 'legalReviewId'),
            ''
        ) IS NULL
    ) THEN
        RAISE EXCEPTION
            'approve legal review decision % requires a legalReviewId',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_review_approval_snapshot';
    END IF;

    EXECUTE pg_catalog.format(
        $eligible_snapshot$
        SELECT EXISTS (
            SELECT 1
              FROM %1$I.legal_evidence_snapshots AS snapshot
             WHERE snapshot.legal_evidence_snapshot_id = $1
               AND snapshot.tenant_id = $2
               AND snapshot.project_id = $3
               AND snapshot.gate_eligible
               AND snapshot.citation_verification_status = 'verified'
               AND NOT snapshot.has_open_conflicts
               AND NOT snapshot.is_stale
               AND pg_catalog.jsonb_typeof(
                   snapshot.metadata_json -> 'legalReviewId'
               ) = 'string'
               AND snapshot.metadata_json ->> 'legalReviewId' = $4
               AND EXISTS (
                   SELECT 1
                     FROM %1$I.legal_evidence_snapshot_items AS item
                    WHERE item.legal_evidence_snapshot_id =
                              snapshot.legal_evidence_snapshot_id
                      AND item.tenant_id = snapshot.tenant_id
                      AND item.project_id = snapshot.project_id
                      AND item.citation_verification_status = 'verified'
                      AND item.verified_at IS NOT NULL
               )
               AND NOT EXISTS (
                   SELECT 1
                     FROM %1$I.legal_evidence_snapshot_items AS item
                    WHERE item.legal_evidence_snapshot_id =
                              snapshot.legal_evidence_snapshot_id
                      AND item.tenant_id = snapshot.tenant_id
                      AND item.project_id = snapshot.project_id
                      AND (
                          item.citation_verification_status <> 'verified'
                          OR item.verified_at IS NULL
                      )
               )
        )
        $eligible_snapshot$,
        TG_TABLE_SCHEMA
    )
    INTO snapshot_is_eligible
    USING
        NEW.legal_evidence_snapshot_id,
        NEW.tenant_id,
        NEW.project_id,
        NEW.metadata_json ->> 'legalReviewId';

    IF NOT snapshot_is_eligible THEN
        RAISE EXCEPTION
            'approve legal review decision % requires a same-scope gate-eligible verified non-stale conflict-free evidence snapshot',
            NEW.legal_review_decision_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_legal_review_approval_snapshot';
    END IF;

    RETURN NEW;
END;
$legal_approval_snapshot_eligible$;

DROP TRIGGER IF EXISTS ctrg_legal_review_approval_snapshot
    ON legal_review_decisions;
CREATE CONSTRAINT TRIGGER ctrg_legal_review_approval_snapshot
    AFTER INSERT ON legal_review_decisions
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_approval_snapshot_eligible();

-- Completion is trusted by the serving runtime, so the database independently
-- verifies the ordered checkpoint chain instead of trusting worker metadata.
CREATE OR REPLACE FUNCTION legal_assert_corpus_completion_evidence()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_completion_evidence$
DECLARE
    actual_page_count         BIGINT;
    actual_record_count       BIGINT;
    actual_resource_count     BIGINT;
    actual_version_refs       BIGINT;
    unique_version_refs       BIGINT;
    unique_page_hashes        BIGINT;
    missing_version_refs      BIGINT;
    invalid_fetch_refs        BIGINT;
    discontinuity_count       BIGINT;
    mismatched_scope_count    BIGINT;
    actual_page_hashes        JSONB;
    actual_terminal_checkpoint TEXT;
    first_checkpoint_occurred_at TIMESTAMPTZ;
    last_checkpoint_occurred_at TIMESTAMPTZ;
BEGIN
    EXECUTE pg_catalog.format(
        $completion_envelope$
        SELECT
            COUNT(*),
            COALESCE(SUM(checkpoint.page_record_count), 0),
            COUNT(*) FILTER (
                WHERE checkpoint.requested_scope IS DISTINCT FROM $5
            ),
            COUNT(DISTINCT checkpoint.page_hash),
            jsonb_agg(
                checkpoint.page_hash
                ORDER BY checkpoint.checkpoint_sequence
            ),
            (
                array_agg(
                    checkpoint.checkpoint_after
                    ORDER BY checkpoint.checkpoint_sequence DESC
                )
            )[1],
            MIN(checkpoint.occurred_at),
            MAX(checkpoint.occurred_at)
        FROM %I.legal_sync_checkpoint_events AS checkpoint
        WHERE checkpoint.provider_id = $1
          AND checkpoint.requested_scope_hash = $2
          AND checkpoint.checkpoint_sequence BETWEEN $3 AND $4
        $completion_envelope$,
        TG_TABLE_SCHEMA
    )
    INTO
        actual_page_count,
        actual_record_count,
        mismatched_scope_count,
        unique_page_hashes,
        actual_page_hashes,
        actual_terminal_checkpoint,
        first_checkpoint_occurred_at,
        last_checkpoint_occurred_at
    USING
        NEW.provider_id,
        NEW.requested_scope_hash,
        NEW.first_checkpoint_sequence,
        NEW.last_checkpoint_sequence,
        NEW.requested_scope;

    IF actual_page_count <> NEW.page_count
       OR actual_record_count <> NEW.record_count
       OR mismatched_scope_count <> 0
       OR unique_page_hashes <> NEW.page_count
       OR actual_page_hashes IS DISTINCT FROM NEW.page_hashes_json
       OR actual_terminal_checkpoint IS DISTINCT FROM
          NEW.terminal_checkpoint
       OR NEW.started_at > first_checkpoint_occurred_at
       OR NEW.completed_at < last_checkpoint_occurred_at
       OR (
            NEW.last_checkpoint_sequence
            - NEW.first_checkpoint_sequence
            + 1
          ) <> NEW.page_count
    THEN
        RAISE EXCEPTION
            'legal corpus completion does not match checkpoint envelope'
            USING ERRCODE = '23514';
    END IF;

    EXECUTE pg_catalog.format(
        $completion_chain$
        SELECT COUNT(*)
        FROM %1$I.legal_sync_checkpoint_events AS current_checkpoint
        JOIN %1$I.legal_sync_checkpoint_events AS prior_checkpoint
          ON prior_checkpoint.provider_id =
             current_checkpoint.provider_id
         AND prior_checkpoint.requested_scope_hash =
             current_checkpoint.requested_scope_hash
         AND prior_checkpoint.checkpoint_sequence =
             current_checkpoint.checkpoint_sequence - 1
        WHERE current_checkpoint.provider_id = $1
          AND current_checkpoint.requested_scope_hash = $2
          AND current_checkpoint.checkpoint_sequence BETWEEN
              $3 + 1 AND $4
          AND current_checkpoint.checkpoint_before IS DISTINCT FROM
              prior_checkpoint.checkpoint_after
        $completion_chain$,
        TG_TABLE_SCHEMA
    )
    INTO discontinuity_count
    USING
        NEW.provider_id,
        NEW.requested_scope_hash,
        NEW.first_checkpoint_sequence,
        NEW.last_checkpoint_sequence;

    IF discontinuity_count <> 0 THEN
        RAISE EXCEPTION
            'legal corpus completion checkpoint chain is discontinuous'
            USING ERRCODE = '23514';
    END IF;

    EXECUTE pg_catalog.format(
        $completion_records$
        SELECT
            COUNT(*),
            COUNT(*) FILTER (
                WHERE version.legal_resource_version_id IS NULL
            ),
            COUNT(DISTINCT version_ref.legal_resource_version_id),
            COUNT(DISTINCT version.legal_resource_id)
        FROM %1$I.legal_sync_checkpoint_events AS checkpoint
        CROSS JOIN LATERAL jsonb_array_elements_text(
            CASE
                WHEN jsonb_typeof(
                    checkpoint.metadata_json
                    -> 'legalResourceVersionIds'
                ) = 'array'
                THEN checkpoint.metadata_json
                     -> 'legalResourceVersionIds'
                ELSE '[]'::JSONB
            END
        ) AS version_ref(legal_resource_version_id)
        LEFT JOIN %1$I.legal_resource_versions AS version
          ON version.legal_resource_version_id =
             version_ref.legal_resource_version_id
        WHERE checkpoint.provider_id = $1
          AND checkpoint.requested_scope_hash = $2
          AND checkpoint.checkpoint_sequence BETWEEN $3 AND $4
        $completion_records$,
        TG_TABLE_SCHEMA
    )
    INTO
        actual_version_refs,
        missing_version_refs,
        unique_version_refs,
        actual_resource_count
    USING
        NEW.provider_id,
        NEW.requested_scope_hash,
        NEW.first_checkpoint_sequence,
        NEW.last_checkpoint_sequence;

    IF actual_version_refs <> NEW.record_count
       OR missing_version_refs <> 0
       OR unique_version_refs <> actual_version_refs
       OR actual_resource_count <> NEW.resource_count
    THEN
        RAISE EXCEPTION
            'legal corpus completion record manifest is incomplete'
            USING ERRCODE = '23514';
    END IF;

    EXECUTE pg_catalog.format(
        $completion_fetches$
        SELECT COUNT(*)
        FROM %1$I.legal_sync_checkpoint_events AS checkpoint
        WHERE checkpoint.provider_id = $1
          AND checkpoint.requested_scope_hash = $2
          AND checkpoint.checkpoint_sequence BETWEEN $3 AND $4
          AND NOT EXISTS (
              SELECT 1
              FROM %1$I.legal_source_fetches AS fetch
              WHERE fetch.source_fetch_id =
                    checkpoint.metadata_json ->> 'sourceFetchId'
                AND fetch.provider_id = checkpoint.provider_id
                AND fetch.sync_run_id = checkpoint.sync_run_id
          )
        $completion_fetches$,
        TG_TABLE_SCHEMA
    )
    INTO invalid_fetch_refs
    USING
        NEW.provider_id,
        NEW.requested_scope_hash,
        NEW.first_checkpoint_sequence,
        NEW.last_checkpoint_sequence;

    IF invalid_fetch_refs <> 0 THEN
        RAISE EXCEPTION
            'legal corpus completion source fetch evidence is invalid'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$legal_completion_evidence$;

DROP TRIGGER IF EXISTS ctrg_legal_corpus_completion_evidence
    ON legal_corpus_scope_completion_attestations;
CREATE CONSTRAINT TRIGGER ctrg_legal_corpus_completion_evidence
    AFTER INSERT ON legal_corpus_scope_completion_attestations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_corpus_completion_evidence();

CREATE OR REPLACE FUNCTION legal_assert_raw_reconciliation_reference()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $legal_raw_reconciliation_reference$
DECLARE
    fetched_object_key       TEXT;
    fetched_object_version   TEXT;
    fetched_sha256           TEXT;
    fetched_size_bytes       BIGINT;
    fetched_sync_run_id      TEXT;
BEGIN
    IF NEW.source_fetch_id IS NULL THEN
        IF NEW.observation_status = 'referenced' THEN
            RAISE EXCEPTION
                'referenced raw object requires an exact source fetch'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    EXECUTE pg_catalog.format(
        $reconciliation_fetch$
        SELECT
            raw_object_key,
            raw_object_version,
            raw_sha256,
            raw_size_bytes,
            sync_run_id
        FROM %I.legal_source_fetches
        WHERE source_fetch_id = $1
        $reconciliation_fetch$,
        TG_TABLE_SCHEMA
    )
    INTO
        fetched_object_key,
        fetched_object_version,
        fetched_sha256,
        fetched_size_bytes,
        fetched_sync_run_id
    USING NEW.source_fetch_id;

    IF NOT FOUND
       OR fetched_object_key IS DISTINCT FROM NEW.raw_object_key
       OR fetched_object_version IS DISTINCT FROM
          NEW.raw_object_version
       OR fetched_sha256 IS DISTINCT FROM NEW.raw_sha256
       OR fetched_size_bytes IS DISTINCT FROM NEW.raw_size_bytes
       OR fetched_sync_run_id IS DISTINCT FROM NEW.sync_run_id
       OR NEW.observation_status <> 'referenced'
    THEN
        RAISE EXCEPTION
            'raw object reconciliation does not match source fetch evidence'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$legal_raw_reconciliation_reference$;

DROP TRIGGER IF EXISTS ctrg_legal_raw_reconciliation_reference
    ON legal_raw_object_reconciliation_items;
CREATE CONSTRAINT TRIGGER ctrg_legal_raw_reconciliation_reference
    AFTER INSERT ON legal_raw_object_reconciliation_items
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION legal_assert_raw_reconciliation_reference();

-- ---------------------------------------------------------------------------
-- Append-only enforcement
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION legal_reject_append_only_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $legal_append_only$
BEGIN
    RAISE EXCEPTION
        'table % is append-only; create a superseding row or event',
        TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$legal_append_only$;

DROP TRIGGER IF EXISTS trg_legal_source_providers_append_only
    ON legal_source_providers;
CREATE TRIGGER trg_legal_source_providers_append_only
    BEFORE UPDATE OR DELETE ON legal_source_providers
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_sync_runs_append_only
    ON legal_sync_runs;
CREATE TRIGGER trg_legal_sync_runs_append_only
    BEFORE UPDATE OR DELETE ON legal_sync_runs
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_sync_run_events_append_only
    ON legal_sync_run_events;
CREATE TRIGGER trg_legal_sync_run_events_append_only
    BEFORE UPDATE OR DELETE ON legal_sync_run_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_sync_scope_lease_events_append_only
    ON legal_sync_scope_lease_events;
CREATE TRIGGER trg_legal_sync_scope_lease_events_append_only
    BEFORE UPDATE OR DELETE ON legal_sync_scope_lease_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_sync_checkpoint_events_append_only
    ON legal_sync_checkpoint_events;
CREATE TRIGGER trg_legal_sync_checkpoint_events_append_only
    BEFORE UPDATE OR DELETE ON legal_sync_checkpoint_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_corpus_scope_completion_attestations_append_only
    ON legal_corpus_scope_completion_attestations;
CREATE TRIGGER trg_legal_corpus_scope_completion_attestations_append_only
    BEFORE UPDATE OR DELETE ON legal_corpus_scope_completion_attestations
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_source_fetches_append_only
    ON legal_source_fetches;
CREATE TRIGGER trg_legal_source_fetches_append_only
    BEFORE UPDATE OR DELETE ON legal_source_fetches
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_raw_object_reconciliation_runs_append_only
    ON legal_raw_object_reconciliation_runs;
CREATE TRIGGER trg_legal_raw_object_reconciliation_runs_append_only
    BEFORE UPDATE OR DELETE ON legal_raw_object_reconciliation_runs
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_raw_object_reconciliation_items_append_only
    ON legal_raw_object_reconciliation_items;
CREATE TRIGGER trg_legal_raw_object_reconciliation_items_append_only
    BEFORE UPDATE OR DELETE ON legal_raw_object_reconciliation_items
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_resources_append_only
    ON legal_resources;
CREATE TRIGGER trg_legal_resources_append_only
    BEFORE UPDATE OR DELETE ON legal_resources
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_resource_identity_crosswalks_append_only
    ON legal_resource_identity_crosswalks;
CREATE TRIGGER trg_legal_resource_identity_crosswalks_append_only
    BEFORE UPDATE OR DELETE ON legal_resource_identity_crosswalks
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_resource_versions_append_only
    ON legal_resource_versions;
CREATE TRIGGER trg_legal_resource_versions_append_only
    BEFORE UPDATE OR DELETE ON legal_resource_versions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_resource_version_lineages_append_only
    ON legal_resource_version_lineages;
CREATE TRIGGER trg_legal_resource_version_lineages_append_only
    BEFORE UPDATE OR DELETE ON legal_resource_version_lineages
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_provisions_append_only
    ON legal_provisions;
CREATE TRIGGER trg_legal_provisions_append_only
    BEFORE UPDATE OR DELETE ON legal_provisions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_citations_append_only
    ON legal_citations;
CREATE TRIGGER trg_legal_citations_append_only
    BEFORE UPDATE OR DELETE ON legal_citations
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_relation_assertions_append_only
    ON legal_relation_assertions;
CREATE TRIGGER trg_legal_relation_assertions_append_only
    BEFORE UPDATE OR DELETE ON legal_relation_assertions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_term_definitions_append_only
    ON legal_term_definitions;
CREATE TRIGGER trg_legal_term_definitions_append_only
    BEFORE UPDATE OR DELETE ON legal_term_definitions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_term_provision_links_append_only
    ON legal_term_provision_links;
CREATE TRIGGER trg_legal_term_provision_links_append_only
    BEFORE UPDATE OR DELETE ON legal_term_provision_links
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_semantic_artifacts_append_only
    ON legal_semantic_artifacts;
CREATE TRIGGER trg_legal_semantic_artifacts_append_only
    BEFORE UPDATE OR DELETE ON legal_semantic_artifacts
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_query_runs_append_only
    ON legal_query_runs;
CREATE TRIGGER trg_legal_query_runs_append_only
    BEFORE UPDATE OR DELETE ON legal_query_runs
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_query_run_events_append_only
    ON legal_query_run_events;
CREATE TRIGGER trg_legal_query_run_events_append_only
    BEFORE UPDATE OR DELETE ON legal_query_run_events
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_project_legal_resource_links_append_only
    ON project_legal_resource_links;
CREATE TRIGGER trg_project_legal_resource_links_append_only
    BEFORE UPDATE OR DELETE ON project_legal_resource_links
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_findings_append_only
    ON legal_findings;
CREATE TRIGGER trg_legal_findings_append_only
    BEFORE UPDATE OR DELETE ON legal_findings
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_evidence_snapshots_append_only
    ON legal_evidence_snapshots;
CREATE TRIGGER trg_legal_evidence_snapshots_append_only
    BEFORE UPDATE OR DELETE ON legal_evidence_snapshots
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_evidence_snapshot_items_append_only
    ON legal_evidence_snapshot_items;
CREATE TRIGGER trg_legal_evidence_snapshot_items_append_only
    BEFORE UPDATE OR DELETE ON legal_evidence_snapshot_items
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

DROP TRIGGER IF EXISTS trg_legal_review_decisions_append_only
    ON legal_review_decisions;
CREATE TRIGGER trg_legal_review_decisions_append_only
    BEFORE UPDATE OR DELETE ON legal_review_decisions
    FOR EACH ROW EXECUTE FUNCTION legal_reject_append_only_mutation();

-- ---------------------------------------------------------------------------
-- Fail-closed tenant isolation
-- ---------------------------------------------------------------------------
-- Every tenant transaction must first execute:
--   SELECT set_config('realytics.tenant_id', '<tenant-id>', true);
-- Missing and explicitly empty settings both deny reads and writes.

ALTER TABLE legal_query_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_query_runs FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_tenant_isolation ON legal_query_runs;
CREATE POLICY legal_tenant_isolation ON legal_query_runs
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_query_run_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_query_run_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_tenant_isolation ON legal_query_run_events;
CREATE POLICY legal_tenant_isolation ON legal_query_run_events
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE project_legal_resource_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_legal_resource_links FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_tenant_isolation ON project_legal_resource_links;
CREATE POLICY legal_tenant_isolation ON project_legal_resource_links
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_findings ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_findings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_tenant_isolation ON legal_findings;
CREATE POLICY legal_tenant_isolation ON legal_findings
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_evidence_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_evidence_snapshots FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_tenant_isolation ON legal_evidence_snapshots;
CREATE POLICY legal_tenant_isolation ON legal_evidence_snapshots
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_evidence_snapshot_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_evidence_snapshot_items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_tenant_isolation ON legal_evidence_snapshot_items;
CREATE POLICY legal_tenant_isolation ON legal_evidence_snapshot_items
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

ALTER TABLE legal_review_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE legal_review_decisions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS legal_tenant_isolation ON legal_review_decisions;
CREATE POLICY legal_tenant_isolation ON legal_review_decisions
    FOR ALL
    USING (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    )
    WITH CHECK (
        tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')
    );

-- Private-schema wrappers also revoke Supabase roles. This canonical migration
-- keeps a safe baseline for non-Supabase deployments.
REVOKE ALL ON TABLE
    legal_source_providers,
    legal_sync_runs,
    legal_sync_run_events,
    legal_sync_scope_lease_events,
    legal_sync_checkpoint_events,
    legal_corpus_scope_completion_attestations,
    legal_source_fetches,
    legal_raw_object_reconciliation_runs,
    legal_raw_object_reconciliation_items,
    legal_resources,
    legal_resource_identity_crosswalks,
    legal_resource_versions,
    legal_resource_version_lineages,
    legal_provisions,
    legal_citations,
    legal_relation_assertions,
    legal_term_definitions,
    legal_term_provision_links,
    legal_semantic_artifacts,
    legal_query_runs,
    legal_query_run_events,
    project_legal_resource_links,
    legal_findings,
    legal_evidence_snapshots,
    legal_evidence_snapshot_items,
    legal_review_decisions
FROM PUBLIC;
