from pathlib import Path


API_ROOT = Path(__file__).parents[1]
MIGRATION = (
    API_ROOT
    / "db"
    / "migrations"
    / "0017_identity_runtime_privileges.sql"
)
ROLES = API_ROOT.parents[1] / "supabase" / "roles.sql"


def test_identity_role_is_credential_free_and_cannot_bypass_rls() -> None:
    sql = ROLES.read_text(encoding="utf-8")
    identity = sql.split("DO $realytics_identity_role$", maxsplit=1)[1]

    assert "CREATE ROLE realytics_identity" in identity
    assert "NOLOGIN" in identity
    assert "NOBYPASSRLS" in identity
    assert "NOINHERIT" in identity
    assert "PASSWORD" not in identity.upper()


def test_identity_privilege_migration_is_exact_and_self_auditing() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "GRANT USAGE ON SCHEMA realytics" in sql
    assert "REVOKE CREATE ON SCHEMA realytics" in sql
    assert "GRANT SELECT, INSERT ON TABLE realytics.principals" in sql
    assert "GRANT UPDATE (" in sql
    assert "email, display_name, last_verified_at, updated_at" in sql
    assert "GRANT SELECT, INSERT ON TABLE realytics.tenant_memberships" in sql
    assert "GRANT SELECT, INSERT, UPDATE ON TABLE realytics.tenant_invitations" in sql
    assert "identity_invitation_token_index" in sql
    assert "tenant_identity_access_events" in sql
    assert "forbidden table privilege" in sql
    assert "broad UPDATE privilege" in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT TRUNCATE" not in sql
