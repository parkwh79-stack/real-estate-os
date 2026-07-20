from __future__ import annotations

from pathlib import Path

from db import postgres


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "0009_legal_governance_and_tenant_overlay.sql"
).read_text(encoding="utf-8")


def _table_body(table_name: str) -> str:
    return MIGRATION.split(
        f"CREATE TABLE IF NOT EXISTS {table_name} (",
        1,
    )[1].split("\n);", 1)[0]


def test_citator_verified_state_requires_human_review_and_exact_excerpt() -> None:
    assert "ALTER COLUMN citation_status SET DEFAULT 'unknown'" in MIGRATION
    assert "ALTER COLUMN assertion_status SET DEFAULT 'unknown'" in MIGRATION
    assert "'unknown'," in MIGRATION
    assert "ck_legal_citation_verified_review" in MIGRATION
    assert "ck_legal_relation_verified_review" in MIGRATION
    assert "human_review_status = 'approved'" in MIGRATION
    assert "NULLIF(btrim(reviewed_by), '') IS NOT NULL" in MIGRATION
    assert "evidence_excerpt_sha256" in MIGRATION
    assert "extensions.digest(" in MIGRATION
    assert "supersedes_citation_id" in MIGRATION
    assert "supersedes_assertion_id" in MIGRATION

    interpretive_constraint = MIGRATION.split(
        "ADD CONSTRAINT ck_legal_relation_interpretive_review",
        1,
    )[1].split(";", 1)[0]
    for relation_type in (
        "applies",
        "distinguishes",
        "follows",
        "interprets",
        "limits",
        "criticizes",
        "overrules_candidate",
    ):
        assert f"'{relation_type}'" in interpretive_constraint
    assert "assertion_status NOT IN ('verified')" in interpretive_constraint


def test_citator_review_history_is_append_only() -> None:
    for table in (
        "legal_citation_review_events",
        "legal_relation_review_events",
    ):
        body = _table_body(table)
        assert "reviewer_id" in body
        assert "reviewed_at" in body
        assert "reason" in body
        assert (
            f"trg_{table}_append_only"
            if table == "legal_citation_review_events"
            else "trg_legal_relation_review_events_append_only"
        ) in MIGRATION
    assert "uq_legal_citation_single_successor" in MIGRATION
    assert "uq_legal_relation_single_successor" in MIGRATION
    review_event_trigger = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_citator_review_event()",
        1,
    )[1].split("$legal_citator_review_event$;", 1)[0]
    assert "reviewStatus" in review_event_trigger
    assert "legal_citation_review_events" in review_event_trigger
    assert "legal_relation_review_events" in review_event_trigger
    assert "delegation.scope = ''citator_review''" in review_event_trigger
    assert "supersedes_citation_id IS NOT NULL" in review_event_trigger
    assert "supersedes_assertion_id IS NOT NULL" in review_event_trigger


def test_citator_review_status_and_predecessor_chain_are_exact() -> None:
    citation_constraint = MIGRATION.split(
        "ADD CONSTRAINT ck_legal_citation_review_status_exact",
        1,
    )[1].split("NOT VALID", 1)[0]
    relation_constraint = MIGRATION.split(
        "ADD CONSTRAINT ck_legal_relation_review_status_exact",
        1,
    )[1].split("NOT VALID", 1)[0]
    for constraint, status_column in (
        (citation_constraint, "citation_status"),
        (relation_constraint, "assertion_status"),
    ):
        assert "metadata_json ->> 'reviewStatus' = 'approved'" in constraint
        assert f"{status_column} = 'verified'" in constraint
        assert "human_review_status = 'approved'" in constraint
        assert "metadata_json ->> 'reviewStatus' = 'rejected'" in constraint
        assert f"{status_column} = 'rejected'" in constraint
        assert "human_review_status = 'rejected'" in constraint
        assert "metadata_json ->> 'reviewStatus' = 'needs_revision'" in constraint
        assert f"{status_column} = 'unknown'" in constraint
        assert "human_review_status = 'pending'" in constraint

    for table, record_id, successor_index in (
        (
            "legal_citation_review_events",
            "legal_citation_id",
            "uq_legal_citation_review_single_successor",
        ),
        (
            "legal_relation_review_events",
            "legal_relation_assertion_id",
            "uq_legal_relation_review_single_successor",
        ),
    ):
        body = _table_body(table)
        assert f"UNIQUE ({record_id})" in body
        assert "supersedes_review_event_id" in body
        assert "<> legal_" in body
        assert successor_index in MIGRATION

    function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_citator_review_event()",
        1,
    )[1].split("$legal_citator_review_event$;", 1)[0]
    delegation_read = function_body.index(
        "delegation_id := NULLIF("
    )
    citation_chain_check = function_body.index(
        "sourceCitationId"
    )
    assert delegation_read < citation_chain_check
    for identity_field in (
        "source_resource_id",
        "source_version_id",
        "source_provision_id",
        "cited_identifier_text",
        "target_resource_id",
        "target_version_id",
        "target_provision_id",
        "subject_resource_id",
        "object_resource_id",
        "relation_type",
        "assertion_method",
        "algorithm_version",
        "confidence",
        "as_of_date",
    ):
        assert identity_field in function_body
    assert "predecessor.asserted_at <= $12" in function_body
    assert "predecessor.asserted_at <= $11" in function_body
    assert "sourceCitationId" in function_body
    assert "sourceAssertionId" in function_body
    assert "ORDER BY latest.reviewed_at DESC" in function_body
    assert "NOT COALESCE(successor_valid, FALSE)" in function_body
    assert "NOT COALESCE(review_chain_valid, FALSE)" in function_body
    assert (
        "REVOKE ALL ON FUNCTION legal_assert_citator_review_event()"
        in MIGRATION
    )


