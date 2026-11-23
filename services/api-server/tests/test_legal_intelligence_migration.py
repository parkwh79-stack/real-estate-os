from __future__ import annotations

from db import postgres


MIGRATION = (
    postgres.MIGRATIONS_DIR / "0008_legal_intelligence_core.sql"
).read_text(encoding="utf-8")

GLOBAL_APPEND_ONLY_TABLES = (
    "legal_source_providers",
    "legal_sync_runs",
    "legal_sync_run_events",
    "legal_sync_scope_lease_events",
    "legal_sync_checkpoint_events",
    "legal_corpus_scope_completion_attestations",
    "legal_source_fetches",
    "legal_raw_object_reconciliation_runs",
    "legal_raw_object_reconciliation_items",
    "legal_resources",
    "legal_resource_identity_crosswalks",
    "legal_resource_versions",
    "legal_resource_version_lineages",
    "legal_provisions",
    "legal_citations",
    "legal_relation_assertions",
    "legal_term_definitions",
    "legal_term_provision_links",
    "legal_semantic_artifacts",
)

TENANT_APPEND_ONLY_TABLES = (
    "legal_query_runs",
    "legal_query_run_events",
    "project_legal_resource_links",
    "legal_findings",
    "legal_evidence_snapshots",
    "legal_evidence_snapshot_items",
    "legal_review_decisions",
)


def test_legal_intelligence_migration_has_global_and_tenant_boundaries() -> None:
    for table in GLOBAL_APPEND_ONLY_TABLES + TENANT_APPEND_ONLY_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in MIGRATION

    for table in GLOBAL_APPEND_ONLY_TABLES:
        table_body = MIGRATION.split(
            f"CREATE TABLE IF NOT EXISTS {table} (", 1
        )[1].split("\n);", 1)[0]
        assert "tenant_id" not in table_body

    for table in TENANT_APPEND_ONLY_TABLES:
        table_body = MIGRATION.split(
            f"CREATE TABLE IF NOT EXISTS {table} (", 1
        )[1].split("\n);", 1)[0]
        assert "tenant_id" in table_body
        assert "project_id" in table_body
        assert "REFERENCES projects(project_id, tenant_id)" in table_body


def test_legal_intelligence_uses_trigram_without_forcing_pgvector() -> None:
    assert (
        "CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;"
        in MIGRATION
    )
    assert "installed_schema IS DISTINCT FROM 'extensions'" in MIGRATION
    assert "extensions.gin_trgm_ops" in MIGRATION
    assert "CREATE EXTENSION IF NOT EXISTS vector" not in MIGRATION
    assert "CREATE EXTENSION IF NOT EXISTS pgvector" not in MIGRATION

    assert "idx_legal_resources_exact_identifier" in MIGRATION
    assert "idx_legal_resources_identifier_trgm" in MIGRATION
    assert "idx_legal_versions_title_trgm" in MIGRATION
    assert "idx_legal_versions_full_text" in MIGRATION
    assert "idx_legal_provisions_identifier_trgm" in MIGRATION
    assert "idx_legal_provisions_full_text" in MIGRATION
    assert MIGRATION.count("TSVECTOR GENERATED ALWAYS AS") == 2


def test_legal_sync_pages_use_fenced_leases_and_append_only_checkpoint_cas() -> None:
    run_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_sync_runs (", 1
    )[1].split("\n);", 1)[0]
    lease_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_sync_scope_lease_events (", 1
    )[1].split("\n);", 1)[0]
    checkpoint_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_sync_checkpoint_events (", 1
    )[1].split("\n);", 1)[0]

    assert "requested_scope_hash" in run_body
    assert "page_hash" in run_body
    assert "page_record_count" in run_body
    assert "requested_scope_hash ~ '^[0-9a-f]{64}$'" in run_body
    assert "page_hash ~ '^[0-9a-f]{64}$'" in run_body
    assert "page_record_count >= 1" in run_body
    assert (
        "sync_run_id, provider_id, requested_scope_hash, requested_scope"
        in run_body
    )

    assert "fencing_token" in lease_body
    assert "prior_lease_event_id" in lease_body
    assert "'acquired', 'renewed', 'released'" in lease_body
    assert "expires_at > occurred_at" in lease_body
    assert "expires_at = occurred_at" in lease_body
    assert "requested_scope_hash, requested_scope" in lease_body
    assert (
        "provider_id, requested_scope_hash, event_sequence"
        in lease_body
    )

    assert "checkpoint_before" in checkpoint_body
    assert "checkpoint_after" in checkpoint_body
    assert "page_hash" in checkpoint_body
    assert "page_record_count" in checkpoint_body
    assert "ck_legal_sync_checkpoint_exact_manifest" in checkpoint_body
    assert "sourceFetchId" in checkpoint_body
    assert "legalResourceVersionIds" in checkpoint_body
    assert "= page_record_count" in checkpoint_body
    assert "UNIQUE (sync_run_id, provider_id)" in checkpoint_body
    assert "requested_scope_hash, requested_scope" in checkpoint_body
    assert (
        "provider_id, requested_scope_hash, checkpoint_sequence"
        in checkpoint_body
    )


