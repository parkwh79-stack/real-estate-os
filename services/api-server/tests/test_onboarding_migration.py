from __future__ import annotations

from pathlib import Path

from scripts import render_supabase_migrations as bridge


MIGRATION = "20260730122658_verified_principal_onboarding.sql"
API_ROOT = Path(__file__).resolve().parents[1]
SOURCE = API_ROOT / "db" / "migrations" / MIGRATION
WRAPPER = bridge.SUPABASE_MIGRATIONS_DIR / MIGRATION


def test_onboarding_migration_has_verified_principal_force_rls_boundary() -> None:
    sql = SOURCE.read_text(encoding="utf-8")

    for table in (
        "onboarding_profiles",
        "onboarding_principal_events",
        "platform_onboarding_operators",
        "tenant_access_requests",
        "tenant_access_request_events",
    ):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
    assert "realytics.principal_issuer" in sql
    assert "realytics.principal_subject" in sql
    assert "tenant_access_request_principal_insert" in sql
    assert "tenant_access_request_principal_resubmit" in sql
    assert "tenant_access_request_reviewer_decision" in sql
    assert "requester_issuer = decision_by_issuer" in sql
    assert "requester_subject = decision_by_subject" in sql
    assert "decision_by_issuer = NULLIF" in sql
    assert "role = 'admin'" in sql
    assert "platform_onboarding_operators" in sql


def test_onboarding_migration_is_append_only_and_defaults_roles_safely() -> None:
    sql = SOURCE.read_text(encoding="utf-8")

    assert "requested_role IN ('analyst', 'viewer')" in sql
    assert "requested_role                 TEXT NOT NULL DEFAULT 'analyst'" in sql
    assert "assigned_role" not in sql
    assert "trg_tenant_access_request_events_append_only" in sql
    assert "trg_onboarding_principal_events_append_only" in sql
    assert "BEFORE UPDATE OR DELETE ON tenant_access_request_events" in sql
    assert "BEFORE UPDATE OR DELETE ON onboarding_principal_events" in sql
    assert "actor_issuer = NULLIF" in sql
    assert "actor_subject = NULLIF" in sql
    assert "ck_tenant_access_request_event_type" in sql
    for event_type in (
        "request_submitted",
        "request_resubmitted",
        "request_approved",
        "request_rejected",
        "tenant_created",
        "principal_registered",
        "membership_created",
    ):
        assert f"'{event_type}'" in sql
    assert "onboarding_validate_request_event_lineage" in sql
    assert "trg_tenant_access_request_event_lineage" in sql
    assert "GET DIAGNOSTICS lineage_row_count = ROW_COUNT" in sql
    assert "IF lineage_row_count <> 1 THEN" in sql
    assert "stored_status <> 'approved'" in sql


def test_onboarding_migration_seals_profile_and_prevents_duplicate_requests() -> None:
    sql = SOURCE.read_text(encoding="utf-8")

    for column in (
        "requester_email_snapshot",
        "requester_display_name_snapshot",
        "profile_version_snapshot",
        "terms_version_snapshot",
        "privacy_version_snapshot",
        "profile_snapshot_hash",
    ):
        assert column in sql
    assert "profile_snapshot_hash ~ '^[0-9a-f]{64}$'" in sql
    assert "business_registration_number ~ '^[0-9]{10}$'" in sql
    assert "uq_tenant_access_request_pending_principal" in sql
    assert "uq_tenant_access_request_business_active" in sql
    assert "status IN ('pending', 'approved')" in sql


def test_identity_role_gets_only_pre_membership_onboarding_columns() -> None:
    sql = SOURCE.read_text(encoding="utf-8")

    assert "GRANT SELECT (\n    issuer, subject, contact_email" in sql
    assert "ON TABLE onboarding_profiles TO realytics_identity" in sql
    assert "ON TABLE tenant_access_requests TO realytics_identity" in sql
    assert "ON TABLE tenant_access_request_events TO realytics_identity" in sql
    assert "GRANT SELECT (tenant_id, name, status)" in sql
    assert "ON TABLE tenants TO realytics_identity" in sql
    assert "GRANT INSERT (" in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT UPDATE ON TABLE" not in sql
    # New-tenant/principal/membership creation is intentionally absent from
    # identity-role grants and remains an API runtime review transaction.
    assert "ON TABLE principals TO realytics_identity" not in sql
    assert "ON TABLE tenant_memberships TO realytics_identity" not in sql


def test_supabase_wrapper_is_generated_from_authoritative_onboarding_sql() -> None:
    expected = bridge.render_migration(SOURCE)

    assert WRAPPER.exists()
    assert WRAPPER.read_text(encoding="utf-8") == expected
    assert (
        f"-- BEGIN AUTHORITATIVE REALYTICS SQL: {MIGRATION}"
        in expected
    )