def test_orphan_citator_review_events_are_rejected_at_commit() -> None:
    function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_citator_review_event_target()",
        1,
    )[1].split("$legal_citator_review_event_target$;", 1)[0]
    assert "SET search_path = pg_catalog, pg_temp" in function_body
    assert "statement_timestamp() + INTERVAL '5 minutes'" in function_body
    assert "reviewed.supersedes_citation_id = $6" in function_body
    assert "reviewed.supersedes_assertion_id = $6" in function_body
    assert "predecessor.source_resource_id" in function_body
    assert "predecessor.cited_identifier_text" in function_body
    assert "predecessor.subject_resource_id" in function_body
    assert "predecessor.relation_type" in function_body
    assert "reviewed.asserted_at = $4" in function_body
    assert "predecessor.asserted_at <= $4" in function_body
    assert "reviewed.metadata_json ->> ''reviewStatus'' = $2" in function_body
    assert "sourceCitationId" in function_body
    assert "sourceAssertionId" in function_body
    assert "latest.legal_citation_review_event_id" in function_body
    assert "latest.legal_relation_review_event_id" in function_body
    assert "delegation.scope = ''citator_review''" in function_body
    for trigger in (
        "ctrg_legal_citation_review_event_target",
        "ctrg_legal_relation_review_event_target",
    ):
        trigger_body = MIGRATION.split(
            f"CREATE CONSTRAINT TRIGGER {trigger}",
            1,
        )[1].split(";", 1)[0]
        assert "DEFERRABLE INITIALLY DEFERRED" in trigger_body
        assert "legal_assert_citator_review_event_target()" in trigger_body
    assert (
        "REVOKE ALL ON FUNCTION "
        "legal_assert_citator_review_event_target()"
        in MIGRATION
    )


def test_maker_checker_and_final_legal_role_are_database_invariants() -> None:
    final_role_constraint = MIGRATION.split(
        "ADD CONSTRAINT ck_legal_review_final_decision_role",
        1,
    )[1].split(";", 1)[0]
    assert "'approve'" in final_role_constraint
    assert "'reject'" in final_role_constraint
    assert "'waive_with_conditions'" in final_role_constraint
    assert "'legal_counsel_approver'" in final_role_constraint
    assert "'external_counsel'" in final_role_constraint
    assert "'tenant_admin'" not in final_role_constraint
    assert "ck_legal_review_external_delegation" in MIGRATION

    function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_maker_checker()",
        1,
    )[1].split("$legal_maker_checker$;", 1)[0]
    assert "snapshot.created_by" in function_body
    assert "snapshot_creator = NEW.reviewer_id" in function_body
    assert "legal_approval_delegation_id" in function_body
    assert "delegation.valid_until >= $6" in function_body
    assert "delegation.created_at <= $6" in function_body
    assert "delegation.scope = $7" in function_body
    assert "NEW.decision_scope" in function_body
    assert (
        "delegation.scope IN (''approval_gate'', ''waiver_approval'')"
        not in function_body
    )
    assert "successor.delegation_version" in function_body
    assert "CONSTRAINT = 'ck_legal_review_maker_checker'" in function_body
    assert "NEW.legal_evidence_snapshot_id IS NULL" in function_body
    assert "NEW.legal_review_id" in function_body


def test_delegation_successors_preserve_exact_authority_chain() -> None:
    body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_approval_delegation_chain()",
        1,
    )[1].split("$legal_approval_delegation_chain$;", 1)[0]
    assert "uq_legal_approval_delegation_successor" in MIGRATION
    assert "NEW.delegation_key IS DISTINCT FROM predecessor.delegation_key" in body
    assert (
        "NEW.delegation_version <> predecessor.delegation_version + 1"
        in body
    )
    assert "NEW.grantee_id IS DISTINCT FROM predecessor.grantee_id" in body
    assert "NEW.scope IS DISTINCT FROM predecessor.scope" in body
    assert "NEW.status NOT IN ('revoked', 'expired', 'consumed')" in body
    assert "statement_timestamp() + INTERVAL '5 minutes'" in body
    assert (
        "REVOKE ALL ON FUNCTION legal_assert_approval_delegation_chain()"
        in MIGRATION
    )


def test_governance_approvals_use_bounded_database_time() -> None:
    body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_governance_causal_time()",
        1,
    )[1].split("$legal_governance_causal_time$;", 1)[0]
    assert "statement_timestamp() - INTERVAL '5 minutes'" in body
    assert "statement_timestamp() + INTERVAL '5 minutes'" in body
    assert "NEW.approved_at < NEW.created_at" in body
    assert "NEW.approved_at < NEW.authored_at" in body
    assert "NEW.reviewed_at < NEW.created_at" in body
    for trigger in (
        "trg_legal_required_claim_set_causal_time",
        "trg_counsel_opinion_causal_time",
        "trg_legal_analysis_causal_time",
    ):
        assert trigger in MIGRATION


def test_counsel_and_analysis_lifecycles_are_single_immediate_chains() -> None:
    for index_name in (
        "uq_counsel_opinion_root",
        "uq_counsel_opinion_single_successor",
        "uq_legal_analysis_root",
        "uq_legal_analysis_single_successor",
    ):
        assert index_name in MIGRATION

    opinion = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_counsel_opinion_chain()",
        1,
    )[1].split("$legal_counsel_opinion_chain$;", 1)[0]
    assert "SET search_path = pg_catalog, pg_temp" in opinion
    assert "NEW.opinion_version <> 1" in opinion
    assert "NEW.status NOT IN ('draft', 'issued')" in opinion
    assert "NEW.opinion_version <> predecessor.opinion_version + 1" in opinion
    assert "predecessor.status NOT IN ('draft', 'issued')" in opinion
    for sealed_field in (
        "legal_evidence_snapshot_id",
        "object_locator",
        "content_sha256",
        "privileged",
        "authored_by",
        "authored_at",
    ):
        assert sealed_field in opinion
    assert "pg_catalog.pg_advisory_xact_lock" in opinion
    assert (
        "REVOKE ALL ON FUNCTION legal_assert_counsel_opinion_chain()"
        in MIGRATION
    )

    analysis = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_analysis_version_chain()",
        1,
    )[1].split("$legal_analysis_version_chain$;", 1)[0]
    assert "SET search_path = pg_catalog, pg_temp" in analysis
    assert "NEW.analysis_version <> 1" in analysis
    assert "NEW.human_review_status <> 'pending'" in analysis
    assert "NEW.analysis_version <> predecessor.analysis_version + 1" in analysis
    assert "predecessor.human_review_status <> 'pending'" in analysis
    for sealed_field in (
        "legal_evidence_snapshot_id",
        "object_locator",
        "content_sha256",
        "input_snapshot_sha256",
        "model_id",
        "prompt_sha256",
        "created_by",
        "created_at",
    ):
        assert sealed_field in analysis
    assert "new legal analysis content must re-enter pending review" in analysis
    assert "pg_catalog.pg_advisory_xact_lock" in analysis
    assert (
        "REVOKE ALL ON FUNCTION legal_assert_analysis_version_chain()"
        in MIGRATION
    )


