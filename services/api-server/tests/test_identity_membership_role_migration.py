from pathlib import Path


API_ROOT = Path(__file__).parents[1]
FILENAME = "20260730112222_tenant_membership_role_administration.sql"
SOURCE = API_ROOT / "db" / "migrations" / FILENAME
WRAPPER = API_ROOT.parents[1] / "supabase" / "migrations" / FILENAME


def test_membership_role_audit_migration_is_append_only_and_forced_rls() -> None:
    sql = SOURCE.read_text(encoding="utf-8")

    assert "ALTER TABLE tenant_identity_access_events ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE tenant_identity_access_events FORCE ROW LEVEL SECURITY" in sql
    assert "DROP POLICY IF EXISTS tenant_scope_tenant_identity_access_events" in sql
    assert "SELECT NULLIF(" in sql
    assert "current_setting('realytics.tenant_id', true)" in sql
    assert "identity_reject_append_only_mutation" in sql
    assert "BEFORE UPDATE OR DELETE ON tenant_identity_access_events" in sql
    assert "tenant_scope_tenant_identity_access_events" in sql
    assert "REVOKE ALL ON FUNCTION identity_reject_append_only_mutation() FROM PUBLIC" in sql
    assert "GRANT" not in sql
    assert "TO realytics_identity" not in sql
    assert "FROM realytics_identity" not in sql
    assert "TO realytics_runtime" not in sql
    assert "FROM realytics_runtime" not in sql


def test_supabase_wrapper_contains_identical_membership_audit_sql() -> None:
    source = SOURCE.read_text(encoding="utf-8").rstrip("\n")
    rendered = WRAPPER.read_text(encoding="utf-8")
    begin = f"-- BEGIN AUTHORITATIVE REALYTICS SQL: {FILENAME}\n"
    end = f"\n-- END AUTHORITATIVE REALYTICS SQL: {FILENAME}"

    authoritative_body = rendered.split(begin, 1)[1].split(end, 1)[0]
    assert authoritative_body == source