def test_completion_attestation_rebuilds_checkpoint_and_fetch_evidence() -> None:
    completion_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS "
        "legal_corpus_scope_completion_attestations (",
        1,
    )[1].split("\n);", 1)[0]
    validator = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_corpus_completion_evidence()",
        1,
    )[1].split("$legal_completion_evidence$;", 1)[0]

    assert "first_checkpoint_sequence" in completion_body
    assert "last_checkpoint_sequence" in completion_body
    assert "terminal_checkpoint" in completion_body
    assert "page_hashes_json" in completion_body
    assert "manifest_sha256" in completion_body
    assert "verification_status = 'verified'" in completion_body
    assert "legal_sync_checkpoint_events AS checkpoint" in validator
    assert "completion checkpoint chain is discontinuous" in validator
    assert "legalResourceVersionIds" in validator
    assert "legal_source_fetches AS fetch" in validator
    assert "SET search_path = pg_catalog, pg_temp" in MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_corpus_completion_evidence()",
        1,
    )[1].split("AS $legal_completion_evidence$", 1)[0]
    assert "ctrg_legal_corpus_completion_evidence" in MIGRATION


def test_raw_object_reconciliation_is_preserving_and_source_pinned() -> None:
    run_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS "
        "legal_raw_object_reconciliation_runs (",
        1,
    )[1].split("\n);", 1)[0]
    item_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS "
        "legal_raw_object_reconciliation_items (",
        1,
    )[1].split("\n);", 1)[0]
    validator = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_raw_reconciliation_reference()",
        1,
    )[1].split("$legal_raw_reconciliation_reference$;", 1)[0]

    assert "inventory_manifest_sha256" in run_body
    assert "'completed', 'partial'" in run_body
    assert "'orphan_preserved'" in item_body
    assert "'manual_disposition_required'" in item_body
    assert "retention_until > observed_at" in item_body
    assert "raw_object_key" in validator
    assert "raw_object_version" in validator
    assert "raw_sha256" in validator
    assert "raw_size_bytes" in validator
    assert "TG_TABLE_SCHEMA" in validator
    assert "ctrg_legal_raw_reconciliation_reference" in MIGRATION


def test_legal_corpus_keeps_authority_transport_and_raw_payload_separate() -> None:
    provider_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_source_providers (", 1
    )[1].split("\n);", 1)[0]
    fetch_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_source_fetches (", 1
    )[1].split("\n);", 1)[0]

    assert "source_authority" in provider_body
    assert "authority_tier" in provider_body
    assert "transport" not in provider_body
    assert "transport" in fetch_body
    assert "'direct_api'" in fetch_body
    assert "'self_hosted_mcp'" in fetch_body
    assert "'remote_mcp'" in fetch_body
    assert "raw_object_key" in fetch_body
    assert "raw_sha256" in fetch_body
    assert "raw_size_bytes" in fetch_body
    assert "success_has_immutable_object" in fetch_body
    assert "'quarantined'" in fetch_body
    assert "ck_legal_source_fetch_response_status" in fetch_body
    assert "response_status BETWEEN 100 AND 599" in fetch_body
    assert "ck_legal_source_fetch_status_response_semantics" in fetch_body
    assert "response_status BETWEEN 200 AND 299" in fetch_body
    assert "COALESCE(response_status = 304, FALSE)" in fetch_body
    assert "provider_etag" in fetch_body
    assert "provider_last_modified" in fetch_body
    assert "ck_legal_source_fetch_raw_object_tuple" in fetch_body
    assert (
        fetch_body.count(
            "btrim(COALESCE(raw_object_version, '')) <> ''"
        )
        == 2
    )
    assert (
        "fetch_status NOT IN (\n"
        "                'succeeded',\n"
        "                'not_modified',\n"
        "                'partial'"
        in fetch_body
    )
    assert "sanitized_source_locator" in fetch_body
    assert "request_fingerprint" in fetch_body


