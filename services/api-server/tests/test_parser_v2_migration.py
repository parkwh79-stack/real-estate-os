from __future__ import annotations

from db import postgres


_PARSER_V2_TENANT_TABLES = (
    "parser_document_versions",
    "parser_project_profiles",
    "parser_runs",
    "parser_run_events",
    "parser_candidate_set_seals",
    "parsed_fact_candidates",
    "parser_candidate_inputs",
    "parser_validation_findings",
    "parser_validation_finding_events",
    "parser_review_decisions",
    "parser_source_conflicts",
    "parser_source_conflict_events",
    "parser_conflict_candidates",
    "parser_review_decision_conflicts",
    "parser_canonical_selections",
    "parser_approved_snapshots",
    "parser_snapshot_selections",
    "parser_snapshot_source_runs",
    "parser_validity_events",
)


def test_parser_v2_migration_keeps_conflict_resolution_append_only() -> None:
    ddl = "\n".join(postgres.schema_ddl())

    assert "CREATE TABLE IF NOT EXISTS parser_source_conflicts" in ddl
    assert "CREATE TABLE IF NOT EXISTS parser_source_conflict_events" in ddl
    assert "conflict_status IN ('resolved', 'waived')" in ddl
    assert "trg_parser_source_conflict_events_append_only" in ddl


def test_parser_v2_migration_separates_execution_and_approval_status() -> None:
    ddl = "\n".join(postgres.schema_ddl())

    assert "CREATE TABLE IF NOT EXISTS parser_runs" in ddl
    assert "execution_status" in ddl
    assert "approval_status" in ddl
    assert "CHECK (approval_status = 'not_requested')" in ddl
    assert "CHECK (approval_status IN ('not_requested', 'pending', 'approved', 'rejected', 'superseded'))" in ddl


def test_parser_v2_migration_versions_documents_and_snapshots_within_scope() -> None:
    ddl = "\n".join(postgres.schema_ddl())

    assert "document_id                     TEXT NOT NULL" in ddl
    assert "UNIQUE (tenant_id, project_id, document_id, version_number)" in ddl
    assert "purpose                TEXT NOT NULL" in ddl
    assert "UNIQUE (tenant_id, project_id, purpose, snapshot_version)" in ddl


def test_parser_v2_migration_pins_project_profile_and_execution_plan() -> None:
    ddl = "\n".join(postgres.schema_ddl())

    assert "CREATE TABLE IF NOT EXISTS parser_project_profiles" in ddl
    assert "PRIMARY KEY (tenant_id, project_id, profile_id, profile_version)" in ddl
    assert "FOREIGN KEY (project_id, tenant_id)" in ddl
    assert "REFERENCES projects(project_id, tenant_id)" in ddl
    assert "profile_id            TEXT NOT NULL" in ddl
    assert "profile_version       TEXT NOT NULL" in ddl
    assert "plan_hash             TEXT NOT NULL" in ddl
    assert "requested_modules     TEXT[] NOT NULL" in ddl
    assert "CHECK (plan_hash ~ '^[0-9a-f]{64}$')" in ddl
    assert "UNIQUE (tenant_id, project_id, profile_id, profile_version, plan_hash)" in ddl
    assert "FOREIGN KEY (tenant_id, project_id, profile_id, profile_version, plan_hash)" in ddl
    assert (
        "REFERENCES parser_project_profiles(tenant_id, project_id, profile_id, "
        "profile_version, plan_hash)" in ddl
    )
    assert "COMMENT ON TABLE parser_project_profiles" in ddl
    assert "persist the built-in/default intake profile for its tenant/project before creating parser_runs" in ddl
    assert "parser_runs never reference an implicit global profile" in ddl
    assert "idx_parser_runs_profile" in ddl
    assert "idx_parser_runs_plan_hash" in ddl
    assert "trg_parser_project_profiles_append_only" in ddl


def test_parser_v2_migration_indexes_field_registry_validation_state() -> None:
    ddl = "\n".join(postgres.schema_ddl())

    assert "field_registry_version   TEXT NOT NULL" in ddl
    assert "validation_status        TEXT NOT NULL DEFAULT 'passed'" in ddl
    assert "validation_codes         TEXT[] NOT NULL" in ddl
    assert "validation_finding_count INTEGER NOT NULL DEFAULT 0" in ddl
    assert "validation_status IN ('passed', 'passed_with_findings', 'blocked')" in ddl
    assert "validation_status <> 'blocked'" in ddl
    assert "validation_finding_count > 0 AND cardinality(validation_codes) > 0" in ddl
    assert "idx_parsed_fact_candidates_validation" in ddl
    assert "OR validation_status = 'blocked'" in ddl
    assert "field_registry_version       TEXT NOT NULL" in ddl
    assert "field_registry_version TEXT NOT NULL" in ddl
    assert "idx_parser_canonical_selections_registry" in ddl
    assert "idx_parser_approved_snapshots_registry" in ddl