def test_upgrade_constraints_protect_new_rows_without_blocking_legacy_backfill():
    for name in (
        "ck_legal_citation_verified_review",
        "ck_legal_citation_review_actor",
        "ck_legal_relation_verified_review",
        "ck_legal_relation_interpretive_review",
        "ck_legal_review_decision_human_actor",
        "ck_legal_review_final_decision_role",
        "ck_legal_review_external_delegation",
        "ck_legal_review_decision_review_id",
        "ck_legal_review_decision_projection_binding",
        "ck_legal_evidence_required_claim_set",
    ):
        start = MIGRATION.index(f"ADD CONSTRAINT {name}")
        statement = MIGRATION[start:MIGRATION.index(";", start)]
        assert "NOT VALID" in statement


def test_required_claim_text_hash_is_unique_within_each_policy() -> None:
    body = _table_body("legal_required_claims")
    assert (
        "UNIQUE (legal_required_claim_set_id, claim_text_sha256)"
        in body
    )


def test_required_claim_policy_is_a_single_immediate_append_only_chain() -> None:
    assert "uq_legal_required_claim_set_root" in MIGRATION
    assert "uq_legal_required_claim_set_successor" in MIGRATION
    body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_required_claim_set_chain()",
        1,
    )[1].split("$legal_required_claim_set_chain$;", 1)[0]
    assert "SET search_path = pg_catalog, pg_temp" in body
    assert "NEW.claim_set_version <> 1" in body
    assert "NEW.status <> 'draft'" in body
    assert (
        "NEW.claim_set_version <> predecessor.claim_set_version + 1"
        in body
    )
    assert "NEW.review_scope IS DISTINCT FROM predecessor.review_scope" in body
    assert "NEW.status = 'draft'" in body
    assert "predecessor.status <> 'approved'" in body
    assert "NEW.status IN ('approved', 'rejected')" in body
    assert "predecessor.status <> 'draft'" in body
    assert (
        "NEW.claim_set_sha256 IS DISTINCT FROM\n"
        "               predecessor.claim_set_sha256"
        in body
    )
    assert "NEW.created_by IS DISTINCT FROM predecessor.created_by" in body
    assert "pg_catalog.pg_advisory_xact_lock" in body
    assert (
        "REVOKE ALL ON FUNCTION legal_assert_required_claim_set_chain()"
        in MIGRATION
    )


def test_tenant_native_legal_objects_have_scope_rls_and_append_only_history() -> None:
    tenant_tables = (
        "legal_approval_delegations",
        "legal_required_claim_sets",
        "legal_required_claims",
        "project_legal_matters",
        "legal_waivers",
        "counsel_opinions",
        "legal_analysis_versions",
        "project_legal_annotations",
        "tenant_legal_term_aliases",
        "tenant_legal_term_alias_review_events",
    )
    for table in tenant_tables:
        body = _table_body(table)
        assert "tenant_id" in body
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in MIGRATION
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in MIGRATION
        assert f"ON {table}" in MIGRATION

    required_claim_set = _table_body("legal_required_claim_sets")
    assert "claim_set_sha256" in required_claim_set
    assert "approved_by <> created_by" in required_claim_set
    assert "review_scope" in required_claim_set
    assert "legal_assert_required_claim_set_complete" in MIGRATION
    assert "legal_assert_snapshot_required_claim_policy" in MIGRATION

    waiver = _table_body("legal_waivers")
    assert "valid_until" in waiver
    assert "conditions_json" in waiver
    assert "cure_status" in waiver
    assert "cure_evidence_snapshot_id" in waiver
    assert "approved_by <> proposed_by" in waiver

    opinion = _table_body("counsel_opinions")
    assert "object_locator" in opinion
    assert "content_sha256" in opinion
    assert "privileged" in opinion
    assert "approved_by <> authored_by" in opinion

    analysis = _table_body("legal_analysis_versions")
    assert "input_snapshot_sha256" in analysis
    assert "prompt_sha256" in analysis
    assert "human_review_status = 'approved'" in analysis
    assert "NOT analysis_only" in analysis
    assert "reviewed_by <> created_by" in analysis
    assert "legal_counsel_approver" in analysis
    assert "external_counsel" in analysis


def test_project_legal_matter_is_a_single_server_timed_leaf_chain() -> None:
    assert "uq_project_legal_matter_root" in MIGRATION
    assert "uq_project_legal_matter_single_successor" in MIGRATION
    chain = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_project_legal_matter_chain()",
        1,
    )[1].split("$legal_project_matter_chain$;", 1)[0]
    assert "NEW.created_at := statement_timestamp()" in chain
    assert "pg_catalog.pg_advisory_xact_lock" in chain
    assert "NEW.matter_version <> 1" in chain
    assert "NEW.status NOT IN ('open', 'under_review', 'blocked')" in chain
    assert "predecessor.tenant_id = $2" in chain
    assert "predecessor.project_id = $3" in chain
    assert "predecessor.matter_key = $4" in chain
    assert "successor.supersedes_matter_id" in chain
    assert "FOR UPDATE OF predecessor" in chain
    assert "NEW.matter_version <> predecessor.matter_version + 1" in chain
    assert "NEW.matter_type IS DISTINCT FROM predecessor.matter_type" in chain
    assert "NEW.opened_at IS DISTINCT FROM predecessor.opened_at" in chain
    assert "predecessor.status = 'waived'" in chain
    assert "'resolved'" in chain
    assert "predecessor.status = 'superseded'" in chain
    assert (
        "REVOKE ALL ON FUNCTION legal_assert_project_legal_matter_chain()"
        in MIGRATION
    )