def test_legal_resource_versions_are_bitemporal_and_source_pinned() -> None:
    version_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_resource_versions (", 1
    )[1].split("\n);", 1)[0]

    for column in (
        "publication_date",
        "promulgation_date",
        "effective_from",
        "effective_to",
        "valid_time_start",
        "valid_time_end",
        "observed_at",
        "fetched_at",
        "verified_at",
        "source_fetch_id",
        "normalized_sha256",
        "parser_schema_version",
        "supersedes_version_id",
    ):
        assert column in version_body

    assert "fk_legal_version_source_fetch" in version_body
    assert "fk_legal_version_supersedes_same_resource" in version_body
    assert "ck_legal_version_system_time" in version_body


def test_official_resource_identity_survives_provider_revisions() -> None:
    resource_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_resources (", 1
    )[1].split("\n);", 1)[0]
    crosswalk_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_resource_identity_crosswalks (",
        1,
    )[1].split("\n);", 1)[0]
    version_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_resource_versions (", 1
    )[1].split("\n);", 1)[0]

    assert "cross_revision_key" in resource_body
    assert "'official-resource:v1:' || identity_hash" in resource_body
    assert "UNIQUE (cross_revision_key)" in resource_body
    assert "provider_id" in crosswalk_body
    assert "provider_official_identifier" in crosswalk_body
    assert "first_observed_source_fetch_id" in crosswalk_body
    assert "fk_legal_resource_crosswalk_resource" in crosswalk_body
    assert "fk_legal_resource_crosswalk_fetch" in crosswalk_body
    assert "FOREIGN KEY (legal_resource_id)" in version_body
    assert "fk_legal_version_resource_provider" not in version_body


def test_effective_time_lineage_is_separate_and_fail_closed() -> None:
    lineage_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_resource_version_lineages (",
        1,
    )[1].split("\n);", 1)[0]

    for column in (
        "effective_predecessor_version_id",
        "lineage_type",
        "continuity_status",
        "validation_policy",
        "predecessor_effective_to",
        "successor_effective_from",
        "derived_predecessor_effective_to",
        "gap_days",
    ):
        assert column in lineage_body
    assert "'source_correction'" in lineage_body
    assert "'effective_successor'" in lineage_body
    assert "'same_window_correction'" in lineage_body
    assert "'open_end_closed'" in lineage_body
    assert "'documented_gap'" in lineage_body
    assert "metadata_json ->> 'gapReason'" in lineage_body
    assert "fk_legal_version_lineage_predecessor" in lineage_body
    assert "uq_legal_version_lineages_successor_edge" in MIGRATION
    assert "WHERE lineage_type = 'effective_successor'" in MIGRATION
    validator = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_validate_resource_version_lineage()",
        1,
    )[1].split("$legal_effective_lineage$;", 1)[0]
    assert "source correction rewrites its official time window" in validator
    assert "successor_start <= predecessor_end" in validator
    assert "calculated_gap = successor_start - (predecessor_end + 1)" in validator
    assert "declaredPredecessorOfficialVersionIdentifier" in validator
    assert "trg_legal_validate_resource_version_lineage" in MIGRATION


def test_legal_terms_extend_the_existing_glossary_instead_of_forking_it() -> None:
    assert "CREATE TABLE IF NOT EXISTS legal_terms" not in MIGRATION
    assert MIGRATION.count(
        "REFERENCES glossary_terms(term_id) ON DELETE RESTRICT"
    ) == 2
    assert "CREATE TABLE IF NOT EXISTS legal_term_definitions" in MIGRATION
    assert "CREATE TABLE IF NOT EXISTS legal_term_provision_links" in MIGRATION
    assert "'official'" in MIGRATION
    assert "'industry_practice'" in MIGRATION
    assert "'realytics_standard'" in MIGRATION


