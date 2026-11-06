from __future__ import annotations

from db.postgres_legal_operations_privileges import (
    LEGAL_OPERATIONS_PRIVILEGES,
    audit_legal_operations_role,
    render_legal_operations_role_sql,
)


def test_legal_operations_role_is_exactly_scoped():
    assert LEGAL_OPERATIONS_PRIVILEGES == {
        "legal_provider_quarantine": ("SELECT", "INSERT"),
        "legal_provider_dead_letters": ("SELECT", "INSERT"),
        "legal_provider_metrics": ("SELECT", "INSERT"),
        "legal_provider_replay_objects": ("SELECT", "INSERT"),
        "legal_provider_replay_approval_events": (
            "SELECT",
            "INSERT",
        ),
        "legal_provider_replay_requests": ("SELECT", "INSERT"),
        "legal_provider_replay_events": ("SELECT", "INSERT"),
    }


def test_legal_operations_role_sql_has_no_mutation_or_elevation():
    sql = "\n".join(
        render_legal_operations_role_sql(
            role_name="realytics_legal_operations",
            schema_name="realytics",
        )
    )
    for marker in (
        "NOLOGIN",
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOINHERIT",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert marker in sql
    assert "PASSWORD" not in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql
    assert (
        'GRANT SELECT, INSERT ON TABLE '
        '"realytics"."legal_provider_replay_requests"'
    ) in sql
    assert (
        'GRANT SELECT, INSERT ON TABLE '
        '"realytics"."legal_provider_quarantine"'
    ) in sql
    assert (
        'GRANT SELECT, INSERT ON TABLE '
        '"realytics"."legal_provider_replay_objects"'
    ) in sql


class _AuditCursor:
    def __init__(self, grants, *, memberships=()):
        self.grants = list(grants)
        self.memberships = list(memberships)
        self.statement = ""

    def execute(self, statement, _params=None):
        self.statement = " ".join(str(statement).split())

    def fetchone(self):
        if "FROM pg_roles WHERE rolname" in self.statement:
            return (False, False, False, False, False, False, False)
        if "has_schema_privilege" in self.statement:
            return (True, False)
        return None

    def fetchall(self):
        if "FROM pg_auth_members" in self.statement:
            return [(value,) for value in self.memberships]
        if "information_schema.role_table_grants" in self.statement:
            return self.grants
        return []


def test_legal_operations_role_audit_detects_extra_grants():
    expected = [
        (table, privilege)
        for table, privileges in LEGAL_OPERATIONS_PRIVILEGES.items()
        for privilege in privileges
    ]
    cursor = _AuditCursor(
        [*expected, ("legal_resources", "SELECT")],
        memberships=("postgres",),
    )
    missing, forbidden = audit_legal_operations_role(
        cursor,
        role_name="realytics_legal_operations",
        schema_name="realytics",
    )
    assert missing == []
    assert "realytics.legal_resources.SELECT" in forbidden
    assert (
        "role:realytics_legal_operations:member_of:postgres"
        in forbidden
    )