def test_waiver_lifecycle_is_an_exact_append_only_state_machine() -> None:
    waiver = _table_body("legal_waivers")
    exact_state = waiver.split(
        "CONSTRAINT ck_legal_waiver_status_cure_exact",
        1,
    )[1].split(
        "CONSTRAINT ck_legal_waiver_approval",
        1,
    )[0]
    for expected in (
        "status = 'proposed' AND cure_status = 'not_started'",
        "status = 'approved'",
        "'not_started'",
        "'in_progress'",
        "'evidence_submitted'",
        "status = 'cured' AND cure_status = 'verified'",
        "status = 'revoked' AND cure_status = 'failed'",
        "status IN ('rejected', 'expired', 'superseded')",
        "cure_status = 'not_applicable'",
    ):
        assert expected in exact_state
    assert "uq_legal_waiver_single_successor" in MIGRATION

    lifecycle = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_waiver_lifecycle()",
        1,
    )[1].split("$legal_waiver_lifecycle$;", 1)[0]
    assert "SET search_path = pg_catalog, pg_temp" in lifecycle
    assert "NEW.waiver_version <> 1" in lifecycle
    assert "NEW.supersedes_waiver_id IS NOT NULL" in lifecycle
    assert "transition_kind := 'approval'" in lifecycle
    assert "transition_kind := 'in_progress'" in lifecycle
    assert "transition_kind := 'terminal'" in lifecycle
    assert "predecessor.tenant_id = $2" in lifecycle
    assert "predecessor.project_id = $3" in lifecycle
    assert "predecessor.project_legal_matter_id = $4" in lifecycle
    assert "predecessor.waiver_version = $5 - 1" in lifecycle
    assert "predecessor.status = ''approved''" in lifecycle
    assert "predecessor.cure_status = ''in_progress''" in lifecycle
    assert "predecessor.cure_reviewed_at IS NOT NULL" in lifecycle
    assert "<> lower(btrim($17))" in lifecycle
    assert "NEW.approved_at < NEW.proposed_at" in lifecycle
    assert "NEW.approved_at <" in lifecycle
    assert "statement_timestamp() - INTERVAL '5 minutes'" in lifecycle
    assert "NEW.valid_from < NEW.approved_at" in lifecycle
    assert "NEW.cure_reviewed_at >= NEW.valid_until" in lifecycle
    assert "NEW.cure_reviewed_at <" in lifecycle
    assert "statement_timestamp() + INTERVAL '5 minutes'" in lifecycle
    assert "pg_catalog.pg_advisory_xact_lock" in lifecycle
    assert "NEW.recorded_at := statement_timestamp()" in lifecycle
    assert (
        "REVOKE ALL ON FUNCTION legal_assert_waiver_lifecycle()"
        in MIGRATION
    )
    assert "CREATE OR REPLACE FUNCTION legal_try_timestamptz(value TEXT)" in (
        MIGRATION
    )
    assert (
        "REVOKE ALL ON FUNCTION legal_try_timestamptz(TEXT) FROM PUBLIC"
        in MIGRATION
    )

    snapshot = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_governance_snapshot()",
        1,
    )[1].split("$legal_governance_snapshot$;", 1)[0]
    assert "condition_entry ->> 'description'" in snapshot
    assert "waiver_condition_outcomes_valid" in snapshot
    assert "NEW.cure_status NOT IN ('verified', 'failed')" in snapshot
    assert "outcome_entry ->> 'status' <>" in snapshot
    assert "'satisfied'" in snapshot
    assert "outcome_entry ->> 'status' = 'failed'" in snapshot
    assert "terminal waiver cure requires current verified evidence" in snapshot
    assert "cure_snapshot_creator" in snapshot
    assert (
        "waiver cure evidence maker cannot verify its own snapshot"
        in snapshot
    )
    assert "waiver cure maker cannot verify its own transition" in snapshot


def test_governance_approvals_revalidate_normalized_corpus_lineage() -> None:
    snapshot = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_assert_governance_snapshot()",
        1,
    )[1].split("$legal_governance_snapshot$;", 1)[0]

    # Both the authority snapshot and a terminal waiver-cure snapshot must
    # resolve every citation through the normalized corpus instead of trusting
    # the booleans stored on legal_evidence_snapshots.
    assert snapshot.count(
        "FROM %I.legal_evidence_snapshot_items AS lineage_item"
    ) == 2
    for relation in (
        "%I.legal_resource_versions AS lineage_version",
        "%I.legal_source_fetches AS lineage_fetch",
        "%I.legal_source_providers AS lineage_provider",
        "%I.legal_provisions AS lineage_provision",
        "%I.legal_resource_versions AS replacement",
        "%I.legal_source_fetches AS replacement_fetch",
        "%I.legal_source_providers AS replacement_provider",
    ):
        assert snapshot.count(relation) == 2
    for invariant in (
        "lineage_version.source_fetch_id IS DISTINCT FROM",
        "lineage_fetch.fetch_status NOT IN",
        "lineage_provider.provider_status <> ''active''",
        "lineage_provision.source_locator IS DISTINCT FROM",
        "lineage_version.effective_from > snapshot.as_of_date",
        "lineage_version.valid_time_end < snapshot.as_of_date",
        "replacement.supersedes_version_id =",
        "replacement_fetch.fetch_status IN",
        "replacement_provider.provider_status = ''active''",
        "lineage_item.verified_at > statement_timestamp()",
        "snapshot.freshness_policy_version <> ",
        "lineage_item.verified_at + INTERVAL ''7 days'' < ",
        "lineage_version.verified_at > statement_timestamp()",
    ):
        assert snapshot.count(invariant) == 2


