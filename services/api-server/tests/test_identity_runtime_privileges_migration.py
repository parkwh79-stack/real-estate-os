from pathlib import Path


API_ROOT = Path(__file__).parents[1]
MIGRATION = (
    API_ROOT
    / "db"
    / "migrations"
    / "0017_identity_runtime_privileges.sql"
)
TENANTS_SELECT_ONLY_MIGRATION = (
    API_ROOT
    / "db"
    / "migrations"
    / "20260728031948_identity_tenants_select_only.sql"
)
SUPABASE_TENANTS_SELECT_ONLY_MIGRATION = (
    API_ROOT.parents[1]
    / "supabase"
    / "migrations"
    / "20260728031948_identity_tenants_select_only.sql"
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


def test_identity_tenant_directory_is_column_scoped_and_self_auditing() -> None:
    sql = TENANTS_SELECT_ONLY_MIGRATION.read_text(encoding="utf-8")

    assert (
        "REVOKE ALL PRIVILEGES ON TABLE realytics.tenants\n"
        "    FROM realytics_identity;"
    ) in sql
    assert (
        "GRANT SELECT (tenant_id, status) ON TABLE realytics.tenants\n"
        "    TO realytics_identity;"
    ) in sql
    assert "has_column_privilege(" in sql
    assert "'tenant_id', 'SELECT'" in sql
    assert "'status', 'SELECT'" in sql
    assert "'name', 'created_at', 'updated_at', 'metadata_json'" in sql
    assert "forbidden tenant column" in sql
    assert "GRANT SELECT ON TABLE realytics.tenants" not in sql
    for privilege in (
        "INSERT",
        "UPDATE",
        "DELETE",
        "TRUNCATE",
        "REFERENCES",
        "TRIGGER",
    ):
        assert f"'{privilege}'" in sql
        assert f"GRANT {privilege}" not in sql
    assert "realytics_identity_tenants_postflight" in sql


def test_supabase_wrapper_contains_identical_tenant_privilege_sql() -> None:
    source = TENANTS_SELECT_ONLY_MIGRATION.read_text(encoding="utf-8").rstrip(
        "\n"
    )
    rendered = SUPABASE_TENANTS_SELECT_ONLY_MIGRATION.read_text(
        encoding="utf-8"
    )
    begin = (
        "-- BEGIN AUTHORITATIVE REALYTICS SQL: "
        "20260728031948_identity_tenants_select_only.sql\n"
    )
    end = (
        "\n-- END AUTHORITATIVE REALYTICS SQL: "
        "20260728031948_identity_tenants_select_only.sql"
    )

    authoritative_body = rendered.split(begin, 1)[1].split(end, 1)[0]
    assert authoritative_body == source