def test_parser_v2_validation_findings_are_immutable_and_resolution_is_evented() -> None:
    ddl = "\n".join(postgres.schema_ddl())
    migration = (postgres.MIGRATIONS_DIR / "0006_parser_v2_platform.sql").read_text(encoding="utf-8")
    finding_table = migration.split(
        "CREATE TABLE IF NOT EXISTS parser_validation_findings (", 1
    )[1].split("\n);", 1)[0]

    assert "CREATE TABLE IF NOT EXISTS parser_validation_findings" in ddl
    assert "FOREIGN KEY (parsing_run_id, tenant_id, project_id)" in ddl
    assert "REFERENCES parser_runs(parsing_run_id, tenant_id, project_id)" in ddl
    assert "FOREIGN KEY (candidate_id, parsing_run_id, tenant_id, project_id)" in ddl
    assert "REFERENCES parsed_fact_candidates(candidate_id, parsing_run_id, tenant_id, project_id)" in ddl
    assert "code ~ '^[A-Z][A-Z0-9_]*$'" in ddl
    assert "severity IN ('info', 'warning', 'error', 'critical')" in ddl
    assert "resolved_at" not in finding_table
    assert "resolved_by" not in finding_table
    assert "resolution_note" not in finding_table
    assert "CREATE TABLE IF NOT EXISTS parser_validation_finding_events" in ddl
    assert "FOREIGN KEY (finding_id, tenant_id, project_id)" in ddl
    assert "REFERENCES parser_validation_findings(finding_id, tenant_id, project_id)" in ddl
    assert "event_type IN ('resolved', 'reopened', 'waived')" in ddl
    assert "CHECK (sequence_number >= 1)" in ddl
    assert "CHECK (btrim(actor_id) <> '')" in ddl
    assert "CHECK (btrim(resolution_note) <> '')" in ddl
    assert "idx_parser_validation_findings_queue" in ddl
    assert "idx_parser_validation_findings_candidate" in ddl
    assert "idx_parser_validation_findings_code" in ddl
    assert "idx_parser_validation_finding_events_finding" in ddl
    assert "idx_parser_validation_finding_events_type" in ddl
    assert "trg_parser_validation_findings_append_only" in ddl
    assert "trg_parser_validation_finding_events_append_only" in ddl


def test_parser_v2_candidate_set_seal_is_single_run_scoped_and_append_only() -> None:
    ddl = "\n".join(postgres.schema_ddl())

    assert "CREATE TABLE IF NOT EXISTS parser_candidate_set_seals" in ddl
    assert "candidate_set_hash TEXT NOT NULL" in ddl
    assert "candidate_count    INTEGER NOT NULL" in ddl
    assert "conflict_count     INTEGER NOT NULL DEFAULT 0" in ddl
    assert "UNIQUE (tenant_id, project_id, parsing_run_id)" in ddl
    assert "FOREIGN KEY (parsing_run_id, tenant_id, project_id)" in ddl
    assert "REFERENCES parser_runs(parsing_run_id, tenant_id, project_id)" in ddl
    assert "CHECK (candidate_set_hash ~ '^[0-9a-f]{64}$')" in ddl
    assert "CHECK (candidate_count >= 1 AND conflict_count >= 0)" in ddl
    assert "idx_parser_candidate_set_seals_project" in ddl
    assert "trg_parser_candidate_set_seals_append_only" in ddl


def test_parser_v2_derived_candidate_binds_engine_output_and_input_snapshot() -> None:
    ddl = "\n".join(postgres.schema_ddl())

    assert "calculation_engine_version TEXT" in ddl
    assert "calculation_output_path  TEXT" in ddl
    assert "calculation_output_hash  TEXT" in ddl
    assert "input_snapshot_hash      TEXT" in ddl
    assert "origin_type = 'derived'" in ddl
    assert "calculation_engine_version IS NOT NULL" in ddl
    assert "calculation_output_path IS NOT NULL" in ddl
    assert "calculation_output_hash IS NOT NULL" in ddl
    assert "input_snapshot_hash IS NOT NULL" in ddl
    assert "origin_type <> 'derived'" in ddl
    assert "calculation_engine_version IS NULL" in ddl
    assert "calculation_output_path IS NULL" in ddl
    assert "calculation_output_hash IS NULL" in ddl
    assert "input_snapshot_hash IS NULL" in ddl
    assert "calculation_output_hash ~ '^[0-9a-f]{64}$'" in ddl
    assert "input_snapshot_hash ~ '^[0-9a-f]{64}$'" in ddl
    assert "idx_parsed_fact_candidates_calculation" in ddl


def test_parser_v2_rls_migration_covers_every_tenant_table_and_fails_closed() -> None:
    migration = (postgres.MIGRATIONS_DIR / "0007_parser_v2_rls.sql").read_text(
        encoding="utf-8"
    )
    tenant_expression = (
        "tenant_id = NULLIF(current_setting('realytics.tenant_id', true), '')"
    )

    assert len(_PARSER_V2_TENANT_TABLES) == 19
    assert migration.count("ENABLE ROW LEVEL SECURITY;") == len(
        _PARSER_V2_TENANT_TABLES
    )
    assert migration.count("FORCE ROW LEVEL SECURITY;") == len(
        _PARSER_V2_TENANT_TABLES
    )
    assert migration.count("CREATE POLICY parser_v2_tenant_isolation") == len(
        _PARSER_V2_TENANT_TABLES
    )
    assert migration.count(tenant_expression) == 2 * len(_PARSER_V2_TENANT_TABLES)

    for table in _PARSER_V2_TENANT_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in migration
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;" in migration
        assert (
            f"DROP POLICY IF EXISTS parser_v2_tenant_isolation ON {table};"
            in migration
        )
        assert f"CREATE POLICY parser_v2_tenant_isolation ON {table}" in migration

    assert "runtime_outbox_events" not in migration


def test_parser_v2_rls_migration_uses_splitter_safe_static_sql() -> None:
    migration = (postgres.MIGRATIONS_DIR / "0007_parser_v2_rls.sql").read_text(
        encoding="utf-8"
    )
    ddl = "\n".join(postgres.schema_ddl())

    assert "DO $$" not in migration
    assert "EXECUTE format" not in migration
    for table in _PARSER_V2_TENANT_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;" in ddl
        assert f"CREATE POLICY parser_v2_tenant_isolation ON {table}" in ddl
