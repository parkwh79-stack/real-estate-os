from __future__ import annotations

import pytest

from db import postgres
from db.postgres_runtime_privileges import (
    FORBIDDEN_APPEND_ONLY_PRIVILEGES,
    PARSER_V2_MANAGED_SUPPORT_PRIVILEGES,
    PARSER_V2_TABLE_PRIVILEGES,
    RuntimePrivilegeProvisioningError,
    audit_runtime_privileges,
    provision_parser_v2_runtime_role,
    required_table_privileges,
    validate_database_identifier,
)


def test_privilege_manifest_covers_every_parser_rls_table_exactly() -> None:
    assert set(PARSER_V2_TABLE_PRIVILEGES) == set(postgres.PARSER_V2_RLS_TABLES)
    assert PARSER_V2_TABLE_PRIVILEGES["parser_validation_finding_events"] == ()
    assert PARSER_V2_MANAGED_SUPPORT_PRIVILEGES["schema_migrations"] == (
        "SELECT",
    )
    assert PARSER_V2_MANAGED_SUPPORT_PRIVILEGES["runtime_outbox_events"] == (
        "SELECT",
        "INSERT",
        "UPDATE",
    )
    for table, privileges in PARSER_V2_TABLE_PRIVILEGES.items():
        assert not set(privileges).intersection(FORBIDDEN_APPEND_ONLY_PRIVILEGES), table


@pytest.mark.parametrize(
    "value",
    ["", "has-dash", "has space", "1starts_with_digit", "x" * 64],
)
def test_database_identifier_validation_fails_closed(value: str) -> None:
    with pytest.raises(RuntimePrivilegeProvisioningError, match="identifier"):
        validate_database_identifier(value, label="runtime role")


class _AuditCursor:
    def __init__(
        self,
        grants: set[tuple[str, str]],
        *,
        schema_usage: bool = True,
        schema_create: bool = False,
    ):
        self.grants = set(grants)
        self.schema_usage = schema_usage
        self.schema_create = schema_create
        self.row = None

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        if normalized.endswith("'USAGE')"):
            self.row = (self.schema_usage,)
        elif normalized.endswith("'CREATE')"):
            self.row = (self.schema_create,)
        elif "WHEN relation_oid IS NULL THEN FALSE" in normalized:
            privilege, _schema, table = params
            self.row = ((table, privilege) in self.grants,)
        else:
            raise AssertionError(normalized)

    def fetchone(self):
        return self.row


def test_privilege_audit_reports_missing_and_forbidden_effective_grants() -> None:
    grants = {
        (table, privilege)
        for table, privileges in required_table_privileges().items()
        for privilege in privileges
    }
    grants.remove(("schema_migrations", "SELECT"))
    grants.add(("parser_runs", "UPDATE"))
    cursor = _AuditCursor(
        grants,
        schema_usage=False,
        schema_create=True,
    )

    missing, forbidden = audit_runtime_privileges(cursor, schema_name="public")

    assert missing == ["schema:public.USAGE", "schema_migrations.SELECT"]
    assert forbidden == ["parser_runs.UPDATE", "schema:public.CREATE"]


class _ProvisionCursor:
    def __init__(self, *, privileged_role: bool = False, owned=()):
        self.privileged_role = privileged_role
        self.owned = list(owned)
        self.rows = []
        self.statements: list[tuple[str, object]] = []
        self.current_schema = "public"

    def execute(self, query, params=None):
        if hasattr(query, "as_string"):
            rendered = query.as_string()
        else:
            rendered = str(query)
        normalized = " ".join(rendered.split())
        self.statements.append((normalized, params))
        if normalized == "SELECT current_schema()":
            self.rows = [(self.current_schema,)]
        elif normalized.startswith("SELECT rolsuper"):
            self.rows = [
                (
                    self.privileged_role,
                    False,
                    False,
                    False,
                    False,
                )
            ]
        elif "owner_role.oid = c.relowner" in normalized:
            self.rows = [(item,) for item in self.owned]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ProvisionConnection:
    def __init__(self, cursor: _ProvisionCursor):
        self.bound_cursor = cursor

    def cursor(self):
        return self.bound_cursor


def test_provisioner_revokes_parser_tables_and_grants_only_manifest() -> None:
    cursor = _ProvisionCursor()
    provision_parser_v2_runtime_role(
        _ProvisionConnection(cursor),
        role_name="realytics_runtime",
        schema_name="public",
        migration_lock_key=postgres.MIGRATION_LOCK_KEY,
    )

    sql = "\n".join(statement for statement, _params in cursor.statements)
    assert "REVOKE CREATE ON SCHEMA \"public\" FROM PUBLIC" in sql
    assert (
        'GRANT SELECT, INSERT ON TABLE "public"."parser_runs" '
        'TO "realytics_runtime"'
    ) in sql
    assert (
        'GRANT SELECT, INSERT, UPDATE ON TABLE "public"."runtime_outbox_events" '
        'TO "realytics_runtime"'
    ) in sql
    assert 'GRANT SELECT ON TABLE "public"."schema_migrations"' in sql
    assert 'GRANT UPDATE ON TABLE "public"."parser_runs"' not in sql
    assert 'GRANT DELETE' not in sql
    validation_event_grants = [
        statement
        for statement, _params in cursor.statements
        if "parser_validation_finding_events" in statement
        and statement.startswith("GRANT")
    ]
    assert validation_event_grants == []


@pytest.mark.parametrize(
    ("cursor", "message"),
    [
        (_ProvisionCursor(privileged_role=True), "NOSUPERUSER"),
        (_ProvisionCursor(owned=("parser_runs",)), "must not own"),
    ],
)
def test_provisioner_rejects_privileged_or_owning_runtime_role(
    cursor: _ProvisionCursor,
    message: str,
) -> None:
    with pytest.raises(RuntimePrivilegeProvisioningError, match=message):
        provision_parser_v2_runtime_role(
            _ProvisionConnection(cursor),
            role_name="realytics_runtime",
            schema_name="public",
            migration_lock_key=postgres.MIGRATION_LOCK_KEY,
        )


def test_provisioner_rejects_dsn_search_path_for_another_schema() -> None:
    cursor = _ProvisionCursor()
    cursor.current_schema = "other_schema"

    with pytest.raises(RuntimePrivilegeProvisioningError, match="search_path"):
        provision_parser_v2_runtime_role(
            _ProvisionConnection(cursor),
            role_name="realytics_runtime",
            schema_name="public",
            migration_lock_key=postgres.MIGRATION_LOCK_KEY,
        )