def test_legal_and_permit_review_projections_are_typed_rls_append_only() -> None:
    for table in (
        "project_legal_review_versions",
        "project_permit_review_versions",
    ):
        body = _table_body(table)
        for column in (
            "tenant_id",
            "project_id",
            "review_id",
            "review_version",
            "evidence_status",
            "legal_evidence_snapshot_id",
            "snapshot_version",
            "snapshot_sha256",
            "snapshot_gate_eligible",
            "parser_status",
            "analysis_only",
            "report_eligible",
            "projection_sha256",
            "supersedes_review_version_id",
            "created_by",
        ):
            assert column in body
        assert (
            "FOREIGN KEY (project_id, tenant_id)"
            in body
        )
        assert (
            "REFERENCES projects(project_id, tenant_id)"
            in body
        )
        assert (
            "COALESCE(\n"
            "                jsonb_typeof(evidence_json -> "
            "'evidenceLocators') = 'array',\n"
            "                FALSE\n"
            "            )"
            in body
        )
        assert f"trg_{table}_append_only" in MIGRATION
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in MIGRATION
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in MIGRATION
        assert table in MIGRATION.split("REVOKE ALL ON TABLE", 1)[1]

    snapshot_trigger = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_review_projection_snapshot()",
        1,
    )[1].split("$legal_review_projection_snapshot$;", 1)[0]
    assert "SET search_path = pg_catalog, pg_temp" in snapshot_trigger
    assert "pg_catalog.format(" in snapshot_trigger
    assert "'FROM %I.%I WHERE review_version_id = $1'" in snapshot_trigger
    assert "TG_TABLE_SCHEMA" in snapshot_trigger
    assert "snapshot.snapshot_sha256" in snapshot_trigger
    assert "snapshot.gate_eligible" in snapshot_trigger
    assert "snapshot.legal_review_id" in snapshot_trigger
    assert "is_superseded" in snapshot_trigger
    assert "DEFERRABLE INITIALLY DEFERRED" in MIGRATION


def test_final_decision_is_bound_to_exact_latest_legal_projection() -> None:
    for column in (
        "legal_review_version_id",
        "legal_review_version",
        "legal_review_projection_sha256",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in MIGRATION
    assert "fk_legal_review_decision_projection_scope" in MIGRATION
    assert "uq_project_legal_review_version_scope" in MIGRATION

    function_body = MIGRATION.split(
        "legal_assert_review_decision_projection_binding()",
        1,
    )[1].split("$legal_review_decision_projection$;", 1)[0]
    assert "SET search_path = pg_catalog, pg_temp" in function_body
    assert "%I.project_legal_review_versions" in function_body
    assert "ORDER BY projection.review_version DESC" in function_body
    assert (
        "NEW.legal_review_version_id IS DISTINCT FROM "
        "latest_projection_id"
        in function_body
    )
    assert (
        "NEW.legal_review_projection_sha256 IS DISTINCT FROM"
        in function_body
    )
    assert (
        "NEW.legal_evidence_snapshot_id IS DISTINCT FROM "
        "latest_snapshot_id"
        in function_body
    )
    assert "NEW.decided_at < latest_projection_created_at" in function_body
    assert "NEW.decided_at < latest_snapshot_created_at" in function_body
    assert (
        "statement_timestamp() - INTERVAL '5 minutes'"
        in function_body
    )
    assert (
        "statement_timestamp() + INTERVAL '5 minutes'"
        in function_body
    )
    assert "DEFERRABLE INITIALLY DEFERRED" in MIGRATION.split(
        "ctrg_legal_review_decision_projection_binding",
        1,
    )[1]


def test_approval_gate_decisions_are_server_chained_without_legacy_mutation() -> None:
    normalized = " ".join(MIGRATION.split())
    assert "\nUPDATE legal_review_decisions" not in MIGRATION
    assert "\nUPDATE legal_evidence_snapshots" not in MIGRATION
    for column in (
        "decision_version",
        "supersedes_legal_review_decision_id",
        "expected_predecessor_legal_review_decision_id",
        "predecessor_expectation_supplied",
        "recorded_at",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in normalized
    assert "uq_legal_review_decision_chain_version" in MIGRATION
    assert "uq_legal_review_decision_single_successor" in MIGRATION

    function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_prepare_review_decision_insert()",
        1,
    )[1].split("$legal_prepare_review_decision$;", 1)[0]
    for marker in (
        "pg_advisory_xact_lock",
        "decision_type NOT IN",
        "'request_changes'",
        "NOT EXISTS (",
        "FOR UPDATE OF predecessor",
        "predecessor_expectation_supplied IS DISTINCT FROM TRUE",
        "IS DISTINCT",
        "ERRCODE = '40001'",
        "NEW.decision_version :=",
        "NEW.supersedes_legal_review_decision_id :=",
        "NEW.recorded_at := statement_timestamp()",
        "NEW.decided_at := NEW.recorded_at",
    ):
        assert marker in function_body
    assert (
        "REVOKE ALL ON FUNCTION legal_prepare_review_decision_insert()"
        in MIGRATION
    )
    assert "legacy rows remain unversioned" in MIGRATION


def test_review_projection_chain_and_hash_are_database_issued() -> None:
    for table in (
        "project_legal_review_versions",
        "project_permit_review_versions",
    ):
        assert "projection_hash_contract        TEXT NOT NULL" in _table_body(
            table
        )
    function_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION legal_prepare_review_projection_insert()",
        1,
    )[1].split("$legal_prepare_review_projection$;", 1)[0]
    for marker in (
        "pg_advisory_xact_lock",
        "FOR UPDATE OF predecessor",
        "NEW.review_version IS DISTINCT FROM",
        "NEW.supersedes_review_version_id IS DISTINCT FROM predecessor_id",
        "ERRCODE = '40001'",
        "NEW.created_at := statement_timestamp()",
        "NEW.projection_hash_contract := 'pg-jsonb-row-v1'",
        "to_jsonb(NEW)",
        "- 'projection_sha256'",
        "- 'projection_hash_contract'",
        "extensions.digest(",
    ):
        assert marker in function_body
    assert (
        "REVOKE ALL ON FUNCTION legal_prepare_review_projection_insert()"
        in MIGRATION
    )


def test_every_governance_trigger_function_revokes_public_execute() -> None:
    normalized = " ".join(MIGRATION.split())
    for function_name in (
        "legal_assert_maker_checker",
        "legal_assert_required_claim_set_complete",
        "legal_assert_snapshot_required_claim_policy",
        "legal_assert_governance_delegation",
        "legal_assert_governance_snapshot",
        "legal_assert_review_decision_projection_binding",
        "legal_assert_review_projection_snapshot",
    ):
        assert f"REVOKE ALL ON FUNCTION {function_name}()" in normalized