def test_legal_tables_are_append_only_and_status_changes_are_evented() -> None:
    assert "CREATE OR REPLACE FUNCTION legal_reject_append_only_mutation()" in MIGRATION
    assert "BEFORE UPDATE OR DELETE" in MIGRATION
    assert "create a superseding row or event" in MIGRATION
    assert "legal_sync_run_events" in MIGRATION
    assert "legal_query_run_events" in MIGRATION

    for table in GLOBAL_APPEND_ONLY_TABLES + TENANT_APPEND_ONLY_TABLES:
        trigger_name = f"trg_{table}_append_only"
        assert f"DROP TRIGGER IF EXISTS {trigger_name}" in MIGRATION
        assert f"CREATE TRIGGER {trigger_name}" in MIGRATION
        assert f"BEFORE UPDATE OR DELETE ON {table}" in MIGRATION


def test_legal_tenant_rls_is_complete_and_fails_closed() -> None:
    tenant_expression = (
        "tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')"
    )

    assert MIGRATION.count("ENABLE ROW LEVEL SECURITY;") == len(
        TENANT_APPEND_ONLY_TABLES
    )
    assert MIGRATION.count("FORCE ROW LEVEL SECURITY;") == len(
        TENANT_APPEND_ONLY_TABLES
    )
    assert MIGRATION.count(
        "CREATE POLICY legal_tenant_isolation ON"
    ) == len(TENANT_APPEND_ONLY_TABLES)
    assert MIGRATION.count(tenant_expression) == 2 * len(
        TENANT_APPEND_ONLY_TABLES
    )

    for table in TENANT_APPEND_ONLY_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in MIGRATION
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in MIGRATION
        assert f"DROP POLICY IF EXISTS legal_tenant_isolation ON {table};" in MIGRATION
        assert f"CREATE POLICY legal_tenant_isolation ON {table}" in MIGRATION


def test_project_confidential_queries_cannot_request_remote_mcp() -> None:
    query_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_query_runs (", 1
    )[1].split("\n);", 1)[0]

    assert "ck_legal_query_remote_transport_classification" in query_body
    assert "'remote_mcp' = ANY(requested_transports)" in query_body
    assert "data_classification IN ('public', 'internal')" in query_body


def test_ai_findings_and_unverified_evidence_fail_approval_closed() -> None:
    finding_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_findings (", 1
    )[1].split("\n);", 1)[0]
    snapshot_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_evidence_snapshots (", 1
    )[1].split("\n);", 1)[0]

    assert "ck_legal_finding_ai_is_preliminary" in finding_body
    assert "analysis_only" in finding_body
    assert "NOT report_eligible" in finding_body
    assert "requires_counsel_review" in finding_body

    assert "ck_legal_evidence_snapshot_gate" in snapshot_body
    assert "citation_verification_status = 'verified'" in snapshot_body
    assert "AND NOT has_open_conflicts" in snapshot_body
    assert "AND NOT is_stale" in snapshot_body
    assert "creation_txid" in snapshot_body
    assert "DEFAULT pg_catalog.txid_current()" in snapshot_body


def test_evidence_item_source_fetch_must_match_its_resource_version() -> None:
    version_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_resource_versions (", 1
    )[1].split("\n);", 1)[0]
    item_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_evidence_snapshot_items (", 1
    )[1].split("\n);", 1)[0]

    assert "UNIQUE (source_fetch_id, legal_resource_version_id)" in version_body
    assert "CONSTRAINT fk_legal_evidence_item_fetch_version" in item_body
    assert (
        "FOREIGN KEY (source_fetch_id, legal_resource_version_id)"
        in item_body
    )


