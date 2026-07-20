from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "db"
    / "migrations"
    / "0014_core_tenant_rls_permissive_base.sql"
)


def test_core_tenant_rls_has_a_permissive_tenant_base_policy() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "DROP POLICY IF EXISTS %I ON %I" in sql
    assert "AS PERMISSIVE" in sql
    assert "realytics.tenant_id" in sql
    assert "WITH CHECK" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
