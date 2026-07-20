from __future__ import annotations

import pytest

from db.postgres_legal_sync_privileges import (
    FORBIDDEN_LEGAL_SYNC_PRIVILEGES,
    LEGAL_SYNC_TABLE_PRIVILEGES,
    LegalSyncPrivilegeError,
    audit_legal_sync_role,
    render_legal_sync_role_sql,
    validate_identifier,
)


def test_legal_sync_privilege_manifest_is_append_only():
    assert LEGAL_SYNC_TABLE_PRIVILEGES
    assert set(LEGAL_SYNC_TABLE_PRIVILEGES.values()) == {
        ("SELECT",),
        ("SELECT", "INSERT"),
    }
    for table in (
        "legal_corpus_required_target_contracts",
        "legal_corpus_coverage_registries",
        "legal_corpus_coverage_contracts",
    ):
        assert LEGAL_SYNC_TABLE_PRIVILEGES[table] == ("SELECT",)
    for privileges in LEGAL_SYNC_TABLE_PRIVILEGES.values():
        assert not set(privileges).intersection(
            FORBIDDEN_LEGAL_SYNC_PRIVILEGES
        )


def test_legal_sync_role_sql_has_no_secret_or_elevated_role():
    statements = render_legal_sync_role_sql(
        role_name="realytics_legal_sync",
        schema_name="realytics",
        postgis_schema_name="extensions",
    )
    sql = "\n".join(statements)

    assert "NOLOGIN" in sql
    assert "NOSUPERUSER" in sql
    assert "NOCREATEDB" in sql
    assert "NOCREATEROLE" in sql
    assert "NOINHERIT" in sql
    assert "NOBYPASSRLS" in sql
    assert "PASSWORD" not in sql
    assert 'REVOKE CREATE ON SCHEMA "realytics"' in sql
    assert 'REVOKE CREATE ON SCHEMA "extensions"' in sql
    for table in LEGAL_SYNC_TABLE_PRIVILEGES:
        qualified = f'"realytics"."{table}"'
        assert (
            f"REVOKE ALL PRIVILEGES ON TABLE {qualified}"
            in sql
        )
        grant = ", ".join(LEGAL_SYNC_TABLE_PRIVILEGES[table])
        assert f"GRANT {grant} ON TABLE {qualified}" in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql


@pytest.mark.parametrize(
    "value",
    ["", "has-dash", "has space", "1starts_with_digit", "x" * 64],
)
def test_legal_sync_role_identifier_fails_closed(value):
    with pytest.raises(LegalSyncPrivilegeError, match="identifier"):
        validate_identifier(value, label="legal sync role")


def test_legal_sync_manifest_covers_reconciliation_and_lineage():
    assert {
        "legal_sync_checkpoint_events",
        "legal_sync_scope_lease_events",
        "legal_corpus_scope_completion_attestations",
        "legal_resource_identity_crosswalks",
        "legal_resource_version_lineages",
        "legal_raw_object_reconciliation_runs",
        "legal_raw_object_reconciliation_items",
    }.issubset(LEGAL_SYNC_TABLE_PRIVILEGES)


class AuditCursor:
    def __init__(self, grants):
        self.grants = set(grants)
        self.statement = ""
        self.params = None

    def execute(self, statement, params=None):
        self.statement = statement
        self.params = params

    def fetchone(self):
        if "FROM pg_roles" in self.statement:
            return (False, False, False, False, False, False)
        if "has_schema_privilege" in self.statement:
            return (True, False)
        if "has_table_privilege" in self.statement:
            table, privilege = self.params
            return ((table, privilege) in self.grants,)
        return None


def test_legal_sync_role_audit_detects_overprivilege():
    required = {
        (f"realytics.{table}", privilege)
        for table, privileges in LEGAL_SYNC_TABLE_PRIVILEGES.items()
        for privilege in privileges
    }
    cursor = AuditCursor(
        required | {("realytics.legal_resources", "UPDATE")}
    )

    missing, forbidden = audit_legal_sync_role(
        cursor,
        schema_name="realytics",
    )

    assert missing == []
    assert forbidden == ["realytics.legal_resources.UPDATE"]