def test_gate_eligible_snapshot_requires_verified_items_at_commit() -> None:
    function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_gate_eligible_snapshot_items()",
        1,
    )[1].split("$legal_gate_snapshot_items$;", 1)[0]
    item_function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_gate_eligible_item_verified()",
        1,
    )[1].split("$legal_gate_item_verified$;", 1)[0]
    stamp_function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_stamp_evidence_snapshot_transaction()",
        1,
    )[1].split("$legal_snapshot_transaction$;", 1)[0]
    snapshot_trigger = MIGRATION.split(
        "CREATE CONSTRAINT TRIGGER ctrg_legal_evidence_snapshot_gate_items",
        1,
    )[1].split(";", 1)[0]
    item_trigger = MIGRATION.split(
        "CREATE CONSTRAINT TRIGGER ctrg_legal_evidence_item_gate_verified",
        1,
    )[1].split(";", 1)[0]

    assert "IF NOT NEW.gate_eligible" in function_body
    assert (
        "FROM %I.legal_evidence_snapshot_items AS item"
        in function_body
    )
    assert "item.tenant_id = $2" in function_body
    assert "item.project_id = $3" in function_body
    assert "item.citation_verification_status <> 'verified'" in function_body
    assert "item.verified_at IS NULL" in function_body
    assert "TG_TABLE_SCHEMA" in function_body
    assert "INTO has_evidence_items" in function_body
    assert "INTO has_invalid_evidence_items" in function_body
    assert "USING" in function_body
    assert "CONSTRAINT = 'ck_legal_evidence_snapshot_gate_items'" in function_body
    assert "NEW.metadata_json -> 'coverageComplete'" in function_body
    assert "IS DISTINCT FROM 'true'::JSONB" in function_body
    assert "NEW.metadata_json -> 'expectedCitationCount'" in function_body
    assert "NEW.metadata_json -> 'sealedCitationCount'" in function_body
    assert "::NUMERIC < 1" in function_body
    assert "<> TRUNC(" in function_body
    assert (
        "(NEW.metadata_json ->> 'sealedCitationCount')::NUMERIC\n"
        "            <> (NEW.metadata_json ->> 'expectedCitationCount')::NUMERIC"
        in function_body
    )
    assert (
        "CONSTRAINT = 'ck_legal_evidence_snapshot_gate_coverage'"
        in function_body
    )
    assert "NEW.metadata_json -> 'claimCoverageComplete'" in function_body
    assert "NEW.metadata_json -> 'expectedClaimCount'" in function_body
    assert "NEW.metadata_json -> 'verifiedClaimCount'" in function_body
    assert (
        "(NEW.metadata_json ->> 'expectedClaimCount')::NUMERIC < 1"
        in function_body
    )
    assert (
        "(NEW.metadata_json ->> 'verifiedClaimCount')::NUMERIC < 0"
        in function_body
    )
    assert (
        "(NEW.metadata_json ->> 'verifiedClaimCount')::NUMERIC\n"
        "            <> (NEW.metadata_json ->> 'expectedClaimCount')::NUMERIC"
        in function_body
    )
    assert (
        "CONSTRAINT = 'ck_legal_evidence_snapshot_gate_claim_coverage'"
        in function_body
    )
    assert "SET search_path = pg_catalog, pg_temp" in function_body

    assert (
        "AFTER INSERT ON legal_evidence_snapshots"
        in snapshot_trigger
    )
    assert "DEFERRABLE INITIALLY DEFERRED" in snapshot_trigger
    assert (
        "legal_assert_gate_eligible_snapshot_items()"
        in snapshot_trigger
    )

    assert (
        "FROM %I.legal_evidence_snapshots AS snapshot"
        in item_function_body
    )
    assert "TG_TABLE_SCHEMA" in item_function_body
    assert "snapshot.tenant_id = $2" in item_function_body
    assert "snapshot.project_id = $3" in item_function_body
    assert "snapshot.gate_eligible" in item_function_body
    assert "snapshot.creation_txid" in item_function_body
    assert (
        "parent_creation_txid <> pg_catalog.txid_current()"
        in item_function_body
    )
    assert (
        "CONSTRAINT = 'ck_legal_evidence_snapshot_items_immutable'"
        in item_function_body
    )
    assert item_function_body.index(
        "parent_creation_txid <> pg_catalog.txid_current()"
    ) < item_function_body.index("IF NOT parent_gate_eligible")
    assert "CONSTRAINT = 'ck_legal_evidence_item_gate_verified'" in item_function_body
    assert "SET search_path = pg_catalog, pg_temp" in item_function_body
    assert (
        "AFTER INSERT ON legal_evidence_snapshot_items"
        in item_trigger
    )
    assert "DEFERRABLE INITIALLY DEFERRED" in item_trigger

    assert "NEW.creation_txid := pg_catalog.txid_current()" in stamp_function_body
    assert "SET search_path = pg_catalog, pg_temp" in stamp_function_body
    assert (
        "BEFORE INSERT ON legal_evidence_snapshots"
        in MIGRATION
    )
    deferred_invariants = MIGRATION.split(
        "-- Deferred legal approval invariants", 1
    )[1].split("-- Append-only enforcement", 1)[0]
    assert "realytics." not in deferred_invariants


