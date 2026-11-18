from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "db"
    / "migrations"
    / "0015_supabase_security_and_rls_policy_cleanup.sql"
)


def test_supabase_security_cleanup_revokes_explicit_public_rpc_roles() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "public.rls_auto_enable()" in sql
    assert "FROM PUBLIC, anon, authenticated, service_role" in sql


def test_supabase_security_cleanup_only_drops_replaced_tenant_policies() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "'parser_v2_tenant_isolation'" in sql
    assert "'legal_tenant_isolation'" in sql
    assert "'tenant_scope_' || relation.relname" in sql
    assert "DROP POLICY %I ON %I.%I" in sql