def test_legal_glossary_extensions_reuse_canonical_terms() -> None:
    for table in (
        "legal_term_source_links",
        "legal_term_relations",
        "tenant_legal_term_aliases",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in MIGRATION
        assert "REFERENCES glossary_terms(term_id)" in _table_body(table)
    assert "CREATE TABLE IF NOT EXISTS legal_terms (" not in MIGRATION


def test_provider_operational_streams_are_privacy_safe_and_append_only() -> None:
    quarantine = _table_body("legal_provider_quarantine")
    assert "payload_sha256" in quarantine
    assert "query" not in quarantine
    assert "response" not in quarantine
    assert "url" not in quarantine

    dead_letters = _table_body("legal_provider_dead_letters")
    assert "attempts >= 1" in dead_letters
    metrics = _table_body("legal_provider_metrics")
    assert "latency_ms >= 0" in metrics
    assert "jsonb_typeof(tags_json) = 'object'" in metrics

    for table in (
        "legal_provider_quarantine",
        "legal_provider_dead_letters",
        "legal_provider_metrics",
        "legal_provider_replay_objects",
        "legal_provider_replay_approval_events",
        "legal_provider_replay_requests",
        "legal_provider_replay_events",
    ):
        assert f"trg_{table}_append_only" in MIGRATION
        assert table in MIGRATION.split("REVOKE ALL ON TABLE", 1)[1]


def test_provider_replay_requires_immutable_two_stage_tenant_binding() -> None:
    replay_object = _table_body("legal_provider_replay_objects")
    approval = _table_body("legal_provider_replay_approval_events")
    request = _table_body("legal_provider_replay_requests")

    for field in (
        "tenant_id",
        "project_id",
        "legal_provider_dead_letter_id",
        "provider_id",
        "object_locator_sha256",
        "object_version_id",
        "object_sha256",
        "object_lock_mode",
        "retention_until",
        "object_store_receipt_sha256",
        "registered_by",
        "registered_at",
    ):
        assert field in replay_object
    assert "fk_legal_provider_replay_object_dead_letter" in replay_object
    assert "object_lock_mode IN ('governance', 'compliance')" in replay_object
    assert "retention_until > registered_at" in replay_object
    assert "extensions.digest(" in replay_object
    assert "substring(object_locator FROM '[^/]+$')" in replay_object

    for field in (
        "tenant_id",
        "project_id",
        "legal_provider_replay_object_id",
        "provider_id",
        "approved_object_locator_sha256",
        "approved_object_version_id",
        "approved_object_sha256",
        "approved_object_retention_until",
        "object_store_receipt_sha256",
        "approved_requester_id",
        "approved_by",
        "approver_role",
        "approved_at",
        "expires_at",
        "supersedes_approval_event_id",
    ):
        assert field in approval
    assert "approver_role = 'compliance_officer'" in approval
    assert "approved_requester_id))" in approval
    assert "<> lower(btrim(approved_by))" in approval
    assert "INTERVAL '24 hours'" in approval
    assert "extensions.digest(" in approval
    assert "fk_legal_provider_replay_approval_object" in approval
    assert "UNIQUE (supersedes_approval_event_id)" in approval
    assert "substring(approved_object_locator FROM '[^/]+$')" in approval
    assert "lower(legal_provider_dead_letter_id) || '.json'" in approval

    assert "legal_provider_replay_approval_event_id TEXT NOT NULL" in request
    assert "tenant_id TEXT NOT NULL" in request
    assert "project_id TEXT NOT NULL" in request
    assert "legal_provider_replay_object_id TEXT NOT NULL" in request
    assert "approved_object_version_id TEXT NOT NULL" in request
    assert "approved_object_retention_until TIMESTAMPTZ NOT NULL" in request
    assert "fk_legal_provider_replay_request_object" in request
    assert "fk_legal_provider_replay_request_approval" in request
    assert "UNIQUE (legal_provider_replay_approval_event_id)" in request
    assert "object_reference_approved_by))" in request
    assert "<> lower(btrim(requested_by))" in request
    assert "requested_at >= object_reference_approved_at" in request


def test_provider_replay_triggers_are_search_path_safe_and_fail_closed() -> None:
    for function_name, terminator in (
        (
            "legal_assert_provider_replay_object_receipt",
            "$legal_provider_replay_object$;",
        ),
        (
            "legal_assert_provider_replay_approval_current",
            "$legal_provider_replay_approval$;",
        ),
        (
            "legal_assert_provider_replay_request_binding",
            "$legal_provider_replay_request$;",
        ),
        (
            "legal_assert_provider_replay_event_transition",
            "$legal_provider_replay_event$;",
        ),
    ):
        body = MIGRATION.split(
            f"CREATE OR REPLACE FUNCTION {function_name}()",
            1,
        )[1].split(terminator, 1)[0]
        assert "SET search_path = pg_catalog, pg_temp" in body
        if function_name != "legal_assert_provider_replay_object_receipt":
            assert "TG_TABLE_SCHEMA" in body
            assert "%I.legal_provider_replay_" in body or (
                "%1$I.legal_provider_replay_" in body
            )
        assert f"REVOKE ALL ON FUNCTION {function_name}()" in MIGRATION

    assert "trg_legal_provider_replay_approval_current" in MIGRATION
    assert "trg_legal_provider_replay_request_binding" in MIGRATION
    assert "trg_legal_provider_replay_event_transition" in MIGRATION
    assert "current unconsumed replay approval" in MIGRATION
    assert "current approved immutable object" in MIGRATION
    catalog_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_provider_replay_catalog_binding()",
        1,
    )[1].split("$legal_provider_replay_catalog$;", 1)[0]
    assert "SET search_path = pg_catalog, pg_temp" in catalog_body
    for field in (
        "legal_provider_replay_object_id",
        "provider_id",
        "object_locator",
        "object_version_id",
        "object_sha256",
        "retention_until",
        "object_store_receipt_sha256",
    ):
        assert field in catalog_body
    assert "ctrg_legal_provider_replay_approval_catalog" in MIGRATION
    assert "ctrg_legal_provider_replay_request_catalog" in MIGRATION
    assert MIGRATION.count("DEFERRABLE INITIALLY DEFERRED") >= 2
    assert "replay_object.registered_at <= $13" in catalog_body


