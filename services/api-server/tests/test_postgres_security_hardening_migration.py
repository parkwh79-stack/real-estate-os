from __future__ import annotations

import hashlib
import re
from pathlib import Path

from db import postgres
from scripts import render_supabase_migrations as bridge


MIGRATION_NAME = "0011_postgres_security_and_rls_policy_hardening.sql"
MIGRATION_PATH = postgres.MIGRATIONS_DIR / MIGRATION_NAME
SUPABASE_PATH = bridge.SUPABASE_MIGRATIONS_DIR / MIGRATION_NAME


def _sql() -> str:
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _policy_tables(sql: str, policy_name: str) -> tuple[str, ...]:
    return tuple(
        re.findall(
            rf"ALTER\s+POLICY\s+{re.escape(policy_name)}\s+"
            r"ON\s+realytics\.([a-z0-9_]+)",
            sql,
            flags=re.IGNORECASE,
        )
    )


def _authoritative_body(rendered: str) -> str:
    begin = f"-- BEGIN AUTHORITATIVE REALYTICS SQL: {MIGRATION_NAME}\n"
    end = f"\n-- END AUTHORITATIVE REALYTICS SQL: {MIGRATION_NAME}"
    return rendered.split(begin, 1)[1].split(end, 1)[0]


def test_hardening_migration_covers_every_existing_parser_and_legal_policy() -> None:
    sql = _sql()

    assert _policy_tables(sql, "parser_v2_tenant_isolation") == (
        postgres.PARSER_V2_RLS_TABLES
    )
    assert set(_policy_tables(sql, "legal_tenant_isolation")) == {
        *postgres.LEGAL_INTELLIGENCE_RLS_TABLES,
        *postgres.LEGAL_GOVERNANCE_RLS_TABLES,
    }
    assert len(_policy_tables(sql, "legal_tenant_isolation")) == len(
        postgres.LEGAL_INTELLIGENCE_RLS_TABLES
        + postgres.LEGAL_GOVERNANCE_RLS_TABLES
    )