def test_gate_snapshot_reconciles_declared_coverage_with_verified_lineage() -> None:
    function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_gate_eligible_snapshot_items()",
        1,
    )[1].split("$legal_gate_snapshot_items$;", 1)[0]

    assert "COUNT(\n                DISTINCT (" in function_body
    assert "item.legal_resource_id" in function_body
    assert "item.legal_resource_version_id" in function_body
    assert "item.legal_provision_id" in function_body
    assert "COUNT(DISTINCT item.claim_text_sha256)" in function_body
    assert "INTO\n        actual_citation_count" in function_body
    assert "actual_claim_count" in function_body

    assert "item.legal_provision_id IS NULL" in function_body
    assert (
        "FROM %1$I.legal_evidence_snapshot_items AS item"
        in function_body
    )
    assert (
        "LEFT JOIN %1$I.legal_resource_versions AS resource_version"
        in function_body
    )
    assert (
        "resource_version.verification_status NOT IN (\n"
        "                        'source_verified',\n"
        "                        'schema_verified'"
        in function_body
    )
    assert "resource_version.verified_at IS NULL" in function_body
    assert (
        "resource_version.source_fetch_id\n"
        "                        IS DISTINCT FROM item.source_fetch_id"
        in function_body
    )
    assert (
        "LEFT JOIN %1$I.legal_source_fetches AS source_fetch"
        in function_body
    )
    assert (
        "source_fetch.fetch_status NOT IN (\n"
        "                        'succeeded',\n"
        "                        'not_modified'"
        in function_body
    )
    assert "LEFT JOIN %1$I.legal_provisions AS provision" in function_body
    assert "provision.legal_resource_version_id =" in function_body
    assert "provision.legal_resource_id = item.legal_resource_id" in function_body
    assert "provision.legal_provision_id IS NULL" in function_body
    assert "CONSTRAINT = 'ck_legal_evidence_snapshot_gate_lineage'" in function_body

    assert (
        "actual_citation_count::NUMERIC\n"
        "            <> (NEW.metadata_json ->> 'sealedCitationCount')::NUMERIC"
        in function_body
    )
    assert (
        "actual_citation_count::NUMERIC\n"
        "            <> (NEW.metadata_json ->> 'expectedCitationCount')::NUMERIC"
        in function_body
    )
    assert (
        "actual_claim_count::NUMERIC\n"
        "            <> (NEW.metadata_json ->> 'verifiedClaimCount')::NUMERIC"
        in function_body
    )
    assert (
        "actual_claim_count::NUMERIC\n"
        "            <> (NEW.metadata_json ->> 'expectedClaimCount')::NUMERIC"
        in function_body
    )
    assert (
        "CONSTRAINT = 'ck_legal_evidence_snapshot_gate_item_counts'"
        in function_body
    )