def test_provider_replay_timestamps_are_bounded_by_database_clock() -> None:
    expectations = (
        (
            "legal_assert_provider_replay_object_receipt",
            "$legal_provider_replay_object$;",
            "NEW.registered_at",
            "ck_legal_provider_replay_object_server_time",
        ),
        (
            "legal_assert_provider_replay_approval_current",
            "$legal_provider_replay_approval$;",
            "NEW.approved_at",
            "ck_legal_provider_replay_approval_server_time",
        ),
        (
            "legal_assert_provider_replay_request_binding",
            "$legal_provider_replay_request$;",
            "NEW.requested_at",
            "ck_legal_provider_replay_request_server_time",
        ),
        (
            "legal_assert_provider_replay_event_transition",
            "$legal_provider_replay_event$;",
            "NEW.recorded_at",
            "ck_legal_provider_replay_event_server_time",
        ),
    )
    for function_name, terminator, timestamp, constraint in expectations:
        body = MIGRATION.split(
            f"CREATE OR REPLACE FUNCTION {function_name}()",
            1,
        )[1].split(terminator, 1)[0]
        assert f"{timestamp} < statement_timestamp()" in body
        assert f"{timestamp} > statement_timestamp()" in body
        assert constraint in body


def test_provider_replay_supersession_and_consumption_share_lock_order() -> None:
    approval_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_provider_replay_approval_current()",
        1,
    )[1].split("$legal_provider_replay_approval$;", 1)[0]
    request_body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_provider_replay_request_binding()",
        1,
    )[1].split("$legal_provider_replay_request$;", 1)[0]

    approval_event_lock = (
        "'legal-provider-replay-approval:'"
        "\n                    || NEW.supersedes_approval_event_id"
    )
    request_event_lock = (
        "'legal-provider-replay-approval:'"
        "\n                || NEW.legal_provider_replay_approval_event_id"
    )
    approval_dead_letter_lock = (
        "'dead-letter:' || NEW.legal_provider_dead_letter_id"
    )

    assert approval_event_lock in approval_body
    assert approval_dead_letter_lock in approval_body
    assert approval_body.index(approval_event_lock) < approval_body.index(
        approval_dead_letter_lock
    )
    assert request_event_lock in request_body
    assert approval_dead_letter_lock in request_body
    assert request_body.index(request_event_lock) < request_body.index(
        approval_dead_letter_lock
    )


def test_provider_replay_event_state_machine_is_enforced_in_database() -> None:
    body = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_provider_replay_event_transition()",
        1,
    )[1].split("$legal_provider_replay_event$;", 1)[0]
    assert "NEW.sequence_no <> 1" in body
    assert "NEW.event_type <> 'requested'" in body
    assert "NEW.sequence_no <> previous_sequence + 1" in body
    assert "NEW.recorded_at < previous_recorded_at" in body
    assert "previous_event_type = 'requested'" in body
    assert "NEW.event_type = 'running'" in body
    assert "previous_event_type = 'running'" in body
    assert "NEW.event_type IN ('completed', 'failed')" in body


def test_provider_metadata_is_global_but_replay_rows_are_forced_rls() -> None:
    normalized = " ".join(MIGRATION.split())
    for table in (
        "legal_provider_quarantine",
        "legal_provider_dead_letters",
        "legal_provider_metrics",
    ):
        assert (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
            not in normalized
        )

    for table in (
        "legal_provider_replay_approval_events",
        "legal_provider_replay_objects",
        "legal_provider_replay_requests",
        "legal_provider_replay_events",
    ):
        assert (
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
            in normalized
        )
        assert (
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
            in normalized
        )

    event_policy = MIGRATION.split(
        "CREATE POLICY legal_tenant_isolation\n"
        "    ON legal_provider_replay_events",
        1,
    )[1].split("REVOKE ALL ON TABLE", 1)[0]
    assert "EXISTS (" in event_policy
    assert "FROM legal_provider_replay_requests AS replay_request" in event_policy
    assert "current_setting('realytics.tenant_id', true)" in event_policy


def test_governance_migration_is_sql_splitter_safe() -> None:
    statements = postgres._split_sql_statements(MIGRATION)
    maker_checker_functions = [
        statement
        for statement in statements
        if statement.startswith(
            "CREATE OR REPLACE FUNCTION legal_assert_maker_checker()"
        )
    ]
    assert len(maker_checker_functions) == 1
    assert "RAISE EXCEPTION" in maker_checker_functions[0]
    assert len(statements) >= 90
    assert "trg_legal_provider_replay_object_receipt" in MIGRATION


def test_project_citator_review_is_a_tenant_native_overlay() -> None:
    version_tables = (
        "project_legal_citation_review_versions",
        "project_legal_relation_review_versions",
    )
    event_tables = (
        "project_legal_citation_review_events",
        "project_legal_relation_review_events",
    )
    for table in (*version_tables, *event_tables):
        body = _table_body(table)
        assert "tenant_id" in body
        assert "project_id" in body
        assert "REFERENCES projects(project_id, tenant_id)" in body
        assert (
            f"ALTER TABLE {table}\n    ENABLE ROW LEVEL SECURITY"
            in MIGRATION
        )
        assert (
            f"ALTER TABLE {table}\n    FORCE ROW LEVEL SECURITY"
            in MIGRATION
        )
        assert f"trg_{table}_append_only" in MIGRATION

    citation_body = _table_body(version_tables[0])
    relation_body = _table_body(version_tables[1])
    assert "REFERENCES legal_citations(legal_citation_id)" in citation_body
    assert (
        "REFERENCES legal_relation_assertions(" in relation_body
    )
    assert "review_version" in citation_body
    assert "supersedes_review_version_id" in citation_body
    assert "maker_id" in citation_body
    assert "reviewer_id" in citation_body
    assert "extensions.digest(" in citation_body
    assert "extensions.digest(" in relation_body


