from __future__ import annotations

import hashlib
from pathlib import Path

from db import postgres
from scripts import render_supabase_migrations as bridge


EXPECTED_MIGRATIONS = (
    "0001_initial_schema.sql",
    "0002_durable_agent_runtime.sql",
    "0003_agent_platform_operational_control.sql",
    "0004_project_and_async_job_authority.sql",
    "0005_assumption_proposals_and_project_versions.sql",
    "0006_parser_v2_platform.sql",
    "0007_parser_v2_rls.sql",
    "0008_legal_intelligence_core.sql",
    "0009_legal_governance_and_tenant_overlay.sql",
    "0010_legal_corpus_coverage_contracts.sql",
    "0011_postgres_security_and_rls_policy_hardening.sql",
    "0012_core_tenant_authority_expand.sql",
    "0013_core_tenant_rls.sql",
    "0014_core_tenant_rls_permissive_base.sql",
    "0015_supabase_security_and_rls_policy_cleanup.sql",
    "0016_identity_invitation_lifecycle.sql",
    "0017_identity_runtime_privileges.sql",
    "20260727002612_normalized_report_publication.sql",
    "20260727002700_report_publication_lineage_lookup_fix.sql",
    "20260728031948_identity_tenants_select_only.sql",
    "20260728083451_user_activity_append_boundary.sql",
    "20260730112222_tenant_membership_role_administration.sql",
    "20260730122658_verified_principal_onboarding.sql",
    "20260731093559_calculation_run_tenant_read_index.sql",
    "20260915005540_legal_governance_trigger_record_dispatch.sql",
    "20260915060531_runtime_role_privilege_alignment.sql",
)


def _authoritative_body(rendered: str, filename: str) -> str:
    begin = f"-- BEGIN AUTHORITATIVE REALYTICS SQL: {filename}\n"
    end = f"\n-- END AUTHORITATIVE REALYTICS SQL: {filename}"
    return rendered.split(begin, 1)[1].split(end, 1)[0]


def test_generator_is_deterministic_and_check_is_read_only(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    source = source_dir / "0001_example.sql"
    source.write_bytes(b"CREATE TABLE example (id TEXT PRIMARY KEY);\r\n")

    bridge.sync_migrations(source_dir=source_dir, output_dir=output_dir)
    generated = output_dir / source.name
    first_bytes = generated.read_bytes()

    assert bridge.sync_migrations(
        source_dir=source_dir,
        output_dir=output_dir,
        check=True,
    ) == ()

    bridge.sync_migrations(source_dir=source_dir, output_dir=output_dir)
    assert generated.read_bytes() == first_bytes

    generated.write_text("drift\n", encoding="utf-8")
    assert bridge.sync_migrations(
        source_dir=source_dir,
        output_dir=output_dir,
        check=True,
    ) == ("changed:0001_example.sql",)
    assert generated.read_text(encoding="utf-8") == "drift\n"


def test_generated_wrappers_match_every_authoritative_migration() -> None:
    sources = bridge.discover_sources()
    expected = bridge.expected_outputs()
    generated = sorted(bridge.SUPABASE_MIGRATIONS_DIR.glob("*.sql"))

    assert tuple(path.name for path in sources) == EXPECTED_MIGRATIONS
    assert tuple(expected) == EXPECTED_MIGRATIONS
    assert tuple(path.name for path in generated) == EXPECTED_MIGRATIONS

    for source in sources:
        rendered = expected[source.name]
        canonical_source = bridge.source_text(source).rstrip("\n")
        assert _authoritative_body(rendered, source.name) == canonical_source
        assert bridge.source_checksum(source) == postgres._migration_checksum(
            source
        )
        assert bridge.source_checksum(source) == hashlib.sha256(
            bridge.source_text(source).encode("utf-8")
        ).hexdigest()
        assert (
            bridge.SUPABASE_MIGRATIONS_DIR / source.name
        ).read_text(encoding="utf-8") == rendered


def test_security_prelude_is_before_authoritative_sql() -> None:
    for filename, rendered in bridge.expected_outputs().items():
        source_boundary = rendered.index(
            f"-- BEGIN AUTHORITATIVE REALYTICS SQL: {filename}"
        )
        required_prelude = (
            "SET statement_timeout = '10min';",
            "SET lock_timeout = '10s';",
            "CREATE SCHEMA IF NOT EXISTS extensions;",
            "REVOKE CREATE ON SCHEMA extensions FROM PUBLIC;",
            "CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;",
            "installed_schema IS DISTINCT FROM 'extensions'",
            "CREATE SCHEMA IF NOT EXISTS realytics;",
            "REVOKE ALL ON SCHEMA realytics\n"
            "    FROM PUBLIC, anon, authenticated, service_role;",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA realytics\n"
            "    REVOKE ALL ON TABLES\n"
            "    FROM PUBLIC, anon, authenticated, service_role;",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA realytics\n"
            "    REVOKE ALL ON SEQUENCES\n"
            "    FROM PUBLIC, anon, authenticated, service_role;",
            "ALTER DEFAULT PRIVILEGES IN SCHEMA realytics\n"
            "    REVOKE ALL ON FUNCTIONS\n"
            "    FROM PUBLIC, anon, authenticated, service_role;",
            "REVOKE ALL ON ALL TABLES IN SCHEMA realytics\n"
            "    FROM PUBLIC, anon, authenticated, service_role;",
            "REVOKE ALL ON ALL SEQUENCES IN SCHEMA realytics\n"
            "    FROM PUBLIC, anon, authenticated, service_role;",
            "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA realytics\n"
            "    FROM PUBLIC, anon, authenticated, service_role;",
            "SET search_path TO realytics, extensions, public, pg_temp;",
            "CREATE TABLE IF NOT EXISTS realytics.schema_migrations",
            "REVOKE ALL ON TABLE realytics.schema_migrations\n"
            "    FROM PUBLIC, anon, authenticated, service_role;",
        )
        assert all(
            rendered.index(fragment) < source_boundary
            for fragment in required_prelude
        )


def test_source_checksum_guard_precedes_sql_and_ledger_record_follows_it() -> None:
    for source in bridge.discover_sources():
        rendered = bridge.render_migration(source)
        checksum = bridge.source_checksum(source)
        ledger_create = rendered.index(
            "CREATE TABLE IF NOT EXISTS realytics.schema_migrations"
        )
        guard = rendered.index("DO $realytics_source_checksum$")
        source_begin = rendered.index(
            f"-- BEGIN AUTHORITATIVE REALYTICS SQL: {source.name}"
        )
        source_end = rendered.index(
            f"-- END AUTHORITATIVE REALYTICS SQL: {source.name}"
        )
        post_source_acl = rendered.index(
            "REVOKE ALL ON ALL TABLES IN SCHEMA realytics",
            source_end,
        )
        footer = rendered.index(
            "INSERT INTO realytics.schema_migrations (version, checksum)"
        )

        assert (
            ledger_create
            < guard
            < source_begin
            < source_end
            < post_source_acl
            < footer
        )
        assert f"WHERE version = '{source.name}'" in rendered
        assert f"recorded_checksum <> '{checksum}'" in rendered
        assert f"VALUES ('{source.name}', '{checksum}')" in rendered
        assert "INSERT INTO supabase_migrations.schema_migrations" not in rendered
        assert "UPDATE supabase_migrations.schema_migrations" not in rendered
        assert rendered.endswith("ON CONFLICT (version) DO NOTHING;\n")