def test_tenant_setting_uses_init_plan_scalar_subselect_and_stays_fail_closed() -> None:
    sql = _sql()
    expected_lookup = (
        "SELECT NULLIF(\n"
        "                pg_catalog.current_setting("
        "'realytics.tenant_id', true),\n"
        "                ''\n"
        "            )"
    )

    # Every one of the 46 policies has both USING and WITH CHECK.
    assert sql.count("pg_catalog.current_setting(") == 92
    assert sql.count(expected_lookup) == 90
    assert sql.count("SELECT NULLIF(") == 92
    assert "NULLIF(current_setting(" not in sql
    assert "current_setting('realytics.tenant_id', false)" not in sql

    policy_blocks = re.findall(
        r"ALTER\s+POLICY\s+(?:parser_v2|legal)_tenant_isolation"
        r".*?;",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert len(policy_blocks) == 46
    for block in policy_blocks:
        assert "USING (" in block
        assert "WITH CHECK (" in block
        assert block.count("SELECT NULLIF(") == 2
        assert block.count(
            "pg_catalog.current_setting('realytics.tenant_id', true)"
        ) == 2 or "legal_provider_replay_events" in block


def test_replay_event_policy_preserves_request_derived_tenant_scope() -> None:
    sql = _sql()
    replay_policy = sql.split(
        "ON realytics.legal_provider_replay_events",
        1,
    )[1].split("ALTER POLICY legal_tenant_isolation", 1)[0]

    assert replay_policy.count("EXISTS (") == 2
    assert replay_policy.count(
        "FROM realytics.legal_provider_replay_requests"
    ) == 2
    assert replay_policy.count(
        "replay_request.legal_provider_replay_request_id"
    ) == 2
    assert replay_policy.count("SELECT NULLIF(") == 2


def test_function_search_paths_and_public_execute_are_hardened() -> None:
    sql = _sql()

    coverage_definer_functions = (
        "legal_assert_coverage_registry_lineage",
        "legal_assert_coverage_contract_identity",
        "legal_assert_coverage_registry_complete",
        "legal_assert_coverage_bound_sync_run",
        "legal_assert_coverage_bound_completion",
    )
    empty_search_path_functions = (
        "parser_reject_append_only_mutation",
        "legal_reject_append_only_mutation",
        *coverage_definer_functions,
    )
    for function_name in empty_search_path_functions:
        assert (
            f"ALTER FUNCTION realytics.{function_name}()\n"
            "    SET search_path = '';"
        ) in sql
        assert (
            f"REVOKE ALL ON FUNCTION realytics.{function_name}()\n"
            "    FROM PUBLIC;"
        ) in sql

    assert (
        "ALTER FUNCTION "
        "realytics.legal_validate_resource_version_lineage()\n"
        "    SET search_path = pg_catalog, realytics;"
    ) in sql
    assert (
        "REVOKE ALL ON FUNCTION "
        "realytics.legal_validate_resource_version_lineage()\n"
        "    FROM PUBLIC;"
    ) in sql

    migration_0008 = (
        postgres.MIGRATIONS_DIR / "0008_legal_intelligence_core.sql"
    ).read_text(encoding="utf-8")
    migration_0009 = (
        postgres.MIGRATIONS_DIR
        / "0009_legal_governance_and_tenant_overlay.sql"
    ).read_text(encoding="utf-8")
    migration_0010 = (
        postgres.MIGRATIONS_DIR / "0010_legal_corpus_coverage_contracts.sql"
    ).read_text(encoding="utf-8")
    assert "SECURITY DEFINER" not in migration_0008
    assert "SECURITY DEFINER" not in migration_0009
    discovered_definers = set(
        re.findall(
            r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+([a-z0-9_]+)\(\)"
            r".*?SECURITY\s+DEFINER",
            migration_0010,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    assert discovered_definers == set(coverage_definer_functions)


def test_optional_public_rls_helper_is_kept_but_not_publicly_executable() -> None:
    sql = _sql()

    assert (
        "pg_catalog.to_regprocedure('public.rls_auto_enable()') IS NOT NULL"
        in sql
    )
    assert (
        "'REVOKE EXECUTE ON FUNCTION "
        "public.rls_auto_enable() FROM PUBLIC'"
    ) in sql
    assert not re.search(
        r"DROP\s+(?:FUNCTION|EVENT\s+TRIGGER).*rls_auto_enable",
        sql,
        flags=re.IGNORECASE,
    )


def test_hardening_migration_is_sql_splitter_safe() -> None:
    statements = postgres._split_sql_statements(_sql())
    helper_acl_blocks = [
        statement
        for statement in statements
        if statement.startswith("DO $realytics_rls_auto_enable_acl$")
    ]

    assert len(helper_acl_blocks) == 1
    assert "to_regprocedure" in helper_acl_blocks[0]
    assert len(statements) == 63


def test_policy_hardening_is_repeatable_and_never_opens_an_acl_gap() -> None:
    sql = _sql()
    all_policy_tables = (
        _policy_tables(sql, "parser_v2_tenant_isolation")
        + _policy_tables(sql, "legal_tenant_isolation")
    )

    assert len(all_policy_tables) == len(set(all_policy_tables)) == 46
    assert not re.search(
        r"(?:DROP|CREATE)\s+POLICY",
        sql,
        flags=re.IGNORECASE,
    )
    assert "GRANT " not in sql
    assert "ALTER TABLE" not in sql


def test_supabase_wrapper_embeds_exact_source_and_checksum() -> None:
    source_text = bridge.source_text(MIGRATION_PATH)
    rendered = SUPABASE_PATH.read_text(encoding="utf-8")
    checksum = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    assert _authoritative_body(rendered) == source_text.rstrip("\n")
    assert f"-- Authoritative normalized SHA-256: {checksum}" in rendered
    assert (
        f"VALUES ('{MIGRATION_NAME}', '{checksum}')"
        in rendered
    )
    assert bridge.sync_migrations(check=True) == ()