def test_project_citator_overlay_is_causal_and_exactly_chained() -> None:
    version_guard = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_project_citator_review_version()",
        1,
    )[1].split("$legal_project_citator_review_version$;", 1)[0]
    assert "statement_timestamp() - INTERVAL '5 minutes'" in version_guard
    assert "statement_timestamp() + INTERVAL '5 minutes'" in version_guard
    assert "source_asserted_at > NEW.reviewed_at" in version_guard
    assert "delegation.created_at <= $6" in version_guard
    assert "delegation.scope = 'citator_review'" in version_guard
    assert "NEW.maker_id IS DISTINCT FROM source_maker" in version_guard
    assert (
        "NEW.supersedes_review_version_id IS DISTINCT FROM "
        "latest_version_id"
    ) in version_guard
    assert "legal_evidence_snapshot_items" in version_guard

    event_guard = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_project_citator_review_event()",
        1,
    )[1].split("$legal_project_citator_review_event$;", 1)[0]
    assert "does not exactly mirror its review version" in event_guard
    assert (
        "NEW.supersedes_review_event_id\n"
        "            IS DISTINCT FROM predecessor_event_id"
    ) in event_guard
    assert "NEW.metadata_json IS DISTINCT FROM version_metadata" in (
        event_guard
    )
    assert "DEFERRABLE INITIALLY DEFERRED" in MIGRATION


def test_project_citator_review_is_bound_to_current_official_evidence() -> None:
    binding_columns = (
        "legal_evidence_snapshot_id",
        "legal_evidence_item_id",
        "legal_resource_version_id",
        "legal_provision_id",
        "source_fetch_id",
        "provider_id",
        "evidence_exact_locator",
        "bound_excerpt_sha256",
        "bound_provision_sha256",
    )
    for table in (
        "project_legal_citation_review_versions",
        "project_legal_relation_review_versions",
        "project_legal_citation_review_events",
        "project_legal_relation_review_events",
    ):
        table_body = _table_body(table)
        for column in binding_columns:
            assert f"{column}" in table_body
        assert "REFERENCES legal_evidence_snapshots(" in table_body
        assert "REFERENCES legal_evidence_snapshot_items(" in table_body
        assert "REFERENCES legal_provisions(" in table_body
        assert "REFERENCES legal_source_fetches(" in table_body
        assert "bound_excerpt_sha256 ~ '^[0-9a-f]{64}$'" in table_body
        assert "bound_provision_sha256 ~ '^[0-9a-f]{64}$'" in table_body
        assert "'snapshotId'" in table_body
        assert "'sourceAuthority'" in table_body
        assert "'currentEvidenceBinding'" in table_body

    version_guard = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_project_citator_review_version()",
        1,
    )[1].split("$legal_project_citator_review_version$;", 1)[0]
    for marker in (
        "item.legal_evidence_item_id = $4",
        "item.legal_resource_version_id = $5",
        "item.legal_provision_id = $6",
        "item.source_fetch_id = $7",
        "version.provider_id = $8",
        "item.exact_locator = $9",
        "item.excerpt_sha256 = $10",
        "provision.provision_sha256 = $11",
        "$5 = $13",
        "$6 = $14",
        "legal_resource_version_lineages",
        "newer.snapshot_version >",
        "replacement.version_sequence >",
        "item.verified_at + INTERVAL '7 days'",
        "provision.source_locator = item.exact_locator",
        "extensions.digest(",
        "$17 = item.excerpt_sha256",
        "position($16 in provision.body_text) > 0",
    ):
        assert marker in version_guard
    assert "$15 <> 'approved'" in version_guard
    assert "NEW.legal_evidence_snapshot_id" in version_guard
    assert "NEW.bound_provision_sha256" in version_guard

    event_guard = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_project_citator_review_event()",
        1,
    )[1].split("$legal_project_citator_review_event$;", 1)[0]
    for field in binding_columns:
        assert f"review.{field}" in event_guard
        assert f"NEW.{field}" in event_guard


def test_verified_relation_binds_both_sides_to_exact_current_evidence() -> None:
    object_binding_columns = (
        "object_legal_evidence_item_id",
        "object_legal_resource_version_id",
        "object_legal_provision_id",
        "object_source_fetch_id",
        "object_provider_id",
        "object_evidence_exact_locator",
        "object_bound_excerpt_sha256",
        "object_bound_provision_sha256",
    )
    for table in (
        "project_legal_relation_review_versions",
        "project_legal_relation_review_events",
    ):
        table_body = _table_body(table)
        for column in object_binding_columns:
            assert column in table_body
        assert "review_status <> 'approved'" in table_body
        assert "'objectEvidenceItemId'" in table_body
        assert "'objectProvisionSha256'" in table_body

    version_guard = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_project_citator_review_version()",
        1,
    )[1].split("$legal_project_citator_review_version$;", 1)[0]
    for marker in (
        "relation.object_resource_id",
        "NEW.object_legal_evidence_item_id",
        "NEW.object_legal_resource_version_id",
        "NEW.object_legal_provision_id",
        "NEW.object_source_fetch_id",
        "NEW.object_provider_id",
        "NEW.object_evidence_exact_locator",
        "NEW.object_bound_excerpt_sha256",
        "NEW.object_bound_provision_sha256",
        "INTO object_scope_valid",
        "source_object_resource_id IS NULL",
        "NOT COALESCE(object_scope_valid, FALSE)",
    ):
        assert marker in version_guard
    assert version_guard.count(
        "item.verified_at + INTERVAL '7 days'"
    ) >= 2
    assert version_guard.count(
        "replacement.version_sequence >"
    ) >= 2

    event_guard = MIGRATION.split(
        "CREATE OR REPLACE FUNCTION "
        "legal_assert_project_citator_review_event()",
        1,
    )[1].split("$legal_project_citator_review_event$;", 1)[0]
    for column in object_binding_columns:
        assert f"review.{column}" in event_guard
        assert f"NEW.{column}" in event_guard


def test_project_citator_review_does_not_require_global_successors() -> None:
    overlay_section = MIGRATION.split(
        "-- Tenant/project Citator review overlay",
        1,
    )[1]
    assert "Project review never creates a successor" in overlay_section
    assert "REVOKE INSERT, UPDATE, DELETE" in overlay_section
    assert "legal_citations," in overlay_section
    assert "legal_relation_assertions," in overlay_section
