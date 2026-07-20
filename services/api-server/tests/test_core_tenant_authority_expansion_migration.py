from __future__ import annotations

import re
from pathlib import Path

from db.postgres import _split_sql_statements
from scripts import render_supabase_migrations as bridge


API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_NAME = "0012_core_tenant_authority_expand.sql"
CANONICAL_MIGRATION = API_ROOT / "db" / "migrations" / MIGRATION_NAME
SUPABASE_WRAPPER = (
    REPOSITORY_ROOT / "supabase" / "migrations" / MIGRATION_NAME
)

REQUIRED_PROJECT_TABLES = {
    "reports",
    "orchestrations",
    "market_evidence",
    "land_evidence",
    "risk_registers",
    "evidence_graphs",
    "chief_pod_artifacts",
    "pod_ai_reviews",
    "agent_handoffs",
    "recalculation_events",
    "calculation_runs",
    "deal_state_records",
    "prompt_artifacts",
    "assumption_overrides",
    "restructuring_cures",
    "project_snapshots",
    "legal_reviews",
    "permit_reviews",
    "assumption_proposals",
    "project_versions",
}
PARENT_BOUND_TABLES = {"agent_run_steps"}
OPTIONAL_PROJECT_TABLES = {
    "glossary_term_candidates",
    "land_parcels",
}
AUDIT_LOCATOR_TABLES = {"user_activity_logs"}
ALL_NEW_TENANT_COLUMNS = (
    (REQUIRED_PROJECT_TABLES - {"project_versions"})
    | PARENT_BOUND_TABLES
    | OPTIONAL_PROJECT_TABLES
    | AUDIT_LOCATOR_TABLES
)


def _canonical_sql() -> str:
    return CANONICAL_MIGRATION.read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )


def _authoritative_body(rendered: str) -> str:
    begin = f"-- BEGIN AUTHORITATIVE REALYTICS SQL: {MIGRATION_NAME}\n"
    end = f"\n-- END AUTHORITATIVE REALYTICS SQL: {MIGRATION_NAME}"
    return rendered.split(begin, 1)[1].split(end, 1)[0]