def test_approve_decision_requires_same_scope_eligible_snapshot() -> None:
    function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_approval_snapshot_eligible()",
        1,
    )[1].split("$legal_approval_snapshot_eligible$;", 1)[0]
    trigger_body = MIGRATION.split(
        "CREATE CONSTRAINT TRIGGER ctrg_legal_review_approval_snapshot",
        1,
    )[1].split(";", 1)[0]

    assert "IF NEW.decision_type <> 'approve'" in function_body
    assert "NEW.legal_evidence_snapshot_id IS NULL" in function_body
    assert "snapshot.tenant_id = $2" in function_body
    assert "snapshot.project_id = $3" in function_body
    assert "snapshot.gate_eligible" in function_body
    assert "snapshot.citation_verification_status = 'verified'" in function_body
    assert "NOT snapshot.has_open_conflicts" in function_body
    assert "NOT snapshot.is_stale" in function_body
    assert (
        "FROM %1$I.legal_evidence_snapshots AS snapshot"
        in function_body
    )
    assert (
        "FROM %1$I.legal_evidence_snapshot_items AS item"
        in function_body
    )
    assert "TG_TABLE_SCHEMA" in function_body
    assert "item.citation_verification_status = 'verified'" in function_body
    assert "NEW.metadata_json -> 'legalReviewId'" in function_body
    assert "btrim(NEW.metadata_json ->> 'legalReviewId')" in function_body
    assert "snapshot.metadata_json -> 'legalReviewId'" in function_body
    assert "snapshot.metadata_json ->> 'legalReviewId' = $4" in function_body
    assert "INTO snapshot_is_eligible" in function_body
    assert "CONSTRAINT = 'ck_legal_review_approval_snapshot'" in function_body
    assert "SET search_path = pg_catalog, pg_temp" in function_body

    assert (
        "AFTER INSERT ON legal_review_decisions"
        in trigger_body
    )
    assert "DEFERRABLE INITIALLY DEFERRED" in trigger_body
    assert (
        "legal_assert_approval_snapshot_eligible()"
        in trigger_body
    )


def test_machine_inferred_citations_and_relations_require_human_review() -> None:
    citation_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_citations (", 1
    )[1].split("\n);", 1)[0]
    relation_body = MIGRATION.split(
        "CREATE TABLE IF NOT EXISTS legal_relation_assertions (", 1
    )[1].split("\n);", 1)[0]

    assert "ck_legal_citation_inference_requires_review" in citation_body
    assert "assertion_method <> 'machine_inferred'" in citation_body
    assert "human_review_status <> 'not_required'" in citation_body
    assert "'overrules_candidate'" in relation_body
    assert "human_review_status" in relation_body
    assert "'candidate', 'verified', 'rejected', 'superseded'" in relation_body


def test_legal_migration_is_splitter_safe() -> None:
    statements = postgres._split_sql_statements(MIGRATION)

    append_only_functions = [
        statement
        for statement in statements
        if statement.startswith(
            "CREATE OR REPLACE FUNCTION legal_reject_append_only_mutation()"
        )
    ]
    gate_snapshot_functions = [
        statement
        for statement in statements
        if statement.startswith(
            "CREATE OR REPLACE FUNCTION "
            "legal_assert_gate_eligible_snapshot_items()"
        )
    ]
    gate_item_functions = [
        statement
        for statement in statements
        if statement.startswith(
            "CREATE OR REPLACE FUNCTION "
            "legal_assert_gate_eligible_item_verified()"
        )
    ]
    approval_functions = [
        statement
        for statement in statements
        if statement.startswith(
            "CREATE OR REPLACE FUNCTION "
            "legal_assert_approval_snapshot_eligible()"
        )
    ]
    stamp_functions = [
        statement
        for statement in statements
        if statement.startswith(
            "CREATE OR REPLACE FUNCTION "
            "legal_stamp_evidence_snapshot_transaction()"
        )
    ]
    pg_trgm_checks = [
        statement
        for statement in statements
        if statement.startswith("DO $legal_pg_trgm_schema$")
    ]

    assert len(append_only_functions) == 1
    assert "RAISE EXCEPTION" in append_only_functions[0]
    assert len(gate_snapshot_functions) == 1
    assert "RAISE EXCEPTION" in gate_snapshot_functions[0]
    assert len(gate_item_functions) == 1
    assert "RAISE EXCEPTION" in gate_item_functions[0]
    assert len(approval_functions) == 1
    assert "RAISE EXCEPTION" in approval_functions[0]
    assert len(stamp_functions) == 1
    assert "pg_catalog.txid_current()" in stamp_functions[0]
    assert len(pg_trgm_checks) == 1
    assert "installed_schema IS DISTINCT FROM 'extensions'" in pg_trgm_checks[0]
    assert len(statements) >= 100