def _declared_array(sql: str, variable_name: str) -> set[str]:
    matches = re.findall(
        rf"{variable_name}\s+CONSTANT\s+TEXT\[\]\s*:=\s*ARRAY\[(.*?)\];",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert matches, f"missing {variable_name} declaration"
    values: set[str] = set()
    for body in matches:
        values.update(re.findall(r"'([a-z0-9_]+)'", body))
    return values


def test_identity_tables_make_membership_the_authoritative_role_contract() -> None:
    sql = _canonical_sql()

    assert "CREATE TABLE IF NOT EXISTS principals (" in sql
    assert "PRIMARY KEY (issuer, subject)" in sql
    assert "CREATE TABLE IF NOT EXISTS tenant_memberships (" in sql
    assert "PRIMARY KEY (tenant_id, issuer, subject)" in sql
    assert "FOREIGN KEY (issuer, subject)" in sql
    assert "REFERENCES principals(issuer, subject)" in sql
    assert "membership_version  BIGINT NOT NULL DEFAULT 1" in sql
    assert "CHECK (membership_version > 0)" in sql
    assert "'active',\n                'inactive'," in sql
    assert "'legal_counsel_approver'" in sql
    assert "request and user metadata are not authorization inputs" in sql
    assert "CREATE TABLE IF NOT EXISTS project_memberships" not in sql


def test_legacy_identity_backfill_is_verified_supabase_only_and_fail_closed() -> None:
    sql = _canonical_sql()
    lowered = sql.lower()

    assert "metadata_json ->> 'authSource' = 'bearer_token'" in sql
    assert "supabase[.](co|in)/auth/v1" in sql
    assert "'legacy_supabase_signed_bearer'" in sql
    assert "join tenants as tenant" in lowered
    assert "insert into tenants" not in lowered
    assert "user_metadata" not in lowered
    assert "raw_user_meta_data" not in lowered
    assert "payload_json ->> 'tenant" not in sql
    assert "metadata_json ->> 'tenant" not in sql
    assert "alter table users alter column tenant_id set not null" not in lowered
    assert (
        "alter table sessions alter column tenant_id set not null"
        not in lowered
    )


def test_every_audited_missing_core_table_receives_a_tenant_column() -> None:
    sql = _canonical_sql()
    added_tables = {
        match.group(1).lower()
        for match in re.finditer(
            r"ALTER TABLE\s+([a-z0-9_]+)\s+"
            r"ADD COLUMN IF NOT EXISTS tenant_id TEXT;",
            sql,
            flags=re.IGNORECASE,
        )
    }

    assert added_tables == ALL_NEW_TENANT_COLUMNS
    assert _declared_array(sql, "required_project_tables") == (
        REQUIRED_PROJECT_TABLES
    )
    assert _declared_array(sql, "optional_project_tables") == (
        OPTIONAL_PROJECT_TABLES
    )


def test_project_authority_backfill_is_lineage_only_and_fail_closed() -> None:
    sql = _canonical_sql()

    add_columns_at = sql.index(
        "ALTER TABLE reports ADD COLUMN IF NOT EXISTS tenant_id TEXT;"
    )
    collect_at = sql.index(
        "CREATE TEMPORARY TABLE realytics_project_tenant_candidates"
    )
    ambiguity_at = sql.index("DO $reject_ambiguous_project_tenants$")
    project_update_at = sql.index("UPDATE projects AS project")
    project_preflight_at = sql.index("DO $project_tenant_preflight$")
    project_not_null_at = sql.index(
        "ALTER TABLE projects ALTER COLUMN tenant_id SET NOT NULL;"
    )

    assert (
        add_columns_at
        < collect_at
        < ambiguity_at
        < project_update_at
        < project_preflight_at
        < project_not_null_at
    )
    assert "count(DISTINCT candidate.tenant_id) > 1" in sql
    assert "migration 0012 never chooses between tenants" in sql
    assert "migration 0012 does not create tenants" in sql
    assert "FROM information_schema.columns" in sql
    assert "column_inventory.column_name IN ('project_id', 'tenant_id')" in sql


def test_project_cas_allocator_indexes_and_composite_fks_are_declared() -> None:
    sql = _canonical_sql()

    assert (
        "ADD COLUMN IF NOT EXISTS state_version BIGINT NOT NULL DEFAULT 1"
        in sql
    )
    assert "CHECK (state_version > 0)" in sql
    assert "CREATE SEQUENCE IF NOT EXISTS project_id_sequence" in sql
    assert "nextval('project_id_sequence'::regclass)" in sql
    assert "max(project_id::BIGINT)" in sql
    assert "UNIQUE (tenant_id, project_id)" in sql
    assert "'FOREIGN KEY (tenant_id, project_id) '" in sql
    assert "'REFERENCES projects(tenant_id, project_id) '" in sql
    assert "'(tenant_id, project_id)'" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_tenant_run" in sql
    assert "FOREIGN KEY (tenant_id, run_id)" in sql
    assert "REFERENCES agent_runs(tenant_id, run_id)" in sql


def test_optional_global_stores_remain_nullable_but_project_rows_are_bound() -> None:
    sql = _canonical_sql()
    lowered = sql.lower()

    for table in OPTIONAL_PROJECT_TABLES:
        assert (
            f"alter table {table} alter column tenant_id set not null"
            not in lowered
        )
        assert table in _declared_array(sql, "optional_project_tables")

    assert "'CHECK (project_id IS NULL OR ('" in sql
    assert "'btrim(project_id) <> '''' AND tenant_id IS NOT NULL'" in sql


def test_activity_project_locator_is_not_miscast_as_a_project_foreign_key() -> None:
    sql = _canonical_sql()
    lowered = sql.lower()

    assert (
        "alter table user_activity_logs alter column tenant_id set not null"
        not in lowered
    )
    assert "fk_user_activity_logs_tenant_project" not in sql
    assert "Retaining % user activity project locator(s)" in sql
    assert "fk_user_activity_logs_tenant" in sql


def test_expansion_does_not_enable_rls_or_create_policies() -> None:
    lowered = _canonical_sql().lower()

    assert "enable row level security" not in lowered
    assert "force row level security" not in lowered
    assert "create policy" not in lowered


def test_canonical_sql_parses_into_complete_migration_statements() -> None:
    statements = _split_sql_statements(_canonical_sql())

    assert len(statements) >= 60
    assert statements[0].startswith(
        "CREATE TABLE IF NOT EXISTS principals"
    )
    assert statements[-1].startswith("COMMENT ON SEQUENCE")
    assert all(statement.rstrip().endswith(";") for statement in statements)


def test_supabase_wrapper_is_generated_from_byte_identical_canonical_body() -> None:
    canonical = _canonical_sql().rstrip("\n")
    rendered = SUPABASE_WRAPPER.read_text(encoding="utf-8").replace(
        "\r\n", "\n"
    )

    assert rendered == bridge.render_migration(CANONICAL_MIGRATION)
    assert _authoritative_body(rendered) == canonical
